"""Brouillon persistant de « Nouvelle production » (#189), à l'écran (AppTest).

Régression du test utilisateur du 12/11/2026 : sujet + Voltix + démo #150, sortie vers « Personnages » pour la
voix, retour → tout doit être là. Puis rechargement (nouvelle session), isolement entre projets, éléments
désactivés, effacement confirmé, et aucune préparation ni génération déclenchée par l'enregistrement.
"""

from __future__ import annotations

import pytest

from lody import settings
from lody.animation import modes
from lody.characters import CharacterRepository
from lody.compose_drafts import ComposeDraftRepository
from lody.generation.service import ProductionRepository
from lody.projects import ProjectRepository
from test.lody.test_generation_ui import (  # noqa: F401
    SUBJECT,
    _button,
    _fresh_resources,
    _labels,
    _launch,
    _project,
    _run,
    _text,
    engine,
)

pytest.importorskip("streamlit.testing.v1")


def _animated(project):
    repo = ProjectRepository(settings.db_path())
    return repo.update(project.id, settings={**project.settings, modes.SETTINGS_KEY: modes.ANIMATION})


def _go(app, project, view):
    app.query_params.clear()
    app.query_params["projet"] = project.id
    app.query_params["vue"] = view
    return app.run()


def _drafts():
    return ComposeDraftRepository(settings.db_path())


def _captions(app):
    return " ".join(str(caption.value) for caption in app.caption)


def test_voltix_demo_subject_round_trip_through_characters_keeps_everything(engine):  # noqa: F811
    project = _animated(_project())
    voltix = CharacterRepository(settings.db_path()).create(project.id, name="Voltix")
    app = _run({"projet": project.id, "vue": "production"})
    app.text_area(key=f"request_{project.id}").set_value(SUBJECT).run()
    app.multiselect(key=f"chars_sel_{project.id}").set_value([voltix.id]).run()
    app = _button(app, "Installer la démo Voltix #150").click().run()
    assert not app.exception and "Enregistré automatiquement" in _text(app)
    assert "Storyboard en attente : démo Voltix #150" in _captions(app)

    app = _go(app, project, "personnages")   # choisir la voix du personnage…
    assert not app.exception
    app = _go(app, project, "production")    # …puis revenir
    assert not app.exception
    assert app.text_area(key=f"request_{project.id}").value == SUBJECT
    assert app.multiselect(key=f"chars_sel_{project.id}").value == [voltix.id]
    assert "Storyboard en attente : démo Voltix #150" in _captions(app)
    assert "Brouillon restauré" in _text(app)
    # L'enregistrement n'est jamais une préparation : aucune production, donc aucun coût ni appel.
    assert ProductionRepository(settings.db_path()).list_for_project(project.id) == []

    # Rechargement du navigateur / redémarrage du conteneur : nouvelle session, même base.
    fresh = _run({"projet": project.id, "vue": "production"})
    assert not fresh.exception
    assert fresh.text_area(key=f"request_{project.id}").value == SUBJECT
    assert fresh.multiselect(key=f"chars_sel_{project.id}").value == [voltix.id]
    assert "Storyboard en attente : démo Voltix #150" in _captions(fresh)
    assert "Brouillon restauré" in _text(fresh)
    assert _labels(fresh).count("Préparer la génération") == 1
    assert ProductionRepository(settings.db_path()).list_for_project(project.id) == []


def test_draft_of_project_a_is_never_restored_on_project_b(engine):  # noqa: F811
    a, b = _project(), _project("Audiovisuel")
    app = _run({"projet": a.id, "vue": "production"})
    app.text_area(key=f"request_{a.id}").set_value("Sujet du projet A").run()
    assert _drafts().get(a.id).request == "Sujet du projet A"
    other = _run({"projet": b.id, "vue": "production"})
    assert not other.text_area(key=f"request_{b.id}").value
    assert "Brouillon restauré" not in _text(other) and _drafts().get(b.id) is None


def test_deactivated_character_is_dropped_with_a_message(engine):  # noqa: F811
    project = _project()
    characters = CharacterRepository(settings.db_path())
    kept, gone = characters.create(project.id, name="Léa"), characters.create(project.id, name="Marc")
    app = _run({"projet": project.id, "vue": "production"})
    app.text_area(key=f"request_{project.id}").set_value(SUBJECT).run()
    app.multiselect(key=f"chars_sel_{project.id}").set_value([kept.id, gone.id]).run()
    characters.deactivate(project.id, gone.id)
    fresh = _run({"projet": project.id, "vue": "production"})
    assert not fresh.exception
    assert fresh.multiselect(key=f"chars_sel_{project.id}").value == [kept.id]
    assert "1 élément(s) retiré(s)" in _text(fresh)


def test_clear_asks_for_confirmation_then_erases(engine):  # noqa: F811
    project = _project()
    app = _run({"projet": project.id, "vue": "production"})
    app.text_area(key=f"request_{project.id}").set_value(SUBJECT).run()
    app = _button(app, "Recommencer / Effacer le brouillon").click().run()
    assert _drafts().get(project.id).request == SUBJECT  # rien d'effacé sans confirmation
    app = _button(app, "Non, garder le brouillon").click().run()
    assert app.text_area(key=f"request_{project.id}").value == SUBJECT
    app = _button(app, "Recommencer / Effacer le brouillon").click().run()
    app = _button(app, "Oui, effacer le brouillon").click().run()
    assert not app.exception and "Brouillon effacé" in _text(app)
    assert not app.text_area(key=f"request_{project.id}").value and _drafts().get(project.id) is None
    assert "Brouillon restauré" not in _text(_run({"projet": project.id, "vue": "production"}))


def test_launching_keeps_the_subject_and_needs_a_new_estimate(engine):  # noqa: F811
    _launch(engine)
    project = _project()
    stored = _drafts().get(project.id)
    assert stored is not None and stored.request == SUBJECT and stored.prepared_id is None
    app = _run({"projet": project.id, "vue": "production"})
    assert app.text_area(key=f"request_{project.id}").value == SUBJECT
    assert "Préparer la génération" in _labels(app)  # nouvelle estimation et nouvelle confirmation exigées
