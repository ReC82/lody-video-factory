"""Dépôt SQLite des productions (table ``productions``, créée par ``lody.db``).

Une connexion courte par opération. Les transitions sensibles (confirmation) sont des
``UPDATE … WHERE status = ?`` atomiques : deux clics ou deux sessions ne peuvent pas les gagner
tous les deux. Aucune clé API n'est acceptée (``secrets_guard``).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from lody import db
from lody.generation.models import ACTIVE_STATUSES, ProductionStatus
from lody.secrets_guard import find_secret_path

JSON_COLUMNS = ("brief", "storyboard", "visual_prompts", "params", "cost_detail", "assets", "warnings")
_JSON_DEFAULTS: dict[str, Any] = {
    "brief": {}, "storyboard": [], "visual_prompts": [], "params": {}, "cost_detail": {}, "assets": [], "warnings": [],
}
UPDATABLE = frozenset({
    "subject", "brief", "script", "script_source", "storyboard", "visual_prompts", "params",
    "cost_currency", "cost_low", "cost_high", "cost_partial", "cost_detail", "confirmed_at", "provider",
    "external_task_id", "status", "progress", "current_step", "error_code", "error_message",
    "started_at", "finished_at", "last_polled_at", "video_ref", "video_duration", "assets", "warnings",
})
# Colonnes de contenu saisi ou dérivé du brief : jamais de clé. (Les identifiants de tâche et les
# chemins de fichiers ressemblent à des jetons opaques : ils sont validés ailleurs, pas ici.)
GUARDED = frozenset({"brief", "storyboard", "visual_prompts", "params", "cost_detail", "subject", "script"})


class ProductionNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Production:
    id: str
    project_id: str
    root_production_id: str
    parent_production_id: str | None
    version: int
    subject: str
    brief: dict[str, Any]
    script: str
    script_source: str
    storyboard: list[dict[str, Any]]
    visual_prompts: list[str]
    params: dict[str, Any]
    cost_currency: str
    cost_low: Decimal | None
    cost_high: Decimal | None
    cost_partial: bool
    cost_detail: dict[str, Any]
    confirmed_at: str | None
    provider: str
    external_task_id: str | None
    idempotency_key: str
    status: ProductionStatus
    progress: int | None
    current_step: str
    error_code: str
    error_message: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    last_polled_at: str | None
    video_ref: str
    video_duration: float | None
    assets: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"V{self.version}"

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value not in (None, "") else None


class ProductionRepository:
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
    def _from_row(row: sqlite3.Row) -> Production:
        data = dict(row)
        for column in JSON_COLUMNS:
            data[column] = json.loads(data[column] or json.dumps(_JSON_DEFAULTS[column]))
        data["status"] = ProductionStatus(data["status"])
        data["cost_low"] = _decimal(data["cost_low"])
        data["cost_high"] = _decimal(data["cost_high"])
        data["cost_partial"] = bool(data["cost_partial"])
        return Production(**data)

    @staticmethod
    def _encode(column: str, value: Any) -> Any:
        if column in GUARDED and find_secret_path({column: value} if not isinstance(value, (dict, list)) else value):
            raise ValueError("Une clé secrète ne peut pas être enregistrée dans une production.")
        if column in JSON_COLUMNS:
            return json.dumps(value, ensure_ascii=False)
        if column in ("cost_low", "cost_high"):
            return None if value is None else str(value)
        if column == "status":
            return ProductionStatus(value).value
        if column == "cost_partial":
            return 1 if value else 0
        return value

    # -- lecture -----------------------------------------------------------
    def get(self, production_id: str) -> Production:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM productions WHERE id = ?", (production_id,)).fetchone()
        if row is None:
            raise ProductionNotFound(production_id)
        return self._from_row(row)

    def list_for_project(self, project_id: str) -> list[Production]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM productions WHERE project_id = ? ORDER BY created_at DESC, version DESC", (project_id,)
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def list_by_status(self, statuses: Iterable[ProductionStatus]) -> list[Production]:
        values = [ProductionStatus(status).value for status in statuses]
        if not values:
            return []
        marks = ",".join("?" for _ in values)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM productions WHERE status IN ({marks}) ORDER BY created_at", values
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def active_for_project(self, project_id: str) -> list[Production]:
        return [p for p in self.list_for_project(project_id) if p.is_active]

    def chain(self, root_id: str) -> list[Production]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM productions WHERE root_production_id = ? ORDER BY version", (root_id,)
            ).fetchall()
        return [self._from_row(row) for row in rows]

    # -- écriture ----------------------------------------------------------
    def create(self, *, project_id: str, subject: str, provider: str, status: ProductionStatus,
               parent_production_id: str | None = None, **fields: Any) -> Production:
        for column in fields:
            if column not in UPDATABLE:
                raise ValueError(f"colonne inconnue : {column}")
        production_id = f"prd_{uuid.uuid4().hex[:12]}"
        now = self._clock()
        with self._connect() as connection:
            if parent_production_id:
                parent = connection.execute(
                    "SELECT root_production_id FROM productions WHERE id = ?", (parent_production_id,)
                ).fetchone()
                if parent is None:
                    raise ProductionNotFound(parent_production_id)
                root_id = parent["root_production_id"]
                version = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM productions WHERE root_production_id = ?", (root_id,)
                ).fetchone()[0]
            else:
                root_id, version = production_id, 1
            values: dict[str, Any] = {
                "id": production_id, "project_id": project_id, "root_production_id": root_id,
                "parent_production_id": parent_production_id, "version": version,
                "subject": self._encode("subject", subject), "provider": provider,
                "idempotency_key": f"lody-{production_id}", "status": status.value,
                "created_at": now, "updated_at": now,
            }
            for column, value in fields.items():
                values[column] = self._encode(column, value)
            names = ", ".join(values)
            marks = ", ".join(f":{name}" for name in values)
            connection.execute(f"INSERT INTO productions ({names}) VALUES ({marks})", values)
        return self.get(production_id)

    def update(self, production_id: str, **fields: Any) -> Production:
        if not fields:
            return self.get(production_id)
        values = self._prepare(fields)
        assignments = ", ".join(f"{name} = :{name}" for name in values)
        values["_id"] = production_id
        with self._connect() as connection:
            updated = connection.execute(
                f"UPDATE productions SET {assignments}, updated_at = :_now WHERE id = :_id", {**values, "_now": self._clock()}
            ).rowcount
        if not updated:
            raise ProductionNotFound(production_id)
        return self.get(production_id)

    def transition(self, production_id: str, allowed_from: Iterable[ProductionStatus], **fields: Any) -> bool:
        """Change l'état seulement si la production est *encore* dans un des états attendus.

        Retourne ``False`` si quelqu'un d'autre l'a déjà fait : c'est ce qui empêche un double
        lancement (deux clics, deux onglets).
        """
        sources = [ProductionStatus(status).value for status in allowed_from]
        values = self._prepare(fields)
        assignments = ", ".join(f"{name} = :{name}" for name in values)
        marks = ",".join(f":_from{index}" for index in range(len(sources)))
        params = {**values, "_id": production_id, "_now": self._clock(),
                  **{f"_from{index}": source for index, source in enumerate(sources)}}
        with self._connect() as connection:
            updated = connection.execute(
                f"UPDATE productions SET {assignments}, updated_at = :_now "
                f"WHERE id = :_id AND status IN ({marks})", params
            ).rowcount
        return updated == 1

    def _prepare(self, fields: dict[str, Any]) -> dict[str, Any]:
        for column in fields:
            if column not in UPDATABLE:
                raise ValueError(f"colonne non modifiable : {column}")
        return {column: self._encode(column, value) for column, value in fields.items()}
