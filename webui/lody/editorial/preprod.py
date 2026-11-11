"""Format de préproduction ``lody.preprod/1`` (#167) et adaptateur depuis un storyboard ``lody.animation/1``.

Un document de préproduction décrit un épisode AVANT toute génération :

- ``brief`` : l'intention éditoriale — objectif central, niveau du public, ce que le spectateur sait déjà,
  la ou les notions nouvelles (avec leur explication), les exemples concrets, l'appel à l'action final,
  les prononciations déclarées pour la synthèse vocale, les cibles de durée ;
- ``scenes`` : pour chaque scène, quatre textes qui ne doivent jamais se confondre —
  ``spoken`` (ce qui part à la synthèse vocale), ``captions`` (sous-titres), ``displayed`` (textes posés
  à l'écran : titres, étiquettes) et ``visual`` (ce que l'on VOIT, décrit concrètement).

La validation est stricte sur la forme (clé inconnue = refus, types, longueurs) et collecte toutes les
erreurs à la fois, avec leur chemin (``scenes[2].visual``) ; le fond est jugé par ``checks``.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

SCHEMA = "lody.preprod/1"
ANIMATION_SCHEMA = "lody.animation/1"

AUDIENCES = ("novice", "initie")
DEFAULT_WORDS_PER_SECOND = 2.6  # débit d'un narrateur français clair (~155 mots/min) ; à remplacer par la mesure
DEFAULT_SCENE_SECONDS = (5.0, 7.0)
MAX_SCENES = 40
MAX_TEXT = 1200
MAX_LIST = 40

BRIEF_TEXT = ("title", "objective", "audience", "cta", "next_episode")
BRIEF_LISTS = ("assumed_knowledge", "examples", "extra_jargon")
BRIEF_KEYS = (*BRIEF_TEXT, *BRIEF_LISTS, "new_terms", "pronunciations", "words_per_second", "scene_seconds",
              "target_seconds")
SCENE_KEYS = ("id", "spoken", "captions", "displayed", "visual", "duration_s", "audio_s")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PreprodError(ValueError):
    """Document de préproduction invalide : ``errors`` associe un chemin précis à un message lisible."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{path} : {message}" for path, message in errors.items()))
        self.errors = errors


class _Checker:
    def __init__(self) -> None:
        self.errors: dict[str, str] = {}

    def fail(self, path: str, message: str) -> None:
        self.errors.setdefault(path, message)

    def keys(self, value: Any, path: str, allowed: tuple[str, ...], required: tuple[str, ...] = ()) -> bool:
        if not isinstance(value, dict):
            self.fail(path, "objet JSON attendu.")
            return False
        unknown = sorted(set(value) - set(allowed))
        if unknown:
            self.fail(path, "clé(s) inconnue(s) : " + ", ".join(map(str, unknown)) + ".")
        missing = [key for key in required if key not in value]
        if missing:
            self.fail(path, "clé(s) obligatoire(s) absente(s) : " + ", ".join(missing) + ".")
        return not unknown and not missing

    def text(self, value: Any, path: str, maximum: int = MAX_TEXT) -> None:
        if not isinstance(value, str):
            self.fail(path, "texte attendu.")
        elif _CONTROL.search(value):
            self.fail(path, "caractère de contrôle interdit.")
        elif len(value) > maximum:
            self.fail(path, f"texte trop long ({maximum} caractères maximum).")

    def texts(self, value: Any, path: str) -> None:
        if not isinstance(value, list) or len(value) > MAX_LIST:
            self.fail(path, f"liste de textes attendue ({MAX_LIST} au maximum).")
            return
        for index, item in enumerate(value):
            self.text(item, f"{path}[{index}]")

    def number(self, value: Any, path: str, low: float, high: float) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            self.fail(path, "nombre attendu.")
            return False
        if not low <= value <= high:
            self.fail(path, f"valeur hors limites ({low:g} à {high:g}).")
            return False
        return True

    def pair(self, value: Any, path: str, low: float, high: float) -> None:
        if not isinstance(value, list) or len(value) != 2:
            self.fail(path, "paire [minimum, maximum] attendue.")
            return
        if self.number(value[0], f"{path}[0]", low, high) & self.number(value[1], f"{path}[1]", low, high):
            if value[0] > value[1]:
                self.fail(path, "le minimum dépasse le maximum.")


def _check_brief(c: _Checker, brief: Any) -> None:
    if not c.keys(brief, "brief", BRIEF_KEYS):
        return
    for key in BRIEF_TEXT:
        if key in brief:
            c.text(brief[key], f"brief.{key}")
    for key in BRIEF_LISTS:
        if key in brief:
            c.texts(brief[key], f"brief.{key}")
    if "audience" in brief and brief["audience"] not in AUDIENCES:
        c.fail("brief.audience", "valeur inconnue ; choix possibles : " + ", ".join(AUDIENCES) + ".")
    terms = brief.get("new_terms", [])
    if not isinstance(terms, list) or len(terms) > MAX_LIST:
        c.fail("brief.new_terms", f"liste attendue ({MAX_LIST} au maximum).")
    else:
        for index, term in enumerate(terms):
            path = f"brief.new_terms[{index}]"
            if c.keys(term, path, ("term", "explanation", "aliases"), ("term",)):
                c.text(term["term"], f"{path}.term", 80)
                if "explanation" in term:
                    c.text(term["explanation"], f"{path}.explanation")
                if "aliases" in term:
                    c.texts(term["aliases"], f"{path}.aliases")
    pronunciations = brief.get("pronunciations", {})
    if not isinstance(pronunciations, dict) or len(pronunciations) > 200:
        c.fail("brief.pronunciations", "objet {terme écrit : forme parlée} attendu (200 au maximum).")
    else:
        for written, spoken in pronunciations.items():
            c.text(written, f"brief.pronunciations.{written}", 80)
            c.text(spoken, f"brief.pronunciations.{written}", 200)
    if "words_per_second" in brief:
        c.number(brief["words_per_second"], "brief.words_per_second", 1.0, 6.0)
    if "scene_seconds" in brief:
        c.pair(brief["scene_seconds"], "brief.scene_seconds", 1.0, 60.0)
    if "target_seconds" in brief:
        c.pair(brief["target_seconds"], "brief.target_seconds", 5.0, 900.0)


def _check_scene(c: _Checker, scene: Any, path: str) -> None:
    if not c.keys(scene, path, SCENE_KEYS, ("id",)):
        return
    if not isinstance(scene["id"], str) or not SLUG.match(scene["id"]):
        c.fail(f"{path}.id", "identifiant attendu : minuscules, chiffres, « - » ou « _ » (48 caractères maximum).")
    for key in ("spoken", "visual"):
        if key in scene:
            c.text(scene[key], f"{path}.{key}")
    for key in ("captions", "displayed"):
        if key in scene:
            c.texts(scene[key], f"{path}.{key}")
    for key in ("duration_s", "audio_s"):
        if key in scene:
            c.number(scene[key], f"{path}.{key}", 0.1, 600.0)


def validate_preprod(raw: Any) -> dict[str, Any]:
    """Valide la FORME d'un document ``lody.preprod/1`` et renvoie une copie indépendante normalisée
    (listes et brief toujours présents). Lève ``PreprodError`` avec toutes les erreurs."""
    c = _Checker()
    if not c.keys(raw, "$", ("schema", "brief", "scenes"), ("schema", "brief", "scenes")):
        raise PreprodError(c.errors)
    if raw["schema"] != SCHEMA:
        c.fail("schema", f"format non pris en charge (seul « {SCHEMA} » l'est).")
    _check_brief(c, raw["brief"])
    scenes = raw["scenes"]
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= MAX_SCENES:
        c.fail("scenes", f"entre 1 et {MAX_SCENES} scène(s) attendue(s).")
    else:
        seen: set[str] = set()
        for index, scene in enumerate(scenes):
            _check_scene(c, scene, f"scenes[{index}]")
            scene_id = scene.get("id") if isinstance(scene, dict) else None
            if isinstance(scene_id, str):
                if scene_id in seen:
                    c.fail(f"scenes[{index}].id", "identifiant de scène en double.")
                seen.add(scene_id)
    if c.errors:
        raise PreprodError(c.errors)
    document = copy.deepcopy(raw)
    for scene in document["scenes"]:
        scene.setdefault("spoken", "")
        scene.setdefault("visual", "")
        scene.setdefault("captions", [])
        scene.setdefault("displayed", [])
    return document


def brief_settings(brief: dict[str, Any]) -> dict[str, Any]:
    """Réglages effectifs du brief (valeurs par défaut documentées appliquées)."""
    low, high = brief.get("scene_seconds") or DEFAULT_SCENE_SECONDS
    target = brief.get("target_seconds")
    return {"audience": brief.get("audience") or "novice",
            "words_per_second": float(brief.get("words_per_second") or DEFAULT_WORDS_PER_SECOND),
            "scene_seconds": (float(low), float(high)),
            "target_seconds": (float(target[0]), float(target[1])) if target else None}


def _slug_words(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ")


def from_storyboard(storyboard: dict[str, Any], brief: dict[str, Any]) -> dict[str, Any]:
    """Construit un document ``lody.preprod/1`` à partir d'un storyboard animé ``lody.animation/1`` VALIDE
    (#119) et d'un brief éditorial. Correspondance (les chemins des alertes renvoient au storyboard d'origine
    grâce à ``source_paths``) :

    - ``spoken`` ← ``intent.narration`` (texte de la scène ; les formes phonétiques éventuelles y sont lues) ;
    - ``captions`` ← ``render.speech.cues[].text`` ; ``displayed`` ← objets ``text`` des calques ;
    - ``visual`` ← ``intent.purpose`` + ``intent.notes`` + identifiants des objets ``asset`` visibles
      (« cable-sdi » → « cable sdi ») : c'est tout ce que le storyboard dit de l'image ;
    - ``duration_s`` ← ``duration_frames / fps``.
    """
    if not isinstance(storyboard, dict) or storyboard.get("schema") != ANIMATION_SCHEMA:
        raise PreprodError({"schema": f"storyboard « {ANIMATION_SCHEMA} » attendu."})
    fps = storyboard["canvas"]["fps"]
    scenes = []
    for scene in storyboard["scenes"]:
        intent, render = scene.get("intent") or {}, scene.get("render") or {}
        displayed, assets = [], []
        for layer in render.get("layers") or []:
            for obj in layer.get("objects") or []:
                if obj.get("type") == "text":
                    displayed.append(obj["text"]["content"])
                elif obj.get("type") == "asset":
                    assets.append(_slug_words(obj["id"]))
        visual = " ; ".join(part for part in (intent.get("purpose", ""), intent.get("notes", ""),
                                              "objets visibles : " + ", ".join(assets) if assets else "") if part)
        scenes.append({"id": scene["id"], "spoken": intent.get("narration", ""),
                       "captions": [cue["text"] for cue in (render.get("speech") or {}).get("cues", [])],
                       "displayed": displayed, "visual": visual,
                       "duration_s": round(scene["duration_frames"] / fps, 3)})
    return validate_preprod({"schema": SCHEMA, "brief": brief, "scenes": scenes})


# Champ du document de préproduction → champ du storyboard animé d'où il vient (renvoi « à la scène exacte »).
STORYBOARD_PATHS = {"spoken": "intent.narration", "captions": "render.speech.cues", "displayed": "render.layers",
                    "visual": "intent.purpose", "duration_s": "duration_frames", "audio_s": "duration_frames"}
_SCENE_PATH = re.compile(r"^scenes\[(\d+)\]\.(\w+)(?:\[(\d+)\])?$")


def storyboard_path(path: str) -> str:
    """Traduit le chemin d'une alerte vers le storyboard ``lody.animation/1`` d'origine :
    ``scenes[2].captions[1]`` → ``scenes[2].render.speech.cues[1]`` ; les chemins du brief sont inchangés."""
    match = _SCENE_PATH.match(path)
    if not match or match.group(2) not in STORYBOARD_PATHS:
        return path
    index, fieldname, item = match.groups()
    target = f"scenes[{index}].{STORYBOARD_PATHS[fieldname]}"
    return f"{target}[{item}]" if item is not None and fieldname == "captions" else target
