"""Emplacements et options d'exécution (jamais de secret)."""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Dossier persistant des données Lody (base SQLite).

    En conteneur : volume monté sur ``/data`` (LODY_DATA_DIR). En local : ``./data``.
    """
    raw = os.environ.get("LODY_DATA_DIR", "").strip()
    return Path(raw) if raw else _REPO_ROOT / "data"


def db_path() -> Path:
    return data_dir() / "lody.sqlite3"


def config_path() -> Path:
    """config.toml historique, monté en lecture seule (LODY_CONFIG_PATH pour les tests)."""
    raw = os.environ.get("LODY_CONFIG_PATH", "").strip()
    return Path(raw) if raw else _REPO_ROOT / "config.toml"


def seed_defaults_enabled() -> bool:
    return os.environ.get("LODY_SEED_DEFAULTS", "1").strip().lower() not in {"0", "false", "no"}
