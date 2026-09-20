"""Garde-fou : aucune clé secrète ne doit entrer dans un projet (SQLite).

Les clés API vivent dans la configuration du serveur (config.toml / environnement).
Un projet ne peut référencer qu'un *fournisseur* par son identifiant.
"""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEY_NAME = re.compile(
    r"(api[_\-\s]?keys?|secret|tokens?\b|passw(or)?d|bearer|authorization|credential|"
    r"private[_\-]?key|access[_\-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxi-[A-Za-z0-9]{12,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9\-_.=]{16,}", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # Jeton opaque : ≥ 32 caractères sans espace mêlant lettres et chiffres.
    re.compile(r"\b(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{32,}\b"),
)

SECRET_MESSAGE = (
    "Ce champ ressemble à une clé secrète. Les clés se gèrent dans la configuration "
    "du serveur, jamais dans un projet."
)


def looks_like_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def looks_like_secret_key(name: str) -> bool:
    return bool(_SECRET_KEY_NAME.search(name))


def find_secret_path(value: Any, path: str = "") -> str | None:
    """Retourne le chemin du premier élément suspect (clé ou valeur), sinon None.

    Le chemin ne contient jamais la valeur elle-même.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if looks_like_secret_key(str(key)):
                return here
            found = find_secret_path(item, here)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = find_secret_path(item, f"{path}[{index}]")
            if found:
                return found
    elif isinstance(value, str) and looks_like_secret_value(value):
        return path or "(valeur)"
    return None
