"""Nouvelle version d'une vidéo animée, scène par scène (#159) : services de l'écran « Nouvelle version ».

S'appuie sur la réédition ciblée de #121 (cache de scène, plan, aperçu réduit, contrôle qualité) :

- ``plan_for`` : plan de réédition AVANT toute dépense — la V2 (brouillon) comparée à la version précédente,
  avec les temps de rendu MESURÉS dans le manifeste de celle-ci ;
- ``start_preview`` / ``preview_state`` : **aperçu rapide** à ½ résolution du storyboard du brouillon. Ce n'est
  pas une version : aucune production n'est créée, rien n'est confirmé ; la tâche a un identifiant DÉTERMINISTE
  (brouillon + empreinte du storyboard), donc relancer l'aperçu d'un storyboard inchangé ne refait rien, et deux
  aperçus successifs partagent le cache de scène d'aperçu du projet. Local et gratuit ;
- ``scene_thumbnails`` : une image par scène, extraite des scènes déjà rendues de la version précédente ;
- ``comparison`` : audit (scènes réutilisées, temps évité) et contrôle qualité d'une version rendue.

Aucun appel externe, aucune écriture dans les dossiers de rendu d'une version existante (les miniatures sont
rangées à part, dans ``animation/<projet>/.thumbs``).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lody.animation import audio, reedit
from lody.animation import preflight as animation_preflight
from lody.animation import worker as animation_worker
from lody.generation.animation_connector import AnimationConnector
from lody.generation.models import ExternalTask, ProductionStatus, ProviderError, RemoteState
from lody.generation.service import EDITABLE_STATUSES, LaunchError, ProductionService, request_of
from lody.generation.store import Production

PREVIEW_SCALE = 0.5
# #187 : depuis #169, la V2 re-voise RÉELLEMENT une réplique modifiée — la synthèse ElevenLabs a lieu au lancement
# (``AnimationConnector.submit``), pour les seules scènes dont le texte parlé est absent du cache de voix. Le coût
# affiché et l'accord demandé viennent donc des caractères réellement facturables (``billable_voice_characters``),
# jamais du seul plan ; l'aperçu rapide, lui, n'appelle jamais ElevenLabs.
VOICE_REGENERATION_AVAILABLE = True
THUMBS_DIR = ".thumbs"


@dataclass(frozen=True)
class PreviewState:
    state: str            # none | queued | running | done | failed
    progress: int | None = None
    step: str = ""
    video: Path | None = None
    error: str = ""


def connector_of(service: ProductionService, production: Production) -> AnimationConnector | None:
    provider = service.provider(production.provider)
    return provider if isinstance(provider, AnimationConnector) else None


def parent_of(service: ProductionService, production: Production) -> Production | None:
    if not production.parent_production_id:
        return None
    try:
        return service.repo.get(production.parent_production_id)
    except LookupError:
        return None


def job_directory(service: ProductionService, production: Production) -> Path | None:
    """Dossier de rendu d'une version (lecture seule), ou ``None``."""
    connector = connector_of(service, production)
    if connector is None or not production.external_task_id:
        return None
    return animation_worker.find_job_dir(connector.root, production.external_task_id)


def manifest_of(service: ProductionService, production: Production) -> dict[str, Any] | None:
    directory = job_directory(service, production)
    return animation_worker.read_json(directory / "manifest.json") if directory else None


def has_voice(service: ProductionService, production: Production) -> bool:
    """La version a-t-elle été rendue avec une voix enregistrée (synchro labiale sur ``voice.wav``) ?"""
    directory = job_directory(service, production)
    return bool(directory and (directory / audio.VOICE_FILE).is_file())


def plan_for(service: ProductionService, draft: Production) -> dict[str, Any] | None:
    """Plan de réédition du brouillon contre la version précédente (``reedit.plan``), ou ``None``."""
    parent = parent_of(service, draft)
    if parent is None or not parent.is_animated or not draft.is_animated:
        return None
    return reedit.plan(parent.animation, draft.animation, previous_manifest=manifest_of(service, parent),
                       has_voice=has_voice(service, parent))


def billable_voice_characters(service: ProductionService, draft: Production) -> int:
    """Caractères qu'ElevenLabs facturerait au lancement de ce brouillon (texte parlé absent du cache). Aucun appel."""
    connector = connector_of(service, draft)
    if connector is None or not draft.is_animated:
        return 0
    return int(connector.plan_units(request_of(draft)).characters[0])


def voice_notice(plan: dict[str, Any], has_parent_voice: bool, billable_characters: int = 0) -> tuple[str, bool]:
    """(message de coût/voix du plan, accord de synthèse requis ?).

    L'accord est exigé dès qu'un caractère serait facturé (#187), même si le plan n'annonce aucune réplique
    modifiée (cache de voix vidé, voix changée sur la fiche…) : jamais d'appel payant sans case cochée."""
    if billable_characters > 0:
        return (f"Voix à régénérer : {billable_characters} caractère(s) à synthétiser par ElevenLabs au lancement "
                "(payant, estimation ; seules les répliques absentes du cache) — jamais lancée sans ton accord "
                "ci-dessous."), True
    if not plan["summary"]["voice_to_regenerate"]:
        return "Coût : 0 € — rendu 2D local, aucun appel externe.", False
    if not has_parent_voice or not VOICE_REGENERATION_AVAILABLE:
        return ("Répliques modifiées : aucune voix ElevenLabs n’est reliée à leur locuteur — sous-titres mis à jour, "
                "aucune synthèse, aucun coût."), False
    return ("Répliques modifiées : leur voix est déjà en cache — re-voisées sans appel ElevenLabs, coût 0 €."), False


# -- aperçu rapide --------------------------------------------------------------------------------------------
def preview_key(draft: Production) -> str:
    sha = str((draft.animation.get("frozen") or {}).get("sha256") or "")[:16]
    return f"{draft.id}-preview-{sha}"


def preview_task(service: ProductionService, draft: Production) -> ExternalTask | None:
    connector = connector_of(service, draft)
    if connector is None or not draft.is_animated:
        return None
    return ExternalTask(connector.id, connector.task_id_for(preview_key(draft)))


def start_preview(service: ProductionService, draft: Production) -> ExternalTask:
    """Lance (ou retrouve) l'aperçu ½ du storyboard ACTUEL du brouillon. Local, gratuit, sans confirmation."""
    if draft.status not in EDITABLE_STATUSES or not draft.is_animated:
        raise LaunchError("L'aperçu rapide n'est disponible que sur une nouvelle version en préparation.")
    connector = connector_of(service, draft)
    if connector is None:
        raise LaunchError("L'aperçu rapide n'existe que pour le rendu 2D.")
    # #156 : l'aperçu passe par le même moteur que la génération — même garde de licence Remotion, jamais contournée.
    allowed, message = animation_preflight.license_allows_render()
    if not allowed:
        raise LaunchError(f"Aperçu impossible : {message}")
    # #187 : l'aperçu passe par ``submit``, qui synthétiserait la voix manquante — jamais d'appel payant ici.
    billable = billable_voice_characters(service, draft)
    if billable:
        raise LaunchError(f"Aperçu impossible : {billable} caractère(s) de voix ne sont pas encore synthétisés "
                          "(ElevenLabs, payant). L’aperçu rapide n’appelle jamais un service payant : génère la "
                          "nouvelle version après accord sur le coût.")
    request = request_of(draft)
    request = request.with_updates(animation={**(request.animation or {}), "preview_scale": PREVIEW_SCALE})
    try:
        return connector.submit(request, preview_key(draft))
    except ProviderError as error:
        raise LaunchError(f"Aperçu impossible : {error.message}") from None


def preview_state(service: ProductionService, draft: Production) -> PreviewState:
    task = preview_task(service, draft)
    connector = connector_of(service, draft)
    if task is None or connector is None:
        return PreviewState("none")
    if animation_worker.find_job_dir(connector.root, task.task_id) is None:
        return PreviewState("none")
    snapshot = connector.poll(task)
    if snapshot.state is RemoteState.DONE and snapshot.result:
        try:
            video = connector.resolve_asset(task, snapshot.result.video_ref)
        except ValueError:
            return PreviewState("failed", error="La vidéo d'aperçu est introuvable.")
        return PreviewState("done", 100, "Aperçu prêt", video)
    if snapshot.state is RemoteState.FAILED:
        return PreviewState("failed", error=snapshot.error.message if snapshot.error else "L'aperçu a échoué.")
    if snapshot.state is RemoteState.UNKNOWN:
        return PreviewState("none")
    state = "running" if snapshot.state is RemoteState.RUNNING else "queued"
    return PreviewState(state, snapshot.progress, snapshot.step)


# -- miniatures et comparaison --------------------------------------------------------------------------------
def scene_thumbnails(service: ProductionService, production: Production) -> dict[str, Path]:
    """``{id de scène: image}`` tirées des scènes rendues de ``production`` (milieu de chaque scène).

    Les images sont rangées dans ``animation/<projet>/.thumbs/<empreinte de la scène>.jpg`` : jamais dans le
    dossier de rendu de la version (qui reste intact), recalculées seulement si absentes."""
    directory = job_directory(service, production)
    manifest = animation_worker.read_json(directory / "manifest.json") if directory else None
    ffmpeg = animation_worker.ffmpeg_exe()
    if not directory or not manifest or not ffmpeg:
        return {}
    thumbs = directory.parent / THUMBS_DIR
    result: dict[str, Path] = {}
    for item in manifest.get("scenes") or []:
        sha, index = str(item.get("sha256") or ""), int(item.get("index") or 0)
        source = directory / "scenes" / f"{index:02d}.mp4"
        if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha) or not source.is_file():
            continue
        target = thumbs / f"{sha}.jpg"
        if not target.is_file():
            thumbs.mkdir(parents=True, exist_ok=True)
            middle = max(0.0, float(item.get("duration_seconds") or item.get("expected_seconds") or 0) / 2)
            try:
                subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{middle:.2f}", "-i", str(source),
                                "-frames:v", "1", "-vf", "scale=240:-2", str(target)],
                               check=True, capture_output=True, timeout=30)
            except (subprocess.SubprocessError, OSError):
                continue
        if target.is_file():
            result[str(item.get("id") or "")] = target
    return result


def comparison(service: ProductionService, production: Production) -> dict[str, Any] | None:
    """Audit et contrôle qualité d'une version animée rendue, avec sa version précédente si elle existe."""
    if not production.is_animated or production.status is not ProductionStatus.TERMINEE:
        return None
    directory = job_directory(service, production)
    manifest = animation_worker.read_json(directory / "manifest.json") if directory else None
    if not manifest:
        return None
    report = animation_worker.read_json(directory / "qa.json") or {}
    parent = parent_of(service, production)
    previous_video = None
    if parent is not None and parent.is_animated:
        try:
            previous_video = service.resolve_video(parent)
        except ValueError:
            previous_video = None
    audit = manifest.get("audit") or {}
    return {
        "audit": audit,
        "scenes_reused": int(audit.get("scenes_reused") or 0),
        "scenes_rendered": int(audit.get("scenes_rendered") or 0),
        "render_ms_saved": int(audit.get("render_ms_saved") or 0),
        "render_ms": int(manifest.get("render_ms") or 0),
        "qa": manifest.get("qa") or {},
        "qa_checks": [item for item in report.get("checks") or [] if isinstance(item, dict)],
        "warnings": list(manifest.get("warnings") or []),
        "scenes": manifest.get("scenes") or [],
        "parent": parent,
        "previous_video": previous_video,
    }
