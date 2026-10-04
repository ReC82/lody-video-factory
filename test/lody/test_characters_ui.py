"""Interface des personnages récurrents (#32, AppTest) : liste, formulaire, activation, isolation, non-régression."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from lody import settings
from lody.characters import CharacterRepository
from lody.generation.service import build_request
from lody.projects import ProjectRepository
from test.lody.test_generation_ui import _button, _fresh_resources, _labels, _project, _run, _text, engine  # noqa: F401

pytest.importorskip("streamlit.testing.v1")


def _png_bytes(color=(255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (120, 120), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _char_repo():
    return CharacterRepository(settings.db_path())


def _open(project, view="personnages"):
    return _run({"projet": project.id, "vue": view})


def _p(project_id, target="new"):
    return f"char_{project_id}_{target}"


# -- état vide, isolation par projet ---------------------------------------------------------------------------
def test_project_without_characters_shows_the_optional_empty_state(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    assert not app.exception
    text = _text(app)
    assert "Aucun personnage pour l’instant" in text and "facultative" in text
    assert "Ajouter un personnage" in _labels(app)


def test_characters_button_is_on_the_project_page_and_opens_the_characters_page(engine):  # noqa: F811
    project = _project()
    app = _run({"projet": project.id})
    assert "Personnages" in _labels(app)
    app = _button(app, "Personnages").click().run()
    assert "personnages" in dict(app.query_params).get("vue", "") and "Aucun personnage" in _text(app)


# -- création, modification -------------------------------------------------------------------------------------
def test_create_a_character_with_full_fields(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un personnage").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("Gaston").run()
    app.text_input(key=f"{p}_role").set_value("Guide").run()
    app.checkbox(key=f"{p}_is_primary").check().run()
    app = _button(app, "Enregistrer").click().run()
    assert not app.exception
    text = _text(app)
    assert "« Gaston » est ajouté." in text and "Gaston" in text and "Principal" in text
    character = _char_repo().list_for_project(project.id)[0]
    assert character.name == "Gaston" and character.role == "Guide" and character.is_primary


def test_edit_an_existing_character_prefills_the_form_and_saves_changes(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Avant", role="Ancien rôle")
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    p = _p(project.id, character.id)
    assert app.text_input(key=f"{p}_name").value == "Avant"
    assert app.text_input(key=f"{p}_role").value == "Ancien rôle"
    app.text_input(key=f"{p}_name").set_value("Après").run()
    app = _button(app, "Enregistrer").click().run()
    assert "« Après » est modifié." in _text(app)
    assert _char_repo().get(project.id, character.id).name == "Après"


def test_cancel_closes_the_form_without_saving(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un personnage").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("Jamais enregistré").run()
    app = _button(app, "Annuler").click().run()
    assert "Ajouter un personnage" in _labels(app) and "Jamais enregistré" not in _text(app)
    assert _char_repo().count_for_project(project.id) == 0


# -- validation, doublons, homonymes -----------------------------------------------------------------------------
def test_validation_errors_are_shown_and_nothing_is_saved(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un personnage").click().run()
    app = _button(app, "Enregistrer").click().run()  # nom vide
    assert "Donne un nom" in _text(app)
    assert _char_repo().count_for_project(project.id) == 0


def test_duplicate_name_within_the_same_project_is_refused_in_the_ui(engine):  # noqa: F811
    project = _project()
    _char_repo().create(project.id, name="Gaston")
    app = _open(project)
    app = _button(app, "Ajouter un personnage").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("GASTON").run()
    app = _button(app, "Enregistrer").click().run()
    assert "porte déjà ce nom" in _text(app)
    assert _char_repo().count_for_project(project.id) == 1


def test_homonyms_across_two_projects_are_both_accepted(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    _char_repo().create(project_a.id, name="Gaston")
    app = _open(project_b)
    app = _button(app, "Ajouter un personnage").click().run()
    p = _p(project_b.id)
    app.text_input(key=f"{p}_name").set_value("Gaston").run()
    app = _button(app, "Enregistrer").click().run()
    assert "« Gaston » est ajouté." in _text(app)
    assert _char_repo().count_for_project(project_a.id) == 1
    assert _char_repo().count_for_project(project_b.id) == 1


def test_isolation_a_project_never_shows_another_projects_characters(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    _char_repo().create(project_a.id, name="Secret de A, unique")
    app = _open(project_b)
    assert "Secret de A, unique" not in _text(app)
    assert "Aucun personnage pour l’instant" in _text(app)


# -- activation / désactivation, confirmation ----------------------------------------------------------------------
def test_deactivate_requires_confirmation_then_hides_from_default_selection_but_stays_editable(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Gaston")
    app = _open(project)
    app = _button(app, "Désactiver").click().run()
    assert "Désactiver « Gaston » ?" in _text(app)
    assert _char_repo().get(project.id, character.id).is_active  # pas encore désactivé : confirmation en attente
    app = _button(app, "Oui, désactiver").click().run()
    assert "« Gaston » est désactivé" in _text(app)
    assert not _char_repo().get(project.id, character.id).is_active
    assert "Inactif" in _text(app) and "Réactiver" in _labels(app)


def test_cancel_deactivate_confirmation_changes_nothing(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Gaston")
    app = _open(project)
    app = _button(app, "Désactiver").click().run()
    app = _button(app, "Annuler").click().run()
    assert _char_repo().get(project.id, character.id).is_active


def test_reactivate_needs_no_confirmation(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Gaston")
    _char_repo().deactivate(project.id, character.id)
    app = _open(project)
    app = _button(app, "Réactiver").click().run()
    assert "« Gaston » est de nouveau actif." in _text(app)
    assert _char_repo().get(project.id, character.id).is_active


# -- rôle, voix, principal affichés --------------------------------------------------------------------------------
def test_role_and_voice_are_shown_in_the_list(engine):  # noqa: F811
    project = _project()
    _char_repo().create(project.id, name="Gaston", role="Guide", voice_provider="elevenlabs", voice_name="Kev")
    app = _open(project)
    text = _text(app)
    assert "Guide" in text and "Kev" in text


# -- projet archivé ---------------------------------------------------------------------------------------------
def test_archived_project_blocks_the_characters_page(engine):  # noqa: F811
    project = _project()
    ProjectRepository(settings.db_path()).archive(project.id)
    app = _open(project)
    assert not app.exception
    assert "Aucun personnage" not in _text(app)  # jamais rendue : redirigée vers la page du projet
    assert "archivé" in _text(app).lower()


def test_archived_project_view_called_directly_also_refuses_to_edit():  # pas d'AppTest : rendu direct de la vue
    from streamlit.testing.v1 import AppTest

    def script():
        import sys
        sys.path.insert(0, "webui")
        from lody import view_characters
        from lody.characters import CharacterRepository
        from lody.projects import Project

        archived = Project(
            id="prj_archived", name="Archivé", description="", status="archived", created_at="x", updated_at="x",
            language="fr-FR", format="9:16", content_type="pedagogique", tone="", visual_style="", platforms=[],
            text_provider="openai", visual_provider="openai_image", voice_provider="elevenlabs", voice_name="",
            music_provider="none", settings={}, seed_key=None,
        )
        import tempfile
        repo = CharacterRepository(tempfile.mktemp(suffix=".sqlite3"))
        view_characters.render(repo, archived, None)  # service jamais utilisé sur ce chemin (retour anticipé)

    app = AppTest.from_function(script)
    app.run()
    assert not app.exception
    text = " ".join(str(m.value) for m in app.markdown)
    assert "archivé" in text.lower() and "Ajouter un personnage" not in [b.label for b in app.button]


# -- identifiant inconnu, navigation manipulée -----------------------------------------------------------------
def test_editing_an_unknown_character_id_falls_back_gracefully(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app.session_state["_char_form"] = (project.id, "chr_does_not_exist")
    app.run()
    assert not app.exception
    assert "n’existe plus" in _text(app)
    assert "Ajouter un personnage" in _labels(app)  # revenu au mode création, pas de formulaire fantôme


def test_manipulated_session_state_targeting_another_projects_character_is_ignored(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    character = _char_repo().create(project_a.id, name="Personnage de A")
    app = _open(project_b)
    # Un état laissé par project_a (ou forgé) ne doit jamais ouvrir un formulaire pré-rempli chez project_b.
    app.session_state["_char_form"] = (project_a.id, character.id)
    app.run()
    assert not app.exception
    assert "Personnage de A" not in _text(app)
    assert "Ajouter un personnage" in _labels(app)


def test_manipulated_query_project_id_still_isolates_characters(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    _char_repo().create(project_a.id, name="Personnage isolé de A")
    app = _run({"projet": project_b.id, "vue": "personnages"})
    assert "Personnage isolé de A" not in _text(app)


# -- non-régression : le payload de génération n'est jamais affecté -------------------------------------------
def test_ui_crud_of_characters_never_changes_the_generation_payload(engine):  # noqa: F811
    project = _project()
    before = build_request(project, "Un sujet quelconque")

    app = _open(project)
    app = _button(app, "Ajouter un personnage").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("Gaston").run()
    app = _button(app, "Enregistrer").click().run()
    character = _char_repo().list_for_project(project.id)[0]
    app = _button(app, "Modifier").click().run()
    p = _p(project.id, character.id)
    app.text_input(key=f"{p}_role").set_value("Un rôle").run()
    app = _button(app, "Enregistrer").click().run()
    app = _button(app, "Désactiver").click().run()
    _button(app, "Oui, désactiver").click().run()

    after = build_request(ProjectRepository(settings.db_path()).get(project.id), "Un sujet quelconque")
    assert before == after


# -- image de référence (#38) ---------------------------------------------------------------------------------
def test_uploading_a_valid_reference_image_on_creation_saves_it(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un personnage").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("Gaston").run()
    app.file_uploader(key=f"{p}_reference_image").set_value(("ref.png", _png_bytes(), "image/png")).run()
    app = _button(app, "Enregistrer").click().run()
    assert not app.exception
    assert "« Gaston » est ajouté." in _text(app)
    character = _char_repo().list_for_project(project.id)[0]
    assert character.reference_image.startswith(f"references/{project.id}/characters/{character.id}/")


def test_editing_a_character_with_a_reference_image_shows_a_preview(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Gaston")
    _char_repo().set_reference_image(project.id, character.id, _png_bytes())
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    assert len(app.get("image")) == 1  # aperçu affiché
    p = _p(project.id, character.id)
    assert app.checkbox(key=f"{p}_reference_image_remove").label == "Retirer l’image actuelle"
    assert app.file_uploader(key=f"{p}_reference_image").label == "Remplacer l’image"


def test_replacing_an_existing_reference_image(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Gaston")
    first = _char_repo().set_reference_image(project.id, character.id, _png_bytes(color=(255, 0, 0))).reference_image
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    p = _p(project.id, character.id)
    app.file_uploader(key=f"{p}_reference_image").set_value(("new.png", _png_bytes(color=(0, 255, 0)),
                                                              "image/png")).run()
    app = _button(app, "Enregistrer").click().run()
    updated = _char_repo().get(project.id, character.id)
    assert updated.reference_image != first and updated.reference_image != ""


def test_removing_a_reference_image_via_the_checkbox(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Gaston")
    _char_repo().set_reference_image(project.id, character.id, _png_bytes())
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    p = _p(project.id, character.id)
    app.checkbox(key=f"{p}_reference_image_remove").check().run()
    app = _button(app, "Enregistrer").click().run()
    assert _char_repo().get(project.id, character.id).reference_image == ""


def test_uploading_an_invalid_reference_image_shows_an_error_and_does_not_save_it(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un personnage").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("Gaston").run()
    app.file_uploader(key=f"{p}_reference_image").set_value(("evil.png", b"pas une image", "image/png")).run()
    app = _button(app, "Enregistrer").click().run()
    assert not app.exception
    assert "pas une image valide" in _text(app)
    assert _char_repo().count_for_project(project.id) == 0  # rien n'est créé, même partiellement


def test_a_character_without_a_reference_image_behaves_exactly_as_before_38(engine):  # noqa: F811
    """Le cas nominal (aucune image) : comportement strictement inchangé."""
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un personnage").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("Gaston").run()
    app = _button(app, "Enregistrer").click().run()
    assert not app.exception
    character = _char_repo().list_for_project(project.id)[0]
    assert character.reference_image == ""


# -- proposition de référence générée depuis la fiche (#92) ------------------------------------------------------
def test_generate_proposal_button_is_absent_when_creating_a_new_character(engine):  # noqa: F811
    """Fiche pas encore enregistrée (current=None) : rien à prévisualiser depuis, bouton absent."""
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un personnage").click().run()
    assert "Générer une proposition de référence" not in _labels(app)


def test_generate_proposal_button_is_absent_when_a_reference_already_exists(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Gaston")
    _char_repo().set_reference_image(project.id, character.id, _png_bytes())
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    assert "Générer une proposition de référence" not in _labels(app)


def test_generate_proposal_shows_a_preview_built_from_the_saved_sheet(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(
        project.id, name="Gaston", visual_description="Cheveux roux, veste jaune vif",
        reference_prompt="portrait studio, fond neutre", permanent_elements="Toujours un chapeau de paille",
    )
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    app = _button(app, "Générer une proposition de référence").click().run()

    assert not app.exception
    assert "generate_reference_proposal" in engine.calls
    assert len(app.get("image")) == 1  # la proposition, affichée avant tout enregistrement
    assert _char_repo().get(project.id, character.id).reference_image == ""  # pas encore enregistrée
    assert "Valider cette référence" in _labels(app) and "Rejeter" in _labels(app)


def test_validating_the_proposal_saves_it_as_the_reference_image(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Gaston", visual_description="Cheveux roux")
    engine.reference_proposal_bytes = _png_bytes(color=(10, 20, 30))
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    app = _button(app, "Générer une proposition de référence").click().run()
    app = _button(app, "Valider cette référence").click().run()

    assert not app.exception
    assert "enregistrée" in _text(app).lower()
    updated = _char_repo().get(project.id, character.id)
    assert updated.reference_image != ""


def test_rejecting_the_proposal_never_saves_it(engine):  # noqa: F811
    project = _project()
    character = _char_repo().create(project.id, name="Gaston", visual_description="Cheveux roux")
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    app = _button(app, "Générer une proposition de référence").click().run()
    app = _button(app, "Rejeter").click().run()

    assert _char_repo().get(project.id, character.id).reference_image == ""
    assert "Générer une proposition de référence" in _labels(app)  # redevenu disponible


def test_provider_error_during_generation_is_shown_and_nothing_crashes(engine):  # noqa: F811
    from lody.generation.models import ErrorKind, ProviderError

    project = _project()
    _char_repo().create(project.id, name="Gaston", visual_description="Cheveux roux")
    engine.reference_proposal_error = ProviderError(ErrorKind.QUOTA, "Le fournisseur signale un quota insuffisant.")
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    app = _button(app, "Générer une proposition de référence").click().run()

    assert not app.exception
    assert "quota insuffisant" in _text(app)
    assert len(app.get("image")) == 0  # aucune proposition à prévisualiser


def test_reference_prompt_always_guards_against_a_multi_panel_collage_with_embedded_text(engine):  # noqa: F811
    """Essai réel #92 : une fiche seule, sans garde, a produit un montage à 8 vignettes avec un titre
    incrusté (inutilisable comme référence de continuité). La garde est GÉNÉRIQUE (jamais un nom/trait
    codé en dur) : présente même pour une fiche vide, jamais seulement pour une fiche remplie."""
    from lody.view_characters import _reference_prompt_from_sheet

    project = _project()
    filled = _char_repo().create(project.id, name="Gaston", visual_description="Cheveux roux, veste jaune")
    empty = _char_repo().create(project.id, name="Léa")
    for prompt in (_reference_prompt_from_sheet(filled), _reference_prompt_from_sheet(empty)):
        assert "une SEULE image" in prompt and "jamais un montage ni une grille" in prompt
        assert "Aucun texte" in prompt and "aucun logo" in prompt


# -- essai vocal comparatif (#92, diagnostic du jeu vocal) ---------------------------------------------------------
def test_voice_lab_is_absent_without_an_elevenlabs_voice_configured(engine):  # noqa: F811
    project = _project()
    _char_repo().create(project.id, name="Gaston")  # aucune voix configurée
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    assert "Essai vocal comparatif" not in _text(app)


def test_voice_lab_is_absent_for_a_non_elevenlabs_voice(engine):  # noqa: F811
    project = _project()
    _char_repo().create(project.id, name="Gaston", voice_provider="edge", voice_name="fr-FR-SomeVoice")
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    assert "Essai vocal comparatif" not in _text(app)


def test_voice_lab_shows_three_presets_and_generates_on_demand(engine):  # noqa: F811
    engine.supports_voice_preview = True
    project = _project()
    _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Kev",
                        external_voice_id="voice-abc123")
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    assert not app.exception
    assert "Essai vocal comparatif" in _text(app)
    labels = _labels(app)
    assert "Générer cet extrait" in labels  # au moins un des trois presets, pas encore généré

    app = _button(app, "Générer cet extrait").click().run()

    assert not app.exception
    assert engine.calls.count("generate_voice_preview") == 1
    text, voice_id, settings = engine.voice_previews_received[0]
    assert voice_id == "voice-abc123"
    assert text.strip()  # jamais vide
    assert settings["use_speaker_boost"] is True
    assert "Régénérer cet extrait" in _labels(app)  # redevenu disponible pour un nouvel essai


def test_voice_lab_presets_use_officially_documented_settings_only(engine):  # noqa: F811
    """Jamais une balise non confirmée compatible avec eleven_multilingual_v2 (#92) : uniquement
    stability/similarity_boost/style/use_speaker_boost/speed."""
    project = _project()
    character = _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Kev",
                                    external_voice_id="voice-abc123")
    from lody.view_characters import _voice_lab_presets

    for _key, _label, _detail, preset_settings in _voice_lab_presets(project):
        assert set(preset_settings) == {"stability", "similarity_boost", "style", "use_speaker_boost", "speed"}
        assert 0.0 <= preset_settings["stability"] <= 1.0
        assert 0.0 <= preset_settings["style"] <= 1.0
        assert 0.25 <= preset_settings["speed"] <= 4.0
    del character  # fiche non utilisée directement : seuls les réglages génériques sont vérifiés ici


def test_voice_lab_shows_the_provider_error_without_crashing(engine):  # noqa: F811
    from lody.generation.models import ErrorKind, ProviderError

    engine.supports_voice_preview = True
    project = _project()
    _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Kev",
                        external_voice_id="voice-abc123")
    engine.voice_preview_error = ProviderError(ErrorKind.KEY_MISSING, "ElevenLabs n’est pas configuré.")
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    app = _button(app, "Générer cet extrait").click().run()

    assert not app.exception
    assert "ElevenLabs n’est pas configuré" in _text(app)


# -- direction par réplique, eleven_v3 (#92, suite) -----------------------------------------------------------
def test_voice_lab_v3_presets_use_the_exact_same_words_only_tags_differ():
    """Exigence explicite : mêmes mots strictement identiques entre les deux variantes, pour comparer le
    jeu — seules les balises de direction (jamais prononcées) changent."""
    import re

    from lody.view_characters import _VOICE_LAB_V3_PRESETS

    assert len(_VOICE_LAB_V3_PRESETS) == 2
    words = [" ".join(re.sub(r"\[[^\]]*\]", " ", text).split()) for _key, _label, text in _VOICE_LAB_V3_PRESETS]
    assert words[0] == words[1]
    assert "Voyageur" in words[0] and "Attends" in words[0]


def test_voice_lab_v3_settings_target_the_documented_model_and_are_shared_across_variants():
    """model_id="eleven_v3" explicite (jamais la voix/le modèle de production changés silencieusement) ;
    mêmes réglages de voix dans les deux variantes — seule la densité des balises varie."""
    from lody.view_characters import _VOICE_LAB_V3_SETTINGS

    assert _VOICE_LAB_V3_SETTINGS["model_id"] == "eleven_v3"
    assert 0.0 <= _VOICE_LAB_V3_SETTINGS["stability"] <= 1.0
    assert 0.25 <= _VOICE_LAB_V3_SETTINGS["speed"] <= 4.0


def test_voice_lab_v3_presets_show_up_and_generate_a_single_continuous_clip_per_variant(engine):  # noqa: F811
    from lody.view_characters import _prefix

    engine.supports_voice_preview = True
    project = _project()
    character = _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Kev",
                                    external_voice_id="voice-abc123")
    app = _open(project)
    app = _button(app, "Modifier").click().run()

    assert "Direction par réplique (eleven_v3)" in _text(app)
    code_blocks = " ".join(block.value for block in app.code)
    assert "[hesitates]" in code_blocks and "[reflective]" in code_blocks  # balises visibles, texte transparent

    p = _prefix(project.id, character.id)
    v3_button = next(b for b in app.button if b.key == f"{p}_voice_preview_v3_subtle_generate")
    before = engine.calls.count("generate_voice_preview")
    app = v3_button.click().run()

    assert not app.exception
    assert engine.calls.count("generate_voice_preview") == before + 1
    text, voice_id, settings = engine.voice_previews_received[-1]
    assert settings.get("model_id") == "eleven_v3"
    assert voice_id == "voice-abc123"
    assert "[matter-of-fact]" in text and "[hesitates]" in text  # la variante "subtile", balises incluses


def test_voice_direction_apply_button_persists_the_v3_settings(engine):  # noqa: F811
    from lody.view_characters import _VOICE_LAB_V3_SETTINGS

    engine.supports_voice_preview = True
    project = _project()
    character = _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Kev",
                                    external_voice_id="voice-abc123")
    app = _open(project)
    app = _button(app, "Modifier").click().run()

    assert "Appliquer ce réglage eleven_v3 à ce personnage" in _labels(app)
    assert _char_repo().get(project.id, character.id).voice_direction == {}

    app = _button(app, "Appliquer ce réglage eleven_v3 à ce personnage").click().run()

    assert not app.exception
    assert _char_repo().get(project.id, character.id).voice_direction == _VOICE_LAB_V3_SETTINGS
    assert "enregistrés" in _text(app).lower()
    assert "Retirer (revenir aux réglages par défaut du moteur)" in _labels(app)


def test_voice_direction_clear_button_resets_to_default(engine):  # noqa: F811
    from lody.view_characters import _VOICE_LAB_V3_SETTINGS

    engine.supports_voice_preview = True
    project = _project()
    character = _char_repo().create(project.id, name="Gaston", voice_provider="elevenlabs", voice_name="Kev",
                                    external_voice_id="voice-abc123")
    _char_repo().set_voice_direction(project.id, character.id, _VOICE_LAB_V3_SETTINGS)
    app = _open(project)
    app = _button(app, "Modifier").click().run()

    app = _button(app, "Retirer (revenir aux réglages par défaut du moteur)").click().run()

    assert not app.exception
    assert _char_repo().get(project.id, character.id).voice_direction == {}
    assert "Appliquer ce réglage eleven_v3 à ce personnage" in _labels(app)


def test_voice_direction_apply_never_affects_another_character_or_project(engine):  # noqa: F811
    engine.supports_voice_preview = True
    project = _project()
    eli = _char_repo().create(project.id, name="Eli", voice_provider="elevenlabs", voice_name="Kev",
                              external_voice_id="voice-abc123")
    sibling = _char_repo().create(project.id, name="Léa")
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    app = _button(app, "Appliquer ce réglage eleven_v3 à ce personnage").click().run()

    assert not app.exception
    assert _char_repo().get(project.id, eli.id).voice_direction != {}
    assert _char_repo().get(project.id, sibling.id).voice_direction == {}
