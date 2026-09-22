"""Interface des lieux récurrents (#33, AppTest) : liste, formulaire, activation, isolation, non-régression."""

from __future__ import annotations

import pytest

from lody import settings
from lody.generation.service import build_request
from lody.locations import LocationRepository
from lody.projects import ProjectRepository
from test.lody.test_generation_ui import _button, _fresh_resources, _labels, _project, _run, _text, engine  # noqa: F401

pytest.importorskip("streamlit.testing.v1")


def _loc_repo():
    return LocationRepository(settings.db_path())


def _open(project, view="lieux"):
    return _run({"projet": project.id, "vue": view})


def _p(project_id, target="new"):
    return f"loc_{project_id}_{target}"


# -- état vide, isolation par projet ---------------------------------------------------------------------------
def test_project_without_locations_shows_the_optional_empty_state(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    assert not app.exception
    text = _text(app)
    assert "Aucun lieu pour l’instant" in text and "facultative" in text
    assert "Ajouter un lieu" in _labels(app)


def test_locations_button_is_on_the_project_page_and_opens_the_locations_page(engine):  # noqa: F811
    project = _project()
    app = _run({"projet": project.id})
    assert "Lieux" in _labels(app)
    app = _button(app, "Lieux").click().run()
    assert "lieux" in dict(app.query_params).get("vue", "") and "Aucun lieu" in _text(app)


# -- création, modification -------------------------------------------------------------------------------------
def test_create_a_location_with_full_fields(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un lieu").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("Place du marché").run()
    app.text_input(key=f"{p}_location_type").set_value("Extérieur").run()
    app.checkbox(key=f"{p}_is_primary").check().run()
    app = _button(app, "Enregistrer").click().run()
    assert not app.exception
    text = _text(app)
    assert "« Place du marché » est ajouté." in text and "Place du marché" in text and "Principal" in text
    location = _loc_repo().list_for_project(project.id)[0]
    assert location.name == "Place du marché" and location.location_type == "Extérieur" and location.is_primary


def test_edit_an_existing_location_prefills_the_form_and_saves_changes(engine):  # noqa: F811
    project = _project()
    location = _loc_repo().create(project.id, name="Avant", location_type="Ancien type")
    app = _open(project)
    app = _button(app, "Modifier").click().run()
    p = _p(project.id, location.id)
    assert app.text_input(key=f"{p}_name").value == "Avant"
    assert app.text_input(key=f"{p}_location_type").value == "Ancien type"
    app.text_input(key=f"{p}_name").set_value("Après").run()
    app = _button(app, "Enregistrer").click().run()
    assert "« Après » est modifié." in _text(app)
    assert _loc_repo().get(project.id, location.id).name == "Après"


def test_cancel_closes_the_form_without_saving(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un lieu").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("Jamais enregistré").run()
    app = _button(app, "Annuler").click().run()
    assert "Ajouter un lieu" in _labels(app) and "Jamais enregistré" not in _text(app)
    assert _loc_repo().count_for_project(project.id) == 0


# -- validation, doublons, homonymes -----------------------------------------------------------------------------
def test_validation_errors_are_shown_and_nothing_is_saved(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app = _button(app, "Ajouter un lieu").click().run()
    app = _button(app, "Enregistrer").click().run()  # nom vide
    assert "Donne un nom" in _text(app)
    assert _loc_repo().count_for_project(project.id) == 0


def test_duplicate_name_within_the_same_project_is_refused_in_the_ui(engine):  # noqa: F811
    project = _project()
    _loc_repo().create(project.id, name="Place du marché")
    app = _open(project)
    app = _button(app, "Ajouter un lieu").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("PLACE DU MARCHÉ").run()
    app = _button(app, "Enregistrer").click().run()
    assert "porte déjà ce nom" in _text(app)
    assert _loc_repo().count_for_project(project.id) == 1


def test_homonyms_across_two_projects_are_both_accepted(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    _loc_repo().create(project_a.id, name="Place du marché")
    app = _open(project_b)
    app = _button(app, "Ajouter un lieu").click().run()
    p = _p(project_b.id)
    app.text_input(key=f"{p}_name").set_value("Place du marché").run()
    app = _button(app, "Enregistrer").click().run()
    assert "« Place du marché » est ajouté." in _text(app)
    assert _loc_repo().count_for_project(project_a.id) == 1
    assert _loc_repo().count_for_project(project_b.id) == 1


def test_isolation_a_project_never_shows_another_projects_locations(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    _loc_repo().create(project_a.id, name="Lieu secret de A, unique")
    app = _open(project_b)
    assert "Lieu secret de A, unique" not in _text(app)
    assert "Aucun lieu pour l’instant" in _text(app)


# -- activation / désactivation, confirmation ----------------------------------------------------------------------
def test_deactivate_requires_confirmation_then_hides_from_default_selection_but_stays_editable(engine):  # noqa: F811
    project = _project()
    location = _loc_repo().create(project.id, name="Place du marché")
    app = _open(project)
    app = _button(app, "Désactiver").click().run()
    assert "Désactiver « Place du marché » ?" in _text(app)
    assert _loc_repo().get(project.id, location.id).is_active  # pas encore désactivé : confirmation en attente
    app = _button(app, "Oui, désactiver").click().run()
    assert "« Place du marché » est désactivé" in _text(app)
    assert not _loc_repo().get(project.id, location.id).is_active
    assert "Inactif" in _text(app) and "Réactiver" in _labels(app)


def test_cancel_deactivate_confirmation_changes_nothing(engine):  # noqa: F811
    project = _project()
    location = _loc_repo().create(project.id, name="Place du marché")
    app = _open(project)
    app = _button(app, "Désactiver").click().run()
    app = _button(app, "Annuler").click().run()
    assert _loc_repo().get(project.id, location.id).is_active


def test_reactivate_needs_no_confirmation(engine):  # noqa: F811
    project = _project()
    location = _loc_repo().create(project.id, name="Place du marché")
    _loc_repo().deactivate(project.id, location.id)
    app = _open(project)
    app = _button(app, "Réactiver").click().run()
    assert "« Place du marché » est de nouveau actif." in _text(app)
    assert _loc_repo().get(project.id, location.id).is_active


# -- type et notes de continuité affichés --------------------------------------------------------------------------
def test_type_and_continuity_notes_are_shown_in_the_list(engine):  # noqa: F811
    project = _project()
    _loc_repo().create(project.id, name="Place du marché", location_type="Extérieur",
                       continuity_notes="Toujours en plein jour, jamais de pluie")
    app = _open(project)
    text = _text(app)
    assert "Extérieur" in text and "Toujours en plein jour" in text


# -- projet archivé ---------------------------------------------------------------------------------------------
def test_archived_project_blocks_the_locations_page(engine):  # noqa: F811
    project = _project()
    ProjectRepository(settings.db_path()).archive(project.id)
    app = _open(project)
    assert not app.exception
    assert "Aucun lieu" not in _text(app)  # jamais rendue : redirigée vers la page du projet
    assert "archivé" in _text(app).lower()


def test_archived_project_view_called_directly_also_refuses_to_edit():  # pas d'AppTest : rendu direct de la vue
    from streamlit.testing.v1 import AppTest

    def script():
        import sys
        sys.path.insert(0, "webui")
        from lody import view_locations
        from lody.locations import LocationRepository
        from lody.projects import Project

        archived = Project(
            id="prj_archived", name="Archivé", description="", status="archived", created_at="x", updated_at="x",
            language="fr-FR", format="9:16", content_type="pedagogique", tone="", visual_style="", platforms=[],
            text_provider="openai", visual_provider="openai_image", voice_provider="elevenlabs", voice_name="",
            music_provider="none", settings={}, seed_key=None,
        )
        import tempfile
        repo = LocationRepository(tempfile.mktemp(suffix=".sqlite3"))
        view_locations.render(repo, archived)

    app = AppTest.from_function(script)
    app.run()
    assert not app.exception
    text = " ".join(str(m.value) for m in app.markdown)
    assert "archivé" in text.lower() and "Ajouter un lieu" not in [b.label for b in app.button]


# -- identifiant inconnu, navigation manipulée -----------------------------------------------------------------
def test_editing_an_unknown_location_id_falls_back_gracefully(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    app.session_state["_loc_form"] = (project.id, "loc_does_not_exist")
    app.run()
    assert not app.exception
    assert "n’existe plus" in _text(app)
    assert "Ajouter un lieu" in _labels(app)  # revenu au mode création, pas de formulaire fantôme


def test_manipulated_session_state_targeting_another_projects_location_is_ignored(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    location = _loc_repo().create(project_a.id, name="Lieu de A")
    app = _open(project_b)
    app.session_state["_loc_form"] = (project_a.id, location.id)
    app.run()
    assert not app.exception
    assert "Lieu de A" not in _text(app)
    assert "Ajouter un lieu" in _labels(app)


def test_manipulated_query_project_id_still_isolates_locations(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    _loc_repo().create(project_a.id, name="Lieu isolé de A")
    app = _run({"projet": project_b.id, "vue": "lieux"})
    assert "Lieu isolé de A" not in _text(app)


# -- non-régression : le payload de génération n'est jamais affecté -------------------------------------------
def test_ui_crud_of_locations_never_changes_the_generation_payload(engine):  # noqa: F811
    project = _project()
    before = build_request(project, "Un sujet quelconque")

    app = _open(project)
    app = _button(app, "Ajouter un lieu").click().run()
    p = _p(project.id)
    app.text_input(key=f"{p}_name").set_value("Place du marché").run()
    app = _button(app, "Enregistrer").click().run()
    location = _loc_repo().list_for_project(project.id)[0]
    app = _button(app, "Modifier").click().run()
    p = _p(project.id, location.id)
    app.text_input(key=f"{p}_location_type").set_value("Un type").run()
    app = _button(app, "Enregistrer").click().run()
    app = _button(app, "Désactiver").click().run()
    _button(app, "Oui, désactiver").click().run()

    after = build_request(ProjectRepository(settings.db_path()).get(project.id), "Un sujet quelconque")
    assert before == after
