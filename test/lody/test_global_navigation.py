"""Navigation globale Projets / Personnages / Ressources (#213).

Fonctions pures (agrégation en lecture seule, voix affichée, reprise du dernier projet, routes) puis
parcours AppTest. Aucun appel réseau ni fournisseur payant.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lody import library, nav, settings
from lody.animation.assets import AnimationAssetRepository
from lody.characters import CharacterRepository
from lody.locations import LocationRepository
from lody.projects import ProjectRepository
from lody.view_home import resume_target

apptest = pytest.importorskip("streamlit.testing.v1")
WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"


# -- fonctions pures ------------------------------------------------------------
def _char(name: str, **extra):
    base = dict(id=f"chr_{name}", name=name, role="", personality="", visual_description="", voice_provider="",
                voice_name="", external_voice_id="", is_primary=False, is_active=True)
    base.update(extra)
    return SimpleNamespace(**base)


def _proj(pid: str, name: str, updated: str = "2026-11-01T00:00:00+00:00"):
    return SimpleNamespace(id=pid, name=name, updated_at=updated)


def test_voice_text_follows_existing_rule_provider_and_id_both_required():
    full = _char("Voltix", voice_provider="elevenlabs", voice_name="Rachel", external_voice_id="abc123")
    assert library.character_voice_text(full) == "Voix propre : Rachel (ElevenLabs)"
    assert "abc123" not in library.character_voice_text(full)  # jamais d'identifiant affiché
    assert library.character_voice_text(_char("Eli", voice_provider="elevenlabs")).startswith("Pas de voix propre")
    assert library.character_voice_text(_char("Eli", external_voice_id="x")).startswith("Pas de voix propre")
    assert library.character_voice_text(_char("Eli", voice_provider="inconnu", external_voice_id="x")).startswith(
        "Pas de voix propre")


def test_character_entries_keep_their_project_skip_inactive_and_open_in_that_project():
    projects = [_proj("p1", "Câble SDI"), _proj("p2", "Tally")]
    entries = library.character_entries(projects, {
        "p1": [_char("Voltix", role="mascotte", is_primary=True), _char("Ancien", is_active=False)],
        "p2": [_char("Eli")],
    })
    assert [(e.name, e.project_id, e.project_name) for e in entries] == [
        ("Voltix", "p1", "Câble SDI"), ("Eli", "p2", "Tally")]
    assert entries[0].label == "Voltix · mascotte · principal"
    assert all(e.target_view == nav.VIEW_CHARACTERS for e in entries)


def test_filter_matches_name_role_or_project_without_accents_and_groups_by_project():
    projects = [_proj("p1", "Câble SDI"), _proj("p2", "Tally")]
    entries = library.character_entries(projects, {"p1": [_char("Zoé"), _char("Voltix")], "p2": [_char("Éli")]})
    assert [e.name for e in library.filter_entries(entries, "cable")] == ["Zoé", "Voltix"]
    assert [e.name for e in library.filter_entries(entries, "eli")] == ["Éli"]
    assert library.filter_entries(entries, "  ") == entries
    groups = library.group_by_project(entries)
    assert [(pid, [e.name for e in items]) for pid, _, items in groups] == [("p1", ["Voltix", "Zoé"]), ("p2", ["Éli"])]


def test_location_and_asset_entries_target_the_right_pages():
    projects = [_proj("p1", "Câble SDI")]
    loc = SimpleNamespace(id="loc_1", name="Régie", location_type="intérieur", description="", is_primary=False,
                          is_active=True)
    asset = SimpleNamespace(id="ast_1", name="voltix-rig", kind="character", description="", is_active=True)
    [location] = library.location_entries(projects, {"p1": [loc]})
    [rig] = library.asset_entries(projects, {"p1": [asset]})
    assert location.target_view == nav.VIEW_LOCATIONS and location.label == "Régie · intérieur"
    assert rig.target_view == nav.VIEW_PROJECT and rig.label == "voltix-rig · Marionnette"


def test_resume_target_prefers_last_opened_then_most_recent_active():
    old, new = _proj("a", "Ancien", "2026-01-01T00:00:00+00:00"), _proj("b", "Récent", "2026-11-01T00:00:00+00:00")
    assert resume_target([old, new], "a") is old
    assert resume_target([old, new], None) is new
    assert resume_target([old, new], "archivé-ou-supprimé") is new
    assert resume_target([], "a") is None


# -- parcours AppTest ------------------------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_cache(lody_env, monkeypatch):
    import streamlit as st

    monkeypatch.setenv("LODY_SEED_DEFAULTS", "0")
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def _seed():
    db = settings.db_path()
    projects = ProjectRepository(db)
    cable, tally, old = projects.create(name="Câble SDI"), projects.create(name="Tally"), projects.create(name="Archivé")
    characters = CharacterRepository(db)
    characters.create(cable.id, name="Voltix", role="mascotte")
    characters.create(tally.id, name="Eli", role="technicienne")
    characters.create(old.id, name="Fantôme")
    locations = LocationRepository(db)
    locations.create(cable.id, name="Régie", location_type="intérieur")
    AnimationAssetRepository(db).create(cable.id, kind="prop", name="cable-bnc")
    projects.archive(old.id)
    return cable, tally


def _run(params: dict[str, str] | None = None):
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=30)
    for key, value in (params or {}).items():
        app.query_params[key] = value
    return app.run()


def _button(app, label: str):
    return next(button for button in app.button if button.label == label)


def _labels(app) -> list[str]:
    return [button.label for button in app.button]


def _text(app) -> str:
    return " ".join(str(item.value) for item in app.markdown)


def test_home_has_three_domains_resume_and_no_redundant_all_projects_link():
    _seed()
    app = _run()
    assert not app.exception
    assert _labels(app)[:3] == ["Projets", "Personnages", "Ressources"]
    assert _button(app, "Projets").disabled and not _button(app, "Personnages").disabled
    assert "Tous les projets" not in _labels(app)
    assert sum(label.startswith("Reprendre : ") for label in _labels(app)) == 1
    assert "Reprendre : Archivé" not in _labels(app)
    # Le dernier projet ouvert devient la reprise proposée, et l'ouvre en un appui.
    app.text_input(key="home_search").set_value("tally").run()
    _button(app, "Ouvrir").click().run()
    tally_id = app.query_params["projet"]
    assert tally_id and "Tous les projets" in _labels(app)
    _button(app, "Tous les projets").click().run()
    _button(app, "Reprendre : Tally").click().run()
    assert app.query_params["projet"] == tally_id


def test_characters_domain_lists_every_active_project_and_opens_in_the_right_project():
    cable, _ = _seed()
    app = _run()
    _button(app, "Personnages").click().run()
    assert not app.exception
    assert app.query_params["vue"] == ["personnages"] and not app.query_params.get("projet")
    labels = [expander.label for expander in app.expander]
    assert "Voltix · mascotte" in labels and "Eli · technicienne" in labels
    assert "Fantôme" not in labels  # projet archivé : jamais listé
    assert "Câble SDI (1)" in _text(app) and "Dans le projet « Câble SDI »" in _text(app)
    app.text_input(key="lib_char_search").set_value("cable").run()
    assert [e.label for e in app.expander if e.label != "Portée et voix : comment ça marche ?"] == ["Voltix · mascotte"]
    _button(app, "Ouvrir les personnages du projet").click().run()
    assert not app.exception
    assert app.query_params["projet"] == [cable.id] and app.query_params["vue"] == ["personnages"]
    _button(app, "Tous les projets").click().run()
    _button(app, "Personnages").click().run()
    assert app.text_input(key="lib_char_search").value == "cable"  # recherche conservée


def test_resources_domain_switches_between_locations_and_animated_elements():
    cable, _ = _seed()
    app = _run({"vue": "ressources"})
    assert not app.exception
    assert [e.label for e in app.expander] == ["Régie · intérieur"]
    _button(app, "Ouvrir les lieux du projet").click().run()
    assert app.query_params["projet"] == [cable.id] and app.query_params["vue"] == ["lieux"]
    app = _run({"vue": "ressources"})
    app.segmented_control(key="lib_res_kind").set_value("Éléments animés").run()
    assert [e.label for e in app.expander] == [r"cable\-bnc · Accessoire"]  # libellé échappé (Markdown)


def test_existing_deep_links_are_unchanged():
    cable, _ = _seed()
    app = _run({"projet": cable.id, "vue": "personnages"})
    assert not app.exception
    assert "Tous les projets" in _labels(app) and "Ressources" not in _labels(app)  # page du projet, pas la vue globale
    assert nav.Route(nav.VIEW_CHARACTERS, cable.id) == _route(projet=cable.id, vue="personnages")
    assert _route(vue="personnages") == nav.Route(nav.VIEW_ALL_CHARACTERS)
    assert _route(vue="ressources") == nav.Route(nav.VIEW_RESOURCES)
    assert _route(vue="inconnue") == nav.Route(nav.VIEW_HOME)


def _route(**params: str) -> nav.Route:
    import streamlit as st

    st.query_params.clear()
    st.query_params.update(params)
    try:
        return nav.current_route()
    finally:
        st.query_params.clear()


def test_empty_install_still_shows_navigation_and_clear_empty_states():
    app = _run({"vue": "personnages"})
    assert not app.exception
    assert "Aucun personnage pour l’instant" in _text(app)
    app = _run({"vue": "ressources"})
    assert not app.exception and "Aucun lieu pour l’instant" in _text(app)
