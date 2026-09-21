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


def engine_report_path() -> Path:
    """Rapport de capacités du moteur (booléens et noms de modèles, jamais de valeur de clé).

    Produit côté hôte par ``python3 -m lody.generation.engine_facts`` ; lu quand ``config.toml`` n'est
    pas lisible par le conteneur. LODY_ENGINE_REPORT le redéfinit (tests).
    """
    raw = os.environ.get("LODY_ENGINE_REPORT", "").strip()
    return Path(raw) if raw else Path("/engine-report/engine-capabilities.json")


def fonts_dir() -> Path:
    """Polices de sous-titres fournies avec le moteur (resource/fonts) ; LODY_FONTS_DIR pour les tests."""
    raw = os.environ.get("LODY_FONTS_DIR", "").strip()
    return Path(raw) if raw else _REPO_ROOT / "resource" / "fonts"
