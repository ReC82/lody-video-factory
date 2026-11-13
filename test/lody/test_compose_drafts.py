"""Brouillon persistant de « Nouvelle production » (#189) : dépôt, validation et migration SQLite."""

from __future__ import annotations

import json
import sqlite3

import pytest

from lody import db
from lody.animation import pilot
from lody.animation.assets import AnimationAssetRepository
from lody.compose_drafts import (
    DRAFT_VERSION,
    ComposeDraft,
    ComposeDraftRepository,
    DraftValidationError,
    validate_payload,
)
from lody.projects import ProjectRepository

FAKE_KEY = "s" + "k-" + "Zx9" * 8  # factice, construit à l'exécution : jamais une vraie clé


@pytest.fixture
def env(tmp_path):
    path = tmp_path / "lody.sqlite3"
    projects = ProjectRepository(path)
    return projects, ComposeDraftRepository(path, clock=lambda: "2026-11-12T15:00:00+00:00"), path, tmp_path


def test_save_and_get_are_scoped_to_one_project(env):
    projects, drafts, _, _ = env
    a, b = projects.create(name="Projet A"), projects.create(name="Projet B")
    drafts.save(a.id, ComposeDraft(request="Le câble SDI", script="Bonjour", character_ids=["c1", "c1", "c2"],
                                   location_id="l1", demo=True))
    stored = drafts.get(a.id)
    assert stored.request == "Le câble SDI" and stored.script == "Bonjour" and stored.demo is True
    assert stored.character_ids == ["c1", "c2"] and stored.location_id == "l1"
    assert stored.updated_at == "2026-11-12T15:00:00+00:00"
    assert drafts.get(b.id) is None  # jamais restauré sur un autre projet
    drafts.delete(a.id)
    assert drafts.get(a.id) is None


def test_unchanged_draft_is_not_rewritten(env):
    projects, _, path, _ = env
    project = projects.create(name="Projet A")
    times = iter(["2026-11-12T15:00:00+00:00", "2026-11-12T15:05:00+00:00", "2026-11-12T15:10:00+00:00"])
    drafts = ComposeDraftRepository(path, clock=lambda: next(times))
    drafts.save(project.id, ComposeDraft(request="Sujet"))
    assert drafts.save(project.id, ComposeDraft(request="Sujet")).updated_at == "2026-11-12T15:00:00+00:00"
    assert drafts.save(project.id, ComposeDraft(request="Sujet modifié")).updated_at == "2026-11-12T15:10:00+00:00"


def test_unknown_project_is_refused(env):
    _, drafts, _, _ = env
    with pytest.raises(LookupError):
        drafts.save("inconnu", ComposeDraft(request="x"))


@pytest.mark.parametrize("payload", [
    {"version": 99, "request": "x"},
    {"version": DRAFT_VERSION, "request": "x" * 2001},
    {"version": DRAFT_VERSION, "script": "x" * 8001},
    {"version": DRAFT_VERSION, "character_ids": "c1"},
    {"version": DRAFT_VERSION, "character_ids": [{"id": "c1"}]},
    {"version": DRAFT_VERSION, "storyboard": {"source": "ailleurs", "document": {}}},
    {"version": DRAFT_VERSION, "storyboard": {"source": "upload", "document": {"schema": "autre"}}},
    {"version": DRAFT_VERSION, "request": f"Ma clé {FAKE_KEY}"},
    [],
])
def test_invalid_payloads_are_refused(payload):
    with pytest.raises(DraftValidationError):
        validate_payload(payload)


def test_unreadable_stored_draft_is_ignored_without_crashing(env):
    projects, drafts, path, _ = env
    project = projects.create(name="Projet A")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO production_compose_drafts (project_id, payload, created_at, updated_at) "
                           "VALUES (?, ?, 'x', 'x')", (project.id, "{pas du json"))
    assert drafts.get(project.id) is None
    drafts.save(project.id, ComposeDraft(request="Nouveau départ"))
    assert drafts.get(project.id).request == "Nouveau départ"


def test_demo_storyboard_is_kept_validated_without_copying_assets(env):
    projects, drafts, path, tmp_path = env
    project = projects.create(name="Pilote 2D")
    storyboard, _ = pilot.install_voltix_demo(AnimationAssetRepository(path), project.id, tmp_path / "data")
    drafts.save(project.id, ComposeDraft(storyboard={"source": "demo150", "label": "démo Voltix #150",
                                                     "document": storyboard}))
    stored = drafts.get(project.id)
    assert stored.storyboard["source"] == "demo150" and stored.storyboard["document"] == storyboard
    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM production_compose_drafts").fetchone()[0])
    assert payload["version"] == DRAFT_VERSION and '"files"' not in json.dumps(payload)


def test_migration_v10_keeps_every_existing_project(tmp_path):
    path = tmp_path / "lody.sqlite3"
    projects = ProjectRepository(path)
    names = {projects.create(name=f"Projet {i}").name for i in range(3)}
    with sqlite3.connect(path) as connection:  # base « d'avant » : sans la table, en version 9
        connection.execute("DROP TABLE production_compose_drafts")
        connection.execute("PRAGMA user_version = 9")
    ComposeDraftRepository(path)
    ComposeDraftRepository(path)  # rejouée : sans effet
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 10
        assert connection.execute("SELECT count(*) FROM production_compose_drafts").fetchone()[0] == 0
    assert {p.name for p in ProjectRepository(path).list_projects()} >= names
