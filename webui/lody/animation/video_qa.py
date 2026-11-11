"""Contrôle qualité automatique d'un MP4 Remotion 9:16 (#166) : image, sous-titres et synchronisation.

Ce module regarde la vidéo **livrée** (``out.mp4``) avec les données déjà produites par la chaîne 2D — storyboard
figé, ``manifest.json``, ``subtitles.srt``, ``voice.wav`` / ``music.wav`` — et produit :

    qa/qa.json      constats structurés (schéma ``lody.video-qa/1``)
    qa/index.html   rapport HTML AUTONOME : captures début/milieu/fin de chaque scène intégrées côte à côte,
                    lien vers le MP4, répliques, avertissements

Tout est **local, déterministe et gratuit** (ffprobe/ffmpeg + lecture de JSON) : aucun appel réseau, aucune
génération. Familles de contrôles :

- **Technique** (ffprobe) : format 9:16, résolution, cadence, durée et nombre d'images face au storyboard,
  codecs, présence et durée de la piste audio, empreinte du MP4 face au manifeste, scènes de repli.
- **Image** (ffmpeg) : écrans noirs (``blackdetect``), bandes noires (``cropdetect``), captures vides ou noires.
- **Mise en page estimée** depuis le storyboard, avec les mêmes interpolations que le moteur Remotion
  (``renderers/remotion/lib/anim.mjs``) : étiquette ou schéma rogné / hors zone de sécurité, élément caché par
  un personnage, sous-titre hors zone de sécurité ou sur le personnage, objet cité dans une réplique absent de
  l'image, locuteur hors champ.
- **Synchronisation** : ``subtitles.srt`` face aux repères du storyboard, trous audio, parole absente pendant un
  sous-titre, voix hors sous-titres, voix coupée, musique seule trop longue (entre deux répliques ou en fin).

Limites assumées : les boîtes des textes sont **estimées** (largeur moyenne d'un caractère), celles des
personnages englobent tout le dessin (bras, cheveux) ; un constat invite à regarder, il ne bloque jamais la
vidéo. Un contrôle technique **ne garantit pas** la justesse pédagogique : le rapport le rappelle.

Niveaux : ``info`` (à savoir), ``warning`` (à vérifier), ``error`` (défaut certain : écran noir, voix coupée,
format faux). Complémentaire des contrôles du storyboard avant rendu (#121) : ici, on juge la sortie finale.
"""

from __future__ import annotations

import base64
import html
import json
import math
import os
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

QA_SCHEMA = "lody.video-qa/1"
QA_DIR = "qa"
LEVELS = ("info", "warning", "error")

# Zone de sécurité 9:16 (fractions du cadre) : marges couvertes par l'interface des plateformes verticales
# (Shorts, TikTok, Reels : titre et boutons en bas, actions à droite). Approximation volontairement simple.
SAFE_TOP, SAFE_BOTTOM, SAFE_SIDE = 0.08, 0.10, 0.04
ASPECT = 9 / 16
EDGE_TOLERANCE = 2.0               # px : dépassement ignoré (anticrénelage, arrondis)
ASSET_CLIPPED_RATIO = 0.05         # schéma/objet : part de la surface hors image signalée
CHARACTER_CLIPPED_RATIO = 0.20     # personnage : la boîte englobe des marges vides, seuil plus large
CHARACTER_INSET = (0.15, 0.10)     # boîte « utile » d'un personnage : retrait latéral / haut (bras, cheveux)
FEET_RATIO = 0.25                  # bas de la boîte utile = pieds / ressorts
HIDDEN_RATIO = 0.30                # élément couvert à plus de 30 % par un personnage
SUBTITLE_COVER_RATIO = 0.20        # élément couvert à plus de 20 % par le sous-titre
BLACK_MIN_SECONDS = 0.5
BLACK_PIXEL_THRESHOLD = 0.10
BORDER_MIN_RATIO = 0.02            # bande noire : au moins 2 % du cadre sur un bord
EMPTY_STDDEV = 4.0                 # capture quasi uniforme (sur 0-255)
DARK_MEAN = 16.0
AUDIO_WINDOW = 0.1                 # s
SILENCE_DB = -50.0                 # trou audio : sous ce niveau
SILENCE_MIN_SECONDS = 1.0
VOICE_ACTIVE_DB = -40.0            # voix seule : fenêtre « parlée »
SPEECH_COVERAGE_MIN = 0.30         # part minimale d'un sous-titre réellement parlée
SPEECH_CONTRAST_DB = 6.0           # mixage : la parole doit dépasser le fond musical d'au moins 6 dB
VOICE_OUTSIDE_SECONDS = 0.8        # voix sans sous-titre au-delà
CUE_MARGIN = 0.3
VOICE_CUT_TOLERANCE = 0.25
AUDIO_VIDEO_TOLERANCE = 0.25
SRT_TOLERANCE_MS = 50
MUSIC_GAP_SECONDS = 4.0            # musique seule entre deux répliques
MUSIC_TAIL_SECONDS = 3.0           # musique seule après la dernière réplique
CAPTURE_WIDTH = 270
STAT_SIZE = (54, 96)

# Styles de texte du moteur (renderers/remotion/src/LodyScene.jsx) : taille, marge interne x / y (bordure comprise).
TEXT_STYLES = {"title": (88, 10, 10), "subtitle": (54, 30, 14), "caption": (50, 26, 12), "label": (40, 25, 13)}
CHAR_WIDTH = 0.6
_STOPWORDS = frozenset(
    "les des une dans pour par avec sur sous est son ses aux qui que quoi comme mais plus moins tout tous "
    "bout cest moi toi nous vous elle ils elles leur cette ces cet pas peu tres bien voici voila alors donc "
    "marionnette coupe vue face".split())


def finding(check_id: str, level: str, message: str, scene: int | None = None, at: float | None = None,
            **data: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"id": check_id, "level": level, "message": message}
    if scene is not None:
        item["scene"] = scene
    if at is not None:
        item["at"] = round(at, 2)
    if data:
        item["data"] = data
    return item


# -- interpolation (copie fidèle de renderers/remotion/lib/anim.mjs) --------------------------------------------
def ease(name: str, t: float) -> float:
    x = min(1.0, max(0.0, t))
    if name == "linear":
        return x
    if name == "ease_in":
        return x ** 3
    if name == "ease_out":
        return 1 - (1 - x) ** 3
    if name == "spring":
        return 1.0 if x >= 1 else 1 - math.exp(-6 * x) * math.cos(10 * x) * (1 - x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def sample(keys: Any, frame: float, name: str, easing: str = "ease_in_out", fallback: float = 0.0) -> float:
    if not isinstance(keys, list) or not keys:
        return fallback
    if frame <= keys[0]["frame"]:
        return keys[0][name]
    if frame >= keys[-1]["frame"]:
        return keys[-1][name]
    for a, b in zip(keys, keys[1:]):
        if a["frame"] <= frame < b["frame"]:
            return a[name] + (b[name] - a[name]) * ease(easing, (frame - a["frame"]) / (b["frame"] - a["frame"]))
    return keys[-1][name]


def in_motion(obj: dict[str, Any], frame: float) -> bool:
    """Vrai pendant un déplacement (entrée, sortie, glissement) : un objet qui entre est volontairement rogné."""
    keys = obj.get("position") or []
    return any(a["frame"] <= frame < b["frame"] and (a["x"], a["y"]) != (b["x"], b["y"]) for a, b in zip(keys, keys[1:]))


def is_visible(obj: dict[str, Any], frame: int, duration: int) -> bool:
    return (obj.get("start") or 0) <= frame < (obj.get("end") if obj.get("end") is not None else duration)


# -- géométrie estimée ----------------------------------------------------------------------------------------
Box = tuple[float, float, float, float]  # x0, y0, x1, y1 en pixels de l'image finale


def area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def intersection(a: Box, b: Box) -> float:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def wrap_lines(text: str, font_size: float, available: float) -> int:
    """Nombre de lignes après retour à la ligne automatique (largeur moyenne de caractère)."""
    per_line = max(1, int(available // (font_size * CHAR_WIDTH)))
    lines, current = 1, 0
    for word in text.split():
        if current == 0:
            current = len(word)
        elif current + 1 + len(word) <= per_line:
            current += 1 + len(word)
        else:
            lines, current = lines + 1, len(word)
        while current > per_line:
            lines, current = lines + 1, current - per_line
    return lines


@dataclass
class Placed:
    obj_id: str
    kind: str                     # text | character | prop | background | unknown
    label: str
    box: Box | None
    opacity: float
    names: frozenset[str] = field(default_factory=frozenset)
    moving: bool = False


def _camera(render: dict[str, Any], frame: float, canvas: dict[str, Any]) -> tuple[float, float, float]:
    camera = render.get("camera") or {}
    keys, easing = camera.get("keyframes"), camera.get("easing") or "ease_in_out"
    return (sample(keys, frame, "x", easing, canvas["width"] / 2), sample(keys, frame, "y", easing, canvas["height"] / 2),
            sample(keys, frame, "zoom", easing, 1.0))


def _to_screen(box: Box, cam: tuple[float, float, float], canvas: dict[str, Any]) -> Box:
    cx, cy, zoom = cam
    w2, h2 = canvas["width"] / 2, canvas["height"] / 2
    return ((box[0] - cx) * zoom + w2, (box[1] - cy) * zoom + h2, (box[2] - cx) * zoom + w2, (box[3] - cy) * zoom + h2)


def tokens(text: str) -> set[str]:
    """Mots significatifs, sans accents ni casse (« Câble » → « cable ») ; ni nombres, ni mots outils."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return {word for word in re.findall(r"[a-z][a-z0-9]+", plain.replace("'", "")) if len(word) >= 3
            and word not in _STOPWORDS}


def place_objects(scene: dict[str, Any], frame: int, canvas: dict[str, Any], asset_info: dict[str, Any],
                  names: dict[str, str]) -> list[Placed]:
    """Boîtes (estimées) des objets visibles d'une scène à l'image ``frame``, caméra comprise."""
    render, duration = scene["render"], scene["duration_frames"]
    cam = _camera(render, frame, canvas)
    placed = []
    for layer in render.get("layers") or []:
        for obj in layer.get("objects") or []:
            if not is_visible(obj, frame, duration):
                continue
            easing = obj.get("easing") or "ease_in_out"
            x, y = sample(obj.get("position"), frame, "x", easing), sample(obj.get("position"), frame, "y", easing)
            scale = sample(obj.get("scale"), frame, "value", easing, 1.0)
            opacity = sample(obj.get("opacity"), frame, "value", easing, 1.0)
            if obj["type"] == "text":
                content = str(obj["text"]["content"])
                size, pad_x, pad_y = TEXT_STYLES.get(obj["text"].get("style"), TEXT_STYLES["caption"])
                w = (len(content) * size * CHAR_WIDTH + 2 * pad_x) * scale
                h = (size * 1.25 + 2 * pad_y) * scale
                box: Box | None = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
                placed.append(Placed(obj["id"], "text", content, _to_screen(box, cam, canvas), opacity,
                                     frozenset(tokens(content)), in_motion(obj, frame)))
                continue
            key = f"{obj['asset']['id']}@{obj['asset']['version']}"
            info = asset_info.get(key) or {}
            kind = str(info.get("kind") or "unknown")
            label = (names.get(key) or "").split(" — ")[0].strip() or str(info.get("slug") or obj["id"])
            words = tokens(label) | tokens(str(info.get("slug") or "").replace("-", " ")) | tokens(obj["id"].replace("-", " "))
            box = None
            if info.get("width") and info.get("height"):
                anchor = info.get("anchor") or {}
                ax = float(anchor.get("x", 0.5)) if isinstance(anchor, dict) else float(anchor[0])
                ay = float(anchor.get("y", 0.98 if kind == "character" else 0.5)) if isinstance(anchor, dict) else float(anchor[1])
                w, h = float(info["width"]) * scale, float(info["height"]) * scale
                box = _to_screen((x - ax * w, y - ay * h, x + (1 - ax) * w, y + (1 - ay) * h), cam, canvas)
            placed.append(Placed(obj["id"], kind, label, box, opacity, frozenset(words), in_motion(obj, frame)))
    return placed


def useful_box(item: Placed) -> Box | None:
    """Boîte d'un personnage réduite à son corps (la boîte du dessin inclut bras levés et cheveux)."""
    if item.box is None:
        return None
    x0, y0, x1, y1 = item.box
    if item.kind != "character":
        return item.box
    dx, dy = (x1 - x0) * CHARACTER_INSET[0], (y1 - y0) * CHARACTER_INSET[1]
    return (x0 + dx, y0 + dy, x1 - dx, y1)


def caption_box(text: str, canvas: dict[str, Any]) -> tuple[Box, int]:
    """Boîte du sous-titre incrusté (composant ``Captions`` du moteur) et nombre de lignes estimé."""
    width, height = canvas["width"], canvas["height"]
    font = round(width * 0.056)
    available = width * 0.88 - 56
    lines = wrap_lines(text, font, available)
    text_width = min(available, len(text) * font * CHAR_WIDTH) if lines == 1 else available
    box_width = text_width + 56
    top = height * 0.80
    return ((width - box_width) / 2, top, (width + box_width) / 2, top + lines * font * 1.25 + 24), lines


def safe_box(canvas: dict[str, Any]) -> Box:
    w, h = canvas["width"], canvas["height"]
    return (w * SAFE_SIDE, h * SAFE_TOP, w * (1 - SAFE_SIDE), h * (1 - SAFE_BOTTOM))


def _outside(box: Box, frame_box: Box) -> list[str]:
    sides = []
    if box[0] < frame_box[0] - EDGE_TOLERANCE:
        sides.append("gauche")
    if box[2] > frame_box[2] + EDGE_TOLERANCE:
        sides.append("droit")
    if box[1] < frame_box[1] - EDGE_TOLERANCE:
        sides.append("haut")
    if box[3] > frame_box[3] + EDGE_TOLERANCE:
        sides.append("bas")
    return sides


def _short(text: str, size: int = 40) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= size else text[: size - 1] + "…"


def sample_frames(scene: dict[str, Any]) -> list[tuple[str, int]]:
    """Images capturées par scène : début (après la transition d'entrée), milieu, fin (avant la sortie)."""
    duration = scene["duration_frames"]
    t_in = int((scene["render"].get("transition_in") or {}).get("frames") or 0)
    t_out = int((scene["render"].get("transition_out") or {}).get("frames") or 0)
    start = min(duration - 1, t_in + 3)
    end = max(0, duration - 1 - t_out - 3)
    return [("début", start), ("milieu", duration // 2), ("fin", max(start, end))]


def layout_checks(storyboard: dict[str, Any], asset_info: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Mise en page estimée depuis le storyboard : rognages, zone de sécurité, recouvrements, objets cités."""
    canvas = storyboard["canvas"]
    fps = canvas["fps"]
    asset_info = asset_info or {}
    names = {f"{a['id']}@{a['version']}": str(a.get("name") or "")
             for a in ((storyboard.get("frozen") or {}).get("assets") or []) if isinstance(a, dict) and "id" in a}
    frame_box: Box = (0.0, 0.0, float(canvas["width"]), float(canvas["height"]))
    safe = safe_box(canvas)
    found: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    vocabulary: set[str] = set()
    unknown: set[str] = set()
    for scene in storyboard["scenes"]:
        for layer in scene["render"].get("layers") or []:
            for obj in layer.get("objects") or []:
                if obj["type"] == "asset":
                    key = f"{obj['asset']['id']}@{obj['asset']['version']}"
                    info = asset_info.get(key) or {}
                    if info.get("kind") == "background":
                        continue
                    if not (info.get("width") and info.get("height")):
                        unknown.add(obj["id"])
                    vocabulary |= tokens(names.get(key, "")) | tokens(str(info.get("slug") or "").replace("-", " "))

    def add(key: tuple, item: dict[str, Any]) -> None:
        if key not in seen:
            seen.add(key)
            found.append(item)

    offset = 0
    for index, scene in enumerate(storyboard["scenes"], 1):
        frames = [frame for _, frame in sample_frames(scene)]
        for frame in frames:
            at = (offset + frame) / fps
            placed = [p for p in place_objects(scene, frame, canvas, asset_info, names) if p.opacity > 0.05]
            characters = [p for p in placed if p.kind == "character" and p.box]
            for item in placed:
                if item.box is None or item.kind == "background" or item.moving:
                    continue
                if item.kind == "text":
                    sides = _outside(item.box, frame_box)
                    if sides:
                        add(("text_clipped", index, item.obj_id), finding(
                            "text_clipped", "warning", f"Scène {index} : texte « {_short(item.label)} » rogné "
                            f"(dépasse le bord {', '.join(sides)} de l'image).", index, at, object=item.obj_id))
                    elif _outside(item.box, safe):
                        add(("text_unsafe", index, item.obj_id), finding(
                            "text_unsafe", "info", f"Scène {index} : texte « {_short(item.label)} » hors de la zone "
                            "de sécurité (risque d'être masqué par l'interface de la plateforme).", index, at,
                            object=item.obj_id))
                else:
                    outside = 1 - intersection(item.box, frame_box) / max(1.0, area(item.box))
                    limit = CHARACTER_CLIPPED_RATIO if item.kind == "character" else ASSET_CLIPPED_RATIO
                    if outside > limit:
                        add(("asset_clipped", index, item.obj_id), finding(
                            "asset_clipped", "warning", f"Scène {index} : « {_short(item.label)} » coupé par le bord "
                            f"de l'image ({outside:.0%} de sa surface hors champ).", index, at, object=item.obj_id,
                            outside=round(outside, 3)))
                for person in characters:
                    if person is item or item.kind == "character":
                        continue
                    covered = intersection(useful_box(person), item.box) / max(1.0, area(item.box))
                    if covered > HIDDEN_RATIO:
                        add(("hidden_by_character", index, item.obj_id), finding(
                            "hidden_by_character", "warning", f"Scène {index} : « {_short(item.label)} » "
                            f"probablement caché par {person.label} ({covered:.0%} recouvert, estimation).", index,
                            at, object=item.obj_id, character=person.obj_id, covered=round(covered, 3)))
        speech = scene["render"].get("speech") or {}
        for cue in speech.get("cues") or []:
            middle = (cue["start"] + cue["end"]) // 2
            at = (offset + middle) / fps
            text = str(cue["text"])
            placed = [p for p in place_objects(scene, middle, canvas, asset_info, names) if p.opacity > 0.05]
            box, lines = caption_box(text, canvas)
            sides = _outside(box, safe)
            if sides:
                add(("subtitle_unsafe", index, cue["start"]), finding(
                    "subtitle_unsafe", "warning", f"Scène {index} : sous-titre « {_short(text)} » hors de la zone "
                    f"de sécurité ({lines} ligne(s), bord {', '.join(sides)}).", index, at, lines=lines))
            for item in placed:
                if item.box is None or item.kind == "background":
                    continue
                if item.kind == "character":
                    body = useful_box(item)
                    feet = (body[0], body[3] - (body[3] - body[1]) * FEET_RATIO, body[2], body[3])
                    if intersection(box, feet) > 0:
                        add(("subtitle_on_character", index, item.obj_id, cue["start"]), finding(
                            "subtitle_on_character", "warning", f"Scène {index} : le sous-titre « {_short(text)} » "
                            f"recouvre le bas de {item.label} (pieds).", index, at, object=item.obj_id))
                elif intersection(box, item.box) / max(1.0, area(item.box)) > SUBTITLE_COVER_RATIO:
                    add(("subtitle_covers", index, item.obj_id, cue["start"]), finding(
                        "subtitle_covers", "warning", f"Scène {index} : le sous-titre « {_short(text)} » recouvre "
                        f"« {_short(item.label)} ».", index, at, object=item.obj_id))
            on_screen = [p for p in placed if p.box is None or intersection(p.box, frame_box) > 0]
            shown = set().union(*(p.names for p in on_screen)) if on_screen else set()
            missing = sorted((tokens(text) & vocabulary) - shown)
            if missing:
                add(("cited_object_missing", index, cue["start"]), finding(
                    "cited_object_missing", "warning", f"Scène {index} : la réplique « {_short(text)} » cite "
                    f"{', '.join('« ' + word + ' »' for word in missing)}, absent de l'image à ce moment.", index, at,
                    words=missing))
            speaker = speech.get("speaker")
            if speaker:
                ids = {p.obj_id: p for p in placed}
                person = ids.get(speaker)
                if person is None or (person.box is not None and intersection(person.box, frame_box) <= 0):
                    add(("speaker_offscreen", index, cue["start"]), finding(
                        "speaker_offscreen", "warning", f"Scène {index} : le locuteur « {speaker} » n'est pas à "
                        f"l'image pendant « {_short(text)} ».", index, at))
        offset += scene["duration_frames"]
    if unknown:
        found.append(finding("geometry_unknown", "info", "Dimensions inconnues pour "
                             f"{', '.join(sorted(unknown))} : rognage et recouvrements non vérifiés pour ces objets."))
    return found


# -- synchronisation (storyboard, SRT, audio) -----------------------------------------------------------------
def storyboard_cues(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Répliques en secondes absolues, avec leur scène."""
    fps = storyboard["canvas"]["fps"]
    cues, offset = [], 0
    for index, scene in enumerate(storyboard["scenes"], 1):
        for cue in ((scene["render"].get("speech") or {}).get("cues") or []):
            cues.append({"scene": index, "text": str(cue["text"]), "start": (offset + cue["start"]) / fps,
                         "end": (offset + cue["end"]) / fps})
        offset += scene["duration_frames"]
    return cues


def srt_checks(storyboard: dict[str, Any], srt: str | None) -> list[dict[str, Any]]:
    from lody.generation.subtitles import parse_srt

    expected = storyboard_cues(storyboard)
    if not expected:
        return []
    if srt is None:
        return [finding("srt_missing", "info", "Pas de fichier subtitles.srt : seuls les sous-titres incrustés "
                        "sont contrôlés.")]
    actual = parse_srt(srt)
    if len(actual) != len(expected):
        return [finding("srt_mismatch", "warning", f"subtitles.srt contient {len(actual)} sous-titre(s), le storyboard "
                        f"{len(expected)} réplique(s).", expected=len(expected), actual=len(actual))]
    found = []
    for cue, srt_cue in zip(expected, actual):
        shift = max(abs(srt_cue.start_ms - cue["start"] * 1000), abs(srt_cue.end_ms - cue["end"] * 1000))
        if shift > SRT_TOLERANCE_MS or " ".join(srt_cue.text.split()) != " ".join(cue["text"].split()):
            found.append(finding("srt_mismatch", "warning", f"Scène {cue['scene']} : le sous-titre SRT « "
                                 f"{_short(srt_cue.text)} » ne correspond pas à la réplique incrustée (décalage "
                                 f"{shift / 1000:.2f} s ou texte différent).", cue["scene"], cue["start"],
                                 shift_ms=round(shift)))
    return found


def music_checks(cues: list[dict[str, Any]], duration: float | None, has_music: bool) -> list[dict[str, Any]]:
    """Musique seule trop longue : entre deux répliques, ou après la dernière (d'après les minutages)."""
    if not cues or not duration or not has_music:
        return []
    found = []
    for a, b in zip(cues, cues[1:]):
        gap = b["start"] - a["end"]
        if gap > MUSIC_GAP_SECONDS:
            found.append(finding("music_gap", "info", f"Scène {b['scene']} : {gap:.1f} s de musique seule avant « "
                                 f"{_short(b['text'])} ».", b["scene"], a["end"], seconds=round(gap, 2)))
    tail = duration - cues[-1]["end"]
    if tail > MUSIC_TAIL_SECONDS:
        found.append(finding("music_tail", "warning", f"La musique continue {tail:.1f} s après la dernière réplique "
                             f"(au plus {MUSIC_TAIL_SECONDS:.0f} s conseillées).", cues[-1]["scene"], cues[-1]["end"],
                             seconds=round(tail, 2)))
    return found


def runs(levels: list[float], predicate: Any, window: float = AUDIO_WINDOW) -> list[tuple[float, float]]:
    """Intervalles (s) où ``predicate(niveau)`` est vrai, fenêtre par fenêtre."""
    found, start = [], None
    for i, level in enumerate([*levels, None]):
        hit = level is not None and predicate(level)
        if hit and start is None:
            start = i
        elif not hit and start is not None:
            found.append((round(start * window, 3), round(i * window, 3)))
            start = None
    return found


def _window_values(levels: list[float], start: float, end: float) -> list[float]:
    return levels[max(0, int(start / AUDIO_WINDOW)): max(0, int(math.ceil(end / AUDIO_WINDOW)))]


def audio_checks(mix_levels: list[float] | None, voice_levels: list[float] | None, cues: list[dict[str, Any]],
                 duration: float | None) -> list[dict[str, Any]]:
    """Trous audio dans la bande son finale ; parole absente pendant un sous-titre ; voix sans sous-titre."""
    found = []
    if mix_levels:
        end = (duration or len(mix_levels) * AUDIO_WINDOW) - 0.3   # le fondu final peut atteindre le silence
        for a, b in runs(mix_levels, lambda level: level < SILENCE_DB):
            b = min(b, end)
            if b - max(a, 0.2) >= SILENCE_MIN_SECONDS:
                found.append(finding("audio_gap", "warning", f"Silence complet de {b - a:.1f} s à {a:.1f} s "
                                     "(ni voix ni musique).", at=a, seconds=round(b - a, 2)))
    if voice_levels:
        for cue in cues:
            values = _window_values(voice_levels, cue["start"], cue["end"])
            coverage = sum(level > VOICE_ACTIVE_DB for level in values) / max(1, len(values))
            if coverage < SPEECH_COVERAGE_MIN:
                found.append(finding("speech_missing", "warning", f"Scène {cue['scene']} : sous-titre « "
                                     f"{_short(cue['text'])} » affiché alors que la voix est presque muette "
                                     f"({coverage:.0%} du temps) — décalage voix/sous-titre ?", cue["scene"],
                                     cue["start"], coverage=round(coverage, 2)))
        for a, b in runs(voice_levels, lambda level: level > VOICE_ACTIVE_DB):
            covered = any(cue["start"] - CUE_MARGIN <= a and b <= cue["end"] + CUE_MARGIN for cue in cues)
            outside = sum(max(0.0, min(b, c["end"] + CUE_MARGIN) - max(a, c["start"] - CUE_MARGIN)) for c in cues)
            if not covered and (b - a) - outside >= VOICE_OUTSIDE_SECONDS:
                found.append(finding("speech_unsubtitled", "warning", f"Voix entendue de {a:.1f} s à {b:.1f} s sans "
                                     "sous-titre correspondant — décalage voix/sous-titre ?", at=a))
    elif mix_levels and cues:
        cue_windows = [_window_values(mix_levels, c["start"], c["end"]) for c in cues]
        background = sorted(level for i, level in enumerate(mix_levels)
                            if not any(c["start"] - CUE_MARGIN <= i * AUDIO_WINDOW <= c["end"] + CUE_MARGIN
                                       for c in cues) and level > SILENCE_DB)
        if background:
            floor = background[len(background) // 2]
            for cue, values in zip(cues, cue_windows):
                peak = sorted(values)[int(len(values) * 0.9)] if values else SILENCE_DB
                if peak < floor + SPEECH_CONTRAST_DB:
                    found.append(finding("speech_missing", "warning", f"Scène {cue['scene']} : aucune parole nette "
                                         f"pendant « {_short(cue['text'])} » (niveau {peak:.0f} dB, fond "
                                         f"{floor:.0f} dB) — voix absente ou décalée ?", cue["scene"], cue["start"]))
    return found


# -- mesures ffmpeg -------------------------------------------------------------------------------------------
def _run(command: list[str], timeout: float = 600, binary: bool = False) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(command, capture_output=True, text=not binary, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def ffprobe_exe(ffmpeg: str | None) -> str | None:
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
        if sibling.is_file():
            return str(sibling)
    return shutil.which("ffprobe")


def _fps(value: Any) -> float | None:
    try:
        number = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError):
        return None
    return number or None


def probe(ffmpeg: str, ffprobe: str | None, path: Path) -> dict[str, Any]:
    """Caractéristiques du MP4 : {"duration", "video": {...}, "audio": {...} | None}."""
    if ffprobe:
        done = _run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], 60)
        try:
            data = json.loads(done.stdout) if done and done.returncode == 0 else None
        except ValueError:
            data = None
        if data:
            streams = data.get("streams") or []
            video = next((s for s in streams if s.get("codec_type") == "video"), None)
            sound = next((s for s in streams if s.get("codec_type") == "audio"), None)
            return {
                "duration": float((data.get("format") or {}).get("duration") or 0) or None,
                "video": video and {"codec": video.get("codec_name"), "width": video.get("width"),
                                    "height": video.get("height"), "fps": _fps(video.get("avg_frame_rate")),
                                    "pix_fmt": video.get("pix_fmt"),
                                    "frames": int(video["nb_frames"]) if str(video.get("nb_frames", "")).isdigit() else None,
                                    "duration": float(video.get("duration") or 0) or None},
                "audio": sound and {"codec": sound.get("codec_name"), "sample_rate": int(sound.get("sample_rate") or 0),
                                    "channels": sound.get("channels"),
                                    "duration": float(sound.get("duration") or 0) or None},
            }
    done = _run([ffmpeg, "-hide_banner", "-i", str(path)], 60)  # repli sans ffprobe : lecture de l'en-tête
    log = done.stderr if done else ""
    duration = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", log)
    line = re.search(r"Stream #[^\n]*?Video: ([^\n]*)", log)
    video = None
    if line:  # ex. « h264 (High) (avc1 / …), yuv420p(tv, bt709, progressive), 1080x1920 [SAR 1:1], …, 30 fps »
        size = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line[1])
        rate = re.search(r"([\d.]+) fps", line[1])
        pix = re.search(r"\b((?:yuv|yuvj|nv|rgb|bgr|gray|gbr)\w*)", line[1])
        video = {"codec": line[1].split()[0].rstrip(","), "pix_fmt": pix[1] if pix else None,
                 "width": int(size[1]) if size else None, "height": int(size[2]) if size else None,
                 "fps": float(rate[1]) if rate else None, "frames": None, "duration": None}
    sound = re.search(r"Audio: (\w+)[^,]*, (\d+) Hz, (\w+)", log)
    return {
        "duration": (int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) if duration else None,
        "video": video,
        "audio": sound and {"codec": sound[1], "sample_rate": int(sound[2]), "channels": sound[3], "duration": None},
    }


def image_scan(ffmpeg: str, path: Path) -> dict[str, Any]:
    """Une passe : écrans noirs (``blackdetect``) et bandes noires (``cropdetect``, image par image)."""
    done = _run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
                 f"blackdetect=d={BLACK_MIN_SECONDS}:pix_th={BLACK_PIXEL_THRESHOLD},cropdetect=limit=0.08:round=2:reset=1",
                 "-f", "null", "-"])
    log = done.stderr if done else ""
    black = [(float(a), float(b)) for a, b in re.findall(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)", log)]
    crops = [tuple(int(v) for v in m) for m in re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", log)]
    return {"black": black, "crops": crops}


def audio_levels(ffmpeg: str, path: Path, stream: str = "0:a:0") -> list[float] | None:
    """Niveau RMS (dBFS) par fenêtre de 100 ms, mono 8 kHz ; ``None`` sans piste audio."""
    done = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", stream, "-ac", "1",
                 "-ar", "8000", "-f", "s16le", "-"], 600, binary=True)
    if not done or done.returncode != 0 or not done.stdout:
        return None
    import numpy as np

    samples = np.frombuffer(done.stdout, dtype="<i2").astype(np.float64) / 32768.0
    size = int(8000 * AUDIO_WINDOW)
    count = len(samples) // size
    if count == 0:
        return None
    rms = np.sqrt(np.mean(samples[: count * size].reshape(count, size) ** 2, axis=1))
    return [round(float(v), 2) for v in 20 * np.log10(np.maximum(rms, 1e-6))]


def capture(ffmpeg: str, path: Path, seconds: float) -> tuple[bytes | None, dict[str, float] | None]:
    """Capture JPEG réduite (pour le rapport) et statistiques de luminance (moyenne, écart-type) de l'image."""
    jpeg = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", str(path),
                 "-frames:v", "1", "-vf", f"scale={CAPTURE_WIDTH}:-2", "-q:v", "4", "-f", "image2", "-c:v", "mjpeg",
                 "-"], 60, binary=True)
    raw = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", str(path),
                "-frames:v", "1", "-vf", f"scale={STAT_SIZE[0]}:{STAT_SIZE[1]}", "-pix_fmt", "gray", "-f", "rawvideo",
                "-"], 60, binary=True)
    stats = None
    if raw and raw.returncode == 0 and raw.stdout:
        values = list(raw.stdout)
        mean = sum(values) / len(values)
        stats = {"mean": round(mean, 1), "stddev": round(math.sqrt(sum((v - mean) ** 2 for v in values) / len(values)), 1)}
    return (jpeg.stdout if jpeg and jpeg.returncode == 0 and jpeg.stdout else None), stats


# -- contrôles techniques -------------------------------------------------------------------------------------
def technical_checks(info: dict[str, Any], storyboard: dict[str, Any], manifest: dict[str, Any] | None,
                     video_sha: str | None) -> list[dict[str, Any]]:
    canvas = storyboard["canvas"]
    fps = canvas["fps"]
    expected = sum(s["duration_frames"] for s in storyboard["scenes"])
    found = []
    video = info.get("video")
    if not video:
        return [finding("no_video", "error", "Aucune piste vidéo lisible dans le MP4.")]
    width, height = video.get("width") or 0, video.get("height") or 0
    if width and height and abs(width / height - ASPECT) > 0.01:
        found.append(finding("aspect", "error", f"Format {width}×{height} : ce n'est pas du 9:16 vertical.",
                             width=width, height=height))
    if (width, height) != (canvas["width"], canvas["height"]):
        found.append(finding("resolution", "warning", f"Résolution {width}×{height}, le storyboard demande "
                             f"{canvas['width']}×{canvas['height']}."))
    if video.get("fps") and abs(video["fps"] - fps) > 0.01:
        found.append(finding("fps", "warning", f"Cadence {video['fps']:.3f} i/s, le storyboard demande {fps} i/s."))
    if video.get("codec") and video["codec"] != "h264":
        found.append(finding("codec", "info", f"Codec vidéo {video['codec']} (H.264 attendu pour les plateformes)."))
    if video.get("pix_fmt") and video["pix_fmt"] != "yuv420p":
        found.append(finding("pix_fmt", "warning", f"Format de pixels {video['pix_fmt']} : yuv420p attendu "
                             "(lecture sur mobile)."))
    if video.get("frames") is not None and abs(video["frames"] - expected) > 1:
        found.append(finding("frames", "warning", f"{video['frames']} images dans le MP4, {expected} prévues par le "
                             "storyboard.", frames=video["frames"], expected=expected))
    duration = info.get("duration")
    if duration and abs(duration - expected / fps) > max(2 / fps, 0.1):
        found.append(finding("duration", "error", f"Durée {duration:.2f} s, {expected / fps:.2f} s prévues par le "
                             "storyboard.", duration=duration, expected=round(expected / fps, 3)))
    sound = info.get("audio")
    if not sound:
        found.append(finding("no_audio", "error", "Aucune piste audio : vidéo muette."))
    else:
        if sound.get("duration") and duration and abs(sound["duration"] - (video.get("duration") or duration)) > AUDIO_VIDEO_TOLERANCE:
            found.append(finding("audio_length", "warning", f"Piste audio de {sound['duration']:.2f} s pour "
                                 f"{video.get('duration') or duration:.2f} s de vidéo."))
        if sound.get("sample_rate") and sound["sample_rate"] < 44100:
            found.append(finding("sample_rate", "info", f"Audio à {sound['sample_rate']} Hz (44,1 ou 48 kHz attendus)."))
    if manifest:
        if video_sha and manifest.get("sha256") and manifest["sha256"] != video_sha:
            found.append(finding("manifest_sha", "warning", "Le MP4 ne correspond plus à l'empreinte de son "
                                 "manifeste : il a été modifié après le rendu."))
        for scene in manifest.get("fallback_scenes") or []:
            found.append(finding("fallback", "warning", f"Scène {scene} : animation en échec, image fixe de repli.",
                                 scene))
    return found


def image_checks(scan: dict[str, Any], storyboard: dict[str, Any], width: int, height: int) -> list[dict[str, Any]]:
    fps = storyboard["canvas"]["fps"]
    bounds, offset = [], 0
    for scene in storyboard["scenes"]:
        offset += scene["duration_frames"]
        bounds.append(offset / fps)

    def scene_of(seconds: float) -> int:
        return next((i for i, end in enumerate(bounds, 1) if seconds < end), len(bounds))

    found = []
    for start, end in scan.get("black") or []:
        found.append(finding("black", "error", f"Écran noir de {end - start:.1f} s à {start:.1f} s.",
                             scene_of(start), start, seconds=round(end - start, 2)))
    crops = scan.get("crops") or []
    if crops and width and height:
        bordered = [c for c in crops if c[0] < width * (1 - BORDER_MIN_RATIO) or c[1] < height * (1 - BORDER_MIN_RATIO)]
        if len(bordered) > len(crops) / 2:
            w, h = max(c[0] for c in bordered), max(c[1] for c in bordered)
            found.append(finding("borders", "warning", f"Bandes noires probables : contenu utile ≈ {w}×{h} dans un "
                                 f"cadre {width}×{height}.", crop=[w, h]))
    return found


# -- chargement -----------------------------------------------------------------------------------------------
def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class Inputs:
    directory: Path
    video: Path
    storyboard: dict[str, Any]
    manifest: dict[str, Any] | None
    srt: str | None
    asset_info: dict[str, Any]
    voice: Path | None
    music: Path | None


def load_inputs(target: Path) -> Inputs:
    """Dossier de tâche du worker (``out.mp4`` + ``job.json``) ou dossier versionné (``*.mp4`` + ``storyboard.json``)."""
    target = Path(target)
    directory = target if target.is_dir() else target.parent
    if target.is_file():
        video = target
    elif (directory / "out.mp4").is_file():
        video = directory / "out.mp4"
    else:
        videos = sorted(p for p in directory.glob("*.mp4") if not p.name.startswith("."))
        if len(videos) != 1:
            raise ValueError("vidéo introuvable : indiquez le fichier MP4 à contrôler")
        video = videos[0]
    job = _json(directory / "job.json") or {}
    storyboard = job.get("storyboard") if isinstance(job, dict) else None
    storyboard = storyboard or _json(directory / "storyboard.json")
    if not isinstance(storyboard, dict) or not storyboard.get("scenes"):
        raise ValueError("storyboard introuvable (job.json ou storyboard.json)")
    asset_info = (job.get("asset_info") if isinstance(job, dict) else None) or _json(directory / "asset-info.json") or {}
    srt_path = directory / "subtitles.srt"
    return Inputs(directory, video, storyboard, _json(directory / "manifest.json"),
                  srt_path.read_text(encoding="utf-8") if srt_path.is_file() else None,
                  asset_info if isinstance(asset_info, dict) else {},
                  (directory / "voice.wav") if (directory / "voice.wav").is_file() else None,
                  (directory / "music.wav") if (directory / "music.wav").is_file() else None)


# -- exécution ------------------------------------------------------------------------------------------------
def summarize(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {level: sum(1 for f in findings if f["level"] == level) for level in LEVELS}


def run(target: Path, out_dir: Path | None = None, *, ffmpeg: str | None = None, ffprobe: str | None = None,
        clock: Any = time.time) -> dict[str, Any]:
    """Contrôle complet ; écrit ``qa.json`` et ``index.html`` dans ``out_dir`` (``<dossier>/qa`` par défaut)."""
    inputs = load_inputs(Path(target))
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("ffmpeg est introuvable : contrôle impossible")
    ffprobe = ffprobe or ffprobe_exe(ffmpeg)
    out_dir = Path(out_dir) if out_dir else inputs.directory / QA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    storyboard = inputs.storyboard
    canvas = storyboard["canvas"]
    fps = canvas["fps"]
    started = time.monotonic()

    info = probe(ffmpeg, ffprobe, inputs.video)
    video = info.get("video") or {}
    duration = info.get("duration")
    cues = storyboard_cues(storyboard)
    findings = technical_checks(info, storyboard, inputs.manifest, _sha256(inputs.video))
    findings += image_checks(image_scan(ffmpeg, inputs.video), storyboard, video.get("width") or 0,
                             video.get("height") or 0)
    findings += layout_checks(storyboard, inputs.asset_info)
    findings += srt_checks(storyboard, inputs.srt)
    mix = audio_levels(ffmpeg, inputs.video) if info.get("audio") else None
    voice = audio_levels(ffmpeg, inputs.voice) if inputs.voice else None
    findings += audio_checks(mix, voice, cues, duration)
    has_music = inputs.music is not None or bool(((inputs.manifest or {}).get("audio") or {}).get("music"))
    findings += music_checks(cues, duration, has_music)
    if inputs.voice and duration:
        voice_info = probe(ffmpeg, ffprobe, inputs.voice)
        voice_duration = (voice_info.get("audio") or {}).get("duration") or voice_info.get("duration")
        if voice_duration and voice_duration > duration + VOICE_CUT_TOLERANCE:
            findings.append(finding("voice_cut", "error", f"La voix dure {voice_duration:.2f} s pour {duration:.2f} s "
                                    "de vidéo : la fin de la narration est coupée."))

    scenes, offset = [], 0
    for index, scene in enumerate(storyboard["scenes"], 1):
        shots = []
        for label, frame in sample_frames(scene):
            seconds = (offset + frame + 0.5) / fps
            image, stats = capture(ffmpeg, inputs.video, seconds)
            if stats and stats["mean"] < DARK_MEAN:
                findings.append(finding("dark_frame", "warning", f"Scène {index} ({label}, {seconds:.1f} s) : image "
                                        "presque noire.", index, seconds, **stats))
            elif stats and stats["stddev"] < EMPTY_STDDEV:
                findings.append(finding("empty_frame", "warning", f"Scène {index} ({label}, {seconds:.1f} s) : image "
                                        "uniforme, rien n'est dessiné ?", index, seconds, **stats))
            shots.append({"label": label, "frame": frame, "seconds": round(seconds, 2), "stats": stats,
                          "jpeg": base64.b64encode(image).decode() if image else ""})
        scenes.append({"index": index, "id": scene.get("id", ""), "intent": scene.get("intent") or {},
                       "start": round(offset / fps, 2), "end": round((offset + scene["duration_frames"]) / fps, 2),
                       "cues": [c for c in cues if c["scene"] == index], "captures": shots})
        offset += scene["duration_frames"]

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (f.get("scene") or 0, order[f["level"]]))
    report = {
        "schema": QA_SCHEMA, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(clock())),
        "video": os.path.relpath(inputs.video, out_dir), "probe": info, "summary": summarize(findings),
        "findings": findings, "duration_ms": int((time.monotonic() - started) * 1000),
        "inputs": {"manifest": inputs.manifest is not None, "subtitles": inputs.srt is not None,
                   "voice": inputs.voice is not None, "music": inputs.music is not None,
                   "asset_geometry": bool(inputs.asset_info)},
    }
    (out_dir / "qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "index.html").write_text(render_html(report, scenes, storyboard), encoding="utf-8")
    return report


# -- rapport HTML ---------------------------------------------------------------------------------------------
_LEVEL_LABEL = {"error": "Erreur", "warning": "À vérifier", "info": "Info"}
_CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;margin:24px;color:#1e2a44;background:#f6f8fb}
h1{margin:0 0 4px}small,.muted{color:#5b6475}.box{background:#fff;border-radius:12px;padding:16px;margin:16px 0;
box-shadow:0 1px 3px #0002}.counts span{display:inline-block;margin-right:12px;padding:4px 10px;border-radius:12px}
.error{background:#fde2e1;color:#8a1c14}.warning{background:#fff1cc;color:#7a5200}.info{background:#e2ecfb;color:#1d3f7a}
.ok{background:#dcf5e3;color:#14532d}ul{padding-left:18px}li{margin:4px 0}.shots{display:flex;gap:12px;flex-wrap:wrap}
figure{margin:0;text-align:center}figure img{width:216px;border-radius:8px;border:1px solid #ccd3df}
figcaption{font-size:13px;color:#5b6475}table{border-collapse:collapse}td{padding:2px 8px;vertical-align:top}
.tag{font-size:12px;padding:1px 8px;border-radius:10px;margin-right:6px}video{max-height:420px;border-radius:8px}
"""


def _items(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return '<p><span class="tag ok">OK</span>Aucun constat.</p>'
    rows = []
    for f in findings:
        at = f" <small>({f['at']:.1f} s)</small>" if f.get("at") is not None else ""
        rows.append(f'<li><span class="tag {f["level"]}">{_LEVEL_LABEL[f["level"]]}</span>'
                    f"{html.escape(f['message'])}{at}</li>")
    return "<ul>" + "".join(rows) + "</ul>"


def render_html(report: dict[str, Any], scenes: list[dict[str, Any]], storyboard: dict[str, Any]) -> str:
    esc = html.escape
    counts = report["summary"]
    link = esc(report["video"].replace(os.sep, "/"))
    general = [f for f in report["findings"] if not f.get("scene")]
    parts = [
        "<!doctype html><html lang=fr><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        f"<title>Contrôle qualité vidéo — {esc(Path(report['video']).name)}</title><style>{_CSS}</style></head><body>",
        "<h1>Contrôle qualité automatique</h1>",
        f"<p class=muted>Vérifié le {esc(report['checked_at'])} · vidéo <a href=\"{link}\">{esc(Path(report['video']).name)}</a>"
        f" · {storyboard['canvas']['width']}×{storyboard['canvas']['height']}, {storyboard['canvas']['fps']} i/s</p>",
        '<div class="box counts">'
        f'<span class="error">{counts["error"]} erreur(s)</span><span class="warning">{counts["warning"]} à vérifier'
        f'</span><span class="info">{counts["info"]} info(s)</span>'
        "<p class=muted>Contrôle <b>technique</b> et <b>estimé</b> (mesures ffmpeg, mise en page calculée depuis le "
        "storyboard) : il signale ce qu'il faut regarder. Il ne garantit <b>pas</b> la justesse pédagogique ni la "
        "qualité du dessin : un relecteur humain reste nécessaire.</p></div>",
        f'<div class=box><video controls preload=metadata src="{link}"></video>'
        f"<h2>Constats généraux</h2>{_items(general)}</div>",
    ]
    for scene in scenes:
        own = [f for f in report["findings"] if f.get("scene") == scene["index"]]
        intent = scene["intent"]
        shots = "".join(
            "<figure>" + (f'<img alt="capture" src="data:image/jpeg;base64,{shot["jpeg"]}">' if shot["jpeg"]
                          else "<p>(capture impossible)</p>")
            + f"<figcaption>{esc(shot['label'])} — {shot['seconds']:.2f} s</figcaption></figure>"
            for shot in scene["captures"])
        lines = "".join(f"<tr><td><small>{c['start']:.2f}–{c['end']:.2f} s</small></td><td>{esc(c['text'])}</td></tr>"
                        for c in scene["cues"]) or "<tr><td colspan=2 class=muted>(aucune réplique)</td></tr>"
        purpose = f" — {esc(str(intent.get('purpose')))}" if intent.get("purpose") else ""
        parts.append(
            f"<div class=box><h2>Scène {scene['index']} « {esc(str(scene['id']))} »{purpose}</h2>"
            f"<p class=muted>{scene['start']:.2f} s → {scene['end']:.2f} s</p><div class=shots>{shots}</div>"
            f"<h3>Répliques</h3><table>{lines}</table><h3>Constats</h3>{_items(own)}</div>")
    parts.append("<p class=muted>Rapport généré localement par Lody (lody.video-qa/1) : aucun appel externe.</p>"
                 "</body></html>")
    return "\n".join(parts)
