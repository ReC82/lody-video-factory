"""Lieux récurrents d'un projet : modèle, validation et dépôt SQLite (epic #29, ticket #31).

Symétrique à ``characters.py`` (mêmes conventions : repository, validation, garde-fou secrets, casefold
pour l'unicité de nom). Facultatif et sans effet tant que rien ne les sélectionne dans une production
(voir #34+) : aucune clé API (voir secrets_guard).

Image de référence facultative (#38) : voir le docstring équivalent de ``characters.py`` — même contrat
(``reference_image`` hors ``EDITABLE_FIELDS``, modifiable uniquement par ``set_reference_image``/
``clear_reference_image``).
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import db, reference_images
from lody.secrets_guard import SECRET_MESSAGE, find_secret_path

logger = logging.getLogger("lody.locations")

NAME_MIN, NAME_MAX = 2, 80
LOCATION_TYPE_MAX = 60
DESCRIPTION_MAX = 500
REFERENCE_PROMPT_MAX = 500
CONTINUITY_NOTES_MAX = 500

EDITABLE_FIELDS = (
    "name", "location_type", "description", "reference_prompt", "continuity_notes",
    "is_primary", "is_active",
)
DEFAULTS: dict[str, Any] = {
    "location_type": "", "description": "", "reference_prompt": "", "continuity_notes": "",
    "is_primary": False, "is_active": True,
}


class LocationError(Exception):
    """Erreur de base pour le dépôt des lieux."""


class LocationValidationError(LocationError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


class LocationNotFound(LocationError):
    """Identifiant inconnu, ou appartenant à un autre projet — même erreur dans les deux cas."""


@dataclass
class Location:
    id: str
    project_id: str
    name: str
    location_type: str
    description: str
    reference_prompt: str
    continuity_notes: str
    reference_image: str
    is_primary: bool
    is_active: bool
    created_at: str
    updated_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Nettoie et valide un jeu complet de champs. Lève ``LocationValidationError``."""
    errors: dict[str, str] = {}
    clean: dict[str, Any] = {}

    def text(key: str, maximum: int, too_long: str, required: str | None = None) -> None:
        raw = fields.get(key, DEFAULTS.get(key, ""))
        value = " ".join(str(raw or "").split())
        if required and not value:
            errors[key] = required
        elif len(value) > maximum:
            errors[key] = too_long
        clean[key] = value

    text("name", NAME_MAX, f"Le nom est trop long ({NAME_MAX} caractères maximum).", "Donne un nom au lieu.")
    if "name" not in errors and len(clean["name"]) < NAME_MIN:
        errors["name"] = f"Le nom doit faire au moins {NAME_MIN} caractères."
    text("location_type", LOCATION_TYPE_MAX, f"Le type de lieu est trop long ({LOCATION_TYPE_MAX} caractères maximum).")
    text("description", DESCRIPTION_MAX, f"La description est trop longue ({DESCRIPTION_MAX} caractères maximum).")
    text("reference_prompt", REFERENCE_PROMPT_MAX,
         f"Le prompt de référence est trop long ({REFERENCE_PROMPT_MAX} caractères maximum).")
    text("continuity_notes", CONTINUITY_NOTES_MAX,
         f"Les notes de continuité sont trop longues ({CONTINUITY_NOTES_MAX} caractères maximum).")

    clean["is_primary"] = bool(fields.get("is_primary", DEFAULTS["is_primary"]))
    clean["is_active"] = bool(fields.get("is_active", DEFAULTS["is_active"]))

    # Garde-fou secrets : tout champ texte (même précaution que characters.py, pour rester symétrique).
    for key in ("name", "location_type", "description", "reference_prompt", "continuity_notes"):
        if key not in errors and find_secret_path(clean.get(key)):
            errors[key] = SECRET_MESSAGE

    if errors:
        raise LocationValidationError(errors)
    return clean


class LocationRepository:
    def __init__(self, db_path: str | Path, clock: Callable[[], str] = _utc_now):
        self.db_path = Path(db_path)
        self._clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connect() as connection:
            db.migrate(connection)

    @staticmethod
    def _project_exists(connection: sqlite3.Connection, project_id: str) -> bool:
        return connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is not None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Location:
        data = dict(row)
        data["is_primary"] = bool(data["is_primary"])
        data["is_active"] = bool(data["is_active"])
        return Location(**data)

    @staticmethod
    def _values(clean: dict[str, Any]) -> dict[str, Any]:
        values = {key: clean[key] for key in EDITABLE_FIELDS}
        values["is_primary"] = int(clean["is_primary"])
        values["is_active"] = int(clean["is_active"])
        return values

    def get(self, project_id: str, location_id: str) -> Location:
        """Lève ``LocationNotFound`` si l'identifiant n'existe pas OU appartient à un autre projet."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM locations WHERE id = ? AND project_id = ?", (location_id, project_id)
            ).fetchone()
        if row is None:
            raise LocationNotFound(location_id)
        return self._from_row(row)

    def list_for_project(self, project_id: str, *, include_inactive: bool = True) -> list[Location]:
        query = "SELECT * FROM locations WHERE project_id = ?"
        params: list[Any] = [project_id]
        if not include_inactive:
            query += " AND is_active = 1"
        query += " ORDER BY is_active DESC, is_primary DESC, name COLLATE NOCASE"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._from_row(row) for row in rows]

    def count_for_project(self, project_id: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM locations WHERE project_id = ?", (project_id,)
            ).fetchone()[0]

    def _check_name_unique(self, connection: sqlite3.Connection, project_id: str, name: str,
                            exclude_id: str | None) -> None:
        wanted = name.casefold()
        for row in connection.execute("SELECT id, name FROM locations WHERE project_id = ?", (project_id,)):
            if row["id"] != exclude_id and row["name"].casefold() == wanted:
                raise LocationValidationError({"name": "Un lieu porte déjà ce nom dans ce projet."})

    def create(self, project_id: str, **fields: Any) -> Location:
        clean = validate_fields({**DEFAULTS, **fields})
        now = self._clock()
        location_id = f"loc_{uuid.uuid4().hex[:12]}"
        values = self._values(clean)
        with self._connect() as connection:
            if not self._project_exists(connection, project_id):
                raise LocationValidationError({"project_id": "Ce projet n'existe pas."})
            self._check_name_unique(connection, project_id, clean["name"], None)
            connection.execute(
                "INSERT INTO locations (id, project_id, created_at, updated_at, " + ", ".join(EDITABLE_FIELDS)
                + ") VALUES (?, ?, ?, ?, " + ", ".join("?" for _ in EDITABLE_FIELDS) + ")",
                (location_id, project_id, now, now, *(values[key] for key in EDITABLE_FIELDS)),
            )
        logger.info("lieu créé : %s (projet %s)", location_id, project_id)
        return self.get(project_id, location_id)

    def update(self, project_id: str, location_id: str, **fields: Any) -> Location:
        unknown = set(fields) - set(EDITABLE_FIELDS)
        if unknown:
            raise LocationValidationError({"_": "Champ inconnu : " + ", ".join(sorted(unknown))})
        current = self.get(project_id, location_id)
        merged = {key: getattr(current, key) for key in EDITABLE_FIELDS}
        merged.update(fields)
        clean = validate_fields(merged)
        values = self._values(clean)
        with self._connect() as connection:
            self._check_name_unique(connection, project_id, clean["name"], location_id)
            connection.execute(
                "UPDATE locations SET " + ", ".join(f"{key} = ?" for key in EDITABLE_FIELDS)
                + ", updated_at = ? WHERE id = ? AND project_id = ?",
                (*(values[key] for key in EDITABLE_FIELDS), self._clock(), location_id, project_id),
            )
        logger.info("lieu modifié : %s", location_id)
        return self.get(project_id, location_id)

    def _set_active(self, project_id: str, location_id: str, active: bool) -> Location:
        self.get(project_id, location_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE locations SET is_active = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                (int(active), self._clock(), location_id, project_id),
            )
        logger.info("lieu %s : %s", location_id, "activé" if active else "désactivé")
        return self.get(project_id, location_id)

    def activate(self, project_id: str, location_id: str) -> Location:
        return self._set_active(project_id, location_id, True)

    def deactivate(self, project_id: str, location_id: str) -> Location:
        return self._set_active(project_id, location_id, False)

    # -- image de référence (#38, symétrique de characters.py) -----------------------------------------------------
    def set_reference_image(self, project_id: str, location_id: str, data: bytes) -> Location:
        self.get(project_id, location_id)
        try:
            stored = reference_images.save(project_id, "locations", location_id, data)
        except reference_images.ReferenceImageError as error:
            raise LocationValidationError({"reference_image": str(error)}) from error
        with self._connect() as connection:
            connection.execute(
                "UPDATE locations SET reference_image = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                (stored.ref, self._clock(), location_id, project_id),
            )
        logger.info("image de référence enregistrée : %s", location_id)
        return self.get(project_id, location_id)

    def clear_reference_image(self, project_id: str, location_id: str) -> Location:
        self.get(project_id, location_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE locations SET reference_image = '', updated_at = ? WHERE id = ? AND project_id = ?",
                (self._clock(), location_id, project_id),
            )
        logger.info("image de référence retirée : %s", location_id)
        return self.get(project_id, location_id)
