"""Interface du sélecteur de voix ElevenLabs (#55, AppTest) : paramètres du projet, fiche personnage,
conservation du mode manuel, compatibilité avec un ancien voice_id, absence de fuite de la clé."""

from __future__ import annotations

import json

import pytest

from lody import settings
from lody.characters import CharacterRepository
from lody.generation import elevenlabs_voices as ev
from lody.projects import ProjectRepository
from test.lody.test_generation_ui import _button, _fresh_resources, _labels, _project, _run, _text, engine  # noqa: F401

pytest.importorskip("streamlit.testing.v1")

SECRET_KEY = "sk-real-secret-should-never-leak-anywhere-0123456789"


@pytest.fixture(autouse=True)
def _no_catalog_by_default(tmp_path, monkeypatch):
    """Par défaut : ni config.toml lisible ni rapport — catalogue indisponible, mode manuel actif. Chaque test
    qui veut un catalogue écrit explicitement le fichier ci-dessous."""
    monkeypatch.setenv("LODY_VOICE_CATALOG_REPORT", str(tmp_path / "elevenlabs-voices.json"))
    return tmp_path


def _write_report(tmp_path, voices):
    (tmp_path / "elevenlabs-voices.json").write_text(
        json.dumps({"version": 1, "generated_at": "2026-01-01T00:00:00+00:00",
                    "voices": [v.to_dict() for v in voices]}), encoding="utf-8")


def _all_text(app):
    return _text(app) + " ".join(str(c.value) for c in app.caption)


def _settings(project):
    return _run({"projet": project.id, "vue": "parametres"})


def _open_characters(project):
    return _run({"projet": project.id, "vue": "personnages"})


def _char_repo():
    return CharacterRepository(settings.db_path())


# -- sélection dans un projet -----------------------------------------------------------------------------------
def test_selecting_a_voice_in_project_settings_prefills_the_manual_fields(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [ev.VoiceInfo("v1", "Rachel", preview_url="https://cdn/rachel.mp3")])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    assert "Voix ElevenLabs (recherche)" in _text(app) and "Rachel" in _text(app)
    app = _button(app, "Utiliser cette voix").click().run()
    p = f"set_{project.id}"
    assert app.text_input(key=f"{p}_voice_name").value == "Rachel"
    assert app.text_input(key=f"{p}_voice_id").value == "v1"


def test_picker_is_not_shown_when_project_provider_is_not_elevenlabs(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [ev.VoiceInfo("v1", "Rachel")])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="edge")
    app = _settings(project)
    assert "Voix ElevenLabs (recherche)" not in _text(app)


def test_search_box_filters_the_catalog_in_project_settings(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [ev.VoiceInfo("v1", "Rachel"), ev.VoiceInfo("v2", "Adam")])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app = app.text_input(key=f"{p}_ev_search").set_value("rach").run()
    text = _text(app)
    assert "Rachel" in text and "Adam" not in text


# -- sélection dans un personnage --------------------------------------------------------------------------------
def test_selecting_a_voice_when_creating_a_new_character_prefills_its_fields(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [ev.VoiceInfo("v1", "Rachel")])
    project = _project()
    app = _open_characters(project)
    app = _button(app, "Ajouter un personnage").click().run()
    assert "Voix ElevenLabs (recherche)" in _text(app)
    app = _button(app, "Utiliser cette voix").click().run()
    p = f"char_{project.id}_new"
    assert app.text_input(key=f"{p}_voice_name").value == "Rachel"
    assert app.text_input(key=f"{p}_external_voice_id").value == "v1"
    app.text_input(key=f"{p}_name").set_value("Gaston").run()
    app = _button(app, "Enregistrer").click().run()
    created = _char_repo().list_for_project(project.id)[0]
    assert created.voice_name == "Rachel" and created.external_voice_id == "v1"


def test_picker_hidden_when_editing_a_character_with_a_different_provider_already_set(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [ev.VoiceInfo("v1", "Rachel")])
    project = _project()
    _char_repo().create(project.id, name="Gaston", voice_provider="edge", voice_name="Voix gratuite")
    app = _open_characters(project)
    app = _button(app, "Modifier").click().run()
    assert "Voix ElevenLabs (recherche)" not in _text(app)


def test_picker_shown_when_editing_a_character_already_on_elevenlabs(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [ev.VoiceInfo("v9", "Nova")])
    project = _project()
    _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Ancienne voix",
                        external_voice_id="old-id-123")
    app = _open_characters(project)
    app = _button(app, "Modifier").click().run()
    assert "Voix ElevenLabs (recherche)" in _text(app) and "Nova" in _text(app)


# -- conservation du mode manuel -----------------------------------------------------------------------------------
def test_manual_voice_id_entry_still_works_when_catalog_is_unavailable(engine, _no_catalog_by_default):  # noqa: F811
    """Aucun rapport écrit par ce test : le catalogue est indisponible par construction (fixture autouse)."""
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    text = _text(app)
    assert "Voix ElevenLabs (recherche)" in text  # la section existe...
    assert "saisie manuelle" in text.lower()       # ...mais explique clairement qu'il faut saisir à la main
    p = f"set_{project.id}"
    app.text_input(key=f"{p}_voice_id").set_value("manually-typed-voice-id").run()
    app = _button(app, "Enregistrer les paramètres").click().run()
    updated = ProjectRepository(settings.db_path()).get(project.id)
    assert updated.settings["brief"]["voice_id"] == "manually-typed-voice-id"


def test_manual_entry_in_a_character_form_still_works_when_catalog_is_unavailable(engine, _no_catalog_by_default):  # noqa: F811
    project = _project()
    app = _open_characters(project)
    app = _button(app, "Ajouter un personnage").click().run()
    p = f"char_{project.id}_new"
    app.text_input(key=f"{p}_name").set_value("Gaston").run()
    app.text_input(key=f"{p}_external_voice_id").set_value("manual-id-456").run()
    app = _button(app, "Enregistrer").click().run()
    created = _char_repo().list_for_project(project.id)[0]
    assert created.external_voice_id == "manual-id-456"


# -- compatibilité avec un ancien voice_id (aucune interaction avec le catalogue) ------------------------------------
def test_legacy_project_with_only_a_voice_id_still_loads_and_saves_correctly(engine, _no_catalog_by_default):  # noqa: F811
    project = _project()
    repo = ProjectRepository(settings.db_path())
    repo.update(project.id, voice_provider="elevenlabs",
               settings={**project.settings, "brief": {**project.settings.get("brief", {}),
                                                        "voice_id": "legacy-voice-abc123"}})
    app = _settings(project)
    assert not app.exception
    p = f"set_{project.id}"
    assert app.text_input(key=f"{p}_voice_id").value == "legacy-voice-abc123"
    app = _button(app, "Enregistrer les paramètres").click().run()
    assert not app.exception
    assert ProjectRepository(settings.db_path()).get(project.id).settings["brief"]["voice_id"] == "legacy-voice-abc123"


def test_legacy_character_with_only_an_external_voice_id_still_loads(engine, _no_catalog_by_default):  # noqa: F811
    project = _project()
    _char_repo().create(project.id, name="Ancien perso", voice_provider="elevenlabs",
                        external_voice_id="legacy-char-voice-789")
    app = _open_characters(project)
    assert not app.exception
    assert "legacy-char-voice-789" not in _text(app)  # jamais affiché en clair hors formulaire...
    app = _button(app, "Modifier").click().run()
    p = f"char_{project.id}_" + _char_repo().list_for_project(project.id)[0].id
    assert app.text_input(key=f"{p}_external_voice_id").value == "legacy-char-voice-789"


# -- messages clairs (clé manquante/indisponible) et bouton d'actualisation -----------------------------------------
def test_clear_message_when_no_catalog_is_available(engine, _no_catalog_by_default):  # noqa: F811
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    text = _text(app)
    assert "indisponible" in text.lower() or "aucune clé" in text.lower()


def test_refresh_button_is_present_and_clickable_without_error(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [ev.VoiceInfo("v1", "Rachel")])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    assert "Actualiser les voix" in _labels(app)
    app = _button(app, "Actualiser les voix").click().run()
    assert not app.exception


def test_no_huge_list_results_are_capped_for_a_broad_search(engine, _no_catalog_by_default):  # noqa: F811
    many = [ev.VoiceInfo(f"v{i}", f"Voix numéro {i}") for i in range(50)]
    _write_report(_no_catalog_by_default, many)
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    pick_buttons = [b for b in app.button if b.label == "Utiliser cette voix"]
    assert len(pick_buttons) <= 20  # voice_picker.MAX_RESULTS_SHOWN
    assert "autre(s) résultat(s)" in _all_text(app)


# -- absence de fuite de la clé dans le HTML rendu -------------------------------------------------------------------
def test_api_key_never_appears_in_the_rendered_settings_page(engine, tmp_path, monkeypatch):  # noqa: F811
    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    monkeypatch.setattr(ev, "fetch_all", lambda api_key, **kwargs: [ev.VoiceInfo("v1", "Rachel")])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    rendered = _text(app) + " ".join(str(getattr(el, "value", "")) for el in app.text_input)
    assert SECRET_KEY not in rendered
