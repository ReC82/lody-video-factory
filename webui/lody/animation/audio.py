"""Bande son du rendu 2D (#150) : voix off + musique de fond, mixées par le worker avant l'assemblage.

Fichiers facultatifs du dossier de tâche (déposés par le fournisseur, jamais téléchargés par le worker) :

    voice.wav    voix seule (sert aussi à la synchro labiale du moteur de scène)
    music.wav    musique instrumentale de fond
    audio.json   provenance déclarée : {"voice": {...}, "music": {...}} (source, licence, « provisoire »)
    audio.wav    mixage produit ici (ou fourni tel quel s'il n'y a ni voix ni musique séparées)

Règles du mixage : la voix garde son niveau ; la musique est ramenée à environ 20 % du niveau moyen de la
voix (−14 dB), baisse encore pendant la parole (ducking par compression en chaîne latérale), démarre en
fondu et s'éteint en fondu pour finir **exactement** à la durée de la vidéo (bouclée si elle est plus
courte, coupée si elle est plus longue). Aucun appel réseau, aucun coût.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

VOICE_FILE = "voice.wav"
MUSIC_FILE = "music.wav"
MIX_FILE = "audio.wav"
INFO_FILE = "audio.json"
MUSIC_LEVEL = 0.2          # musique ≈ 20 % du niveau narratif (amplitude), soit −14 dB
FADE_IN = 0.6
FADE_OUT = 1.5
SAMPLE_RATE = 48000
_MEAN = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB")


class SoundtrackError(Exception):
    pass


def mean_volume(ffmpeg: str, path: Path) -> float | None:
    """Niveau moyen (dBFS) mesuré par ffmpeg ``volumedetect``, ou ``None``."""
    try:
        done = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect",
                               "-f", "null", "-"], capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    match = _MEAN.search(done.stderr or "")
    if not match or float(match.group(1)) <= -90:
        return None
    return float(match.group(1))


def music_gain_db(voice_db: float | None, music_db: float | None, level: float = MUSIC_LEVEL) -> float:
    """Gain à appliquer à la musique pour qu'elle soit à ``level`` du niveau moyen de la voix."""
    target_offset = 20 * math.log10(level)
    if music_db is None:
        return 0.0
    if voice_db is None:  # musique seule : niveau de fond raisonnable
        return round(-20.0 - music_db, 2)
    return round(voice_db + target_offset - music_db, 2)


def mix_command(ffmpeg: str, seconds: float, voice: Path | None, music: Path | None, output: Path,
                gain_db: float = 0.0) -> list[str]:
    """Commande ffmpeg du mixage final (PCM 48 kHz stéréo), durée exacte ``seconds``."""
    if voice is None and music is None:
        raise SoundtrackError("ni voix ni musique")
    duration = f"{seconds:.3f}"
    fade_out_start = max(0.0, seconds - FADE_OUT)
    norm = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
    inputs: list[str] = []
    filters: list[str] = []
    if voice is not None:
        inputs += ["-i", str(voice)]
    if music is not None:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
    music_index = 1 if voice is not None else 0
    if music is not None:
        filters.append(
            f"[{music_index}:a]{norm},atrim=0:{duration},asetpts=N/SR/TB,volume={gain_db}dB,"
            f"afade=t=in:st=0:d={FADE_IN},afade=t=out:st={fade_out_start:.3f}:d={FADE_OUT}[m]")
    if voice is not None:
        filters.append(f"[0:a]{norm},apad,atrim=0:{duration},asetpts=N/SR/TB"
                       + ("[v]" if music is None else ",asplit=2[v][key]"))
    if voice is not None and music is not None:
        filters.append("[m][key]sidechaincompress=threshold=0.02:ratio=4:attack=15:release=400:makeup=1[md]")
        filters.append("[v][md]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[out]")
    elif voice is not None:
        filters.append("[v]alimiter=limit=0.95[out]")
    else:
        filters.append("[m]anull[out]")
    return [ffmpeg, "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters), "-map", "[out]",
            "-t", duration, "-ar", str(SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s16le", str(output)]


def prepare_soundtrack(ffmpeg: str, directory: Path, seconds: float) -> str | None:
    """Construit ``audio.wav`` depuis ``voice.wav`` et/ou ``music.wav``. Renvoie un résumé, ou ``None``."""
    directory = Path(directory)
    voice = directory / VOICE_FILE
    music = directory / MUSIC_FILE
    voice_path = voice if voice.is_file() else None
    music_path = music if music.is_file() else None
    if voice_path is None and music_path is None:
        return None  # rien à mixer : un audio.wav déposé tel quel reste utilisé
    gain = music_gain_db(mean_volume(ffmpeg, voice_path) if voice_path else None,
                         mean_volume(ffmpeg, music_path)) if music_path else 0.0
    target = directory / MIX_FILE
    temporary = directory / f".{MIX_FILE}.tmp.wav"
    done = subprocess.run(mix_command(ffmpeg, seconds, voice_path, music_path, temporary, gain),
                          capture_output=True, timeout=300, check=False)
    if done.returncode != 0 or not temporary.is_file():
        temporary.unlink(missing_ok=True)
        raise SoundtrackError("mixage audio en échec")
    temporary.replace(target)
    parts = (["voix"] if voice_path else []) + ([f"musique {gain:+.1f} dB avec ducking"] if music_path else [])
    return " + ".join(parts) + f", {seconds:.3f} s"


def describe(directory: Path) -> dict[str, Any]:
    """Provenance de la bande son pour le manifeste : ce qui a été mixé et ce qui est déclaré provisoire."""
    directory = Path(directory)
    try:
        info = json.loads((directory / INFO_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    info = info if isinstance(info, dict) else {}

    def part(name: str, filename: str) -> dict[str, Any] | None:
        if not (directory / filename).is_file():
            return None
        declared = info.get(name) if isinstance(info.get(name), dict) else {}
        return {"provisional": bool(declared.get("provisional", True)),
                **{k: str(v)[:200] for k, v in declared.items() if k in ("source", "license", "voice", "note")}}

    return {"voice": part("voice", VOICE_FILE), "music": part("music", MUSIC_FILE),
            "music_level": MUSIC_LEVEL if (directory / MUSIC_FILE).is_file() else None,
            "mixed": (directory / MIX_FILE).is_file()}


def voice_plan(character: Any) -> dict[str, Any]:
    """Voix à utiliser pour un personnage, SANS aucun appel : celle de sa fiche si elle a été choisie.

    - Fiche avec fournisseur ElevenLabs et ``external_voice_id`` : cette voix-là, jamais une autre ; la
      synthèse est payante, donc ``needs_cost_approval`` (estimer le coût et obtenir l'accord avant).
    - Sinon : voix provisoire à signaler comme telle ; aucun identifiant n'est inventé.
    """
    provider = str(getattr(character, "voice_provider", "") or "").strip().lower()
    voice_id = str(getattr(character, "external_voice_id", "") or "").strip()
    name = str(getattr(character, "name", "") or "ce personnage")
    if provider == "elevenlabs" and voice_id:
        return {"status": "character_voice", "provider": "elevenlabs", "voice_id": voice_id,
                "voice_name": str(getattr(character, "voice_name", "") or ""), "needs_cost_approval": True,
                "message": f"Voix ElevenLabs de la fiche {name} : synthèse payante, accord de coût requis."}
    return {"status": "provisional", "provider": "", "voice_id": "", "needs_cost_approval": False,
            "message": f"Aucune voix choisie dans la fiche {name} : voix de test provisoire, à remplacer."}
