"""Voix off ElevenLabs du rendu 2D (#164) : voix de la fiche → texte oral → synthèse en cache → piste calée.

Chaîne complète, scène par scène (seules les scènes PARLÉES, c'est-à-dire portant ``render.speech.cues``) :

1. **Voix choisie par personnage** — ``speech.speaker`` désigne un objet asset de la scène ; l'asset est relié à
   une fiche personnage (``character_id``) ; la fiche porte la voix (``voice_provider = elevenlabs``,
   ``external_voice_id``) et ses réglages de jeu (``voice_direction``, #92). C'est le contrat déjà utilisé par
   le casting de voix (#155) : aucun second sélecteur, aucun identifiant inventé.
2. **Texte oral enrichi** — chaque repère est passé au lexique de prononciation figé de la production (#163,
   ``lody.pronunciation.render_snapshot``) : la forme parlée part à ElevenLabs, la forme écrite reste à l'écran.
   Tant que #163 n'est pas fusionné, l'adaptateur ``render_text`` laisse le texte inchangé.
3. **Synthèse en cache** — un appel par scène (texte des repères joints : intonation continue), uniquement si
   l'empreinte voix/réglages/texte est absente du cache (même formule et même dossier que le cache des extraits
   de #155) : une reprise, un second rendu ou la réédition d'une autre scène ne repaie rien. L'audio reçu est
   vérifié (décodable, non vide) AVANT d'entrer dans le cache.
4. **Piste et timing** — chaque scène est décodée (48 kHz mono), sa durée exacte et sa plage de parole
   (silences de début et de fin détectés par ffmpeg) sont mesurées, puis la voix est posée au début du premier
   repère prévu (avancée si nécessaire pour tenir dans la scène). Une voix plus longue que sa scène est
   **refusée** avec un message clair : jamais tronquée, jamais accélérée en douce. Les repères sont recalés sur
   la parole réelle (répartition par longueur du texte parlé) : ``voice-timing.json`` sert à la bouche du
   personnage (visèmes calculés sur le texte PARLÉ) et aux sous-titres (texte ÉDITORIAL).

Échec = échec : aucune bascule vers une autre voix (Piper ou autre), aucune vidéo muette en douce.

Ce module ne lit aucune clé : l'appel payant est une fonction injectée (en production
``MoneyPrinterTurboConnector.generate_voice_preview``, qui passe par le moteur, seul détenteur de la clé).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROVIDER = "elevenlabs"
DEFAULT_MODEL = "eleven_multilingual_v2"
FINGERPRINT_VERSION = 1          # même empreinte que ``voice_casting.fingerprint`` (#155) : cache partagé
TIMING_FILE = "voice-timing.json"
TIMING_SCHEMA = "lody.voice-timing/1"
SAMPLE_RATE = 48000
OVERFLOW_TOLERANCE = 0.05        # s : arrondi de décodage toléré au-delà de la durée de la scène
SILENCE_NOISE = "-40dB"
SILENCE_MIN = 0.08
_SILENCE_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")
#: Réglages acceptés par l'endpoint moteur ``/api/v1/voice_preview`` (voir ``VoicePreviewRequest``).
MAX_SCENE_TEXT = 2000          # limite de ``VoicePreviewRequest.text`` côté moteur
ENGINE_SETTINGS = ("model_id", "stability", "similarity_boost", "style", "use_speaker_boost", "speed")

Synthesize = Callable[[str, str, dict[str, Any]], bytes]


class VoiceoverError(Exception):
    """Erreur lisible (aucun chemin système, aucun secret), rattachée à une scène si possible."""

    def __init__(self, message: str, scene: int | None = None):
        super().__init__(message)
        self.message = message
        self.scene = scene


# -- voix d'un personnage --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class VoiceSpec:
    voice_id: str
    settings: tuple[tuple[str, Any], ...]
    character: str
    voice_name: str = ""

    @property
    def settings_dict(self) -> dict[str, Any]:
        return dict(self.settings)


def voice_spec(character: Any) -> VoiceSpec | None:
    """Voix ElevenLabs enregistrée sur la fiche, ou ``None`` (aucune voix choisie : jamais d'identifiant
    inventé). Même lecture que ``voice_casting.character_voice_settings`` (#155)."""
    provider = str(getattr(character, "voice_provider", "") or "").strip().lower()
    voice_id = str(getattr(character, "external_voice_id", "") or "").strip()
    if provider != PROVIDER or not voice_id:
        return None
    direction = dict(getattr(character, "voice_direction", {}) or {})
    direction.setdefault("model_id", DEFAULT_MODEL)
    direction = {key: direction[key] for key in sorted(direction) if key in ENGINE_SETTINGS}
    return VoiceSpec(voice_id, tuple(direction.items()), str(getattr(character, "name", "") or "personnage"),
                     str(getattr(character, "voice_name", "") or ""))


def fingerprint(voice_id: str, settings: dict[str, Any], text: str, *, provider: str = PROVIDER) -> str:
    """Empreinte voix/modèle/réglages/texte — formule identique à celle du casting (#155)."""
    payload = {"v": FINGERPRINT_VERSION, "provider": provider, "voice_id": voice_id,
               "settings": {key: settings[key] for key in sorted(settings)}, "text": text}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# -- texte oral --------------------------------------------------------------------------------------------------
def render_text(text: str, lexicon: dict[str, Any] | None) -> tuple[str, str]:
    """(texte parlé, texte affiché) d'un repère selon le lexique figé de la production (#163).

    Sans lexique, ou tant que ``lody.pronunciation`` n'existe pas (PR #163 non fusionnée), le texte est
    inchangé des deux côtés."""
    clean = " ".join(str(text or "").split())
    if not lexicon or not lexicon.get("entries"):
        return clean, clean
    try:
        from lody.pronunciation import render_snapshot  # type: ignore[import-not-found]
    except ImportError:
        return clean, clean
    rendering = render_snapshot(clean, lexicon)
    return (" ".join(str(rendering.spoken or clean).split()), " ".join(str(rendering.subtitle or clean).split()))


# -- plan --------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class CueText:
    text: str      # texte éditorial (sous-titre)
    spoken: str    # texte envoyé à la voix


@dataclass(frozen=True)
class SceneVoice:
    index: int
    scene_id: str
    spec: VoiceSpec
    cues: tuple[CueText, ...]
    duration_frames: int
    first_cue_start: int

    @property
    def spoken(self) -> str:
        return " ".join(cue.spoken for cue in self.cues)

    @property
    def key(self) -> str:
        return fingerprint(self.spec.voice_id, self.spec.settings_dict, self.spoken)


@dataclass
class VoicePlan:
    scenes: list[SceneVoice] = field(default_factory=list)
    missing: dict[str, list[int]] = field(default_factory=dict)   # personnage sans voix → scènes concernées
    unlinked: list[int] = field(default_factory=list)             # scènes parlées sans fiche reliée (muettes)

    @property
    def characters(self) -> list[str]:
        return list(dict.fromkeys(scene.spec.character for scene in self.scenes))

    def get(self, index: int) -> SceneVoice | None:
        return next((scene for scene in self.scenes if scene.index == index), None)


#: Résolution d'une scène parlée : ``None`` = aucune fiche reliée au locuteur ; sinon l'objet fiche (ou une
#: chaîne : nom d'une fiche introuvable, traitée comme une voix manquante).
SpeakerResolver = Callable[[dict[str, Any]], Any]


def default_character_repo() -> Any:
    from lody import settings
    from lody.characters import CharacterRepository

    return CharacterRepository(settings.db_path())


def speaker_resolver(asset_repo: Any, character_repo: Any, project_id: str) -> SpeakerResolver:
    """Locuteur d'une scène (``speech.speaker``) → fiche reliée à son asset (``character_id``).

    ``None`` : pas de locuteur, ou asset sans fiche reliée (comportement historique : muet, annoncé). Une
    fiche reliée mais introuvable renvoie un libellé, traité comme une voix manquante (bloquant)."""
    from lody.animation.assets import AssetNotFound
    from lody.characters import CharacterNotFound

    def resolve(scene: dict[str, Any]) -> Any:
        render = scene.get("render") or {}
        speaker = (render.get("speech") or {}).get("speaker")
        if not speaker:
            return None
        for layer in render.get("layers") or []:
            for obj in layer.get("objects") or []:
                if obj.get("id") != speaker or obj.get("type") != "asset":
                    continue
                try:
                    asset = asset_repo.get(project_id, obj["asset"]["id"])
                except (AssetNotFound, KeyError, TypeError):
                    return None
                if not asset.character_id:
                    return None
                try:
                    return character_repo.get(project_id, asset.character_id)
                except CharacterNotFound:
                    return "personnage introuvable"
        return None

    return resolve


def plan_voiceover(storyboard: dict[str, Any], resolve: SpeakerResolver,
                   lexicon: dict[str, Any] | None = None) -> VoicePlan:
    """Scènes à faire parler, avec la voix de la fiche de leur locuteur. Aucun appel, aucun coût."""
    plan = VoicePlan()
    for index, scene in enumerate(storyboard.get("scenes") or [], 1):
        speech = (scene.get("render") or {}).get("speech") or {}
        cues = speech.get("cues") or []
        if not cues:
            continue
        character = resolve(scene)
        if character is None:
            plan.unlinked.append(index)
            continue
        spec = voice_spec(character) if not isinstance(character, str) else None
        if spec is None:
            name = character if isinstance(character, str) else str(getattr(character, "name", "") or "personnage")
            plan.missing.setdefault(name, []).append(index)
            continue
        texts = []
        for cue in cues:
            spoken, subtitle = render_text(cue.get("text", ""), lexicon)
            texts.append(CueText(text=subtitle, spoken=spoken))
        plan.scenes.append(SceneVoice(index, str(scene.get("id", "")), spec, tuple(texts), int(scene["duration_frames"]),
                                      int(cues[0].get("start", 0))))
    return plan


# -- cache -------------------------------------------------------------------------------------------------------
def default_cache_dir() -> Path:
    from lody import settings

    return settings.data_dir() / "voice-samples"  # même dossier que les extraits du casting (#155)


class VoiceCache:
    """Audio rangé par empreinte (``<racine>/<2 premiers>/<empreinte>.mp3``), écriture atomique, jamais purgé
    automatiquement (un audio payé reste réutilisable)."""

    def __init__(self, root: Path | None = None):
        self._root = Path(root) if root is not None else None

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else default_cache_dir()

    def path(self, key: str) -> Path:
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("empreinte invalide")
        return self.root / key[:2] / f"{key}.mp3"

    def has(self, key: str) -> bool:
        return self.path(key).is_file()

    def put(self, key: str, data: bytes) -> Path:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False, suffix=".tmp") as handle:
            handle.write(data)
            temporary = Path(handle.name)
        os.replace(temporary, target)
        return target


def billable_characters(plan: VoicePlan, cache: VoiceCache) -> int:
    """Caractères qui seraient facturés : texte parlé des scènes absentes du cache (une seule fois par texte)."""
    seen: set[str] = set()
    total = 0
    for scene in plan.scenes:
        if scene.key in seen or cache.has(scene.key):
            continue
        seen.add(scene.key)
        total += len(scene.spoken)
    return total


# -- audio -------------------------------------------------------------------------------------------------------
def decode(ffmpeg: str, source: Path, target: Path) -> float:
    """Décode en WAV PCM 16 bits 48 kHz mono ; renvoie la durée exacte (s). Lève ``VoiceoverError``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        done = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(source), "-ar", str(SAMPLE_RATE),
                               "-ac", "1", "-c:a", "pcm_s16le", str(target)], capture_output=True, timeout=120,
                              check=False)
    except (OSError, subprocess.SubprocessError):
        raise VoiceoverError("décodage audio impossible") from None
    if done.returncode != 0 or not target.is_file():
        raise VoiceoverError("audio ElevenLabs illisible")
    try:
        with wave.open(str(target), "rb") as handle:
            seconds = handle.getnframes() / float(handle.getframerate())
    except (OSError, wave.Error, EOFError, ZeroDivisionError):
        raise VoiceoverError("audio ElevenLabs illisible") from None
    if seconds <= 0.05:
        raise VoiceoverError("audio ElevenLabs vide")
    return seconds


def speech_span(ffmpeg: str, wav: Path, seconds: float) -> tuple[float, float]:
    """Début et fin de la parole (silences de tête et de queue retirés). Toute la durée si rien n'est détecté."""
    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(wav), "-af",
                               f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN}", "-f", "null", "-"],
                              capture_output=True, text=True, timeout=120, check=False)
        log = done.stderr or ""
    except (OSError, subprocess.SubprocessError):
        log = ""
    return parse_speech_span(log, seconds)


def parse_speech_span(log: str, seconds: float) -> tuple[float, float]:
    starts = [float(value) for value in _SILENCE_START.findall(log)]
    ends = [float(value) for value in _SILENCE_END.findall(log)]
    begin, finish = 0.0, seconds
    if starts and starts[0] <= 0.02 and ends:
        begin = min(ends[0], seconds)
    if starts and (len(ends) < len(starts) or ends[-1] >= seconds - 0.02) and starts[-1] > begin:
        finish = max(begin, starts[-1])
    if finish - begin < 0.05:  # tout « silence » selon le seuil : on garde la durée entière
        return 0.0, seconds
    return round(begin, 3), round(finish, 3)


def time_cues(scene: SceneVoice, fps: int, offset: float, span: tuple[float, float]) -> list[dict[str, Any]]:
    """Repères (en images, relatifs à la scène) répartis sur la parole réelle selon la longueur du texte parlé."""
    weights = [max(1, len(cue.spoken)) for cue in scene.cues]
    total = sum(weights)
    start_s, end_s = offset + span[0], offset + span[1]
    out: list[dict[str, Any]] = []
    cursor, previous_end = start_s, 0
    for cue, weight in zip(scene.cues, weights):
        length = (end_s - start_s) * weight / total
        first = max(previous_end, min(scene.duration_frames - 1, math.floor(cursor * fps)))
        last = max(first + 1, min(scene.duration_frames, math.ceil((cursor + length) * fps)))
        out.append({"start": first, "end": last, "text": cue.text, "spoken": cue.spoken})
        cursor += length
        previous_end = last
    return out


def _segment(ffmpeg: str, source: Path | None, delay: float, seconds: float, target: Path) -> None:
    if source is None:
        inputs = ["-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono"]
        audio_filter = "anull"
    else:
        inputs = ["-i", str(source)]
        audio_filter = f"adelay={int(round(delay * 1000))}:all=1,apad"
    done = subprocess.run([ffmpeg, "-y", "-loglevel", "error", *inputs, "-af", audio_filter, "-t", f"{seconds:.6f}",
                           "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(target)],
                          capture_output=True, timeout=120, check=False)
    if done.returncode != 0 or not target.is_file():
        raise VoiceoverError("préparation de la piste voix impossible")


# -- synthèse ----------------------------------------------------------------------------------------------------
@dataclass
class SynthesisOutcome:
    generated: list[int] = field(default_factory=list)
    reused: list[int] = field(default_factory=list)
    characters: int = 0


def synthesize_missing(plan: VoicePlan, cache: VoiceCache, synthesize: Synthesize, ffmpeg: str) -> SynthesisOutcome:
    """Synthétise les scènes absentes du cache, dans l'ordre ; s'arrête à la PREMIÈRE erreur (rien d'autre n'est
    payé pour une tâche vouée à l'échec ; ce qui a déjà été payé reste en cache pour la reprise)."""
    outcome = SynthesisOutcome()
    done: set[str] = set()
    for scene in plan.scenes:  # vérifié AVANT tout appel : rien n'est payé pour une tâche vouée à l'échec
        if len(scene.spoken) > MAX_SCENE_TEXT:
            raise VoiceoverError(f"la scène {scene.index} a {len(scene.spoken)} caractères à dire (maximum "
                                 f"{MAX_SCENE_TEXT}) : découpe-la", scene.index)
    for scene in plan.scenes:
        key = scene.key
        if key in done or cache.has(key):
            outcome.reused.append(scene.index)
            continue
        try:
            data = synthesize(scene.spoken, scene.spec.voice_id, scene.spec.settings_dict)
        except Exception as error:  # noqa: BLE001 — message du fournisseur, jamais une trace brute
            detail = str(getattr(error, "message", "") or "") or type(error).__name__
            raise VoiceoverError(f"ElevenLabs a refusé la voix de la scène {scene.index} ({detail})",
                                 scene.index) from None
        if not data:
            raise VoiceoverError(f"ElevenLabs n'a renvoyé aucun audio pour la scène {scene.index}", scene.index)
        with tempfile.TemporaryDirectory(prefix="lody-voice-") as temp:
            probe = Path(temp) / "probe.mp3"
            probe.write_bytes(data)
            try:
                decode(ffmpeg, probe, Path(temp) / "probe.wav")
            except VoiceoverError as error:
                raise VoiceoverError(f"{error.message} (scène {scene.index})", scene.index) from None
        cache.put(key, data)
        done.add(key)
        outcome.generated.append(scene.index)
        outcome.characters += len(scene.spoken)
    return outcome


# -- piste finale ------------------------------------------------------------------------------------------------
def build_track(plan: VoicePlan, cache: VoiceCache, ffmpeg: str, directory: Path,
                storyboard: dict[str, Any]) -> dict[str, Any]:
    """Écrit ``voice.wav`` (durée exacte de la vidéo), ``voice-timing.json`` et la provenance dans ``audio.json``.
    Toutes les scènes parlées doivent être en cache. Renvoie le document de timing."""
    from lody.animation import audio

    directory = Path(directory)
    fps = int(storyboard["canvas"]["fps"])
    work = directory / "voice-scenes"
    work.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    timing: dict[str, Any] = {"schema": TIMING_SCHEMA, "fps": fps, "provider": PROVIDER, "scenes": {}}
    offset_frames = 0
    for index, scene in enumerate(storyboard["scenes"], 1):
        frames = int(scene["duration_frames"])
        seconds = frames / fps
        voiced = plan.get(index)
        segment = work / f"{index:02d}.segment.wav"
        if voiced is None:
            _segment(ffmpeg, None, 0.0, seconds, segment)
        else:
            source = cache.path(voiced.key)
            if not source.is_file():
                raise VoiceoverError(f"audio de la scène {index} absent du cache", index)
            decoded = work / f"{index:02d}.voice.wav"
            try:
                length = decode(ffmpeg, source, decoded)
            except VoiceoverError as error:
                raise VoiceoverError(f"{error.message} (scène {index})", index) from None
            if length > seconds + OVERFLOW_TOLERANCE:
                raise VoiceoverError(
                    f"la voix de la scène {index} « {voiced.scene_id} » dure {length:.2f} s pour une scène de "
                    f"{seconds:.2f} s : allonge la scène ou raccourcis la réplique (rien n'est tronqué)", index)
            delay = max(0.0, min(voiced.first_cue_start / fps, seconds - length))
            span = speech_span(ffmpeg, decoded, length)
            _segment(ffmpeg, decoded, delay, seconds, segment)
            timing["scenes"][str(index)] = {
                "scene_id": voiced.scene_id, "key": voiced.key, "character": voiced.spec.character,
                "voice_id": voiced.spec.voice_id, "start_frame": offset_frames, "delay_seconds": round(delay, 3),
                "audio_seconds": round(length, 3), "speech": [round(delay + span[0], 3), round(delay + span[1], 3)],
                "cues": time_cues(voiced, fps, delay, span),
            }
        segments.append(segment)
        offset_frames += frames
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{path.name}'\n" for path in segments), encoding="utf-8")
    temporary = directory / f".{audio.VOICE_FILE}.tmp.wav"
    done = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                           "-c:a", "pcm_s16le", str(temporary)], capture_output=True, timeout=300, check=False)
    if done.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise VoiceoverError("assemblage de la piste voix impossible")
    os.replace(temporary, directory / audio.VOICE_FILE)
    for path in work.iterdir():
        path.unlink(missing_ok=True)
    work.rmdir()
    _write_json(directory / TIMING_FILE, timing)
    info_path = directory / audio.INFO_FILE
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    info = info if isinstance(info, dict) else {}
    names = ", ".join(plan.characters)
    info["voice"] = {"source": "ElevenLabs", "provisional": False,
                     "voice": ", ".join(f"{s.spec.character} : {s.spec.voice_name or s.spec.voice_id}"
                                        for s in {s.spec.character: s for s in plan.scenes}.values())[:200],
                     "note": f"Voix des fiches ({names}), {len(plan.scenes)} scène(s) parlée(s)."[:200]}
    _write_json(info_path, info)
    return timing


def _write_json(path: Path, data: Any) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


# -- lecture par le worker ---------------------------------------------------------------------------------------
def read_timing(directory: Path) -> dict[str, Any]:
    """Repères recalés par scène (``{"<index>": {...}}``), ou ``{}`` (aucune voix ElevenLabs : repères du
    storyboard inchangés)."""
    try:
        data = json.loads((Path(directory) / TIMING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("schema") != TIMING_SCHEMA or not isinstance(data.get("scenes"), dict):
        return {}
    return data["scenes"]


def timed_cues(timing: dict[str, Any], index: int) -> list[dict[str, Any]] | None:
    entry = timing.get(str(index))
    cues = entry.get("cues") if isinstance(entry, dict) else None
    if not isinstance(cues, list) or not cues:
        return None
    clean = []
    for cue in cues:
        if not isinstance(cue, dict):
            return None
        try:
            clean.append({"start": int(cue["start"]), "end": int(cue["end"]), "text": str(cue["text"]),
                          "spoken": str(cue.get("spoken") or cue["text"])})
        except (KeyError, TypeError, ValueError):
            return None
    return clean


def scene_with_timing(scene: dict[str, Any], cues: Sequence[dict[str, Any]] | None) -> dict[str, Any]:
    """Copie de la scène dont les repères de parole sont ceux recalés sur la voix réelle (``spoken`` en plus)."""
    if not cues:
        return scene
    render = dict(scene["render"])
    render["speech"] = {**(render.get("speech") or {}), "cues": [dict(cue) for cue in cues]}
    return {**scene, "render": render}
