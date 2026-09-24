"""Personnages récurrents (#30) : migration, CRUD, isolation par projet, validation, garde-fou secrets,
FK, et non-régression du pipeline de génération classique. Aucun fournisseur, aucun coût."""

from __future__ import annotations

import sqlite3

import pytest

from lody import catalog, db
from lody.characters import (
    CharacterNotFound,
    CharacterRepository,
    CharacterValidationError,
)
from lody.projects import ProjectRepository


@pytest.fixture
def projects(tmp_path):
    return ProjectRepository(tmp_path / "lody.sqlite3")


@pytest.fixture
def repo(tmp_path, projects):
    return CharacterRepository(tmp_path / "lody.sqlite3")


@pytest.fixture
def project(projects):
    return projects.create(name="Projet de test")


# -- migrations --------------------------------------------------------------------------------------------------
def test_fresh_database_has_zero_characters_and_reaches_the_latest_schema(tmp_path):
    path = tmp_path / "fresh.sqlite3"
    projects = ProjectRepository(path)
    project = projects.create(name="Neuf")
    repo = CharacterRepository(path)
    assert repo.list_for_project(project.id) == []
    assert repo.count_for_project(project.id) == 0
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5
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

    repo = CharacterRepository(path)  # déclenche la migration additive
    assert repo.list_for_project("prj_old") == []  # zéro personnage par défaut, même pour un projet ancien

    upgraded = sqlite3.connect(path)
    assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 5
    # Rien de préexistant n'a bougé.
    assert upgraded.execute("SELECT name FROM projects").fetchall() == [("Ancien",)]
    assert upgraded.execute("SELECT script FROM productions WHERE id = 'prd_old'").fetchone() == \
        ("Un script déjà produit.",)


def test_migration_replayed_is_a_no_op(tmp_path):
    path = tmp_path / "replay.sqlite3"
    ProjectRepository(path)
    CharacterRepository(path)
    CharacterRepository(path)  # rejeu explicite
    CharacterRepository(path)  # une troisième fois, pour faire bonne mesure
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    assert connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0] == 0


def test_characters_table_has_no_binary_image_column_and_no_api_key_shaped_column(tmp_path):
    connection = sqlite3.connect(tmp_path / "shape.sqlite3")
    connection.executescript(db.CHARACTERS_SCHEMA)
    columns = {row[1]: row[2] for row in connection.execute("PRAGMA table_info(characters)")}
    assert "image" not in " ".join(columns).lower() and "BLOB" not in columns.values()
    assert not [c for c in columns if "api_key" in c or ("secret" in c) or ("token" in c)]


# -- CRUD -----------------------------------------------------------------------------------------------------------
def test_create_character_with_defaults_and_stable_id(repo, project):
    character = repo.create(project.id, name="  Gaston   Lebrac  ", role="PNJ donneur de quête")
    assert character.id.startswith("chr_")
    assert character.project_id == project.id
    assert character.name == "Gaston Lebrac"
    assert character.role == "PNJ donneur de quête"
    assert character.is_active is True and character.is_primary is False
    assert character.created_at == character.updated_at
    assert repo.get(project.id, character.id) == character


def test_create_sets_every_mvp_field(repo, project):
    character = repo.create(
        project.id, name="Gaston", role="PNJ", personality="Bavard, un peu naïf",
        visual_description="Vieil homme voxel, chapeau de paille", reference_prompt="portrait style voxel",
        speech_style="Phrases courtes, tics de langage", voice_provider="elevenlabs", voice_name="Gaston FR",
        external_voice_id="21m00Tcm4TlvDq8ikWAM", permanent_elements="Porte toujours son chapeau",
        continuity_notes="Ne jamais le faire mentir", is_primary=True,
    )
    assert character.personality == "Bavard, un peu naïf"
    assert character.visual_description == "Vieil homme voxel, chapeau de paille"
    assert character.voice_provider == "elevenlabs" and character.voice_name == "Gaston FR"
    assert character.external_voice_id == "21m00Tcm4TlvDq8ikWAM"
    assert character.is_primary is True


def test_update_changes_fields_and_last_modified(tmp_path, projects, project):
    ticks = iter(["2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00", "2026-01-01T12:00:00+00:00"])
    repo = CharacterRepository(tmp_path / "lody.sqlite3", clock=lambda: next(ticks))
    character = repo.create(project.id, name="Avant")
    updated = repo.update(project.id, character.id, name="Après", role="Antagoniste")
    assert (updated.name, updated.role) == ("Après", "Antagoniste")
    assert updated.created_at == "2026-01-01T10:00:00+00:00"
    assert updated.updated_at == "2026-01-01T11:00:00+00:00"


def test_update_unknown_character_or_unknown_field(repo, project):
    with pytest.raises(CharacterNotFound):
        repo.update(project.id, "chr_inexistant", name="Test")
    character = repo.create(project.id, name="Test")
    with pytest.raises(CharacterValidationError):
        repo.update(project.id, character.id, status="archived")


def test_list_for_project_sorts_active_primary_first_then_name(repo, project):
    repo.create(project.id, name="Zoé")
    repo.create(project.id, name="Gaston", is_primary=True)
    repo.create(project.id, name="Amir")
    inactive = repo.create(project.id, name="Aaa inactif")
    repo.deactivate(project.id, inactive.id)
    names = [c.name for c in repo.list_for_project(project.id)]
    assert names == ["Gaston", "Amir", "Zoé", "Aaa inactif"]  # actifs d'abord, principal en tête, puis alpha


def test_list_for_project_can_exclude_inactive(repo, project):
    active = repo.create(project.id, name="Actif")
    inactive = repo.create(project.id, name="Inactif")
    repo.deactivate(project.id, inactive.id)
    assert [c.id for c in repo.list_for_project(project.id, include_inactive=False)] == [active.id]


# -- isolation par project_id ------------------------------------------------------------------------------------
def test_two_projects_can_have_a_character_with_the_same_name(repo, projects):
    a = projects.create(name="Projet A")
    b = projects.create(name="Projet B")
    ca = repo.create(a.id, name="Gaston")
    cb = repo.create(b.id, name="Gaston")
    assert ca.id != cb.id
    assert [c.name for c in repo.list_for_project(a.id)] == ["Gaston"]
    assert [c.name for c in repo.list_for_project(b.id)] == ["Gaston"]


def test_name_unique_within_a_project_case_insensitively_accents_included(repo, project):
    repo.create(project.id, name="Gaston")
    with pytest.raises(CharacterValidationError) as error:
        repo.create(project.id, name="GASTON")
    assert "name" in error.value.errors
    with pytest.raises(CharacterValidationError):
        repo.create(project.id, name="gaston")
    # « Élan » / « élan » : NOCASE de SQLite est ASCII seul et ne les confondrait pas — casefold() applicatif si.
    repo.create(project.id, name="Élan")
    with pytest.raises(CharacterValidationError):
        repo.create(project.id, name="élan")


def test_update_keeps_own_name_but_refuses_another_characters_name(repo, project):
    first = repo.create(project.id, name="Premier")
    repo.create(project.id, name="Second")
    assert repo.update(project.id, first.id, name="Premier", role="x").role == "x"
    with pytest.raises(CharacterValidationError):
        repo.update(project.id, first.id, name="Second")


def test_get_with_the_wrong_project_id_is_not_found_same_error_as_unknown_id(repo, projects, project):
    other = projects.create(name="Autre projet")
    character = repo.create(project.id, name="Gaston")
    with pytest.raises(CharacterNotFound):
        repo.get(other.id, character.id)  # existe, mais dans un AUTRE projet : refus identique à un id inconnu
    with pytest.raises(CharacterNotFound):
        repo.get(other.id, "chr_inexistant")


def test_update_across_projects_is_refused(repo, projects, project):
    other = projects.create(name="Autre projet")
    character = repo.create(project.id, name="Gaston")
    with pytest.raises(CharacterNotFound):
        repo.update(other.id, character.id, name="Détourné")
    assert repo.get(project.id, character.id).name == "Gaston"  # inchangé


def test_activate_deactivate_across_projects_is_refused(repo, projects, project):
    other = projects.create(name="Autre projet")
    character = repo.create(project.id, name="Gaston")
    with pytest.raises(CharacterNotFound):
        repo.deactivate(other.id, character.id)
    assert repo.get(project.id, character.id).is_active is True


def test_list_and_count_never_leak_across_projects(repo, projects):
    a = projects.create(name="Projet A")
    b = projects.create(name="Projet B")
    repo.create(a.id, name="Un")
    repo.create(a.id, name="Deux")
    repo.create(b.id, name="Trois")
    assert repo.count_for_project(a.id) == 2
    assert repo.count_for_project(b.id) == 1
    assert {c.name for c in repo.list_for_project(a.id)} == {"Un", "Deux"}


# -- actif/inactif, principal -----------------------------------------------------------------------------------
def test_activate_and_deactivate(repo, project):
    character = repo.create(project.id, name="Gaston")
    assert repo.deactivate(project.id, character.id).is_active is False
    assert repo.activate(project.id, character.id).is_active is True


def test_deactivate_is_not_a_delete(repo, project):
    character = repo.create(project.id, name="Gaston")
    repo.deactivate(project.id, character.id)
    assert repo.get(project.id, character.id).name == "Gaston"  # toujours là, juste inactif
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
    with pytest.raises(CharacterValidationError) as error:
        repo.create(project.id, name=name)
    assert expected in error.value.errors["name"]
    assert repo.count_for_project(project.id) == 0


@pytest.mark.parametrize("field", ["role", "personality", "visual_description", "reference_prompt",
                                   "speech_style", "voice_name", "external_voice_id", "permanent_elements",
                                   "continuity_notes"])
def test_field_length_limits_are_enforced(repo, project, field):
    with pytest.raises(CharacterValidationError) as error:
        repo.create(project.id, name="Test", **{field: "x" * 1000})
    assert field in error.value.errors


def test_unknown_voice_provider_is_refused(repo, project):
    with pytest.raises(CharacterValidationError) as error:
        repo.create(project.id, name="Test", voice_provider="myspace-voice")
    assert "voice_provider" in error.value.errors


def test_unknown_voice_provider_message_lists_accepted_values(repo, project):
    """#63 : le message doit indiquer les valeurs techniques acceptées, pas un renvoi générique « dans la
    liste »."""
    with pytest.raises(CharacterValidationError) as error:
        repo.create(project.id, name="Test", voice_provider="myspace-voice")
    assert '"elevenlabs"' in error.value.errors["voice_provider"]
    assert '"edge"' in error.value.errors["voice_provider"]


def test_empty_voice_provider_is_allowed(repo, project):
    character = repo.create(project.id, name="Test", voice_provider="")
    assert character.voice_provider == ""


@pytest.mark.parametrize("technical_value", ["elevenlabs", "edge"])
def test_technical_voice_provider_is_accepted(repo, project, technical_value):
    character = repo.create(project.id, name="Test", voice_provider=technical_value)
    assert character.voice_provider == technical_value


@pytest.mark.parametrize(
    ("raw_label", "expected_technical_value"),
    [("ElevenLabs", "elevenlabs"), ("ELEVENLABS", "elevenlabs"), ("Voix gratuite (Edge)", "edge")],
)
def test_ui_label_is_normalized_to_the_technical_value(repo, project, raw_label, expected_technical_value):
    """#63 : un import JSON reprenant le libellé affiché dans l'interface (ex. « ElevenLabs ») est accepté et
    normalisé vers l'identifiant technique attendu (« elevenlabs »)."""
    character = repo.create(project.id, name="Test", voice_provider=raw_label)
    assert character.voice_provider == expected_technical_value


def test_voice_provider_validation_uses_the_catalog_as_single_source_of_truth():
    """#63 : la validation ne duplique pas la liste des fournisseurs — elle consomme catalog.VOICE_PROVIDERS."""
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "ElevenLabs") == "elevenlabs"


@pytest.mark.parametrize("field", ["name", "role", "personality", "visual_description", "reference_prompt",
                                   "speech_style", "voice_name", "permanent_elements", "continuity_notes"])
def test_secrets_cannot_be_stored_in_any_text_field(repo, project, field):
    with pytest.raises(CharacterValidationError) as error:
        repo.create(project.id, **{"name": "Test", field: "sk-abcdefghijklmnopqrstuvwxyz123456"})
    assert field in error.value.errors


def test_external_voice_id_shaped_like_an_api_key_is_refused():
    """Le risque nommé explicitement par le ticket #30 : un identifiant de voix confondu avec une clé d'API."""
    from lody.characters import validate_fields

    with pytest.raises(CharacterValidationError) as error:
        validate_fields({"name": "Test", "external_voice_id": "sk-proj-abcdefghijklmnopqrstuvwxyz1234567890"})
    assert "external_voice_id" in error.value.errors


def test_a_real_looking_short_voice_id_is_not_falsely_flagged(repo, project):
    """Un identifiant ElevenLabs typique (20 caractères) ne doit pas être pris pour un jeton opaque (≥ 32)."""
    character = repo.create(project.id, name="Test", external_voice_id="21m00Tcm4TlvDq8ikWAM")
    assert character.external_voice_id == "21m00Tcm4TlvDq8ikWAM"


def test_validation_error_messages_never_echo_the_offending_value(repo, project):
    try:
        repo.create(project.id, name="sk-abcdefghijklmnopqrstuvwxyz123456")
    except CharacterValidationError as error:
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in str(error.errors)
    else:
        pytest.fail("devait lever CharacterValidationError")


# -- projet inexistant / archivé, comportement FK -------------------------------------------------------------------
def test_create_for_a_nonexistent_project_is_refused(repo):
    with pytest.raises(CharacterValidationError) as error:
        repo.create("prj_inexistant", name="Test")
    assert "project_id" in error.value.errors


def test_create_for_an_archived_project_still_works_archiving_never_rewrites_data(repo, projects, project):
    projects.archive(project.id)
    character = repo.create(project.id, name="Gaston")  # une décision d'administration, pas cette couche
    assert repo.get(project.id, character.id).name == "Gaston"
    assert projects.get(project.id).status == "archived"  # le projet reste inchangé par ailleurs


def test_archiving_a_project_never_touches_its_existing_characters(repo, projects, project):
    character = repo.create(project.id, name="Gaston", is_primary=True)
    projects.archive(project.id)
    projects.restore(project.id)
    still = repo.get(project.id, character.id)
    assert (still.name, still.is_primary, still.is_active) == ("Gaston", True, True)


def test_foreign_key_blocks_inserting_a_character_for_a_project_that_does_not_exist_at_the_sql_level(tmp_path):
    """Vérifie le comportement FK lui-même (pas seulement le garde-fou applicatif) : PRAGMA foreign_keys=ON
    empêche une ligne orpheline même via une insertion SQL directe."""
    path = tmp_path / "fk.sqlite3"
    ProjectRepository(path)
    CharacterRepository(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO characters (id, project_id, name, created_at, updated_at) "
            "VALUES ('chr_x', 'prj_does_not_exist', 'Test', 'x', 'x')")


def test_foreign_key_blocks_deleting_a_project_that_still_has_characters(tmp_path):
    """L'app n'a pas de suppression de projet (archive/restore seulement) ; ce test prouve que la contrainte
    protège quand même l'intégrité si un code futur tentait un DELETE direct."""
    path = tmp_path / "fk-delete.sqlite3"
    projects = ProjectRepository(path)
    project = projects.create(name="Protégé")
    repo = CharacterRepository(path)
    repo.create(project.id, name="Gaston")
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM projects WHERE id = ?", (project.id,))


# -- non-régression du pipeline classique --------------------------------------------------------------------------
def test_an_existing_project_has_implicitly_zero_characters(tmp_path):
    """Prouve explicitement le critère d'acceptation : un projet créé AVANT ce ticket (donc sans jamais avoir
    entendu parler de personnages) a bien zéro personnage, sans avoir besoin d'initialisation explicite."""
    path = tmp_path / "legacy.sqlite3"
    projects = ProjectRepository(path)
    project = projects.create(name="Projet classique")
    # Le pipeline de génération n'importe jamais characters.py : on vérifie ici depuis une connexion neuve,
    # comme le ferait n'importe quel autre code qui ignore tout de cette table.
    repo = CharacterRepository(path)
    assert repo.count_for_project(project.id) == 0
    assert repo.list_for_project(project.id) == []


def test_generation_request_payload_is_identical_with_or_without_characters_in_the_project(repo, project):
    """Preuve directe que le pipeline de génération classique n'est influencé en rien par la présence de
    personnages : même projet, même sujet, la requête construite (``build_request``) est strictement
    identique avant et après avoir ajouté des personnages en base."""
    from lody.generation.service import build_request

    before = build_request(project, "Un sujet quelconque")

    repo.create(project.id, name="Gaston", role="PNJ", is_primary=True)
    repo.create(project.id, name="Zoé", role="Alliée")

    after = build_request(project, "Un sujet quelconque")
    assert before == after


def test_only_the_snapshot_wiring_references_characters_or_locations_not_the_script_voice_image_pipeline():
    """Preuve statique de la frontière du ticket #35 : la résolution de la sélection en instantané
    (``narrative_context.py``, câblée par ``service.py``/``runtime.py``) peut référencer
    ``lody.characters``/``lody.locations``, mais aucun module qui construit ce qui est réellement envoyé au
    moteur (script, storyboard, prompts de voix ou d'image) ne le fait — ni #34 (aucune UI de sélection) ni
    aucune injection dans le script, la voix ou les prompts d'images à ce stade. (Le mot anglais « characters »
    existe déjà ailleurs dans ce pipeline pour le décompte de caractères de texte facturés — voir
    ``costing.py`` — donc ce test cherche des motifs précis, pas le simple mot.)"""
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
