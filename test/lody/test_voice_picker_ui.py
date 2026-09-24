"""Interface du sélecteur de voix ElevenLabs — selectbox unique et recherchable (#59, AppTest) : recherche
par nom/identifiant/label, filtre « generated » par défaut, maintien/disparition contrôlée de la sélection,
préremplissage provider/nom/identifiant, aperçu audio unique, projet et personnage, mode manuel, absence de
fuite de secret."""

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

# Identifiants réalistes (≥ 6 caractères) : brief.py exige 6 à 40 caractères pour brief.voice_id — un id trop
# court (« v1 ») passe l'affichage mais échoue à l'enregistrement, comme un vrai voice_id ElevenLabs le ferait.
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


# -- selectbox unique, présence -----------------------------------------------------------------------------------
def test_settings_page_shows_a_single_searchable_selectbox_no_per_voice_buttons(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, NOVA])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    assert app.selectbox(key=f"{p}_ev_select")  # un seul sélecteur
    assert "Utiliser cette voix" not in _labels(app)  # l'étape intermédiaire du #55 a disparu
    options = app.selectbox(key=f"{p}_ev_select").options
    assert options == ["— Choisir une voix —", _select_option(RACHEL), _select_option(NOVA)]


# -- recherche : nom, identifiant, label, insensible à la casse -----------------------------------------------------
def test_search_by_name_filters_the_selectbox(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, NOVA])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.text_input(key=f"{p}_ev_search").set_value("rach").run()
    options = app.selectbox(key=f"{p}_ev_select").options
    assert options == ["— Choisir une voix —", _select_option(RACHEL)]


def test_search_by_voice_id_filters_the_selectbox(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, NOVA])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.text_input(key=f"{p}_ev_search").set_value("nova003").run()
    options = app.selectbox(key=f"{p}_ev_select").options
    assert options == ["— Choisir une voix —", _select_option(NOVA)]


def test_search_by_label_value_filters_the_selectbox(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, NOVA])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.text_input(key=f"{p}_ev_search").set_value("irish").run()
    options = app.selectbox(key=f"{p}_ev_select").options
    assert options == ["— Choisir une voix —", _select_option(NOVA)]


def test_search_is_case_insensitive_and_partial(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, NOVA])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.text_input(key=f"{p}_ev_search").set_value("RAC").run()
    options = app.selectbox(key=f"{p}_ev_select").options
    assert options == ["— Choisir une voix —", _select_option(RACHEL)]


def test_search_with_no_match_shows_a_clear_message_and_keeps_manual_fields(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    repo = ProjectRepository(settings.db_path())
    repo.update(project.id, voice_provider="elevenlabs",
               settings={**project.settings, "brief": {**project.settings.get("brief", {}),
                                                        "voice_id": "already-saved-id"}})
    app = _settings(project)
    p = f"set_{project.id}"
    app.text_input(key=f"{p}_ev_search").set_value("zzz-no-match").run()
    assert "Aucune voix ne correspond à cette recherche." in _all_text(app)
    assert app.text_input(key=f"{p}_voice_id").value == "already-saved-id"  # jamais effacé


# -- filtre « generated » ------------------------------------------------------------------------------------------
def test_generated_only_filter_is_checked_by_default(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, ADAM])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    assert app.checkbox(key=f"{p}_ev_generated_only").value is True
    options = app.selectbox(key=f"{p}_ev_select").options
    assert _select_option(ADAM) not in options  # professional exclue par défaut
    assert _select_option(RACHEL) in options


def test_unchecking_the_filter_reveals_professional_and_other_categories(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, ADAM])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.checkbox(key=f"{p}_ev_generated_only").uncheck().run()
    options = app.selectbox(key=f"{p}_ev_select").options
    assert _select_option(ADAM) in options and _select_option(RACHEL) in options


def test_category_is_read_from_metadata_never_inferred_from_the_name(engine, _no_catalog_by_default):  # noqa: F811
    """Une voix nommée de façon trompeuse ne doit jamais être classée par déduction du nom : seule la
    métadonnée ``category`` du catalogue compte."""
    trap = ev.VoiceInfo("v9", "Generated Sounding Name", category="professional", labels={})
    _write_report(_no_catalog_by_default, [trap])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    text = _all_text(app)
    assert "generated" in text.lower() and "aucune voix « generated » disponible" in text.lower()  # exclue malgré son nom


def test_no_generated_voice_available_explains_to_uncheck_the_filter(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [ADAM])  # uniquement une voix professional
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    text = _all_text(app).lower()
    assert "aucune voix « generated » disponible" in text and "décoche" in text


# -- maintien et disparition contrôlée de la sélection ---------------------------------------------------------------
def test_selection_is_kept_across_reruns_while_still_matching_the_filters(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, NOVA])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.selectbox(key=f"{p}_ev_select").select(_select_option(RACHEL)).run()
    # une recherche qui laisse Rachel dans les options ne doit pas désélectionner
    app.text_input(key=f"{p}_ev_search").set_value("a").run()  # « Rachel » ET « Nova » contiennent un a
    assert app.selectbox(key=f"{p}_ev_select").value == RACHEL.voice_id
    assert app.text_input(key=f"{p}_voice_name").value == "Rachel"


def test_selection_disappearing_from_filtered_options_never_silently_clears_saved_fields(engine, _no_catalog_by_default):  # noqa: F811
    """Coeur du ticket #59 : si la voix choisie n'est plus dans les options après un changement de filtre, le
    sélecteur revient à « aucune sélection » MAIS voice_name/voice_id gardent leur valeur déjà enregistrée."""
    _write_report(_no_catalog_by_default, [RACHEL, ADAM])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.checkbox(key=f"{p}_ev_generated_only").uncheck().run()
    app.selectbox(key=f"{p}_ev_select").select(_select_option(ADAM)).run()
    assert app.text_input(key=f"{p}_voice_name").value == "Adam"

    # re-cocher « generated » exclut Adam des options : le sélecteur retombe sur le placeholder...
    app.checkbox(key=f"{p}_ev_generated_only").check().run()
    assert not app.exception
    sb = app.selectbox(key=f"{p}_ev_select")
    assert _select_option(ADAM) not in sb.options
    assert sb.value == ""  # .value renvoie la valeur brute de l'option (le placeholder), pas son libellé formaté
    # ... mais les champs déjà remplis ne sont JAMAIS silencieusement effacés.
    assert app.text_input(key=f"{p}_voice_name").value == "Adam"
    assert app.text_input(key=f"{p}_voice_id").value == ADAM.voice_id


# -- préremplissage exact : provider, nom, identifiant --------------------------------------------------------------
def test_selecting_a_voice_fills_provider_name_and_voice_id_without_saving(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    repo = ProjectRepository(settings.db_path())
    repo.update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.selectbox(key=f"{p}_ev_select").select(_select_option(RACHEL)).run()
    assert app.selectbox(key=f"{p}_voice_provider").value == "elevenlabs"
    assert app.text_input(key=f"{p}_voice_name").value == "Rachel"
    assert app.text_input(key=f"{p}_voice_id").value == RACHEL.voice_id
    # la sélection seule ne sauvegarde rien : le projet en base n'a pas encore changé.
    assert repo.get(project.id).settings.get("brief", {}).get("voice_id") != RACHEL.voice_id


def test_saving_after_selection_persists_the_chosen_voice(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    repo = ProjectRepository(settings.db_path())
    repo.update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.selectbox(key=f"{p}_ev_select").select(_select_option(RACHEL)).run()
    app = _button(app, "Enregistrer les paramètres").click().run()
    assert not app.exception
    updated = repo.get(project.id)
    assert updated.voice_name == "Rachel" and updated.settings["brief"]["voice_id"] == RACHEL.voice_id


# -- un seul aperçu audio --------------------------------------------------------------------------------------------
def test_only_one_audio_preview_is_shown_never_one_per_voice(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL, NOVA])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    assert len(app.get("audio")) == 0  # rien avant toute sélection
    app.selectbox(key=f"{p}_ev_select").select(_select_option(RACHEL)).run()
    assert len(app.get("audio")) == 1
    app.selectbox(key=f"{p}_ev_select").select(_select_option(NOVA)).run()
    assert len(app.get("audio")) == 1  # jamais deux à la fois


def test_voice_id_is_fully_displayed_in_a_copyable_code_block(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.selectbox(key=f"{p}_ev_select").select(_select_option(RACHEL)).run()
    assert RACHEL.voice_id in [c.value for c in app.code]  # st.code : bloc avec icône de copie native Streamlit


# -- personnage : création et édition ----------------------------------------------------------------------------
def test_selecting_a_voice_when_creating_a_new_character_fills_provider_name_and_external_id(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    app = _open_characters(project)
    app = _button(app, "Ajouter un personnage").click().run()
    pc = f"char_{project.id}_new"
    app.selectbox(key=f"{pc}_ev_select").select(_select_option(RACHEL)).run()
    assert app.selectbox(key=f"{pc}_voice_provider").value == "elevenlabs"
    assert app.text_input(key=f"{pc}_voice_name").value == "Rachel"
    assert app.text_input(key=f"{pc}_external_voice_id").value == RACHEL.voice_id
    app.text_input(key=f"{pc}_name").set_value("Gaston").run()
    app = _button(app, "Enregistrer").click().run()
    created = _char_repo().list_for_project(project.id)[0]
    assert created.voice_provider == "elevenlabs" and created.voice_name == "Rachel" and created.external_voice_id == RACHEL.voice_id


def test_editing_an_existing_character_keeps_its_provider_preselected_when_it_matches_the_catalog(engine, _no_catalog_by_default):  # noqa: F811
    """Compatibilité ancien voice_id : si l'identifiant déjà enregistré correspond à une voix du catalogue,
    elle apparaît présélectionnée à l'ouverture."""
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Ancien nom",
                        external_voice_id=RACHEL.voice_id)
    app = _open_characters(project)
    app = _button(app, "Modifier").click().run()
    character_id = _char_repo().list_for_project(project.id)[0].id
    pc = f"char_{project.id}_{character_id}"
    assert app.selectbox(key=f"{pc}_ev_select").value == RACHEL.voice_id


# -- fallback manuel --------------------------------------------------------------------------------------------------
def test_manual_entry_still_works_when_catalog_is_unavailable(engine, _no_catalog_by_default):  # noqa: F811
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    text = _all_text(app)
    assert "Voix ElevenLabs (recherche)" in text
    assert "aucun catalogue" in text.lower() and "manuelle" in text.lower()
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


def test_picker_not_shown_when_project_provider_is_not_elevenlabs(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="edge")
    app = _settings(project)
    assert "Voix ElevenLabs (recherche)" not in _text(app)


def test_picker_hidden_when_editing_a_character_with_a_different_provider_already_set(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    _char_repo().create(project.id, name="Gaston", voice_provider="edge", voice_name="Voix gratuite")
    app = _open_characters(project)
    app = _button(app, "Modifier").click().run()
    assert "Voix ElevenLabs (recherche)" not in _text(app)


# -- rapport indisponible/périmé, actualisation ------------------------------------------------------------------------
def test_refresh_button_reloads_the_file_from_disk_after_an_atomic_replacement(engine, _no_catalog_by_default):  # noqa: F811
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    assert app.selectbox(key=f"{p}_ev_select").options == ["— Choisir une voix —", _select_option(RACHEL)]

    _write_report(_no_catalog_by_default, [NOVA])  # remplacement atomique par le script hôte, page déjà ouverte
    app = _button(app, "Actualiser les voix").click().run()
    assert not app.exception
    assert app.selectbox(key=f"{p}_ev_select").options == ["— Choisir une voix —", _select_option(NOVA)]
    assert "Fichier relu à l’instant." in _all_text(app)


# -- absence de fuite de secret -----------------------------------------------------------------------------------------
def test_api_key_never_appears_in_the_rendered_page_even_if_config_toml_has_one(
    engine, _no_catalog_by_default, tmp_path, monkeypatch,  # noqa: F811
):
    config = tmp_path / "config.toml"
    config.write_text(f'[elevenlabs]\napi_key = "{SECRET_KEY}"\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    _write_report(_no_catalog_by_default, [RACHEL])
    project = _project()
    ProjectRepository(settings.db_path()).update(project.id, voice_provider="elevenlabs")
    app = _settings(project)
    p = f"set_{project.id}"
    app.selectbox(key=f"{p}_ev_select").select(_select_option(RACHEL)).run()
    rendered = (_all_text(app) + " ".join(str(getattr(el, "value", "")) for el in app.text_input)
               + " ".join(str(c.value) for c in app.code))
    assert SECRET_KEY not in rendered
