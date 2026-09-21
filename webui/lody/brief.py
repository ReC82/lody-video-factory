"""Paramètres de production d'un projet et brief final.

Fonctions pures (aucun Streamlit, aucun réseau, aucun fournisseur) : elles valident les
paramètres stockés dans ``project.settings["brief"]`` et assemblent le brief affiché
avant toute génération.
"""

from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Any

from lody import catalog

if TYPE_CHECKING:  # pragma: no cover
    from lody.projects import Project

BRIEF_KEY = "brief"
BRIEF_VERSION = 1

DURATION_LIMITS = (10, 300)
SCENES_LIMITS = (1, 30)
AUDIENCE_MAX = 200
ORIENTATION_MAX = 200
INSTRUCTIONS_MAX = 2000
STRUCTURE_MAX_STEPS = 12
STRUCTURE_STEP_MAX = 120
VISUAL_RULES_MAX = 800
VISUAL_AVOID_MAX_ITEMS = 16
VISUAL_AVOID_ITEM_MAX = 140

NARRATION_PACES = (
    catalog.Option("calme", "Posée"),
    catalog.Option("normal", "Naturelle"),
    catalog.Option("rapide", "Rapide et énergique"),
)
# Repère de débit en mots par minute (estimation indicative, pas une mesure).
WORDS_PER_MINUTE = {"calme": 130, "normal": 155, "rapide": 175}

DEFAULT_BRIEF: dict[str, Any] = {
    "duration_min": 45,
    "duration_max": 60,
    "audience": "",
    "orientation": "",
    "narration_pace": "normal",
    "scenes_per_minute_min": 6,
    "scenes_per_minute_max": 8,
    "voice_id": "",
    "voice_model": "eleven_multilingual_v2",
    "structure": [],
    "standing_instructions": "",
    # Profil visuel PROPRE au projet. Neutre par défaut : aucune règle, aucun vocabulaire métier n'est fourni par
    # la plateforme (un projet n'hérite jamais des règles d'un autre).
    "visual_rules": "",
    "visual_avoid": [],
}

_VOICE_ID = re.compile(r"^[A-Za-z0-9_\-]{6,40}$")
_VOICE_MODEL = re.compile(r"^[A-Za-z0-9_.\-]{3,60}$")
_LEGACY_DURATION = re.compile(r"(\d+)\D+(\d+)")


def brief_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Paramètres de production d'un projet, complétés par des valeurs par défaut.

    Les projets créés avant l'existence de cette page (ex. Audiovisuel) n'ont pas de bloc
    ``brief`` : on retombe sur leurs anciens champs (``target_duration``, ``voice_id``…)
    sans rien réécrire en base.
    """
    merged = copy.deepcopy(DEFAULT_BRIEF)  # copie profonde : jamais d'objet mutable partagé entre projets
    stored = settings.get(BRIEF_KEY)
    if isinstance(stored, dict):
        merged.update({key: copy.deepcopy(stored[key]) for key in DEFAULT_BRIEF if key in stored})
        return merged
    legacy_duration = _LEGACY_DURATION.search(str(settings.get("target_duration", "")))
    if legacy_duration:
        merged["duration_min"], merged["duration_max"] = int(legacy_duration[1]), int(legacy_duration[2])
    for key in ("voice_id", "voice_model"):
        if settings.get(key):
            merged[key] = settings[key]
    return merged


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _range(raw: dict[str, Any], low: str, high: str, limits: tuple[int, int], unit_message: str,
           errors: dict[str, str], clean: dict[str, Any]) -> None:
    first, second = _int(raw.get(low)), _int(raw.get(high))
    if first is None or second is None or not (limits[0] <= first <= limits[1] and limits[0] <= second <= limits[1]):
        errors[f"{BRIEF_KEY}.{low}"] = unit_message
    elif first > second:
        errors[f"{BRIEF_KEY}.{low}"] = "Le minimum doit être inférieur ou égal au maximum."
    else:
        clean[low], clean[high] = first, second


def validate_brief(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Nettoie et valide les paramètres de production. Retourne (valeurs, erreurs)."""
    source = {**DEFAULT_BRIEF, **{key: raw[key] for key in DEFAULT_BRIEF if key in raw}}
    errors: dict[str, str] = {}
    clean: dict[str, Any] = {}

    _range(source, "duration_min", "duration_max", DURATION_LIMITS,
           f"La durée doit être comprise entre {DURATION_LIMITS[0]} et {DURATION_LIMITS[1]} secondes.", errors, clean)
    _range(source, "scenes_per_minute_min", "scenes_per_minute_max", SCENES_LIMITS,
           f"Le rythme visuel doit être compris entre {SCENES_LIMITS[0]} et {SCENES_LIMITS[1]} scènes par minute.",
           errors, clean)

    for key, maximum, label in (("audience", AUDIENCE_MAX, "Le public cible"),
                                ("orientation", ORIENTATION_MAX, "L’orientation")):
        value = " ".join(str(source.get(key) or "").split())
        if len(value) > maximum:
            errors[f"{BRIEF_KEY}.{key}"] = f"{label} est trop long ({maximum} caractères maximum)."
        clean[key] = value

    pace = source.get("narration_pace")
    if pace in catalog.values(NARRATION_PACES):
        clean["narration_pace"] = pace
    else:
        errors[f"{BRIEF_KEY}.narration_pace"] = "Choisis un rythme de narration dans la liste."

    voice_id = str(source.get("voice_id") or "").strip()
    if voice_id and not _VOICE_ID.match(voice_id):
        errors[f"{BRIEF_KEY}.voice_id"] = (
            "L’identifiant de voix ne contient que des lettres, chiffres et tirets (6 à 40 caractères)."
        )
    clean["voice_id"] = voice_id
    voice_model = str(source.get("voice_model") or "").strip()
    if voice_model and not _VOICE_MODEL.match(voice_model):
        errors[f"{BRIEF_KEY}.voice_model"] = "Le modèle de voix n’est pas valide."
    clean["voice_model"] = voice_model

    steps_raw = source.get("structure") or []
    if isinstance(steps_raw, str):
        steps_raw = steps_raw.splitlines()
    if not isinstance(steps_raw, (list, tuple)):
        errors[f"{BRIEF_KEY}.structure"] = "La structure de la vidéo est invalide."
        steps_raw = []
    steps = [" ".join(str(step).split()) for step in steps_raw]
    steps = [step for step in steps if step]
    if len(steps) > STRUCTURE_MAX_STEPS:
        errors[f"{BRIEF_KEY}.structure"] = f"{STRUCTURE_MAX_STEPS} étapes maximum."
    elif any(len(step) > STRUCTURE_STEP_MAX for step in steps):
        errors[f"{BRIEF_KEY}.structure"] = f"Une étape est trop longue ({STRUCTURE_STEP_MAX} caractères maximum)."
    clean["structure"] = steps

    instructions = str(source.get("standing_instructions") or "").strip()
    if len(instructions) > INSTRUCTIONS_MAX:
        errors[f"{BRIEF_KEY}.standing_instructions"] = (
            f"Les consignes sont trop longues ({INSTRUCTIONS_MAX} caractères maximum)."
        )
    clean["standing_instructions"] = instructions

    rules = " ".join(str(source.get("visual_rules") or "").split())
    if len(rules) > VISUAL_RULES_MAX:
        errors[f"{BRIEF_KEY}.visual_rules"] = f"Les consignes visuelles sont trop longues ({VISUAL_RULES_MAX} caractères maximum)."
    clean["visual_rules"] = rules

    avoid_raw = source.get("visual_avoid") or []
    if isinstance(avoid_raw, str):
        avoid_raw = avoid_raw.splitlines()
    if not isinstance(avoid_raw, (list, tuple)):
        errors[f"{BRIEF_KEY}.visual_avoid"] = "La liste « à ne jamais montrer » est invalide."
        avoid_raw = []
    avoid = [" ".join(str(item).split()) for item in avoid_raw]
    avoid = [item for item in avoid if item]
    if len(avoid) > VISUAL_AVOID_MAX_ITEMS:
        errors[f"{BRIEF_KEY}.visual_avoid"] = f"{VISUAL_AVOID_MAX_ITEMS} éléments maximum."
    elif any(len(item) > VISUAL_AVOID_ITEM_MAX for item in avoid):
        errors[f"{BRIEF_KEY}.visual_avoid"] = f"Un élément est trop long ({VISUAL_AVOID_ITEM_MAX} caractères maximum)."
    clean["visual_avoid"] = avoid
    return clean, errors


def estimates(brief: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Repères dérivés : nombre de mots et de scènes attendus (estimations arrondies)."""
    wpm = WORDS_PER_MINUTE.get(brief["narration_pace"], WORDS_PER_MINUTE["normal"])
    words = (round(brief["duration_min"] / 60 * wpm), round(brief["duration_max"] / 60 * wpm))
    scenes = (
        max(1, round(brief["duration_min"] / 60 * brief["scenes_per_minute_min"])),
        max(1, round(brief["duration_max"] / 60 * brief["scenes_per_minute_max"])),
    )
    return {"words": words, "scenes": scenes}


def _span(low: int, high: int, unit: str = "") -> str:
    text = f"{low}" if low == high else f"{low} à {high}"
    return f"{text} {unit}".strip()


def build_brief(project: Project, request: str = "") -> dict[str, Any]:
    """Brief final structuré : demande de l'utilisateur + paramètres permanents du projet."""
    settings = brief_settings(project.settings)
    derived = estimates(settings)
    pace_label = catalog.label(NARRATION_PACES, settings["narration_pace"])
    return {
        "projet": project.name,
        "demande": request.strip(),
        "langue": catalog.label(catalog.LANGUAGES, project.language),
        "format": catalog.label(catalog.FORMATS, project.format),
        "duree_cible": _span(settings["duration_min"], settings["duration_max"], "secondes"),
        "public": settings["audience"],
        "orientation": settings["orientation"],
        "ton": project.tone,
        "narration": pace_label,
        "voix": {
            "fournisseur": catalog.label(catalog.VOICE_PROVIDERS, project.voice_provider),
            "nom": project.voice_name,
            "identifiant": settings["voice_id"],
            "modele": settings["voice_model"],
        },
        "visuels": {
            "style": project.visual_style,
            "rythme": _span(settings["scenes_per_minute_min"], settings["scenes_per_minute_max"], "scènes par minute"),
            "regles": settings["visual_rules"],
            "a_eviter": list(settings["visual_avoid"]),
        },
        "structure": list(settings["structure"]),
        "consignes_permanentes": settings["standing_instructions"],
        "fournisseurs": {
            "script": catalog.label(catalog.TEXT_PROVIDERS, project.text_provider),
            "images": catalog.label(catalog.VISUAL_PROVIDERS, project.visual_provider),
            "voix": catalog.label(catalog.VOICE_PROVIDERS, project.voice_provider),
            "musique": catalog.label(catalog.MUSIC_PROVIDERS, project.music_provider),
        },
        "reperes": {
            "mots": _span(*derived["words"], "mots"),
            "scenes": _span(*derived["scenes"], "scènes"),
        },
    }


def brief_to_text(brief: dict[str, Any]) -> str:
    """Version texte du brief (lisible et copiable)."""
    lines = [f"BRIEF DE PRODUCTION — {brief['projet']}", ""]

    def section(title: str, rows: list[str]) -> None:
        rows = [row for row in rows if row]
        if rows:
            lines.extend([title, *rows, ""])

    section("Demande", [brief["demande"] or "(à saisir)"])
    section("Public et ton", [
        f"- Public : {brief['public']}" if brief["public"] else "",
        f"- Orientation : {brief['orientation']}" if brief["orientation"] else "",
        f"- Ton : {brief['ton']}" if brief["ton"] else "",
    ])
    section("Format", [
        f"- {brief['format']}, {brief['langue']}",
        f"- Durée cible : {brief['duree_cible']}",
        f"- Narration : {brief['narration']} ({brief['reperes']['mots']})",
    ])
    section("Structure de la vidéo", [f"{index}. {step}" for index, step in enumerate(brief["structure"], 1)])
    section("Visuels", [
        f"- Style : {brief['visuels']['style']}" if brief["visuels"]["style"] else "",
        f"- Rythme : {brief['visuels']['rythme']} ({brief['reperes']['scenes']} pour cette vidéo)",
        f"- Consignes : {brief['visuels']['regles']}" if brief["visuels"]["regles"] else "",
        "- À ne jamais montrer : " + " ; ".join(brief["visuels"]["a_eviter"]) if brief["visuels"]["a_eviter"] else "",
    ])
    voice = brief["voix"]
    section("Voix", [f"- {voice['fournisseur']} · {voice['nom']}" if voice["nom"] else f"- {voice['fournisseur']}"])
    if brief["consignes_permanentes"]:
        section("Consignes permanentes", brief["consignes_permanentes"].splitlines())
    return "\n".join(lines).rstrip() + "\n"
