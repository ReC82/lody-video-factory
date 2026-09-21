"""Fichiers propres aux kits de publication, dans le volume de données de Lody (jamais dans le stockage du moteur).

Un fond de miniature généré est enregistré sous ``<données>/kits/<production>/background-N.png``. Toute lecture passe par
``resolve_background`` : chemin relatif strict, dossier de CETTE production, extension PNG — pas de traversée, pas de lien sortant.
"""

from __future__ import annotations

import re
from pathlib import Path

from lody import settings
from lody.generation.safety import resolve_within

_PRODUCTION_ID = re.compile(r"^prd_[0-9a-f]{12}$")
MAX_BYTES = 12_000_000


def kit_root() -> Path:
    return settings.data_dir() / "kits"


def save_background(production_id: str, png: bytes) -> str:
    """Enregistre un fond (PNG) et retourne sa référence relative ``kits/<production>/background-N.png``."""
    if not _PRODUCTION_ID.fullmatch(production_id):
        raise ValueError("identifiant de production invalide")
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > MAX_BYTES:
        raise ValueError("image de fond invalide")
    folder = kit_root() / production_id
    folder.mkdir(parents=True, exist_ok=True)
    number = len(list(folder.glob("background-*.png"))) + 1
    target = folder / f"background-{number}.png"
    temp = folder / f".background-{number}.tmp"
    temp.write_bytes(png)
    temp.replace(target)
    return f"kits/{production_id}/{target.name}"


def resolve_background(ref: str, production_id: str) -> Path:
    """Chemin validé d'un fond de CETTE production, sinon ``ValueError``."""
    if not _PRODUCTION_ID.fullmatch(production_id):
        raise ValueError("identifiant de production invalide")
    return resolve_within(settings.data_dir(), ref, prefix=f"kits/{production_id}", suffixes=(".png",))
