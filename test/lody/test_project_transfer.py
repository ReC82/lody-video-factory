"""Export / import complet de configuration de projet (#44) : format versionné, validation, aperçu sans
écriture, création atomique, isolation stricte, aucun secret ni identifiant interne. Aucun fournisseur,
aucun appel réseau, aucun coût."""

from __future__ import annotations

import dataclasses
import json
import sqlite3

import pytest

from lody.characters import Character, CharacterRepository
from lody.locations import Location, LocationRepository
from lody.project_transfer import (
    CHARACTER_FIELDS,
    LOCATION_FIELDS,
    MAX_FILE_BYTES,
    MAX_ITEMS,
    PROJECT_FIELDS,
    SCHEMA_VERSION,
    TransferError,
    blank_template,
    commit_import,
    commit_update,
    export_project,
    preview_import,
    preview_update,
)
from lody.projects import Project, ProjectRepository


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


def test_blank_template_uses_the_technical_voice_provider_value():
    """#63 : le modèle téléchargeable montre l'identifiant TECHNIQUE attendu (« elevenlabs »), pas le libellé
    affiché dans l'interface (« ElevenLabs »), directement issu de catalog.VOICE_PROVIDERS."""
    template = blank_template()
    assert template["characters"][0]["voice_provider"] == "elevenlabs"


def test_complete_import_accepts_the_ui_label_for_voice_provider(db_path):
    """#63 : un import complet (#44) reprenant le libellé affiché dans l'interface (« ElevenLabs ») au lieu de
    l'identifiant technique est accepté et normalisé vers celui-ci."""
    payload = _payload()
    payload["characters"] = [{"name": "Gaston", "voice_provider": "ElevenLabs"}]
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=[])
    assert preview.is_valid, preview.errors
    created = commit_import(db_path, preview)
    characters_repo = CharacterRepository(db_path)
    assert [c.voice_provider for c in characters_repo.list_for_project(created.id)] == ["elevenlabs"]


def test_complete_import_refuses_an_unknown_voice_provider_with_the_accepted_values():
    payload = _payload(voice_provider="myspace-voice")
    preview = preview_import(json.dumps(payload).encode("utf-8"), existing_project_names=[])
    assert not preview.is_valid
    joined_issues = " ".join(issue.message for issue in preview.errors)
    assert '"elevenlabs"' in joined_issues and '"edge"' in joined_issues


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


# -- exigence complémentaire du ticket #44 : maintien automatique du format d'export -----------------------------
# Champs structurels/internes des dataclasses, volontairement EXCLUS de l'export — chacun avec sa raison.
# Un champ ajouté à Project/Character/Location qui n'apparaît ni dans EDITABLE_FIELDS (donc exporté) ni ici
# fait échouer test_every_*_field_is_exported_or_explicitly_excluded ci-dessous : un oubli est détecté par
# un test, jamais découvert plus tard sous la forme d'un export incomplet.
PROJECT_FIELDS_EXCLUDED = {
    "id": "identifiant interne — régénéré à l'import, jamais réutilisé",
    "status": "état d'archivage (actif/archivé) — décision d'administration, pas une configuration exportable",
    "created_at": "horodatage interne — régénéré à l'import",
    "updated_at": "horodatage interne — régénéré à l'import",
    "seed_key": "marqueur interne des projets d'exemple livrés avec l'application — sans sens hors de ce dépôt",
}
CHARACTER_FIELDS_EXCLUDED = {
    "id": "identifiant interne — régénéré à l'import",
    "project_id": "relation interne — recalculée à l'import (le personnage rejoint le NOUVEAU projet)",
    "created_at": "horodatage interne — régénéré à l'import",
    "updated_at": "horodatage interne — régénéré à l'import",
    "reference_image": "référence de fichier déjà validée (#38) — jamais acceptée depuis un JSON arbitraire, "
                        "voir characters.set_reference_image ; un import ne transfère donc pas l'image",
}
LOCATION_FIELDS_EXCLUDED = {
    "id": "identifiant interne — régénéré à l'import",
    "project_id": "relation interne — recalculée à l'import (le lieu rejoint le NOUVEAU projet)",
    "created_at": "horodatage interne — régénéré à l'import",
    "updated_at": "horodatage interne — régénéré à l'import",
    "reference_image": "référence de fichier déjà validée (#38) — jamais acceptée depuis un JSON arbitraire, "
                        "voir locations.set_reference_image ; un import ne transfère donc pas l'image",
}


def _dataclass_field_names(cls) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_every_project_field_is_exported_or_explicitly_excluded_with_a_reason():
    all_fields = _dataclass_field_names(Project)
    exported, excluded = set(PROJECT_FIELDS), set(PROJECT_FIELDS_EXCLUDED)
    assert not (exported & excluded), exported & excluded  # jamais les deux à la fois
    assert all_fields == exported | excluded, (
        f"champ(s) ni exporté(s) ni exclu(s) avec raison : {all_fields - exported - excluded} — "
        f"ajoute-le/les à PROJECT_FIELDS (via projects.EDITABLE_FIELDS) ou à PROJECT_FIELDS_EXCLUDED ci-dessus.")
    # PROJECT_FIELDS (utilisé par export/import) EST projects.EDITABLE_FIELDS — pas une copie qui pourrait diverger.
    from lody.projects import EDITABLE_FIELDS as project_editable_fields

    assert PROJECT_FIELDS == project_editable_fields


def test_every_character_field_is_exported_or_explicitly_excluded_with_a_reason():
    all_fields = _dataclass_field_names(Character)
    exported, excluded = set(CHARACTER_FIELDS), set(CHARACTER_FIELDS_EXCLUDED)
    assert not (exported & excluded), exported & excluded
    assert all_fields == exported | excluded, (
        f"champ(s) ni exporté(s) ni exclu(s) avec raison : {all_fields - exported - excluded}")
    from lody.characters import EDITABLE_FIELDS as character_editable_fields

    assert CHARACTER_FIELDS == character_editable_fields


def test_every_location_field_is_exported_or_explicitly_excluded_with_a_reason():
    all_fields = _dataclass_field_names(Location)
    exported, excluded = set(LOCATION_FIELDS), set(LOCATION_FIELDS_EXCLUDED)
    assert not (exported & excluded), exported & excluded
    assert all_fields == exported | excluded, (
        f"champ(s) ni exporté(s) ni exclu(s) avec raison : {all_fields - exported - excluded}")
    from lody.locations import EDITABLE_FIELDS as location_editable_fields

    assert LOCATION_FIELDS == location_editable_fields


def test_blank_template_field_names_come_from_the_same_fields_tuples_as_import_export():
    """Le modèle téléchargeable ne doit contenir AUCUNE liste de champs maintenue séparément : ses clés
    doivent être EXACTEMENT PROJECT_FIELDS/CHARACTER_FIELDS/LOCATION_FIELDS — les tuples que preview_import()
    et export_project() utilisent déjà (voir project_transfer.example_item, appelé par blank_template())."""
    template = blank_template()
    assert set(template["project"]) == set(PROJECT_FIELDS)
    for character in template["characters"]:
        assert set(character) == set(CHARACTER_FIELDS)
    for location in template["locations"]:
        assert set(location) == set(LOCATION_FIELDS)


# -- round-trip complet : plusieurs personnages et lieux, TOUS les champs fonctionnels comparés -------------------
def test_full_round_trip_with_multiple_characters_and_locations_preserves_every_functional_field(db_path):
    """Projet avec plusieurs personnages et plusieurs lieux → export JSON → import dans un nouveau projet →
    comparaison de CHAQUE champ fonctionnel (via PROJECT_FIELDS/CHARACTER_FIELDS/LOCATION_FIELDS, la même
    liste que l'export/import lui-même : si un champ cesse de survivre au round-trip, ce test le détecte
    sans qu'il faille se souvenir de l'ajouter à une liste d'assertions à la main)."""
    projects = ProjectRepository(db_path)
    characters = CharacterRepository(db_path)
    locations = LocationRepository(db_path)
    source = projects.create(
        name="Projet source complet", description="Une description complète et détaillée", language="en-US",
        format="16:9", content_type="tutoriel", tone="Inspirant", visual_style="Univers complet et détaillé",
        platforms=["youtube", "linkedin"], text_provider="openai", visual_provider="openai_image",
        voice_provider="elevenlabs", voice_name="Voix source", music_provider="library",
        settings={"brief": {"audience": "Public complet", "orientation": "Progression claire",
                            "structure": ["Accroche", "Développement", "Conclusion"],
                            "standing_instructions": "Toujours rester concret.",
                            "visual_rules": "Toujours un cadrage stable.", "visual_avoid": ["texte à l'écran"]}})
    character_specs = (
        dict(name="Gaston", role="Guide", personality="Bavard, chaleureux", visual_description="Chapeau, gilet",
             reference_prompt="portrait voxel", speech_style="Phrases courtes", voice_provider="elevenlabs",
             voice_name="Voix Gaston", external_voice_id="21m00Tcm4TlvDq8ikWAM", permanent_elements="Porte un chapeau",
             continuity_notes="Toujours enjoué", is_primary=True, is_active=True),
        dict(name="Zoé", role="Alliée", personality="Calme et précise", is_primary=False, is_active=False),
    )
    location_specs = (
        dict(name="Studio", location_type="Intérieur", description="Studio moderne", reference_prompt="plan large",
             continuity_notes="Toujours la même disposition", is_primary=True, is_active=True),
        dict(name="Extérieur ville", location_type="Extérieur", description="Rue animée", is_primary=False, is_active=False),
    )
    source_characters = [characters.create(source.id, **spec) for spec in character_specs]
    source_locations = [locations.create(source.id, **spec) for spec in location_specs]

    payload = export_project(source, characters.list_for_project(source.id), locations.list_for_project(source.id))
    # Le nom du projet SOURCE est déjà pris (par lui-même) : l'import est forcément renommé (politique
    # « jamais d'écrasement », testée pour elle-même ailleurs) — donc exclu ici de la comparaison champ à
    # champ, qui porte sur tout le reste (tous les autres champs de PROJECT_FIELDS, sans exception).
    preview = preview_import(json.dumps(payload).encode("utf-8"),
                             existing_project_names=[p.name for p in projects.list_projects()])
    assert preview.is_valid and preview.name_was_renamed, preview.errors
    created = commit_import(db_path, preview)

    assert created.id != source.id and created.name != source.name
    for field in PROJECT_FIELDS:
        if field == "name":
            continue
        assert getattr(created, field) == getattr(source, field), f"project.{field}"

    new_by_name = {c.name: c for c in characters.list_for_project(created.id)}
    old_by_name = {c.name: c for c in source_characters}
    assert set(new_by_name) == set(old_by_name) == {"Gaston", "Zoé"}
    for name, old in old_by_name.items():
        new = new_by_name[name]
        assert new.id != old.id and new.project_id == created.id
        for field in CHARACTER_FIELDS:
            assert getattr(new, field) == getattr(old, field), f"characters[{name}].{field}"

    new_locs_by_name = {loc.name: loc for loc in locations.list_for_project(created.id)}
    old_locs_by_name = {loc.name: loc for loc in source_locations}
    assert set(new_locs_by_name) == set(old_locs_by_name) == {"Studio", "Extérieur ville"}
    for name, old in old_locs_by_name.items():
        new = new_locs_by_name[name]
        assert new.id != old.id and new.project_id == created.id
        for field in LOCATION_FIELDS:
            assert getattr(new, field) == getattr(old, field), f"locations[{name}].{field}"


# ======================================================================================================================
# -- mise à jour volontaire d'un projet existant (#66) -----------------------------------------------------------------
# ======================================================================================================================

def _update_payload(target_name, **project_overrides):
    return {"schema_version": SCHEMA_VERSION, "project": {"name": target_name, **project_overrides},
            "characters": [], "locations": []}


def test_round_trip_export_modify_update_applies_to_the_same_project(projects, db_path):
    project = projects.create(name="PNJ", tone="Pédagogique et dynamique",
                              settings={"brief": {"audience": "Débutants"}})
    payload = export_project(project, [], [])
    payload["project"]["tone"] = "Sérieux et posé"
    payload["project"]["settings"]["brief"]["audience"] = "Experts"

    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project,
                             target_characters=[], target_locations=[], other_project_names=[])
    assert preview.is_valid, preview.errors
    assert preview.project_changes["tone"] == ("Pédagogique et dynamique", "Sérieux et posé")
    assert "settings" in preview.project_changes  # le brief est PORTÉ par settings, jamais ignoré (#66)

    updated = commit_update(db_path, preview)
    assert updated.id == project.id  # identifiant conservé
    assert updated.tone == "Sérieux et posé"
    assert updated.settings["brief"]["audience"] == "Experts"


def test_settings_and_brief_are_never_ignored_by_the_round_trip(projects, db_path):
    """Exigence explicite du ticket #66 : les réglages complets (dont le brief) doivent être conservés ET
    mis à jour, pas seulement les champs visibles au premier niveau."""
    project = projects.create(name="Avec brief complet", settings={
        "brief": {"audience": "Ados", "structure": ["Accroche", "Chute"], "standing_instructions": "Reste bref."},
        "custom_extra": "conservé aussi",
    })
    payload = export_project(project, [], [])
    assert payload["project"]["settings"] == project.settings  # déjà présent dans l'export (EDITABLE_FIELDS)
    payload["project"]["settings"]["brief"]["audience"] = "Adultes"

    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project,
                             target_characters=[], target_locations=[], other_project_names=[])
    assert preview.is_valid, preview.errors
    updated = commit_update(db_path, preview)
    assert updated.settings["brief"]["audience"] == "Adultes"
    assert updated.settings["brief"]["structure"] == ["Accroche", "Chute"]
    assert updated.settings["custom_extra"] == "conservé aussi"


def test_project_id_and_production_history_survive_an_update(projects, db_path):
    project = projects.create(name="Historique")
    connection = sqlite3.connect(db_path)
    connection.execute(
        "INSERT INTO productions (id, project_id, root_production_id, version, subject, provider, idempotency_key,"
        " status, created_at, updated_at, script) VALUES ('prd_old', ?, 'prd_old', 1, 'Sujet', 'p', 'k',"
        " 'TERMINEE', 'x', 'x', 'Script historique.')", (project.id,))
    connection.commit()
    connection.close()

    payload = export_project(project, [], [])
    payload["project"]["tone"] = "Nouveau ton"
    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project,
                             target_characters=[], target_locations=[], other_project_names=[])
    updated = commit_update(db_path, preview)

    assert updated.id == project.id
    connection = sqlite3.connect(db_path)
    row = connection.execute("SELECT script, status FROM productions WHERE id = 'prd_old'").fetchone()
    assert row == ("Script historique.", "TERMINEE")


def test_characters_and_locations_are_added_modified_and_deactivated(projects, characters, locations, db_path):
    project = projects.create(name="PNJ")
    gaston = characters.create(project.id, name="Gaston", role="Ancien rôle")
    zoe = characters.create(project.id, name="Zoé", role="Sera désactivée")
    atelier = locations.create(project.id, name="Atelier", location_type="Intérieur")
    extra = locations.create(project.id, name="Extérieur", location_type="Extérieur")

    payload = export_project(project, characters.list_for_project(project.id), locations.list_for_project(project.id))
    # Zoé et Extérieur absents du fichier ; Gaston modifié ; Atelier inchangé ; Théo et Studio nouveaux.
    payload["characters"] = [c for c in payload["characters"] if c["name"] != "Zoé"]
    payload["characters"][0]["role"] = "Nouveau rôle"
    payload["characters"].append({"name": "Théo", "role": "Petit nouveau"})
    payload["locations"] = [loc for loc in payload["locations"] if loc["name"] != "Extérieur"]
    payload["locations"].append({"name": "Studio", "location_type": "Intérieur"})

    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project,
                             target_characters=characters.list_for_project(project.id),
                             target_locations=locations.list_for_project(project.id), other_project_names=[])
    assert preview.is_valid, preview.errors
    assert preview.characters_added == ["Théo"]
    assert preview.characters_modified == ["Gaston"]
    assert preview.characters_unchanged == []
    assert preview.characters_deactivated == ["Zoé"]
    assert preview.locations_added == ["Studio"]
    assert preview.locations_unchanged == ["Atelier"]
    assert preview.locations_deactivated == ["Extérieur"]

    commit_update(db_path, preview)

    by_name = {c.name: c for c in characters.list_for_project(project.id)}
    assert by_name["Gaston"].id == gaston.id and by_name["Gaston"].role == "Nouveau rôle" and by_name["Gaston"].is_active
    assert by_name["Zoé"].id == zoe.id and by_name["Zoé"].is_active is False  # désactivée, jamais supprimée
    assert by_name["Théo"].is_active and by_name["Théo"].role == "Petit nouveau"

    loc_by_name = {loc.name: loc for loc in locations.list_for_project(project.id)}
    assert loc_by_name["Atelier"].id == atelier.id  # inchangé : même identifiant
    assert loc_by_name["Extérieur"].id == extra.id and loc_by_name["Extérieur"].is_active is False
    assert loc_by_name["Studio"].is_active


def test_already_inactive_character_absent_from_file_is_not_re_announced(projects, characters, db_path):
    project = projects.create(name="PNJ")
    characters.create(project.id, name="Gaston")
    dormant = characters.create(project.id, name="Dormant")
    characters.deactivate(project.id, dormant.id)

    payload = export_project(project, [c for c in characters.list_for_project(project.id) if c.name == "Gaston"], [])
    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project,
                             target_characters=characters.list_for_project(project.id),
                             target_locations=[], other_project_names=[])
    assert preview.is_valid
    assert preview.characters_deactivated == []  # déjà inactif : rien de nouveau à annoncer
    assert preview.characters_unchanged == ["Gaston"]


def test_reference_image_of_a_matched_character_is_never_touched_by_an_update(projects, characters, db_path):
    """#38 : reference_image est hors EDITABLE_FIELDS — un import complet ne peut ni l'écraser ni la lire
    depuis le fichier (elle n'y figure jamais), donc une mise à jour ne la modifie jamais non plus."""
    project = projects.create(name="PNJ")
    gaston = characters.create(project.id, name="Gaston")
    assert gaston.reference_image == ""  # aucune image dans ce test : on vérifie juste qu'elle n'est pas touchée

    payload = export_project(project, characters.list_for_project(project.id), [])
    payload["characters"][0]["role"] = "Changé"
    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project,
                             target_characters=characters.list_for_project(project.id),
                             target_locations=[], other_project_names=[])
    commit_update(db_path, preview)
    assert characters.get(project.id, gaston.id).reference_image == ""


def test_duplicate_name_within_the_uploaded_file_is_blocked_like_creation(projects, db_path):
    project = projects.create(name="PNJ")
    payload = export_project(project, [], [])
    payload["characters"] = [{"name": "Gaston"}, {"name": "GASTON"}]
    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project,
                             target_characters=[], target_locations=[], other_project_names=[])
    assert not preview.is_valid
    assert any("characters[1].name" in issue.path for issue in preview.errors)


def test_invalid_json_file_is_refused_with_nothing_changed(projects, db_path):
    project = projects.create(name="PNJ", tone="Ton original")
    preview = preview_update(b"{not valid json", target=project, target_characters=[], target_locations=[],
                             other_project_names=[])
    assert not preview.is_valid
    assert projects.get(project.id).tone == "Ton original"


def test_target_name_colliding_with_another_project_is_refused(projects, db_path):
    project = projects.create(name="PNJ")
    other = projects.create(name="Déjà pris")
    payload = _update_payload("Déjà pris")
    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project, target_characters=[],
                             target_locations=[], other_project_names=[other.name])
    assert not preview.is_valid
    assert any(issue.path == "project.name" for issue in preview.errors)
    assert projects.get(project.id).name == "PNJ"  # rien n'a changé


def test_renaming_the_target_project_to_its_own_current_name_is_not_a_collision(projects, db_path):
    project = projects.create(name="PNJ")
    payload = _update_payload("PNJ", tone="Nouveau ton")
    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project, target_characters=[],
                             target_locations=[], other_project_names=[])
    assert preview.is_valid, preview.errors
    updated = commit_update(db_path, preview)
    assert updated.name == "PNJ" and updated.tone == "Nouveau ton"


def test_not_calling_commit_leaves_the_project_untouched(projects, db_path):
    """« Annuler la confirmation ne modifie rien » : ``preview_update`` seul n'écrit jamais — c'est vrai par
    construction (aucune connexion en écriture n'est ouverte avant ``commit_update``), vérifié ici."""
    project = projects.create(name="PNJ", tone="Ton original")
    payload = export_project(project, [], [])
    payload["project"]["tone"] = "Ton jamais appliqué"
    preview_update(json.dumps(payload).encode("utf-8"), target=project, target_characters=[], target_locations=[],
                   other_project_names=[])
    assert projects.get(project.id).tone == "Ton original"


def test_atomic_rollback_on_concurrent_name_collision_leaves_nothing_partial(projects, characters, db_path):
    project = projects.create(name="PNJ")
    characters.create(project.id, name="Gaston")
    payload = export_project(project, characters.list_for_project(project.id), [])
    payload["project"]["name"] = "Nom pris entre-temps"
    payload["characters"][0]["role"] = "Ne doit jamais être écrit"
    payload["characters"].append({"name": "Nouveau, ne doit jamais être créé"})

    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project,
                             target_characters=characters.list_for_project(project.id),
                             target_locations=[], other_project_names=[])
    assert preview.is_valid, preview.errors
    projects.create(name="Nom pris entre-temps")  # collision créée APRÈS l'aperçu, AVANT la confirmation

    with pytest.raises(TransferError):
        commit_update(db_path, preview)

    # Rien n'a bougé : ni le projet cible, ni son personnage.
    assert projects.get(project.id).name == "PNJ"
    assert characters.list_for_project(project.id)[0].role != "Ne doit jamais être écrit"
    assert len(characters.list_for_project(project.id)) == 1  # aucun personnage ajouté


def test_isolation_updating_one_project_never_touches_another(projects, characters, locations, db_path):
    target = projects.create(name="Cible")
    other = projects.create(name="Autre projet intact")
    other_char = characters.create(other.id, name="Personnage de l'autre projet", role="Ne doit jamais changer")
    other_loc = locations.create(other.id, name="Lieu de l'autre projet")

    payload = export_project(target, [], [])
    payload["project"]["tone"] = "Changé"
    payload["characters"] = [{"name": "Nouveau perso"}]
    payload["locations"] = [{"name": "Nouveau lieu"}]
    preview = preview_update(json.dumps(payload).encode("utf-8"), target=target, target_characters=[],
                             target_locations=[], other_project_names=[other.name])
    commit_update(db_path, preview)

    assert characters.get(other.id, other_char.id).role == "Ne doit jamais changer"
    assert locations.get(other.id, other_loc.id).name == "Lieu de l'autre projet"
    assert projects.get(other.id).tone != "Changé"
    assert len(characters.list_for_project(other.id)) == 1  # le nouveau perso a rejoint la CIBLE, pas l'autre
    assert len(characters.list_for_project(target.id)) == 1


def test_matching_by_name_never_crosses_into_another_projects_character(projects, characters, db_path):
    """Deux personnages du même nom dans deux projets différents ne doivent jamais se confondre : la
    correspondance de #66 ne regarde que les éléments du projet CIBLE."""
    target = projects.create(name="Cible")
    other = projects.create(name="Autre")
    target_gaston = characters.create(target.id, name="Gaston", role="Rôle cible")
    other_gaston = characters.create(other.id, name="Gaston", role="Rôle de l'autre projet")

    payload = export_project(target, characters.list_for_project(target.id), [])
    payload["characters"][0]["role"] = "Rôle mis à jour"
    preview = preview_update(json.dumps(payload).encode("utf-8"), target=target,
                             target_characters=characters.list_for_project(target.id),
                             target_locations=[], other_project_names=[other.name])
    commit_update(db_path, preview)

    assert characters.get(target.id, target_gaston.id).role == "Rôle mis à jour"
    assert characters.get(other.id, other_gaston.id).role == "Rôle de l'autre projet"  # jamais touché


def test_commit_update_refuses_an_invalid_preview_without_touching_the_database(projects, db_path):
    project = projects.create(name="PNJ", tone="Ton original")
    invalid_preview = preview_update(b"{not json", target=project, target_characters=[], target_locations=[])
    with pytest.raises(TransferError):
        commit_update(db_path, invalid_preview)
    assert projects.get(project.id).tone == "Ton original"


def test_commit_update_refuses_when_the_target_project_no_longer_exists(projects, db_path):
    project = projects.create(name="PNJ")
    payload = export_project(project, [], [])
    preview = preview_update(json.dumps(payload).encode("utf-8"), target=project, target_characters=[],
                             target_locations=[], other_project_names=[])
    connection = sqlite3.connect(db_path)
    connection.execute("DELETE FROM projects WHERE id = ?", (project.id,))
    connection.commit()
    connection.close()
    with pytest.raises(TransferError):
        commit_update(db_path, preview)


# -- non-régression : le mode création (#44) reste inchangé -------------------------------------------------------
def test_create_mode_still_renames_on_collision_after_66(projects, db_path):
    projects.create(name="Existe déjà")
    payload = _payload("Existe déjà")
    preview = preview_import(json.dumps(payload).encode("utf-8"),
                             existing_project_names=[p.name for p in projects.list_projects()])
    assert preview.is_valid and preview.name_was_renamed
    created = commit_import(db_path, preview)
    assert created.name != "Existe déjà"
    names = {p.name for p in projects.list_projects()}
    assert "Existe déjà" in names and created.name in names  # le projet existant n'a pas bougé, le nouveau s'ajoute
    assert len(projects.list_projects()) == 2
