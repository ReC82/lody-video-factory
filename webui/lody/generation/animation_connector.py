"""Fournisseur « Animation 2D » (#120) : rend le storyboard animé figé d'une production avec le worker 2D.

Nouveau ``VideoGenerationProvider`` À CÔTÉ du moteur historique (``mpt_connector``), qui reste le fournisseur
par défaut et n'est pas modifié. Il est choisi par le mode de rendu du projet (``lody.animation.modes``) :
Animation 2D (échec d'une scène = échec) ou Hybride (repli d'image fixe par scène, signalé).

Ce qu'il consomme : ``productions.animation`` (storyboard ``lody.animation/1`` figé par #119), transmis par le
service dans ``GenerationRequest.animation = {"project_id", "storyboard"}``. Ce qu'il produit : un dossier de
travail isolé par projet et par tâche (``<données>/animation/<projet>/<tâche>/``, voir ``lody.animation.worker``),
rendu par un processus worker séparé, à priorité basse, un seul rendu à la fois sur la machine.

Aucun appel payant : pas d'appel texte (la narration vient du storyboard), pas d'image générée, pas de TTS
(la voix off du rendu 2D viendra avec la piste audio du storyboard ; la vidéo est muette en attendant, ce que
le préflight annonce). Le coût fournisseur est donc nul ; seule la durée de rendu est estimée.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lody import settings
from lody.animation import modes
from lody.animation import worker as animation_worker
from lody.animation.assets import FILE_EXTENSIONS, AnimationAssetRepository, AssetNotFound
from lody.animation.contract import (
    AnimationValidationError,
    duration_seconds,
    iter_asset_refs,
    total_frames,
    validate_document,
)
from lody.generation.models import (
    Capability,
    CapabilityState,
    CapabilityStatus,
    ErrorKind,
    ExternalTask,
    GenerationRequest,
    GenerationResult,
    PreflightReport,
    ProviderError,
    RemoteState,
    SceneUnits,
    TaskSnapshot,
)
from lody.generation.provider import VideoGenerationProvider
from lody.generation.safety import ALLOWED_VIDEO_SUFFIXES, resolve_within, sanitize

logger = logging.getLogger("lody.animation.provider")

PROVIDER_ID = modes.ANIMATION_PROVIDER
RENDER_SPEED_FACTOR = 7.0       # ≈ 7× le temps réel à concurrence 1 (benchmark #118, hôte 2 vCPU)
DISK_PER_SECOND = 2 * 1024**2   # rendus de scène + vidéo finale : ≈ 2 Mo par seconde de vidéo, large
DISK_MARGIN = 200 * 1024**2
STALE_AFTER = 45.0              # battement absent depuis 45 s et aucun worker : la tâche est reprise
MAX_RESTARTS = 3
WORKER_FIX = "LODY_ANIMATION_RENDERER"

Launcher = Callable[[Path], None]


def _reap(process: subprocess.Popen) -> None:
    threading.Thread(target=process.wait, name="lody-animation-reaper", daemon=True).start()


def spawn_worker(directory: Path) -> None:
    """Lance ``python -m lody.animation.worker <dossier>`` dans un processus détaché, sans aucun secret.

    L'environnement ne contient que le strict nécessaire (chemins, langue, commande du moteur de scène) :
    aucune clé de l'application ne peut atteindre le moteur de rendu ni ses journaux.
    """
    env = animation_worker.minimal_env()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    for key in (animation_worker.RENDERER_ENV, animation_worker.SCENE_TIMEOUT_ENV):
        if os.environ.get(key):
            env[key] = os.environ[key]
    with (directory / "worker.stderr").open("ab") as stderr:
        process = subprocess.Popen([sys.executable, "-m", "lody.animation.worker", str(directory)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                                   cwd=str(directory), env=env, start_new_session=True)
    _reap(process)


def binding(request: GenerationRequest) -> tuple[str, dict[str, Any]] | None:
    """(projet, storyboard figé) transmis par le service, ou ``None`` (aucun storyboard attaché)."""
    data = request.animation or {}
    project_id, storyboard = data.get("project_id"), data.get("storyboard")
    if not isinstance(project_id, str) or not isinstance(storyboard, dict) or not storyboard:
        return None
    return project_id, storyboard


def narration_of(storyboard: dict[str, Any]) -> str:
    """Texte parlé du storyboard : narration de chaque scène, sinon ses repères de parole."""
    parts = []
    for scene in storyboard.get("scenes") or []:
        narration = str((scene.get("intent") or {}).get("narration") or "").strip()
        if not narration:
            cues = ((scene.get("render") or {}).get("speech") or {}).get("cues") or []
            narration = " ".join(str(cue.get("text", "")).strip() for cue in cues).strip()
        if narration:
            parts.append(narration)
    return " ".join(parts)


class AnimationConnector(VideoGenerationProvider):
    id = PROVIDER_ID
    display_name = "Animation 2D"

    def __init__(self, root: Path | None = None, asset_repo: AnimationAssetRepository | None = None,
                 launcher: Launcher = spawn_worker, renderer: str | None = None, ffmpeg: str | None = None,
                 clock: Callable[[], float] = time.time):
        self._root = Path(root) if root else None
        self._asset_repo = asset_repo
        self._launcher = launcher
        self._renderer = renderer
        self._ffmpeg = ffmpeg
        self._clock = clock

    # -- emplacements -----------------------------------------------------------------------------------------
    @property
    def root(self) -> Path:
        return self._root or settings.data_dir()

    def assets(self) -> AnimationAssetRepository:
        if self._asset_repo is None:
            self._asset_repo = AnimationAssetRepository(settings.db_path())
        return self._asset_repo

    def _renderer_command(self) -> list[str]:
        return animation_worker.renderer_command(self._renderer)

    def _ffmpeg_exe(self) -> str | None:
        return self._ffmpeg or animation_worker.ffmpeg_exe()

    @staticmethod
    def mode_of(request: GenerationRequest) -> str:
        return request.render_mode if modes.is_animated(request.render_mode) else modes.ANIMATION

    # -- estimation ------------------------------------------------------------------------------------------
    def plan_units(self, request: GenerationRequest) -> SceneUnits:
        bound = binding(request)
        scenes = len(bound[1].get("scenes") or []) if bound else 1
        return SceneUnits(text_calls=0, scenes=(max(1, scenes),) * 2, images=(0, 0), characters=(0, 0),
                          music_tracks=0, audio_seconds=(0, 0))

    def describe_script_request(self, request: GenerationRequest, narrative_block: str = "",
                                dialogue_character: str = "") -> dict:
        return {"source": "storyboard animé", "note": "narration tirée du storyboard : aucun appel texte"}

    def write_script(self, request: GenerationRequest, narrative_block: str = "", dialogue_character: str = "") -> str:
        bound = binding(request)
        text = narration_of(bound[1]) if bound else ""
        if not text:
            raise ProviderError(ErrorKind.REJECTED, "Le storyboard animé ne contient aucune narration ni réplique : "
                                "ajoute une narration aux scènes ou fournis ton script.", stage="script")
        return text

    def trace_prompts(self, request: GenerationRequest) -> dict:
        bound = binding(request)
        scenes = bound[1].get("scenes") or [] if bound else []
        return {"scenes": [{"index": i, "prompt_sent": "", "final_prompt": "", "engine_template_applied": False,
                            "animated_scene": scene.get("id", "")} for i, scene in enumerate(scenes, 1)],
                "image_template": {"applied": False, "text": "", "origin": "aucun : rendu 2D, aucune image générée"}}

    def describe_params(self, request: GenerationRequest) -> dict:
        bound = binding(request)
        params: dict[str, Any] = {"mode": self.mode_of(request), "renderer_configured": bool(self._renderer_command())}
        if bound:
            storyboard = bound[1]
            seconds = duration_seconds(storyboard)
            params.update({
                "canvas": dict(storyboard.get("canvas") or {}), "scenes": len(storyboard.get("scenes") or []),
                "frames": total_frames(storyboard), "duration_seconds": seconds,
                "estimated_render_seconds": round(seconds * RENDER_SPEED_FACTOR),
            })  # pas d'empreinte ici : jeton opaque refusé par le garde-fou de ``params`` (elle est dans ``animation``)
        return params

    # -- préflight (gratuit) ---------------------------------------------------------------------------------
    def _asset_files(self, project_id: str, storyboard: dict[str, Any]) -> tuple[dict[str, dict], dict[str, str]]:
        """Fichiers des versions figées, vérifiés sur disque : ({"id@v": {role: {...}}}, erreurs par chemin)."""
        files: dict[str, dict] = {}
        errors: dict[str, str] = {}
        for path, ref in iter_asset_refs(storyboard):
            key = f"{ref['id']}@{ref['version']}"
            if key in files:
                continue
            try:
                version = self.assets().get_version(project_id, ref["id"], ref["version"])
            except AssetNotFound:
                errors[path] = "asset ou version introuvable dans le catalogue de ce projet."
                continue
            entries = {}
            for item in version.manifest["files"]:
                try:
                    source = resolve_within(self.root, item["path"], suffixes=FILE_EXTENSIONS)
                except ValueError:
                    errors[path] = f"fichier « {item['path']} » absent du stockage."
                    break
                if animation_worker.sha256_file(source) != item["sha256"]:
                    errors[path] = f"fichier « {item['path']} » modifié depuis sa publication (empreinte différente)."
                    break
                entries[item["role"]] = {"source": source, "path": item["path"], "sha256": item["sha256"]}
            else:
                files[key] = entries
        return files, errors

    def preflight(self, request: GenerationRequest) -> PreflightReport:
        mode = self.mode_of(request)
        bound = binding(request)
        items = [
            CapabilityStatus(Capability.TEXT, CapabilityState.NOT_NEEDED, requested="storyboard animé",
                             message="La narration vient du storyboard animé : aucun appel texte."),
            CapabilityStatus(Capability.VOICE, CapabilityState.DISABLED, requested="aucune",
                             message="Rendu 2D : pas encore de voix off, la vidéo est muette ; les répliques du "
                                     "storyboard deviennent les sous-titres."),
            CapabilityStatus(Capability.MUSIC, CapabilityState.DISABLED, requested="aucune",
                             message="Rendu 2D : pas de musique pour l'instant."),
        ]
        # Visuels : le storyboard figé et les fichiers de ses assets.
        storyboard_ok = False
        if not bound:
            items.append(CapabilityStatus(
                Capability.VISUAL, CapabilityState.NOT_CONFIGURED, requested="storyboard animé",
                message="Aucun storyboard animé n'est attaché : importe le storyboard (JSON lody.animation/1) "
                        "de cette vidéo avant de la préparer.", fix="project"))
        else:
            project_id, storyboard = bound
            try:
                storyboard = validate_document(storyboard, require_pinned=True)
                files, errors = self._asset_files(project_id, storyboard)
            except AnimationValidationError as error:
                files, errors = {}, error.errors
            if errors:
                first = next(iter(errors.items()))
                items.append(CapabilityStatus(
                    Capability.VISUAL, CapabilityState.UNAVAILABLE, requested="storyboard animé",
                    message=f"Le storyboard animé n'est pas rendable : {first[0]} — {first[1]}"
                            + (f" (+{len(errors) - 1} autre(s))" if len(errors) > 1 else ""),
                    fix="project", admin=sanitize("; ".join(f"{k}: {v}" for k, v in errors.items()), 600)))
            else:
                storyboard_ok = True
                scenes = len(storyboard["scenes"])
                items.append(CapabilityStatus(
                    Capability.VISUAL, CapabilityState.READY, requested="storyboard animé",
                    configured=f"{scenes} scène(s), {duration_seconds(storyboard):g} s, {len(files)} asset(s)",
                    message="Storyboard animé figé et fichiers d'assets vérifiés."))
        # Moteur : commande du moteur de scène + ffmpeg pour l'assemblage.
        command = self._renderer_command()
        ffmpeg = self._ffmpeg_exe()
        if not command:
            items.append(CapabilityStatus(
                Capability.ENGINE, CapabilityState.NOT_CONFIGURED, requested="moteur 2D",
                message="Le moteur de rendu 2D n'est pas installé sur ce serveur : demande à l'administrateur "
                        "de le configurer.", fix="platform",
                admin=f"Variable {WORKER_FIX} absente (commande du moteur de scène, voir docs/lody-animation-provider.md)."))
        elif not (shutil.which(command[0]) or Path(command[0]).is_file()):
            items.append(CapabilityStatus(
                Capability.ENGINE, CapabilityState.UNAVAILABLE, requested="moteur 2D",
                message="Le moteur de rendu 2D configuré est introuvable sur ce serveur.", fix="platform",
                admin=f"{WORKER_FIX} : exécutable « {sanitize(Path(command[0]).name, 80)} » introuvable."))
        elif not ffmpeg:
            items.append(CapabilityStatus(
                Capability.ENGINE, CapabilityState.UNAVAILABLE, requested="moteur 2D",
                message="L'outil d'assemblage vidéo (ffmpeg) est introuvable sur ce serveur.", fix="platform",
                admin="ffmpeg absent du PATH et imageio-ffmpeg indisponible."))
        else:
            items.append(CapabilityStatus(
                Capability.ENGINE, CapabilityState.READY, requested="moteur 2D", configured="worker 2D",
                message="Worker 2D disponible : un rendu à la fois, priorité basse."))
        # Stockage : place libre pour les rendus de scène et la vidéo finale.
        seconds = duration_seconds(bound[1]) if storyboard_ok and bound else 60
        need = int(seconds * DISK_PER_SECOND + DISK_MARGIN)
        try:
            target = self.root
            while not target.exists() and target != target.parent:
                target = target.parent
            free = shutil.disk_usage(target).free
        except OSError:
            free = None
        if free is None:
            items.append(CapabilityStatus(Capability.STORAGE, CapabilityState.UNVERIFIED,
                                          message="Espace disque impossible à vérifier.", fix="platform"))
        elif free < need:
            items.append(CapabilityStatus(
                Capability.STORAGE, CapabilityState.UNAVAILABLE,
                message=f"Espace disque insuffisant pour le rendu ({need // 1024**2} Mo nécessaires).", fix="platform"))
        else:
            items.append(CapabilityStatus(Capability.STORAGE, CapabilityState.READY, message="Espace disque suffisant."))
        # Paramètres : mode et plafonds.
        estimate = f" Rendu estimé : environ {max(1, round(seconds * RENDER_SPEED_FACTOR / 60))} min." if storyboard_ok else ""
        if storyboard_ok and seconds > animation_worker.MAX_TOTAL_SECONDS:
            items.append(CapabilityStatus(
                Capability.SETTINGS, CapabilityState.UNAVAILABLE, requested=modes.RENDER_MODES[mode],
                message=f"Storyboard trop long : {animation_worker.MAX_TOTAL_SECONDS // 60} minutes au plus.", fix="project"))
        else:
            items.append(CapabilityStatus(
                Capability.SETTINGS, CapabilityState.READY, requested=modes.RENDER_MODES[mode],
                configured=modes.RENDER_MODES[mode], message=modes.DESCRIPTIONS[mode] + estimate))
        return PreflightReport(tuple(items))

    # -- tâche -------------------------------------------------------------------------------------------------
    @staticmethod
    def task_id_for(idempotency_key: str) -> str:
        import hashlib

        return "anim-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:20]

    def submit(self, request: GenerationRequest, idempotency_key: str) -> ExternalTask:
        bound = binding(request)
        if not bound:
            raise ProviderError(ErrorKind.REJECTED, "Aucun storyboard animé n'est attaché à cette production.",
                                stage="submit")
        project_id, storyboard = bound
        task_id = self.task_id_for(idempotency_key)
        try:
            directory = animation_worker.job_dir(self.root, project_id, task_id)
        except ValueError:
            raise ProviderError(ErrorKind.REJECTED, "Identifiant de projet invalide pour le rendu 2D.") from None
        if (directory / "job.json").is_file():  # même clé → même tâche : jamais deux rendus
            self._ensure_worker(directory)
            return ExternalTask(PROVIDER_ID, task_id)
        try:
            storyboard = validate_document(storyboard, require_pinned=True)
        except AnimationValidationError:
            raise ProviderError(ErrorKind.REJECTED, "Le storyboard animé de cette production est invalide.") from None
        files, errors = self._asset_files(project_id, storyboard)
        if errors:
            raise ProviderError(ErrorKind.REJECTED, "Fichiers d'assets absents ou modifiés : "
                                + sanitize(next(iter(errors.values())), 200), stage="submit")
        staging = directory.with_name(f".{task_id}.staging")
        shutil.rmtree(staging, ignore_errors=True)
        (staging / "assets").mkdir(parents=True)
        job_assets: dict[str, dict] = {}
        for key, entries in files.items():
            asset_id, version = key.split("@")
            job_assets[key] = {}
            for role, entry in entries.items():
                relative = f"assets/{asset_id}/{version}/{role}{Path(entry['path']).suffix.lower()}"
                (staging / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(entry["source"], staging / relative)  # copie : le catalogue n'est jamais lu par le worker
                job_assets[key][role] = {"path": relative, "sha256": entry["sha256"]}
        job = {"schema": animation_worker.JOB_SCHEMA, "task_id": task_id, "project_id": project_id,
               "mode": self.mode_of(request), "storyboard": storyboard, "assets": job_assets,
               "asset_info": self._asset_info(project_id, files), "created_at": self._clock()}
        animation_worker.write_json(staging / "job.json", job)
        animation_worker.write_json(staging / "state.json", {"state": "queued", "step": "En attente du worker",
                                                             "heartbeat_at": self._clock(), "restarts": 0})
        try:
            staging.rename(directory)  # publication atomique du dossier complet
        except OSError:
            shutil.rmtree(staging, ignore_errors=True)
            if not (directory / "job.json").is_file():
                raise ProviderError(ErrorKind.PROVIDER, "Impossible de préparer le dossier de rendu 2D.") from None
        self._launch(directory)
        return ExternalTask(PROVIDER_ID, task_id)

    def _asset_info(self, project_id: str, files: dict[str, dict]) -> dict[str, dict]:
        """Type, dimensions, ancrage et nom court de chaque version figée : placement exact par le moteur (#150)."""
        info: dict[str, dict] = {}
        for key in files:
            asset_id, version = key.split("@")
            asset = self.assets().get(project_id, asset_id)
            manifest = self.assets().get_version(project_id, asset_id, int(version)).manifest
            base = next((item["path"] for item in manifest["files"] if item["role"] == "base"), "")
            parts = Path(base).parts
            slug = parts[1] if len(parts) > 2 and parts[0] == "animation-assets" else ""
            info[key] = {"kind": asset.kind, "width": manifest.get("width"), "height": manifest.get("height"),
                         "anchor": manifest.get("anchor") or {"x": 0.5, "y": 0.5}, "slug": slug}
        return info

    def _launch(self, directory: Path) -> None:
        try:
            self._launcher(directory)
        except OSError as error:
            logger.warning("worker 2D non lancé (%s)", type(error).__name__)
            raise ProviderError(ErrorKind.UNAVAILABLE, "Le worker de rendu 2D n'a pas pu démarrer.") from None

    def _ensure_worker(self, directory: Path) -> dict[str, Any]:
        """Relance un worker disparu (redémarrage, arrêt) : le rendu local est gratuit et reprend où il en était."""
        state = animation_worker.read_json(directory / "state.json") or {}
        if state.get("state") in animation_worker.TERMINAL_STATES or animation_worker.worker_alive(directory):
            return state
        heartbeat = float(state.get("heartbeat_at") or 0)
        if state.get("state") != "interrupted" and self._clock() - heartbeat < STALE_AFTER:
            return state  # worker juste lancé, pas encore installé
        restarts = int(state.get("restarts") or 0)
        if restarts >= MAX_RESTARTS:
            state.update(state="failed", step="Échec",
                         error=f"Le worker de rendu 2D s'est arrêté {restarts} fois : rendu abandonné.")
            animation_worker.write_json(directory / "state.json", state)
            return state
        state.update(restarts=restarts + 1, heartbeat_at=self._clock(), step="Reprise du rendu")
        animation_worker.write_json(directory / "state.json", state)
        self._launch(directory)
        return state

    def _directory(self, task: ExternalTask) -> Path | None:
        return animation_worker.find_job_dir(self.root, task.task_id)

    def _video_ref(self, directory: Path) -> str:
        return f"animation/{directory.parent.name}/{directory.name}/out.mp4"

    def _result(self, directory: Path) -> GenerationResult | None:
        manifest = animation_worker.read_json(directory / "manifest.json")
        if not manifest or not (directory / "out.mp4").is_file():
            return None
        ref = self._video_ref(directory)
        assets: list[dict[str, str]] = [{"kind": "video", "ref": ref}]
        return GenerationResult(ref, manifest.get("duration_seconds") or manifest.get("expected_seconds"),
                                assets=tuple(assets), warnings=tuple(manifest.get("warnings") or ()))

    def poll(self, task: ExternalTask) -> TaskSnapshot:
        directory = self._directory(task)
        if directory is None:
            return TaskSnapshot(RemoteState.UNKNOWN)
        try:
            state = self._ensure_worker(directory)
        except ProviderError as error:
            return TaskSnapshot(RemoteState.FAILED, error=error)
        status = state.get("state")
        if status == "done":
            result = self._result(directory)
            if result is None:
                return TaskSnapshot(RemoteState.FAILED, error=ProviderError(
                    ErrorKind.VIDEO_MISSING, "Le rendu 2D est annoncé terminé mais la vidéo est introuvable."))
            return TaskSnapshot(RemoteState.DONE, 100, "Terminée", result=result, warnings=result.warnings)
        if status == "failed":
            return TaskSnapshot(RemoteState.FAILED, error=ProviderError(
                ErrorKind.PROVIDER, sanitize(state.get("error") or "Le rendu 2D a échoué.", 300), stage="render"))
        if status == "cancelled":
            return TaskSnapshot(RemoteState.FAILED, error=ProviderError(ErrorKind.INTERRUPTED, "Rendu 2D arrêté à la demande."))
        total = int(state.get("total_frames") or 0)
        done = int(state.get("rendered_frames") or 0)
        step = sanitize(state.get("step") or "En attente du worker", 120)
        if status in ("running",) and total:
            return TaskSnapshot(RemoteState.RUNNING, min(95, 5 + int(90 * done / total)), step)
        if status == "interrupted":
            return TaskSnapshot(RemoteState.RUNNING, None, "Rendu interrompu, reprise automatique")
        return TaskSnapshot(RemoteState.QUEUED, 0, step)

    def cancel(self, task: ExternalTask) -> bool:
        """Demande l'arrêt d'un rendu (drapeau lu entre deux scènes). Non exposé dans l'interface pour l'instant."""
        directory = self._directory(task)
        if directory is None:
            return False
        (directory / "cancel").write_text("1", encoding="utf-8")
        return True

    def recover(self, task: ExternalTask) -> GenerationResult | None:
        directory = self._directory(task)
        return self._result(directory) if directory else None

    def read_subtitles(self, task: ExternalTask) -> str | None:
        directory = self._directory(task)
        if directory is None:
            return None
        try:
            return (directory / "subtitles.srt").read_text(encoding="utf-8")
        except OSError:
            return None

    def render_trace(self, task: ExternalTask) -> dict[str, Any] | None:
        """Traces par scène (statut, durée réelle, empreinte, durée de rendu) du manifeste, ou ``None``."""
        directory = self._directory(task)
        return animation_worker.read_json(directory / "manifest.json") if directory else None

    def resolve_asset(self, task: ExternalTask, ref: str, suffixes: tuple[str, ...] = ALLOWED_VIDEO_SUFFIXES) -> Path:
        directory = self._directory(task)
        if directory is None:
            raise ValueError("tâche inconnue")
        return resolve_within(self.root, ref, prefix=f"animation/{directory.parent.name}/{directory.name}",
                              suffixes=suffixes)

