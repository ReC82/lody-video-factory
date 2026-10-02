"""Suivi de génération par étapes réelles (#93) — remplace le pourcentage global trompeur.

Fonctions PURES uniquement (aucun Streamlit, aucun appel réseau) : à partir de ``production`` (déjà
figée en base — ``status``/``current_step``/``progress``/``script_source``/``storyboard``/
``visual_prompts``/``trace``) et de la liste d'images RÉELLEMENT déjà reçues pour cette tâche
(``provider.list_scene_images``, déjà exposé et déjà utilisé par ``kit_service.py`` — jamais un nouvel
appel moteur, jamais un chiffre inventé), calcule une liste ordonnée d'étapes nommées avec un état
honnête (en attente / en cours / terminée / en erreur).

Aucune nouvelle source de vérité : chaque champ lu ici existe déjà (``store.py``) ou est déjà exposé par
le connecteur. ``mpt_connector.list_scene_images`` scanne ``storage/tasks/<task_id>/`` — le moteur y
écrit chaque image (``openai-image-*.png``) dès qu'elle est reçue et enregistrée, jamais avant (voir
``app/services/material.py:_download_videos_openai_image_on_demand``) : une image n'est donc JAMAIS
comptée ici avant d'avoir été réellement reçue et enregistrée. Les tentatives automatiques du moteur
(jusqu'à 3 par image, voir ``app/services/material.py:_request_openai_image``) ne produisent jamais de
fichier intermédiaire : seule une tentative FINALEMENT réussie écrit un fichier — un échec suivi d'un
succès ne peut donc jamais être compté comme deux images, et une tentative qui échoue définitivement
n'est jamais comptée du tout. C'est pourquoi aucun compteur de tentatives séparé n'est nécessaire ou
inventé ici : le seul fait observable et honnête est le nombre de fichiers réellement présents.

Le seul champ nouveau est ``production.trace["current_step_started_at"]``, écrit par ``service.py`` à
chaque changement RÉEL de ``current_step`` (jamais à chaque sondage, voir ``_apply``/``_run``) — un champ
JSON additif dans une colonne déjà existante (``trace``), aucune migration de schéma. Et
``trace["failed_at_step"]``, écrit par ``_fail`` juste avant que ``current_step`` ne devienne
« Échec » : sans lui, on perdrait l'information de QUELLE étape était active au moment de l'échec.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from lody.generation.models import ProductionStatus as S
from lody.generation.store import Production

PREPARATION = "Préparation"
SCRIPT = "Écriture et validation du script"
SCENES = "Préparation des scènes"
VOICE = "Génération de la voix"
SUBTITLES = "Sous-titres"
IMAGES = "Génération des images"
ASSEMBLY = "Montage et export"
READY = "Vidéo prête"

# Ordre RÉEL du pipeline moteur (vérifié ligne à ligne dans app/services/task.py:_run_pipeline, jamais
# supposé) : voix -> sous-titres -> images -> montage. Les sous-titres sont bien écrits AVANT les images.
STEP_ORDER = (PREPARATION, SCRIPT, SCENES, VOICE, SUBTITLES, IMAGES, ASSEMBLY, READY)

PENDING, IN_PROGRESS, DONE, ERROR = "en_attente", "en_cours", "terminee", "erreur"

# current_step (littéral, posé par service.py ou renvoyé par le connecteur via step_for_progress/
# _TIMELINE) -> étape canonique affichée. Toute valeur non reconnue (production ancienne, connecteur
# futur) retombe sur SCENES : jamais une étape déjà dépassée, jamais une inférence au-delà de ce qui est
# honnêtement su.
_RAW_STEP_TO_CANONICAL = {
    "Écriture du script": SCRIPT,
    "Préparation des scènes": SCENES,
    "Envoi au moteur": SCENES,
    "Dans la file du moteur": SCENES,
    "En attente dans la file du moteur": SCENES,
    "En attente dans la file": SCENES,  # connecteur de démonstration
    "Préparation": SCENES,  # jalon moteur interne (file + préflight), avant la voix
    "Génération de la voix": VOICE,
    "Création des sous-titres": SUBTITLES,
    "Génération des images": IMAGES,
    "Montage de la vidéo": ASSEMBLY,
    "Terminée": READY,
}

# Message contextualisé pour les étapes réputées longues — jamais un temps restant, seulement un repère.
_LONG_STEP_HINTS = {
    VOICE: "La synthèse vocale dépend de la longueur du script.",
    IMAGES: "La création des images peut être la partie la plus longue.",
    ASSEMBLY: "Le montage final peut prendre plusieurs minutes selon la durée de la vidéo.",
}


@dataclass(frozen=True)
class StepInfo:
    label: str
    state: str  # en_attente / en_cours / terminee / erreur
    detail: str = ""
    hint: str = ""


def _canonical_for_raw_step(raw: str) -> str:
    return _RAW_STEP_TO_CANONICAL.get(raw) or SCENES


def _active_label(production: Production) -> str:
    """Étape canonique correspondant à la position ACTUELLE de la production — jamais calculée en
    avance sur ce qui est honnêtement su (voir le docstring du module)."""
    status = production.status
    if status in (S.BROUILLON, S.EN_ATTENTE_CONFIRMATION, S.CONFIRMEE):
        return PREPARATION
    if status is S.TERMINEE:
        return READY
    if status is S.ECHEC:
        failed_at = (production.trace or {}).get("failed_at_step") or ""
        return _canonical_for_raw_step(failed_at)
    return _canonical_for_raw_step(production.current_step or "")


def compute_steps(production: Production, images_done: int = 0) -> list[StepInfo]:
    """Liste ordonnée des étapes avec leur état honnête — la fonction centrale de ce module.

    ``images_done`` : nombre d'images RÉELLEMENT reçues pour cette production à l'instant de l'appel
    (voir ``provider.list_scene_images`` — jamais calculé ici, toujours fourni par l'appelant, qui l'a
    lui-même lu depuis le disque). Un échec marque l'étape active comme ``erreur`` et laisse toutes les
    étapes suivantes ``en_attente`` (jamais atteintes, jamais présentées comme terminées) ; les étapes
    précédentes restent ``terminee`` (elles ont réellement eu lieu avant l'échec).
    """
    active = _active_label(production)
    active_index = STEP_ORDER.index(active)
    failed = production.status is S.ECHEC

    steps: list[StepInfo] = []
    for index, label in enumerate(STEP_ORDER):
        if failed and index == active_index:
            state = ERROR
        elif failed and index > active_index:
            state = PENDING
        elif index < active_index:
            state = DONE
        elif index == active_index:
            state = DONE if production.status is S.TERMINEE else IN_PROGRESS
        else:
            state = PENDING

        detail = ""
        if label == SCRIPT and production.script_source == "manual":
            detail = "Script fourni manuellement : aucun appel au générateur."
            if state == IN_PROGRESS:  # fourni = instantané, jamais une attente réseau
                state = DONE
        if label == IMAGES and state != PENDING:
            total = len(production.visual_prompts) or len(production.storyboard)
            detail = (f"{images_done} image(s) reçue(s) sur {total} prévue(s) au maximum."
                     if total else f"{images_done} image(s) reçue(s).")

        hint = _LONG_STEP_HINTS.get(label, "") if state == IN_PROGRESS else ""
        steps.append(StepInfo(label=label, state=state, detail=detail, hint=hint))
    return steps


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def step_elapsed_seconds(production: Production, now: datetime | None = None) -> int | None:
    """Secondes écoulées dans l'étape COURANTE depuis son DERNIER changement réel — jamais depuis le
    début de toute la production. ``None`` si inconnu (production antérieure à #93, champ absent) :
    jamais une valeur inventée."""
    started = _parse_iso((production.trace or {}).get("current_step_started_at"))
    if started is None:
        return None
    end = now or datetime.now(timezone.utc)
    return max(int((end - started).total_seconds()), 0)


def last_image_activity_seconds(images: list[dict], now: datetime | None = None) -> int | None:
    """Secondes depuis la dernière image RÉELLEMENT reçue — ``None`` si aucune image encore reçue."""
    timestamps = [moment for moment in (_parse_iso(image.get("captured_at")) for image in images) if moment is not None]
    if not timestamps:
        return None
    end = now or datetime.now(timezone.utc)
    return max(int((end - max(timestamps)).total_seconds()), 0)


def elapsed_label(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    minutes, secs = divmod(seconds, 60)
    return f"{minutes} min {secs:02d} s" if minutes else f"{secs} s"


def waiting_text(seconds: int | None) -> str:
    """Message honnête sur la dernière activité connue — jamais un temps restant estimé (voir le
    docstring du module : aucune base fiable n'existe pour ça)."""
    if seconds is None:
        return "En attente de la réponse du fournisseur."
    return f"Dernière activité connue il y a {elapsed_label(seconds)}."
