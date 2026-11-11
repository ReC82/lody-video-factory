"""Worker de rendu 2D (#120) : rend un storyboard animé figé, scène par scène, dans un dossier de travail isolé.

Protocole (tout est dans ``<données>/animation/<projet>/<tâche>/``, rien n'est lu ailleurs) :

    job.json        demande figée : mode, storyboard ``lody.animation/1`` figé, fichiers d'assets copiés
    assets/…        copies VÉRIFIÉES (SHA-256) des fichiers du catalogue utilisés par CE storyboard
    scenes/NN.mp4   rendu de chaque scène + ``NN.json`` (trace : statut, durée réelle, empreinte, durée de rendu)
    state.json      état courant (queued | running | interrupted | done | failed | cancelled), avancement, battement
    subtitles.srt   sous-titres tirés des repères de parole du storyboard (aucun appel, aucun coût)
    out.mp4         vidéo finale (H.264 yuv420p) ; ``manifest.json`` la décrit (empreinte, durée, scènes, replis)
    cancel          drapeau d'arrêt demandé : le worker s'arrête entre deux scènes (état ``cancelled``)
    worker.log      journal NETTOYÉ (aucun secret : voir ``lody.generation.safety.sanitize``)

Garanties :

- **Un seul rendu à la fois** sur la machine (verrou ``animation/.render.lock``) : les autres tâches attendent
  en ``queued``. Un verrou par tâche (``job.lock``) empêche deux workers sur la même tâche ; il est libéré par
  le système à la mort du processus, ce qui permet au fournisseur de détecter un worker disparu.
- **Ressources bornées** : priorité basse (``nice``), délai maximal par scène, plafond d'images total, moteur de
  scène lancé avec un environnement minimal (aucune variable de l'application, donc aucun secret).
- **Arrêt et reprise sûrs** : SIGTERM/SIGINT ou drapeau ``cancel`` arrêtent proprement le moteur de scène ; une
  scène déjà rendue (empreinte vérifiée) n'est jamais refaite : la reprise repart de la première scène manquante.
- **Hybride** : une scène dont l'animation échoue est remplacée par son image de repli (``render.fallback``),
  tracée et signalée. En mode Animation 2D, l'échec d'une scène fait échouer la tâche.

Le moteur de scène est une commande externe (``LODY_ANIMATION_RENDERER``, ex. l'adaptateur Remotion du
prototype #118) appelée ainsi :

    <commande> --input scenes/NN-input.json --output scenes/NN.render.mp4
    <commande> --input scenes/NN-input.json --still <image> --output scenes/NN-still.png

``NN-input.json`` contient ``{"schema": "lody.animation-scene/1", "canvas", "scene", "index", "assets"}`` où
``assets`` associe ``"<id>@<version>"`` aux chemins ABSOLUS des fichiers copiés dans ``assets/``. Le contrat
reste déclaratif : aucun code n'est généré ni exécuté depuis le storyboard.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from lody.animation import audio, modes
from lody.animation.contract import (
    AnimationValidationError,
    canonical_json,
    document_sha256,
    total_frames,
    validate_document,
)

logger = logging.getLogger("lody.animation.worker")

JOB_SCHEMA = "lody.animation-job/1"
SCENE_SCHEMA = "lody.animation-scene/1"
RENDERER_ENV = "LODY_ANIMATION_RENDERER"
SCENE_TIMEOUT_ENV = "LODY_ANIMATION_SCENE_TIMEOUT"
DEFAULT_SCENE_TIMEOUT = 900.0          # 15 min par scène : ≈ 7× le temps réel mesuré en #118, avec marge
MAX_TOTAL_SECONDS = 600                # 10 min de vidéo au plus par tâche (plafond d'images = fps × 600)
NICENESS = 10
HEARTBEAT_EVERY = 2.0
LOCK_POLL = 1.0
TERMINAL_STATES = ("done", "failed", "cancelled")
_SAFE_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_RASTER = (".png", ".webp")
_FALLBACK_COLOR = "0x1f2937"


class JobError(Exception):
    """Erreur lisible, destinée à l'utilisateur (aucun chemin système, aucun secret)."""


class SceneRenderError(Exception):
    pass


class Stopped(Exception):
    """Arrêt demandé (signal ou drapeau ``cancel``)."""


# -- fichiers ------------------------------------------------------------------------------------------------
def write_json(path: Path, data: Any) -> None:
    """Écriture atomique (fichier temporaire puis renommage) : un lecteur ne voit jamais un JSON tronqué."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def is_safe_part(value: object) -> bool:
    return isinstance(value, str) and bool(_SAFE_PART.fullmatch(value))


def jobs_root(data_dir: Path) -> Path:
    return Path(data_dir) / "animation"


def job_dir(data_dir: Path, project_id: str, task_id: str) -> Path:
    if not (is_safe_part(project_id) and is_safe_part(task_id)):
        raise ValueError("identifiant de projet ou de tâche invalide")
    return jobs_root(data_dir) / project_id / task_id


def find_job_dir(data_dir: Path, task_id: str) -> Path | None:
    """Dossier d'une tâche, quel que soit son projet (l'identifiant de tâche est unique)."""
    if not is_safe_part(task_id):
        return None
    root = jobs_root(data_dir)
    if not root.is_dir():
        return None
    for project in sorted(root.iterdir()):
        candidate = project / task_id
        if is_safe_part(project.name) and (candidate / "job.json").is_file():
            return candidate
    return None


def ffmpeg_exe() -> str | None:
    """ffmpeg du système, sinon celui embarqué par ``imageio-ffmpeg`` (dépendance du moteur historique)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # absent ou binaire introuvable : le préflight le signale
        return None


def renderer_command(raw: str | None = None) -> list[str]:
    """Commande du moteur de scène (``LODY_ANIMATION_RENDERER``), découpée comme un shell, sans l'exécuter."""
    value = os.environ.get(RENDERER_ENV, "") if raw is None else raw
    try:
        return shlex.split(value.strip())
    except ValueError:
        return []


def scene_timeout() -> float:
    try:
        value = float(os.environ.get(SCENE_TIMEOUT_ENV, "") or DEFAULT_SCENE_TIMEOUT)
    except ValueError:
        return DEFAULT_SCENE_TIMEOUT
    return min(max(value, 10.0), 4 * 3600.0)


def minimal_env() -> dict[str, str]:
    """Environnement du moteur de scène : rien de l'application (aucune clé, aucun chemin de configuration)."""
    keep = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR") if key in os.environ}
    keep.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return keep


@contextmanager
def file_lock(path: Path, *, blocking: bool) -> Iterator[bool]:
    """Verrou ``flock`` exclusif ; renvoie ``False`` (sans attendre) s'il est déjà pris et ``blocking`` est faux."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            acquired = True
        except BlockingIOError:
            acquired = False
        try:
            yield acquired
        finally:
            if acquired:
                fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def worker_alive(directory: Path) -> bool:
    """Vrai si un worker tient le verrou de cette tâche (le système le libère à la mort du processus)."""
    with file_lock(directory / "job.lock", blocking=False) as acquired:
        return not acquired


def media_duration(ffmpeg: str, path: Path) -> float | None:
    """Durée réelle d'un média (lue par ffmpeg), ou ``None``."""
    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                              timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = _DURATION.search(done.stderr or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


# -- moteur de scène -----------------------------------------------------------------------------------------
class SceneRenderer(Protocol):
    def render(self, scene_input: Path, output: Path, timeout: float, tick: Callable[[], None]) -> None: ...

    def still(self, scene_input: Path, frame: int, output: Path, timeout: float, tick: Callable[[], None]) -> None: ...


class CommandRenderer:
    """Moteur de scène externe (adaptateur Remotion ou autre), lancé avec un environnement minimal."""

    def __init__(self, command: list[str]):
        if not command:
            raise JobError("Aucun moteur de rendu 2D n'est configuré.")
        self.command = list(command)

    def _run(self, arguments: list[str], output: Path, timeout: float, tick: Callable[[], None]) -> None:
        log = output.with_suffix(output.suffix + ".log")
        with log.open("w", encoding="utf-8") as sink:
            try:
                process = subprocess.Popen(self.command + arguments, stdout=sink, stderr=subprocess.STDOUT,
                                           env=minimal_env(), cwd=str(output.parent), start_new_session=True)
            except OSError as error:
                raise SceneRenderError(f"moteur de rendu introuvable ou non exécutable ({type(error).__name__})") from None
            deadline = time.monotonic() + timeout
            try:
                while True:
                    try:
                        code = process.wait(timeout=1.0)
                        break
                    except subprocess.TimeoutExpired:
                        tick()  # battement + arrêt demandé (lève Stopped)
                        if time.monotonic() > deadline:
                            raise SceneRenderError(f"délai de rendu dépassé ({int(timeout)} s)") from None
            except BaseException:
                _kill(process)
                raise
        if code != 0:
            raise SceneRenderError(f"le moteur de rendu a échoué (code {code})")
        if not output.is_file() or output.stat().st_size == 0:
            raise SceneRenderError("le moteur de rendu n'a produit aucun fichier")

    def render(self, scene_input: Path, output: Path, timeout: float, tick: Callable[[], None]) -> None:
        self._run(["--input", str(scene_input), "--output", str(output)], output, timeout, tick)

    def still(self, scene_input: Path, frame: int, output: Path, timeout: float, tick: Callable[[], None]) -> None:
        self._run(["--input", str(scene_input), "--still", str(int(frame)), "--output", str(output)], output,
                  timeout, tick)


def _kill(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass


# -- exécution d'une tâche -----------------------------------------------------------------------------------
@dataclass
class _Context:
    directory: Path
    job: dict[str, Any]
    renderer: SceneRenderer | None
    ffmpeg: str
    timeout: float
    clock: Callable[[], float]
    stop_requested: Callable[[], bool]
    state: dict[str, Any]
    last_beat: float = 0.0

    def save_state(self, **changes: Any) -> None:
        self.state.update(changes)
        self.state["heartbeat_at"] = self.clock()
        write_json(self.directory / "state.json", self.state)
        self.last_beat = time.monotonic()

    def tick(self) -> None:
        if self.stop_requested() or (self.directory / "cancel").exists():
            raise Stopped()
        if time.monotonic() - self.last_beat >= HEARTBEAT_EVERY:
            self.save_state()

    def log(self, message: str) -> None:
        from lody.generation.safety import sanitize

        with (self.directory / "worker.log").open("a", encoding="utf-8") as sink:
            sink.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.clock()))} {sanitize(message, 400)}\n")


def load_job(directory: Path) -> dict[str, Any]:
    """Relit et revérifie la demande : schéma, storyboard figé intact, fichiers d'assets présents et intacts."""
    job = read_json(directory / "job.json")
    if not job or job.get("schema") != JOB_SCHEMA:
        raise JobError("Demande de rendu illisible.")
    if job.get("mode") not in (modes.ANIMATION, modes.HYBRID):
        raise JobError("Mode de rendu inconnu pour cette tâche.")
    try:
        storyboard = validate_document(job.get("storyboard"), require_pinned=True)
    except AnimationValidationError as error:
        raise JobError("Storyboard animé invalide : " + "; ".join(f"{k} : {v}" for k, v in list(error.errors.items())[:3])) from None
    if total_frames(storyboard) > storyboard["canvas"]["fps"] * MAX_TOTAL_SECONDS:
        raise JobError(f"Storyboard trop long : {MAX_TOTAL_SECONDS // 60} minutes au plus par vidéo animée.")
    for files in (job.get("assets") or {}).values():
        for entry in files.values():
            path = directory / entry["path"]
            if not path.is_file() or sha256_file(path) != entry["sha256"]:
                raise JobError("Un fichier d'asset copié pour ce rendu est absent ou modifié.")
    job["storyboard"] = storyboard
    return job


def scene_input(ctx: _Context, index: int, scene: dict[str, Any]) -> Path:
    used: set[str] = set()
    for layer in scene["render"]["layers"]:
        for obj in layer["objects"]:
            if obj["type"] == "asset":
                used.add(f"{obj['asset']['id']}@{obj['asset']['version']}")
    fallback = scene["render"]["fallback"]
    if fallback["type"] == "asset":
        used.add(f"{fallback['asset']['id']}@{fallback['asset']['version']}")
    assets = {key: {role: str(ctx.directory / entry["path"]) for role, entry in ctx.job["assets"][key].items()}
              for key in sorted(used) if key in (ctx.job.get("assets") or {})}
    data = {"schema": SCENE_SCHEMA, "canvas": ctx.job["storyboard"]["canvas"], "index": index, "scene": scene,
            "assets": assets}
    info = ctx.job.get("asset_info") or {}
    if info:  # #150 : type, dimensions et ancrage du manifeste (placement exact par le moteur de scène)
        data["asset_info"] = {key: info[key] for key in sorted(used) if key in info}
    voice = ctx.directory / audio.VOICE_FILE
    if voice.is_file():  # #150 : voix seule pour la synchro labiale, décalée au début de CETTE scène
        offset = sum(s["duration_frames"] for s in ctx.job["storyboard"]["scenes"][: index - 1])
        data["voice"] = {"path": str(voice), "offset_frames": offset}
    path = ctx.directory / "scenes" / f"{index:02d}-input.json"
    write_json(path, data)
    return path


def _encode_still(ctx: _Context, image: Path | None, seconds: float, output: Path) -> None:
    canvas = ctx.job["storyboard"]["canvas"]
    width, height, fps = canvas["width"], canvas["height"], canvas["fps"]
    if image is not None:
        source = ["-loop", "1", "-i", str(image)]
        video_filter = (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={_FALLBACK_COLOR},format=yuv420p")
    else:
        source = ["-f", "lavfi", "-i", f"color=c={_FALLBACK_COLOR}:s={width}x{height}:r={fps}"]
        video_filter = "format=yuv420p"
    command = [ctx.ffmpeg, "-y", "-loglevel", "error", *source, "-t", f"{seconds:.3f}", "-r", str(fps),
               "-vf", video_filter, "-c:v", "libx264", "-preset", "veryfast", "-an", str(output)]
    done = subprocess.run(command, capture_output=True, timeout=300, check=False)
    if done.returncode != 0 or not output.is_file():
        raise SceneRenderError("image de repli impossible à encoder")


def _fallback(ctx: _Context, index: int, scene: dict[str, Any], input_path: Path, output: Path) -> str:
    """Repli d'image fixe d'une scène (mode Hybride). Renvoie la source utilisée."""
    seconds = scene["duration_frames"] / ctx.job["storyboard"]["canvas"]["fps"]
    fallback = scene["render"]["fallback"]
    candidates: list[tuple[str, Path]] = []
    if fallback["type"] == "asset":
        key = f"{fallback['asset']['id']}@{fallback['asset']['version']}"
        files = (ctx.job.get("assets") or {}).get(key) or {}
        ordered = sorted(files.items(), key=lambda item: (item[0] != "base", item[0]))
        candidates += [("décor du catalogue", ctx.directory / entry["path"]) for _, entry in ordered
                       if entry["path"].lower().endswith(_RASTER)]
    elif ctx.renderer is not None:
        still = ctx.directory / "scenes" / f"{index:02d}-still.png"
        try:
            ctx.renderer.still(input_path, fallback["frame"], still, min(ctx.timeout, 300.0), ctx.tick)
            candidates.append((f"image {fallback['frame']} de la scène", still))
        except SceneRenderError as error:
            ctx.log(f"scène {index} : image fixe de repli impossible ({error})")
    for label, image in candidates:
        try:
            _encode_still(ctx, image, seconds, output)
            return label
        except (SceneRenderError, subprocess.SubprocessError, OSError):
            ctx.log(f"scène {index} : repli « {label} » impossible à encoder")
    _encode_still(ctx, None, seconds, output)
    return "carton neutre"


def _voice_digest(ctx: _Context) -> str:
    voice = ctx.directory / audio.VOICE_FILE
    return sha256_file(voice) if voice.is_file() else ""


def _render_scene(ctx: _Context, index: int, scene: dict[str, Any]) -> dict[str, Any]:
    scenes_dir = ctx.directory / "scenes"
    output = scenes_dir / f"{index:02d}.mp4"
    trace_path = scenes_dir / f"{index:02d}.json"
    expected = canonical_json(scene) + _voice_digest(ctx)  # une voix changée rend la scène à refaire (bouche)
    cached = read_json(trace_path)
    if (cached and cached.get("scene_sha256") == hashlib.sha256(expected.encode()).hexdigest()
            and output.is_file() and sha256_file(output) == cached.get("sha256")):
        return {**cached, "cached": True}  # reprise : scène déjà rendue et intacte, jamais refaite

    fps = ctx.job["storyboard"]["canvas"]["fps"]
    started = time.monotonic()
    input_path = scene_input(ctx, index, scene)
    temporary = scenes_dir / f"{index:02d}.render.mp4"
    temporary.unlink(missing_ok=True)
    status, detail, source = "animated", "", ""
    try:
        if ctx.renderer is None:
            raise SceneRenderError("aucun moteur de rendu 2D configuré")
        ctx.renderer.render(input_path, temporary, ctx.timeout, ctx.tick)
        os.replace(temporary, output)
    except SceneRenderError as error:
        detail = str(error)
        ctx.log(f"scène {index} « {scene['id']} » : échec de l'animation ({detail})")
        if ctx.job["mode"] != modes.HYBRID:
            raise JobError(f"La scène {index} « {scene['id']} » n'a pas pu être animée : {detail}.") from None
        source = _fallback(ctx, index, scene, input_path, output)
        status = "fallback"
    trace = {
        "index": index, "id": scene["id"], "status": status, "detail": detail, "fallback_source": source,
        "frames": scene["duration_frames"], "expected_seconds": round(scene["duration_frames"] / fps, 3),
        "duration_seconds": media_duration(ctx.ffmpeg, output), "render_ms": int((time.monotonic() - started) * 1000),
        "sha256": sha256_file(output), "scene_sha256": hashlib.sha256(expected.encode()).hexdigest(),
    }
    write_json(trace_path, trace)
    return trace


def _concat(ctx: _Context, outputs: list[Path], target: Path) -> None:
    canvas = ctx.job["storyboard"]["canvas"]
    listing = ctx.directory / "scenes" / "concat.txt"
    listing.write_text("".join(f"file '{path.name}'\n" for path in outputs), encoding="utf-8")
    temporary = ctx.directory / "out.partial.mp4"
    command = [ctx.ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing)]
    soundtrack = ctx.directory / audio.MIX_FILE
    if soundtrack.is_file():
        command += ["-i", str(soundtrack), "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-b:a", "160k",
                    "-shortest"]
    else:
        command += ["-an"]
    command += ["-vf", f"fps={canvas['fps']},scale={canvas['width']}:{canvas['height']},format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-movflags", "+faststart", "-f", "mp4",
                str(temporary)]
    done = subprocess.run(command, capture_output=True, timeout=max(600.0, ctx.timeout), check=False)
    if done.returncode != 0 or not temporary.is_file():
        ctx.log("assemblage ffmpeg en échec : " + (done.stderr or b"").decode("utf-8", "replace")[-300:])
        raise JobError("L'assemblage final des scènes a échoué.")
    os.replace(temporary, target)


def write_subtitles(directory: Path, storyboard: dict[str, Any]) -> int:
    """SRT tiré des repères de parole (``render.speech.cues``), décalés par scène. Renvoie le nombre de repères."""
    from lody.generation import subtitles

    fps = storyboard["canvas"]["fps"]
    cues, offset = [], 0
    for scene in storyboard["scenes"]:
        for cue in ((scene["render"].get("speech") or {}).get("cues") or []):
            cues.append(subtitles.Cue(len(cues) + 1, round((offset + cue["start"]) * 1000 / fps),
                                      round((offset + cue["end"]) * 1000 / fps), cue["text"]))
        offset += scene["duration_frames"]
    if cues:
        (directory / "subtitles.srt").write_text(subtitles.to_srt(cues), encoding="utf-8")
    return len(cues)


def run_job(directory: Path, renderer: SceneRenderer | None, *, ffmpeg: str | None = None,
            timeout: float | None = None, clock: Callable[[], float] = time.time,
            stop_requested: Callable[[], bool] = lambda: False) -> dict[str, Any]:
    """Exécute (ou reprend) une tâche jusqu'à un état final ou un arrêt. Renvoie l'état final."""
    directory = Path(directory)
    with file_lock(directory / "job.lock", blocking=False) as mine:
        if not mine:
            return read_json(directory / "state.json") or {}
        state = read_json(directory / "state.json") or {}
        if state.get("state") in TERMINAL_STATES:
            return state
        ctx = _Context(directory, {}, renderer, ffmpeg or ffmpeg_exe() or "", timeout or scene_timeout(), clock,
                       stop_requested, state)
        try:
            ctx.job = load_job(directory)
            if not ctx.ffmpeg:
                raise JobError("ffmpeg est introuvable : l'assemblage de la vidéo est impossible.")
            ctx.save_state(state="queued", step="En attente d'un emplacement de rendu", error="")
            with _global_slot(ctx):
                _execute(ctx)
        except Stopped:
            if (directory / "cancel").exists():
                ctx.save_state(state="cancelled", step="Arrêtée", error="Rendu arrêté à la demande.")
            else:
                ctx.save_state(state="interrupted", step="Interrompue, reprise possible")
            ctx.log("rendu arrêté proprement")
        except JobError as error:
            ctx.save_state(state="failed", step="Échec", error=str(error))
            ctx.log(f"échec : {error}")
        except Exception as error:  # jamais de trace brute : type seulement, journal nettoyé
            ctx.save_state(state="failed", step="Échec", error="Erreur inattendue du worker de rendu 2D.")
            ctx.log(f"erreur inattendue ({type(error).__name__})")
        return ctx.state


@contextmanager
def _global_slot(ctx: _Context) -> Iterator[None]:
    """Un seul rendu à la fois sur la machine ; en attendant, la tâche reste ``queued`` et répond aux arrêts."""
    lock = ctx.directory.parents[1] / ".render.lock"
    while True:
        with file_lock(lock, blocking=False) as acquired:
            if acquired:
                yield
                return
        ctx.tick()
        time.sleep(LOCK_POLL)


def _execute(ctx: _Context) -> None:
    storyboard = ctx.job["storyboard"]
    scenes = storyboard["scenes"]
    total = total_frames(storyboard)
    (ctx.directory / "scenes").mkdir(exist_ok=True)
    started = time.monotonic()
    ctx.save_state(state="running", total_scenes=len(scenes), total_frames=total, rendered_frames=0,
                   step="Rendu des scènes")
    ctx.log(f"rendu démarré : {len(scenes)} scène(s), {total} image(s), mode {ctx.job['mode']}")
    traces, done_frames = [], 0
    for index, scene in enumerate(scenes, 1):
        ctx.tick()
        ctx.save_state(scene=index, step=f"Rendu de la scène {index}/{len(scenes)}")
        trace = _render_scene(ctx, index, scene)
        traces.append(trace)
        done_frames += scene["duration_frames"]
        ctx.save_state(rendered_frames=done_frames)
    ctx.tick()
    ctx.save_state(step="Assemblage de la vidéo")
    target = ctx.directory / "out.mp4"
    try:
        sound = audio.prepare_soundtrack(ctx.ffmpeg, ctx.directory, total / storyboard["canvas"]["fps"])
    except (audio.SoundtrackError, subprocess.SubprocessError, OSError):
        raise JobError("Le mixage de la voix et de la musique a échoué.") from None
    if sound:
        ctx.log(f"bande son : {sound}")
    _concat(ctx, [ctx.directory / "scenes" / f"{i:02d}.mp4" for i in range(1, len(scenes) + 1)], target)
    cues = write_subtitles(ctx.directory, storyboard)
    fallbacks = [t for t in traces if t["status"] == "fallback"]
    manifest = {
        "schema": "lody.animation-render/1", "mode": ctx.job["mode"], "frames": total,
        "fps": storyboard["canvas"]["fps"], "width": storyboard["canvas"]["width"],
        "height": storyboard["canvas"]["height"],
        "expected_seconds": round(total / storyboard["canvas"]["fps"], 3),
        "duration_seconds": media_duration(ctx.ffmpeg, target), "sha256": sha256_file(target),
        "size_bytes": target.stat().st_size, "storyboard_sha256": document_sha256(storyboard),
        "render_ms": int((time.monotonic() - started) * 1000), "subtitle_cues": cues,
        "audio": audio.describe(ctx.directory),
        "scenes": [{key: value for key, value in t.items() if key != "scene_sha256"} for t in traces],
        "fallback_scenes": [t["index"] for t in fallbacks],
        "warnings": [f"Scène {t['index']} « {t['id']} » : animation en échec, remplacée par l'image de repli "
                     f"({t['fallback_source']})." for t in fallbacks],
    }
    write_json(ctx.directory / "manifest.json", manifest)
    ctx.save_state(state="done", step="Terminée", rendered_frames=total)
    ctx.log(f"rendu terminé : {manifest['duration_seconds']} s, {len(fallbacks)} repli(s)")


# -- point d'entrée du processus dédié -----------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage : python -m lody.animation.worker <dossier de la tâche>", file=sys.stderr)
        return 2
    try:
        os.nice(NICENESS)
    except OSError:
        pass
    stop = {"flag": False}

    def _on_signal(signum: int, frame: Any) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    command = renderer_command()
    renderer = CommandRenderer(command) if command else None
    state = run_job(Path(args[0]), renderer, stop_requested=lambda: stop["flag"])
    return 0 if state.get("state") in ("done", "interrupted", "cancelled") else 1


if __name__ == "__main__":
    raise SystemExit(main())
