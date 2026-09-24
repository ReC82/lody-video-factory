"""Import/export partiel de personnages et de lieux dans un projet EXISTANT (#57) : zéro/un/plusieurs
éléments, round-trip, isolation entre projets, conflits de noms, élément principal, échec atomique,
mauvais resource_type/version/JSON, champs inconnus/optionnels, absence de secrets et d'identifiants
internes, garde-fou de couverture partagé avec #44. Aucun fournisseur, aucun appel réseau, aucun coût."""

from __future__ import annotations

import json

import pytest

from lody.characters import CharacterRepository
from lody.locations import LocationRepository
from lody.project_transfer import CHARACTER_FIELDS as PROJECT_CHARACTER_FIELDS
from lody.project_transfer import LOCATION_FIELDS as PROJECT_LOCATION_FIELDS
from lody.project_transfer import MAX_FILE_BYTES, MAX_ITEMS, SCHEMA_VERSION
from lody.projects import ProjectRepository
from lody.resource_transfer import (
    RESOURCE_CHARACTERS,
    RESOURCE_LOCATIONS,
    TransferError,
    character_template,
    commit_character_import,
    commit_location_import,
    export_characters,
    export_locations,
    location_template,
    preview_character_import,
    preview_location_import,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "lody.sqlite3"


@pytest.fixture
def projects(db_path):
    return ProjectRepository(db_path)


@pytest.fixture
def characters(db_path, projects):
    return CharacterRepository(db_path)


@pytest.fixture
def locations(db_path, projects):
    return LocationRepository(db_path)


@pytest.fixture
def project(projects):
    return projects.create(name="Projet cible", visual_style="Style existant")


def _char_payload(*names, resource_type=RESOURCE_CHARACTERS, version=SCHEMA_VERSION, **item_overrides):
    return {"schema_version": version, "resource_type": resource_type,
            "characters": [{"name": name, **item_overrides} for name in names]}


def _loc_payload(*names, resource_type=RESOURCE_LOCATIONS, version=SCHEMA_VERSION, **item_overrides):
    return {"schema_version": version, "resource_type": resource_type,
            "locations": [{"name": name, **item_overrides} for name in names]}


# -- export : zéro, un, plusieurs, sélection partielle ---------------------------------------------------------------
def test_export_characters_empty(project):
    payload = export_characters([])
    assert payload == {"schema_version": SCHEMA_VERSION, "resource_type": RESOURCE_CHARACTERS, "characters": []}


def test_export_characters_several(project, characters):
    characters.create(project.id, name="Gaston", role="Guide")
    characters.create(project.id, name="Zoé", role="Alliée")
    payload = export_characters(characters.list_for_project(project.id))
    assert [c["name"] for c in payload["characters"]] == ["Gaston", "Zoé"]


def test_export_partial_selection_only_exports_what_is_given(project, characters):
    characters.create(project.id, name="Gaston")
    zoe = characters.create(project.id, name="Zoé")
    payload = export_characters([zoe])  # sélection partielle : un seul des deux personnages du projet
    assert [c["name"] for c in payload["characters"]] == ["Zoé"]


def test_export_locations_empty_and_several(project, locations):
    assert export_locations([])["locations"] == []
    locations.create(project.id, name="Studio")
    payload = export_locations(locations.list_for_project(project.id))
    assert [loc["name"] for loc in payload["locations"]] == ["Studio"]


def test_export_never_contains_internal_ids_or_timestamps(project, characters):
    characters.create(project.id, name="Gaston", external_voice_id="21m00Tcm4TlvDq8ikWAM")
    payload = export_characters(characters.list_for_project(project.id))
    dumped = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("project_id", project.id, "created_at", "updated_at", '"id":'):
        assert forbidden not in dumped, forbidden


def test_export_never_contains_a_secret(project, characters):
    from lody.secrets_guard import find_secret_path

    characters.create(project.id, name="Gaston", external_voice_id="21m00Tcm4TlvDq8ikWAM")
    payload = export_characters(characters.list_for_project(project.id))
    assert find_secret_path(payload) is None


# -- modèle : même source de vérité que #44, réimportable -----------------------------------------------------------
def test_character_template_has_two_examples_and_matches_the_import_field_set():
    template = character_template()
    assert template["schema_version"] == 1 and template["resource_type"] == RESOURCE_CHARACTERS
    assert len(template["characters"]) == 2
    assert set(template["characters"][0]) == set(PROJECT_CHARACTER_FIELDS)


def test_location_template_has_two_examples_and_matches_the_import_field_set():
    template = location_template()
    assert len(template["locations"]) == 2
    assert set(template["locations"][0]) == set(PROJECT_LOCATION_FIELDS)


def test_character_template_is_importable_as_is(db_path, project):
    template = character_template()
    preview = preview_character_import(json.dumps(template).encode("utf-8"), existing_names=[])
    assert preview.is_valid, preview.errors
    created = commit_character_import(db_path, project.id, preview)
    assert [c.name for c in created] == ["Personnage principal", "Personnage secondaire"]


def test_location_template_is_importable_as_is(db_path, project):
    template = location_template()
    preview = preview_location_import(json.dumps(template).encode("utf-8"), existing_names=[])
    assert preview.is_valid
    created = commit_location_import(db_path, project.id, preview)
    assert [loc.name for loc in created] == ["Lieu principal", "Lieu secondaire"]


# -- import : zéro, un, plusieurs, dans un projet DÉJÀ existant --------------------------------------------------------
def test_import_zero_characters_is_valid_and_creates_nothing(db_path, project, characters):
    payload = {"schema_version": 1, "resource_type": RESOURCE_CHARACTERS, "characters": []}
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert preview.is_valid
    created = commit_character_import(db_path, project.id, preview)
    assert created == [] and characters.count_for_project(project.id) == 0


def test_import_one_character(db_path, project, characters):
    preview = preview_character_import(json.dumps(_char_payload("Gaston")).encode("utf-8"), existing_names=[])
    assert preview.is_valid
    created = commit_character_import(db_path, project.id, preview)
    assert len(created) == 1 and created[0].name == "Gaston" and created[0].project_id == project.id
    assert created[0].id.startswith("chr_")


def test_import_several_characters_at_once(db_path, project, characters):
    preview = preview_character_import(json.dumps(_char_payload("Gaston", "Zoé", "Ana")).encode("utf-8"),
                                       existing_names=[])
    created = commit_character_import(db_path, project.id, preview)
    assert {c.name for c in created} == {"Gaston", "Zoé", "Ana"}


def test_import_adds_to_a_project_that_already_has_data(db_path, project, characters):
    characters.create(project.id, name="Déjà là")
    preview = preview_character_import(json.dumps(_char_payload("Nouveau")).encode("utf-8"),
                                       existing_names=[c.name for c in characters.list_for_project(project.id)])
    commit_character_import(db_path, project.id, preview)
    names = {c.name for c in characters.list_for_project(project.id)}
    assert names == {"Déjà là", "Nouveau"}


def test_import_never_creates_a_new_project_or_touches_other_project_fields(db_path, project, projects, characters):
    before_count = projects.count()
    preview = preview_character_import(json.dumps(_char_payload("Gaston")).encode("utf-8"), existing_names=[])
    commit_character_import(db_path, project.id, preview)
    assert projects.count() == before_count  # aucun nouveau projet
    assert projects.get(project.id).visual_style == "Style existant"  # aucun autre champ touché


def test_import_locations_one_and_several(db_path, project, locations):
    preview = preview_location_import(json.dumps(_loc_payload("Studio")).encode("utf-8"), existing_names=[])
    created = commit_location_import(db_path, project.id, preview)
    assert len(created) == 1 and created[0].name == "Studio" and created[0].project_id == project.id
    assert created[0].id.startswith("loc_")

    preview2 = preview_location_import(json.dumps(_loc_payload("Extérieur", "Intérieur")).encode("utf-8"),
                                       existing_names=["Studio"])
    created2 = commit_location_import(db_path, project.id, preview2)
    assert {loc.name for loc in created2} == {"Extérieur", "Intérieur"}


# -- round-trip export puis réimport ----------------------------------------------------------------------------------
def test_round_trip_character_export_then_import_preserves_functional_fields(db_path, project, characters):
    characters.create(project.id, name="Gaston", role="Guide", personality="Bavard",
                      visual_description="Chapeau", reference_prompt="portrait", speech_style="Phrases courtes",
                      voice_provider="elevenlabs", voice_name="Voix Gaston", external_voice_id="21m00Tcm4TlvDq8ikWAM",
                      permanent_elements="Porte un chapeau", continuity_notes="Toujours enjoué",
                      is_primary=True, is_active=True)
    payload = export_characters(characters.list_for_project(project.id))

    other = ProjectRepository(db_path).create(name="Projet destination")
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    created = commit_character_import(db_path, other.id, preview)
    assert len(created) == 1
    new_character = created[0]
    original = characters.list_for_project(project.id)[0]
    assert new_character.id != original.id and new_character.project_id == other.id
    for f in PROJECT_CHARACTER_FIELDS:
        if f == "name":
            continue
        assert getattr(new_character, f) == getattr(original, f), f


def test_round_trip_location_export_then_import_preserves_functional_fields(db_path, project, locations):
    locations.create(project.id, name="Studio", location_type="Intérieur", description="Studio moderne",
                     reference_prompt="plan large", continuity_notes="Même agencement",
                     is_primary=True, is_active=True)
    payload = export_locations(locations.list_for_project(project.id))
    other = ProjectRepository(db_path).create(name="Projet destination lieux")
    preview = preview_location_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    created = commit_location_import(db_path, other.id, preview)
    original = locations.list_for_project(project.id)[0]
    for f in PROJECT_LOCATION_FIELDS:
        if f == "name":
            continue
        assert getattr(created[0], f) == getattr(original, f), f


# -- isolation entre projets -----------------------------------------------------------------------------------------
def test_import_into_project_a_never_creates_anything_in_project_b(db_path, project, projects, characters):
    other = projects.create(name="Projet B")
    preview = preview_character_import(json.dumps(_char_payload("Gaston")).encode("utf-8"), existing_names=[])
    commit_character_import(db_path, project.id, preview)
    assert characters.count_for_project(project.id) == 1
    assert characters.count_for_project(other.id) == 0


def test_two_projects_can_import_a_character_with_the_same_name_independently(db_path, projects, characters):
    a = projects.create(name="Projet A")
    b = projects.create(name="Projet B")
    preview_a = preview_character_import(json.dumps(_char_payload("Gaston")).encode("utf-8"), existing_names=[])
    preview_b = preview_character_import(json.dumps(_char_payload("Gaston")).encode("utf-8"), existing_names=[])
    commit_character_import(db_path, a.id, preview_a)
    commit_character_import(db_path, b.id, preview_b)
    assert characters.list_for_project(a.id)[0].name == characters.list_for_project(b.id)[0].name == "Gaston"
    assert characters.list_for_project(a.id)[0].id != characters.list_for_project(b.id)[0].id


# -- conflit de nom avec le projet cible -------------------------------------------------------------------------------
def test_name_conflict_with_existing_character_is_blocking(db_path, project, characters):
    characters.create(project.id, name="Gaston")
    preview = preview_character_import(json.dumps(_char_payload("Gaston")).encode("utf-8"),
                                       existing_names=["Gaston"])
    assert not preview.is_valid
    assert preview.conflicts == ["Gaston"]
    assert characters.count_for_project(project.id) == 1  # rien ajouté


def test_name_conflict_is_case_and_accent_insensitive(db_path, project, characters):
    characters.create(project.id, name="Élan")
    preview = preview_character_import(json.dumps(_char_payload("élan")).encode("utf-8"), existing_names=["Élan"])
    assert not preview.is_valid and preview.conflicts == ["élan"]


def test_duplicate_names_within_the_same_file_are_still_rejected_like_44(db_path, project):
    preview = preview_character_import(json.dumps(_char_payload("Gaston", "GASTON")).encode("utf-8"),
                                       existing_names=[])
    assert not preview.is_valid
    assert any(issue.path == "characters[1].name" for issue in preview.errors)


def test_location_name_conflict_is_blocking(db_path, project, locations):
    locations.create(project.id, name="Studio")
    preview = preview_location_import(json.dumps(_loc_payload("Studio")).encode("utf-8"), existing_names=["Studio"])
    assert not preview.is_valid and preview.conflicts == ["Studio"]


# -- élément principal : annoncé, jamais bloquant (le modèle actuel ne l'interdit pas) ----------------------------------
def test_preview_warns_when_import_would_create_more_than_one_primary(db_path, project, characters):
    characters.create(project.id, name="Déjà principal", is_primary=True)
    payload = _char_payload("Nouveau principal", is_primary=True)
    preview = preview_character_import(json.dumps(payload).encode("utf-8"),
                                       existing_names=["Déjà principal"], existing_primary_count=1)
    assert preview.is_valid  # avertissement seulement : le modèle n'interdit pas plusieurs "principal"
    assert any("principal" in w.lower() for w in preview.warnings)


def test_preview_does_not_warn_when_at_most_one_primary_results(db_path, project):
    payload = _char_payload("Un seul principal", is_primary=True)
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[],
                                       existing_primary_count=0)
    assert preview.is_valid
    assert not any("principal" in w.lower() for w in preview.warnings)


# -- transaction annulée si un élément du lot est invalide -----------------------------------------------------------
def test_preview_rejects_the_whole_batch_if_one_item_is_invalid(db_path, project, characters):
    payload = _char_payload("Bon nom") | {"characters": [{"name": "Bon nom"}, {"name": "x"}]}  # "x" trop court
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert not preview.is_valid
    assert any(issue.path == "characters[1].name" for issue in preview.errors)


def test_commit_refuses_an_invalid_preview_without_writing_anything(db_path, project, characters):
    invalid_preview = preview_character_import(b"{not json", existing_names=[])
    with pytest.raises(TransferError):
        commit_character_import(db_path, project.id, invalid_preview)
    assert characters.count_for_project(project.id) == 0


def test_commit_atomic_failure_on_concurrent_name_leaves_no_partial_write(db_path, project, characters):
    preview = preview_character_import(json.dumps(_char_payload("Un", "Deux")).encode("utf-8"), existing_names=[])
    assert preview.is_valid
    characters.create(project.id, name="Un")  # course concurrente : pris entre l'aperçu et la confirmation
    with pytest.raises(TransferError):
        commit_character_import(db_path, project.id, preview)
    remaining = characters.list_for_project(project.id)
    assert [c.name for c in remaining] == ["Un"]  # ni "Un" (déjà là) dupliqué, ni "Deux" créé seul


# -- mauvais resource_type, version, JSON, types, taille --------------------------------------------------------------
def test_characters_file_rejected_when_imported_as_locations():
    payload = _char_payload("Gaston")  # resource_type == "characters"
    preview = preview_location_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert not preview.is_valid
    assert any(issue.path == "resource_type" for issue in preview.errors)


def test_locations_file_rejected_when_imported_as_characters():
    payload = _loc_payload("Studio")
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert not preview.is_valid
    assert any(issue.path == "resource_type" for issue in preview.errors)


def test_unknown_schema_version_is_refused():
    payload = _char_payload("Gaston", version=99)
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert not preview.is_valid
    assert any(issue.path == "schema_version" and "99" in issue.message for issue in preview.errors)


def test_invalid_json_is_refused_with_a_clear_message():
    preview = preview_character_import(b"{not valid json", existing_names=[])
    assert not preview.is_valid
    assert any("JSON invalide" in issue.message for issue in preview.errors)


def test_invalid_types_are_refused_with_a_precise_path():
    payload = {"schema_version": 1, "resource_type": RESOURCE_CHARACTERS, "characters": "pas-une-liste"}
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert not preview.is_valid
    assert any(issue.path == "characters" for issue in preview.errors)


def test_bool_field_with_wrong_type_is_refused():
    payload = _char_payload("Gaston", is_primary="oui")
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert not preview.is_valid
    assert any(issue.path == "characters[0].is_primary" for issue in preview.errors)


def test_file_too_large_is_refused():
    payload = _char_payload("Gaston")
    payload["characters"][0]["personality"] = "x" * (MAX_FILE_BYTES + 10)
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert not preview.is_valid
    assert any("volumineux" in issue.message for issue in preview.errors)


def test_too_many_items_is_refused():
    payload = _char_payload(*[f"Personnage {i}" for i in range(MAX_ITEMS + 1)])
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert not preview.is_valid
    assert any(issue.path == "characters" for issue in preview.errors)


# -- champs inconnus (avertissement, jamais bloquant) et champs optionnels absents ----------------------------------------
def test_unknown_field_is_ignored_and_reported_as_a_warning():
    payload = _char_payload("Gaston")
    payload["characters"][0]["mystery"] = "valeur"
    payload["surprise_top_level"] = True
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert preview.is_valid
    assert any("characters[0].mystery" in w for w in preview.warnings)
    assert any("surprise_top_level" in w for w in preview.warnings)


def test_missing_optional_fields_default_correctly():
    payload = _char_payload("Juste un nom")
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert preview.is_valid
    item = preview.clean[0]
    assert item["role"] == "" and item["is_primary"] is False and item["is_active"] is True


def test_missing_required_name_is_refused():
    payload = {"schema_version": 1, "resource_type": RESOURCE_CHARACTERS, "characters": [{}]}
    preview = preview_character_import(json.dumps(payload).encode("utf-8"), existing_names=[])
    assert not preview.is_valid
    assert any(issue.path == "characters[0].name" for issue in preview.errors)


# -- garde-fou de couverture partagé avec #44 --------------------------------------------------------------------------
def test_resource_transfer_uses_the_exact_same_field_tuples_as_project_transfer():
    """Preuve que ce module n'a créé aucun second schéma : mêmes tuples d'objet Python, pas une copie."""
    import lody.resource_transfer as resource_module

    assert resource_module.CHARACTER_FIELDS is PROJECT_CHARACTER_FIELDS
    assert resource_module.LOCATION_FIELDS is PROJECT_LOCATION_FIELDS


def test_a_field_missing_from_the_shared_fields_tuple_would_fail_project_transfer_own_guard():
    """Le garde-fou de couverture de schéma est déjà exercé par #44
    (test_project_transfer.py::test_every_character_field_is_exported_or_explicitly_excluded_with_a_reason
    et l'équivalent pour Location) sur CHARACTER_FIELDS/LOCATION_FIELDS eux-mêmes ; comme ce module les
    réutilise à l'identique (test ci-dessus), un champ oublié y échouerait déjà — rien à dupliquer ici."""
    from lody.characters import EDITABLE_FIELDS as character_editable_fields
    from lody.locations import EDITABLE_FIELDS as location_editable_fields

    assert PROJECT_CHARACTER_FIELDS == character_editable_fields
    assert PROJECT_LOCATION_FIELDS == location_editable_fields
