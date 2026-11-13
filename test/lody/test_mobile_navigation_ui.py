"""#212 — retour rapide au projet et personnages compacts (AppTest, sans fournisseur payant)."""

from __future__ import annotations

import pytest

from lody import settings
from lody.characters import CharacterRepository
from lody.generation.models import ProductionStatus as S
from lody.generation.service import build_request
from lody.generation.store import ProductionRepository
from lody.view_characters import row_label
from test.lody.test_generation_ui import _button, _fresh_resources, _labels, _project, _run, _text, engine  # noqa: F401

pytest.importorskip("streamlit.testing.v1")


def _chars():
    return CharacterRepository(settings.db_path())


def _p(project_id, target="new"):
    return f"char_{project_id}_{target}"


# -- 1. « Retour au projet » en premier, sur chaque sous-page -------------------------------------------------
@pytest.mark.parametrize("view", ["parametres", "production", "personnages", "lieux"])
def test_back_to_project_is_the_first_action_under_the_header(engine, view):  # noqa: F811
    project = _project()
    app = _run({"projet": project.id, "vue": view})
    assert not app.exception
    labels = _labels(app)
    # En-tête commun (« Tous les projets », secondaire) puis immédiatement « Retour au projet », avant tout le reste.
    assert labels[:2] == ["Tous les projets", "Retour au projet"]
    assert labels.count("Retour au projet") <= 2  # au plus un rappel en bas d'une longue page (production)


@pytest.mark.parametrize("view", ["personnages", "lieux"])
def test_back_to_project_is_not_duplicated_on_resource_pages(engine, view):  # noqa: F811
    project = _project()
    app = _run({"projet": project.id, "vue": view})
    assert _labels(app).count("Retour au projet") == 1


def test_back_to_project_opens_the_project_page(engine):  # noqa: F811
    project = _project()
    app = _run({"projet": project.id, "vue": "personnages"})
    app = _button(app, "Retour au projet").click().run()
    params = dict(app.query_params)
    assert params.get("projet") in (project.id, [project.id]) and "vue" not in params
    assert "Personnages" in _labels(app)  # page du projet


def test_home_and_project_page_have_no_back_to_project(engine):  # noqa: F811
    project = _project()
    assert "Retour au projet" not in _labels(_run())
    assert "Retour au projet" not in _labels(_run({"projet": project.id}))


def test_tracking_page_has_a_single_back_to_project_at_the_top(engine):  # noqa: F811
    project = _project()
    subject = "Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming."
    production = ProductionRepository(settings.db_path()).create(
        params={"request": build_request(project, subject).to_dict(), "engine": {}}, project_id=project.id,
        subject=subject, provider="moneyprinterturbo", status=S.ECHEC, script="Un script déjà écrit. " * 5,
        error_code="key_missing", error_message="Clé manquante.", current_step="Échec")
    app = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    assert not app.exception
    labels = _labels(app)
    assert labels[:2] == ["Tous les projets", "Retour au projet"] and labels.count("Retour au projet") == 1


# -- 2. Personnages : liste compacte, ajout en haut -----------------------------------------------------------
def _three_characters(project):
    repo = _chars()
    repo.create(project.id, name="Voltix", role="Mascotte", is_primary=True,
                visual_description="Grande description qui ne doit pas s’afficher en carte.")
    repo.create(project.id, name="Eli", role="Guide")
    repo.create(project.id, name="Nora")


def test_characters_page_is_compact_add_first_then_one_line_per_character(engine):  # noqa: F811
    project = _project()
    _three_characters(project)
    app = _run({"projet": project.id, "vue": "personnages"})
    assert not app.exception
    labels = _labels(app)
    assert labels[:3] == ["Tous les projets", "Retour au projet", "Ajouter un personnage"]
    rows = [e.label for e in app.expander if "·" in e.label or e.label in ("Nora",)]
    assert rows == ["Voltix · Mascotte · principal", "Eli · Guide", "Nora"]
    # Titre compact, plus de pavé introductif permanent : l'aide est repliée.
    text = _text(app)
    assert 'hero-title">Personnages</h1>' in text and 'class="hero-sub"' not in text
    assert "décris les personnages récurrents" in text  # aide conservée, dans une section repliable
    # Le formulaire n'est pas rendu tant qu'on ne l'a pas demandé.
    assert not [t for t in app.text_input if t.key == f"{_p(project.id)}_name"]


def test_add_opens_the_form_in_place_of_the_list_and_cancel_returns_without_saving(engine):  # noqa: F811
    project = _project()
    _three_characters(project)
    app = _run({"projet": project.id, "vue": "personnages"})
    app = _button(app, "Ajouter un personnage").click().run()
    p = _p(project.id)
    assert app.text_input(key=f"{p}_name").value == ""
    labels = _labels(app)
    assert "Ajouter un personnage" not in labels and "Modifier" not in labels  # la liste laisse place au formulaire
    assert "Voltix · Mascotte · principal" not in [e.label for e in app.expander]
    app.text_input(key=f"{p}_name").set_value("Brouillon jamais enregistré").run()
    app = _button(app, "Annuler").click().run()
    assert not app.exception
    assert [c.name for c in _chars().list_for_project(project.id)] == ["Voltix", "Eli", "Nora"]
    assert "Ajouter un personnage" in _labels(app)
    assert "Voltix · Mascotte · principal" in [e.label for e in app.expander]


def test_deactivation_still_needs_an_explicit_confirmation(engine):  # noqa: F811
    project = _project()
    character = _chars().create(project.id, name="Voltix")
    app = _run({"projet": project.id, "vue": "personnages"})
    app = _button(app, "Désactiver").click().run()
    assert _chars().get(project.id, character.id).is_active  # un seul tap ne désactive rien
    row = next(e for e in app.expander if e.label == "Voltix")
    assert row.proto.expanded  # la confirmation est visible sans rouvrir la ligne
    app = _button(app, "Oui, désactiver").click().run()
    assert not _chars().get(project.id, character.id).is_active
    assert "Voltix · inactif" in [e.label for e in app.expander]


def test_voice_stays_saved_after_edit_and_reload(engine):  # noqa: F811
    project = _project()
    character = _chars().create(project.id, name="Voltix", role="Mascotte", voice_provider="elevenlabs",
                                voice_name="Kev", external_voice_id="voice-abc123")
    app = _run({"projet": project.id, "vue": "personnages"})
    app = _button(app, "Modifier").click().run()
    p = _p(project.id, character.id)
    app.text_input(key=f"{p}_role").set_value("Mascotte électrique").run()
    app = _button(app, "Enregistrer").click().run()
    assert not app.exception
    saved = _chars().get(project.id, character.id)
    assert (saved.role, saved.voice_provider, saved.voice_name, saved.external_voice_id) == (
        "Mascotte électrique", "elevenlabs", "Kev", "voice-abc123")
    reloaded = _run({"projet": project.id, "vue": "personnages"})  # équivalent F5
    reloaded = _button(reloaded, "Modifier").click().run()
    assert reloaded.text_input(key=f"{p}_external_voice_id").value == "voice-abc123"


def test_row_label_escapes_markdown_and_shows_status(engine):  # noqa: F811
    project = _project()
    character = _chars().create(project.id, name="*Voltix* [x]")
    _chars().deactivate(project.id, character.id)
    assert row_label(_chars().get(project.id, character.id)) == r"\*Voltix\* \[x\] · inactif"
