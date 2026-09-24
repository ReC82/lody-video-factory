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


# -- mode mise à jour d'un projet existant (#66) --------------------------------------------------------------------
def _switch_to_update_mode(app, project_id):
    return app.radio(key=f"impexp_{project_id}_mode").set_value("Mettre à jour un projet existant").run()


def _update_uploader(app, project_id):
    return app.file_uploader(key=f"impexp_{project_id}_update_uploader")


def _upload_update(app, project_id, payload):
    raw = json.dumps(payload).encode("utf-8")
    return _update_uploader(app, project_id).set_value(("update.json", raw, "application/json")).run()


def _type_confirmation(app, project_id, text):
    return app.text_input(key=f"impexp_{project_id}_update_confirm_name").set_value(text).run()


def _confirm_update_button(app):
    return next(button for button in app.button if button.label == "Confirmer la mise à jour du projet")


def test_update_mode_is_available_alongside_create_mode(engine):  # noqa: F811
    project = _project()
    app = _settings(project)
    assert {"Créer un nouveau projet", "Mettre à jour un projet existant"} <= set(
        app.radio(key=f"impexp_{project.id}_mode").options)


def test_update_mode_defaults_target_to_the_currently_open_project(engine):  # noqa: F811
    project = _project()
    app = _settings(project)
    app = _switch_to_update_mode(app, project.id)
    assert not app.exception
    selector = app.selectbox(key=f"impexp_{project.id}_update_target")
    assert selector.value == project.id


def test_update_preview_shows_detailed_changes_without_writing(engine):  # noqa: F811
    project = _project()
    before_tone = project.tone
    app = _settings(project)
    app = _switch_to_update_mode(app, project.id)
    payload = _payload(project.name, tone="Ton complètement différent")
    payload["characters"] = [{"name": "Nouveau personnage"}]
    app = _upload_update(app, project.id, payload)
    assert not app.exception
    text = _text(app)
    assert "Aperçu" in text and "Nouveau personnage" in text and "ajouté" in text
    assert "Ton" in text or "Champs projet modifiés" in text
    assert ProjectRepository(settings.db_path()).get(project.id).tone == before_tone  # rien écrit encore


def test_update_confirm_button_requires_typing_the_target_name(engine):  # noqa: F811
    project = _project()
    app = _settings(project)
    app = _switch_to_update_mode(app, project.id)
    app = _upload_update(app, project.id, _payload(project.name, tone="Changé"))
    assert _confirm_update_button(app).proto.disabled  # rien tapé : bloqué

    app = _type_confirmation(app, project.id, "un nom incorrect")
    assert _confirm_update_button(app).proto.disabled  # mauvais nom : toujours bloqué

    app = _type_confirmation(app, project.id, project.name)
    assert not _confirm_update_button(app).proto.disabled  # bon nom : débloqué


def test_confirming_update_applies_changes_and_keeps_the_same_project_id(engine):  # noqa: F811
    project = _project()
    before_count = ProjectRepository(settings.db_path()).count()
    app = _settings(project)
    app = _switch_to_update_mode(app, project.id)
    app = _upload_update(app, project.id, _payload(project.name, tone="Ton mis à jour depuis l’UI"))
    app = _type_confirmation(app, project.id, project.name)
    app = _confirm_update_button(app).click().run()
    assert not app.exception
    repo = ProjectRepository(settings.db_path())
    updated = repo.get(project.id)
    assert updated.id == project.id
    assert updated.tone == "Ton mis à jour depuis l’UI"
    assert repo.count() == before_count  # aucun nouveau projet créé, contrairement au mode création


def test_not_confirming_the_update_leaves_the_project_untouched(engine):  # noqa: F811
    """« Annuler la confirmation ne modifie rien » : ne pas cliquer sur confirmer, ou naviguer ailleurs."""
    project = _project()
    before_tone = project.tone
    app = _settings(project)
    app = _switch_to_update_mode(app, project.id)
    _upload_update(app, project.id, _payload(project.name, tone="Ne doit jamais s’appliquer"))
    assert ProjectRepository(settings.db_path()).get(project.id).tone == before_tone


def test_update_target_can_be_switched_to_a_different_project(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    app = _settings(project_a)
    app = _switch_to_update_mode(app, project_a.id)
    app.selectbox(key=f"impexp_{project_a.id}_update_target").set_value(project_b.id).run()
    app = _upload_update(app, project_a.id, _payload(project_b.name, tone="Appliqué à B, pas A"))
    app = _type_confirmation(app, project_a.id, project_b.name)
    app = _confirm_update_button(app).click().run()
    assert not app.exception
    repo = ProjectRepository(settings.db_path())
    assert repo.get(project_b.id).tone == "Appliqué à B, pas A"
    assert repo.get(project_a.id).tone != "Appliqué à B, pas A"  # le projet ouvert n'a pas bougé


def test_update_target_name_already_used_by_another_project_is_refused(engine):  # noqa: F811
    project_a = _project("LodyCrypto")
    project_b = _project("Audiovisuel")
    app = _settings(project_a)
    app = _switch_to_update_mode(app, project_a.id)
    app = _upload_update(app, project_a.id, _payload(project_b.name))  # renomme A comme B
    assert "déjà utilisé par un autre projet" in _text(app)
    repo = ProjectRepository(settings.db_path())
    assert repo.get(project_a.id).name != project_b.name  # rien n'a changé


# -- non-régression : le mode création (#44) reste le comportement par défaut --------------------------------------
def test_create_mode_is_the_default_and_still_works_unchanged_after_66(engine):  # noqa: F811
    project = _project()
    app = _settings(project)
    assert app.radio(key=f"impexp_{project.id}_mode").value == "Créer un nouveau projet"
    app = _upload(app, project.id, _payload("Toujours créé"))
    app = _button(app, "Confirmer la création du projet").click().run()
    repo = ProjectRepository(settings.db_path())
    assert any(p.name == "Toujours créé" for p in repo.list_projects())
    assert repo.get(project.id).name == project.name
