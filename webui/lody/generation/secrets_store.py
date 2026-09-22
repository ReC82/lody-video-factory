"""Écriture côté Lody de ``secrets.toml`` (boîte aux lettres des clés en attente d'application) et lecture du
statut d'application. Volontairement asymétrique :

* **écriture** : la seule chose que ce module sait faire avec une valeur de clé est l'écrire, chiffres et lettres
  inclus, dans ``secrets.toml`` (0640, groupe partagé ``lody-secrets`` — voir docs/lody-secrets.md). Il n'existe **aucune fonction qui relit une valeur** — pas « pour l'instant »,
  elle n'existe pas du tout, ce qui se vérifie en lisant ce fichier plutôt qu'en faisant confiance à un test.
* **lecture** : uniquement le fichier de statut (``secrets-status.json``), écrit par le script hôte, qui ne contient
  jamais de secret par construction (voir ``apply_secrets.py``).

``secrets.toml`` n'est JAMAIS ré-écrit en entier sans raison : une nouvelle valeur est fusionnée avec les valeurs
en attente déjà présentes (si l'utilisateur enregistre deux champs avant que le service systemd n'ait eu le temps
de les appliquer), pas de collision perdue.
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from lody import settings
from lody.generation.secrets_fields import FIELDS, ShapeError, validate
from lody.generation.toml_patch import toml_string

__all__ = ["ShapeError", "clear_pending", "field_status", "read_pending", "set_pending", "status"]

DEFAULT_STATUS: dict[str, Any] = {"state": "idle", "fields": {}}


def read_pending(path: Path) -> dict[str, str]:
    """Valeurs actuellement en attente, indexées par ``field.path``. Ne journalise ni ne relève jamais la valeur
    ailleurs que dans ce dict interne à la fusion — un fichier corrompu ou absent redonne simplement « rien en
    attente », jamais une erreur qui pourrait échouer avec le contenu du fichier dans son message."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}
    pending: dict[str, str] = {}
    for spec in FIELDS.values():
        section = raw.get(spec.section)
        if not isinstance(section, dict) or spec.key not in section:
            continue
        value = section[spec.key]
        if spec.kind == "list1":
            if isinstance(value, list) and value and isinstance(value[0], str):
                pending[spec.path] = value[0]
        elif isinstance(value, str):
            pending[spec.path] = value
    return pending


def _serialize(pending: dict[str, str]) -> str:
    by_section: dict[str, list[str]] = {}
    for path, value in pending.items():
        spec = FIELDS[path]
        line = f"{spec.key} = [{toml_string(value)}]" if spec.kind == "list1" else f"{spec.key} = {toml_string(value)}"
        by_section.setdefault(spec.section, []).append(line)
    parts = []
    for section, lines in by_section.items():
        parts.append(f"[{section}]\n" + "\n".join(lines) + "\n")
    return ("\n".join(parts) + "\n") if parts else ""


def _atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".secrets-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def set_pending(field_path: str, value: str, *, secrets_path: Path | None = None,
                reload_path: Path | None = None) -> None:
    """Valide la FORME de ``value`` (voir ``secrets_fields.validate``) puis l'écrit dans ``secrets.toml`` (0640,
    groupe ``lody-secrets`` hérité du bit setgid du dossier — voir docs/lody-secrets.md), fusionnée avec les
    autres champs déjà en attente. Touche ensuite le fichier signal : c'est ce qui réveille le ``systemd.path``
    côté hôte. Lève ``ShapeError`` sans rien écrire si la forme est invalide."""
    cleaned = validate(field_path, value)  # lève ShapeError avant toute écriture si invalide
    path = secrets_path or settings.secrets_path()
    pending = read_pending(path)
    pending[field_path] = cleaned
    _atomic_write(path, _serialize(pending), 0o640)
    _atomic_write(reload_path or settings.secrets_reload_path(), "", 0o640)


def clear_pending(field_paths: list[str], *, secrets_path: Path | None = None) -> None:
    """Retire des champs de ``secrets.toml`` une fois traités (avec succès, restaurés, ou rejetés) : rien n'y
    reste plus longtemps que nécessaire. Appelé par ``apply_secrets`` (côté hôte), jamais par l'interface."""
    path = secrets_path or settings.secrets_path()
    pending = read_pending(path)
    for field_path in field_paths:
        pending.pop(field_path, None)
    if pending:
        _atomic_write(path, _serialize(pending), 0o640)
    elif path.exists():
        path.unlink()


def status(*, status_path: Path | None = None) -> dict[str, Any]:
    """Dernier statut connu du pipeline d'application (jamais de secret dedans, voir ``apply_secrets.py``)."""
    path = status_path or settings.secrets_status_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return dict(DEFAULT_STATUS)
    if not isinstance(data, dict) or "state" not in data:
        return dict(DEFAULT_STATUS)
    return data


def field_status(field_path: str, *, status_path: Path | None = None) -> dict[str, Any]:
    """Statut pour UN champ : ``{"state": "idle|applying|restarting|verifying|restoring|ok|invalid|unverified|
    rejected|rolled_back|critical", "message": "..."}``. Jamais de valeur de secret."""
    overall = status(status_path=status_path)
    in_flight = overall.get("state") in {"applying", "restarting", "verifying", "restoring"}
    fields = overall.get("fields") if isinstance(overall.get("fields"), dict) else {}
    entry = fields.get(field_path) if isinstance(fields, dict) else None
    if in_flight and field_path in (overall.get("processing") or []):
        return {"state": overall["state"], "message": ""}
    if isinstance(entry, dict):
        return {"state": str(entry.get("state", "idle")), "message": str(entry.get("message", ""))}
    return {"state": "idle", "message": ""}
