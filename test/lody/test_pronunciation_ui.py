"""Section « Prononciation » des paramètres (#162), en Streamlit AppTest : aucun appel réseau ni payant."""

import pytest

from lody import settings
from lody.projects import ProjectRepository
from lody.pronunciation import PronunciationRepository
from lody.seeds import SEED_PROJECTS

apptest = pytest.importorskip("streamlit.testing.v1")
from test.lody.test_app import _fresh_cache, _run  # noqa: E402,F401


def _project():
    repo = ProjectRepository(settings.db_path())
    repo.seed_defaults(SEED_PROJECTS)
    return next(p for p in repo.list_projects() if p.name == "Audiovisuel")


def _button(app, label):
    return next(button for button in app.button if button.label == label)


def test_add_an_expression_then_it_waits_for_listening_before_validation():
    project = _project()
    app = _run({"projet": project.id, "vue": "parametres"})
    assert not app.exception
    p = f"pron_{project.id}"
    app.text_input(key=f"{p}_new_written").set_value("SDI")
    app.text_input(key=f"{p}_new_spoken").set_value("èss-dé-i")
    _button(app, "Ajouter au lexique").click().run()
    assert not app.exception
    entries = PronunciationRepository(settings.db_path()).list_for_project(project.id)
    assert [(entry.written, entry.spoken, entry.validated) for entry in entries] == [("SDI", "èss-dé-i", False)]
    validate = [button for button in app.button if button.label.startswith("Valider cette prononciation")]
    assert all(button.disabled for button in validate)  # jamais validable sans écoute


def test_invalid_expression_shows_a_french_error_and_saves_nothing():
    project = _project()
    app = _run({"projet": project.id, "vue": "parametres"})
    p = f"pron_{project.id}"
    app.text_input(key=f"{p}_new_written").set_value("SDI")
    _button(app, "Ajouter au lexique").click().run()
    assert "Indique la lecture demandée" in " ".join(str(item.value) for item in app.markdown)
    assert PronunciationRepository(settings.db_path()).list_for_project(project.id) == []


def test_editorial_check_separates_voice_text_and_subtitles():
    project = _project()
    repo = PronunciationRepository(settings.db_path())
    entry = repo.create(project.id, written="HDMI", spoken="hache-dé-èm-i")
    repo.mark_validated(project.id, entry.id, "test")
    app = _run({"projet": project.id, "vue": "parametres"})
    app.text_area(key=f"pron_{project.id}_audit_text").set_value("Le SDI et l'HDMI.").run()
    assert not app.exception
    codes = [str(block.value) for block in app.code]
    assert "Le SDI et l'hache-dé-èm-i." in codes and "Le SDI et l'HDMI." in codes
    assert "SDI" in " ".join(str(item.value) for item in app.markdown)
