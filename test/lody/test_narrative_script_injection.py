"""Injection du contexte narratif (personnages + lieu du SNAPSHOT) dans le prompt d'écriture du script (#36).

La résolution et le figeage du snapshot lui-même sont couverts par test_narrative_context.py (#35) ; ce
fichier ne teste que l'injection dans le prompt : ``render_prompt_block`` (rendu pur depuis le snapshot),
``script_prompt`` (assemblage du prompt moteur), et l'intégration bout-en-bout via
``ProductionService.confirm()``/``run()`` avec un faux connecteur (aucun appel réseau, aucun appel payant).
"""

from __future__ import annotations

import copy

import pytest

from lody.generation.mpt_connector import script_prompt
from lody.generation.models import GenerationRequest
from lody.generation.narrative_context import NARRATIVE_BLOCK_MAX, render_prompt_block
from lody.generation.store import ProductionRepository
from lody.projects import DEFAULTS
from test.lody.test_narrative_context import Env, SUBJECT

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# -- render_prompt_block (fonction pure, depuis un snapshot déjà résolu) ------------------------------------------
def test_render_prompt_block_is_empty_without_any_selection():
    assert render_prompt_block({}) == ""


def test_render_prompt_block_ignores_a_missing_narrative_context_key():
    """Productions antérieures à #35 : la clé n'existe pas du tout (``.get`` renvoie ``None``), pas d'erreur."""
    assert render_prompt_block(None) == ""


def test_one_character_is_rendered_with_only_script_relevant_fields():
    narrative = {"version": 1, "characters": [{
        "id": "chr_technical_id", "name": "Léa", "role": "Guide", "personality": "Curieuse",
        "visual_description": "Manteau bleu à motifs", "speech_style": "Phrases courtes, tutoie l’audience",
        "permanent_elements": "Porte toujours un chapeau", "continuity_notes": "Toujours enthousiaste",
        "is_primary": True,
    }], "location": None}
    block = render_prompt_block(narrative)
    assert block.startswith("### Contexte narratif") and block.endswith("### Fin du contexte narratif ###")
    for expected in ("Léa", "Guide", "Curieuse", "Phrases courtes, tutoie l’audience", "Toujours enthousiaste"):
        assert expected in block
    # jamais les champs visuels, techniques, ou l'indicateur "principal" (hors périmètre script — voir #36)
    for forbidden in ("Manteau bleu", "chapeau", "chr_technical_id", "True"):
        assert forbidden not in block


def test_several_characters_are_all_rendered_in_the_snapshot_s_order():
    narrative = {"version": 1, "characters": [
        {"id": "1", "name": "Léa", "role": "Guide"},
        {"id": "2", "name": "Marc", "role": "Sceptique"},
        {"id": "3", "name": "Théo", "role": "Comique"},
    ], "location": None}
    block = render_prompt_block(narrative)
    assert "Léa" in block and "Marc" in block and "Théo" in block
    assert block.index("Léa") < block.index("Marc") < block.index("Théo")


def test_character_and_location_together_characters_come_before_the_location():
    narrative = {
        "version": 1, "characters": [{"id": "1", "name": "Léa", "role": "Guide"}],
        "location": {"id": "loc1", "name": "Atelier solaire", "location_type": "Intérieur",
                     "description": "Plein de panneaux", "continuity_notes": "Toujours rangé", "is_primary": False},
    }
    block = render_prompt_block(narrative)
    for expected in ("Léa", "Atelier solaire", "Intérieur", "Plein de panneaux", "Toujours rangé"):
        assert expected in block
    assert block.index("Léa") < block.index("Atelier solaire")


def test_location_only_without_any_character():
    narrative = {"version": 1, "characters": [],
                "location": {"id": "loc1", "name": "Atelier", "location_type": "", "description": "", "continuity_notes": ""}}
    block = render_prompt_block(narrative)
    assert "Atelier" in block and "Léa" not in block


def test_block_is_bounded_and_never_truncates_a_field_in_the_middle():
    long_field = "x" * 400
    narrative = {"version": 1, "characters": [
        {"id": str(i), "name": f"Perso{i}", "role": "Rôle", "personality": long_field} for i in range(6)
    ], "location": None}
    block = render_prompt_block(narrative)
    assert len(block) <= NARRATIVE_BLOCK_MAX + 100  # marge d'en-tête/pied ; jamais illimité malgré 6 personnages
    # aucune ligne ne contient une occurrence PARTIELLE du champ long (0 = absent, 400 = entier, rien entre les deux)
    assert all(line.count("x") in (0, 400) for line in block.splitlines())


def test_no_secret_or_technical_field_ever_appears_even_if_present_in_the_snapshot_dict():
    narrative = {"version": 1, "characters": [{
        "id": "chr_x", "name": "Léa", "voice_provider": "elevenlabs", "voice_name": "Kev",
        "external_voice_id": "abcdef123456", "is_primary": True,
        "visual_description": "secret visuel", "permanent_elements": "secret permanent",
    }], "location": {"id": "loc_x", "name": "Atelier", "is_primary": True}}
    block = render_prompt_block(narrative)
    for forbidden in ("elevenlabs", "Kev", "abcdef123456", "secret visuel", "secret permanent", "chr_x", "loc_x"):
        assert forbidden not in block


# -- script_prompt (mpt_connector) : assemblage complet du prompt moteur -------------------------------------------
def _request(**overrides) -> GenerationRequest:
    return GenerationRequest(subject="Sujet de test", **overrides)


def test_script_prompt_without_a_narrative_block_is_unchanged():
    baseline = script_prompt(_request())
    assert script_prompt(_request(), "") == baseline
    assert "Contexte narratif" not in baseline


def test_script_prompt_appends_the_narrative_block_after_everything_else():
    baseline = script_prompt(_request())
    block = render_prompt_block({"version": 1, "characters": [{"id": "1", "name": "Léa", "role": "Guide"}], "location": None})
    full = script_prompt(_request(), block)
    assert full.startswith(baseline)
    assert "Léa" in full and "### Fin du contexte narratif ###" in full


def test_script_prompt_stays_within_the_engine_limit_even_with_a_large_narrative_block():
    block = render_prompt_block({"version": 1, "characters": [
        {"id": str(i), "name": f"Perso{i}", "role": "R" * 60, "personality": "P" * 500} for i in range(10)
    ], "location": None})
    full = script_prompt(_request(instructions="C" * 2000), block, limit=2000)
    assert len(full) <= 2000


# -- intégration bout-en-bout (ProductionService.confirm/run, faux connecteur, aucun appel payant) -----------------
def test_no_choice_produces_the_exact_same_prompt_as_today(env):  # noqa: F811
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    env.service.confirm(draft.id, accept_partial=True)
    assert env.connector.narrative_blocks_received == [""]


def test_one_character_is_injected(env):  # noqa: F811
    lea = env.add_character(name="Léa", role="Guide", personality="Curieuse")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id])
    env.service.confirm(draft.id, accept_partial=True)
    block = env.connector.narrative_blocks_received[-1]
    assert "Léa" in block and "Guide" in block and "Curieuse" in block


def test_several_characters_are_all_injected(env):  # noqa: F811
    lea = env.add_character(name="Léa")
    marc = env.add_character(name="Marc")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id, marc.id])
    env.service.confirm(draft.id, accept_partial=True)
    block = env.connector.narrative_blocks_received[-1]
    assert "Léa" in block and "Marc" in block


def test_character_and_location_are_injected_together(env):  # noqa: F811
    lea = env.add_character(name="Léa")
    atelier = env.add_location(name="Atelier solaire")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id], location_id=atelier.id)
    env.service.confirm(draft.id, accept_partial=True)
    block = env.connector.narrative_blocks_received[-1]
    assert "Léa" in block and "Atelier solaire" in block


def test_snapshot_is_used_even_after_the_project_s_character_sheet_changes(env):  # noqa: F811
    lea = env.add_character(name="Léa", personality="Curieuse")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id])

    env.characters.update(env.project.id, lea.id, personality="Complètement différente")
    env.characters.deactivate(env.project.id, lea.id)  # même désactivée, la production déjà préparée est inchangée

    env.service.confirm(draft.id, accept_partial=True)
    block = env.connector.narrative_blocks_received[-1]
    assert "Curieuse" in block and "Complètement différente" not in block


def test_old_production_without_a_narrative_context_key_is_ignored_cleanly(env):  # noqa: F811
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    legacy_snapshot = copy.deepcopy(draft.snapshot)
    del legacy_snapshot["narrative_context"]
    assert env.service.repo.transition(draft.id, [draft.status], snapshot=legacy_snapshot)

    env.service.confirm(draft.id, accept_partial=True)
    assert env.connector.narrative_blocks_received == [""]
    assert env.connector.script_error is None  # aucune exception : le prompt est simplement identique à avant #36


def test_isolation_between_projects(env):  # noqa: F811
    lea = env.add_character(name="Léa")
    draft_a = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id])

    other_project = env.projects.create(**{**copy.deepcopy(DEFAULTS), "name": "Autre projet", "language": "fr-FR",
                                            "text_provider": "openai", "visual_provider": "openai_image",
                                            "voice_provider": "elevenlabs", "voice_name": "V", "music_provider": "elevenlabs"})
    marc = env.characters.create(other_project.id, name="Marc")
    draft_b = env.service.prepare(other_project, SUBJECT, provider_id="scripted", character_ids=[marc.id])

    env.service.confirm(draft_a.id, accept_partial=True)
    env.service.confirm(draft_b.id, accept_partial=True)

    first, second = env.connector.narrative_blocks_received
    assert "Léa" in first and "Marc" not in first
    assert "Marc" in second and "Léa" not in second


def test_no_secret_or_sensitive_data_reaches_the_prompt(env):  # noqa: F811
    lea = env.add_character(name="Léa", voice_provider="elevenlabs", voice_name="Kev", external_voice_id="abcdef123456")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id])
    env.service.confirm(draft.id, accept_partial=True)
    block = env.connector.narrative_blocks_received[-1]
    for forbidden in ("elevenlabs", "Kev", "abcdef123456", lea.id):
        assert forbidden not in block


def test_trace_records_the_injected_block_for_audit(env):  # noqa: F811
    lea = env.add_character(name="Léa", role="Guide")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[lea.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    stored = ProductionRepository(env.path).get(launched.id)
    assert "Léa" in stored.trace["script_request"]["video_script_prompt"]
    origins = {item["item"]: item["origin"] for item in stored.trace["origins"]}
    assert "instantané de production" in origins["Prompt éditorial du script"]


def test_a_provided_script_never_triggers_any_script_call_or_injection(env):  # noqa: F811
    """Sans appel d'écriture (script déjà fourni), aucune injection n'a de sens : write_script n'est jamais appelé."""
    lea = env.add_character(name="Léa")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=(
        "Un script assez long pour passer la validation minimale de quarante caractères ici."
    ), character_ids=[lea.id])
    env.service.confirm(draft.id, accept_partial=True)
    assert "write_script" not in env.connector.calls
    assert env.connector.narrative_blocks_received == []
