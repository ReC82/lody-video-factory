"""Couche projets : création, validation, persistance, archivage, exemples, secrets."""

import json
import sqlite3

import pytest

from lody import catalog
from lody.projects import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    ProjectNotFound,
    ProjectRepository,
    ProjectValidationError,
)
from lody.seeds import SEED_PROJECTS


@pytest.fixture
def repo(tmp_path):
    return ProjectRepository(tmp_path / "lody.sqlite3")


def test_create_project_with_defaults_and_stable_id(repo):
    project = repo.create(name="  Ma   chaîne  ", platforms=["tiktok", "youtube_shorts"])
    assert project.id.startswith("prj_")
    assert project.name == "Ma chaîne"
    assert project.status == STATUS_ACTIVE
    assert project.platforms == ["youtube_shorts", "tiktok"]  # ordre du catalogue
    assert project.created_at == project.updated_at
    assert project.text_provider == "openai" and project.voice_provider == "elevenlabs"
    assert repo.get(project.id) == project


@pytest.mark.parametrize(
    ("name", "expected"),
    [("", "Donne un nom"), ("   ", "Donne un nom"), ("a", "au moins 2"), ("x" * 81, "trop long")],
)
def test_name_validation_messages_are_human(repo, name, expected):
    with pytest.raises(ProjectValidationError) as error:
        repo.create(name=name)
    assert expected in error.value.errors["name"]
    assert repo.count() == 0


def test_duplicate_name_is_refused_case_insensitively(repo):
    repo.create(name="Ma chaîne")
    with pytest.raises(ProjectValidationError) as error:
        repo.create(name="MA CHAÎNE")
    assert "porte déjà ce nom" in error.value.errors["name"]


@pytest.mark.parametrize(
    "field", ["content_type", "language", "format", "text_provider", "visual_provider", "voice_provider", "music_provider"]
)
def test_unknown_choices_are_refused(repo, field):
    with pytest.raises(ProjectValidationError) as error:
        repo.create(name="Test", **{field: "inconnu"})
    assert field in error.value.errors


def test_unknown_voice_provider_message_lists_accepted_values(repo):
    """#63 : le message doit indiquer les identifiants techniques acceptés."""
    with pytest.raises(ProjectValidationError) as error:
        repo.create(name="Test", voice_provider="inconnu")
    assert '"elevenlabs"' in error.value.errors["voice_provider"]
    assert '"edge"' in error.value.errors["voice_provider"]


@pytest.mark.parametrize("technical_value", ["elevenlabs", "edge"])
def test_technical_voice_provider_is_accepted(repo, technical_value):
    project = repo.create(name="Test", voice_provider=technical_value)
    assert project.voice_provider == technical_value


@pytest.mark.parametrize(
    ("field", "raw_label", "expected_technical_value"),
    [
        ("voice_provider", "ElevenLabs", "elevenlabs"),
        ("voice_provider", "Voix gratuite (Edge)", "edge"),
        ("text_provider", "OpenAI", "openai"),
        ("visual_provider", "Images IA (OpenAI)", "openai_image"),
    ],
)
def test_ui_label_is_normalized_to_the_technical_value(repo, field, raw_label, expected_technical_value):
    """#63 : un import JSON reprenant le libellé affiché dans l'interface (ex. « ElevenLabs ») est accepté et
    normalisé vers l'identifiant technique attendu, pour tous les champs de type catalogue."""
    project = repo.create(name="Test", **{field: raw_label})
    assert getattr(project, field) == expected_technical_value


def test_choice_validation_uses_the_catalog_as_single_source_of_truth():
    """#63 : la validation ne duplique pas les listes de choix — elle consomme catalog.CATALOGS."""
    for field, options in catalog.CATALOGS.items():
        for option in options:
            assert catalog.normalize(options, option.label) == option.value


def test_unknown_platform_is_refused(repo):
    with pytest.raises(ProjectValidationError) as error:
        repo.create(name="Test", platforms=["myspace"])
    assert "platforms" in error.value.errors


def test_projects_survive_reopening_the_database(tmp_path):
    path = tmp_path / "lody.sqlite3"
    created = ProjectRepository(path).create(name="Persistant", description="Toujours là", settings={"note": "ok"})
    reopened = ProjectRepository(path)
    found = reopened.get(created.id)
    assert (found.name, found.description, found.settings) == ("Persistant", "Toujours là", {"note": "ok"})
    assert [p.id for p in reopened.list_projects()] == [created.id]


def test_update_changes_fields_and_last_modified(tmp_path):
    ticks = iter(["2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00", "2026-01-01T12:00:00+00:00"])
    repo = ProjectRepository(tmp_path / "lody.sqlite3", clock=lambda: next(ticks))
    project = repo.create(name="Avant")
    updated = repo.update(project.id, name="Après", tone="Sérieux et posé", platforms=["linkedin"])
    assert (updated.name, updated.tone, updated.platforms) == ("Après", "Sérieux et posé", ["linkedin"])
    assert updated.created_at == "2026-01-01T10:00:00+00:00"
    assert updated.updated_at == "2026-01-01T11:00:00+00:00"


def test_update_keeps_own_name_but_refuses_another_projects_name(repo):
    first = repo.create(name="Premier")
    repo.create(name="Second")
    assert repo.update(first.id, name="Premier", description="x").description == "x"
    with pytest.raises(ProjectValidationError):
        repo.update(first.id, name="Second")


def test_update_unknown_project_and_unknown_field(repo):
    with pytest.raises(ProjectNotFound):
        repo.update("prj_inexistant", name="Test")
    project = repo.create(name="Test")
    with pytest.raises(ProjectValidationError):
        repo.update(project.id, status="archived")


def test_archive_and_restore(repo):
    project = repo.create(name="À archiver")
    assert repo.archive(project.id).status == STATUS_ARCHIVED
    assert repo.list_projects(STATUS_ACTIVE) == []
    assert [p.id for p in repo.list_projects(STATUS_ARCHIVED)] == [project.id]
    assert repo.restore(project.id).status == STATUS_ACTIVE
    with pytest.raises(ProjectNotFound):
        repo.archive("prj_inexistant")


def test_seed_is_idempotent_and_never_resets_user_changes(tmp_path):
    path = tmp_path / "lody.sqlite3"
    repo = ProjectRepository(path)
    assert repo.seed_defaults(SEED_PROJECTS) == 2
    assert repo.seed_defaults(SEED_PROJECTS) == 0
    assert {p.name for p in repo.list_projects()} == {"Audiovisuel", "LodyCrypto"}

    crypto = next(p for p in repo.list_projects() if p.name == "LodyCrypto")
    repo.update(crypto.id, name="Crypto perso", tone="Décontracté")
    audio = next(p for p in repo.list_projects() if p.name == "Audiovisuel")
    repo.archive(audio.id)

    # « Redémarrage » : nouvel objet sur la même base.
    restarted = ProjectRepository(path)
    assert restarted.seed_defaults(SEED_PROJECTS) == 0
    assert restarted.count() == 2
    assert restarted.get(crypto.id).name == "Crypto perso"
    assert restarted.get(crypto.id).tone == "Décontracté"
    assert restarted.get(audio.id).status == STATUS_ARCHIVED


def test_seeds_contain_no_secret_and_reference_providers_only():
    for seed in SEED_PROJECTS:
        assert "api_key" not in json.dumps(seed).lower()
        assert seed["text_provider"] and seed["voice_provider"]


@pytest.mark.parametrize(
    "fields",
    [
        {"description": "ma clé sk-abcdefghijklmnopqrstuvwxyz123456"},
        {"name": "Projet Bearer abcdefghijklmnopqrstuvwxyz"},
        {"visual_style": "ghp_abcdefghijklmnopqrstuvwxyz0123456789"},
        {"voice_name": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8"},
        {"settings": {"api_key": "peu importe"}},
        {"settings": {"nested": {"password": "x"}}},
        {"settings": {"note": "xi-abcdefghijklmnop1234"}},
    ],
)
def test_secrets_cannot_be_stored_in_a_project(repo, fields):
    payload = {"name": "Projet sûr", **fields}
    with pytest.raises(ProjectValidationError) as error:
        repo.create(**payload)
    message = " ".join(error.value.errors.values())
    assert "clé secrète" in message
    # Le message ne recopie jamais la valeur fautive.
    for value in fields.values():
        assert str(value) not in message
    assert repo.count() == 0


def test_secret_check_also_applies_to_updates(repo):
    project = repo.create(name="Sain")
    with pytest.raises(ProjectValidationError):
        repo.update(project.id, description="sk-abcdefghijklmnopqrstuvwxyz123456")
    assert repo.get(project.id).description == ""


def test_database_file_holds_no_secret_pattern(tmp_path):
    path = tmp_path / "lody.sqlite3"
    repo = ProjectRepository(path)
    repo.seed_defaults(SEED_PROJECTS)
    repo.create(name="Autre", description="Description normale")
    with sqlite3.connect(path) as connection:
        dump = "\n".join(connection.iterdump())
    for marker in ("sk-", "Bearer ", "api_key", "password"):
        assert marker not in dump


# -- mise à niveau des exemples (paramètres de production de LodyCrypto) ------------------
def _old_seeds():
    """Exemples tels qu'ils étaient déployés avant l'ajout des paramètres de production."""
    import copy

    old = copy.deepcopy(SEED_PROJECTS)
    for seed in old:
        seed.pop("legacy_values", None)
        seed["settings"].pop("brief", None)
        seed["settings"]["target_duration"] = "45 – 65 s"
    old[1]["tone"] = "Pédagogique et dynamique"
    old[1]["visual_style"] = "Sombre et moderne, cyan et bleu — sans texte ni logo"
    return old


def test_upgrade_adds_the_brief_to_an_existing_lodycrypto_once(tmp_path):
    repo = ProjectRepository(tmp_path / "lody.sqlite3")
    repo.seed_defaults(_old_seeds())
    assert repo.upgrade_seeds(SEED_PROJECTS) == 1
    assert repo.upgrade_seeds(SEED_PROJECTS) == 0  # idempotent
    crypto = next(p for p in repo.list_projects() if p.name == "LodyCrypto")
    assert crypto.settings["brief"]["audience"] == "Débutants complets en crypto"
    assert "target_duration" not in crypto.settings
    assert crypto.tone == "Simple, honnête, dynamique, sans posture d’expert"
    assert "sans marque" in crypto.visual_style
    assert repo.count() == 2


def test_upgrade_never_touches_audiovisuel(tmp_path):
    repo = ProjectRepository(tmp_path / "lody.sqlite3")
    repo.seed_defaults(_old_seeds())
    before = next(p for p in repo.list_projects() if p.name == "Audiovisuel")
    repo.seed_defaults(SEED_PROJECTS)
    after = repo.get(before.id)
    assert after == before


def test_upgrade_keeps_user_modified_fields(tmp_path):
    repo = ProjectRepository(tmp_path / "lody.sqlite3")
    repo.seed_defaults(_old_seeds())
    crypto = next(p for p in repo.list_projects() if p.name == "LodyCrypto")
    repo.update(crypto.id, tone="Mon ton perso")
    repo.upgrade_seeds(SEED_PROJECTS)
    upgraded = repo.get(crypto.id)
    assert upgraded.tone == "Mon ton perso"             # modification utilisateur conservée
    assert "sans marque" in upgraded.visual_style        # champ resté à sa valeur d'origine : mis à niveau
    assert "brief" in upgraded.settings


def test_upgrade_does_not_overwrite_an_existing_brief(tmp_path):
    repo = ProjectRepository(tmp_path / "lody.sqlite3")
    repo.seed_defaults(SEED_PROJECTS)
    crypto = next(p for p in repo.list_projects() if p.name == "LodyCrypto")
    repo.update(crypto.id, settings={**crypto.settings, "brief": {**crypto.settings["brief"], "audience": "Modifié"}})
    assert repo.upgrade_seeds(SEED_PROJECTS) == 0
    assert repo.get(crypto.id).settings["brief"]["audience"] == "Modifié"
