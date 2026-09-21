"""Connecteur de démonstration : simule une génération complète sans appeler aucun fournisseur.

Explicite et visible dans l'interface. L'état est déduit de l'heure écoulée depuis l'envoi (fichier
``meta.json`` sur le volume de données), donc une « génération » simulée survit à un redémarrage du
conteneur. La vidéo est une mire synthétique produite localement par ffmpeg (aucun réseau).
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from lody import settings
from lody.generation.models import (
    ExternalTask,
    GenerationRequest,
    Capability,
    CapabilityState,
    CapabilityStatus,
    GenerationResult,
    PreflightReport,
    RemoteState,
    TaskSnapshot,
)
from lody.generation.provider import VideoGenerationProvider
from lody.generation.safety import ALLOWED_VIDEO_SUFFIXES, is_safe_task_id, resolve_within

PROVIDER_ID = "demo"
DEMO_VIDEO_SECONDS = 8.0
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# (seuil en « pas », progression, étape) — mêmes jalons que le moteur réel.
_TIMELINE = ((0, 0, "En attente dans la file"), (1, 5, "Préparation"), (2, 20, "Génération de la voix"),
             (3, 30, "Création des sous-titres"), (4, 40, "Génération des images"), (5, 60, "Montage de la vidéo"))
_DONE_STEP = 6


def default_video_maker(target: Path) -> None:
    """Mire vidéo + tonalité avec un bandeau « DÉMO » ; sans ffmpeg, un fichier factice."""
    base = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100", "-t", str(int(DEMO_VIDEO_SECONDS))]
    encode = ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(target)]
    text = f"drawtext=fontfile={FONT}:text='DEMONSTRATION':fontcolor=white:fontsize=26:box=1:boxcolor=black@0.65:boxborderw=8:x=(w-text_w)/2:y=h*0.06"
    for extra in (["-vf", text], []):
        try:
            done = subprocess.run(base + extra + encode, capture_output=True, timeout=90, check=False)
        except (OSError, subprocess.SubprocessError):
            break
        if done.returncode == 0 and target.exists():
            return
    target.write_bytes(b"lody-demo-placeholder")


def demo_script(subject: str) -> str:
    topic = subject.strip().rstrip(".?!") or "ce sujet"
    return (
        f"Aujourd’hui, on parle de : {topic}. C’est un script de démonstration, écrit sans aucun fournisseur. "
        "Première idée : on part d’une explication simple, sans jargon. "
        "Deuxième idée : un exemple concret pour bien comprendre. "
        "Attention à un piège courant : aller trop vite et croire que tout est garanti. "
        "Ce qu’il faut retenir : comprendre avant de décider. "
        "Dans le prochain épisode, on va un cran plus loin."
    )


class DemoConnector(VideoGenerationProvider):
    id = PROVIDER_ID
    display_name = "Démonstration (simulation)"
    is_demo = True
    supports_thumbnail_background = True  # simulé : montre le parcours estimation → confirmation, sans coût réel

    def __init__(self, root: Path | None = None, clock: Callable[[], float] = time.time, step_seconds: float = 3.0,
                 make_video: Callable[[Path], None] = default_video_maker):
        self._root = Path(root) if root else settings.data_dir()
        self._clock = clock
        self._step = max(step_seconds, 0.001)
        self._make_video = make_video

    def _dir(self, task_id: str) -> Path:
        return self._root / "demo" / task_id

    def preflight(self, request: GenerationRequest) -> PreflightReport:
        """Une simulation n'a besoin d'aucun fournisseur : tout est « prêt »."""
        return PreflightReport(tuple(
            CapabilityStatus(capability, CapabilityState.READY, message="Simulation : aucun fournisseur n’est appelé.")
            for capability in (Capability.TEXT, Capability.VISUAL, Capability.VOICE, Capability.MUSIC,
                               Capability.ENGINE, Capability.STORAGE, Capability.SETTINGS)))

    def describe_script_request(self, request: GenerationRequest) -> dict:
        return {"video_subject": request.subject, "video_language": request.language, "note": "simulation : aucun appel"}

    def write_script(self, request: GenerationRequest) -> str:
        return demo_script(request.subject)

    def submit(self, request: GenerationRequest, idempotency_key: str) -> ExternalTask:
        task_id = "demo-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:16]
        directory = self._dir(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        meta = directory / "meta.json"
        if not meta.exists():  # même clé → même tâche : jamais deux simulations
            meta.write_text(json.dumps({"started": self._clock(), "scenes": len(request.visual_prompts), "script": request.script[:8000]},
                                       ensure_ascii=False), encoding="utf-8")
        return ExternalTask(PROVIDER_ID, task_id)

    def _started(self, task: ExternalTask) -> float | None:
        if not is_safe_task_id(task.task_id):
            return None
        try:
            return float(json.loads((self._dir(task.task_id) / "meta.json").read_text(encoding="utf-8"))["started"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def poll(self, task: ExternalTask) -> TaskSnapshot:
        started = self._started(task)
        if started is None:
            return TaskSnapshot(RemoteState.UNKNOWN)
        steps = int((self._clock() - started) // self._step)
        if steps >= _DONE_STEP:
            ref = self._ensure_video(task)
            return TaskSnapshot(RemoteState.DONE, 100, "Terminée",
                                result=GenerationResult(ref, DEMO_VIDEO_SECONDS, assets=({"kind": "video", "ref": ref},)))
        _, progress, label = [entry for entry in _TIMELINE if steps >= entry[0]][-1]
        return TaskSnapshot(RemoteState.QUEUED if progress == 0 else RemoteState.RUNNING, progress, label)

    def list_scene_images(self, task: ExternalTask) -> list[dict[str, str]]:
        """Trois fonds de démonstration synthétiques (jamais de texte), créés localement."""
        if self._started(task) is None:
            return []
        from lody.generation.thumbnail import placeholder_background

        images = []
        for number in (1, 2, 3):
            target = self._dir(task.task_id) / f"scene-{number}.png"
            if not target.exists():
                buffer = io.BytesIO()
                placeholder_background(f"{task.task_id}-{number}", (512, 768)).save(buffer, format="PNG")
                target.write_bytes(buffer.getvalue())
            images.append({"ref": f"demo/{task.task_id}/{target.name}", "name": target.name})
        return images

    def read_subtitles(self, task: ExternalTask) -> str | None:
        """SRT de démonstration : le script réparti régulièrement sur la durée simulée."""
        if self._started(task) is None:
            return None
        try:
            script = json.loads((self._dir(task.task_id) / "meta.json").read_text(encoding="utf-8")).get("script", "")
        except (OSError, ValueError):
            return None
        from lody.generation import subtitles
        from lody.generation.storyboard import split_sentences

        sentences = split_sentences(script) or ["Vidéo de démonstration"]
        step = int(DEMO_VIDEO_SECONDS * 1000 / len(sentences))
        cues = [subtitles.Cue(i + 1, i * step, (i + 1) * step - 40, sentence) for i, sentence in enumerate(sentences)]
        return subtitles.to_srt(cues)

    def generate_thumbnail_background(self, request: GenerationRequest, prompt: str) -> bytes:
        from lody.generation.thumbnail import placeholder_background

        buffer = io.BytesIO()
        placeholder_background(prompt, (1024, 1536)).save(buffer, format="PNG")
        return buffer.getvalue()

    def _ensure_video(self, task: ExternalTask) -> str:
        ref = f"demo/{task.task_id}/final-1.mp4"
        target = self._dir(task.task_id) / "final-1.mp4"
        if not target.exists():
            self._make_video(target)
        return ref

    def recover(self, task: ExternalTask) -> GenerationResult | None:
        try:
            self.resolve_asset(task, f"demo/{task.task_id}/final-1.mp4")
        except ValueError:
            return None
        return GenerationResult(f"demo/{task.task_id}/final-1.mp4", DEMO_VIDEO_SECONDS)

    def resolve_asset(self, task: ExternalTask, ref: str, suffixes: tuple[str, ...] = ALLOWED_VIDEO_SUFFIXES) -> Path:
        if not is_safe_task_id(task.task_id):
            raise ValueError("identifiant de tâche invalide")
        return resolve_within(self._root, ref, prefix=f"demo/{task.task_id}", suffixes=suffixes)
