"""Interface d'import/export de configuration de projet (#44, AppTest) : boutons de la page Paramètres,
aperçu sans écriture, confirmation créant un nouveau projet, fichier invalide, nom déjà pris."""

from __future__ import annotations

import json

import pytest

from lody import settings
from lody.projects import ProjectRepository
from lody.project_transfer import SCHEMA_VERSION
from test.lody.test_generation_ui import _button, _fresh_resources, _labels, _project, _run, _text, engine  # noqa: F401

pytest.importorskip("streamlit.testing.v1")


def _settings(project):
    return _run({"projet": project.id, "vue": "parametres"})


def _uploader(app, project_id):
    return app.file_uploader(key=f"impexp_{project_id}_uploader")


def _upload(app, project_id, payload):
    raw = json.dumps(payload).encode("utf-8")
    return _uploader(app, project_id).set_value(("import.json", raw, "application/json")).run()


def _download_labels(app):
    return [button.label for button in app.download_button]


def _confirm_button(app):
    return next(button for button in app.button if button.label == "Confirmer la création du projet")


def _payload(name="Projet importé", **overrides):
    return {"schema_version": SCHEMA_VERSION, "project": {"name": name, **overrides}, "characters": [], "locations": []}


# -- section présente, actions d'export -----------------------------------------------------------------------
def test_settings_page_has_the_import_export_section_and_download_buttons(engine):  # noqa: F811
    project = _project()
    app = _settings(project)
    assert not app.exception
    assert "Import / Export" in _text(app)
    assert "Exporter ce projet" in _download_labels(app) and "Télécharger un modèle vierge" in _download_labels(app)


# -- aperçu sans écriture -------------------------------------------------------------------------------------
def test_uploading_a_valid_file_shows_a_preview_without_creating_a_project(engine):  # noqa: F811
    project = _project()
    before = ProjectRepository(settings.db_path()).count()
    app = _settings(project)
    payload = _payload("Aperçu UI")
    payload["characters"] = [{"name": "Gaston"}]
    app = _upload(app, project.id, payload)
    assert not app.exception
    text = _text(app)
    assert "Aperçu" in text and "Aperçu UI" in text and "Gaston" in text
    assert ProjectRepository(settings.db_path()).count() == before
    assert "Confirmer la création du projet" in _labels(app)


def test_confirming_creates_a_new_project_and_leaves_the_current_one_untouched(engine):  # noqa: F811
    project = _project()
    app = _settings(project)
    app = _upload(app, project.id, _payload("Créé depuis l’UI"))
    app = _button(app, "Confirmer la création du projet").click().run()
    assert not app.exception
    repo = ProjectRepository(settings.db_path())
    created = next(p for p in repo.list_projects() if p.name == "Créé depuis l’UI")
    assert created.id != project.id
    assert repo.get(project.id).name == project.name  # le projet courant n'a pas bougé


# -- fichier invalide : erreur affichée, rien créé -------------------------------------------------------------
def test_invalid_json_shows_an_error_and_creates_nothing(engine):  # noqa: F811
    project = _project()
    before = ProjectRepository(settings.db_path()).count()
    app = _settings(project)
    app = _uploader(app, project.id).set_value(("bad.json", b"{not valid json", "application/json")).run()
    assert not app.exception
    assert "invalide" in _text(app).lower()
    assert _confirm_button(app).proto.disabled  # visible mais désactivé : rien à confirmer
    assert ProjectRepository(settings.db_path()).count() == before


# -- nom déjà pris : jamais d'écrasement --------------------------------------------------------------------------
def test_importing_a_project_with_an_existing_name_renames_it_instead_of_overwriting(engine):  # noqa: F811
    project = _project()
    app = _settings(project)
    app = _upload(app, project.id, _payload(project.name))  # même nom que le projet déjà ouvert
    text = _text(app)
    assert project.name in text and "(import)" in text  # aperçu propose un nom distinct
    app = _button(app, "Confirmer la création du projet").click().run()
    repo = ProjectRepository(settings.db_path())
    assert repo.get(project.id).name == project.name  # projet existant intact
    assert any(p.name == f"{project.name} (import)" for p in repo.list_projects())
