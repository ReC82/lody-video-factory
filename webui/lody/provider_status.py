"""État de configuration des fournisseurs : « prêt » ou « clé à configurer ».

Lit config.toml en lecture seule et ne retourne QUE des booléens : aucune valeur
de clé n'est conservée, affichée ni journalisée.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from lody import settings
from lody.catalog import PROVIDER_ENV_ALTERNATIVES, PROVIDER_REQUIREMENTS

Readiness = dict[tuple[str, str], bool]


def _is_filled(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(isinstance(item, str) and item.strip() for item in value)
    return False


def _load_config(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def readiness() -> Readiness:
    """Table {(type, fournisseur): prêt ?}. Le contenu de config.toml est jeté aussitôt."""
    config = _load_config(settings.config_path())
    result: Readiness = {}
    for (kind, value), requirements in PROVIDER_REQUIREMENTS.items():
        in_config = any(_is_filled(config.get(section, {}).get(key)) for section, key in requirements)
        in_env = any(
            os.environ.get(name, "").strip()
            for name in PROVIDER_ENV_ALTERNATIVES.get((kind, value), ())
        )
        result[(kind, value)] = in_config or in_env
    return result


def is_ready(kind: str, value: str, table: Readiness) -> bool:
    """Un fournisseur sans exigence de clé est toujours prêt."""
    return table.get((kind, value), True)
