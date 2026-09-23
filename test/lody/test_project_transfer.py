"""Export / import complet de configuration de projet (#44) : format versionné, validation, aperçu sans
écriture, création atomique, isolation stricte, aucun secret ni identifiant interne. Aucun fournisseur,
aucun appel réseau, aucun coût."""

from __future__ import annotations

import json
import sqlite3

import pytest

from lody.characters import CharacterRepository
from lody.locations import LocationRepository
from lody.project_transfer import (
    MAX_FILE_BYTES,
    MAX_ITEMS,
    SCHEMA_VERSION,
    TransferError,
    blank_template,
    commit_import,
    export_project,
    preview_import,
)
from lody.projects import ProjectRepository


@pytest.fixture
def projects(tmp_path):
    return ProjectRepository(tmp_path / "lody.sqlite3")


@pytest.fixture
def characters(tmp_path, projects):
    return CharacterRepository(tmp_path / "lody.sqlite3")


@pytest.fixture
def locations(tmp_path, projects):
    return LocationRepository(tmp_path / "lody.sqlite3")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "lody.sqlite3"


@pytest.fixture
def project(projects):
    return projects.create(name="Projet source", description="Un projet complet", tone="Clair et précis",
                           visual_style="Univers sombre et cyan", voice_name="Kev", music_provider="library",
                           settings={"brief": {"audience": "Débutants", "structure": ["Accroche", "Idée"]}})


def _payload(project_name="Import Test", **project_overrides):
    return {"schema_version": SCHEMA_VERSION,
            "project": {"name": project_name, **project_overrides},
            "characters": [], "locations": []}


# -- export ---------------------------------------------------------------------------------------------------------
def test_export_of_an_empty_project_works_and_has_empty_lists(project):
    payload = export_project(project, [], [])
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["characters"] == [] and payload["locations"] == []
    assert payload["project"]["name"] == "Projet source"


def test_export_with_several_characters_and_locations(project, characters, locations):
    characters.create(project.id, name="Gaston", role="Guide", is_primary=True)
    characters.create(project.id, name="Zoé", role="Alliée")
    locations.create(project.id, name="Studio", location_type="Intérieur", is_primary=True)
    locations.create(project.id, name="Extérieur", location_type="Extérieur")
    payload = export_project(project, characters.list_for_project(project.id), locations.list_for_project(project.id))
    assert [c["name"] for c in payload["characters"]] == ["Gaston", "Zoé"]
    assert [loc["name"] for loc in payload["locations"]] == ["Studio", "Extérieur"]
    assert payload["characters"][0]["is_primary"] is True


def test_export_never_contains_secrets_or_internal_identifiers(project, characters, locations):
    characters.create(project.id, name="Gaston", external_voice_id="21m00Tcm4TlvDq8ikWAM")
    locations.create(project.id, name="Studio")
    payload = export_project(project, characters.list_for_project(project.id), locations.list_for_project(project.id))
    text = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("project_id", project.id, "created_at", "updated_at", "\"id\":"):
        assert forbidden not in text, forbidden
    # aucun champ ne ressemble à une clé API/jeton, y compris dans les personnages/lieux
    from lody.secrets_guard import find_secret_path

    assert find_secret_path(payload) is None


def test_export_project_without_any_character_or_location_round_trips(project):
    payload = export_project(project, [], [])
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=["Autre projet"])
    assert preview.is_valid and preview.character_names == [] and preview.location_names == []


# -- modèle téléchargeable --------------------------------------------------------------------------------------------
def test_blank_template_is_valid_json_with_schema_version_1_and_two_characters_and_two_locations():
    template = blank_template()
    assert template["schema_version"] == 1
    assert len(template["characters"]) == 2 and len(template["locations"]) == 2
    encoded = json.dumps(template, ensure_ascii=False)
    assert json.loads(encoded) == template  # syntaxiquement valide, round-trip JSON identique


def test_blank_template_is_importable_once_downloaded(db_path):
    template = blank_template()
    preview = preview_import(json.dumps(template).encode("utf-8"), existing_project_names=[])
    assert preview.is_valid, preview.errors
    created = commit_import(db_path, preview)
    assert created.name == template["project"]["name"]


def test_blank_template_contains_no_real_secret():
    from lody.secrets_guard import find_secret_path

    assert find_secret_path(blank_template()) is None


# -- aperçu sans écriture -------------------------------------------------------------------------------------------
def test_preview_does_not_write_anything(db_path):
    projects = ProjectRepository(db_path)
    before = projects.count()
    preview_import(json.dumps(_payload()).encode("utf-8"), existing_project_names=[])
    assert projects.count() == before


def test_preview_shows_name_key_settings_character_and_location_counts_and_names():
    payload = _payload("Aperçu", description="Une description", tone="Décontracté")
    payload["characters"] = [{"name": "Gaston"}, {"name": "Zoé"}]
    payload["locations"] = [{"name": "Studio"}]
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=[])
    assert preview.is_valid
    assert preview.project_name == "Aperçu"
    assert preview.project_summary["description"] == "Une description"
    assert preview.character_names == ["Gaston", "Zoé"]
    assert preview.location_names == ["Studio"]
    assert preview.errors == []


# -- import confirmé : nouveau projet, nouveaux ids ------------------------------------------------------------------
def test_confirmed_import_creates_a_new_project(db_path):
    projects = ProjectRepository(db_path)
    payload = _payload("Nouveau projet importé", visual_style="Style importé")
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=[p.name for p in projects.list_projects()])
    created = commit_import(db_path, preview)
    assert created.name == "Nouveau projet importé" and created.visual_style == "Style importé"
    assert projects.get(created.id) == created


def test_new_ids_and_correct_project_id_relations_for_characters_and_locations(db_path):
    characters, locations = CharacterRepository(db_path), LocationRepository(db_path)
    payload = _payload("Avec enfants")
    payload["characters"] = [{"name": "Gaston", "role": "Guide"}]
    payload["locations"] = [{"name": "Studio", "location_type": "Intérieur"}]
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=[])
    created = commit_import(db_path, preview)
    child_characters = characters.list_for_project(created.id)
    child_locations = locations.list_for_project(created.id)
    assert len(child_characters) == 1 and child_characters[0].id.startswith("chr_")
    assert child_characters[0].project_id == created.id and child_characters[0].name == "Gaston"
    assert len(child_locations) == 1 and child_locations[0].id.startswith("loc_")
    assert child_locations[0].project_id == created.id


def test_round_trip_export_then_import_keeps_functional_settings_but_creates_new_ids(db_path, project, characters, locations):
    characters.create(project.id, name="Gaston", role="Guide", personality="Bavard", is_primary=True)
    locations.create(project.id, name="Studio", location_type="Intérieur", continuity_notes="Toujours le même angle")
    payload = export_project(project, characters.list_for_project(project.id), locations.list_for_project(project.id))
    preview = preview_import(json.dumps(payload).encode("utf-8"),
                             existing_project_names=[p.name for p in ProjectRepository(characters.db_path).list_projects()])
    created = commit_import(characters.db_path, preview)
    assert created.id != project.id
    assert created.visual_style == project.visual_style and created.settings == project.settings
    new_character = characters.list_for_project(created.id)[0]
    old_character = characters.list_for_project(project.id)[0]
    assert new_character.id != old_character.id
    assert (new_character.name, new_character.role, new_character.personality) == ("Gaston", "Guide", "Bavard")
    new_location = locations.list_for_project(created.id)[0]
    old_location = locations.list_for_project(project.id)[0]
    assert new_location.id != old_location.id and new_location.continuity_notes == "Toujours le même angle"


# -- JSON invalide, version inconnue, champs manquants/incorrects -----------------------------------------------------
def test_invalid_json_is_refused_with_a_clear_issue():
    preview = preview_import(b"{not valid json")
    assert not preview.is_valid
    assert any("JSON invalide" in issue.message for issue in preview.errors)


def test_unknown_schema_version_is_refused_with_a_clear_message():
    payload = _payload()
    payload["schema_version"] = 99
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid
    assert any(issue.path == "schema_version" and "99" in issue.message for issue in preview.errors)


def test_missing_schema_version_is_refused():
    payload = _payload()
    del payload["schema_version"]
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid and any(issue.path == "schema_version" for issue in preview.errors)


def test_missing_required_project_name_is_refused_with_precise_path():
    payload = {"schema_version": SCHEMA_VERSION, "project": {}, "characters": [], "locations": []}
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid
    assert any(issue.path == "project.name" for issue in preview.errors)


def test_missing_project_section_entirely_is_refused():
    payload = {"schema_version": SCHEMA_VERSION, "characters": [], "locations": []}
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid and any(issue.path == "project" for issue in preview.errors)


def test_invalid_types_are_refused_with_precise_paths():
    payload = _payload("Types invalides", platforms="pas-une-liste")
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid
    assert any(issue.path == "project.platforms" for issue in preview.errors)

    payload2 = _payload("Types invalides 2")
    payload2["characters"] = [{"name": "Test", "is_primary": "oui"}]
    preview2 = preview_import(json.dumps(payload2).encode("utf-8"))
    assert not preview2.is_valid
    assert any(issue.path == "characters[0].is_primary" for issue in preview2.errors)

    payload3 = _payload("Types invalides 3")
    payload3["locations"] = "pas-une-liste"
    preview3 = preview_import(json.dumps(payload3).encode("utf-8"))
    assert not preview3.is_valid and any(issue.path == "locations" for issue in preview3.errors)


def test_field_length_limits_match_the_web_form_limits():
    payload = _payload("x" * 200)  # NAME_MAX du projet est 80
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid and any(issue.path == "project.name" for issue in preview.errors)

    payload2 = _payload("Longueur personnage")
    payload2["characters"] = [{"name": "x" * 200}]
    preview2 = preview_import(json.dumps(payload2).encode("utf-8"))
    assert not preview2.is_valid and any(issue.path == "characters[0].name" for issue in preview2.errors)


def test_duplicate_character_names_within_the_import_are_refused_case_and_accent_insensitively():
    payload = _payload("Doublons perso")
    payload["characters"] = [{"name": "Gaston"}, {"name": "GASTON"}]
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid
    assert any(issue.path == "characters[1].name" for issue in preview.errors)


def test_duplicate_location_names_within_the_import_are_refused():
    payload = _payload("Doublons lieu")
    payload["locations"] = [{"name": "Studio"}, {"name": "studio"}]
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid and any(issue.path == "locations[1].name" for issue in preview.errors)


def test_optional_fields_default_correctly_when_absent():
    payload = _payload("Champs par défaut")
    payload["characters"] = [{"name": "Sans autre champ"}]
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert preview.is_valid
    character = preview.clean["characters"][0]
    assert character["role"] == "" and character["is_primary"] is False and character["is_active"] is True


def test_unknown_fields_are_ignored_and_reported_as_a_non_blocking_warning():
    payload = _payload("Champs inconnus")
    payload["project"]["mystery_field"] = "valeur"
    payload["characters"] = [{"name": "Gaston", "another_mystery": "x"}]
    payload["surprise_top_level"] = True
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert preview.is_valid  # politique documentée : ignoré, jamais bloquant
    assert any("project.mystery_field" in w for w in preview.warnings)
    assert any("characters[0].another_mystery" in w for w in preview.warnings)
    assert any("surprise_top_level" in w for w in preview.warnings)


def test_file_too_large_is_refused():
    payload = _payload("Trop gros")
    payload["project"]["description"] = "x" * (MAX_FILE_BYTES + 10)
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid
    assert any("volumineux" in issue.message for issue in preview.errors)


def test_too_many_characters_is_refused():
    payload = _payload("Trop de personnages")
    payload["characters"] = [{"name": f"Personnage {i}"} for i in range(MAX_ITEMS + 1)]
    preview = preview_import(json.dumps(payload).encode("utf-8"))
    assert not preview.is_valid and any(issue.path == "characters" for issue in preview.errors)


# -- échec atomique -----------------------------------------------------------------------------------------------------
def test_atomic_failure_leaves_no_partial_project_in_sqlite(db_path):
    projects = ProjectRepository(db_path)
    payload = _payload("Course concurrente")
    payload["characters"] = [{"name": "Un personnage valide"}]
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=[])
    projects.create(name="Course concurrente")  # un autre import a pris ce nom entre l'aperçu et la confirmation
    with pytest.raises(TransferError):
        commit_import(db_path, preview)
    connection = sqlite3.connect(db_path)
    assert connection.execute("SELECT COUNT(*) FROM projects WHERE name = 'Course concurrente'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0  # aucune écriture partielle


def test_commit_refuses_an_invalid_preview_without_touching_the_database(db_path):
    projects = ProjectRepository(db_path)
    invalid_preview = preview_import(b"{not json")
    with pytest.raises(TransferError):
        commit_import(db_path, invalid_preview)
    assert projects.count() == 0


# -- isolation stricte entre projets --------------------------------------------------------------------------------
def test_imported_project_never_leaks_into_or_from_another_project(db_path, project, characters, locations):
    characters.create(project.id, name="Perso original")
    other = ProjectRepository(db_path).create(name="Autre projet préexistant", visual_style="Style autre")
    payload = _payload("Import isolé")
    payload["characters"] = [{"name": "Perso importé"}]
    preview = preview_import(json.dumps(payload).encode("utf-8"),
                             existing_project_names=[p.name for p in ProjectRepository(db_path).list_projects()])
    created = commit_import(db_path, preview)
    assert [c.name for c in characters.list_for_project(created.id)] == ["Perso importé"]
    assert [c.name for c in characters.list_for_project(project.id)] == ["Perso original"]
    assert [c.name for c in characters.list_for_project(other.id)] == []
    assert created.visual_style != other.visual_style


# -- projet existant jamais écrasé ----------------------------------------------------------------------------------
def test_existing_project_with_the_same_name_is_never_overwritten_a_distinct_name_is_used(db_path):
    projects = ProjectRepository(db_path)
    existing = projects.create(name="Mon Projet", visual_style="Style original")
    payload = _payload("Mon Projet", visual_style="Style importé, différent")
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=[p.name for p in projects.list_projects()])
    assert preview.name_was_renamed and preview.project_name != "Mon Projet"
    created = commit_import(db_path, preview)
    assert created.id != existing.id
    assert projects.get(existing.id).name == "Mon Projet" and projects.get(existing.id).visual_style == "Style original"
    assert created.visual_style == "Style importé, différent"


def test_existing_project_name_matched_case_insensitively_and_accents_included(db_path):
    projects = ProjectRepository(db_path)
    projects.create(name="Élan Studio")
    payload = _payload("élan studio")
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=[p.name for p in projects.list_projects()])
    assert preview.name_was_renamed


# -- aucune régression sur la création classique d'un projet ----------------------------------------------------------
def test_classic_project_creation_is_unaffected_by_the_transfer_module(db_path):
    projects = ProjectRepository(db_path)
    before_count = projects.count()
    created = projects.create(name="Création classique", visual_style="Style habituel")
    assert projects.count() == before_count + 1
    assert projects.get(created.id).name == "Création classique"


def test_importing_never_mutates_an_unrelated_existing_projects_characters_or_locations(db_path, project, characters, locations):
    characters.create(project.id, name="Gardien")
    payload = _payload("Ne touche personne d'autre")
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=[])
    commit_import(db_path, preview)
    assert [c.name for c in characters.list_for_project(project.id)] == ["Gardien"]
