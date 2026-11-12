"""Catalogue d'assets d'animation 2D versionnés, gel du storyboard et compatibilité (#119).

Compatibilité vérifiée : migration additive depuis le schéma v8, production classique strictement inchangée,
V2 (classique et animée), export de configuration, transfert de projet existant, export/import du catalogue.
Aucun réseau, aucun rendu, aucun fournisseur payant.
"""

import copy
import json
import sqlite3
from pathlib import Path

import pytest

from lody import db, project_transfer
from lody.animation.assets import (
    CATALOG_SCHEMA,
    AnimationAssetRepository,
    AssetNotFound,
    AssetValidationError,
    manifest_sha256,
)
from lody.animation.contract import AnimationValidationError, document_sha256, validate_document
from lody.animation.freeze import freeze_document, remap_assets
from lody.characters import CharacterRepository
from lody.generation.export import build_config_export
from lody.generation.models import ProductionStatus as S
from lody.generation.store import ProductionRepository
from lody.locations import LocationRepository
from lody.projects import ProjectRepository
from test.lody.test_generation_service import Env, _finished_v1, _prepare

DOCS = Path(__file__).resolve().parents[2] / "docs" / "animation"
CLOCK = "2026-11-10T20:00:00+00:00"
HEX = "a" * 64


def _load(name):
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def _manifest(**overrides):
    manifest = {"format": "svg", "width": 600, "height": 900, "anchor": {"x": 0.5, "y": 1.0},
                "parts": ["head", "mouth"], "expressions": ["neutre", "joie"], "gestures": ["salut"],
                "files": [{"role": "base", "path": "animation-assets/mimo/v1/mimo.svg", "sha256": HEX}]}
    manifest.update(overrides)
    return manifest


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "lody.sqlite3"


@pytest.fixture
def project(db_path):
    return ProjectRepository(db_path).create(name="Projet animé")


@pytest.fixture
def other_project(db_path):
    return ProjectRepository(db_path).create(name="Autre projet")


@pytest.fixture
def repo(db_path, project):
    return AnimationAssetRepository(db_path, clock=lambda: CLOCK)


def _import_example(repo, db_path, project):
    """Importe le catalogue de la maquette (avec le personnage « Mimo » et le lieu « Colline »)."""
    CharacterRepository(db_path).create(project.id, name="Mimo")
    LocationRepository(db_path).create(project.id, name="Colline")
    id_map, warnings = repo.import_catalog(project.id, _load("exemple-catalogue.json"))
    assert warnings == []
    return id_map


# -- migration ---------------------------------------------------------------------------------------------------
def test_upgrade_from_schema_v8_is_additive_and_idempotent(tmp_path):
    path = tmp_path / "v8.sqlite3"
    connection = sqlite3.connect(path)
    for target, step in db.MIGRATIONS:
        if target <= 8:
            step(connection) if callable(step) else connection.executescript(step)
    connection.execute("PRAGMA user_version = 8")
    connection.execute(
        "INSERT INTO projects (id, name, created_at, updated_at, language, format, content_type, text_provider,"
        " visual_provider, voice_provider, music_provider) VALUES ('prj_old', 'Ancien', 'x', 'x', 'fr-FR', '9:16',"
        " 'pedagogique', 'openai', 'openai_image', 'elevenlabs', 'none')")
    connection.execute(
        "INSERT INTO productions (id, project_id, root_production_id, version, subject, provider, idempotency_key,"
        " status, created_at, updated_at, script) VALUES ('prd_old', 'prj_old', 'prd_old', 1, 'Sujet', 'p', 'k',"
        " 'TERMINEE', 'x', 'x', 'Un script déjà produit.')")
    connection.commit()
    connection.close()

    old = ProductionRepository(path).get("prd_old")  # déclenche la migration v9
    assert old.animation == {} and not old.is_animated and old.script == "Un script déjà produit."
    AnimationAssetRepository(path)  # rejouer la migration est sans effet
    upgraded = sqlite3.connect(path)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 10
    tables = {row[0] for row in upgraded.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"animation_assets", "animation_asset_versions"} <= tables
    db._animation(upgraded)  # idempotente même rejouée à la main
    assert upgraded.execute("SELECT COUNT(*) FROM animation_assets").fetchone() == (0,)


# -- catalogue ---------------------------------------------------------------------------------------------------
def test_versions_are_numbered_and_immutable(repo, project, db_path):
    asset = repo.create(project.id, kind="character", name="Mimo — marionnette")
    v1 = repo.add_version(project.id, asset.id, _manifest(), notes="Première")
    v2 = repo.add_version(project.id, asset.id, _manifest(expressions=["neutre", "joie", "surprise"]))
    assert (v1.version, v2.version) == (1, 2) and v1.sha256 == manifest_sha256(_manifest())
    assert repo.get_version(project.id, asset.id).version == 2  # « latest »
    assert repo.get_version(project.id, asset.id, 1).manifest == _manifest()
    with pytest.raises(AssetNotFound):
        repo.get_version(project.id, asset.id, 3)
    connection = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError, match="immuable"):
        connection.execute("UPDATE animation_asset_versions SET manifest = '{}' WHERE asset_id = ?", (asset.id,))
    assert not hasattr(repo, "update_version")


def test_assets_link_to_existing_characters_and_locations_without_changing_them(repo, project, other_project, db_path):
    characters, locations = CharacterRepository(db_path), LocationRepository(db_path)
    mimo = characters.create(project.id, name="Mimo")
    before = characters.get(project.id, mimo.id)
    colline = locations.create(project.id, name="Colline")
    elsewhere = characters.create(other_project.id, name="Ailleurs")

    rig = repo.create(project.id, kind="character", name="Mimo rig", character_id=mimo.id)
    decor = repo.create(project.id, kind="background", name="Colline décor", location_id=colline.id)
    assert repo.for_character(project.id, mimo.id) == [rig]
    assert repo.for_location(project.id, colline.id) == [decor]
    assert characters.get(project.id, mimo.id) == before  # la fiche personnage n'est pas touchée

    with pytest.raises(AssetValidationError) as error:
        repo.create(project.id, kind="character", name="Intrus", character_id=elsewhere.id)
    assert "character_id" in error.value.errors
    with pytest.raises(AssetValidationError) as error:
        repo.create(project.id, kind="prop", name="Accessoire", character_id=mimo.id)
    assert "character_id" in error.value.errors
    with pytest.raises(AssetValidationError):
        repo.update(project.id, rig.id, kind="background")  # le type ne change jamais


def test_catalog_is_isolated_per_project(repo, project, other_project):
    asset = repo.create(project.id, kind="prop", name="Graphique")
    repo.add_version(project.id, asset.id, _manifest())
    assert repo.list_for_project(other_project.id) == []
    with pytest.raises(AssetNotFound):
        repo.get(other_project.id, asset.id)
    with pytest.raises(AssetNotFound):
        repo.get_version(other_project.id, asset.id)
    with pytest.raises(AssetValidationError):
        repo.create(project.id, kind="prop", name="GRAPHIQUE")  # nom unique par projet (casefold)
    assert repo.create(other_project.id, kind="prop", name="Graphique").project_id == other_project.id


@pytest.mark.parametrize("bad, field", [
    ({"files": [{"role": "base", "path": "../secrets/x.svg", "sha256": HEX}]}, "manifest.files[0].path"),
    ({"files": [{"role": "base", "path": "https://exemple.invalid/x.svg", "sha256": HEX}]}, "manifest.files[0].path"),
    ({"files": [{"role": "base", "path": "/abs/x.svg", "sha256": HEX}]}, "manifest.files[0].path"),
    ({"files": [{"role": "base", "path": "a/x.js", "sha256": HEX}]}, "manifest.files[0].path"),
    ({"format": "gif"}, "manifest.format"),
    ({"gestures": ["salut", "salut"]}, "manifest.gestures"),
    ({"anchor": {"x": 2, "y": 0}}, "manifest.anchor"),
    ({"script": "alert(1)"}, "manifest"),
])
def test_manifest_validation(repo, project, bad, field):
    asset = repo.create(project.id, kind="character", name="Mimo")
    with pytest.raises(AssetValidationError) as error:
        repo.add_version(project.id, asset.id, _manifest(**bad))
    assert field in error.value.errors


# -- gel du storyboard -------------------------------------------------------------------------------------------
def test_freeze_pins_latest_versions_and_detects_tampering(repo, db_path, project):
    id_map = _import_example(repo, db_path, project)
    document = remap_assets(_load("exemple-deux-scenes.json"), id_map)
    frozen = freeze_document(repo, project.id, document, clock=lambda: CLOCK)

    mimo = id_map["ast_00000000a001"]
    assert frozen["scenes"][0]["render"]["layers"][1]["objects"][0]["asset"] == {"id": mimo, "version": 2}
    pinned = {(entry["id"], entry["version"]) for entry in frozen["frozen"]["assets"]}
    assert (mimo, 2) in pinned and len(pinned) == 3
    assert frozen["frozen"]["sha256"] == document_sha256(frozen) and frozen["frozen"]["at"] == CLOCK
    assert validate_document(frozen, require_pinned=True) == frozen

    # une nouvelle version publiée ensuite ne change pas le document figé
    repo.add_version(project.id, mimo, _manifest(expressions=["neutre", "joie", "concentre", "clin-oeil"],
                                                 gestures=["salut", "designer", "hochement"]))
    assert validate_document(frozen, require_pinned=True)["scenes"][0]["render"]["layers"][1]["objects"][0][
        "asset"]["version"] == 2

    tampered = copy.deepcopy(frozen)
    tampered["scenes"][0]["intent"]["narration"] = "Texte retouché après le gel."
    with pytest.raises(AnimationValidationError) as error:
        validate_document(tampered, require_pinned=True)
    assert "frozen.sha256" in error.value.errors


def test_freeze_checks_names_kinds_and_project(repo, db_path, project, other_project):
    id_map = _import_example(repo, db_path, project)
    document = remap_assets(_load("exemple-deux-scenes.json"), id_map)

    bad = copy.deepcopy(document)
    bad["scenes"][0]["render"]["layers"][1]["objects"][0]["gestures"][0]["name"] = "pirouette"
    with pytest.raises(AnimationValidationError) as error:
        freeze_document(repo, project.id, bad)
    assert "scenes[0].render.layers[1].objects[0].gestures[0].name" in error.value.errors

    bad = copy.deepcopy(document)
    bad["scenes"][0]["render"]["layers"][1]["objects"][0]["asset"]["version"] = 1  # v1 n'a pas « clin-oeil »
    bad["scenes"][1]["render"]["layers"][1]["objects"][1]["asset"]["version"] = 1
    with pytest.raises(AnimationValidationError) as error:
        freeze_document(repo, project.id, bad)
    assert "scenes[1].render.layers[1].objects[1].expressions[1].name" in error.value.errors

    bad = copy.deepcopy(document)
    bad["scenes"][1]["render"]["fallback"]["asset"] = {"id": id_map["ast_00000000a003"], "version": 1}
    with pytest.raises(AnimationValidationError) as error:
        freeze_document(repo, project.id, bad)
    assert "scenes[1].render.fallback.asset" in error.value.errors

    with pytest.raises(AnimationValidationError):  # assets d'un autre projet : introuvables
        freeze_document(AnimationAssetRepository(db_path), other_project.id, document)

    repo.deactivate(project.id, id_map["ast_00000000a003"])
    with pytest.raises(AnimationValidationError) as error:
        freeze_document(repo, project.id, document)
    assert any("désactivé" in message for message in error.value.errors.values())


# -- export / import ---------------------------------------------------------------------------------------------
def test_catalog_export_import_round_trip_keeps_versions_and_hashes(repo, db_path, project, other_project):
    id_map = _import_example(repo, db_path, project)
    frozen = freeze_document(repo, project.id, remap_assets(_load("exemple-deux-scenes.json"), id_map))
    exported = repo.export_catalog(project.id)
    assert exported["schema"] == CATALOG_SCHEMA and len(exported["assets"]) == 3
    assert json.loads(json.dumps(exported)) == exported  # JSON pur

    CharacterRepository(db_path).create(other_project.id, name="mimo")  # liens retrouvés par nom (casse ignorée)
    new_map, warnings = repo.import_catalog(other_project.id, exported)
    assert any("Colline" in warning for warning in warnings)  # lieu absent : lien abandonné, signalé
    rigs = [a for a in repo.export_catalog(other_project.id)["assets"] if a["kind"] == "character"]
    assert [a["character"] for a in rigs] == ["mimo"]

    refrozen = freeze_document(repo, other_project.id, remap_assets(frozen, new_map))
    # mêmes versions, mêmes manifestes : contenu identique hors identifiants d'assets
    assert sorted((e["version"], e["sha256"], e["kind"]) for e in refrozen["frozen"]["assets"]) == \
        sorted((e["version"], e["sha256"], e["kind"]) for e in frozen["frozen"]["assets"])
    assert all(e["id"] in new_map.values() for e in refrozen["frozen"]["assets"])

    tampered = copy.deepcopy(exported)
    tampered["assets"][0]["versions"][0]["manifest"]["width"] = 1
    with pytest.raises(AssetValidationError) as error:
        repo.import_catalog(other_project.id, tampered)
    assert "assets[0].versions[0].sha256" in error.value.errors


def test_existing_project_transfer_is_unchanged_by_the_catalog(repo, db_path, project):
    """Le transfert de projet (#44/#66) n'exporte pas le catalogue et son format reste en version 1."""
    _import_example(repo, db_path, project)
    characters = CharacterRepository(db_path).list_for_project(project.id)
    locations = LocationRepository(db_path).list_for_project(project.id)
    exported = project_transfer.export_project(ProjectRepository(db_path).get(project.id), characters, locations)
    assert set(exported) == {"schema_version", "project", "characters", "locations"}
    assert exported["schema_version"] == 1
    preview = project_transfer.preview_import(json.dumps(exported), existing_project_names=[project.name])
    assert preview.is_valid and preview.character_names == ["Mimo"] and preview.location_names == ["Colline"]


# -- production : classique inchangée, animée figée, V2 ----------------------------------------------------------
def test_classic_production_and_its_v2_never_carry_animation(tmp_path):
    env = Env(tmp_path)
    v1 = _finished_v1(env)
    assert v1.animation == {} and not v1.is_animated
    v2 = env.service.create_v2(v1.id)
    assert v2.animation == {}
    config = build_config_export(v1)
    assert config["storyboard_anime"].startswith("aucun")


def test_animated_production_is_frozen_immutable_and_inherited_by_v2(tmp_path):
    env = Env(tmp_path)
    path = env.path
    repo = AnimationAssetRepository(path, clock=lambda: CLOCK)
    project = env.project
    id_map = _import_example(repo, path, project)
    frozen = freeze_document(repo, project.id, remap_assets(_load("exemple-deux-scenes.json"), id_map))

    draft = _prepare(env)
    store = env.service.repo
    working = remap_assets(_load("exemple-deux-scenes.json"), id_map)
    with pytest.raises(AnimationValidationError):  # seul un document FIGÉ entre en base
        store.transition(draft.id, [S.EN_ATTENTE_CONFIRMATION], animation=working)
    assert store.transition(draft.id, [S.EN_ATTENTE_CONFIRMATION], animation=frozen)
    with pytest.raises(ValueError, match="immuable"):
        store.update(draft.id, animation={})

    launched = env.service.confirm(draft.id)
    env.connector.queue(env.connector.done("task-0001"))
    finished = env.service.refresh(launched.id)
    assert finished.status is S.TERMINEE and finished.animation == frozen and finished.is_animated
    with pytest.raises(ValueError, match="immuable"):
        store.transition(finished.id, [S.TERMINEE], animation={})

    # une nouvelle version d'asset ne touche ni la V1 ni la V2 qui en hérite
    repo.add_version(project.id, id_map["ast_00000000a001"], _manifest())
    v2 = env.service.create_v2(finished.id)
    assert v2.animation == frozen and store.get(finished.id).animation == frozen
    config = build_config_export(finished)
    assert config["storyboard_anime"]["frozen"]["sha256"] == frozen["frozen"]["sha256"]
