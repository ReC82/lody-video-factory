"""Édition simple d'une scène d'un storyboard animé (#159) : ce que l'écran « Nouvelle version » modifie.

Le propriétaire ne saisit jamais de JSON. Il choisit, pour une scène :

- l'**expression** et le **geste** de chaque personnage, parmi les noms DÉCLARÉS par la version de son asset ;
- le **texte dit** (chaque réplique, qui devient le sous-titre) et le **texte affiché** (titres, étiquettes) ;
- la **version** d'un asset (schéma, décor, personnage) parmi celles du catalogue du projet ;
- le **cadrage** (plan large, moyen, serré) et la **durée** de la scène (étirement de toute son animation).

``summarize`` décrit chaque scène pour l'affichage (temps, dialogue, personnages, schémas) ; ``edit_scene``
applique un lot de changements à UNE scène et renvoie un **nouveau** document (l'original n'est jamais modifié),
validé par le contrat ``lody.animation/1``. Le gel (versions et gestes déclarés) est refait par le service.

Module pur : aucune base, aucun réseau, aucun rendu.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from lody.animation.contract import (
    CUE_TEXT_MAX,
    MAX_SCENE_SECONDS,
    TEXT_CONTENT_MAX,
    AnimationValidationError,
    validate_document,
)

# Cadrages proposés : zoom de la caméra 2D au début de la scène (le mouvement de caméra existant est conservé).
FRAMINGS: dict[str, tuple[str, float]] = {
    "large": ("Plan large — toute la scène", 1.0),
    "moyen": ("Plan moyen — un peu plus près", 1.2),
    "serre": ("Plan serré — gros plan", 1.5),
}
KIND_LABELS = {"character": "personnage", "background": "décor", "prop": "objet / schéma", "overlay": "habillage"}
MIN_SCENE_SECONDS = 1.0


class SceneEditError(ValueError):
    """Modification refusée, avec un message lisible (français) destiné à l'interface."""


@dataclass(frozen=True)
class AssetUse:
    """Un objet « asset » d'une scène, tel que l'écran l'affiche."""

    key: str                 # « calque/objet » : identifie l'objet dans la scène
    object_id: str
    asset_id: str
    version: int
    name: str
    kind: str
    expressions: tuple[tuple[int, str], ...] = ()      # (image, nom)
    gestures: tuple[tuple[int, int, str], ...] = ()    # (début, fin, nom)
    lipsync: bool = False


@dataclass(frozen=True)
class SceneSummary:
    index: int
    id: str
    start_seconds: float
    end_seconds: float
    seconds: float
    purpose: str
    narration: str
    speaker: str
    cues: tuple[tuple[str, float, float], ...]          # (texte, début, fin) en secondes depuis le début de la vidéo
    texts: tuple[tuple[str, str, str], ...]             # (clé, contenu, style)
    assets: tuple[AssetUse, ...] = field(default_factory=tuple)
    zoom: float = 1.0

    @property
    def characters(self) -> tuple[AssetUse, ...]:
        return tuple(item for item in self.assets if item.kind == "character")

    @property
    def schemas(self) -> tuple[AssetUse, ...]:
        return tuple(item for item in self.assets if item.kind != "character")


def _objects(scene: dict[str, Any]):
    for layer in scene["render"]["layers"]:
        for obj in layer["objects"]:
            yield f"{layer['id']}/{obj['id']}", obj


def _frozen_index(document: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(entry["id"], entry["version"]): entry for entry in (document.get("frozen") or {}).get("assets") or []}


def framing_of(zoom: float) -> str:
    """Cadrage proposé le plus proche d'un zoom de caméra."""
    return min(FRAMINGS, key=lambda key: abs(FRAMINGS[key][1] - zoom))


def summarize(document: dict[str, Any]) -> list[SceneSummary]:
    """Résumé de chaque scène d'un storyboard (figé ou non) : ce que la liste de scènes affiche."""
    fps = document["canvas"]["fps"]
    frozen = _frozen_index(document)
    result, offset = [], 0
    for index, scene in enumerate(document["scenes"], 1):
        frames = scene["duration_frames"]
        intent = scene.get("intent") or {}
        render = scene["render"]
        speech = render.get("speech") or {}
        cues = tuple((cue["text"], round((offset + cue["start"]) / fps, 2), round((offset + cue["end"]) / fps, 2))
                     for cue in speech.get("cues") or [])
        texts, assets = [], []
        for key, obj in _objects(scene):
            if obj["type"] == "text":
                texts.append((key, obj["text"]["content"], obj["text"]["style"]))
                continue
            ref = obj["asset"]
            entry = frozen.get((ref["id"], ref["version"])) or {}
            assets.append(AssetUse(
                key=key, object_id=obj["id"], asset_id=ref["id"],
                version=ref["version"] if isinstance(ref["version"], int) else 0,
                name=entry.get("name") or obj["id"], kind=entry.get("kind") or "prop",
                expressions=tuple((event["frame"], event["name"]) for event in obj.get("expressions") or []),
                gestures=tuple((event["start"], event["end"], event["name"]) for event in obj.get("gestures") or []),
                lipsync=bool(obj.get("lipsync"))))
        speaker = ""
        if speech.get("speaker"):
            speaker = next((item.name for item in assets if item.object_id == speech["speaker"]), speech["speaker"])
        keyframes = (render.get("camera") or {}).get("keyframes") or []
        result.append(SceneSummary(
            index=index, id=scene["id"], start_seconds=round(offset / fps, 2), end_seconds=round((offset + frames) / fps, 2),
            seconds=round(frames / fps, 2), purpose=str(intent.get("purpose") or ""),
            narration=str(intent.get("narration") or ""), speaker=speaker, cues=cues, texts=tuple(texts),
            assets=tuple(assets), zoom=float(keyframes[0]["zoom"]) if keyframes else 1.0))
        offset += frames
    return result


# -- modifications --------------------------------------------------------------------------------------------
def _scene(document: dict[str, Any], scene_id: str) -> dict[str, Any]:
    scene = next((item for item in document["scenes"] if item["id"] == scene_id), None)
    if scene is None:
        raise SceneEditError("Cette scène n'existe pas (ou plus) dans le storyboard.")
    return scene


def _object(scene: dict[str, Any], key: str) -> dict[str, Any]:
    obj = dict(_objects(scene)).get(key)
    if obj is None:
        raise SceneEditError("Cet élément n'existe pas (ou plus) dans la scène.")
    return obj


def _clean_text(value: Any, maximum: int, what: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise SceneEditError(f"Le {what} ne peut pas être vide.")
    if len(text) > maximum:
        raise SceneEditError(f"Le {what} est trop long ({len(text)} caractères, {maximum} au maximum).")
    return text


def _set_zoom(document: dict[str, Any], scene: dict[str, Any], zoom: float) -> None:
    canvas = document["canvas"]
    camera = scene["render"].get("camera")
    if not camera or not camera.get("keyframes"):
        if math.isclose(zoom, 1.0):
            return  # plan large sans caméra : rien à ajouter
        scene["render"]["camera"] = {"keyframes": [{"frame": 0, "x": canvas["width"] / 2, "y": canvas["height"] / 2,
                                                    "zoom": zoom, "rotation": 0}]}
        return
    first = float(camera["keyframes"][0]["zoom"]) or 1.0
    if math.isclose(first, zoom):
        return
    for key in camera["keyframes"]:  # le mouvement (travelling, zoom progressif) garde ses proportions
        key["zoom"] = round(min(8.0, max(0.25, float(key["zoom"]) * zoom / first)), 3)


def _stretch(scene: dict[str, Any], frames: int) -> None:
    """Change la durée de la scène en étirant TOUTE son animation (images-clés, gestes, répliques, repli)."""
    old = scene["duration_frames"]
    if frames == old:
        return
    ratio = frames / old

    def at(value: int, *, last: int) -> int:
        return min(last, max(0, round(value * ratio)))

    render = scene["render"]
    for _, obj in _objects(scene):
        for name in ("position", "scale", "rotation", "opacity"):
            for key in obj.get(name) or []:
                key["frame"] = at(key["frame"], last=frames)
        for name in ("start", "end"):
            if name in obj:
                obj[name] = at(obj[name], last=frames)
        for event in obj.get("expressions") or []:
            event["frame"] = at(event["frame"], last=frames)
        for event in obj.get("gestures") or []:
            event["start"], event["end"] = at(event["start"], last=frames), at(event["end"], last=frames)
    for key in (render.get("camera") or {}).get("keyframes") or []:
        key["frame"] = at(key["frame"], last=frames)
    for cue in (render.get("speech") or {}).get("cues") or []:
        cue["start"], cue["end"] = at(cue["start"], last=frames), at(cue["end"], last=frames)
    fallback = render.get("fallback") or {}
    if "frame" in fallback:
        fallback["frame"] = at(fallback["frame"], last=frames - 1)
    scene["duration_frames"] = frames


def edit_scene(document: dict[str, Any], scene_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Applique ``changes`` à la scène ``scene_id`` et renvoie un NOUVEAU document validé (non figé).

    ``changes`` (toutes les clés sont facultatives) :

    - ``expressions`` : ``{(clé d'objet, n° d'événement): nom}`` ;
    - ``gestures`` : ``{(clé d'objet, n° d'événement): nom}`` ;
    - ``cues`` : ``{n° de réplique: texte}`` (le texte dit et sous-titré) ;
    - ``texts`` : ``{clé d'objet texte: contenu}`` (texte affiché) ;
    - ``versions`` : ``{clé d'objet asset: version}`` ;
    - ``framing`` : clé de ``FRAMINGS`` ;
    - ``seconds`` : nouvelle durée de la scène.

    Les noms de gestes et d'expressions sont revérifiés au gel (déclarés par la version de l'asset).
    Lève ``SceneEditError`` (message lisible) si la modification est impossible."""
    result = copy.deepcopy(document)
    result.pop("frozen", None)
    scene = _scene(result, scene_id)
    fps = result["canvas"]["fps"]

    for (key, position), name in (changes.get("expressions") or {}).items():
        events = _object(scene, key).get("expressions") or []
        if not 0 <= position < len(events):
            raise SceneEditError("Ce changement d'expression n'existe pas (ou plus).")
        events[position]["name"] = str(name)
    for (key, position), name in (changes.get("gestures") or {}).items():
        events = _object(scene, key).get("gestures") or []
        if not 0 <= position < len(events):
            raise SceneEditError("Ce geste n'existe pas (ou plus).")
        events[position]["name"] = str(name)
    cues = (scene["render"].get("speech") or {}).get("cues") or []
    narration = (scene.get("intent") or {}).get("narration")
    for position, text in (changes.get("cues") or {}).items():
        if not 0 <= position < len(cues):
            raise SceneEditError("Cette réplique n'existe pas (ou plus).")
        new = _clean_text(text, CUE_TEXT_MAX, "texte de la réplique")
        old = cues[position]["text"]
        cues[position]["text"] = new
        # L'intention narrative (texte de référence de la scène, jamais lu par le moteur) suit la réplique
        # quand elle la contient mot pour mot : le script de la nouvelle version reste cohérent.
        if isinstance(narration, str) and old in narration and old != new:
            narration = narration.replace(old, new, 1)
    if isinstance(narration, str) and narration != (scene.get("intent") or {}).get("narration"):
        scene["intent"]["narration"] = narration[:1200]
    for key, content in (changes.get("texts") or {}).items():
        obj = _object(scene, key)
        if obj["type"] != "text":
            raise SceneEditError("Cet élément n'est pas un texte affiché.")
        obj["text"]["content"] = _clean_text(content, TEXT_CONTENT_MAX, "texte affiché")
    for key, version in (changes.get("versions") or {}).items():
        obj = _object(scene, key)
        if obj["type"] != "asset":
            raise SceneEditError("Cet élément n'utilise pas d'asset du catalogue.")
        obj["asset"]["version"] = int(version)
    if changes.get("framing") is not None:
        if changes["framing"] not in FRAMINGS:
            raise SceneEditError("Cadrage inconnu.")
        _set_zoom(result, scene, FRAMINGS[changes["framing"]][1])
    if changes.get("seconds") is not None:
        seconds = float(changes["seconds"])
        if not MIN_SCENE_SECONDS <= seconds <= MAX_SCENE_SECONDS:
            raise SceneEditError(f"Durée de scène entre {MIN_SCENE_SECONDS:g} et {MAX_SCENE_SECONDS} secondes.")
        _stretch(scene, round(seconds * fps))

    try:
        return validate_document(result)
    except AnimationValidationError as error:
        prefix = f"scenes[{result['scenes'].index(scene)}]"
        details = [message for path, message in error.errors.items() if path.startswith(prefix)] or list(
            error.errors.values())
        hint = " Essaie une durée plus longue." if changes.get("seconds") is not None else ""
        raise SceneEditError("Modification impossible : " + details[0] + hint) from None


def has_changes(changes: dict[str, Any]) -> bool:
    return any(changes.get(key) not in (None, {}, "") for key in
               ("expressions", "gestures", "cues", "texts", "versions", "framing", "seconds"))
