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


def voice_catalog_report_path() -> Path:
    """Catalogue de voix ElevenLabs assaini (voice_id/nom/métadonnées publiques, jamais de clé).

    Même dossier monté en lecture seule que le rapport de capacités du moteur (``./engine-report``,
    voir ``docker-compose.lody.yml``) : aucun montage supplémentaire n'est nécessaire. Produit côté hôte
    par ``python3 -m lody.generation.elevenlabs_voices`` (ticket #55) ; LODY_VOICE_CATALOG_REPORT pour
    les tests/conteneur.
    """
    raw = os.environ.get("LODY_VOICE_CATALOG_REPORT", "").strip()
    return Path(raw) if raw else Path("/engine-report/elevenlabs-voices.json")


def fonts_dir() -> Path:
    """Polices de sous-titres fournies avec le moteur (resource/fonts) ; LODY_FONTS_DIR pour les tests."""
    raw = os.environ.get("LODY_FONTS_DIR", "").strip()
    return Path(raw) if raw else _REPO_ROOT / "resource" / "fonts"


def secrets_dir() -> Path:
    """Dossier des clés en attente d'application (``secrets.toml``, signal, statut) : monté en écriture pour Lody
    UNIQUEMENT sur ce dossier — jamais sur config.toml. LODY_SECRETS_DIR pour les tests/conteneur."""
    raw = os.environ.get("LODY_SECRETS_DIR", "").strip()
    return Path(raw) if raw else _REPO_ROOT / "secrets"


def secrets_path() -> Path:
    return secrets_dir() / "secrets.toml"


def secrets_reload_path() -> Path:
    return secrets_dir() / "secrets.reload"


def secrets_status_path() -> Path:
    return secrets_dir() / "secrets-status.json"


def system_settings_enabled() -> bool:
    """Verrou de mise en ligne : désactivé par défaut. Ne doit être activé qu'une fois le site protégé par une
    authentification — voir docs/lody-secrets.md."""
    return os.environ.get("LODY_ENABLE_SYSTEM_SETTINGS", "").strip().lower() in {"1", "true", "yes"}
