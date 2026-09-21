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

Readiness = dict[tuple[str, str], bool | None]


def _is_filled(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(isinstance(item, str) and item.strip() for item in value)
    return False


def _load_config(path: Path) -> dict | None:
    """Contenu de config.toml, ou ``None`` s'il est illisible (droits, absence, TOML invalide)."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def readiness() -> Readiness:
    """Table {(type, fournisseur): prêt ?}. Le contenu de config.toml est jeté aussitôt.

    ``None`` = non vérifié : la configuration n'est pas lisible par Lody (ex. fichier en 0600 d'un autre
    utilisateur). On ne prétend alors ni « prêt » ni « clé manquante ».
    """
    config = _load_config(settings.config_path())
    result: Readiness = {}
    for (kind, value), requirements in PROVIDER_REQUIREMENTS.items():
        in_env = any(
            os.environ.get(name, "").strip()
            for name in PROVIDER_ENV_ALTERNATIVES.get((kind, value), ())
        )
        if config is None:
            result[(kind, value)] = True if in_env else None
            continue
        in_config = any(_is_filled(config.get(section, {}).get(key)) for section, key in requirements)
        result[(kind, value)] = in_config or in_env
    return result


def is_unverified(kind: str, value: str, table: Readiness) -> bool:
    return (kind, value) in table and table[(kind, value)] is None


def is_ready(kind: str, value: str, table: Readiness) -> bool:
    """Faux seulement si on sait qu'une clé manque : sans exigence ou non vérifié, on ne bloque pas."""
    return table.get((kind, value), True) is not False
