"""Diagnostic — rendre visibles les sélections et injections personnage/lieu (ticket #69).

Le pipeline de génération n'est pas modifié : ``script_injection_applied``/``visual_injection_applied``
(``lody.generation.narrative_context``) sont des lectures PURES sur des données déjà enregistrées
(``production.snapshot``/``production.trace``), et le nouveau bloc de ``view_tracking.py`` ne fait que les
afficher. Ce fichier couvre : les deux fonctions pures, l'intégration bout-en-bout (Eli seul, Place du
village seule, les deux ensemble, aucune sélection, production historique, modification après confirmation,
V2, isolation entre projets), et l'écran (texte obsolète disparu, bloc affiché, aucun secret). Aucun appel
réseau, aucun appel payant.
"""

from __future__ import annotations

import copy

import pytest

from lody.generation.narrative_context import (
    NARRATIVE_CONTEXT_VERSION,
    _HEADER,
    _VISUAL_HEADER,
    script_injection_applied,
    visual_injection_applied,
)
from lody.generation.store import ProductionRepository
from lody.secrets_guard import find_secret_path
from lody.view_tracking import _narrative_diagnostic_html
from test.lody.test_generation_ui import _button, _fresh_resources, _labels, _prepare, _project, _run, engine  # noqa: F401
from test.lody.test_narrative_context import PRICES, SUBJECT, Env  # noqa: F401

pytest.importorskip("streamlit.testing.v1")


def _text(app):
    """Comme test_generation_ui._text, mais inclut aussi ``st.caption`` (utilisé par la légende de la
    section personnages/lieu de view_production.py — jamais un ``st.markdown``)."""
    return " ".join(str(item.value) for item in app.markdown) + " " + " ".join(str(c.value) for c in app.caption)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# ======================================================================================================================
# -- fonctions pures : script_injection_applied / visual_injection_applied --------------------------------------------
# ======================================================================================================================
def test_script_injection_applied_requires_both_a_selection_and_the_marker_in_the_sent_text():
    narrative = {"characters": [{"name": "Eli"}], "location": None}
    assert script_injection_applied(narrative, f"préambule\n{_HEADER}\nsuite") is True
    assert script_injection_applied(narrative, "texte sans marqueur") is False
    assert script_injection_applied({}, _HEADER) is False  # aucune sélection : jamais "appliqué"
    assert script_injection_applied(None, _HEADER) is False
    assert script_injection_applied(narrative, None) is False  # script fourni/repris : rien envoyé
    assert script_injection_applied(narrative, "") is False


def test_visual_injection_applied_requires_both_a_selection_and_the_marker_in_the_sent_text():
    narrative = {"characters": [], "location": {"name": "Place du village"}}
    assert visual_injection_applied(narrative, f"{_VISUAL_HEADER}\nbla") is True
    assert visual_injection_applied(narrative, "prompt original, sans continuité") is False
    assert visual_injection_applied({}, _VISUAL_HEADER) is False
    assert visual_injection_applied(narrative, None) is False


# ======================================================================================================================
# -- intégration bout-en-bout (ProductionService, faux connecteur, aucun appel payant) ---------------------------------
# ======================================================================================================================
def test_no_selection_shows_no_character_or_location_selected(env):  # noqa: F811
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    launched = env.service.confirm(draft.id, accept_partial=True)
    html = _narrative_diagnostic_html(launched)
    assert "Aucun personnage ou lieu sélectionné" in html
    assert "Eli" not in html and "Place du village" not in html


def test_historical_production_without_narrative_context_shows_the_compatibility_message(env):  # noqa: F811
    """Production antérieure à #35 : la clé ``narrative_context`` n'existe même pas dans le snapshot. Le
    snapshot n'est modifiable QU'AVANT confirmation (voir store.SNAPSHOT_WRITABLE_FROM) : on l'altère donc
    sur le brouillon, puis on confirme — comme test_narrative_context.py::
    test_legacy_production_snapshot_without_narrative_context_key_is_accepted."""
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    legacy_snapshot = copy.deepcopy(draft.snapshot)
    del legacy_snapshot["narrative_context"]
    assert env.service.repo.transition(draft.id, [draft.status], snapshot=legacy_snapshot)
    launched = env.service.confirm(draft.id, accept_partial=True)
    html = _narrative_diagnostic_html(launched)
    assert "Aucun personnage ou lieu sélectionné" in html


def test_production_with_no_snapshot_at_all_shows_the_compatibility_message(env):  # noqa: F811
    """Encore plus ancien : ``snapshot`` lui-même est vide (``{}``)."""
    repo = ProductionRepository(env.path)
    # Construction directe, comme test_project_isolation_ui.py::test_diagnostic_of_an_older_production...
    from lody.generation.models import ProductionStatus as S

    old = repo.create(project_id=env.project.id, subject=SUBJECT, provider="scripted", status=S.TERMINEE,
                      script="Un script déjà écrit avant la traçabilité.",
                      storyboard=[{"index": 1, "narration": "Un texte.", "prompt": "Prompt scène 1", "seconds": 3.0}],
                      params={"request": {"subject": SUBJECT}, "engine": {}})
    html = _narrative_diagnostic_html(old)
    assert "Aucun personnage ou lieu sélectionné" in html


def test_eli_alone_is_selected_snapshotted_and_injected_only_where_mentioned(env):  # noqa: F811
    eli = env.add_character(name="Eli", role="Guide", personality="Curieux")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    launched = env.service.confirm(draft.id, accept_partial=True)

    assert launched.snapshot["narrative_context"]["version"] == NARRATIVE_CONTEXT_VERSION
    assert [c["name"] for c in launched.snapshot["narrative_context"]["characters"]] == ["Eli"]
    assert launched.snapshot["narrative_context"]["location"] is None

    html = _narrative_diagnostic_html(launched)
    assert "Eli" in html
    assert "Lieu sélectionné (snapshoté)</dt><dd>Aucun" in html
    assert "Injection dans le script</dt><dd>Appliquée" in html  # le connecteur scripté reprend narrative_block
    # ScriptedConnector.write_script écrit toujours SCRIPT (voir fakes.py) ; "Eli" n'y est jamais mentionné :
    # la continuité visuelle n'est donc ajoutée à AUCUNE scène (comportement normal de #37, pas une anomalie).
    assert "Injection dans les prompts visuels</dt><dd>Non appliquée" in html
    assert "Transmis au fournisseur</dt><dd>Oui" in html  # le script, lui, a bien reçu le bloc


def test_place_du_village_alone_applies_to_every_scene(env):  # noqa: F811
    village = env.add_location(name="Place du village", location_type="Extérieur")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", location_id=village.id)
    launched = env.service.confirm(draft.id, accept_partial=True)

    assert launched.snapshot["narrative_context"]["location"]["name"] == "Place du village"
    assert launched.snapshot["narrative_context"]["characters"] == []

    html = _narrative_diagnostic_html(launched)
    assert "Personnages sélectionnés (snapshotés)</dt><dd>Aucun" in html
    assert "Place du village" in html
    assert "Injection dans le script</dt><dd>Appliquée" in html
    scene_count = len(launched.storyboard)
    assert f"Appliquée sur {scene_count} scène(s) sur {scene_count}" in html  # le lieu s'applique à TOUTES les scènes
    assert "Transmis au fournisseur</dt><dd>Oui" in html


def test_eli_and_place_du_village_together(env):  # noqa: F811
    eli = env.add_character(name="Eli")
    village = env.add_location(name="Place du village")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id],
                                location_id=village.id)
    launched = env.service.confirm(draft.id, accept_partial=True)

    html = _narrative_diagnostic_html(launched)
    assert "Eli" in html and "Place du village" in html
    assert "Injection dans le script</dt><dd>Appliquée" in html
    scene_count = len(launched.storyboard)
    # Le lieu s'applique à toutes les scènes même si "Eli" n'est mentionné dans aucune narration.
    assert f"Appliquée sur {scene_count} scène(s) sur {scene_count}" in html


def test_user_supplied_script_makes_script_injection_not_applicable(env):  # noqa: F811
    eli = env.add_character(name="Eli")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script="Mon script fourni, assez long pour passer la validation minimale.",
                                character_ids=[eli.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    html = _narrative_diagnostic_html(launched)
    assert "Injection dans le script</dt><dd>Non applicable" in html


def test_diagnostic_stays_identical_after_editing_the_character_sheet(env):  # noqa: F811
    eli = env.add_character(name="Eli", personality="Curieux")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    before = _narrative_diagnostic_html(launched)

    env.characters.update(env.project.id, eli.id, name="Eli (renommé)", personality="Complètement différent")
    env.characters.deactivate(env.project.id, eli.id)

    after = _narrative_diagnostic_html(env.service.repo.get(launched.id))
    assert after == before  # jamais lu depuis la fiche personnage actuelle
    assert "Eli</dd>" in after or "Eli<" in after  # toujours l'ancien nom, celui du snapshot


def test_v2_diagnostic_shows_the_same_selection_as_v1(env):  # noqa: F811
    eli = env.add_character(name="Eli")
    v1 = env.service.confirm(
        env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id]).id,
        accept_partial=True,
    )
    env.connector.queue(env.connector.done("task-0001"))
    v1 = env.service.refresh(v1.id)
    v2 = env.service.create_v2(v1.id)
    assert v2.snapshot["narrative_context"] == v1.snapshot["narrative_context"]
    # v2 n'a pas encore de trace (script pas encore réécrit) : compatible, pas d'erreur.
    html = _narrative_diagnostic_html(v2)
    assert "Eli" in html


def test_isolation_between_projects_diagnostic_never_leaks_another_projects_selection(env, tmp_path):  # noqa: F811
    other = env.projects.create(name="Autre projet", language="fr-FR", text_provider="openai",
                                visual_provider="openai_image", voice_provider="elevenlabs", voice_name="V",
                                music_provider="elevenlabs")
    env.characters.create(other.id, name="Personnage de l'autre projet")
    eli = env.add_character(name="Eli")

    draft_a = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    launched_a = env.service.confirm(draft_a.id, accept_partial=True)

    html = _narrative_diagnostic_html(launched_a)
    assert "Eli" in html
    assert "Personnage de l'autre projet" not in html


def test_no_secret_pattern_ever_appears_in_the_diagnostic_html(env):  # noqa: F811
    eli = env.add_character(name="Eli", role="Guide", personality="Curieux, bavard")
    village = env.add_location(name="Place du village")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id],
                                location_id=village.id)
    launched = env.service.confirm(draft.id, accept_partial=True)
    html = _narrative_diagnostic_html(launched)
    assert find_secret_path(html) is None


def test_provider_payload_is_strictly_unchanged_by_reading_the_diagnostic(env):  # noqa: F811
    """Fonctions pures : les lire (même plusieurs fois) ne modifie ni le storyboard, ni les prompts, ni le
    coût déjà enregistrés — la preuve que #69 n'ajoute aucun appel ni ne change aucun payload fournisseur."""
    eli = env.add_character(name="Eli")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    before_storyboard = copy.deepcopy(launched.storyboard)
    before_trace = copy.deepcopy(launched.trace)
    before_cost = (launched.cost_low, launched.cost_high, launched.cost_detail)

    for _ in range(3):
        _narrative_diagnostic_html(launched)

    after = env.service.repo.get(launched.id)
    assert after.storyboard == before_storyboard
    assert after.trace == before_trace
    assert (after.cost_low, after.cost_high, after.cost_detail) == before_cost


# ======================================================================================================================
# -- écran (AppTest) : texte obsolète disparu, nouveau bloc, aucun secret ----------------------------------------------
# ======================================================================================================================
def _project_with_a_character():
    """La section « Personnages et lieu (facultatif) » ne s'affiche que si le projet a AU MOINS un
    personnage ou lieu actif (voir ``_render_narrative_section``, comportement inchangé par #69)."""
    from lody import settings
    from lody.characters import CharacterRepository

    project = _project()
    CharacterRepository(settings.db_path()).create(project.id, name="Eli")
    return project


def test_obsolete_claim_is_gone_from_the_new_production_screen(engine):  # noqa: F811
    project = _project_with_a_character()
    app = _run({"projet": project.id, "vue": "production"})
    assert not app.exception
    text = _text(app)
    assert "n’est ajouté au script, à la voix ou aux images" not in text
    assert "figée au lancement" in text


def test_new_caption_mentions_that_a_selection_can_be_used_and_points_to_the_diagnostic(engine):  # noqa: F811
    project = _project_with_a_character()
    app = _run({"projet": project.id, "vue": "production"})
    text = _text(app)
    assert "diagnostic" in text.lower()
    assert "script et/ou les prompts d’images" in text


def test_tracking_page_shows_the_narrative_diagnostic_block(engine):  # noqa: F811
    from lody.characters import CharacterRepository
    from lody.locations import LocationRepository
    from lody import settings

    project = _project()
    characters = CharacterRepository(settings.db_path())
    locations = LocationRepository(settings.db_path())
    eli = characters.create(project.id, name="Eli")
    village = locations.create(project.id, name="Place du village")

    app = _run({"projet": project.id, "vue": "production"})
    keys = {"characters": f"chars_sel_{project.id}", "location": f"loc_sel_{project.id}"}
    app.multiselect(key=keys["characters"]).set_value([eli.id]).run()
    app.selectbox(key=keys["location"]).set_value(village.id).run()
    app = _prepare(app, project)
    app = _button(app, "Confirmer et générer la vidéo").click().run()
    assert not app.exception

    from lody.generation.store import ProductionRepository

    production = ProductionRepository(settings.db_path()).list_for_project(project.id)[0]
    app = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    text = _text(app)
    assert "Personnages et lieu" in text
    assert "Eli" in text and "Place du village" in text
    assert "Injection dans le script" in text and "Injection dans les prompts visuels" in text
    assert "Transmis au fournisseur" in text
    for secret in ("FAKE-CONFIG-VALUE-NOT-A-KEY", "sk-", "api_key"):
        assert secret not in text
