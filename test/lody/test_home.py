"""Tests du prototype d'interface Lody Video Factory (webui/Lody.py)."""

import ast
import re
import sys
from pathlib import Path

import pytest

WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
LODY_DIR = WEBUI_DIR / "lody"
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from lody import profiles

apptest = pytest.importorskip("streamlit.testing.v1")


def _run_app():
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=30)
    return app.run()


def _all_text(app) -> str:
    return " ".join(str(element.value) for element in app.markdown)


def test_demo_projects_are_audiovisuel_and_lodycrypto():
    assert [p.name for p in profiles.PROJECTS] == ["Audiovisuel", "LodyCrypto"]


def test_unknown_project_falls_back_to_default():
    assert profiles.get_project("inconnu").key == profiles.DEFAULT_PROJECT_KEY
    assert profiles.get_project(None).key == profiles.DEFAULT_PROJECT_KEY


def test_profiles_hold_no_secret():
    secret_like = re.compile(r"(api[_-]?key|token|secret|password|sk-[A-Za-z0-9])", re.I)
    for project in profiles.PROJECTS:
        for value in vars(project).values():
            assert not secret_like.search(str(value))


def test_lody_modules_never_import_the_engine_or_providers():
    forbidden = ("app", "requests", "openai", "moviepy")
    for source in [WEBUI_DIR / "Lody.py", *LODY_DIR.glob("*.py")]:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in forbidden, f"{source.name} imports {name}"


def test_home_renders_brand_and_main_prompt():
    app = _run_app()
    assert not app.exception
    text = _all_text(app)
    assert "Lody Video Factory" in text
    assert "Que veux-tu créer ?" in text
    assert app.button(key="generate").label == "Générer la vidéo"


def test_switching_project_changes_profile_summary():
    app = _run_app()
    assert "Audiovisuel" in _all_text(app)
    app.session_state["project_key"] = "lodycrypto"
    app.run()
    text = _all_text(app)
    assert not app.exception
    assert "LodyCrypto" in text
    assert "Sombre et moderne" in text


def test_generate_with_empty_request_shows_error_and_no_draft():
    app = _run_app()
    app.button(key="generate").click().run()
    assert app.session_state["request_error"] is True
    assert app.session_state["draft"] is None
    assert "aucune génération lancée" not in _all_text(app)


def test_generate_with_request_only_builds_a_local_draft():
    app = _run_app()
    app.text_area(key="request_text").set_value("Explique simplement le Fill et le Key.").run()
    app.button(key="generate").click().run()
    assert not app.exception
    draft = app.session_state["draft"]
    assert draft["projet"] == "Audiovisuel"
    assert draft["fournisseurs_appeles"] == []
    assert "aucune génération lancée" in _all_text(app)
