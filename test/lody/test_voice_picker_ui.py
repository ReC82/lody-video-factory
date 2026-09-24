"""Sélecteur de voix ElevenLabs intégré à la section Voix des formulaires (#62, AppTest) : plus de bloc
séparé au-dessus du formulaire, une seule représentation de la voix sélectionnée, section « Saisie
manuelle » repliable (dépliée automatiquement quand la valeur existante n'est pas dans le catalogue),
recherche/filtre/copie/aperçu conservés, même logique pour le personnage et les paramètres du projet."""

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

RACHEL = ev.VoiceInfo("rachel001", "Rachel", category="generated", labels={"language": "fr", "gender": "female"},
                      preview_url="https://cdn.elevenlabs.io/rachel.mp3")
ADAM = ev.VoiceInfo("adam002id", "Adam", category="professional", labels={"language": "en"}, preview_url="")
NOVA = ev.VoiceInfo("nova003id", "Nova", category="generated", labels={"language": "en", "accent": "irish"},
                    preview_url="https://cdn.elevenlabs.io/nova.mp3")


@pytest.fixture(autouse=True)
def _no_catalog_by_default(tmp_path, monkeypatch):
    """Par défaut : aucun rapport — catalogue indisponible, mode manuel actif. Chaque test qui veut un
    catalogue écrit explicitement le fichier ci-dessous."""
    monkeypatch.setenv("LODY_VOICE_CATALOG_REPORT", str(tmp_path / "elevenlabs-voices.json"))
    return tmp_path


def _write_report(tmp_path, voices, generated_at="2026-01-01T00:00:00+00:00"):
    (tmp_path / "elevenlabs-voices.json").write_text(
        json.dumps({"version": 1, "generated_at": generated_at, "voices": [v.to_dict() for v in voices]}),
        encoding="utf-8")


def _all_text(app):
    return _text(app) + " ".join(str(c.value) for c in app.caption)


def _settings(project):
    return _run({"projet": project.id, "vue": "parametres"})


def _open_characters(project):
    return _run({"projet": project.id, "vue": "personnages"})


def _char_repo():
    return CharacterRepository(settings.db_path())


def _select_option(voice: ev.VoiceInfo) -> str:
    return f"{voice.name} — {voice.voice_id} — {voice.category or '—'}"


def _manual_expander(app):
    return next(e for e in app.expander if e.label == "Saisie manuelle")


def _open_new_character_form(project):
    app = _open_characters(project)
    return _button(app, "Ajouter un personnage").click().run()


def _open_edit_character_form(project, character_id):
    app = _open_characters(project)
    return next(b for b in app.button if b.key == f"edit_{character_id}").click().run()


# -- plus de bloc séparé au-dessus du formulaire, une seule représentation ------------------------------------------
def test_catalog_lives_inside_the_voice_expander_not_above_the_form(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    app = _open_new_character_form(project)
    assert not app.exception
    # « Voix ElevenLabs (recherche) » (le bandeau séparé du #55/#59) n'existe plus du tout.
    assert "Voix ElevenLabs (recherche)" not in _text(app)
    # Le sélecteur est bien présent, et une section « Saisie manuelle » repliable existe à ses côtés.
    pc = f"char_{project.id}_new"
    assert app.selectbox(key=f"{pc}_ev_select")
    assert _manual_expander(app)


def test_selected_voice_id_appears_only_once_in_a_code_block(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    app = _open_new_character_form(project)
    pc = f"char_{project.id}_new"
    app = app.selectbox(key=f"{pc}_ev_select").select(_select_option(RACHEL)).run()
    codes = [c.value for c in app.code]
    assert codes.count(RACHEL.voice_id) == 1  # jamais répété


def test_no_refresh_button_inside_the_form_catalog_is_still_current_every_rerun(engine, _no_catalog_by_default):  # noqa: F811
    """#62 : le composant vit maintenant dans le formulaire (st.button y est interdit). resolve_catalog()
    n'a toujours aucun cache : toute interaction reflète déjà le fichier le plus récent."""
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    app = _open_new_character_form(project)
    assert "Actualiser les voix" not in _labels(app)
    pc = f"char_{project.id}_new"
    assert app.selectbox(key=f"{pc}_ev_select").options == ["— Choisir une voix —", _select_option(RACHEL)]

    _write_report(_no_catalog_by_default, [NOVA])  # remplacement atomique par le script hôte
    app2 = _open_new_character_form(project)  # un nouveau rendu suffit, sans bouton dédié
    pc2 = f"char_{project.id}_new"
    assert app2.selectbox(key=f"{pc2}_ev_select").options == ["— Choisir une voix —", _select_option(NOVA)]


# -- création avec une voix du catalogue --------------------------------------------------------------------------
def test_new_character_with_an_available_catalog_starts_with_manual_collapsed(engine, _no_catalog_by_default):  # noqa: F811
    """Rien n'est encore choisi, mais le catalogue est utilisable : la saisie manuelle reste repliée par
    défaut pour privilégier le catalogue (elle se déplierait quand même si le catalogue était indisponible —
    voir test_manual_entry_still_works_when_catalog_is_unavailable)."""
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    app = _open_new_character_form(project)
    assert _manual_expander(app).proto.expanded is False


def test_creating_a_character_with_a_catalog_voice(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    app = _open_new_character_form(project)
    pc = f"char_{project.id}_new"
    app = app.selectbox(key=f"{pc}_ev_select").select(_select_option(RACHEL)).run()
    assert app.selectbox(key=f"{pc}_voice_provider").value == "elevenlabs"
    assert app.text_input(key=f"{pc}_voice_name").value == "Rachel"
    assert app.text_input(key=f"{pc}_external_voice_id").value == RACHEL.voice_id
    assert _char_repo().list_for_project(project.id) == []  # rien enregistré avant le clic Enregistrer
    app.text_input(key=f"{pc}_name").set_value("Gaston").run()
    app = _button(app, "Enregistrer").click().run()
    created = _char_repo().list_for_project(project.id)[0]
    assert (created.voice_provider, created.voice_name, created.external_voice_id) == ("elevenlabs", "Rachel", RACHEL.voice_id)


# -- modification d'un personnage ayant déjà une voix ElevenLabs (présélection, manuel replié) ------------------------
def test_editing_a_character_with_an_existing_catalog_voice_is_preselected_and_manual_is_collapsed(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    character = _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Rachel",
                                    external_voice_id=RACHEL.voice_id)
    app = _open_edit_character_form(project, character.id)
    pc = f"char_{project.id}_{character.id}"
    assert app.selectbox(key=f"{pc}_ev_select").value == RACHEL.voice_id
    assert _manual_expander(app).proto.expanded is False


# -- personnage avec identifiant historique absent du catalogue --------------------------------------------------------
def test_character_with_a_historical_id_absent_from_the_catalog_keeps_its_value_and_expands_manual(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    character = _char_repo().create(project.id, name="Zoé", voice_provider="elevenlabs", voice_name="Ancienne Voix",
                                    external_voice_id="old-historical-id-999")
    app = _open_edit_character_form(project, character.id)
    pc = f"char_{project.id}_{character.id}"
    assert app.selectbox(key=f"{pc}_ev_select").value == ""  # aucune correspondance dans le catalogue
    assert _manual_expander(app).proto.expanded is True  # jamais caché silencieusement
    assert app.text_input(key=f"{pc}_voice_name").value == "Ancienne Voix"
    assert app.text_input(key=f"{pc}_external_voice_id").value == "old-historical-id-999"


# -- saisie manuelle et autre fournisseur --------------------------------------------------------------------------
def test_character_with_a_different_provider_hides_the_catalog_and_expands_manual(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    character = _char_repo().create(project.id, name="Ana", voice_provider="edge", voice_name="Voix gratuite")
    app = _open_edit_character_form(project, character.id)
    pc = f"char_{project.id}_{character.id}"
    assert f"{pc}_ev_select" not in [w.key for w in app.selectbox]  # catalogue non affiché
    assert _manual_expander(app).proto.expanded is True
    assert app.text_input(key=f"{pc}_voice_name").value == "Voix gratuite"


def test_manual_entry_still_works_when_catalog_is_unavailable(engine, _no_catalog_by_default):  # noqa: F811
    project = _project()
    app = _open_new_character_form(project)
    pc = f"char_{project.id}_new"
    assert _manual_expander(app).proto.expanded is True  # rien d'autre à montrer : replié n'aurait aucun sens
    app.text_input(key=f"{pc}_name").set_value("Gaston").run()
    app.text_input(key=f"{pc}_external_voice_id").set_value("manual-id-456").run()
    app = _button(app, "Enregistrer").click().run()
    created = _char_repo().list_for_project(project.id)[0]
    assert created.external_voice_id == "manual-id-456"


# -- recherche et filtre generated -----------------------------------------------------------------------------------
def test_search_and_generated_filter_still_work_inside_the_form(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, ADAM])
    project = _project()
    app = _open_new_character_form(project)
    pc = f"char_{project.id}_new"
    assert app.checkbox(key=f"{pc}_ev_generated_only").value is True
    options = app.selectbox(key=f"{pc}_ev_select").options
    assert _select_option(ADAM) not in options and _select_option(RACHEL) in options

    app.checkbox(key=f"{pc}_ev_generated_only").uncheck().run()
    assert _select_option(ADAM) in app.selectbox(key=f"{pc}_ev_select").options

    app.text_input(key=f"{pc}_ev_search").set_value("rach").run()
    assert app.selectbox(key=f"{pc}_ev_select").options == ["— Choisir une voix —", _select_option(RACHEL)]


# -- changement puis annulation sans altérer la valeur enregistrée --------------------------------------------------
def test_changing_selection_then_cancelling_never_alters_the_saved_value(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, NOVA])
    project = _project()
    character = _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Rachel",
                                    external_voice_id=RACHEL.voice_id)
    app = _open_edit_character_form(project, character.id)
    pc = f"char_{project.id}_{character.id}"
    app.checkbox(key=f"{pc}_ev_generated_only").uncheck().run()
    app = app.selectbox(key=f"{pc}_ev_select").select(_select_option(NOVA)).run()
    assert app.text_input(key=f"{pc}_voice_name").value == "Nova"  # changé en mémoire...
    app = _button(app, "Annuler").click().run()
    assert not app.exception
    unchanged = _char_repo().get(project.id, character.id)
    assert unchanged.voice_name == "Rachel" and unchanged.external_voice_id == RACHEL.voice_id  # ... jamais enregistré


# -- même logique pour les paramètres vocaux du projet --------------------------------------------------------------
def test_project_settings_apply_the_same_logic(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    assert "Voix ElevenLabs (recherche)" not in _text(app)
    p = f"set_{project.id}"
    assert app.selectbox(key=f"{p}_ev_select")
    assert _manual_expander(app)
    app = app.selectbox(key=f"{p}_ev_select").select(_select_option(RACHEL)).run()
    assert app.selectbox(key=f"{p}_voice_provider").value == "elevenlabs"
    assert app.text_input(key=f"{p}_voice_name").value == "Rachel"
    assert app.text_input(key=f"{p}_voice_id").value == RACHEL.voice_id
    app = _button(app, "Enregistrer les paramètres").click().run()
    updated = ProjectRepository(settings.db_path()).get(project.id)
    assert updated.voice_name == "Rachel" and updated.settings["brief"]["voice_id"] == RACHEL.voice_id


def test_project_settings_with_a_historical_voice_id_expands_manual_and_keeps_it(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    repo = ProjectRepository(settings.db_path())
    repo.update(project.id, voice_provider="elevenlabs",
               settings={**project.settings, "brief": {**project.settings.get("brief", {}),
                                                        "voice_id": "legacy-voice-abc123"}})
    app = _settings(project)
    p = f"set_{project.id}"
    assert app.selectbox(key=f"{p}_ev_select").value == ""
    assert _manual_expander(app).proto.expanded is True
    assert app.text_input(key=f"{p}_voice_id").value == "legacy-voice-abc123"
    app = _button(app, "Enregistrer les paramètres").click().run()
    assert not app.exception
    assert repo.get(project.id).settings["brief"]["voice_id"] == "legacy-voice-abc123"


def test_project_settings_picker_hidden_when_provider_is_not_elevenlabs(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="edge")
    app = _settings(project)
    p = f"set_{project.id}"
    assert f"{p}_ev_select" not in [w.key for w in app.selectbox]
    assert _manual_expander(app).proto.expanded is True


# -- non-régression : catalogue vide/indisponible, absence de fuite ---------------------------------------------------
def test_clear_message_when_no_catalog_is_available(engine, _no_catalog_by_default):  # noqa: F811
    project = _project()
    app = _open_new_character_form(project)
    text = _all_text(app)
    assert "aucun catalogue" in text.lower() and "manuelle" in text.lower()


def test_clear_message_for_an_empty_catalog(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [])
    project = _project()
    app = _open_new_character_form(project)
    text = _all_text(app).lower()
    assert "aucune voix" in text and "manuelle" in text


def test_no_generated_voice_available_explains_to_uncheck_the_filter(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [ADAM])
    project = _project()
    app = _open_new_character_form(project)
    text = _all_text(app).lower()
    assert "aucune voix « generated » disponible" in text and "décoche" in text


def test_only_one_audio_preview_is_shown(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, NOVA])
    project = _project()
    app = _open_new_character_form(project)
    pc = f"char_{project.id}_new"
    assert len(app.get("audio")) == 0
    app = app.selectbox(key=f"{pc}_ev_select").select(_select_option(RACHEL)).run()
    assert len(app.get("audio")) == 1
    app = app.selectbox(key=f"{pc}_ev_select").select(_select_option(NOVA)).run()
    assert len(app.get("audio")) == 1


def test_api_key_never_appears_in_the_rendered_page_even_if_config_toml_has_one(
    engine, _no_catalog_by_default, tmp_path, monkeypatch,  # noqa: F811
):
    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    app = _open_new_character_form(project)
    pc = f"char_{project.id}_new"
    app = app.selectbox(key=f"{pc}_ev_select").select(_select_option(RACHEL)).run()
    rendered = (_all_text(app) + " ".join(str(getattr(el, "value", "")) for el in app.text_input)
               + " ".join(str(c.value) for c in app.code))
    assert SECRET_KEY not in rendered


# -- non-régression du formulaire Personnage lui-même -----------------------------------------------------------------
def test_regular_character_fields_still_work_unrelated_to_voice(engine, _no_catalog_by_default):  # noqa: F811
    project = _project()
    app = _open_new_character_form(project)
    p = f"char_{project.id}_new"
    app.text_input(key=f"{p}_name").set_value("Gaston").run()
    app.text_input(key=f"{p}_role").set_value("Guide").run()
    app.checkbox(key=f"{p}_is_primary").check().run()
    app = _button(app, "Enregistrer").click().run()
    assert not app.exception
    character = _char_repo().list_for_project(project.id)[0]
    assert character.name == "Gaston" and character.role == "Guide" and character.is_primary
