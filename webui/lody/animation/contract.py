"""Contrat de storyboard animé ``lody.animation/1`` (#119) : format JSON, validation stricte, empreinte.

Une scène sépare deux choses qui ne doivent jamais se mélanger :

- ``intent`` — l'**intention narrative**, lisible par un humain ou un LLM : ce qui est dit (``narration``),
  pourquoi la scène existe (``purpose``), l'ambiance (``mood``), des notes libres. Aucun moteur ne
  l'interprète ; elle sert à écrire, relire et rééditer la scène.
- ``render`` — les **instructions techniques** déclaratives du moteur : calques, objets, références
  d'assets versionnés, images-clés de position (trajectoire), d'échelle, de rotation et d'opacité,
  expressions, gestes, texte affiché, timecodes de parole, caméra 2D, transitions et image fixe de repli.

Sécurité : **aucun champ n'est du code**. Les courbes sont des énumérations (``easing``), les gestes et
expressions des NOMS déclarés par la version de l'asset (vérifiés par ``freeze``), le texte est affiché
comme du texte. Toute clé inconnue est refusée, à tous les niveaux : on ne peut pas glisser un champ
« script »/« code » qu'un adaptateur exécuterait. Aucune URL ni chemin de fichier dans le storyboard :
les fichiers sont portés par le catalogue d'assets (``lody.animation.assets``), jamais par la scène.

Unités : temps en **images** (entiers, relatifs au début de la scène, comme ``useCurrentFrame()`` dans le
prototype Remotion de #118), positions en **pixels du canevas**, angles en degrés. Les valeurs entre deux
images-clés sont interpolées par l'adaptateur selon ``easing`` (équivalent de ``keyframes()`` du POC).

Le module est pur (aucune base, aucun réseau) : ``validate_document`` renvoie une copie propre ou lève
``AnimationValidationError`` avec des chemins précis (``scenes[1].render.layers[0].objects[2].position``).
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Iterator
from typing import Any

from lody.secrets_guard import SECRET_MESSAGE, find_secret_path

SCHEMA = "lody.animation/1"

FPS_CHOICES = (24, 25, 30, 50, 60)
CANVAS_MIN, CANVAS_MAX = 320, 3840
MAX_SCENES = 30
MAX_SCENE_SECONDS = 120
MAX_LAYERS = 12
MAX_OBJECTS_PER_LAYER = 24
MAX_KEYFRAMES = 120
MAX_EVENTS = 60
MAX_SPEECH_CUES = 400
MAX_TRANSITION_SECONDS = 2

TEXT_LIMITS = {"narration": 1200, "purpose": 300, "mood": 80, "notes": 1000}
TEXT_CONTENT_MAX = 200
CUE_TEXT_MAX = 60
NAME_MAX = 48

EASINGS = ("linear", "ease_in", "ease_out", "ease_in_out", "spring")
DEFAULT_EASING = "ease_in_out"
TEXT_STYLES = ("title", "subtitle", "caption", "label")
TRANSITIONS = ("cut", "fade", "slide_left", "slide_right", "slide_up", "zoom")
ASSET_KINDS = ("character", "background", "prop", "overlay")

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
ASSET_ID = re.compile(r"^ast_[0-9a-f]{12}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

LATEST = "latest"


class AnimationValidationError(ValueError):
    """Document invalide : ``errors`` associe un chemin JSON précis à un message lisible."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{path} : {message}" for path, message in errors.items()))
        self.errors = errors


class _Checker:
    """Collecte toutes les erreurs (pas seulement la première) : l'auteur corrige tout en une fois."""

    def __init__(self) -> None:
        self.errors: dict[str, str] = {}

    def fail(self, path: str, message: str) -> None:
        self.errors.setdefault(path, message)

    def obj(self, value: Any, path: str, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> bool:
        if not isinstance(value, dict):
            self.fail(path, "objet JSON attendu.")
            return False
        unknown = sorted(set(value) - set(required) - set(optional))
        if unknown:
            self.fail(path, "clé(s) inconnue(s) : " + ", ".join(map(str, unknown)) + ".")
        missing = [key for key in required if key not in value]
        if missing:
            self.fail(path, "clé(s) obligatoire(s) absente(s) : " + ", ".join(missing) + ".")
        return not unknown and not missing

    def array(self, value: Any, path: str, minimum: int, maximum: int) -> bool:
        if not isinstance(value, list):
            self.fail(path, "liste JSON attendue.")
            return False
        if not minimum <= len(value) <= maximum:
            self.fail(path, f"entre {minimum} et {maximum} élément(s) attendu(s), {len(value)} reçu(s).")
            return False
        return True

    def integer(self, value: Any, path: str, low: int, high: int) -> bool:
        if isinstance(value, bool) or not isinstance(value, int):
            self.fail(path, "entier attendu.")
            return False
        if not low <= value <= high:
            self.fail(path, f"valeur hors limites ({low} à {high}).")
            return False
        return True

    def number(self, value: Any, path: str, low: float, high: float) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            self.fail(path, "nombre attendu.")
            return False
        if not low <= value <= high:
            self.fail(path, f"valeur hors limites ({low:g} à {high:g}).")
            return False
        return True

    def text(self, value: Any, path: str, maximum: int, *, allow_empty: bool = True) -> bool:
        if not isinstance(value, str):
            self.fail(path, "texte attendu.")
            return False
        if _CONTROL.search(value):
            self.fail(path, "caractère de contrôle interdit.")
            return False
        if len(value) > maximum:
            self.fail(path, f"texte trop long ({maximum} caractères maximum).")
            return False
        if not allow_empty and not value.strip():
            self.fail(path, "texte vide.")
            return False
        return True

    def slug(self, value: Any, path: str) -> bool:
        if not isinstance(value, str) or not SLUG.match(value):
            self.fail(path, "identifiant attendu : minuscules, chiffres, « - » ou « _ » (48 caractères maximum).")
            return False
        return True

    def choice(self, value: Any, path: str, choices: tuple[str, ...]) -> bool:
        if value not in choices:
            self.fail(path, "valeur inconnue ; choix possibles : " + ", ".join(choices) + ".")
            return False
        return True


# -- références d'assets ---------------------------------------------------------------------------------
def _check_ref(c: _Checker, ref: Any, path: str, require_pinned: bool) -> None:
    if not c.obj(ref, path, ("id", "version")):
        return
    if not isinstance(ref["id"], str) or not ASSET_ID.match(ref["id"]):
        c.fail(f"{path}.id", "identifiant d'asset du catalogue attendu (ast_ suivi de 12 caractères hexadécimaux).")
    version = ref["version"]
    if version == LATEST:
        if require_pinned:
            c.fail(f"{path}.version", "version explicite obligatoire dans un storyboard figé (pas « latest »).")
    else:
        c.integer(version, f"{path}.version", 1, 1_000_000)


# -- images-clés -----------------------------------------------------------------------------------------
def _check_keyframes(c: _Checker, value: Any, path: str, duration: int, fields: dict[str, tuple[float, float]]) -> None:
    if not c.array(value, path, 1, MAX_KEYFRAMES):
        return
    previous = -1
    for index, key in enumerate(value):
        item = f"{path}[{index}]"
        if not c.obj(key, item, ("frame", *fields)):
            continue
        if c.integer(key["frame"], f"{item}.frame", 0, duration):
            if key["frame"] <= previous:
                c.fail(f"{item}.frame", "les images-clés doivent être strictement croissantes.")
            previous = key["frame"]
        for name, (low, high) in fields.items():
            c.number(key[name], f"{item}.{name}", low, high)


def _check_window(c: _Checker, item: dict[str, Any], path: str, duration: int) -> None:
    start, end = item.get("start", 0), item.get("end", duration)
    ok = c.integer(start, f"{path}.start", 0, duration) & c.integer(end, f"{path}.end", 0, duration)
    if ok and start >= end:
        c.fail(f"{path}.end", "doit être strictement après « start ».")


# -- objets ----------------------------------------------------------------------------------------------
def _check_object(c: _Checker, obj: Any, path: str, duration: int, canvas: dict[str, int], require_pinned: bool) -> None:
    optional = ("start", "end", "scale", "rotation", "opacity", "easing", "asset", "text", "expressions",
                "gestures", "lipsync")
    if not c.obj(obj, path, ("id", "type", "position"), optional):
        return
    c.slug(obj["id"], f"{path}.id")
    if not c.choice(obj["type"], f"{path}.type", ("asset", "text")):
        return
    _check_window(c, obj, path, duration)
    width, height = canvas["width"], canvas["height"]
    # Une position hors champ (jusqu'à un canevas de marge) permet les entrées et sorties d'écran.
    _check_keyframes(c, obj["position"], f"{path}.position", duration,
                     {"x": (-width, 2 * width), "y": (-height, 2 * height)})
    if "scale" in obj:
        _check_keyframes(c, obj["scale"], f"{path}.scale", duration, {"value": (0.05, 10)})
    if "rotation" in obj:
        _check_keyframes(c, obj["rotation"], f"{path}.rotation", duration, {"value": (-720, 720)})
    if "opacity" in obj:
        _check_keyframes(c, obj["opacity"], f"{path}.opacity", duration, {"value": (0, 1)})
    if "easing" in obj:
        c.choice(obj["easing"], f"{path}.easing", EASINGS)

    if obj["type"] == "text":
        for forbidden in ("asset", "expressions", "gestures", "lipsync"):
            if forbidden in obj:
                c.fail(f"{path}.{forbidden}", "réservé aux objets de type « asset ».")
        if "text" not in obj:
            c.fail(f"{path}.text", "obligatoire pour un objet de type « text ».")
        elif c.obj(obj["text"], f"{path}.text", ("content", "style")):
            c.text(obj["text"]["content"], f"{path}.text.content", TEXT_CONTENT_MAX, allow_empty=False)
            c.choice(obj["text"]["style"], f"{path}.text.style", TEXT_STYLES)
        return

    if "text" in obj:
        c.fail(f"{path}.text", "réservé aux objets de type « text ».")
    if "asset" not in obj:
        c.fail(f"{path}.asset", "obligatoire pour un objet de type « asset ».")
    else:
        _check_ref(c, obj["asset"], f"{path}.asset", require_pinned)
    if "expressions" in obj and c.array(obj["expressions"], f"{path}.expressions", 1, MAX_EVENTS):
        previous = -1
        for index, event in enumerate(obj["expressions"]):
            item = f"{path}.expressions[{index}]"
            if c.obj(event, item, ("frame", "name")):
                if c.integer(event["frame"], f"{item}.frame", 0, duration):
                    if event["frame"] <= previous:
                        c.fail(f"{item}.frame", "les changements d'expression doivent être strictement croissants.")
                    previous = event["frame"]
                c.slug(event["name"], f"{item}.name")
    if "gestures" in obj and c.array(obj["gestures"], f"{path}.gestures", 1, MAX_EVENTS):
        previous_end = 0
        for index, event in enumerate(obj["gestures"]):
            item = f"{path}.gestures[{index}]"
            if c.obj(event, item, ("start", "end", "name")):
                _check_window(c, event, item, duration)
                c.slug(event["name"], f"{item}.name")
                if isinstance(event["start"], int) and event["start"] < previous_end:
                    c.fail(f"{item}.start", "deux gestes d'un même objet ne peuvent pas se chevaucher.")
                if isinstance(event["end"], int):
                    previous_end = event["end"]
    if "lipsync" in obj and not isinstance(obj["lipsync"], bool):
        c.fail(f"{path}.lipsync", "booléen attendu.")


# -- scène -----------------------------------------------------------------------------------------------
def _check_scene(c: _Checker, scene: Any, path: str, canvas: dict[str, int], require_pinned: bool) -> None:
    if not c.obj(scene, path, ("id", "duration_frames", "intent", "render")):
        return
    c.slug(scene["id"], f"{path}.id")
    fps = canvas["fps"]
    if not c.integer(scene["duration_frames"], f"{path}.duration_frames", 1, fps * MAX_SCENE_SECONDS):
        return
    duration = scene["duration_frames"]

    intent = scene["intent"]
    if c.obj(intent, f"{path}.intent", (), tuple(TEXT_LIMITS)):
        for key, maximum in TEXT_LIMITS.items():
            if key in intent:
                c.text(intent[key], f"{path}.intent.{key}", maximum)

    render, rpath = scene["render"], f"{path}.render"
    if not c.obj(render, rpath, ("layers", "fallback"), ("camera", "speech", "transition_in", "transition_out")):
        return

    object_ids: dict[str, str] = {}
    if c.array(render["layers"], f"{rpath}.layers", 1, MAX_LAYERS):
        layer_ids: set[str] = set()
        for li, layer in enumerate(render["layers"]):
            lpath = f"{rpath}.layers[{li}]"
            if not c.obj(layer, lpath, ("id", "objects")):
                continue
            if c.slug(layer["id"], f"{lpath}.id"):
                if layer["id"] in layer_ids:
                    c.fail(f"{lpath}.id", "identifiant de calque en double dans la scène.")
                layer_ids.add(layer["id"])
            if not c.array(layer["objects"], f"{lpath}.objects", 1, MAX_OBJECTS_PER_LAYER):
                continue
            for oi, obj in enumerate(layer["objects"]):
                opath = f"{lpath}.objects[{oi}]"
                _check_object(c, obj, opath, duration, canvas, require_pinned)
                if isinstance(obj, dict) and isinstance(obj.get("id"), str):
                    if obj["id"] in object_ids:
                        c.fail(f"{opath}.id", "identifiant d'objet en double dans la scène.")
                    object_ids[obj["id"]] = obj.get("type", "")

    camera = render.get("camera")
    if camera is not None and c.obj(camera, f"{rpath}.camera", ("keyframes",), ("easing",)):
        _check_keyframes(c, camera["keyframes"], f"{rpath}.camera.keyframes", duration, {
            "x": (0, canvas["width"]), "y": (0, canvas["height"]), "zoom": (0.25, 8), "rotation": (-45, 45)})
        if "easing" in camera:
            c.choice(camera["easing"], f"{rpath}.camera.easing", EASINGS)

    speech = render.get("speech")
    if speech is not None and c.obj(speech, f"{rpath}.speech", ("cues",), ("speaker",)):
        if "speaker" in speech and c.slug(speech["speaker"], f"{rpath}.speech.speaker"):
            if object_ids.get(speech["speaker"]) != "asset":
                c.fail(f"{rpath}.speech.speaker", "doit désigner un objet « asset » de cette scène.")
        if c.array(speech["cues"], f"{rpath}.speech.cues", 1, MAX_SPEECH_CUES):
            previous_end = 0
            for index, cue in enumerate(speech["cues"]):
                item = f"{rpath}.speech.cues[{index}]"
                if c.obj(cue, item, ("text", "start", "end")):
                    c.text(cue["text"], f"{item}.text", CUE_TEXT_MAX, allow_empty=False)
                    _check_window(c, cue, item, duration)
                    if isinstance(cue["start"], int) and cue["start"] < previous_end:
                        c.fail(f"{item}.start", "les timecodes de parole ne peuvent pas se chevaucher.")
                    if isinstance(cue["end"], int):
                        previous_end = cue["end"]

    transition_frames = 0
    for key in ("transition_in", "transition_out"):
        transition = render.get(key)
        if transition is None or not c.obj(transition, f"{rpath}.{key}", ("type", "frames")):
            continue
        if c.choice(transition["type"], f"{rpath}.{key}.type", TRANSITIONS) and c.integer(
                transition["frames"], f"{rpath}.{key}.frames", 0, fps * MAX_TRANSITION_SECONDS):
            if transition["type"] == "cut" and transition["frames"] != 0:
                c.fail(f"{rpath}.{key}.frames", "une coupe franche (« cut ») dure 0 image.")
            elif transition["type"] != "cut" and transition["frames"] == 0:
                c.fail(f"{rpath}.{key}.frames", "une transition animée dure au moins 1 image.")
            transition_frames += transition["frames"]
    if transition_frames > duration:
        c.fail(f"{rpath}.transition_out", "les transitions d'entrée et de sortie dépassent la durée de la scène.")

    # Repli : ce que le rendu hybride (#120) affiche si l'animation de CETTE scène échoue — une image fixe.
    fallback, fpath = render["fallback"], f"{rpath}.fallback"
    if c.obj(fallback, fpath, ("type",), ("frame", "asset")):
        if c.choice(fallback["type"], f"{fpath}.type", ("still", "asset")):
            if fallback["type"] == "still":
                if "asset" in fallback:
                    c.fail(f"{fpath}.asset", "inutile pour un repli « still » (image de la scène elle-même).")
                if "frame" not in fallback:
                    c.fail(f"{fpath}.frame", "obligatoire pour un repli « still ».")
                else:
                    c.integer(fallback["frame"], f"{fpath}.frame", 0, duration - 1)
            else:
                if "frame" in fallback:
                    c.fail(f"{fpath}.frame", "inutile pour un repli « asset ».")
                if "asset" not in fallback:
                    c.fail(f"{fpath}.asset", "obligatoire pour un repli « asset ».")
                else:
                    _check_ref(c, fallback["asset"], f"{fpath}.asset", require_pinned)


# -- bloc « frozen » -------------------------------------------------------------------------------------
def _check_frozen(c: _Checker, frozen: Any) -> None:
    if not c.obj(frozen, "frozen", ("at", "sha256", "assets")):
        return
    c.text(frozen["at"], "frozen.at", 40, allow_empty=False)
    if not isinstance(frozen["sha256"], str) or not SHA256.match(frozen["sha256"]):
        c.fail("frozen.sha256", "empreinte SHA-256 hexadécimale attendue.")
    if not c.array(frozen["assets"], "frozen.assets", 0, 500):
        return
    seen: set[tuple[str, int]] = set()
    for index, entry in enumerate(frozen["assets"]):
        path = f"frozen.assets[{index}]"
        if not c.obj(entry, path, ("id", "version", "sha256", "kind", "name")):
            continue
        _check_ref(c, {"id": entry["id"], "version": entry["version"]}, path, True)
        if not isinstance(entry["sha256"], str) or not SHA256.match(entry["sha256"]):
            c.fail(f"{path}.sha256", "empreinte SHA-256 hexadécimale attendue.")
        c.choice(entry["kind"], f"{path}.kind", ASSET_KINDS)
        c.text(entry["name"], f"{path}.name", 120)
        key = (entry["id"], entry["version"])
        if key in seen:
            c.fail(path, "asset figé en double.")
        seen.add(key)


# -- API -------------------------------------------------------------------------------------------------
def validate_document(raw: Any, *, require_pinned: bool = False) -> dict[str, Any]:
    """Valide un storyboard animé et renvoie une copie indépendante. Lève ``AnimationValidationError``.

    ``require_pinned`` : document FIGÉ pour une production — toutes les références d'assets portent une
    version explicite, le bloc ``frozen`` est présent, couvre exactement ces références, et son empreinte
    correspond au contenu (aucune modification après le gel)."""
    c = _Checker()
    if not c.obj(raw, "$", ("schema", "canvas", "scenes"), ("frozen",)):
        raise AnimationValidationError(c.errors)
    if raw["schema"] != SCHEMA:
        c.fail("schema", f"format non pris en charge (seul « {SCHEMA} » l'est).")
    canvas = raw["canvas"]
    if c.obj(canvas, "canvas", ("fps", "width", "height")):
        if canvas["fps"] not in FPS_CHOICES or isinstance(canvas["fps"], bool):
            c.fail("canvas.fps", "images par seconde : " + ", ".join(map(str, FPS_CHOICES)) + ".")
        for key in ("width", "height"):
            if c.integer(canvas[key], f"canvas.{key}", CANVAS_MIN, CANVAS_MAX) and canvas[key] % 2:
                c.fail(f"canvas.{key}", "dimension paire attendue (encodage H.264 yuv420p).")
    if c.errors:  # sans canevas valide, les bornes des scènes n'ont pas de sens
        raise AnimationValidationError(c.errors)

    if c.array(raw["scenes"], "scenes", 1, MAX_SCENES):
        scene_ids: set[str] = set()
        for index, scene in enumerate(raw["scenes"]):
            _check_scene(c, scene, f"scenes[{index}]", canvas, require_pinned)
            if isinstance(scene, dict) and isinstance(scene.get("id"), str):
                if scene["id"] in scene_ids:
                    c.fail(f"scenes[{index}].id", "identifiant de scène en double.")
                scene_ids.add(scene["id"])

    if "frozen" in raw:
        _check_frozen(c, raw["frozen"])
    elif require_pinned:
        c.fail("frozen", "bloc obligatoire dans un storyboard figé.")

    if not c.errors and require_pinned:
        pinned = {(ref["id"], ref["version"]) for _, ref in iter_asset_refs(raw)}
        frozen_keys = {(entry["id"], entry["version"]) for entry in raw["frozen"]["assets"]}
        if pinned != frozen_keys:
            c.fail("frozen.assets", "ne correspond pas exactement aux assets référencés par les scènes.")
        if raw["frozen"]["sha256"] != document_sha256(raw):
            c.fail("frozen.sha256", "le storyboard a été modifié après avoir été figé.")

    # Le bloc ``frozen`` est exclu : ses empreintes SHA-256 sont des jetons opaques légitimes, générés ici.
    content = {key: value for key, value in raw.items() if key != "frozen"}
    if not c.errors and (leaked := find_secret_path(content)):
        c.fail(leaked, SECRET_MESSAGE)
    if c.errors:
        raise AnimationValidationError(c.errors)
    return copy.deepcopy(raw)


def iter_asset_refs(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """(chemin, référence) de chaque asset utilisé, dans l'ordre du document (objets puis repli)."""
    for si, scene in enumerate(document.get("scenes") or []):
        render = scene.get("render") or {}
        for li, layer in enumerate(render.get("layers") or []):
            for oi, obj in enumerate(layer.get("objects") or []):
                if obj.get("type") == "asset" and isinstance(obj.get("asset"), dict):
                    yield f"scenes[{si}].render.layers[{li}].objects[{oi}].asset", obj["asset"]
        fallback = render.get("fallback") or {}
        if fallback.get("type") == "asset" and isinstance(fallback.get("asset"), dict):
            yield f"scenes[{si}].render.fallback.asset", fallback["asset"]


def canonical_json(value: Any) -> str:
    """JSON canonique (clés triées, aucun espace) : base stable des empreintes et des comparaisons."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def document_sha256(document: dict[str, Any]) -> str:
    """Empreinte du CONTENU du storyboard (le bloc ``frozen`` est exclu : il contient l'empreinte)."""
    content = {key: value for key, value in document.items() if key != "frozen"}
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def total_frames(document: dict[str, Any]) -> int:
    return sum(int(scene["duration_frames"]) for scene in document["scenes"])


def duration_seconds(document: dict[str, Any]) -> float:
    return round(total_frames(document) / document["canvas"]["fps"], 3)
