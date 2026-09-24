"""Migrations SQLite de Lody : ordonnées, idempotentes, versionnées par ``PRAGMA user_version``.

Une seule base (``lody.sqlite3``) sert les projets et les productions. Chaque migration n'utilise
que ``IF NOT EXISTS`` : la rejouer (deux conteneurs qui démarrent, base copiée, redémarrage au
milieu) est sans effet. Les migrations ne suppriment ni ne réécrivent jamais de données.

Aucune colonne ne peut recevoir de clé API : ce sont des champs métier (voir ``secrets_guard``).
"""

from __future__ import annotations

import sqlite3
from typing import Any

PROJECTS_SCHEMA = """
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

# Statuts internes d'une production (voir generation.models.ProductionStatus).
PRODUCTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS productions (
    id                   TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL REFERENCES projects (id),
    root_production_id   TEXT NOT NULL,
    parent_production_id TEXT REFERENCES productions (id),
    version              INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    subject              TEXT NOT NULL,
    brief                TEXT NOT NULL DEFAULT '{}',
    script               TEXT NOT NULL DEFAULT '',
    script_source        TEXT NOT NULL DEFAULT '',
    storyboard           TEXT NOT NULL DEFAULT '[]',
    visual_prompts       TEXT NOT NULL DEFAULT '[]',
    params               TEXT NOT NULL DEFAULT '{}',
    cost_currency        TEXT NOT NULL DEFAULT 'EUR',
    cost_low             TEXT,
    cost_high            TEXT,
    cost_partial         INTEGER NOT NULL DEFAULT 1,
    cost_detail          TEXT NOT NULL DEFAULT '{}',
    confirmed_at         TEXT,
    provider             TEXT NOT NULL,
    external_task_id     TEXT,
    idempotency_key      TEXT NOT NULL UNIQUE,
    status               TEXT NOT NULL CHECK (status IN (
        'BROUILLON', 'EN_ATTENTE_CONFIRMATION', 'CONFIRMEE', 'EN_FILE',
        'EN_COURS', 'TERMINEE', 'ECHEC', 'ANNULEE')),
    progress             INTEGER CHECK (progress IS NULL OR (progress BETWEEN 0 AND 100)),
    current_step         TEXT NOT NULL DEFAULT '',
    error_code           TEXT NOT NULL DEFAULT '',
    error_message        TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    started_at           TEXT,
    finished_at          TEXT,
    updated_at           TEXT NOT NULL,
    last_polled_at       TEXT,
    video_ref            TEXT NOT NULL DEFAULT '',
    video_duration       REAL,
    assets               TEXT NOT NULL DEFAULT '[]',
    warnings             TEXT NOT NULL DEFAULT '[]',
    UNIQUE (root_production_id, version)
);
CREATE INDEX IF NOT EXISTS idx_productions_project ON productions (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_productions_status ON productions (status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_productions_external
    ON productions (provider, external_task_id) WHERE external_task_id IS NOT NULL;
"""

# Kit de publication : un par production (donc par version), avec ses propres métadonnées, choix de miniature et historique.
KITS_SCHEMA = """
CREATE TABLE IF NOT EXISTS publication_kits (
    id                TEXT PRIMARY KEY,
    production_id     TEXT NOT NULL UNIQUE REFERENCES productions (id),
    project_id        TEXT NOT NULL REFERENCES projects (id),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    generated_with    TEXT NOT NULL DEFAULT 'local',
    metadata          TEXT NOT NULL DEFAULT '{}',
    initial_metadata  TEXT NOT NULL DEFAULT '{}',
    thumbnail         TEXT NOT NULL DEFAULT '{}',
    history           TEXT NOT NULL DEFAULT '[]',
    background_job    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_kits_project ON publication_kits (project_id);
"""


# Personnages et lieux récurrents d'un projet (epic #29, fondations #30/#31) : entièrement facultatifs,
# jamais lus par le pipeline de génération tant que la sélection (#34) n'existe pas. Aucune image binaire
# ici (voir #38) ; aucune clé API (voir secrets_guard, appliqué par characters.py/locations.py).
CHARACTERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects (id),
    name                TEXT NOT NULL,
    role                TEXT NOT NULL DEFAULT '',
    personality         TEXT NOT NULL DEFAULT '',
    visual_description  TEXT NOT NULL DEFAULT '',
    reference_prompt    TEXT NOT NULL DEFAULT '',
    speech_style        TEXT NOT NULL DEFAULT '',
    voice_provider      TEXT NOT NULL DEFAULT '',
    voice_name          TEXT NOT NULL DEFAULT '',
    external_voice_id   TEXT NOT NULL DEFAULT '',
    permanent_elements  TEXT NOT NULL DEFAULT '',
    continuity_notes    TEXT NOT NULL DEFAULT '',
    is_primary          INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_characters_project ON characters (project_id, is_active DESC, name COLLATE NOCASE);
"""

LOCATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES projects (id),
    name              TEXT NOT NULL,
    location_type     TEXT NOT NULL DEFAULT '',
    description       TEXT NOT NULL DEFAULT '',
    reference_prompt  TEXT NOT NULL DEFAULT '',
    continuity_notes  TEXT NOT NULL DEFAULT '',
    is_primary        INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_locations_project ON locations (project_id, is_active DESC, name COLLATE NOCASE);
"""


def _add_columns(connection: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """ALTER TABLE ... ADD COLUMN, seulement pour les colonnes absentes (SQLite n'a pas IF NOT EXISTS ici)."""
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _productions_isolation(connection: sqlite3.Connection) -> None:
    """v3 : instantané immuable des paramètres du projet (``snapshot``) et trace des prompts réellement envoyés
    (``trace``). Les productions antérieures gardent ``{}`` : leurs données ne sont ni réécrites ni inventées."""
    _add_columns(connection, "productions", {
        "snapshot": "TEXT NOT NULL DEFAULT '{}'",
        "trace": "TEXT NOT NULL DEFAULT '{}'",
    })


def _reference_images(connection: sqlite3.Connection) -> None:
    """v6 (#38) : référence (chemin relatif déjà validé, voir ``lody.reference_images``) d'une image de
    référence facultative — les octets vivent hors SQLite. Chaîne vide (valeur par défaut) = aucune image :
    comportement inchangé pour tout personnage/lieu existant tant que rien n'est téléversé."""
    _add_columns(connection, "characters", {"reference_image": "TEXT NOT NULL DEFAULT ''"})
    _add_columns(connection, "locations", {"reference_image": "TEXT NOT NULL DEFAULT ''"})


MIGRATIONS: tuple[tuple[int, Any], ...] = (
    (1, PROJECTS_SCHEMA),
    (2, PRODUCTIONS_SCHEMA),
    (3, _productions_isolation),
    (4, KITS_SCHEMA),
    (5, CHARACTERS_SCHEMA + LOCATIONS_SCHEMA),
    (6, _reference_images),
)
SCHEMA_VERSION = MIGRATIONS[-1][0]


def migrate(connection: sqlite3.Connection) -> None:
    """Applique les migrations manquantes, dans l'ordre. Sans effet si la base est à jour."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    for target, step in MIGRATIONS:
        if version < target:
            if callable(step):
                step(connection)
            else:
                connection.executescript(step)
            connection.execute(f"PRAGMA user_version = {target}")
            version = target
    connection.execute("PRAGMA journal_mode = WAL")
