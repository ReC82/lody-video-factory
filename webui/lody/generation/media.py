"""Mesures locales sur un fichier vidéo (gratuit, sans réseau)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def probe_duration_ms(path: Path, timeout: float = 20.0) -> int | None:
    """Durée réelle en millisecondes via ffprobe ; ``None`` si ffprobe est absent ou échoue."""
    try:
        done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                              capture_output=True, text=True, timeout=timeout, check=False)
        return round(float(done.stdout.strip()) * 1000) if done.returncode == 0 and done.stdout.strip() else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
