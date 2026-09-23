"""Injection du contexte narratif (personnages + lieu du SNAPSHOT) dans les prompts D'IMAGE du storyboard (#37).

La résolution et le figeage du snapshot sont couverts par test_narrative_context.py (#35), l'injection dans
le prompt du SCRIPT par test_narrative_script_injection.py (#36). Ce fichier ne teste que l'enrichissement
des prompts VISUELS : ``enrich_visual_prompts`` (fonction pure, sur un storyboard déjà construit) et
l'intégration bout-en-bout via ``ProductionService.confirm()``/``run()`` avec un faux connecteur (aucun
appel réseau, aucun appel payant).
"""

from __future__ import annotations

import copy

import pytest

from lody.generation.narrative_context import VISUAL_BLOCK_MAX, enrich_visual_prompts
from lody.generation.storyboard import Scene
from lody.generation.store import ProductionRepository
from lody.projects import DEFAULTS
from test.lody.test_narrative_context import Env, SUBJECT

MARKER = "### Continuité visuelle"


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# -- enrich_visual_prompts (fonction pure, sur un storyboard déjà construit) ---------------------------------------
def test_no_selection_leaves_scenes_strictly_untouched():
    scenes = [Scene(1, "Léa entre dans l’atelier.", "prompt original 1", 5.0),
             Scene(2, "Elle observe les panneaux.", "prompt original 2", 4.0)]
    result = enrich_visual_prompts(scenes, {})
    assert result == scenes
    assert [s.prompt for s in result] == ["prompt original 1", "prompt original 2"]


def test_missing_narrative_context_key_is_ignored_cleanly():
    """Productions antérieures à #35 : la clé n'existe pas du tout (``None``), pas d'erreur."""
    scenes = [Scene(1, "Un texte quelconque.", "prompt", 3.0)]
    assert enrich_visual_prompts(scenes, None) == scenes


def test_character_is_only_added_to_scenes_that_mention_them():
    narrative = {"characters": [{"id": "1", "name": "Léa", "visual_description": "Cheveux roux, veste jaune"}],
                "location": None}
    scenes = [Scene(1, "Léa observe le ciel étoilé.", "prompt 1", 5.0),
             Scene(2, "Le soleil se lève doucement.", "prompt 2", 4.0)]
    result = enrich_visual_prompts(scenes, narrative)
    assert MARKER in result[0].prompt and "Léa" in result[0].prompt and "Cheveux roux" in result[0].prompt
    assert MARKER not in result[1].prompt and "Léa" not in result[1].prompt  # jamais inventé si non mentionné


def test_several_characters_appear_in_the_snapshot_s_stable_order():
    narrative = {"characters": [
        {"id": "1", "name": "Léa", "visual_description": "Rousse"},
        {"id": "2", "name": "Marc", "visual_description": "Blond"},
    ], "location": None}
    scenes = [Scene(1, "Léa et Marc discutent ensemble.", "prompt", 5.0)]
    result = enrich_visual_prompts(scenes, narrative)
    block = result[0].prompt
    assert "Léa" in block and "Marc" in block
    assert block.index("Léa") < block.index("Marc")


def test_character_and_location_together():
    narrative = {"characters": [{"id": "1", "name": "Léa", "visual_description": "Rousse"}],
                "location": {"id": "l1", "name": "Atelier solaire", "location_type": "Intérieur",
                             "description": "Panneaux partout"}}
    scenes = [Scene(1, "Léa entre dans l’atelier solaire.", "prompt", 5.0)]
    result = enrich_visual_prompts(scenes, narrative)
    assert "Léa" in result[0].prompt and "Atelier solaire" in result[0].prompt


def test_location_only_applies_to_every_scene_even_without_being_named():
    narrative = {"characters": [], "location": {"id": "l1", "name": "Atelier solaire", "description": "Panneaux"}}
    scenes = [Scene(1, "Un texte qui ne nomme aucun lieu.", "prompt 1", 5.0),
             Scene(2, "Un autre texte neutre.", "prompt 2", 4.0)]
    result = enrich_visual_prompts(scenes, narrative)
    assert "Atelier solaire" in result[0].prompt and "Atelier solaire" in result[1].prompt


def test_repeated_mention_is_abbreviated_after_the_first_to_avoid_needless_repetition():
    narrative = {"characters": [{"id": "1", "name": "Léa", "visual_description": "Cheveux roux et veste jaune vif"}],
                "location": None}
    scenes = [Scene(1, "Léa sourit.", "p1", 3.0), Scene(2, "Léa repart au loin.", "p2", 3.0)]
    result = enrich_visual_prompts(scenes, narrative)
    assert "Cheveux roux et veste jaune vif" in result[0].prompt
    assert "Cheveux roux et veste jaune vif" not in result[1].prompt  # pas de répétition inutile du long bloc
    assert "scène 1" in result[1].prompt  # rappel court référençant la première apparition


def test_scene_count_order_and_narration_are_never_changed():
    narrative = {"characters": [{"id": "1", "name": "Léa"}], "location": {"id": "l1", "name": "Atelier"}}
    scenes = [Scene(1, "Léa parle.", "p1", 3.0), Scene(2, "Silence.", "p2", 2.0), Scene(3, "Léa repart.", "p3", 3.0)]
    result = enrich_visual_prompts(scenes, narrative)
    assert len(result) == 3
    assert [s.index for s in result] == [1, 2, 3]
    assert [s.narration for s in result] == [s.narration for s in scenes]
    assert [s.seconds for s in result] == [3.0, 2.0, 3.0]


def test_truncation_is_deterministic_and_never_cuts_a_field_in_the_middle():
    marker = "DESCRIPTIONVISUELLEMARQUEUR"  # motif distinctif, jamais un sous-mot du français
    long_field = marker * 20  # 560 caractères
    narrative = {"characters": [
        {"id": str(i), "name": f"Perso{i}", "visual_description": long_field} for i in range(5)
    ], "location": None}
    narration = " ".join(f"Perso{i}" for i in range(5)) + " sont réunis dans la même scène."
    scenes = [Scene(1, narration, "prompt", 5.0)]
    result = enrich_visual_prompts(scenes, narrative)
    block_addition = result[0].prompt[len("prompt"):]
    assert len(block_addition) <= VISUAL_BLOCK_MAX + 100  # marge d'en-tête/pied, jamais illimité
    assert all(line.count(marker) in (0, 20) for line in block_addition.splitlines())


def test_no_secret_technical_or_script_only_field_ever_appears():
    narrative = {"characters": [{
        "id": "chr_x", "name": "Léa", "voice_provider": "elevenlabs", "voice_name": "Kev",
        "external_voice_id": "abcdef123456", "role": "Guide", "personality": "Curieuse", "speech_style": "Douce",
        "visual_description": "Rousse", "is_primary": True,
    }], "location": {"id": "loc_x", "name": "Atelier", "is_primary": True}}
    scenes = [Scene(1, "Léa observe l’atelier.", "prompt", 5.0)]
    result = enrich_visual_prompts(scenes, narrative)
    block = result[0].prompt
    # rien de technique/vocal/secret, et rien des champs réservés au SCRIPT (#36, disjoints de #37)
    for forbidden in ("elevenlabs", "Kev", "abcdef123456", "chr_x", "loc_x", "Guide", "Curieuse", "Douce"):
        assert forbidden not in block


# -- intégration bout-en-bout (ProductionService.confirm/run, faux connecteur, aucun appel payant) -----------------
_SCRIPT_ONE_CHARACTER = (
    "Léa observe attentivement les panneaux solaires de l’atelier. "
    "Léa explique ensuite le fonctionnement du courant continu aux visiteurs. "
    "Léa termine la visite par un sourire chaleureux et rassurant."
)
_SCRIPT_TWO_CHARACTERS = (
    "Léa et Marc observent attentivement les panneaux solaires de l’atelier. "
    "Léa et Marc expliquent ensuite le fonctionnement du courant continu. "
    "Léa et Marc terminent la visite par un sourire chaleureux et rassurant."
)
_SCRIPT_NO_NAME_MENTIONED = (
    "Un narrateur explique calmement les bases de l’énergie renouvelable. "
    "Il poursuit avec quelques exemples concrets et rassurants pour continuer. "
    "Il conclut par un conseil pratique et clair pour la suite du parcours."
)


def _submitted_visual_prompts(env, draft):  # noqa: F811
    env.service.confirm(draft.id, accept_partial=True)
    return " ".join(env.connector.submitted[-1].visual_prompts)


def test_no_choice_produces_strictly_identical_visual_prompts(env):  # noqa: F811
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER)
    prompts = _submitted_visual_prompts(env, draft)
    assert MARKER not in prompts


def test_one_selected_character_mentioned_everywhere_is_injected(env):  # noqa: F811
    lea = env.add_character(name="Léa", visual_description="Cheveux roux, veste jaune",
                            reference_prompt="portrait studio, fond neutre")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[lea.id])
    prompts = _submitted_visual_prompts(env, draft)
    assert MARKER in prompts and "Léa" in prompts and "Cheveux roux" in prompts


def test_several_characters_are_injected_in_a_stable_order(env):  # noqa: F811
    lea = env.add_character(name="Léa", visual_description="Rousse")
    marc = env.add_character(name="Marc", visual_description="Blond")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_TWO_CHARACTERS,
                                character_ids=[lea.id, marc.id])
    prompts = _submitted_visual_prompts(env, draft)
    assert prompts.index("Léa") < prompts.index("Marc")


def test_character_and_location_together_end_to_end(env):  # noqa: F811
    lea = env.add_character(name="Léa", visual_description="Rousse")
    atelier = env.add_location(name="Atelier solaire", description="Panneaux partout")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[lea.id], location_id=atelier.id)
    prompts = _submitted_visual_prompts(env, draft)
    assert "Léa" in prompts and "Atelier solaire" in prompts


def test_location_only_is_injected_even_without_being_named(env):  # noqa: F811
    atelier = env.add_location(name="Atelier solaire", description="Panneaux partout")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_NO_NAME_MENTIONED,
                                location_id=atelier.id)
    env.service.confirm(draft.id, accept_partial=True)
    for prompt in env.connector.submitted[-1].visual_prompts:
        assert "Atelier solaire" in prompt  # dans TOUTES les scènes, pas seulement celles qui le nomment


def test_old_production_without_a_narrative_context_key_is_ignored_cleanly(env):  # noqa: F811
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER)
    legacy_snapshot = copy.deepcopy(draft.snapshot)
    del legacy_snapshot["narrative_context"]
    assert env.service.repo.transition(draft.id, [draft.status], snapshot=legacy_snapshot)
    prompts = _submitted_visual_prompts(env, draft)
    assert MARKER not in prompts


def test_snapshot_is_used_even_after_the_project_s_character_sheet_changes(env):  # noqa: F811
    lea = env.add_character(name="Léa", visual_description="Cheveux roux")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[lea.id])

    env.characters.update(env.project.id, lea.id, visual_description="Cheveux bleus complètement différents")
    env.characters.deactivate(env.project.id, lea.id)

    prompts = _submitted_visual_prompts(env, draft)
    assert "Cheveux roux" in prompts and "Cheveux bleus" not in prompts


def test_isolation_between_projects(env):  # noqa: F811
    lea = env.add_character(name="Léa", visual_description="Rousse")
    draft_a = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                  character_ids=[lea.id])

    other_project = env.projects.create(**{**copy.deepcopy(DEFAULTS), "name": "Autre projet", "language": "fr-FR",
                                            "text_provider": "openai", "visual_provider": "openai_image",
                                            "voice_provider": "elevenlabs", "voice_name": "V", "music_provider": "elevenlabs"})
    marc = env.characters.create(other_project.id, name="Marc", visual_description="Blond")
    marc_script = _SCRIPT_ONE_CHARACTER.replace("Léa", "Marc")
    draft_b = env.service.prepare(other_project, SUBJECT, provider_id="scripted", script=marc_script,
                                  character_ids=[marc.id])

    prompts_a = _submitted_visual_prompts(env, draft_a)
    prompts_b = _submitted_visual_prompts(env, draft_b)
    assert "Rousse" in prompts_a and "Blond" not in prompts_a
    assert "Blond" in prompts_b and "Rousse" not in prompts_b


def test_visual_injection_never_touches_the_script_or_the_voice(env):  # noqa: F811
    lea = env.add_character(name="Léa", visual_description="Cheveux roux, veste jaune",
                            role="Guide", personality="Curieuse")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[lea.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    stored = ProductionRepository(env.path).get(launched.id)
    assert stored.script == _SCRIPT_ONE_CHARACTER  # script fourni, jamais réécrit ni altéré par l'injection visuelle
    assert MARKER not in stored.script and "Cheveux roux" not in stored.script
    submitted = env.connector.submitted[-1]
    assert submitted.script == _SCRIPT_ONE_CHARACTER
    # voix strictement celle du projet, jamais touchée par la sélection de personnages/lieu (#37 hors périmètre voix)
    assert submitted.voice.provider == env.project.voice_provider and submitted.voice.name == env.project.voice_name


def test_scene_count_and_cost_are_unchanged_with_or_without_selection(env):  # noqa: F811
    without = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER)
    lea = env.add_character(name="Léa", visual_description="Rousse")
    with_selection = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                         character_ids=[lea.id])
    assert without.cost_low == with_selection.cost_low and without.cost_high == with_selection.cost_high
    assert without.cost_detail.get("scenes") == with_selection.cost_detail.get("scenes")


def test_trace_records_the_visual_continuity_for_audit(env):  # noqa: F811
    lea = env.add_character(name="Léa", visual_description="Cheveux roux")
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[lea.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    stored = ProductionRepository(env.path).get(launched.id)
    origins = {item["item"]: item["origin"] for item in stored.trace["origins"]}
    assert "instantané de production" in origins["Prompts de scène (envoyés par Lody)"]
