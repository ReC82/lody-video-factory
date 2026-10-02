"""Diagnostic — afficher la provenance exacte du script, du TTS et des prompts de scène (ticket #81).

Lecture seule, en complément du diagnostic existant (#69/#70/#75/#76) : distingue explicitement « script
final validé (#76) et transmis dans video_script » de « voix snapshotée (prévue) » vs « voix transmise
(trace, #75) », et relie chaque prompt de scène à son passage source, ses personnages mentionnés, son lieu
et son injection. Toujours lu depuis production.script/snapshot/trace/params — jamais depuis les fiches
projet/personnages/lieux actuelles. Faux connecteurs uniquement, aucun appel réel, aucun changement de
pipeline ou de payload.

Les noms de personnage et de voix utilisés ici sont volontairement génériques (« Personnage test »,
« Voix spécifique du personnage », « Ancienne voix projet — SENTINELLE ») : le comportement vérifié ne
dépend d'aucun nom particulier — il doit valoir pour n'importe quel personnage et n'importe quelle voix.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from lody.characters import CharacterRepository
from lody.generation.costing import PriceBook
from lody.generation.narrative_context import scene_mentioned_characters
from lody.generation.service import ProductionService
from lody.generation.store import Production, ProductionRepository
from lody.locations import LocationRepository
from lody.projects import DEFAULTS, ProjectRepository
from lody.secrets_guard import find_secret_path
from lody.view_tracking import _provenance_html, _scene_provenance_line
from test.lody.fakes import ScriptedConnector, SyncExecutor
from test.lody.test_generation_ui import _button, _prepare, _project, _run, engine  # noqa: F401

pytest.importorskip("streamlit.testing.v1")

PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.3"), ("music", "elevenlabs"): Decimal("0.1")})
SUBJECT = "Explique en une phrase ce qu'est un bloc dans une blockchain, pour un débutant curieux."

# Voix spécifique d'un personnage sélectionné — n'importe quel personnage, n'importe quelle voix : aucun
# traitement particulier lié à un nom.
CHARACTER_VOICE_ID = "CHARACTER-VOICE-TEST1"
CHARACTER_VOICE_NAME = "Voix spécifique du personnage"
# Voix de repli du PROJET, volontairement distincte et nommée SENTINELLE : si elle apparaît alors qu'un
# personnage avec une voix spécifique valide est sélectionné, c'est la preuve d'une régression.
OLD_PROJECT_VOICE_SENTINEL_ID = "OLD-PROJECT-VOICE-SENTINEL9"
OLD_PROJECT_VOICE_SENTINEL_NAME = "Ancienne voix projet — SENTINELLE (ne doit jamais être envoyée à sa place)"


def _text(app):
    """Comme test_generation_ui._text, mais inclut ``app.caption`` (légende et ligne de provenance par
    scène, toutes deux rendues en ``st.caption``)."""
    return " ".join(str(item.value) for item in app.markdown) + " " + " ".join(str(c.value) for c in app.caption)


class Env:
    """Projet avec une voix projet SENTINELLE distincte de la voix spécifique du personnage test, service
    câblé sur un faux connecteur (aucun appel réel)."""

    def __init__(self, tmp_path):
        self.path = tmp_path / "lody.sqlite3"
        self.projects = ProjectRepository(self.path)
        self.characters = CharacterRepository(self.path)
        self.locations = LocationRepository(self.path)
        fields = {**copy.deepcopy(DEFAULTS), "name": "Projet test", "language": "fr-FR",
                  "text_provider": "openai", "visual_provider": "openai_image",
                  "voice_provider": "elevenlabs", "voice_name": OLD_PROJECT_VOICE_SENTINEL_NAME,
                  "music_provider": "none",
                  "settings": {"brief": {"voice_id": OLD_PROJECT_VOICE_SENTINEL_ID,
                                         "voice_model": "eleven_multilingual_v2"}}}
        self.project = self.projects.create(**fields)
        self.connector = ScriptedConnector(tmp_path / "storage")
        self.service = ProductionService(
            ProductionRepository(self.path), {"scripted": self.connector}, SyncExecutor(),
            price_book=lambda: PRICES, character_repo=self.characters, location_repo=self.locations,
        )

    def add_test_character(self, **overrides):
        fields = {"name": "Personnage test", "is_primary": True, "voice_provider": "elevenlabs",
                  "external_voice_id": CHARACTER_VOICE_ID, "voice_name": CHARACTER_VOICE_NAME, **overrides}
        return self.characters.create(self.project.id, **fields)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def _fake_production(**overrides) -> Production:
    base = dict(
        id="prd_x", project_id="prj_x", root_production_id="prd_x", parent_production_id=None, version=1,
        subject=SUBJECT, brief={}, script="", script_source="", storyboard=[], visual_prompts=[], params={},
        cost_currency="EUR", cost_low=None, cost_high=None, cost_partial=True, cost_detail={},
        confirmed_at=None, provider="scripted", external_task_id=None, idempotency_key="k", status=None,
        progress=None, current_step="", error_code="", error_message="", created_at="x", started_at=None,
        finished_at=None, updated_at="x", last_polled_at=None, video_ref="", video_duration=None,
    )
    base.update(overrides)
    return Production(**base)


# ======================================================================================================================
# -- scene_mentioned_characters() : fonction pure ------------------------------------------------------------------
# ======================================================================================================================
def test_scene_mentioned_characters_finds_only_characters_actually_named():
    characters = [{"name": "Personnage test"}, {"name": "Zoé"}]
    assert scene_mentioned_characters("Personnage test arrive au village.", characters) == ["Personnage test"]
    assert scene_mentioned_characters("Rien de spécial ici.", characters) == []
    assert scene_mentioned_characters("Personnage test et Zoé discutent.", characters) == ["Personnage test", "Zoé"]


# ======================================================================================================================
# -- _provenance_html() : fonction pure, compatibilité et non-ambiguïté --------------------------------------------
# ======================================================================================================================
def test_historical_production_without_snapshot_or_trace_shows_placeholders_not_a_crash():
    old = _fake_production(script="Un script déjà écrit avant la traçabilité.", snapshot={}, trace={})
    html = _provenance_html(old)
    assert "Un script déjà écrit avant la traçabilité." in html
    assert html.count("—") >= 3  # voix snapshotée, voix transmise, source : toutes absentes proprement
    assert "antérieure à #70/#75" in html


def test_voice_not_yet_transmitted_is_distinguished_from_no_snapshot_at_all():
    """Une production préparée (voix déjà snapshotée) mais pas encore envoyée (trace encore vide) doit
    distinguer « pas encore transmise » de « jamais eu de voix du tout » (production historique)."""
    production = _fake_production(
        snapshot={"request": {"voice": {"provider": "elevenlabs", "voice_id": CHARACTER_VOICE_ID,
                                        "name": CHARACTER_VOICE_NAME, "model": "x"}}},
        trace={},
    )
    html = _provenance_html(production)
    assert CHARACTER_VOICE_ID in html
    assert "pas encore transmise" in html


def test_character_voice_sent_and_project_sentinel_never_sent_is_provable_from_the_html():
    """LE critère d'acceptation central du ticket : une production permet de prouver si la voix spécifique
    du personnage OU la voix de repli (sentinelle) du projet a été envoyée — quel que soit le nom du
    personnage ou de la voix."""
    production = _fake_production(
        script="Bienvenue dans le monde des blocs.",
        snapshot={"request": {"voice": {"provider": "elevenlabs", "voice_id": CHARACTER_VOICE_ID,
                                        "name": CHARACTER_VOICE_NAME, "model": "x"}}},
        trace={"voice": {"provider": "elevenlabs", "voice_id": CHARACTER_VOICE_ID, "name": CHARACTER_VOICE_NAME,
                        "source": "character", "fallback": False, "reason": "voix du personnage"}},
        params={"voice_resolution": {"source": "character"}},
    )
    html = _provenance_html(production)
    assert CHARACTER_VOICE_ID in html and CHARACTER_VOICE_NAME in html
    assert OLD_PROJECT_VOICE_SENTINEL_ID not in html and OLD_PROJECT_VOICE_SENTINEL_NAME not in html
    assert "Identique" in html  # prévue == transmise, prouvé par deux sources indépendantes
    assert "personnage sélectionné" in html


def test_a_simulated_divergence_between_snapshot_and_trace_is_visibly_flagged():
    """Défense en profondeur : si jamais la voix snapshotée et la voix transmise divergeaient (un bug
    hypothétique que #75 empêche déjà en pratique), ce diagnostic doit le rendre visible, jamais le taire."""
    production = _fake_production(
        snapshot={"request": {"voice": {"provider": "elevenlabs", "voice_id": CHARACTER_VOICE_ID,
                                        "name": CHARACTER_VOICE_NAME, "model": "x"}}},
        trace={"voice": {"provider": "elevenlabs", "voice_id": "AUTRE-ID-DIFFERENT", "name": "Autre voix"}},
    )
    html = _provenance_html(production)
    assert "DIVERGENCE" in html


def test_manual_script_is_also_labelled_validated_and_transmitted():
    production = _fake_production(script="Un script fourni à la main, parfaitement valide.", script_source="manual")
    html = _provenance_html(production)
    assert "Un script fourni à la main, parfaitement valide." in html
    assert "validé (#76)" in html and "video_script" in html


def test_no_secret_ever_appears_in_the_provenance_html():
    production = _fake_production(
        script="Un script tout à fait normal.",
        snapshot={"request": {"voice": {"provider": "elevenlabs", "voice_id": CHARACTER_VOICE_ID,
                                        "name": CHARACTER_VOICE_NAME, "model": "x"}}},
        trace={"voice": {"provider": "elevenlabs", "voice_id": CHARACTER_VOICE_ID, "name": CHARACTER_VOICE_NAME}},
    )
    assert find_secret_path(_provenance_html(production)) is None


# ======================================================================================================================
# -- _scene_provenance_line() : fonction pure -----------------------------------------------------------------------
# ======================================================================================================================
def test_scene_line_lists_mentioned_character_and_location_and_injection_state():
    narrative = {"characters": [{"name": "Personnage test"}], "location": {"name": "Place du village"}}
    prompt_sent = ("un prompt de scène\n\n### Continuité visuelle (instantané de production, #34/#35/#37) ###\n"
                  "Personnage test, Place du village")
    line = _scene_provenance_line({"prompt_sent": prompt_sent, "engine_template_applied": True},
                                  "Personnage test explore la place.", narrative)
    assert "Personnages mentionnés : Personnage test" in line
    assert "Lieu (toutes les scènes) : Place du village" in line
    assert "Injection visuelle : appliquée" in line
    assert "Gabarit moteur : appliqué" in line


def test_scene_line_without_any_mention_or_selection_says_so_explicitly():
    line = _scene_provenance_line({"prompt_sent": "un prompt ordinaire", "engine_template_applied": False},
                                  "Une narration neutre.", {})
    assert "Personnages mentionnés : aucun" in line
    assert "Injection visuelle : non appliquée" in line
    assert "Gabarit moteur : non appliqué" in line
    assert "Lieu" not in line  # aucun lieu sélectionné : pas de ligne « Lieu » du tout


# ======================================================================================================================
# -- intégration bout-en-bout (ProductionService, faux connecteur) + écran (AppTest) -------------------------------
# ======================================================================================================================
def test_character_and_place_du_village_end_to_end_proves_character_voice_not_sentinel_was_sent(env):
    character = env.add_test_character()
    village = env.locations.create(env.project.id, name="Place du village", location_type="Extérieur")
    script = ("Personnage test se promène tranquillement. Il observe les alentours avec attention. "
             "Le soleil brille fort aujourd'hui.")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=script,
                                character_ids=[character.id], location_id=village.id)
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "EN_FILE"

    html = _provenance_html(launched)
    assert CHARACTER_VOICE_ID in html
    assert OLD_PROJECT_VOICE_SENTINEL_ID not in html and OLD_PROJECT_VOICE_SENTINEL_NAME not in html

    narrative = launched.snapshot.get("narrative_context") or {}
    narrations = {s["index"]: s["narration"] for s in launched.storyboard}
    for scene in launched.trace.get("scenes") or []:
        line = _scene_provenance_line(scene, narrations.get(scene["index"], ""), narrative)
        assert "Place du village" in line  # le lieu s'applique à toutes les scènes


def test_fallback_voice_is_named_explicitly_not_silently_shown_as_normal(env):
    incomplete = env.characters.create(env.project.id, name="Sans voix", is_primary=True)
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[incomplete.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    html = _provenance_html(launched)
    assert OLD_PROJECT_VOICE_SENTINEL_NAME in html  # repli sur la voix du projet, bien visible
    assert "voix du projet" in html


def test_tracking_page_shows_the_provenance_section_with_no_secret(engine):  # noqa: F811
    from lody import settings
    from lody.characters import CharacterRepository as _CR
    from lody.locations import LocationRepository as _LR
    from lody.generation.store import ProductionRepository as _PR

    project = _project()
    characters = _CR(settings.db_path())
    locations = _LR(settings.db_path())
    character = characters.create(project.id, name="Personnage test", is_primary=True,
                                  voice_provider="elevenlabs", external_voice_id=CHARACTER_VOICE_ID,
                                  voice_name=CHARACTER_VOICE_NAME)
    locations.create(project.id, name="Place du village")

    app = _run({"projet": project.id, "vue": "production"})
    app.multiselect(key=f"chars_sel_{project.id}").set_value([character.id]).run()
    app.selectbox(key=f"loc_sel_{project.id}").set_value(
        next(loc.id for loc in locations.list_for_project(project.id))).run()
    app = _prepare(app, project)
    app = _button(app, "Confirmer et générer la vidéo").click().run()
    assert not app.exception

    production = _PR(settings.db_path()).list_for_project(project.id)[0]
    app = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    text = _text(app)

    assert "Provenance — script et voix" in text
    assert "validé (#76)" in text and "video_script" in text
    assert "Voix snapshotée" in text and "Voix transmise" in text
    assert CHARACTER_VOICE_ID in text
    assert "Personnages mentionnés" in text
    for secret in ("FAKE-CONFIG-VALUE-NOT-A-KEY", "sk-", "api_key"):
        assert secret not in text


def test_isolation_between_projects_provenance_never_leaks(env):
    other = env.projects.create(name="Autre projet", language="fr-FR", text_provider="openai",
                                visual_provider="openai_image", voice_provider="edge", music_provider="none")
    other_char = env.characters.create(other.id, name="Personnage de l'autre projet",
                                       voice_provider="elevenlabs", external_voice_id="AUTREPROJETID00001",
                                       is_primary=True)
    draft_other = env.service.prepare(other, SUBJECT, provider_id="scripted", character_ids=[other_char.id])
    launched_other = env.service.confirm(draft_other.id, accept_partial=True)

    character = env.add_test_character()
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[character.id])
    launched = env.service.confirm(draft.id, accept_partial=True)

    html = _provenance_html(launched)
    assert "AUTREPROJETID00001" not in html
    other_html = _provenance_html(launched_other)
    assert CHARACTER_VOICE_ID not in other_html
