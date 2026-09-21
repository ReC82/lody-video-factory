"""Dépôt SQLite des kits de publication (table ``publication_kits``, créée par ``lody.db``).

Un kit appartient à UNE production (donc à une version) : une V2 a son propre kit, la V1 n'est jamais modifiée.
Aucun secret : les colonnes ne contiennent que des textes de publication et des choix de miniature.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import db
from lody.secrets_guard import find_secret_path

JSON_COLUMNS = ("metadata", "initial_metadata", "thumbnail", "history", "background_job")
_DEFAULTS: dict[str, Any] = {"metadata": {}, "initial_metadata": {}, "thumbnail": {}, "history": [], "background_job": {}}
UPDATABLE = frozenset({"metadata", "initial_metadata", "thumbnail", "history", "background_job", "generated_with"})
HISTORY_MAX = 40


class KitNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Kit:
    id: str
    production_id: str
    project_id: str
    created_at: str
    updated_at: str
    generated_with: str
    metadata: dict[str, Any] = field(default_factory=dict)
    initial_metadata: dict[str, Any] = field(default_factory=dict)
    thumbnail: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    background_job: dict[str, Any] = field(default_factory=dict)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KitRepository:
    def __init__(self, db_path: str | Path, clock: Callable[[], str] = _utc_now):
        self.db_path = Path(db_path)
        self._clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            db.migrate(connection)

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

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Kit:
        data = dict(row)
        for column in JSON_COLUMNS:
            data[column] = json.loads(data[column] or json.dumps(_DEFAULTS[column]))
        return Kit(**data)

    @staticmethod
    def _encode(column: str, value: Any) -> Any:
        if column in JSON_COLUMNS:
            # Motifs de clés connus seulement : un slug de nom de fichier (borné par [a-z0-9._-]) ressemblerait à un jeton opaque.
            if find_secret_path(value, opaque=False):
                raise ValueError("Une clé secrète ne peut pas être enregistrée dans un kit de publication.")
            return json.dumps(value, ensure_ascii=False)
        return value

    def get(self, kit_id: str) -> Kit:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM publication_kits WHERE id = ?", (kit_id,)).fetchone()
        if row is None:
            raise KitNotFound(kit_id)
        return self._from_row(row)

    def find_by_production(self, production_id: str) -> Kit | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM publication_kits WHERE production_id = ?", (production_id,)).fetchone()
        return self._from_row(row) if row else None

    def create(self, *, production_id: str, project_id: str, **fields: Any) -> Kit:
        """Crée le kit d'une production (un seul par production : la contrainte UNIQUE arbitre les créations simultanées)."""
        for column in fields:
            if column not in UPDATABLE:
                raise ValueError(f"colonne inconnue : {column}")
        now = self._clock()
        values: dict[str, Any] = {"id": f"kit_{uuid.uuid4().hex[:12]}", "production_id": production_id, "project_id": project_id,
                                  "created_at": now, "updated_at": now}
        values.update({column: self._encode(column, value) for column, value in fields.items()})
        names = ", ".join(values)
        marks = ", ".join(f":{name}" for name in values)
        try:
            with self._connect() as connection:
                connection.execute(f"INSERT INTO publication_kits ({names}) VALUES ({marks})", values)
        except sqlite3.IntegrityError:
            existing = self.find_by_production(production_id)
            if existing:  # créé en même temps par une autre session
                return existing
            raise
        return self.find_by_production(production_id)  # type: ignore[return-value]

    def update(self, kit_id: str, **fields: Any) -> Kit:
        for column in fields:
            if column not in UPDATABLE:
                raise ValueError(f"colonne non modifiable : {column}")
        if "history" in fields:
            fields["history"] = list(fields["history"])[-HISTORY_MAX:]
        values = {column: self._encode(column, value) for column, value in fields.items()}
        assignments = ", ".join(f"{name} = :{name}" for name in values)
        with self._connect() as connection:
            updated = connection.execute(
                f"UPDATE publication_kits SET {assignments}, updated_at = :_now WHERE id = :_id",
                {**values, "_now": self._clock(), "_id": kit_id}).rowcount
        if not updated:
            raise KitNotFound(kit_id)
        return self.get(kit_id)

    def transition_job(self, kit_id: str, allowed_states: tuple[str, ...], job: dict[str, Any]) -> bool:
        """Change l'état de la tâche « fond de miniature » seulement si elle est encore dans l'un des états attendus."""
        marks = ",".join(f":s{i}" for i in range(len(allowed_states)))
        params = {"_id": kit_id, "_now": self._clock(), "job": self._encode("background_job", job),
                  **{f"s{i}": state for i, state in enumerate(allowed_states)}}
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE publication_kits SET background_job = :job, updated_at = :_now WHERE id = :_id "
                f"AND COALESCE(json_extract(background_job, '$.state'), 'idle') IN ({marks})", params).rowcount
        return updated == 1
