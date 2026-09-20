"""Parcours de l'interface (Streamlit AppTest) : aucun appel réseau ni fournisseur."""

import ast
from pathlib import Path

import pytest

from lody.projects import STATUS_ARCHIVED, ProjectRepository
from lody import settings
from lody.seeds import SEED_PROJECTS

apptest = pytest.importorskip("streamlit.testing.v1")
WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"


@pytest.fixture(autouse=True)
def _fresh_cache(lody_env):
    """La base est mise en cache par processus : on la réinitialise pour chaque test."""
    import streamlit as st

    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def _run(query: dict | None = None):
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=30)
    for key, value in (query or {}).items():
        app.query_params[key] = value
    return app.run()


def _text(app) -> str:
    return " ".join(str(item.value) for item in app.markdown)


def _button(app, label: str):
    return next(button for button in app.button if button.label == label)


def _repo() -> ProjectRepository:
    """Base du test, avec les exemples initiaux (comme au premier démarrage)."""
    repo = ProjectRepository(settings.db_path())
    repo.seed_defaults(SEED_PROJECTS)
    return repo


def test_application_starts_and_lists_the_two_example_projects():
    app = _run()
    assert not app.exception
    text = _text(app)
    assert "Lody Video Factory" in text and "Tes projets vidéo" in text
    assert "Audiovisuel" in text and "LodyCrypto" in text
    assert "MoneyPrinterTurbo" in text  # crédit du projet source
    assert _button(app, "Nouveau projet")


def test_empty_state_when_no_project_exists(monkeypatch):
    monkeypatch.setenv("LODY_SEED_DEFAULTS", "0")
    app = _run()
    assert not app.exception
    assert "Aucun projet pour l’instant" in _text(app)
    assert _button(app, "Créer mon premier projet")


def test_create_project_from_the_form_then_it_is_persisted():
    app = _run({"vue": "nouveau"})
    app.text_input(key="form_new_new_name").set_value("Ma chaîne tech")
    app.text_area(key="form_new_new_description").set_value("Des vidéos courtes sur la tech.")
    _button(app, "Créer le projet").click().run()
    assert not app.exception
    created = next(p for p in _repo().list_projects() if p.name == "Ma chaîne tech")
    assert created.description == "Des vidéos courtes sur la tech."
    assert app.query_params["projet"] == [created.id] or app.query_params["projet"] == created.id
    assert "créé" in _text(app)

    # « Redémarrage » : nouvelle session, cache vidé, même base.
    import streamlit as st

    st.cache_resource.clear()
    reopened = _run()
    assert "Ma chaîne tech" in _text(reopened)


def test_form_shows_human_error_for_missing_name_and_creates_nothing():
    app = _run({"vue": "nouveau"})
    _button(app, "Créer le projet").click().run()
    assert "Donne un nom à ton projet." in _text(app)
    assert "Quelques points sont à corriger" in _text(app)
    assert len(_repo().list_projects()) == 2  # seulement les exemples


def test_form_refuses_a_pasted_secret_and_does_not_echo_it():
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    app = _run({"vue": "nouveau"})
    app.text_input(key="form_new_new_name").set_value("Projet piégé")
    app.text_area(key="form_new_new_description").set_value(f"voici ma clé {secret}")
    _button(app, "Créer le projet").click().run()
    assert "clé secrète" in _text(app)
    assert secret not in _text(app)
    assert all(p.name != "Projet piégé" for p in _repo().list_projects())


def test_project_page_shows_summary_and_readiness_without_key_values():
    project = next(p for p in _repo().list_projects() if p.name == "LodyCrypto")
    app = _run({"projet": project.id})
    text = _text(app)
    assert not app.exception
    assert "LodyCrypto" in text and "Commencer une production" not in text  # bouton, pas markdown
    assert _button(app, "Commencer une production") and _button(app, "Modifier") and _button(app, "Archiver")
    assert "Prêt" in text and "Clé à configurer" in text
    assert "FAKE-CONFIG-VALUE-NOT-A-KEY" not in text


def test_edit_project_updates_and_bumps_last_modified():
    project = next(p for p in _repo().list_projects() if p.name == "Audiovisuel")
    app = _run({"projet": project.id, "vue": "modifier"})
    app.text_input(key=f"form_edit_{project.id}_name").set_value("Audiovisuel Pro")
    _button(app, "Enregistrer").click().run()
    assert not app.exception
    updated = _repo().get(project.id)
    assert updated.name == "Audiovisuel Pro"
    assert updated.updated_at >= project.updated_at
    assert "Modifications enregistrées" in _text(app)


def test_archive_requires_confirmation_then_project_moves_to_archived_section():
    project = next(p for p in _repo().list_projects() if p.name == "Audiovisuel")
    app = _run({"projet": project.id})
    _button(app, "Archiver").click().run()
    assert "Archiver « Audiovisuel » ?" in _text(app)
    assert _repo().get(project.id).status != STATUS_ARCHIVED  # rien n'est archivé avant confirmation

    _button(app, "Oui, archiver").click().run()
    assert _repo().get(project.id).status == STATUS_ARCHIVED
    home = _text(app)
    assert "est archivé" in home
    assert any("Projets archivés (1)" in expander.label for expander in app.expander)


def test_archive_can_be_cancelled():
    project = next(p for p in _repo().list_projects() if p.name == "Audiovisuel")
    app = _run({"projet": project.id})
    _button(app, "Archiver").click().run()
    _button(app, "Annuler").click().run()
    assert "Archiver « Audiovisuel » ?" not in _text(app)
    assert _repo().get(project.id).status != STATUS_ARCHIVED


def test_archived_project_offers_restore_and_blocks_production():
    repo = _repo()
    project = next(p for p in repo.list_projects() if p.name == "Audiovisuel")
    repo.archive(project.id)
    app = _run({"projet": project.id})
    assert "Ce projet est archivé" in _text(app)
    assert not any(button.label == "Commencer une production" for button in app.button)
    _button(app, "Restaurer ce projet").click().run()
    assert repo.get(project.id).status != STATUS_ARCHIVED

    repo.archive(project.id)
    blocked = _run({"projet": project.id, "vue": "production"})
    assert not any(button.label == "Générer la vidéo" for button in blocked.button)


def test_unknown_project_falls_back_to_home_with_message():
    app = _run({"projet": "prj_inexistant"})
    assert not app.exception
    assert "Tes projets vidéo" in _text(app)


def test_production_generate_only_builds_a_local_draft_and_calls_no_provider():
    project = next(p for p in _repo().list_projects() if p.name == "Audiovisuel")
    app = _run({"projet": project.id, "vue": "production"})
    _button(app, "Générer la vidéo").click().run()
    assert "Décris d’abord ta vidéo" in _text(app)

    app.text_area(key=f"request_{project.id}").set_value("Explique simplement le Fill et le Key.")
    _button(app, "Générer la vidéo").click().run()
    draft = app.session_state[f"draft_{project.id}"]
    assert draft["projet"] == "Audiovisuel" and draft["fournisseurs_appeles"] == []
    assert "aucune génération lancée" in _text(app)


def test_storage_error_is_shown_calmly(monkeypatch, tmp_path):
    blocked = tmp_path / "fichier"
    blocked.write_text("je ne suis pas un dossier", encoding="utf-8")
    monkeypatch.setenv("LODY_DATA_DIR", str(blocked / "sous-dossier"))
    app = _run()
    assert not app.exception
    assert "momentanément inaccessibles" in _text(app)


def test_lody_modules_never_import_the_engine_or_providers():
    forbidden = {"app", "requests", "openai", "moviepy", "httpx", "urllib3"}
    for source in [WEBUI_DIR / "Lody.py", *(WEBUI_DIR / "lody").glob("*.py")]:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in forbidden, f"{source.name} importe {name}"
