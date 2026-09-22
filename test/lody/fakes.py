"""Faux connecteur scriptable : aucun réseau, aucun fournisseur, tout est compté."""

from __future__ import annotations

from pathlib import Path

from lody.generation.models import (
    ExternalTask,
    GenerationRequest,
    Capability,
    CapabilityState,
    CapabilityStatus,
    GenerationResult,
    PreflightReport,
    ProviderError,
    ReadinessIssue,
    RemoteState,
    TaskSnapshot,
)
from lody.generation.provider import VideoGenerationProvider
from lody.generation.safety import ALLOWED_VIDEO_SUFFIXES, resolve_within

def _png(seed: str, size=(256, 384)) -> bytes:
    import io

    from lody.generation.thumbnail import placeholder_background

    buffer = io.BytesIO()
    placeholder_background(seed, size).save(buffer, format="PNG")
    return buffer.getvalue()


SCRIPT = (
    "La blockchain est un registre partagé. Chaque bloc contient des transactions. "
    "Personne ne peut modifier le passé sans que tout le monde le voie. "
    "Dans un jeu, cela permet de posséder vraiment ses objets. "
    "Attention : posséder un objet ne veut pas dire qu’il prendra de la valeur. "
    "Retiens l’idée : comprendre avant de dépenser. Rendez-vous au prochain épisode."
)


class ScriptedConnector(VideoGenerationProvider):
    id = "scripted"
    display_name = "Faux moteur"
    supports_thumbnail_background = False   # activable par les tests : le moteur historique ne sait pas générer une image seule

    def __init__(self, root: Path, *, issues: list[ReadinessIssue] | None = None):
        self.root = Path(root)
        self.issues = issues or []
        self.calls: list[str] = []
        self.submitted: list[GenerationRequest] = []
        self.snapshots: list[TaskSnapshot | ProviderError] = []
        self.script_error: ProviderError | None = None
        self.submit_error: ProviderError | None = None
        self.recover_result: GenerationResult | None = None
        self.write_video = True
        self.scene_images: list[dict] = []
        self.subtitles_text: str | None = None
        self.background_bytes: bytes | None = None
        self.background_error: ProviderError | None = None
        self.narrative_blocks_received: list[str] = []  # #36 : ce que write_script a réellement reçu, dans l'ordre

    # -- scénario -----------------------------------------------------------
    def queue(self, *items: TaskSnapshot | ProviderError) -> None:
        self.snapshots.extend(items)

    def done(self, task_id: str = "task-0001", **kwargs) -> TaskSnapshot:
        ref = f"tasks/{task_id}/final-1.mp4"
        if self.write_video:
            path = self.root / ref
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake-mp4")
        return TaskSnapshot(RemoteState.DONE, 100, "Terminée", result=GenerationResult(
            ref, 52.4, assets=({"kind": "video", "ref": ref},), warnings=kwargs.get("warnings", ())))

    # -- interface ------------------------------------------------------------
    def list_scene_images(self, task):
        self.calls.append("list_scene_images")
        return list(self.scene_images)

    def read_subtitles(self, task):
        self.calls.append("read_subtitles")
        return self.subtitles_text

    def generate_thumbnail_background(self, request, prompt):
        self.calls.append("thumbnail_background")   # appel payant : les tests comptent chaque appel
        if self.background_error:
            raise self.background_error
        return self.background_bytes if self.background_bytes is not None else _png(prompt)

    def describe_params(self, request):
        return {"video_aspect": request.aspect, "video_language": request.language, "subtitle_display_mode": "sentence"}

    def preflight(self, request):
        self.calls.append("check_ready")
        items = {c: CapabilityStatus(c, CapabilityState.READY, message="Prêt.") for c in Capability}
        for index, issue in enumerate(self.issues):  # chaque problème du scénario bloque (ou avertit) une capacité
            capability = list(Capability)[index % len(Capability)]
            state = CapabilityState.NOT_CONFIGURED if issue.blocking else CapabilityState.READY
            items[capability] = CapabilityStatus(capability, state, message=issue.message, fix="platform")
        return PreflightReport(tuple(items.values()))

    def describe_script_request(self, request, narrative_block=""):
        return {"video_subject": request.subject, "video_language": request.language,
                "video_script_prompt": narrative_block or "(aucun bloc narratif)"}

    def write_script(self, request, narrative_block=""):
        self.calls.append("write_script")
        self.narrative_blocks_received.append(narrative_block)
        if self.script_error:
            raise self.script_error
        return SCRIPT

    def submit(self, request, idempotency_key):
        self.calls.append("submit")
        if self.submit_error:
            raise self.submit_error
        self.submitted.append(request)
        return ExternalTask(self.id, f"task-{len(self.submitted):04d}")

    def poll(self, task):
        self.calls.append("poll")
        item = self.snapshots.pop(0) if self.snapshots else TaskSnapshot(RemoteState.RUNNING, 20, "Voix")
        if isinstance(item, ProviderError):
            raise item
        return item

    def recover(self, task):
        return self.recover_result

    def resolve_asset(self, task, ref, suffixes=ALLOWED_VIDEO_SUFFIXES):
        return resolve_within(self.root, ref, prefix=f"tasks/{task.task_id}", suffixes=suffixes)


class SyncExecutor:
    """Exécute immédiatement dans le thread appelant (déterministe)."""

    def __init__(self):
        self.jobs = 0

    def submit(self, fn, *args):
        self.jobs += 1
        return fn(*args)


class DeferredExecutor:
    """Garde les tâches en attente : simule un redémarrage avant l'exécution du worker."""

    def __init__(self):
        self.pending: list = []

    def submit(self, fn, *args):
        self.pending.append((fn, args))
