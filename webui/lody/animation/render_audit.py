"""Audit de reproductibilité, de cache et de coût d'un rendu 2D (#168).

Module **isolé et en lecture seule** (sauf ``clean(..., apply=True)``) : il relit ce que le worker de rendu
(``lody.animation.worker``) a déjà écrit et n'appelle jamais aucun service payant.

- ``audit_render`` : rapport d'un dossier de rendu (dossier de tâche ``<données>/animation/<projet>/<tâche>/`` ou
  dossier de démonstration versionné ``docs/animation/…``) : durées par étape, scènes réutilisées, empreintes,
  tailles, dépenses *connues* (déclarées dans ``audio.json``) et *estimées* (tarifs ``pricing.toml``), contrôles
  de seuils et recommandations. ``to_html`` en fait une page locale autonome.
- ``invalidation`` : compare deux storyboards et dit ce qu'une retouche fait refaire — avec la clé de cache
  actuelle du worker (scène entière + empreinte de la voix complète) et avec une clé « par dépendance » —
  pour repérer les invalidations inutiles (un texte d'écran changé ne doit pas resynthétiser la voix).
- ``StageRecorder`` / ``measure`` : durée réelle, temps CPU et mémoire maximale (``getrusage``) d'une étape
  ou d'une commande locale ; résultat dans un ``metrics.json`` relu par ``audit_render``.
- ``disk_status`` / ``clean_plan`` / ``clean`` : garde d'espace disque et nettoyage des intermédiaires
  temporaires des tâches **terminées** (jamais ``out.mp4``, ``manifest.json``, ``job.json``, ``assets/`` ni les
  scènes rendues ``scenes/NN.mp4`` qui servent de cache de reprise).
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import resource
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from lody.animation.contract import canonical_json

AUDIT_SCHEMA = "lody.animation-render-audit/1"
METRICS_SCHEMA = "lody.animation-metrics/1"
METRICS_FILE = "metrics.json"
TERMINAL_STATES = ("done", "failed", "cancelled")  # mêmes états finaux que le worker

# Étapes de la chaîne 2D, dans l'ordre. « instrumented » = mesurable aujourd'hui depuis les sorties du worker.
STAGES = (
    ("script", "Script", False),
    ("assets", "Assets (copie vérifiée)", False),
    ("voice", "Voix (synthèse)", False),
    ("visemes", "Visèmes (synchro labiale)", False),
    ("remotion", "Rendu des scènes (Remotion)", True),
    ("mix", "Mixage voix + musique", False),
    ("assembly", "Assemblage + sous-titres", True),
)

# Seuils : dérivés des rendus mesurés (#150 : ×7,1 le temps réel ; #137 : ×6,0), avec marge.
THRESHOLDS: dict[str, float] = {
    "render_realtime_factor": 10.0,       # secondes de rendu par seconde de vidéo (rendu complet)
    "scene_realtime_factor": 12.0,        # idem, par scène
    "bytes_per_video_second": 1_000_000,  # MP4 final ; 9:16 H.264 crf 23 ≈ 85–115 Ko/s mesurés
    "duration_drift_seconds": 0.1,        # écart durée réelle / durée attendue
    "peak_rss_bytes": 2 * 1024**3,        # mémoire max d'une étape mesurée
    "job_dir_bytes": 2 * 1024**3,         # taille d'un dossier de tâche
    "min_free_bytes": 5 * 1024**3,        # espace libre minimal avant un nouveau rendu
}

_LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) (.*)$")
_FREE_SOURCES = ("synthèse locale", "piper", "générée", "generated", "edge")
_PAID_PROVIDERS = {"elevenlabs": ("voice", "elevenlabs")}


# -- lecture --------------------------------------------------------------------------------------------------
def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def load_storyboard(directory: Path) -> dict[str, Any] | None:
    """Storyboard d'un dossier de rendu : ``job.json`` (tâche du worker) ou ``storyboard.json`` (démo)."""
    job = _read_json(directory / "job.json")
    if isinstance(job, dict) and isinstance(job.get("storyboard"), dict):
        return job["storyboard"]
    board = _read_json(directory / "storyboard.json")
    return board if isinstance(board, dict) else None


def parse_log(directory: Path) -> list[tuple[float, str]]:
    """Lignes horodatées de ``worker.log`` (résolution : la seconde)."""
    try:
        lines = (directory / "worker.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        match = _LOG_LINE.match(line)
        if match:
            stamp = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            events.append((stamp.timestamp(), match.group(2)))
    return events


def _is_reused(trace: dict[str, Any]) -> bool:
    """Scène servie sans rendu : reprise du worker (``cached``) ou cache de scène (#121, ``cache``)."""
    return bool(trace.get("cached")) or str(trace.get("cache") or "") in ("hit", "job", "project")


# -- étapes ---------------------------------------------------------------------------------------------------
def stage_timings(manifest: dict[str, Any], metrics: dict[str, Any] | None,
                  log: list[tuple[float, str]]) -> list[dict[str, Any]]:
    """Durée de chaque étape : mesurée (``metrics.json``), déduite du manifeste, ou « non instrumentée »."""
    measured = (metrics or {}).get("stages") or {}
    scenes = manifest.get("scenes") or []
    rendered_ms = sum(int(t.get("render_ms") or 0) for t in scenes if not _is_reused(t))
    total_ms = manifest.get("render_ms")
    derived: dict[str, tuple[float, str]] = {}
    if scenes:
        derived["remotion"] = (rendered_ms / 1000, "somme des render_ms des scènes rendues (manifeste)")
    if isinstance(total_ms, (int, float)) and scenes:
        derived["assembly"] = (max(0.0, (total_ms - sum(int(t.get("render_ms") or 0) for t in scenes)) / 1000),
                               "render_ms total − scènes : mixage, assemblage ffmpeg, sous-titres")
    start = next((s for s, m in log if m.startswith("rendu démarré")), None)
    sound = next((s for s, m in log if m.startswith("bande son")), None)
    end = next((s for s, m in log if m.startswith("rendu terminé")), None)
    if start is not None and end is not None and "assembly" not in derived:
        derived["assembly"] = (max(0.0, end - (sound or start)), "journal du worker (à la seconde)")
    out = []
    for key, label, instrumented in STAGES:
        entry: dict[str, Any] = {"stage": key, "label": label}
        if key in measured:
            data = measured[key]
            entry.update(source="mesuré (metrics.json)", wall_seconds=data.get("wall_seconds"),
                         cpu_seconds=data.get("cpu_seconds"), max_rss_bytes=data.get("max_rss_bytes"))
        elif key in derived:
            entry.update(source=derived[key][1], wall_seconds=round(derived[key][0], 3))
        else:
            entry.update(source="non instrumenté" if not instrumented else "absent du manifeste",
                         wall_seconds=None)
        if key == "visemes" and key not in measured:
            entry["note"] = "calculés par le moteur de scène pendant le rendu (inclus dans « remotion »)"
        if key == "mix" and key not in measured:
            entry["note"] = "inclus dans « assembly » tant que le worker ne l'horodate pas séparément"
        out.append(entry)
    return out


# -- dépenses -------------------------------------------------------------------------------------------------
def narration_chars(storyboard: dict[str, Any] | None) -> int:
    """Caractères à synthétiser : texte parlé de chaque scène (``spoken_text``)."""
    return sum(len(spoken_text(scene)) for scene in (storyboard or {}).get("scenes") or [])


def _voice_provider(part: dict[str, Any]) -> str:
    declared = str(part.get("provider") or "").strip().lower()
    if declared:
        return declared
    source = str(part.get("source") or "").lower()
    if "elevenlabs" in source:
        return "elevenlabs"
    if any(word in source for word in _FREE_SOURCES):
        return "local"
    return ""


def spend(directory: Path, storyboard: dict[str, Any] | None, book: Any = None) -> dict[str, Any]:
    """Dépenses *réelles connues* (``audio.json`` : ``cost.amount``/``cost.currency`` déclarés par le
    fournisseur) et *estimées* (tarifs ``pricing.toml``). Rien n'est inventé : sans tarif, ``unpriced``."""
    info = _read_json(directory / "audio.json")
    info = info if isinstance(info, dict) else {}
    chars = narration_chars(storyboard)
    known, estimated = [], []
    for name in ("voice", "music"):
        part = info.get(name) if isinstance(info.get(name), dict) else None
        if part is None:
            continue
        cost = part.get("cost") if isinstance(part.get("cost"), dict) else None
        provider = _voice_provider(part)
        if cost and cost.get("amount") is not None:
            known.append({"component": name, "provider": provider or "?", "amount": str(cost.get("amount")),
                          "currency": str(cost.get("currency") or "")})
        elif provider == "local":
            known.append({"component": name, "provider": "local", "amount": "0", "currency": "",
                          "note": "synthèse ou génération locale : aucun appel payant"})
    rate = book.rate("voice", "elevenlabs") if book is not None else None
    currency = getattr(book, "currency", "") if book is not None else ""
    if chars:
        line: dict[str, Any] = {"component": "voice", "provider": "elevenlabs", "characters": chars,
                                "unit": "par 1 000 caractères", "currency": currency}
        if rate is None:
            line.update(status="unpriced", amount=None, note="tarif ElevenLabs non configuré (pricing.toml)")
        else:
            line.update(status="priced", amount=str((Decimal(chars) / 1000 * rate).quantize(Decimal("0.0001"))))
        line["scope"] = "si la voix complète est (re)synthétisée par ElevenLabs"
        estimated.append(line)
    return {"known": known, "estimated": estimated, "narration_chars": chars}


# -- rapport --------------------------------------------------------------------------------------------------
def _check(name: str, value: float | None, limit: float, message: str) -> dict[str, Any]:
    status = "unknown" if value is None else ("ok" if value <= limit else "warn")
    return {"check": name, "value": value, "limit": limit, "status": status, "message": message}


def audit_render(directory: Path, *, book: Any = None, previous_storyboard: dict[str, Any] | None = None,
                 verify_files: bool = True) -> dict[str, Any]:
    """Rapport d'audit d'un dossier de rendu. ``previous_storyboard`` ajoute l'analyse d'invalidation."""
    directory = Path(directory)
    manifest = _read_json(directory / "manifest.json")
    if not isinstance(manifest, dict):
        raise FileNotFoundError("manifest.json absent ou illisible : aucun rendu terminé dans ce dossier")
    storyboard = load_storyboard(directory)
    metrics = _read_json(directory / METRICS_FILE)
    metrics = metrics if isinstance(metrics, dict) else None
    scenes = manifest.get("scenes") or []
    fps = manifest.get("fps") or 30
    video_seconds = manifest.get("duration_seconds") or manifest.get("expected_seconds") or 0
    stages = stage_timings(manifest, metrics, parse_log(directory))

    integrity: dict[str, Any] = {"storyboard_sha256": manifest.get("storyboard_sha256"),
                                 "mp4_sha256": manifest.get("sha256")}
    mp4 = next((p for p in (directory / "out.mp4", *sorted(directory.glob("*.mp4"))) if p.is_file()), None)
    if verify_files and mp4 is not None:
        integrity["mp4_file"] = mp4.name
        integrity["mp4_matches_manifest"] = _sha256(mp4) == manifest.get("sha256")
    if storyboard is not None:
        frozen = (storyboard.get("frozen") or {}).get("sha256")
        integrity["storyboard_frozen_sha256"] = frozen
        integrity["storyboard_assets_pinned"] = len((storyboard.get("frozen") or {}).get("assets") or [])

    scene_rows, checks = [], []
    for trace in scenes:
        seconds = trace.get("expected_seconds") or (trace.get("frames") or 0) / fps
        factor = round((trace.get("render_ms") or 0) / 1000 / seconds, 2) if seconds else None
        scene_rows.append({"index": trace.get("index"), "id": trace.get("id"), "status": trace.get("status"),
                           "reused": _is_reused(trace), "frames": trace.get("frames"),
                           "render_seconds": round((trace.get("render_ms") or 0) / 1000, 3),
                           "realtime_factor": factor, "sha256": trace.get("sha256")})
        if not _is_reused(trace):
            checks.append(_check(f"scene_{trace.get('index')}_realtime_factor", factor,
                                 THRESHOLDS["scene_realtime_factor"],
                                 f"scène {trace.get('index')} « {trace.get('id')} » : secondes de rendu par seconde"))
        drift = None
        if trace.get("duration_seconds") is not None and trace.get("expected_seconds") is not None:
            drift = round(abs(trace["duration_seconds"] - trace["expected_seconds"]), 3)
        checks.append(_check(f"scene_{trace.get('index')}_duration_drift", drift,
                             THRESHOLDS["duration_drift_seconds"], "écart durée réelle / attendue (s)"))
    total_render = (manifest.get("render_ms") or 0) / 1000
    checks.append(_check("render_realtime_factor", round(total_render / video_seconds, 2) if video_seconds else None,
                         THRESHOLDS["render_realtime_factor"], "rendu complet : secondes par seconde de vidéo"))
    size = manifest.get("size_bytes")
    checks.append(_check("bytes_per_video_second", round(size / video_seconds) if size and video_seconds else None,
                         THRESHOLDS["bytes_per_video_second"], "poids du MP4 final par seconde"))
    for stage in stages:
        if stage.get("max_rss_bytes") is not None:
            checks.append(_check(f"{stage['stage']}_peak_rss", stage["max_rss_bytes"], THRESHOLDS["peak_rss_bytes"],
                                 f"mémoire maximale de l'étape {stage['label']}"))
    disk = {"directory_bytes": _tree_bytes(directory), **disk_status(directory)}
    checks.append(_check("job_dir_bytes", disk["directory_bytes"], THRESHOLDS["job_dir_bytes"],
                         "taille du dossier de rendu"))

    reused = sum(1 for t in scenes if _is_reused(t))
    report: dict[str, Any] = {
        "schema": AUDIT_SCHEMA, "directory": directory.name, "mode": manifest.get("mode"),
        "video": {"seconds": video_seconds, "frames": manifest.get("frames"), "fps": fps,
                  "size": [manifest.get("width"), manifest.get("height")], "bytes": size},
        "render_seconds": total_render, "stages": stages,
        "cache": {"scenes": len(scenes), "reused": reused, "rendered": len(scenes) - reused,
                  "render_seconds_saved": round(sum(int(t.get("render_ms_saved") or 0) for t in scenes) / 1000, 3),
                  "fallbacks": len(manifest.get("fallback_scenes") or [])},
        "scenes": scene_rows, "integrity": integrity, "spend": spend(directory, storyboard, book),
        "disk": disk, "checks": checks, "audio": manifest.get("audio"),
    }
    if previous_storyboard is not None and storyboard is not None:
        report["invalidation"] = invalidation(previous_storyboard, storyboard)
    report["recommendations"] = recommendations(report)
    return report


def recommendations(report: dict[str, Any]) -> list[str]:
    """Recommandations concrètes tirées du rapport (aucune n'est appliquée automatiquement)."""
    out = []
    for check in report["checks"]:
        if check["status"] == "warn":
            out.append(f"Seuil dépassé — {check['message']} : {check['value']} > {check['limit']}.")
    missing = [s["label"] for s in report["stages"] if s.get("wall_seconds") is None]
    if missing:
        out.append("Étapes sans mesure : " + ", ".join(missing) + ". Les mesurer avec "
                   "`scripts/animation/audit_render.py measure --stage …` (ou StageRecorder) en attendant que le "
                   "worker horodate chaque étape.")
    integrity = report["integrity"]
    if integrity.get("mp4_matches_manifest") is False:
        out.append("Le MP4 ne correspond plus à l'empreinte du manifeste : ne pas le considérer comme validé.")
    inval = report.get("invalidation")
    if inval:
        out.extend(inval["findings"])
    if report["cache"]["scenes"] and report["cache"]["reused"] == 0:
        out.append("Aucune scène réutilisée dans ce rendu : une V2 qui ne retouche qu'une scène doit passer par le "
                   "cache de scène par dépendance (#121) pour ne refaire que celle-là.")
    for line in report["spend"]["estimated"]:
        if line.get("status") == "unpriced":
            out.append("Tarif ElevenLabs absent de pricing.toml : l'estimation de coût voix reste partielle.")
    if report["disk"].get("status") == "warn":
        out.append("Espace disque libre sous le seuil : lancer `audit_render.py clean <données> --apply` "
                   "(intermédiaires des tâches terminées) avant un nouveau rendu.")
    return out


# -- invalidation ---------------------------------------------------------------------------------------------
def _text_contents(scene: dict[str, Any]) -> list[str]:
    return [str(obj.get("text", {}).get("content") or "") for layer in scene["render"].get("layers") or []
            for obj in layer.get("objects") or [] if obj.get("type") == "text"]


def _without_texts(scene: dict[str, Any]) -> dict[str, Any]:
    clone = copy.deepcopy(scene["render"])
    clone.pop("speech", None)
    for layer in clone.get("layers") or []:
        for obj in layer.get("objects") or []:
            if obj.get("type") == "text":
                obj.get("text", {}).pop("content", None)
    return clone


def scene_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Nature des différences entre deux versions d'une scène."""
    changes = []
    if (old.get("intent") or {}).get("narration") != (new.get("intent") or {}).get("narration"):
        changes.append("narration")
    if {k: v for k, v in (old.get("intent") or {}).items() if k != "narration"} != \
            {k: v for k, v in (new.get("intent") or {}).items() if k != "narration"}:
        changes.append("intent")
    if old.get("duration_frames") != new.get("duration_frames"):
        changes.append("duration")
    if _text_contents(old) != _text_contents(new):
        changes.append("screen_text")
    if (old["render"].get("speech") or {}) != (new["render"].get("speech") or {}):
        changes.append("speech_cues")
    if canonical_json(_without_texts(old)) != canonical_json(_without_texts(new)):
        changes.append("visual")
    if old.get("id") != new.get("id"):
        changes.append("id")
    return changes


def spoken_text(scene: dict[str, Any]) -> str:
    """Texte réellement synthétisé pour une scène : ses répliques (comme la voix par scène de #164), à défaut
    sa narration."""
    cues = ((scene.get("render") or {}).get("speech") or {}).get("cues") or []
    if cues:
        return " ".join(str(cue.get("text") or "") for cue in cues)
    return str((scene.get("intent") or {}).get("narration") or "")


def invalidation(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Ce qu'une retouche fait refaire, avec la clé de cache actuelle et avec une clé par dépendance.

    - **Actuel** (``main``) : rendu de scène refait si le JSON canonique de la scène change (y compris ``intent``,
      jamais lu par le moteur) ou si ``voice.wav`` change ; la voix est une piste unique, donc resynthétisée en
      entier dès qu'un texte parlé change, ce qui invalide le rendu de TOUTES les scènes (synchro labiale).
    - **Par dépendance** (cible, cf. #121 et #164) : rendu refait seulement si le visuel, le texte d'écran, les
      répliques ou la durée de la scène changent, ou si SA voix change ; voix synthétisée et posée scène par scène,
      resynthétisée seulement quand le texte parlé de cette scène change.
    """
    old_scenes, new_scenes = old.get("scenes") or [], new.get("scenes") or []
    old_by_id = {scene.get("id"): scene for scene in old_scenes}
    new_ids = {scene.get("id") for scene in new_scenes}
    pairs = []
    for i, scene in enumerate(new_scenes, 1):
        previous = old_by_id.get(scene.get("id"))
        if previous is None and i <= len(old_scenes) and old_scenes[i - 1].get("id") not in new_ids:
            previous = old_scenes[i - 1]  # scène renommée à la même place
        pairs.append((i, scene, previous))
    voice_changed = [spoken_text(s) for s in old_scenes] != [spoken_text(s) for s in new_scenes]
    rows, findings = [], []
    current_render = target_render = current_chars = target_chars = 0
    for i, scene, previous in pairs:
        chars = len(spoken_text(scene))
        changes = ["new"] if previous is None else scene_changes(previous, scene)
        own_voice = previous is None or spoken_text(previous) != spoken_text(scene)
        current = bool(changes) or voice_changed
        target = bool({"new", "visual", "screen_text", "speech_cues", "duration"} & set(changes)) or own_voice
        current_render += current
        target_render += target
        current_chars += chars if voice_changed else 0
        target_chars += chars if own_voice else 0
        rows.append({"index": i, "id": scene.get("id"), "changes": changes, "render_current": current,
                     "render_by_dependency": target, "voice_resynth_by_scene": own_voice})
        if current and not target:
            reason = "seulement « " + ", ".join(changes) + " »" if changes else "scène identique"
            findings.append(f"Scène {i} « {scene.get('id')} » : rendu refait inutilement par la clé actuelle "
                            f"({reason}{' ; voix complète resynthétisée pour une autre scène' if voice_changed else ''}).")
        if "screen_text" in changes and not own_voice and voice_changed:
            findings.append(f"Scène {i} « {scene.get('id')} » : seul le texte d'écran change, mais la voix complète "
                            "est resynthétisée — la voix de cette scène, inchangée, ne devrait pas être refacturée.")
    if current_chars > target_chars:
        findings.append(f"Voix : {current_chars} caractères resynthétisés avec une piste unique contre "
                        f"{target_chars} avec une synthèse par scène (économie {current_chars - target_chars}).")
    return {"scenes": rows, "voice_changed": voice_changed,
            "render_current": current_render, "render_by_dependency": target_render,
            "voice_chars_current": current_chars, "voice_chars_by_scene": target_chars,
            "assumptions": ["actuel : voix = une piste unique resynthétisée dès qu'un texte parlé change",
                            "texte parlé d'une scène = ses répliques (render.speech.cues), à défaut sa narration",
                            "par dépendance : voix synthétisée et posée scène par scène (#164), cache de scène #121"],
            "findings": findings}


# -- mesures --------------------------------------------------------------------------------------------------
class StageRecorder:
    """Mesure des étapes (durée réelle, CPU du processus et de ses enfants, mémoire max) vers ``metrics.json``.

    ``max_rss_bytes`` est le maximum observé (processus courant ou enfant attendu) : une borne haute, pas la
    mémoire de la seule étape quand plusieurs étapes tournent dans le même processus.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        data = _read_json(self.path)
        self.data = data if isinstance(data, dict) and data.get("schema") == METRICS_SCHEMA else \
            {"schema": METRICS_SCHEMA, "stages": {}}

    @staticmethod
    def _usage() -> tuple[float, int]:
        own = resource.getrusage(resource.RUSAGE_SELF)
        kids = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime
        return cpu, max(own.ru_maxrss, kids.ru_maxrss) * 1024  # Linux : kio

    @contextmanager
    def stage(self, name: str) -> Iterator[dict[str, Any]]:
        started, (cpu0, _) = time.monotonic(), self._usage()
        entry: dict[str, Any] = {}
        try:
            yield entry
        finally:
            cpu1, rss = self._usage()
            entry.update(wall_seconds=round(time.monotonic() - started, 3), cpu_seconds=round(cpu1 - cpu0, 3),
                         max_rss_bytes=rss, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            self.data["stages"][name] = entry
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        temporary.replace(self.path)


def measure(command: list[str], recorder: StageRecorder, stage: str, timeout: float | None = None) -> int:
    """Lance une commande LOCALE et enregistre ses mesures sous ``stage``. Renvoie le code de sortie."""
    with recorder.stage(stage) as entry:
        done = subprocess.run(command, check=False, timeout=timeout)
        entry["exit_code"] = done.returncode
    return done.returncode


# -- disque ---------------------------------------------------------------------------------------------------
def disk_status(path: Path, min_free: float | None = None) -> dict[str, Any]:
    """Espace libre du système de fichiers de ``path`` comparé au seuil ``min_free_bytes``."""
    limit = THRESHOLDS["min_free_bytes"] if min_free is None else min_free
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return {"free_bytes": None, "min_free_bytes": limit, "status": "unknown"}
    return {"free_bytes": usage.free, "total_bytes": usage.total, "min_free_bytes": limit,
            "status": "ok" if usage.free >= limit else "warn"}


# Intermédiaires régénérables du worker, relatifs au dossier de tâche. Tout le reste est conservé.
_TEMP_PATTERNS = ("out.partial.mp4", ".*.tmp", ".*.tmp.wav", "scenes/.*.tmp", "scenes/*.render.mp4",
                  "scenes/*.render.mp4.log", "scenes/*-still.png", "scenes/*-still.png.log", "scenes/concat.txt",
                  "scenes/*-input.json")
_PROTECTED = ("out.mp4", "manifest.json", "job.json", "state.json", "subtitles.srt", "audio.wav", "voice.wav",
              "music.wav", "audio.json", "worker.log", METRICS_FILE)


def clean_plan(directory: Path) -> dict[str, Any]:
    """Fichiers temporaires supprimables d'une tâche, ou la raison pour laquelle on n'y touche pas."""
    from lody.animation import worker

    directory = Path(directory)
    state = (_read_json(directory / "state.json") or {}).get("state")
    if directory.is_symlink() or not (directory / "job.json").is_file():
        return {"task": directory.name, "skip": "pas un dossier de tâche", "files": [], "bytes": 0}
    if state not in TERMINAL_STATES:
        return {"task": directory.name, "skip": f"tâche non terminée ({state or 'état inconnu'})", "files": [],
                "bytes": 0}
    if worker.worker_alive(directory):
        return {"task": directory.name, "skip": "un worker tient encore la tâche", "files": [], "bytes": 0}
    files: set[Path] = set()
    for pattern in _TEMP_PATTERNS:
        for item in directory.glob(pattern):
            resolved = item.parent.resolve() / item.name
            if (item.is_file() and not item.is_symlink() and item.name not in _PROTECTED
                    and directory.resolve() in resolved.parents):
                files.add(item)
    ordered = sorted(files)
    return {"task": directory.name, "skip": "", "files": [str(p.relative_to(directory)) for p in ordered],
            "bytes": sum(p.stat().st_size for p in ordered)}


def clean(data_dir: Path, *, apply: bool = False) -> dict[str, Any]:
    """Plan (``apply=False``, défaut) ou suppression des intermédiaires de toutes les tâches terminées."""
    from lody.animation import worker

    root = worker.jobs_root(Path(data_dir))
    tasks, total = [], 0
    if root.is_dir():
        for project in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                              and worker.is_safe_part(p.name)):
            for task in sorted(t for t in project.iterdir() if t.is_dir() and worker.is_safe_part(t.name)):
                plan = clean_plan(task)
                if apply and not plan["skip"]:
                    for relative in plan["files"]:
                        (task / relative).unlink(missing_ok=True)
                total += plan["bytes"]
                tasks.append({"project": project.name, **plan})
    return {"applied": apply, "bytes": total, "tasks": tasks, "disk": disk_status(Path(data_dir))}


# -- HTML -----------------------------------------------------------------------------------------------------
def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "oui" if value else "non"
    return html.escape(str(value))


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_cell(c)}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def to_html(report: dict[str, Any]) -> str:
    """Page HTML autonome (aucune ressource externe) du rapport."""
    parts = [
        "<!doctype html><html lang='fr'><meta charset='utf-8'>",
        f"<title>Audit rendu 2D — {html.escape(report['directory'])}</title>",
        "<style>body{font:14px system-ui,sans-serif;margin:2em;max-width:70em}table{border-collapse:collapse;"
        "margin:.5em 0 1.5em}td,th{border:1px solid #ccc;padding:.25em .6em;text-align:left}"
        ".warn{color:#b45309;font-weight:600}.ok{color:#15803d}</style>",
        f"<h1>Audit du rendu 2D « {html.escape(report['directory'])} »</h1>",
        f"<p>{_cell(report['video']['seconds'])} s de vidéo, rendu en {_cell(report['render_seconds'])} s ; "
        f"scènes : {report['cache']['scenes']} dont {report['cache']['reused']} réutilisée(s) "
        f"({report['cache']['render_seconds_saved']} s de rendu économisées), "
        f"{report['cache']['fallbacks']} repli(s).</p>",
        "<h2>Étapes</h2>",
        _table(["Étape", "Durée (s)", "CPU (s)", "RAM max (octets)", "Source"],
               [[s["label"], s.get("wall_seconds"), s.get("cpu_seconds"), s.get("max_rss_bytes"),
                 s["source"] + (f" — {s['note']}" if s.get("note") else "")] for s in report["stages"]]),
        "<h2>Scènes</h2>",
        _table(["#", "Scène", "Statut", "Réutilisée", "Rendu (s)", "× temps réel", "Empreinte"],
               [[r["index"], r["id"], r["status"], r["reused"], r["render_seconds"], r["realtime_factor"],
                 (r["sha256"] or "")[:16]] for r in report["scenes"]]),
        "<h2>Contrôles</h2>",
        "<table><thead><tr><th>Contrôle</th><th>Valeur</th><th>Seuil</th><th>État</th></tr></thead><tbody>"
        + "".join(f"<tr><td>{_cell(c['message'])}</td><td>{_cell(c['value'])}</td><td>{_cell(c['limit'])}</td>"
                  f"<td class='{c['status']}'>{_cell(c['status'])}</td></tr>" for c in report["checks"])
        + "</tbody></table>",
        "<h2>Dépenses</h2>",
        _table(["Type", "Composant", "Fournisseur", "Montant", "Détail"],
               [["connue", k["component"], k["provider"], f"{k['amount']} {k.get('currency', '')}".strip(),
                 k.get("note", "")] for k in report["spend"]["known"]]
               + [["estimée", e["component"], e["provider"],
                   (f"{e['amount']} {e['currency']}" if e.get("amount") is not None else None),
                   f"{e['characters']} caractères — {e['scope']}" + (f" ; {e['note']}" if e.get("note") else "")]
                  for e in report["spend"]["estimated"]]),
    ]
    if report.get("invalidation"):
        inval = report["invalidation"]
        parts += ["<h2>Invalidation</h2>",
                  f"<p>Rendus refaits : {inval['render_current']} (clé actuelle) contre "
                  f"{inval['render_by_dependency']} (par dépendance) ; caractères de voix : "
                  f"{inval['voice_chars_current']} contre {inval['voice_chars_by_scene']}.</p>",
                  _table(["#", "Scène", "Changements", "Refaite (actuel)", "Refaite (dépendance)", "Voix à refaire"],
                         [[r["index"], r["id"], ", ".join(r["changes"]) or "aucun", r["render_current"],
                           r["render_by_dependency"], r["voice_resynth_by_scene"]] for r in inval["scenes"]])]
    parts += ["<h2>Recommandations</h2><ul>"
              + "".join(f"<li>{html.escape(r)}</li>" for r in report["recommendations"]) + "</ul>",
              f"<h2>Intégrité</h2><pre>{html.escape(json.dumps(report['integrity'], indent=1))}</pre></html>"]
    return "\n".join(parts)
