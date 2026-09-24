"""Interface d'import/export partiel de personnages et de lieux (#57, AppTest) : boutons des pages
Personnages/Lieux, aperçu avant écriture, confirmation explicite, ajout dans un projet déjà peuplé,
isolation entre projets, conflit de nom, projet archivé, non-régression des écrans manuels et de #44."""

from __future__ import annotations

import json

import pytest

from lody import settings
from lody.characters import CharacterRepository
from lody.locations import LocationRepository
from lody.projects import ProjectRepository
from lody.resource_transfer import character_template, location_template
from test.lody.test_generation_ui import _button, _fresh_resources, _labels, _project, _run, _text, engine  # noqa: F401

pytest.importorskip("streamlit.testing.v1")


def _open_characters(project):
    return _run({"projet": project.id, "vue": "personnages"})


def _open_locations(project):
    return _run({"projet": project.id, "vue": "lieux"})


def _dl_labels(app):
    return [b.label for b in app.download_button]


def _char_repo():
    return CharacterRepository(settings.db_path())


def _loc_repo():
    return LocationRepository(settings.db_path())


def _upload(app, key, payload):
    return app.file_uploader(key=key).set_value(("import.json", json.dumps(payload).encode("utf-8"),
                                                  "application/json")).run()


def _confirm(app):
    return next(b for b in app.button if b.label == "Confirmer l’import")


# -- présence des boutons -----------------------------------------------------------------------------------------
def test_characters_page_has_export_template_and_import_actions(engine):  # noqa: F811
    project = _project()
    app = _open_characters(project)
    assert not app.exception
    assert "Import / Export" in _text(app)
    assert "Exporter les personnages" in _dl_labels(app) and "Télécharger un modèle" in _dl_labels(app)
    assert app.file_uploader(key=f"charimp_{project.id}_uploader")


def test_locations_page_has_export_template_and_import_actions(engine):  # noqa: F811
    project = _project()
    app = _open_locations(project)
    assert not app.exception
    assert "Exporter les lieux" in _dl_labels(app) and "Télécharger un modèle" in _dl_labels(app)
    assert app.file_uploader(key=f"locimp_{project.id}_uploader")


# -- import d'un ou plusieurs éléments, aperçu, confirmation --------------------------------------------------------
def test_importing_the_character_template_previews_before_writing_then_creates_on_confirm(engine):  # noqa: F811
    project = _project()
    app = _open_characters(project)
    app = _upload(app, f"charimp_{project.id}_uploader", character_template())
    assert not app.exception
    text = _text(app)
    assert "Aperçu" in text and "Personnage principal" in text and "Personnage secondaire" in text
    assert _char_repo().count_for_project(project.id) == 0  # rien écrit avant confirmation
    app = _confirm(app).click().run()
    assert not app.exception
    created = _char_repo().list_for_project(project.id)
    assert {c.name for c in created} == {"Personnage principal", "Personnage secondaire"}


def test_importing_the_location_template_previews_then_creates_on_confirm(engine):  # noqa: F811
    project = _project()
    app = _open_locations(project)
    app = _upload(app, f"locimp_{project.id}_uploader", location_template())
    assert _loc_repo().count_for_project(project.id) == 0
    app = _confirm(app).click().run()
    created = _loc_repo().list_for_project(project.id)
    assert {loc.name for loc in created} == {"Lieu principal", "Lieu secondaire"}


def test_import_adds_to_a_project_that_already_has_characters(engine):  # noqa: F811
    project = _project()
    _char_repo().create(project.id, name="Déjà là")
    payload = {"schema_version": 1, "resource_type": "characters", "characters": [{"name": "Nouveau venu"}]}
    app = _open_characters(project)
    app = _upload(app, f"charimp_{project.id}_uploader", payload)
    app = _confirm(app).click().run()
    names = {c.name for c in _char_repo().list_for_project(project.id)}
    assert names == {"Déjà là", "Nouveau venu"}


# -- isolation entre projets -----------------------------------------------------------------------------------------
def test_import_into_one_project_never_creates_anything_in_another(engine):  # noqa: F811
    project = _project()
    other = ProjectRepository(settings.db_path()).create(name="Autre projet")
    app = _open_characters(project)
    app = _upload(app, f"charimp_{project.id}_uploader", character_template())
    _confirm(app).click().run()
    assert _char_repo().count_for_project(project.id) == 2
    assert _char_repo().count_for_project(other.id) == 0


# -- conflit de nom : aperçu bloquant, rien créé -----------------------------------------------------------------------
def test_name_conflict_blocks_import_with_a_clear_message_and_nothing_is_created(engine):  # noqa: F811
    project = _project()
    _char_repo().create(project.id, name="Personnage principal")  # même nom que le modèle
    app = _open_characters(project)
    app = _upload(app, f"charimp_{project.id}_uploader", character_template())
    text = _text(app)
    assert "Personnage principal" in text and "invalide" in text.lower()
    assert _confirm(app).proto.disabled
    assert _char_repo().count_for_project(project.id) == 1  # rien ajouté


def test_location_name_conflict_blocks_import(engine):  # noqa: F811
    project = _project()
    _loc_repo().create(project.id, name="Lieu principal")
    app = _open_locations(project)
    app = _upload(app, f"locimp_{project.id}_uploader", location_template())
    assert _confirm(app).proto.disabled
    assert _loc_repo().count_for_project(project.id) == 1


# -- fichier invalide / mauvais resource_type : géré proprement -------------------------------------------------------
def test_wrong_resource_type_file_is_refused_on_the_characters_page(engine):  # noqa: F811
    project = _project()
    app = _open_characters(project)
    app = _upload(app, f"charimp_{project.id}_uploader", location_template())  # fichier de LIEUX sur la page personnages
    assert "invalide" in _text(app).lower()
    assert _confirm(app).proto.disabled
    assert _char_repo().count_for_project(project.id) == 0


def test_invalid_json_shows_an_error_and_creates_nothing(engine):  # noqa: F811
    project = _project()
    app = _open_characters(project)
    app = app.file_uploader(key=f"charimp_{project.id}_uploader").set_value(
        ("bad.json", b"{not valid json", "application/json")).run()
    assert "invalide" in _text(app).lower()
    assert _confirm(app).proto.disabled
    assert _char_repo().count_for_project(project.id) == 0


# -- projet archivé : section masquée, comme le reste de la page ------------------------------------------------------
def test_import_export_section_hidden_for_an_archived_project_characters(engine):  # noqa: F811
    project = _project()
    ProjectRepository(settings.db_path()).archive(project.id)
    app = _open_characters(project)
    assert "Import / Export" not in _text(app) and _dl_labels(app) == []


def test_import_export_section_hidden_for_an_archived_project_locations(engine):  # noqa: F811
    project = _project()
    ProjectRepository(settings.db_path()).archive(project.id)
    app = _open_locations(project)
    assert "Import / Export" not in _text(app) and _dl_labels(app) == []


# -- non-régression : création manuelle et #44 restent intacts -----------------------------------------------------------
def test_manual_character_creation_still_works_alongside_the_new_section(engine):  # noqa: F811
    project = _project()
    app = _open_characters(project)
    app = _button(app, "Ajouter un personnage").click().run()
    p = f"char_{project.id}_new"
    app.text_input(key=f"{p}_name").set_value("Créé à la main").run()
    app = _button(app, "Enregistrer").click().run()
    assert not app.exception
    assert _char_repo().list_for_project(project.id)[0].name == "Créé à la main"


def test_full_project_export_import_from_settings_page_is_unaffected(engine):  # noqa: F811
    """Non-régression #44 : l'import/export complet (page Paramètres) continue de fonctionner à l'identique."""
    project = _project()
    app = _run({"projet": project.id, "vue": "parametres"})
    assert not app.exception
    assert "Exporter ce projet" in _dl_labels(app) and "Télécharger un modèle vierge" in _dl_labels(app)
