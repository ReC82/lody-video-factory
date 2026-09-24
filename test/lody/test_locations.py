"""Lieux récurrents (#31) : migration, CRUD, isolation par projet, validation, garde-fou secrets, FK,
et non-régression du pipeline de génération classique. Aucun fournisseur, aucun coût."""

from __future__ import annotations

import io
import sqlite3

import pytest
from PIL import Image

from lody import db, reference_images
from lody.locations import (
    LocationNotFound,
    LocationRepository,
    LocationValidationError,
)
from lody.projects import ProjectRepository


@pytest.fixture
def projects(tmp_path):
    return ProjectRepository(tmp_path / "lody.sqlite3")


@pytest.fixture
def repo(tmp_path, projects):
    return LocationRepository(tmp_path / "lody.sqlite3")


@pytest.fixture
def project(projects):
    return projects.create(name="Projet de test")


# -- migrations --------------------------------------------------------------------------------------------------
def test_fresh_database_has_zero_locations_and_reaches_the_latest_schema(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    projects = ProjectRepository(path)
    project = projects.create(name="Neuf")
    repo = LocationRepository(path)
    assert repo.list_for_project(project.id) == []
    assert repo.count_for_project(project.id) == 0
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 6
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "productions", "publication_kits", "characters", "locations"} <= tables


def test_upgrade_from_currently_deployed_schema_v4_keeps_existing_data(tmp_path):
    """Reproduit le schéma v4 réellement déployé (projects + productions + kits, sans characters/locations),
    avec un projet et une production déjà présents, puis vérifie que la mise à niveau ne les touche pas."""
    path = tmp_path / "v4.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(db.PROJECTS_SCHEMA + db.PRODUCTIONS_SCHEMA)
    db._productions_isolation(connection)
    connection.executescript(db.KITS_SCHEMA)
    connection.execute("PRAGMA user_version = 4")
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

    repo = LocationRepository(path)  # déclenche la migration additive
    assert repo.list_for_project("prj_old") == []  # zéro lieu par défaut, même pour un projet ancien

    upgraded = sqlite3.connect(path)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 6
    # Rien de préexistant n'a bougé.
    assert upgraded.execute("SELECT name FROM projects").fetchall() == [("Ancien",)]
    assert upgraded.execute("SELECT script FROM productions WHERE id = 'prd_old'").fetchone() == \
        ("Un script déjà produit.",)


def test_migration_replayed_is_a_no_op(tmp_path):
    path = tmp_path / "replay.sqlite3"
    ProjectRepository(path)
    LocationRepository(path)
    LocationRepository(path)  # rejeu explicite
    LocationRepository(path)  # une troisième fois, pour faire bonne mesure
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
    assert connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0


def test_locations_table_has_no_binary_image_column_and_no_api_key_shaped_column(tmp_path):
    connection = sqlite3.connect(tmp_path / "shape.sqlite3")
    connection.executescript(db.LOCATIONS_SCHEMA)
    columns = {row[1]: row[2] for row in connection.execute("PRAGMA table_info(locations)")}
    assert "image" not in " ".join(columns).lower() and "BLOB" not in columns.values()
    assert not [c for c in columns if "api_key" in c or ("secret" in c) or ("token" in c)]


# -- CRUD -----------------------------------------------------------------------------------------------------------
def test_create_location_with_defaults_and_stable_id(repo, project):
    location = repo.create(project.id, name="  Place   du marché  ", location_type="extérieur, place publique")
    assert location.id.startswith("loc_")
    assert location.project_id == project.id
    assert location.name == "Place du marché"
    assert location.location_type == "extérieur, place publique"
    assert location.is_active is True and location.is_primary is False
    assert location.created_at == location.updated_at
    assert repo.get(project.id, location.id) == location


def test_create_sets_every_mvp_field(repo, project):
    location = repo.create(
        project.id, name="Place du marché", location_type="extérieur", description="Marché coloré, animé le matin",
        reference_prompt="voxel art, place de village ensoleillée", continuity_notes="Toujours en plein jour",
        is_primary=True,
    )
    assert location.description == "Marché coloré, animé le matin"
    assert location.reference_prompt == "voxel art, place de village ensoleillée"
    assert location.continuity_notes == "Toujours en plein jour"
    assert location.is_primary is True


def test_update_changes_fields_and_last_modified(tmp_path, projects, project):
    ticks = iter(["2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00", "2026-01-01T12:00:00+00:00"])
    repo = LocationRepository(tmp_path / "lody.sqlite3", clock=lambda: next(ticks))
    location = repo.create(project.id, name="Avant")
    updated = repo.update(project.id, location.id, name="Après", location_type="intérieur")
    assert (updated.name, updated.location_type) == ("Après", "intérieur")
    assert updated.created_at == "2026-01-01T10:00:00+00:00"
    assert updated.updated_at == "2026-01-01T11:00:00+00:00"


def test_update_unknown_location_or_unknown_field(repo, project):
    with pytest.raises(LocationNotFound):
        repo.update(project.id, "loc_inexistant", name="Test")
    location = repo.create(project.id, name="Test")
    with pytest.raises(LocationValidationError):
        repo.update(project.id, location.id, status="archived")


def test_list_for_project_sorts_active_primary_first_then_name(repo, project):
    repo.create(project.id, name="Zoo")
    repo.create(project.id, name="Gare", is_primary=True)
    repo.create(project.id, name="Atelier")
    inactive = repo.create(project.id, name="Aaa inactif")
    repo.deactivate(project.id, inactive.id)
    names = [c.name for c in repo.list_for_project(project.id)]
    assert names == ["Gare", "Atelier", "Zoo", "Aaa inactif"]  # actifs d'abord, principal en tête, puis alpha


def test_list_for_project_can_exclude_inactive(repo, project):
    active = repo.create(project.id, name="Actif")
    inactive = repo.create(project.id, name="Inactif")
    repo.deactivate(project.id, inactive.id)
    assert [c.id for c in repo.list_for_project(project.id, include_inactive=False)] == [active.id]


# -- isolation par project_id ------------------------------------------------------------------------------------
def test_two_projects_can_have_a_location_with_the_same_name(repo, projects):
    a = projects.create(name="Projet A")
    b = projects.create(name="Projet B")
    la = repo.create(a.id, name="Place du marché")
    lb = repo.create(b.id, name="Place du marché")
    assert la.id != lb.id
    assert [c.name for c in repo.list_for_project(a.id)] == ["Place du marché"]
    assert [c.name for c in repo.list_for_project(b.id)] == ["Place du marché"]


def test_name_unique_within_a_project_case_insensitively_accents_included(repo, project):
    repo.create(project.id, name="Place du marché")
    with pytest.raises(LocationValidationError) as error:
        repo.create(project.id, name="PLACE DU MARCHÉ")
    assert "name" in error.value.errors
    with pytest.raises(LocationValidationError):
        repo.create(project.id, name="place du marché")
    # « Élysée » / « élysée » : NOCASE de SQLite est ASCII seul et ne les confondrait pas — casefold() applicatif si.
    repo.create(project.id, name="Élysée")
    with pytest.raises(LocationValidationError):
        repo.create(project.id, name="élysée")


def test_update_keeps_own_name_but_refuses_another_locations_name(repo, project):
    first = repo.create(project.id, name="Premier lieu")
    repo.create(project.id, name="Second lieu")
    assert repo.update(project.id, first.id, name="Premier lieu", location_type="x").location_type == "x"
    with pytest.raises(LocationValidationError):
        repo.update(project.id, first.id, name="Second lieu")


def test_get_with_the_wrong_project_id_is_not_found_same_error_as_unknown_id(repo, projects, project):
    other = projects.create(name="Autre projet")
    location = repo.create(project.id, name="Place du marché")
    with pytest.raises(LocationNotFound):
        repo.get(other.id, location.id)  # existe, mais dans un AUTRE projet : refus identique à un id inconnu
    with pytest.raises(LocationNotFound):
        repo.get(other.id, "loc_inexistant")


def test_update_across_projects_is_refused(repo, projects, project):
    other = projects.create(name="Autre projet")
    location = repo.create(project.id, name="Place du marché")
    with pytest.raises(LocationNotFound):
        repo.update(other.id, location.id, name="Détourné")
    assert repo.get(project.id, location.id).name == "Place du marché"  # inchangé


def test_activate_deactivate_across_projects_is_refused(repo, projects, project):
    other = projects.create(name="Autre projet")
    location = repo.create(project.id, name="Place du marché")
    with pytest.raises(LocationNotFound):
        repo.deactivate(other.id, location.id)
    assert repo.get(project.id, location.id).is_active is True


def test_list_and_count_never_leak_across_projects(repo, projects):
    a = projects.create(name="Projet A")
    b = projects.create(name="Projet B")
    repo.create(a.id, name="Un lieu")
    repo.create(a.id, name="Deux lieu")
    repo.create(b.id, name="Trois lieu")
    assert repo.count_for_project(a.id) == 2
    assert repo.count_for_project(b.id) == 1
    assert {c.name for c in repo.list_for_project(a.id)} == {"Un lieu", "Deux lieu"}


# -- actif/inactif, principal -----------------------------------------------------------------------------------
def test_activate_and_deactivate(repo, project):
    location = repo.create(project.id, name="Place du marché")
    assert repo.deactivate(project.id, location.id).is_active is False
    assert repo.activate(project.id, location.id).is_active is True


def test_deactivate_is_not_a_delete(repo, project):
    location = repo.create(project.id, name="Place du marché")
    repo.deactivate(project.id, location.id)
    assert repo.get(project.id, location.id).name == "Place du marché"  # toujours là, juste inactif
    assert repo.count_for_project(project.id) == 1


def test_is_primary_is_a_plain_field_no_exclusivity_enforced_in_this_mvp(repo, project):
    a = repo.create(project.id, name="Alpha", is_primary=True)
    b = repo.create(project.id, name="Bravo", is_primary=True)
    assert a.is_primary is True and b.is_primary is True  # aucune contrainte d'unicité imposée par ce MVP


# -- validation et garde-fou anti-secrets -------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "expected"),
    [("", "Donne un nom"), ("   ", "Donne un nom"), ("a", "au moins 2"), ("x" * 81, "trop long")],
)
def test_name_validation_messages_are_human(repo, project, name, expected):
    with pytest.raises(LocationValidationError) as error:
        repo.create(project.id, name=name)
    assert expected in error.value.errors["name"]
    assert repo.count_for_project(project.id) == 0


@pytest.mark.parametrize("field", ["location_type", "description", "reference_prompt", "continuity_notes"])
def test_field_length_limits_are_enforced(repo, project, field):
    with pytest.raises(LocationValidationError) as error:
        repo.create(project.id, name="Test", **{field: "x" * 1000})
    assert field in error.value.errors


@pytest.mark.parametrize("field", ["name", "location_type", "description", "reference_prompt", "continuity_notes"])
def test_secrets_cannot_be_stored_in_any_text_field(repo, project, field):
    with pytest.raises(LocationValidationError) as error:
        repo.create(project.id, **{"name": "Test", field: "sk-abcdefghijklmnopqrstuvwxyz123456"})
    assert field in error.value.errors


def test_reference_prompt_shaped_like_an_api_key_is_refused():
    """Le même risque que pour les personnages (#30), transposé au champ le plus « libre » d'un lieu."""
    from lody.locations import validate_fields

    with pytest.raises(LocationValidationError) as error:
        validate_fields({"name": "Test", "reference_prompt": "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"})
    assert "reference_prompt" in error.value.errors


def test_validation_error_messages_never_echo_the_offending_value(repo, project):
    try:
        repo.create(project.id, name="sk-abcdefghijklmnopqrstuvwxyz123456")
    except LocationValidationError as error:
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in str(error.errors)
    else:
        pytest.fail("devait lever LocationValidationError")


# -- projet inexistant / archivé, comportement FK -------------------------------------------------------------------
def test_create_for_a_nonexistent_project_is_refused(repo):
    with pytest.raises(LocationValidationError) as error:
        repo.create("prj_inexistant", name="Test")
    assert "project_id" in error.value.errors


def test_create_for_an_archived_project_still_works_archiving_never_rewrites_data(repo, projects, project):
    projects.archive(project.id)
    location = repo.create(project.id, name="Place du marché")  # une décision d'administration, pas cette couche
    assert repo.get(project.id, location.id).name == "Place du marché"
    assert projects.get(project.id).status == "archived"  # le projet reste inchangé par ailleurs


def test_archiving_a_project_never_touches_its_existing_locations(repo, projects, project):
    location = repo.create(project.id, name="Place du marché", is_primary=True)
    projects.archive(project.id)
    projects.restore(project.id)
    still = repo.get(project.id, location.id)
    assert (still.name, still.is_primary, still.is_active) == ("Place du marché", True, True)


def test_foreign_key_blocks_inserting_a_location_for_a_project_that_does_not_exist_at_the_sql_level(tmp_path):
    """Vérifie le comportement FK lui-même (pas seulement le garde-fou applicatif) : PRAGMA foreign_keys=ON
    empêche une ligne orpheline même via une insertion SQL directe."""
    path = tmp_path / "fk.sqlite3"
    ProjectRepository(path)
    LocationRepository(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO locations (id, project_id, name, created_at, updated_at) "
            "VALUES ('loc_x', 'prj_does_not_exist', 'Test', 'x', 'x')")


def test_foreign_key_blocks_deleting_a_project_that_still_has_locations(tmp_path):
    """L'app n'a pas de suppression de projet (archive/restore seulement) ; ce test prouve que la contrainte
    protège quand même l'intégrité si un code futur tentait un DELETE direct."""
    path = tmp_path / "fk-delete.sqlite3"
    projects = ProjectRepository(path)
    project = projects.create(name="Protégé")
    repo = LocationRepository(path)
    repo.create(project.id, name="Place du marché")
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM projects WHERE id = ?", (project.id,))


# -- non-régression du pipeline classique --------------------------------------------------------------------------
def test_an_existing_project_has_implicitly_zero_locations(tmp_path):
    """Prouve explicitement le critère d'acceptation : un projet créé AVANT ce ticket (donc sans jamais avoir
    entendu parler de lieux) a bien zéro lieu, sans avoir besoin d'initialisation explicite."""
    path = tmp_path / "legacy.sqlite3"
    projects = ProjectRepository(path)
    project = projects.create(name="Projet classique")
    repo = LocationRepository(path)
    assert repo.count_for_project(project.id) == 0
    assert repo.list_for_project(project.id) == []


def test_generation_request_payload_is_identical_with_or_without_locations_in_the_project(repo, project):
    """Preuve directe que le pipeline de génération classique n'est influencé en rien par la présence de
    lieux : même projet, même sujet, la requête construite (``build_request``) est strictement identique
    avant et après avoir ajouté des lieux en base."""
    from lody.generation.service import build_request

    before = build_request(project, "Un sujet quelconque")

    repo.create(project.id, name="Place du marché", is_primary=True)
    repo.create(project.id, name="Ruelle sombre")

    after = build_request(project, "Un sujet quelconque")
    assert before == after


def test_only_the_snapshot_wiring_references_characters_or_locations_not_the_script_voice_image_pipeline():
    """Preuve statique de la frontière du ticket #35 : la résolution de la sélection en instantané
    (``narrative_context.py``, câblée par ``service.py``/``runtime.py``) peut référencer
    ``lody.characters``/``lody.locations``, mais aucun module qui construit ce qui est réellement envoyé au
    moteur (script, storyboard, prompts de voix ou d'image) ne le fait — aucune injection dans le script, la
    voix ou les prompts d'images à ce stade."""
    import pathlib
    import re

    patterns = [
        re.compile(r"\bimport\s+lody\.characters\b"), re.compile(r"\bimport\s+lody\.locations\b"),
        re.compile(r"\bfrom\s+lody\s+import\b.*\bcharacters\b"), re.compile(r"\bfrom\s+lody\s+import\b.*\blocations\b"),
        re.compile(r"\bfrom\s+lody\.characters\s+import\b"), re.compile(r"\bfrom\s+lody\.locations\s+import\b"),
        re.compile(r"\bCharacterRepository\b"), re.compile(r"\bLocationRepository\b"),
        re.compile(r"FROM\s+characters\b", re.IGNORECASE), re.compile(r"FROM\s+locations\b", re.IGNORECASE),
    ]
    # Seul le contrat de résolution du snapshot (#35) : jamais le script, le storyboard, la voix ou les
    # prompts d'image réellement envoyés (storyboard.py, mpt_connector.py, safety.py, typography.py, ...).
    allowed = {"narrative_context.py", "service.py", "runtime.py"}
    generation_dir = pathlib.Path(__file__).resolve().parents[2] / "webui" / "lody" / "generation"
    offenders = [
        path.name for path in generation_dir.glob("*.py")
        if path.name not in allowed and any(pattern.search(path.read_text(encoding="utf-8")) for pattern in patterns)
    ]
    assert offenders == []


# -- image de référence (#38, symétrique de test_characters.py) --------------------------------------------------
def _png(color=(255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (120, 120), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _isolated_reference_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("LODY_DATA_DIR", str(tmp_path / "ref_storage"))


def test_new_location_has_no_reference_image_by_default(repo, project):
    location = repo.create(project.id, name="Atelier")
    assert location.reference_image == ""


def test_set_reference_image_stores_a_validated_reference_not_bytes(repo, project):
    location = repo.create(project.id, name="Atelier")
    updated = repo.set_reference_image(project.id, location.id, _png())
    assert updated.reference_image.startswith(f"references/{project.id}/locations/{location.id}/")
    path = reference_images.resolve(updated.reference_image, project.id, "locations", location.id)
    assert path.read_bytes() != b""


def test_set_reference_image_rejects_an_invalid_image_and_leaves_the_location_untouched(repo, project):
    location = repo.create(project.id, name="Atelier")
    with pytest.raises(LocationValidationError) as error:
        repo.set_reference_image(project.id, location.id, b"pas une image")
    assert "reference_image" in error.value.errors
    assert repo.get(project.id, location.id).reference_image == ""


def test_set_reference_image_on_an_unknown_location_raises_not_found(repo, project):
    with pytest.raises(LocationNotFound):
        repo.set_reference_image(project.id, "loc_doesnotexist0", _png())


def test_set_reference_image_never_writes_into_another_projects_location(repo, projects, project):
    other_project = projects.create(name="Autre projet")
    location = repo.create(other_project.id, name="Atelier")
    with pytest.raises(LocationNotFound):
        repo.set_reference_image(project.id, location.id, _png())


def test_clear_reference_image_resets_the_column_but_keeps_the_file_on_disk(repo, project):
    location = repo.create(project.id, name="Atelier")
    with_image = repo.set_reference_image(project.id, location.id, _png())
    path = reference_images.resolve(with_image.reference_image, project.id, "locations", location.id)
    cleared = repo.clear_reference_image(project.id, location.id)
    assert cleared.reference_image == ""
    assert path.exists()


def test_reference_image_is_excluded_from_editable_fields_and_generic_update(repo, project):
    from lody.locations import EDITABLE_FIELDS

    assert "reference_image" not in EDITABLE_FIELDS
    location = repo.create(project.id, name="Atelier")
    with pytest.raises(LocationValidationError) as error:
        repo.update(project.id, location.id, reference_image="references/evil/path")
    assert "Champ inconnu" in str(error.value)
    assert repo.get(project.id, location.id).reference_image == ""
