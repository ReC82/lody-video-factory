"""Liste compacte des projets de l'accueil (#195) : recherche, tri, lignes repliables, archivés.

Fonctions pures + parcours AppTest avec 25 projets ; aucun appel réseau ni fournisseur.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from lody import settings
from lody.project_list import SORT_NAME, SORT_RECENT, filter_projects, fold, matches, sort_projects
from lody.projects import STATUS_ACTIVE, Project, ProjectRepository
from lody.view_home import label_text

apptest = pytest.importorskip("streamlit.testing.v1")
WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"


def _project(name: str, description: str = "", updated: str = "2026-11-01T10:00:00+00:00", **extra) -> Project:
    return Project(id=f"prj_{fold(name).replace(' ', '_')}", name=name, description=description, status=STATUS_ACTIVE,
                   created_at=updated, updated_at=updated, language="fr", format="9:16", content_type="educatif",
                   tone="", visual_style="", platforms=[], text_provider="", visual_provider="", voice_provider="",
                   voice_name="", music_provider="", **extra)


# -- fonctions pures ------------------------------------------------------------
def test_fold_ignores_case_accents_and_extra_spaces():
    assert fold("  Câble   SDI — Étape ") == "cable sdi — etape"
    assert fold("") == ""


def test_search_is_accent_and_case_insensitive_on_name_and_description():
    cable = _project("Câble SDI", "Le coaxial 75 Ω expliqué")
    assert matches(cable, "cable") and matches(cable, "CÂBLE") and matches(cable, "coaxial")
    assert matches(cable, "sdi cable")  # chaque mot, dans n'importe quel ordre
    assert not matches(cable, "hdmi")
    assert matches(cable, "   ")  # recherche vide : tout


def test_filter_keeps_order_of_matching_projects():
    projects = [_project("Tally"), _project("Genlock"), _project("Mélangeur vidéo")]
    assert [p.name for p in filter_projects(projects, "melangeur")] == ["Mélangeur vidéo"]
    assert filter_projects(projects, "") == projects


def test_sort_recent_first_by_default_and_by_name_without_accents():
    old = _project("Zèbre", updated="2026-01-01T00:00:00+00:00")
    new = _project("abeille", updated="2026-11-01T00:00:00+00:00")
    mid = _project("Écran", updated="2026-06-01T00:00:00+00:00")
    assert [p.name for p in sort_projects([old, mid, new], SORT_RECENT)] == ["abeille", "Écran", "Zèbre"]
    assert [p.name for p in sort_projects([old, mid, new], None)] == ["abeille", "Écran", "Zèbre"]
    assert [p.name for p in sort_projects([old, new, mid], SORT_NAME)] == ["abeille", "Écran", "Zèbre"]


def test_label_text_escapes_markdown_from_project_names():
    assert label_text("Prix *spécial* [promo]") == r"Prix \*spécial\* \[promo\]"
    assert label_text("   ") == "Sans nom"
    unchanged = replace(_project("Audiovisuel")).name
    assert label_text(unchanged) == "Audiovisuel"


# -- parcours AppTest ------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_cache(lody_env, monkeypatch):
    import streamlit as st

    monkeypatch.setenv("LODY_SEED_DEFAULTS", "0")
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def _repo() -> ProjectRepository:
    return ProjectRepository(settings.db_path())


def _make_25(repo: ProjectRepository) -> list[Project]:
    names = [f"Projet {index:02d}" for index in range(1, 24)] + ["Câble SDI", "Éclairage plateau"]
    return [repo.create(name=name, description=f"Description de {name}") for name in names]


def _run(app=None):
    app = app or apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=30)
    return app.run()


def _button(app, label: str):
    return next(button for button in app.button if button.label == label)


def _labels(app) -> list[str]:
    return [expander.label for expander in app.expander]


def _text(app) -> str:
    return " ".join(str(item.value) for item in app.markdown)


def test_each_project_is_a_collapsed_row_and_open_is_a_separate_action():
    _make_25(_repo())
    app = _run()
    assert not app.exception
    assert len(app.expander) == 25
    assert "25 projets" in _text(app)
    assert all(not expander.proto.expanded for expander in app.expander)  # repliées par défaut
    assert sum(1 for button in app.button if button.label == "Ouvrir") == 25  # dans chaque ligne, jamais l'appui sur la ligne
    assert not any(button.label in ("Archiver", "Supprimer") for button in app.button)  # aucune action destructive


def test_search_finds_a_project_among_25_without_accents_then_opens_it():
    created = _make_25(_repo())
    app = _run()
    app.text_input(key="home_search").set_value("cable").run()
    assert _labels(app) == ["Câble SDI"]
    assert "1 résultat sur 25" in _text(app)
    _button(app, "Ouvrir").click().run()
    assert not app.exception
    target = next(project for project in created if project.name == "Câble SDI")
    assert app.query_params["projet"] == [target.id]


def test_search_survives_a_round_trip_and_the_opened_project_is_unfolded():
    _make_25(_repo())
    app = _run()
    app.text_input(key="home_search").set_value("eclairage").run()
    _button(app, "Ouvrir").click().run()
    assert app.query_params.get("projet")  # page du projet : la liste n'est plus affichée
    _button(app, "Tous les projets").click().run()
    assert not app.exception and not app.query_params.get("projet")
    assert app.text_input(key="home_search").value == "eclairage"  # recherche conservée
    assert _labels(app) == ["Éclairage plateau"]
    assert app.expander[0].proto.expanded


def test_no_match_shows_a_clear_empty_state_and_clearing_restores_the_list():
    _make_25(_repo())
    app = _run()
    app.text_input(key="home_search").set_value("introuvable").run()
    assert not app.expander
    assert "Aucun projet ne correspond" in _text(app)
    _button(app, "Effacer la recherche").click().run()
    assert len(app.expander) == 25
    assert app.text_input(key="home_search").value == ""


def test_sort_by_name_orders_rows_alphabetically_ignoring_accents():
    _make_25(_repo())
    app = _run()
    app.segmented_control(key="home_sort").set_value(SORT_NAME).run()
    labels = _labels(app)
    assert labels.index("Câble SDI") < labels.index("Éclairage plateau") < labels.index("Projet 01")


def test_archived_projects_are_behind_a_toggle_but_found_by_search_and_restorable():
    repo = _repo()
    created = _make_25(repo)
    archived = created[0]
    repo.archive(archived.id)
    app = _run()
    assert len(app.expander) == 24
    toggle = app.toggle(key="home_show_archived")
    assert "Afficher les projets archivés (1)" in toggle.label
    toggle.set_value(True).run()
    assert len(app.expander) == 25
    app.toggle(key="home_show_archived").set_value(False).run()
    app.text_input(key="home_search").set_value(archived.name).run()
    assert archived.name in _labels(app)
    _button(app, "Restaurer").click().run()
    assert not app.exception
    assert repo.get(archived.id).status == STATUS_ACTIVE
