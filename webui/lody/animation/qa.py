"""Contrôle qualité automatique du rendu 2D (#121) : ce qu'un relecteur repérerait en regardant la vidéo.

Deux familles de contrôles, tous **locaux et gratuits** (ffmpeg + lecture du storyboard, aucun appel externe) :

- **Sur le MP4 rendu** (``run``) : écran noir, durée de la vidéo et de chaque scène, durée de la bande son
  (voix + musique) face à la vidéo, voix plus longue que la vidéo (coupée à la fin).
- **Sur le storyboard** (``static_checks``, utilisable AVANT tout rendu) : sous-titres trop longs pour la zone
  (coupés), répliques trop rapides ou trop brèves, texte qui dépasse du cadre, continuité entre deux scènes
  (version de marionnette différente, saut de position à une coupe franche), synchro labiale sans voix.

Les mesures de texte sont des **estimations** (largeur moyenne d'un caractère gras ≈ 0,6 × la taille de police,
mêmes styles que le moteur Remotion) : un avertissement invite à vérifier, il ne bloque jamais la vidéo.

Niveaux : ``ok``, ``info`` (à savoir), ``warning`` (à vérifier), ``error`` (défaut visible certain : écran noir,
voix coupée, durée fausse). Aucun contrôle ne fait échouer un rendu : le résultat est affiché et tracé.
"""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any

QA_SCHEMA = "lody.animation-qa/1"
LEVELS = ("ok", "info", "warning", "error")
BLACK_MIN_SECONDS = 0.5          # écran noir continu signalé au-delà
BLACK_PIXEL_THRESHOLD = 0.10     # luminance d'un pixel « noir » (le carton de repli, gris foncé, n'en est pas)
DURATION_TOLERANCE = 0.12        # s : marge de l'encodage (une image à 24 fps ≈ 0,042 s)
VOICE_CUT_TOLERANCE = 0.25       # s de voix au-delà de la fin de la vidéo
MAX_CHARS_PER_SECOND = 20.0      # au-delà, une réplique n'est pas lisible en sous-titre
MIN_CUE_SECONDS = 0.5
CUT_JUMP_RATIO = 0.25            # saut de position > 25 % de la largeur à une coupe franche
CHAR_WIDTH = 0.6                 # largeur moyenne d'un caractère gras / taille de police
# Styles de texte du moteur Remotion (renderers/remotion/src/LodyScene.jsx) : taille, marges horizontale/verticale.
TEXT_STYLES = {"title": (88, 10, 10), "subtitle": (54, 30, 14), "caption": (50, 26, 12), "label": (40, 25, 13)}

_BLACK = re.compile(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)")
_TIME = re.compile(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_AUDIO_STREAM = re.compile(r"Stream #\d+:\d+(?:\[[^\]]*\])?(?:\([^)]*\))?: Audio:")


def check(check_id: str, level: str, message: str, scene: int | None = None, **data: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"id": check_id, "level": level, "message": message}
    if scene is not None:
        item["scene"] = scene
    if data:
        item["data"] = data
    return item


# -- mesures ffmpeg ----------------------------------------------------------------------------------------------
def _ffmpeg(ffmpeg: str, arguments: list[str], timeout: float = 600) -> str:
    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-nostats", *arguments], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stderr or ""


def _hms(match: re.Match | None) -> float | None:
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def duration_of(ffmpeg: str, path: Path) -> float | None:
    return _hms(_DURATION.search(_ffmpeg(ffmpeg, ["-i", str(path)], 60)))


def has_audio(ffmpeg: str, path: Path) -> bool:
    return bool(_AUDIO_STREAM.search(_ffmpeg(ffmpeg, ["-i", str(path)], 60)))


def audio_stream_duration(ffmpeg: str, path: Path) -> float | None:
    """Durée réellement décodée de la piste audio (et non celle du conteneur)."""
    log = _ffmpeg(ffmpeg, ["-progress", "pipe:2", "-i", str(path), "-map", "0:a:0", "-f", "null", "-"])
    found = _TIME.findall(log)
    if not found:
        return None
    hours, minutes, seconds = found[-1]
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def black_intervals(ffmpeg: str, path: Path) -> list[tuple[float, float]]:
    log = _ffmpeg(ffmpeg, ["-i", str(path), "-an", "-vf",
                           f"blackdetect=d={BLACK_MIN_SECONDS}:pix_th={BLACK_PIXEL_THRESHOLD}", "-f", "null", "-"])
    return [(float(a), float(b)) for a, b in _BLACK.findall(log)]


def scene_at(storyboard: dict[str, Any], seconds: float) -> int:
    fps = storyboard["canvas"]["fps"]
    offset = 0
    for index, scene in enumerate(storyboard["scenes"], 1):
        offset += scene["duration_frames"]
        if seconds < offset / fps:
            return index
    return len(storyboard["scenes"])


# -- contrôles du storyboard (avant rendu) ---------------------------------------------------------------------
def wrap_lines(text: str, font_size: float, available: float) -> int:
    """Nombre de lignes après retour à la ligne automatique (estimation par largeur moyenne de caractère)."""
    per_line = max(1, int(available // (font_size * CHAR_WIDTH)))
    lines, current = 1, 0
    for word in text.split():
        size = len(word)
        if current == 0:
            current = size
        elif current + 1 + size <= per_line:
            current += 1 + size
        else:
            lines += 1
            current = size
        while current > per_line:  # mot plus long qu'une ligne : coupé
            lines += 1
            current -= per_line
    return lines


def _subtitle_checks(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    canvas = storyboard["canvas"]
    width, height, fps = canvas["width"], canvas["height"], canvas["fps"]
    font = round(width * 0.056)                       # Captions du moteur : 5,6 % de la largeur
    available = width * 0.88 - 56                     # 6 % de marge de chaque côté, 28 px de remplissage
    max_lines = max(1, int((height * 0.20 - 24) // (font * 1.25)))  # zone basse : 20 % de la hauteur
    found = []
    for index, scene in enumerate(storyboard["scenes"], 1):
        transition = scene["render"].get("transition_out") or {}
        fade_from = scene["duration_frames"] - int(transition.get("frames") or 0)
        for cue in (scene["render"].get("speech") or {}).get("cues") or []:
            seconds = (cue["end"] - cue["start"]) / fps
            text = cue["text"].strip()
            short = text if len(text) <= 40 else text[:39] + "…"
            lines = wrap_lines(text, font, available)
            if lines > max_lines:
                found.append(check("subtitle_cut", "warning", f"Scène {index} : sous-titre « {short} » trop long "
                                   f"({lines} lignes estimées, {max_lines} au plus) : il sera coupé en bas de "
                                   "l'image.", index, lines=lines, max_lines=max_lines))
            if seconds < MIN_CUE_SECONDS:
                found.append(check("subtitle_short", "warning", f"Scène {index} : sous-titre « {short} » affiché "
                                   f"{seconds:.2f} s seulement : illisible.", index, seconds=round(seconds, 3)))
            elif len(text) / seconds > MAX_CHARS_PER_SECOND:
                found.append(check("subtitle_fast", "warning", f"Scène {index} : sous-titre « {short} » trop rapide "
                                   f"({len(text) / seconds:.0f} caractères/s, {MAX_CHARS_PER_SECOND:.0f} au plus).",
                                   index, chars_per_second=round(len(text) / seconds, 1)))
            if transition.get("type") not in (None, "cut") and cue["end"] > fade_from:
                found.append(check("subtitle_in_transition", "info", f"Scène {index} : le sous-titre « {short} » "
                                   "se termine pendant la transition de sortie.", index))
    return found


def _max_value(keyframes: list[dict[str, Any]] | None, default: float) -> float:
    return max((float(k["value"]) for k in keyframes or []), default=default)


def _text_checks(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    canvas = storyboard["canvas"]
    width, height = canvas["width"], canvas["height"]
    found = []
    for index, scene in enumerate(storyboard["scenes"], 1):
        for layer in scene["render"]["layers"]:
            for obj in layer["objects"]:
                if obj["type"] != "text":
                    continue
                font, pad_x, pad_y = TEXT_STYLES.get(obj["text"]["style"], (50, 26, 12))
                scale = _max_value(obj.get("scale"), 1.0)
                box_w = (len(obj["text"]["content"]) * font * CHAR_WIDTH + 2 * pad_x) * scale
                box_h = (font * 1.2 + 2 * pad_y) * scale
                content = obj["text"]["content"]
                short = content if len(content) <= 30 else content[:29] + "…"
                for key in obj.get("position") or []:
                    x, y = float(key["x"]), float(key["y"])
                    if not (0 <= x <= width and 0 <= y <= height):
                        continue  # centre hors champ : entrée ou sortie de l'image, voulue
                    if x - box_w / 2 < 0 or x + box_w / 2 > width or y - box_h / 2 < 0 or y + box_h / 2 > height:
                        found.append(check("text_overflow", "warning", f"Scène {index} : le texte « {short} » "
                                           f"dépasse du cadre à l'image {key['frame']} (≈ {box_w:.0f} px de large "
                                           f"pour {width} px).", index, object=obj["id"], frame=key["frame"]))
                        break
    return found


def _asset_objects(scene: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {obj["id"]: obj for layer in scene["render"]["layers"] for obj in layer["objects"] if obj["type"] == "asset"}


def _continuity_checks(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    width = storyboard["canvas"]["width"]
    scenes = storyboard["scenes"]
    found = []
    versions: dict[str, set] = {}
    for scene in scenes:
        for obj in _asset_objects(scene).values():
            versions.setdefault(obj["asset"]["id"], set()).add(obj["asset"]["version"])
    for asset_id, seen in sorted(versions.items()):
        if len(seen) > 1:
            found.append(check("continuity_version", "warning", f"L'asset {asset_id} apparaît en plusieurs versions "
                               f"({', '.join(str(v) for v in sorted(seen, key=str))}) : son aspect changera d'une "
                               "scène à l'autre.", asset=asset_id))
    for index in range(1, len(scenes)):
        before, after = scenes[index - 1], scenes[index]
        out_type = (before["render"].get("transition_out") or {}).get("type", "cut")
        in_type = (after["render"].get("transition_in") or {}).get("type", "cut")
        if out_type != "cut" or in_type != "cut":
            continue
        left, right = _asset_objects(before), _asset_objects(after)
        for object_id in sorted(set(left) & set(right)):
            a, b = left[object_id].get("position") or [], right[object_id].get("position") or []
            if not a or not b or left[object_id]["asset"]["id"] != right[object_id]["asset"]["id"]:
                continue
            last, first = a[-1], b[0]
            jump = math.hypot(float(first["x"]) - float(last["x"]), float(first["y"]) - float(last["y"]))
            if jump > CUT_JUMP_RATIO * width:
                found.append(check("continuity_jump", "info", f"Raccord scène {index} → {index + 1} : « {object_id} » "
                                   f"saute de {jump:.0f} px à la coupe franche (voulu ?).", index + 1,
                                   object=object_id, jump_px=round(jump)))
    return found


def _sync_checks(storyboard: dict[str, Any], has_voice: bool) -> list[dict[str, Any]]:
    found = []
    for index, scene in enumerate(storyboard["scenes"], 1):
        lipsync = any(obj.get("lipsync") for obj in _asset_objects(scene).values())
        cues = (scene["render"].get("speech") or {}).get("cues") or []
        if lipsync and cues and not has_voice:
            found.append(check("sync_no_voice", "info", f"Scène {index} : synchro labiale sans voix enregistrée : la "
                               "bouche suit le texte seul.", index))
    return found


def static_checks(storyboard: dict[str, Any], has_voice: bool = True) -> list[dict[str, Any]]:
    """Contrôles du storyboard seul (gratuits, avant tout rendu)."""
    return (_subtitle_checks(storyboard) + _text_checks(storyboard) + _continuity_checks(storyboard)
            + _sync_checks(storyboard, has_voice))


# -- contrôles du MP4 ------------------------------------------------------------------------------------------
def media_checks(ffmpeg: str, directory: Path, storyboard: dict[str, Any],
                 traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    directory = Path(directory)
    video = directory / "out.mp4"
    fps = storyboard["canvas"]["fps"]
    expected = sum(s["duration_frames"] for s in storyboard["scenes"]) / fps
    found = []
    if not ffmpeg or not video.is_file():
        return [check("media_unchecked", "info", "Vidéo non contrôlée (ffmpeg ou fichier indisponible).")]
    actual = duration_of(ffmpeg, video)
    if actual is None:
        found.append(check("duration", "warning", "Durée de la vidéo illisible."))
    elif abs(actual - expected) > DURATION_TOLERANCE:
        found.append(check("duration", "error", f"Durée de la vidéo {actual:.2f} s au lieu de {expected:.2f} s.",
                           actual=actual, expected=round(expected, 3)))
    for trace in traces:
        real, wanted = trace.get("duration_seconds"), trace.get("expected_seconds")
        if real is not None and wanted is not None and abs(float(real) - float(wanted)) > DURATION_TOLERANCE:
            found.append(check("scene_duration", "warning", f"Scène {trace['index']} « {trace['id']} » : "
                               f"{float(real):.2f} s rendues pour {float(wanted):.2f} s prévues (décalage de la "
                               "suite).", trace["index"], actual=real, expected=wanted))
    for start, end in black_intervals(ffmpeg, video):
        index = scene_at(storyboard, start)
        found.append(check("black_screen", "error", f"Écran noir de {end - start:.1f} s à {start:.1f} s "
                           f"(scène {index}).", index, start=round(start, 3), end=round(end, 3)))
    voice = directory / "voice.wav"
    if voice.is_file():
        spoken = duration_of(ffmpeg, voice)
        if spoken is not None and spoken > expected + VOICE_CUT_TOLERANCE:
            found.append(check("voice_cut", "error", f"Voix coupée : elle dure {spoken:.2f} s pour une vidéo de "
                               f"{expected:.2f} s ({spoken - expected:.2f} s perdues à la fin).",
                               voice=spoken, expected=round(expected, 3)))
    if has_audio(ffmpeg, video):
        sound = audio_stream_duration(ffmpeg, video)
        if sound is not None and actual is not None and abs(sound - actual) > DURATION_TOLERANCE:
            found.append(check("audio_duration", "warning", f"Bande son de {sound:.2f} s pour une vidéo de "
                               f"{actual:.2f} s : la musique ou la voix ne finit pas avec l'image.",
                               audio=sound, video=actual))
    elif voice.is_file() or (directory / "music.wav").is_file():
        found.append(check("audio_missing", "error", "La voix ou la musique est fournie mais la vidéo est muette."))
    return found


def worst(items: list[dict[str, Any]]) -> str:
    return max((item["level"] for item in items), key=LEVELS.index, default="ok")


def run(ffmpeg: str, directory: Path, storyboard: dict[str, Any], traces: list[dict[str, Any]],
        frame_size: tuple[int, int] | None = None) -> dict[str, Any]:
    """Rapport complet ``lody.animation-qa/1`` (écrit dans ``qa.json`` par le worker)."""
    directory = Path(directory)
    items = media_checks(ffmpeg, directory, storyboard, traces) + static_checks(
        storyboard, has_voice=(directory / "voice.wav").is_file())
    level = worst(items)
    return {"schema": QA_SCHEMA, "status": "ok" if level == "info" else level,
            "frame_size": list(frame_size) if frame_size else None, "checks": items,
            "counts": {name: sum(1 for item in items if item["level"] == name) for name in LEVELS[1:]}}


def summary(report: dict[str, Any]) -> dict[str, Any]:
    return {"status": report.get("status", "ok"), "counts": report.get("counts", {})}


def warnings(report: dict[str, Any]) -> list[str]:
    """Messages à montrer à l'utilisateur (avertissements et erreurs seulement)."""
    return [f"Contrôle qualité : {item['message']}" for item in report.get("checks", [])
            if item["level"] in ("warning", "error")]
