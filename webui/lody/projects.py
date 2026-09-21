"""Projets Lody : modèle, validation et dépôt SQLite.

Couche indépendante de Streamlit. Aucune clé API n'est stockée ici : un projet ne
référence un fournisseur que par son identifiant (voir ``secrets_guard``).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import catalog
from lody.brief import BRIEF_KEY, validate_brief
from lody.secrets_guard import SECRET_MESSAGE, find_secret_path

logger = logging.getLogger("lody.projects")

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
SCHEMA_VERSION = 1

NAME_MIN, NAME_MAX = 2, 80
DESCRIPTION_MAX = 500
TONE_MAX = 60
VISUAL_STYLE_MAX = 200
VOICE_NAME_MAX = 80
SETTINGS_MAX_BYTES = 16 * 1024

EDITABLE_FIELDS = (
    "name",
    "description",
    "language",
    "format",
    "content_type",
    "tone",
    "visual_style",
    "platforms",
    "text_provider",
    "visual_provider",
    "voice_provider",
    "voice_name",
    "music_provider",
    "settings",
)
DEFAULTS: dict[str, Any] = {
    "description": "",
    "language": "fr-FR",
    "format": "9:16",
    "content_type": "pedagogique",
    "tone": "Pédagogique et dynamique",
    "visual_style": "",
    "platforms": [],
    "text_provider": "openai",
    "visual_provider": "openai_image",
    "voice_provider": "elevenlabs",
    "voice_name": "",
    "music_provider": "none",
    "settings": {},
}


class ProjectError(Exception):
    """Erreur de base de la couche projets."""


class ProjectValidationError(ProjectError):
    """Données invalides : ``errors`` associe un champ à un message lisible."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


class ProjectNotFound(ProjectError):
    pass


@dataclass
class Project:
    id: str
    name: str
    description: str
    status: str
    created_at: str
    updated_at: str
    language: str
    format: str
    content_type: str
    tone: str
    visual_style: str
    platforms: list[str]
    text_provider: str
    visual_provider: str
    voice_provider: str
    voice_name: str
    music_provider: str
    settings: dict[str, Any] = field(default_factory=dict)
    seed_key: str | None = None

    @property
    def is_archived(self) -> bool:
        return self.status == STATUS_ARCHIVED


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    language        TEXT NOT NULL,
    format          TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    tone            TEXT NOT NULL DEFAULT '',
    visual_style    TEXT NOT NULL DEFAULT '',
    platforms       TEXT NOT NULL DEFAULT '[]',
    text_provider   TEXT NOT NULL,
    visual_provider TEXT NOT NULL,
    voice_provider  TEXT NOT NULL,
    voice_name      TEXT NOT NULL DEFAULT '',
    music_provider  TEXT NOT NULL,
    settings        TEXT NOT NULL DEFAULT '{}',
    seed_key        TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_projects_status_updated ON projects (status, updated_at DESC);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Nettoie et valide un jeu complet de champs. Lève ProjectValidationError."""
    errors: dict[str, str] = {}
    clean: dict[str, Any] = {}

    def text(key: str, maximum: int, too_long: str, required: str | None = None) -> None:
        raw = fields.get(key, DEFAULTS.get(key, ""))
        value = " ".join(str(raw or "").split()) if key != "description" else str(raw or "").strip()
        if required and not value:
            errors[key] = required
        elif len(value) > maximum:
            errors[key] = too_long
        clean[key] = value

    text("name", NAME_MAX, f"Le nom est trop long ({NAME_MAX} caractères maximum).",
         "Donne un nom à ton projet.")
    if "name" not in errors and len(clean["name"]) < NAME_MIN:
        errors["name"] = f"Le nom doit faire au moins {NAME_MIN} caractères."
    text("description", DESCRIPTION_MAX, f"La description est trop longue ({DESCRIPTION_MAX} caractères maximum).")
    text("tone", TONE_MAX, f"Le ton est trop long ({TONE_MAX} caractères maximum).")
    text("visual_style", VISUAL_STYLE_MAX, f"Le style visuel est trop long ({VISUAL_STYLE_MAX} caractères maximum).")
    text("voice_name", VOICE_NAME_MAX, f"Le nom de la voix est trop long ({VOICE_NAME_MAX} caractères maximum).")

    choice_messages = {
        "content_type": "Choisis un type de contenu dans la liste.",
        "language": "Choisis une langue dans la liste.",
        "format": "Choisis un format dans la liste.",
        "text_provider": "Choisis un fournisseur de texte dans la liste.",
        "visual_provider": "Choisis un fournisseur d’images dans la liste.",
        "voice_provider": "Choisis un fournisseur de voix dans la liste.",
        "music_provider": "Choisis une option de musique dans la liste.",
    }
    for key, message in choice_messages.items():
        value = fields.get(key, DEFAULTS[key])
        if value in catalog.values(catalog.CATALOGS[key]):
            clean[key] = value
        else:
            errors[key] = message
            clean[key] = value

    raw_platforms = fields.get("platforms", DEFAULTS["platforms"]) or []
    allowed = catalog.values(catalog.PLATFORMS)
    if isinstance(raw_platforms, (list, tuple)) and all(item in allowed for item in raw_platforms):
        clean["platforms"] = [item for item in allowed if item in raw_platforms]
    else:
        errors["platforms"] = "Une plateforme choisie n’est pas reconnue."
        clean["platforms"] = []

    raw_settings = fields.get("settings", DEFAULTS["settings"]) or {}
    if not isinstance(raw_settings, dict):
        errors["settings"] = "Les réglages avancés sont invalides."
        clean["settings"] = {}
    else:
        try:
            too_big = len(json.dumps(raw_settings, ensure_ascii=False).encode("utf-8")) > SETTINGS_MAX_BYTES
        except (TypeError, ValueError):
            errors["settings"] = "Les réglages avancés sont invalides."
            too_big = False
        else:
            if too_big:
                errors["settings"] = "Les réglages avancés sont trop volumineux."
        clean["settings"] = raw_settings
        if BRIEF_KEY in raw_settings:
            raw_brief = raw_settings[BRIEF_KEY]
            if isinstance(raw_brief, dict):
                clean_brief, brief_errors = validate_brief(raw_brief)
                errors.update(brief_errors)
                clean["settings"] = {**raw_settings, BRIEF_KEY: clean_brief}
            else:
                errors["settings"] = "Les paramètres de production sont invalides."

    # Garde-fou secrets : tout champ texte, plateformes et réglages.
    for key in ("name", "description", "tone", "visual_style", "voice_name", "platforms", "settings"):
        if key not in errors and find_secret_path(clean.get(key)):
            errors[key] = SECRET_MESSAGE

    if errors:
        # Aucun message ne reprend la valeur fautive : rien ne fuit dans les logs/UI.
        raise ProjectValidationError(errors)
    return clean


class ProjectRepository:
    """Accès SQLite aux projets. Une connexion courte par opération (thread-safe)."""

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
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                connection.executescript(_SCHEMA)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.execute("PRAGMA journal_mode = WAL")

    # -- lecture ---------------------------------------------------------
    @staticmethod
    def _from_row(row: sqlite3.Row) -> Project:
        data = dict(row)
        data["platforms"] = json.loads(data["platforms"] or "[]")
        data["settings"] = json.loads(data["settings"] or "{}")
        return Project(**data)

    def get(self, project_id: str) -> Project:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return self._from_row(row)

    def list_projects(self, status: str | None = None) -> list[Project]:
        query = "SELECT * FROM projects"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC, created_at DESC, name COLLATE NOCASE"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._from_row(row) for row in rows]

    def count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0]

    # -- écriture --------------------------------------------------------
    def _check_name_unique(self, connection: sqlite3.Connection, name: str, exclude_id: str | None) -> None:
        wanted = name.casefold()
        for row in connection.execute("SELECT id, name FROM projects"):
            if row["id"] != exclude_id and row["name"].casefold() == wanted:
                raise ProjectValidationError({"name": "Un projet porte déjà ce nom. Choisis-en un autre."})

    @staticmethod
    def _values(clean: dict[str, Any]) -> dict[str, Any]:
        values = {key: clean[key] for key in EDITABLE_FIELDS}
        values["platforms"] = json.dumps(clean["platforms"], ensure_ascii=False)
        values["settings"] = json.dumps(clean["settings"], ensure_ascii=False)
        return values

    def create(self, **fields: Any) -> Project:
        clean = validate_fields({**DEFAULTS, **fields})
        now = self._clock()
        project_id = f"prj_{uuid.uuid4().hex[:12]}"
        values = self._values(clean)
        with self._connect() as connection:
            self._check_name_unique(connection, clean["name"], None)
            connection.execute(
                "INSERT INTO projects (id, status, created_at, updated_at, "
                + ", ".join(EDITABLE_FIELDS)
                + ") VALUES (?, ?, ?, ?, "
                + ", ".join("?" for _ in EDITABLE_FIELDS)
                + ")",
                (project_id, STATUS_ACTIVE, now, now, *(values[key] for key in EDITABLE_FIELDS)),
            )
        logger.info("projet créé : %s", project_id)
        return self.get(project_id)

    def update(self, project_id: str, **fields: Any) -> Project:
        unknown = set(fields) - set(EDITABLE_FIELDS)
        if unknown:
            raise ProjectValidationError({"_": "Champ inconnu : " + ", ".join(sorted(unknown))})
        current = self.get(project_id)
        merged = {key: getattr(current, key) for key in EDITABLE_FIELDS}
        merged.update(fields)
        clean = validate_fields(merged)
        values = self._values(clean)
        with self._connect() as connection:
            self._check_name_unique(connection, clean["name"], project_id)
            connection.execute(
                "UPDATE projects SET " + ", ".join(f"{key} = ?" for key in EDITABLE_FIELDS)
                + ", updated_at = ? WHERE id = ?",
                (*(values[key] for key in EDITABLE_FIELDS), self._clock(), project_id),
            )
        logger.info("projet modifié : %s", project_id)
        return self.get(project_id)

    def _set_status(self, project_id: str, status: str) -> Project:
        self.get(project_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?",
                (status, self._clock(), project_id),
            )
        logger.info("projet %s : %s", project_id, status)
        return self.get(project_id)

    def archive(self, project_id: str) -> Project:
        return self._set_status(project_id, STATUS_ARCHIVED)

    def restore(self, project_id: str) -> Project:
        return self._set_status(project_id, STATUS_ACTIVE)

    # -- exemples initiaux ----------------------------------------------
    def seed_defaults(self, seeds: list[dict[str, Any]]) -> int:
        """Insère les projets d'exemple absents (clé ``seed_key``). Idempotent.

        Un exemple déjà présent — même modifié, renommé ou archivé — n'est jamais recréé ni
        réinitialisé. Retourne le nombre de projets ajoutés ; les mises à niveau des exemples
        existants sont gérées par ``upgrade_seeds`` (appelée ensuite).
        """
        added = 0
        for seed in seeds:
            seed = dict(seed)
            seed_key = seed.pop("seed_key")
            clean = validate_fields({**DEFAULTS, **seed})
            values = self._values(clean)
            now = self._clock()
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO projects (id, status, created_at, updated_at, seed_key, "
                    + ", ".join(EDITABLE_FIELDS)
                    + ") VALUES (?, ?, ?, ?, ?, "
                    + ", ".join("?" for _ in EDITABLE_FIELDS)
                    + ") ON CONFLICT(seed_key) DO NOTHING",
                    (f"prj_{uuid.uuid4().hex[:12]}", STATUS_ACTIVE, now, now, seed_key,
                     *(values[key] for key in EDITABLE_FIELDS)),
                )
                added += cursor.rowcount
        if added:
            logger.info("projets d'exemple ajoutés : %d", added)
        self.upgrade_seeds(seeds)
        return added

    def upgrade_seeds(self, seeds: list[dict[str, Any]]) -> int:
        """Ajoute les paramètres de production aux exemples créés avant leur existence.

        Ne concerne que les exemples dont la définition contient un bloc ``brief`` : un projet
        qui possède déjà ce bloc n'est jamais touché. Un champ n'est remplacé que s'il porte
        encore sa valeur d'origine (``legacy_values``) : une modification de l'utilisateur est
        toujours conservée. Retourne le nombre de projets mis à niveau.
        """
        upgraded = 0
        for seed in seeds:
            seed_brief = seed.get("settings", {}).get(BRIEF_KEY)
            if not seed_brief:
                continue
            with self._connect() as connection:
                row = connection.execute("SELECT * FROM projects WHERE seed_key = ?", (seed["seed_key"],)).fetchone()
                if row is None:
                    continue
                project = self._from_row(row)
                if BRIEF_KEY in project.settings:
                    continue
                merged = {key: getattr(project, key) for key in EDITABLE_FIELDS}
                for field, legacy_value in seed.get("legacy_values", {}).items():
                    if getattr(project, field) == legacy_value:
                        merged[field] = seed[field]
                settings = {key: value for key, value in project.settings.items() if key != "target_duration"}
                settings[BRIEF_KEY] = seed_brief
                merged["settings"] = settings
                values = self._values(validate_fields(merged))
                connection.execute(
                    "UPDATE projects SET " + ", ".join(f"{key} = ?" for key in EDITABLE_FIELDS)
                    + ", updated_at = ? WHERE id = ?",
                    (*(values[key] for key in EDITABLE_FIELDS), self._clock(), project.id),
                )
                upgraded += 1
        if upgraded:
            logger.info("exemples mis à niveau : %d", upgraded)
        return upgraded
