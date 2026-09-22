"""Sélection facultative de personnages et d'un lieu dans « Nouvelle production » (ticket #34, AppTest).

Le dépôt et la résolution en snapshot immuable sont déjà couverts par test_narrative_context.py (#35) ;
ce fichier ne teste que l'écran : offre limitée aux éléments actifs du projet courant, aucune sélection
obligatoire, comportement inchangé sans sélection, invalidation de l'estimation si la sélection change,
et absence de fuite entre projets.
"""

from __future__ import annotations

import pytest

from lody import settings
from lody.characters import CharacterRepository
from lody.generation.service import ProductionRepository, request_of
from lody.locations import LocationRepository
from test.lody.test_generation_ui import (  # noqa: F401
    SUBJECT,
    _button,
    _fresh_resources,
    _labels,
    _prepare,
    _project,
    _run,
    _text,
    engine,
)

pytest.importorskip("streamlit.testing.v1")

SECTION_TITLE = "Personnages et lieu (facultatif)"


def _repos():
    """Ouverts sur la même base que l'application (voir ``settings.db_path()`` dans ``lody_env``)."""
    return CharacterRepository(settings.db_path()), LocationRepository(settings.db_path())


def _open(project):
    return _run({"projet": project.id, "vue": "production"})


def _expand(app):
    """La section est repliée par défaut, mais ses widgets existent dans l'arbre dès le script exécuté."""
    return next(expander for expander in app.expander if expander.label == SECTION_TITLE)


def _draft(project):
    return ProductionRepository(settings.db_path()).list_for_project(project.id)[0]


# -- absence de personnages/lieux : formulaire inchangé -----------------------------------------------------
def test_project_without_characters_or_locations_keeps_the_form_unchanged(engine):  # noqa: F811
    project = _project()
    app = _open(project)
    assert not app.exception
    assert SECTION_TITLE not in [expander.label for expander in app.expander]
    app = _prepare(app, project)
    assert "Estimation et confirmation" in _text(app)
    assert _draft(project).snapshot.get("narrative_context") == {}


# -- offre limitée aux éléments actifs du projet courant -----------------------------------------------------
def test_only_active_characters_and_locations_of_the_current_project_are_offered(engine):  # noqa: F811
    characters, locations = _repos()
    project = _project()
    other = _project("Audiovisuel")

    active = characters.create(project.id, name="Léa", role="Guide")
    inactive = characters.create(project.id, name="Marc")
    characters.deactivate(project.id, inactive.id)
    characters.create(other.id, name="Personnage d'un autre projet")

    active_location = locations.create(project.id, name="Atelier", location_type="Intérieur")
    inactive_location = locations.create(project.id, name="Entrepôt")
    locations.deactivate(project.id, inactive_location.id)
    locations.create(other.id, name="Lieu d'un autre projet")

    app = _open(project)
    expander = _expand(app)
    multiselect = next(iter(expander.multiselect))
    selectbox = next(iter(expander.selectbox))

    # .options renvoie les libellés affichés (format_func déjà appliqué), pas les identifiants bruts.
    assert multiselect.options == [active.name]
    assert set(selectbox.options) == {"Aucun lieu", active_location.name}


def test_the_primary_flag_is_shown_but_never_preselected(engine):  # noqa: F811
    characters, locations = _repos()
    project = _project()
    characters.create(project.id, name="Léa", is_primary=True)
    locations.create(project.id, name="Atelier", is_primary=True)

    app = _open(project)
    expander = _expand(app)
    multiselect = next(iter(expander.multiselect))
    selectbox = next(iter(expander.selectbox))

    assert multiselect.value == []  # rien d'imposé automatiquement
    assert selectbox.value is None
    assert multiselect.options == ["Léa · Principal"]
    assert "Atelier · Principal" in selectbox.options


# -- sélection facultative : rien ne change sans sélection -----------------------------------------------------
def test_preparing_without_any_selection_matches_the_previous_behaviour(engine):  # noqa: F811
    characters, locations = _repos()
    project = _project()
    characters.create(project.id, name="Léa")
    locations.create(project.id, name="Atelier")

    app = _open(project)
    app = _prepare(app, project)
    assert "Estimation et confirmation" in _text(app)
    assert _draft(project).snapshot.get("narrative_context") == {}


# -- sélection effective : enregistrée dans le snapshot et résumée ---------------------------------------------
def test_selection_round_trip_produces_expected_snapshot_and_summary(engine):  # noqa: F811
    characters, locations = _repos()
    project = _project()
    lea = characters.create(project.id, name="Léa", role="Guide")
    atelier = locations.create(project.id, name="Atelier solaire")

    app = _open(project)
    char_key = next(iter(_expand(app).multiselect)).key
    app = app.multiselect(key=char_key).set_value([lea.id]).run()
    loc_key = next(iter(_expand(app).selectbox)).key
    app = app.selectbox(key=loc_key).set_value(atelier.id).run()
    app = _prepare(app, project)
    assert not app.exception

    draft = _draft(project)
    narrative = draft.snapshot.get("narrative_context")
    assert narrative and [c["id"] for c in narrative["characters"]] == [lea.id]
    assert narrative["location"]["id"] == atelier.id

    text = _text(app)
    assert "Estimation et confirmation" in text
    assert "Léa" in text and "Atelier solaire" in text  # résumé dans l'écran d'estimation et de confirmation

    # Non-injection (#34 hors périmètre) : le payload transmis au moteur n'est jamais changé par la sélection.
    assert request_of(draft).to_dict().get("subject") == SUBJECT


def test_changing_the_selection_after_preparing_invalidates_the_estimate(engine):  # noqa: F811
    characters, _ = _repos()
    project = _project()
    lea = characters.create(project.id, name="Léa")
    marc = characters.create(project.id, name="Marc")

    app = _open(project)
    char_key = next(iter(_expand(app).multiselect)).key
    app = app.multiselect(key=char_key).set_value([lea.id]).run()
    app = _prepare(app, project)
    assert "Estimation et confirmation" in _text(app)

    char_key = next(iter(_expand(app).multiselect)).key
    app = app.multiselect(key=char_key).set_value([lea.id, marc.id]).run()
    assert "Préparer la génération" in _labels(app)
    assert "Estimation et confirmation" not in _text(app)


def test_deactivating_a_character_while_selected_silently_drops_it(engine):  # noqa: F811
    characters, _ = _repos()
    project = _project()
    lea = characters.create(project.id, name="Léa")
    marc = characters.create(project.id, name="Marc")  # reste actif : la section continue d'être proposée

    app = _open(project)
    char_key = next(iter(_expand(app).multiselect)).key
    app = app.multiselect(key=char_key).set_value([lea.id, marc.id]).run()

    characters.deactivate(project.id, lea.id)  # désactivation concurrente (ex. un autre onglet)

    app = app.run()  # nouveau tour de script : ne doit jamais planter, ni renvoyer l'identifiant fantôme
    assert not app.exception
    multiselect = next(iter(_expand(app).multiselect))
    assert multiselect.value == [marc.id] and lea.name not in multiselect.options and marc.name in multiselect.options

    app = _prepare(app, project)
    assert not app.exception
    narrative = _draft(project).snapshot.get("narrative_context")
    assert [c["id"] for c in narrative["characters"]] == [marc.id]


def test_deactivating_the_last_active_character_removes_the_section_entirely(engine):  # noqa: F811
    """Cas limite : plus aucun personnage/lieu actif → le formulaire redevient identique à un projet vierge."""
    characters, _ = _repos()
    project = _project()
    lea = characters.create(project.id, name="Léa")

    app = _open(project)
    char_key = next(iter(_expand(app).multiselect)).key
    app = app.multiselect(key=char_key).set_value([lea.id]).run()

    characters.deactivate(project.id, lea.id)
    app = app.run()

    assert not app.exception
    assert SECTION_TITLE not in [expander.label for expander in app.expander]
    app = _prepare(app, project)
    assert not app.exception
    assert _draft(project).snapshot.get("narrative_context") == {}


def test_switching_project_never_leaks_a_selection_into_another_project(engine):  # noqa: F811
    characters, _ = _repos()
    project = _project()
    other = _project("Audiovisuel")
    lea = characters.create(project.id, name="Léa")
    characters.create(other.id, name="Personnage de l'autre projet")

    app = _open(project)
    char_key = next(iter(_expand(app).multiselect)).key
    app = app.multiselect(key=char_key).set_value([lea.id]).run()

    other_app = _open(other)
    assert not other_app.exception
    # L'état de session est namespacé par projet (clé ``chars_sel_<project.id>``) : rien de la sélection
    # faite dans le premier projet ne transparaît dans un autre, même s'il propose ses propres personnages.
    assert "Léa" not in _text(other_app)
    expander = _expand(other_app)
    multiselect = next(iter(expander.multiselect))
    assert multiselect.value == []
