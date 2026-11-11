"""Musique instrumentale de TEST V2 (#160), générée : aucune piste tierce, donc aucune question de licence.

Retour du propriétaire sur la V1 (``musique_test.py``) : niveau correct, musique peu convaincante. Cette V2 est
plus rythmée et plus « tech » : 116 BPM, la mineur (la m – fa – do – sol), grosse caisse sur chaque temps,
claquement sur 2 et 4, charleston en croches, basse syncopée et accords plaqués courts, introduction allégée
(2 mesures sans batterie complète) et fondu de sortie. Déterministe (bruit à graine fixe) : même fichier à
chaque exécution. Interface identique à ``musique_test`` (``write(path, seconds)``) ; le worker coupe la
musique à la durée exacte de la vidéo : elle ne peut jamais l'allonger.

Ce n'est PAS une musique de production : une piste sous licence (libre ou payante) reste à choisir.

Usage : ``python scripts/animation/musique_v2.py sortie.wav [durée_s]``
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000
BPM = 116
CHORDS = [  # (basse en Hz, notes de l'accord)
    (55.00, [220.00, 261.63, 329.63]),   # la mineur
    (43.65, [174.61, 220.00, 261.63]),   # fa
    (65.41, [196.00, 261.63, 329.63]),   # do
    (49.00, [196.00, 246.94, 293.66]),   # sol
]
BASS_STEPS = (0, 3, 4, 6)  # double-croches (sur 8 croches) où la basse joue : rythme syncopé
INTRO_BARS = 2


def _env(n: int, attack: float, release: float) -> np.ndarray:
    t = np.arange(n) / SAMPLE_RATE
    return np.minimum(1.0, t / attack) * np.exp(-t / release)


def _add(out: np.ndarray, start: int, sound: np.ndarray) -> None:
    n = min(len(sound), len(out) - start)
    if n > 0 and start >= 0:
        out[start:start + n] += sound[:n]


def _kick() -> np.ndarray:
    n = int(0.28 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    freq = 45 + 90 * np.exp(-t / 0.03)  # balayage de hauteur descendant
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    return 0.9 * np.sin(phase) * _env(n, 0.002, 0.11)


def _clap(rng: np.random.Generator) -> np.ndarray:
    n = int(0.2 * SAMPLE_RATE)
    noise = rng.standard_normal(n)
    noise = np.diff(noise, prepend=0.0)  # bruit éclairci (passe-haut grossier)
    return 0.22 * noise * _env(n, 0.001, 0.05)


def _hat(rng: np.random.Generator, open_: bool) -> np.ndarray:
    n = int((0.12 if open_ else 0.04) * SAMPLE_RATE)
    noise = np.diff(np.diff(rng.standard_normal(n), prepend=0.0), prepend=0.0)
    return 0.05 * noise * _env(n, 0.001, 0.05 if open_ else 0.012)


def _bass(freq: float, seconds: float) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    wave_ = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) + 0.12 * np.sin(6 * np.pi * freq * t)
    return 0.22 * wave_ * _env(n, 0.005, 0.16)


def _stab(notes: list[float], seconds: float) -> np.ndarray:
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    chord = sum(np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * 2.003 * f * t) for f in notes)
    return 0.06 * chord * _env(n, 0.004, 0.09)


def render(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(160)
    beat = 60.0 / BPM
    eighth = beat / 2
    bar = 4 * beat
    total = int(seconds * SAMPLE_RATE)
    out = np.zeros(total)
    kick, clap = _kick(), _clap(rng)
    hats = [_hat(rng, False), _hat(rng, True)]
    position, index = 0.0, 0
    while position < seconds:
        bass, notes = CHORDS[index % len(CHORDS)]
        start = int(position * SAMPLE_RATE)
        full = index >= INTRO_BARS
        for b in range(4):
            s = start + int(b * beat * SAMPLE_RATE)
            if full or b == 0:
                _add(out, s, kick)
            if full and b in (1, 3):
                _add(out, s, clap)
        for e in range(8):
            s = start + int(e * eighth * SAMPLE_RATE)
            _add(out, s, hats[1] if e % 4 == 3 else hats[0])
            if e in BASS_STEPS and full:
                _add(out, s, _bass(bass * 2, eighth * 0.9))
            if e in (0, 3, 6):  # accords plaqués syncopés (3 + 3 + 2)
                _add(out, s, _stab(notes, eighth * 1.2))
        position += bar
        index += 1
    fade = min(total, int(1.5 * SAMPLE_RATE))
    if fade:
        out[-fade:] *= np.linspace(1.0, 0.0, fade)
    out /= max(1e-9, float(np.max(np.abs(out))))
    return 0.6 * out


def write(path: Path, seconds: float) -> None:
    data = (render(seconds) * 32767).astype("<i2")
    stereo = np.repeat(data[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(stereo.tobytes())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    write(Path(sys.argv[1]), float(sys.argv[2]) if len(sys.argv) > 2 else 24.0)
