"""Musique instrumentale de TEST, générée (#150) : aucune piste tierce, donc aucune question de licence.

Boucle douce de 8 mesures à 100 BPM en do majeur (do – la mineur – fa – sol) : nappe d'accords filtrée,
arpège pincé discret et pulsation de basse. Déterministe (aucun hasard) : le même fichier à chaque exécution.
Ce n'est PAS une musique de production : elle sert à vérifier le mixage (niveau ~20 %, ducking, fin exacte).

Usage : ``python scripts/animation/musique_test.py sortie.wav [durée_s]``
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000
BPM = 100
CHORDS = [  # fréquences (Hz) : basse, puis notes de l'accord
    (65.41, [261.63, 329.63, 392.00]),   # do
    (55.00, [220.00, 261.63, 329.63]),   # la mineur
    (43.65, [174.61, 220.00, 261.63]),   # fa
    (49.00, [196.00, 246.94, 293.66]),   # sol
]


def _envelope(n: int, attack: float, release: float) -> np.ndarray:
    t = np.arange(n) / SAMPLE_RATE
    return np.minimum(1.0, t / attack) * np.exp(-t / release)


def render(seconds: float) -> np.ndarray:
    beat = 60.0 / BPM
    bar = 4 * beat
    total = int(seconds * SAMPLE_RATE)
    out = np.zeros(total)
    t_all = np.arange(total) / SAMPLE_RATE
    position = 0.0
    index = 0
    while position < seconds:
        bass, notes = CHORDS[index % len(CHORDS)]
        start = int(position * SAMPLE_RATE)
        length = min(total - start, int(bar * SAMPLE_RATE))
        t = t_all[start:start + length] - position
        # Nappe : sinus doux légèrement désaccordés, attaque et relâche lentes.
        pad = sum(np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * f * 1.003 * t) for f in notes)
        swell = np.minimum(1.0, t / 0.35) * np.minimum(1.0, (bar - t) / 0.35)
        out[start:start + length] += 0.05 * pad * swell
        # Basse : une note par temps, courte.
        for k in range(4):
            s = start + int(k * beat * SAMPLE_RATE)
            n = min(total - s, int(0.45 * SAMPLE_RATE))
            if n > 0:
                tt = np.arange(n) / SAMPLE_RATE
                out[s:s + n] += 0.16 * np.sin(2 * np.pi * bass * 2 * tt) * _envelope(n, 0.01, 0.18)
        # Arpège pincé en croches.
        for k in range(8):
            s = start + int(k * beat / 2 * SAMPLE_RATE)
            n = min(total - s, int(0.5 * SAMPLE_RATE))
            if n > 0:
                f = notes[k % 3] * (2 if k % 4 == 3 else 1)
                tt = np.arange(n) / SAMPLE_RATE
                pluck = np.sin(2 * np.pi * f * tt) + 0.3 * np.sin(4 * np.pi * f * tt)
                out[s:s + n] += 0.07 * pluck * _envelope(n, 0.004, 0.12)
        position += bar
        index += 1
    out /= max(1e-9, np.max(np.abs(out)))
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
