"""Script PNJ MVP — dialogue mono-personnage plutôt qu'une narration documentaire (ticket #77).

Structure le contenu parlé en deux modes explicitement distincts et TOUJOURS tracés (jamais silencieux,
voir ``narrative_context.script_mode``) :

- ``MODE_NARRATION`` (historique, inchangé) : aucun personnage de référence résolu depuis le snapshot
  (aucune sélection, ou plusieurs personnages sans principal unique — ambiguïté volontairement non résolue
  tant que #39, multi-locuteurs, n'existe pas) ;
- ``MODE_CHARACTER_DIALOGUE`` : EXACTEMENT un personnage de référence (même règle de sélection que
  ``resolve_voice``, #70, voir ``_reference_character``) — le script est alors écrit comme ses propres
  répliques, à la première personne, jamais une description de lui à la troisième personne.

Ne change ni la voix (#70/#75) ni le payload envoyé au moteur (``video_script``/``video_terms`` restent
construits exactement comme avant) : seule l'INSTRUCTION donnée pour ÉCRIRE le script en dépend. Le texte
produit reste soumis, dans les deux modes, au même garde-fou brief/script (#76) et au même diagnostic de
provenance (#81, non modifié par ce ticket).

Noms de personnage/voix volontairement génériques (fixtures permanentes, jamais un nom particulier codé en
dur) : le comportement vérifié ici doit valoir pour n'importe quel personnage.

Faux connecteurs/transport uniquement : aucun appel réel, aucun coût caché.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from lody.characters import CharacterRepository
from lody.generation import mpt_connector as mpt
from lody.generation.costing import PriceBook
from lody.generation.narrative_context import (
    MODE_CHARACTER_DIALOGUE,
    MODE_NARRATION,
    _reference_character,
    resolve_voice,
    script_mode,
)
from lody.generation.models import VoiceSpec
from lody.generation.service import ProductionService, build_request
from lody.generation.store import Production, ProductionRepository
from lody.locations import LocationRepository
from lody.projects import DEFAULTS, ProjectRepository
from lody.secrets_guard import find_secret_path
from test.lody.fakes import ScriptedConnector, SyncExecutor
from test.lody.test_generation_connector import CONFIG, TASK, _connector

PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.3"), ("music", "elevenlabs"): Decimal("0.1")})
SUBJECT = "Explique en une phrase ce qu'est un bloc dans une blockchain, pour un débutant curieux."

# Volontairement générique : le comportement vérifié ici ne dépend d'aucun nom particulier.
CHARACTER_FIELDS = {"role": "ami curieux", "personality": "jeune, naturel, un peu moqueur",
                    "speech_style": "phrases courtes, familières, quelques private jokes"}


class Env:
    """Projet, personnages/lieux, service câblé sur un faux connecteur (aucun appel réel)."""

    def __init__(self, tmp_path):
        self.path = tmp_path / "lody.sqlite3"
        self.projects = ProjectRepository(self.path)
        self.characters = CharacterRepository(self.path)
        self.locations = LocationRepository(self.path)
        fields = {**copy.deepcopy(DEFAULTS), "name": "Projet test", "language": "fr-FR",
                  "text_provider": "openai", "visual_provider": "openai_image",
                  "voice_provider": "elevenlabs", "voice_name": "Voix du projet", "music_provider": "none"}
        self.project = self.projects.create(**fields)
        self.connector = ScriptedConnector(tmp_path / "storage")
        self.service = ProductionService(
            ProductionRepository(self.path), {"scripted": self.connector}, SyncExecutor(),
            price_book=lambda: PRICES, character_repo=self.characters, location_repo=self.locations,
        )

    def add_character(self, name="Personnage test", **overrides):
        fields = {"name": name, **CHARACTER_FIELDS, **overrides}
        return self.characters.create(self.project.id, **fields)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def _narrative(*characters) -> dict:
    return {"version": 4, "characters": list(characters), "location": None}


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
# -- script_mode() / _reference_character() : fonctions pures ------------------------------------------------------
# ======================================================================================================================
def test_no_character_selected_stays_in_narration_mode():
    assert script_mode(None) == {"mode": MODE_NARRATION, "character_name": "", "reason": "aucun personnage sélectionné"}
    assert script_mode({}) == {"mode": MODE_NARRATION, "character_name": "", "reason": "aucun personnage sélectionné"}


def test_single_character_without_primary_flag_triggers_dialogue_mode():
    narrative = _narrative({"name": "Personnage test", "is_primary": False})
    info = script_mode(narrative)
    assert info["mode"] == MODE_CHARACTER_DIALOGUE
    assert info["character_name"] == "Personnage test"
    assert "seul personnage sélectionné" in info["reason"]


def test_primary_character_among_several_selected_triggers_dialogue_mode_for_the_primary():
    narrative = _narrative({"name": "Personnage principal", "is_primary": True},
                           {"name": "Second personnage", "is_primary": False})
    info = script_mode(narrative)
    assert info["mode"] == MODE_CHARACTER_DIALOGUE
    assert info["character_name"] == "Personnage principal"
    assert "principal" in info["reason"]


def test_several_characters_without_a_unique_primary_stays_in_narration_mode():
    """#39 (multi-locuteurs) reste hors scope : ambiguïté jamais résolue arbitrairement, on reste en
    narration tant que ce ticket n'existe pas — exactement comme resolve_voice (#70) le fait déjà pour la
    voix."""
    narrative = _narrative({"name": "Personnage A", "is_primary": False}, {"name": "Personnage B", "is_primary": False})
    info = script_mode(narrative)
    assert info["mode"] == MODE_NARRATION
    assert info["character_name"] == ""
    assert "aucun principal unique" in info["reason"]

    both_primary = _narrative({"name": "Personnage A", "is_primary": True}, {"name": "Personnage B", "is_primary": True})
    assert script_mode(both_primary)["mode"] == MODE_NARRATION


def test_script_mode_and_resolve_voice_share_the_exact_same_reference_selection():
    """Garantie structurelle (jamais une coïncidence) : narration <=> voix du projet, dialogue <=> voix
    (ou repli) du personnage de référence — les deux fonctions ne peuvent jamais diverger sur QUI est le
    personnage de référence, car elles appellent la même ``_reference_character``."""
    cases = [
        _narrative(),
        _narrative({"name": "Solo", "is_primary": False}),
        _narrative({"name": "Principal", "is_primary": True}, {"name": "Autre", "is_primary": False}),
        _narrative({"name": "A", "is_primary": False}, {"name": "B", "is_primary": False}),
    ]
    project_voice = VoiceSpec(provider="elevenlabs", voice_id="PROJECT-VOICE-ID", name="Voix du projet", model="x")
    for narrative in cases:
        mode_info = script_mode(narrative)
        _, voice_origin = resolve_voice(project_voice, narrative)
        if mode_info["mode"] == MODE_NARRATION:
            assert voice_origin["source"] == "project"  # #77 : la narration n'est jamais implicitement
            # attribuée à la voix d'un personnage — repli projet garanti dans les deux cas à la fois.
        else:
            assert mode_info["character_name"] == voice_origin["character_name"]


def test_reference_character_helper_returns_none_with_the_exact_reason_strings_used_before_the_refactor():
    """Non-régression du refactor de #70 : les deux messages de repli de resolve_voice restent identiques
    mot pour mot (voir test_voice_resolution.py, qui les vérifie aussi côté intégration)."""
    assert _reference_character([]) == (None, "aucun personnage sélectionné")
    ambiguous = [{"name": "A", "is_primary": False}, {"name": "B", "is_primary": False}]
    assert _reference_character(ambiguous) == (None, "plusieurs personnages sélectionnés, aucun principal unique")


# ======================================================================================================================
# -- mpt_connector.script_prompt() : instruction de tête, fonction pure ---------------------------------------------
# ======================================================================================================================
def _plain_project(tmp_path):
    fields = {**copy.deepcopy(DEFAULTS), "name": "Projet test", "language": "fr-FR", "text_provider": "openai",
              "visual_provider": "openai_image", "voice_provider": "elevenlabs", "voice_name": "Voix du projet",
              "music_provider": "none"}
    return ProjectRepository(tmp_path / "lody.sqlite3").create(**fields)


def test_script_prompt_without_dialogue_character_is_byte_for_byte_identical_to_before_77(tmp_path):
    request = build_request(_plain_project(tmp_path), SUBJECT)
    baseline = mpt.script_prompt(request)
    assert mpt.script_prompt(request, dialogue_character="") == baseline
    assert "Écris uniquement le texte parlé de la narration" in baseline


def test_script_prompt_with_dialogue_character_swaps_the_head_instruction_only(tmp_path):
    request = build_request(_plain_project(tmp_path), SUBJECT)
    narration = mpt.script_prompt(request)
    dialogue = mpt.script_prompt(request, dialogue_character="Personnage test")
    assert "Écris uniquement le texte parlé de la narration" not in dialogue
    assert "Personnage test" in dialogue
    assert "à la première personne" in dialogue
    assert "pas de description de Personnage test à la troisième personne" in dialogue
    # Le reste (durée cible) est inchangé, seule la première ligne diffère :
    assert narration.splitlines()[1:] == dialogue.splitlines()[1:]


def test_script_prompt_dialogue_instruction_still_forbids_titles_scene_lists_and_stage_directions(tmp_path):
    dialogue = mpt.script_prompt(build_request(_plain_project(tmp_path), SUBJECT), dialogue_character="Personnage test")
    for forbidden in ("pas de titre", "pas d’indication de scène", "pas de liste", "pas de didascalie"):
        assert forbidden in dialogue


# ======================================================================================================================
# -- intégration (ProductionService, faux connecteur) ----------------------------------------------------------------
# ======================================================================================================================
def test_classic_video_without_any_character_is_unaffected(env):  # noqa: F811
    """Vidéo classique, sans personnage sélectionné : comportement et payload strictement identiques à
    avant #77 (narration, dialogue_character toujours vide transmis au connecteur)."""
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "EN_FILE"
    assert env.connector.dialogue_characters_received == [""]
    assert launched.trace["script_mode"] == {"mode": MODE_NARRATION, "character_name": "", "reason": "aucun personnage sélectionné"}


def test_single_selected_character_triggers_dialogue_mode_and_is_traced(env):  # noqa: F811
    character = env.add_character()
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[character.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert env.connector.dialogue_characters_received == ["Personnage test"]
    assert launched.trace["script_mode"]["mode"] == MODE_CHARACTER_DIALOGUE
    assert launched.trace["script_mode"]["character_name"] == "Personnage test"


def test_primary_character_among_several_is_the_one_used_for_dialogue(env):  # noqa: F811
    primary = env.add_character(name="Personnage principal", is_primary=True)
    second = env.add_character(name="Second personnage", is_primary=False)
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted",
                                character_ids=[primary.id, second.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert env.connector.dialogue_characters_received == ["Personnage principal"]
    assert launched.trace["script_mode"]["character_name"] == "Personnage principal"


def test_several_characters_without_unique_primary_stays_in_narration_end_to_end(env):  # noqa: F811
    a = env.add_character(name="Personnage A", is_primary=False)
    b = env.add_character(name="Personnage B", is_primary=False)
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[a.id, b.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert env.connector.dialogue_characters_received == [""]
    assert launched.trace["script_mode"]["mode"] == MODE_NARRATION


def test_manual_script_never_triggers_generation_but_mode_is_still_traced(env):  # noqa: F811
    """« Script manuel : aucune génération automatique » (#77) : write_script n'est jamais appelé, mais le
    mode reste tracé (#81) comme pour tout le reste du diagnostic."""
    character = env.add_character()
    manual_script = "Salut ! Aujourd'hui je vous raconte un truc trop cool sur la blockchain."
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=manual_script,
                                character_ids=[character.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert env.connector.dialogue_characters_received == []  # jamais appelé
    assert launched.script == manual_script
    assert launched.trace["script_mode"]["mode"] == MODE_CHARACTER_DIALOGUE  # tracé malgré tout (#81)


def test_manual_script_is_validated_by_the_same_guard_in_dialogue_mode_as_in_narration_mode(env):  # noqa: F811
    """Non-régression #76 : le garde-fou s'applique identiquement, qu'un personnage soit sélectionné ou
    non — aucun traitement de faveur en mode dialogue."""
    character = env.add_character()
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted",
                                script="Scène 1 : ouverture sur le personnage. Scène 2 : explication. Scène 3 : chute.",
                                character_ids=[character.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "ECHEC"
    assert "write_script" not in env.connector.calls  # script fourni : jamais d'appel d'écriture


@pytest.mark.parametrize("excluded_text", [
    "Épisode 0 — Lancement\n\nSalut, aujourd'hui on explore un sujet passionnant ensemble.",
    "Les visuels seront des images fixes uniquement, sans mouvement ni transition. Voici mon avis sur le sujet.",
    "Scène 1 : ouverture. Scène 2 : explication. Scène 3 : chute humoristique.",
    "Consignes visuelles : sans texte ni logo, à ne jamais montrer de marque réelle. On attaque le sujet.",
    "Ce texte dure 45 à 60 secondes au format 9:16 vertical, parfait pour les réseaux.",
])
def test_technical_visual_and_scene_instructions_are_excluded_from_spoken_text_in_dialogue_mode(env, excluded_text):  # noqa: F811
    """Consigne technique, instruction visuelle, indication de scène, durée/format : exclus du texte parlé
    même quand un personnage de référence est sélectionné (mode dialogue) — le garde-fou #76 ne dépend
    jamais du mode."""
    character = env.add_character()
    env.connector.script_text = excluded_text
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[character.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "ECHEC"
    assert "refusée" in launched.error_message or "refusé" in launched.error_message


def test_title_duration_format_and_project_metadata_excluded_from_spoken_text(env):  # noqa: F811
    character = env.add_character()
    env.connector.script_text = "Épisode 3 : la suite — durée 45 à 60 secondes, format 9:16."
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[character.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "ECHEC"


def test_valid_character_dialogue_passes_the_guard_and_reaches_the_engine(env):  # noqa: F811
    character = env.add_character()
    env.connector.script_text = ("Salut, c'est moi ! Franchement la blockchain c'est moins compliqué qu'on le "
                                "dit. Chaque bloc garde une trace que personne ne peut trafiquer en douce.")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[character.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "EN_FILE"
    assert launched.script == env.connector.script_text
    assert launched.trace["script_mode"]["mode"] == MODE_CHARACTER_DIALOGUE


def test_storyboard_keeps_visual_continuity_out_of_the_spoken_script(env):  # noqa: F811
    """Le storyboard garde ses informations utiles (prompts visuels) séparément du texte réellement destiné
    au TTS — ``production.script`` ne contient jamais de bloc de continuité visuelle (#37/#81)."""
    character = env.add_character()
    env.connector.script_text = ("Salut, c'est moi. Je me balade tranquille. On continue l'aventure ensemble, "
                                "ça va être énorme, je vous jure.")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[character.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert "Continuité visuelle" not in launched.script
    assert launched.storyboard  # les scènes existent toujours, avec leurs prompts à part


def test_no_secret_or_internal_path_in_the_script_mode_trace(env):  # noqa: F811
    character = env.add_character()
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[character.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert find_secret_path(str(launched.trace["script_mode"])) is None


def test_historical_production_without_script_mode_in_trace_reads_back_without_crashing():
    """Production antérieure à #77 : la clé "script_mode" n'existe pas dans ``trace`` — lire son absence ne
    doit jamais planter (comportement déjà garanti pour toutes les autres clés ajoutées par les tickets
    précédents, voir #69/#70/#75/#81)."""
    old = _fake_production(script="Un texte déjà écrit avant #77.", trace={}, snapshot={})
    assert old.trace.get("script_mode") is None  # absence tolérée, jamais une exception
    assert script_mode(old.snapshot.get("narrative_context")) == {
        "mode": MODE_NARRATION, "character_name": "", "reason": "aucun personnage sélectionné"}


def test_payload_video_script_never_contains_non_speakable_content_real_connector(tmp_path):
    """Bout-en-bout avec le VRAI connecteur (transport HTTP factice, jamais d'appel réel) : le
    ``video_script`` du payload final est exactement le texte validé par le garde-fou #76, écrit en mode
    dialogue, jamais le brief ni une consigne technique."""
    characters = CharacterRepository(tmp_path / "lody.sqlite3")
    locations = LocationRepository(tmp_path / "lody.sqlite3")
    projects = ProjectRepository(tmp_path / "lody.sqlite3")
    fields = {**copy.deepcopy(DEFAULTS), "name": "Projet test", "language": "fr-FR", "text_provider": "openai",
              "visual_provider": "openai_image", "voice_provider": "elevenlabs", "voice_name": "Voix du projet",
              "music_provider": "none",
              "settings": {"brief": {"voice_id": "PROJECT-VOICE-ID-TEST", "voice_model": "eleven_multilingual_v2"}}}
    project = projects.create(**fields)
    character = characters.create(project.id, name="Personnage test", **CHARACTER_FIELDS)

    dialogue_script = ("Salut, c'est moi qui vous parle directement. Franchement ce sujet est plus simple "
                       "qu'il n'y paraît, je vous explique ça tranquillement.")
    ping = (200, {})
    script_response = (200, {"status": 200, "data": {"video_script": dialogue_script}})
    videos_response = (200, {"status": 200, "data": {"task_id": TASK}})
    connector, transport = _connector(tmp_path, ping, script_response, videos_response, config=CONFIG)

    service = ProductionService(
        ProductionRepository(tmp_path / "lody.sqlite3"), {mpt.PROVIDER_ID: connector}, SyncExecutor(),
        price_book=lambda: PRICES, character_repo=characters, location_repo=locations,
    )
    draft = service.prepare(project, SUBJECT, provider_id=mpt.PROVIDER_ID, character_ids=[character.id])
    launched = service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "EN_FILE"

    scripts_call = next(call for call in transport.requests if call[1].endswith("/api/v1/scripts"))
    sent_prompt = scripts_call[2]["video_script_prompt"]
    assert "Personnage test" in sent_prompt and "à la première personne" in sent_prompt
    assert "Écris uniquement le texte parlé de la narration" not in sent_prompt

    videos_call = next(call for call in transport.requests if call[1].endswith("/api/v1/videos"))
    assert videos_call[2]["video_script"] == dialogue_script
    assert "Continuité visuelle" not in videos_call[2]["video_script"]
    assert find_secret_path(videos_call[2]["video_script"]) is None
