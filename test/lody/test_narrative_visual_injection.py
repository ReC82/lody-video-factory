"""Injection du contexte narratif (personnages + lieu du SNAPSHOT) dans les prompts D'IMAGE du storyboard (#37).

La résolution et le figeage du snapshot sont couverts par test_narrative_context.py (#35), l'injection dans
le prompt du SCRIPT par test_narrative_script_injection.py (#36). Ce fichier ne teste que l'enrichissement
des prompts VISUELS : ``enrich_visual_prompts`` (fonction pure, sur un storyboard déjà construit) et
l'intégration bout-en-bout via ``ProductionService.confirm()``/``run()`` avec un faux connecteur (aucun
appel réseau, aucun appel payant).

Depuis le diagnostic du ticket #92 : ``always_present_character_id``/le paramètre ``always_present_id``
d'``enrich_visual_prompts`` garantissent la présence du personnage de référence du mode dialogue (#77) dans
CHAQUE scène, même quand son nom n'est jamais prononcé dans sa propre réplique (cause établie du défaut
« Eli change de vêtements et de couleur des yeux entre les images » — voir le docstring de
``always_present_character_id``). Les tests dédiés plus bas utilisent systématiquement DEUX personnages aux
traits différents (jamais un seul cas particulier) pour vérifier que le correctif dépend des données
(id, description) et non d'un nom ou d'une caractéristique codée en dur.
"""

from __future__ import annotations

import copy

import pytest

from lody.generation.narrative_context import VISUAL_BLOCK_MAX, always_present_character_id, enrich_visual_prompts
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


def test_a_single_richly_filled_character_and_location_are_not_silently_dropped():
    """Non-régression #92 (essai réel) : avant correctif, VISUAL_BLOCK_MAX=500 était plus petit que la
    ligne d'UN SEUL personnage normalement rempli (chaque champ pouvant approcher sa propre limite de 500
    caractères, voir characters.py) — ``_bounded_block`` abandonnait alors la ligne ENTIÈRE, et la
    continuité visuelle disparaissait silencieusement dès la toute première scène, sans même atteindre le
    lieu. Tailles ci-dessous mesurées sur une configuration RÉELLE (projet de test, personnage seul +
    lieu) : jamais un cas artificiellement petit qui masquerait le bug."""
    character = {
        "id": "1", "name": "Nova",
        "visual_description": "D" * 308, "reference_prompt": "R" * 349,
        "permanent_elements": "P" * 195, "continuity_notes": "C" * 255,
    }
    location = {
        "id": "l1", "name": "Avant-poste", "location_type": "T" * 60,
        "description": "E" * 292, "reference_prompt": "F" * 430, "continuity_notes": "G" * 348,
    }
    narrative = {"characters": [character], "location": location}
    scenes = [Scene(1, "Nova arrive sur place.", "prompt", 5.0)]
    result = enrich_visual_prompts(scenes, narrative)
    block = result[0].prompt
    assert MARKER in block
    assert "D" * 308 in block  # description visuelle du personnage réellement présente, pas juste le marqueur
    assert "E" * 292 in block  # description du lieu réellement présente
    assert "(continuité tronquée" not in block  # tient dans le budget : rien n'est silencieusement perdu


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


def test_repeated_mention_gets_the_full_description_every_time_not_a_stale_backreference():
    """Cause établie (#92, essai réel) : chaque appel d'image est un appel réseau indépendant et SANS ÉTAT
    (aucun connecteur ne transmet de référence visuelle ni de mémoire de scène à scène). Un rappel du type
    « déjà décrit·e à la scène 1 » ne transmettait donc RIEN d'utile au fournisseur — il n'a jamais eu accès
    à la scène 1. La description complète doit apparaître à CHAQUE scène où le personnage est présent."""
    narrative = {"characters": [{"id": "1", "name": "Léa", "visual_description": "Cheveux roux et veste jaune vif"}],
                "location": None}
    scenes = [Scene(1, "Léa sourit.", "p1", 3.0), Scene(2, "Léa repart au loin.", "p2", 3.0)]
    result = enrich_visual_prompts(scenes, narrative)
    assert "Cheveux roux et veste jaune vif" in result[0].prompt
    assert "Cheveux roux et veste jaune vif" in result[1].prompt  # jamais un simple rappel sans contenu
    assert "déjà décrit" not in result[1].prompt  # plus de rappel qui ne transmet rien à un fournisseur sans état


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
    # 10 personnages (jamais 1 seul) : même à VISUAL_BLOCK_MAX=3000 (#92, dimensionné sur un SEUL personnage
    # réellement rempli), un nombre suffisant de personnages très détaillés continue de tronquer — la
    # troncature reste un cas réel à couvrir, pas un vestige de l'ancienne borne à 500.
    narrative = {"characters": [
        {"id": str(i), "name": f"Perso{i}", "visual_description": long_field} for i in range(10)
    ], "location": None}
    narration = " ".join(f"Perso{i}" for i in range(10)) + " sont réunis dans la même scène."
    scenes = [Scene(1, narration, "prompt", 5.0)]
    result = enrich_visual_prompts(scenes, narrative)
    block_addition = result[0].prompt[len("prompt"):]
    assert len(block_addition) <= VISUAL_BLOCK_MAX + 100  # marge d'en-tête/pied, jamais illimité
    assert "(continuité tronquée" in block_addition  # la troncature se déclenche bien, pas un test devenu vide
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


# ======================================================================================================================
# -- #92 : personnage de référence du mode dialogue toujours présent, même sans jamais prononcer son nom -----------
# ======================================================================================================================
# Deux personnages, DEUX JEUX DE TRAITS complètement différents et arbitraires (jamais une seule caractéristique
# d'un cas particulier) : si le correctif dépendait d'un nom ou d'un trait précis, un seul des deux échouerait.
_TRAITS_A = {"name": "Nova", "visual_description": "Cheveux violets en pics, combinaison argentée, yeux ambrés.",
            "permanent_elements": "Combinaison argentée et gants métalliques.", "continuity_notes": "Ne jamais changer la couleur des cheveux."}
_TRAITS_B = {"name": "Theo", "visual_description": "Crâne rasé, long manteau kaki élimé, cicatrice sur la joue gauche.",
            "permanent_elements": "Manteau kaki et cicatrice visible.", "continuity_notes": "La cicatrice doit rester visible à chaque plan."}

# Réplique à la première personne qui NE PRONONCE JAMAIS le nom du personnage qui parle (exactement la
# situation réelle diagnostiquée en #92 : le mode dialogue, #77, écrit toujours à la première personne).
# Assez de mots pour que l'estimation de scènes (durée du script / rythme) en produise PLUSIEURS, afin de
# vérifier la continuité sur un vrai storyboard à plusieurs scènes, pas un seul bloc générique.
_FIRST_PERSON_NO_SELF_NAME = (
    "Attends, tu sautes encore devant moi, juste là, sans rien dire du tout ? "
    "Bonjour, est-ce que tu m’entends vraiment, ou bien tu regardes ailleurs ? "
    "Je reste calme pour l’instant, mais ça commence sérieusement à devenir étrange. "
    "Est-ce que tu sais seulement marcher normalement, ou c’est un jeu entre nous deux ? "
    "Franchement, je commence vraiment à me poser des questions sur tout ce qui se passe ici depuis ce matin. "
    "Personne ne répond jamais à mes questions les plus simples, et ça devient franchement inquiétant pour moi."
)


def test_always_present_character_id_is_empty_without_any_reference_character():
    assert always_present_character_id(None) == ""
    assert always_present_character_id({}) == ""
    assert always_present_character_id({"characters": []}) == ""


def test_always_present_character_id_resolves_the_sole_selected_character():
    narrative = {"characters": [{"id": "chr_a", **_TRAITS_A}]}
    assert always_present_character_id(narrative) == "chr_a"


def test_always_present_character_id_resolves_the_unique_primary_among_several():
    narrative = {"characters": [{"id": "chr_a", "is_primary": True, **_TRAITS_A},
                                {"id": "chr_b", "is_primary": False, **_TRAITS_B}]}
    assert always_present_character_id(narrative) == "chr_a"


def test_always_present_character_id_is_empty_with_several_characters_and_no_unique_primary():
    """Ambiguïté volontairement non résolue (#39, multi-locuteurs hors scope) : jamais une présence forcée
    arbitraire quand aucun personnage de référence unique n'est déterminable."""
    narrative = {"characters": [{"id": "chr_a", "is_primary": False, **_TRAITS_A},
                                {"id": "chr_b", "is_primary": False, **_TRAITS_B}]}
    assert always_present_character_id(narrative) == ""


@pytest.mark.parametrize("traits", [_TRAITS_A, _TRAITS_B], ids=["traits-A", "traits-B"])
def test_enrich_visual_prompts_injects_the_always_present_character_even_when_never_named(traits):
    """Le cœur du correctif #92, vérifié avec DEUX jeux de traits différents (paramétré) : si le mécanisme
    dépendait d'un nom ou d'une caractéristique précise plutôt que de l'id transmis, un des deux échouerait."""
    character = {"id": "chr_ref", **traits}
    narrative = {"characters": [character], "location": None}
    scenes = [Scene(1, "Toi, devant mon stand, tu sautes. Encore. Bonjour ?", "prompt 1", 4.0),
             Scene(2, "Je reste calme.", "prompt 2", 3.0)]
    # Sans le correctif (always_present_id="") : le nom n'étant jamais prononcé, AUCUNE scène ne recevrait le bloc.
    unfixed = enrich_visual_prompts(scenes, narrative, always_present_id="")
    assert all(MARKER not in s.prompt for s in unfixed)

    fixed = enrich_visual_prompts(scenes, narrative, always_present_id="chr_ref")
    assert all(MARKER in s.prompt for s in fixed)
    assert all(traits["visual_description"] in s.prompt or "déjà décrit" in s.prompt for s in fixed)
    assert traits["visual_description"] in fixed[0].prompt  # description complète à la première apparition


def test_enrich_visual_prompts_still_only_adds_a_non_reference_character_where_actually_mentioned():
    """La présence forcée ne s'applique QU'au personnage de référence transmis : un autre personnage
    sélectionné (mode narration, pas de personnage de référence unique ici) reste soumis à la détection par
    mention — jamais introduit dans une scène où il est absent (exigence explicite du ticket #92)."""
    nova = {"id": "chr_a", **_TRAITS_A}
    theo = {"id": "chr_b", **_TRAITS_B}
    narrative = {"characters": [nova, theo], "location": None}
    scenes = [Scene(1, "Nova observe la scène en silence.", "prompt 1", 4.0),
             Scene(2, "Un trampoline apparaît sans aucune raison.", "prompt 2", 3.0)]
    # Aucun personnage de référence unique (deux personnages, aucun principal) : always_present_id="".
    result = enrich_visual_prompts(scenes, narrative, always_present_id="")
    assert "Nova" in result[0].prompt and _TRAITS_A["visual_description"] in result[0].prompt
    assert "Theo" not in result[0].prompt and _TRAITS_B["visual_description"] not in result[0].prompt
    assert MARKER not in result[1].prompt  # ni Nova ni Theo ne sont mentionnés : rien n'est inventé


def test_enrich_visual_prompts_ignores_an_always_present_id_absent_from_the_character_list():
    """Identifiant orphelin (ex. incohérence de données) : aucun crash, aucune présence forcée inventée —
    se comporte exactement comme ``always_present_id=""``."""
    narrative = {"characters": [{"id": "chr_a", **_TRAITS_A}], "location": None}
    scenes = [Scene(1, "Un texte qui ne nomme personne.", "prompt", 4.0)]
    result = enrich_visual_prompts(scenes, narrative, always_present_id="chr_does_not_exist")
    assert MARKER not in result[0].prompt


# -- intégration bout-en-bout : script de dialogue réaliste qui ne prononce jamais le nom du personnage ------------
@pytest.mark.parametrize("traits", [_TRAITS_A, _TRAITS_B], ids=["traits-A", "traits-B"])
def test_end_to_end_dialogue_mode_keeps_visual_continuity_in_every_scene_without_self_naming(env, traits):  # noqa: F811
    """Reproduction fidèle du scénario réel du ticket #92 : un seul personnage sélectionné (donc mode
    dialogue, #77), un script à la première personne qui ne mentionne jamais son propre nom. AVANT le
    correctif, aucune scène ne recevait la continuité visuelle de ce personnage — son apparence (couleur des
    yeux, tenue...) n'était donc jamais transmise au générateur d'images, quelle que soit la scène."""
    character = env.add_character(**traits)
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_FIRST_PERSON_NO_SELF_NAME,
                                character_ids=[character.id])
    env.service.confirm(draft.id, accept_partial=True)
    submitted_prompts = env.connector.submitted[-1].visual_prompts
    assert len(submitted_prompts) >= 2  # plusieurs scènes réellement distinctes (pas un seul bloc générique)
    for prompt in submitted_prompts:
        assert MARKER in prompt
        assert traits["visual_description"] in prompt or "déjà décrit" in prompt
    # L'action/le cadrage varient bien scène par scène : les prompts ne sont pas identiques entre eux.
    assert len(set(submitted_prompts)) == len(submitted_prompts)


def test_end_to_end_two_characters_no_primary_never_introduces_either_into_an_unrelated_scene(env):  # noqa: F811
    """Garde-fou de non-régression : avec plusieurs personnages sans principal unique (mode narration,
    aucune présence forcée), une scène qui ne nomme ni l'un ni l'autre ne doit jamais en recevoir un."""
    nova = env.add_character(**_TRAITS_A)
    theo = env.add_character(**_TRAITS_B)
    # Assez de mots PAR PHRASE pour que l'estimation de scènes en produise une par phrase (vérifié : 66 mots,
    # 3 phrases => 3 scènes, une phrase par scène) — sinon la phrase neutre du milieu pourrait être regroupée
    # avec une phrase qui nomme un personnage, et le test ne prouverait plus rien.
    script = (
        "Nova avance très prudemment le long de ce couloir désert et parfaitement silencieux, l'oreille aux "
        "aguets, prête à réagir au moindre petit signe suspect. "
        "Un bruit sourd et particulièrement inquiétant résonne soudain au loin, sans la moindre explication "
        "valable pour l'instant présent dans ce couloir. "
        "Theo apparaît enfin tout au bout du couloir, visiblement soulagé de retrouver Nova parfaitement "
        "saine et sauve après cette frayeur inattendue."
    )
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=script,
                                character_ids=[nova.id, theo.id])
    env.service.confirm(draft.id, accept_partial=True)
    submitted_prompts = env.connector.submitted[-1].visual_prompts
    # La scène du milieu ne nomme ni Nova ni Theo : elle ne doit recevoir aucun des deux.
    middle = next(p for p in submitted_prompts if "bruit sourd" in p)
    assert "Nova" not in middle and "Theo" not in middle
    assert _TRAITS_A["visual_description"] not in middle and _TRAITS_B["visual_description"] not in middle


# ======================================================================================================================
# -- #92 : transmission RÉELLE d'une image de référence persistante, quand le fournisseur la supporte -------------
# ======================================================================================================================
@pytest.fixture(autouse=True)
def _isolated_reference_storage_for_upload_tests(tmp_path, monkeypatch):
    """Isole le stockage des images de référence (#38) — nécessaire ici car ces tests, contrairement au
    reste du fichier, appellent réellement ``CharacterRepository.set_reference_image``."""
    monkeypatch.setenv("LODY_DATA_DIR", str(tmp_path / "ref_storage_upload"))


def _png_bytes() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color=(200, 100, 50)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_reference_image_is_uploaded_and_transmitted_when_the_provider_supports_it(env):  # noqa: F811
    env.connector.supports_reference_images = True
    reference_bytes = _png_bytes()
    nova = env.add_character(**_TRAITS_A)
    env.characters.set_reference_image(env.project.id, nova.id, reference_bytes)
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[nova.id])
    env.service.confirm(draft.id, accept_partial=True)

    assert env.connector.reference_images_received == [(reference_bytes, env.connector.submitted[-1].image_asset_scope)]
    assert env.connector.submitted[-1].character_reference_asset_id == env.connector.reference_asset_id
    assert len(env.connector.submitted[-1].image_asset_scope) == 64  # jeton opaque, haute entropie


def test_reference_image_is_never_transmitted_when_the_provider_does_not_support_it(env):  # noqa: F811
    """supports_reference_images reste False par défaut (ScriptedConnector) : même avec une image de
    référence configurée, rien n'est jamais uploadé ni prétendu transmis — repli honnête sur le texte seul."""
    assert env.connector.supports_reference_images is False
    nova = env.add_character(**_TRAITS_A)
    env.characters.set_reference_image(env.project.id, nova.id, _png_bytes())
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[nova.id])
    env.service.confirm(draft.id, accept_partial=True)

    assert env.connector.reference_images_received == []
    assert env.connector.submitted[-1].character_reference_asset_id == ""


def test_character_without_a_reference_image_never_triggers_an_upload(env):  # noqa: F811
    env.connector.supports_reference_images = True
    nova = env.add_character(**_TRAITS_A)  # jamais de set_reference_image : aucune référence configurée
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[nova.id])
    env.service.confirm(draft.id, accept_partial=True)

    assert "upload_reference_image" not in env.connector.calls
    assert env.connector.submitted[-1].character_reference_asset_id == ""


def test_upload_failure_degrades_to_text_only_and_records_a_warning_never_fails_the_production(env):  # noqa: F811
    """Un échec d'upload ne doit jamais faire échouer toute la production (la description textuelle
    complète suffit déjà, #99) — mais doit être honnêtement signalé, jamais silencieux."""
    from lody.generation.models import ErrorKind, ProviderError

    env.connector.supports_reference_images = True
    env.connector.reference_upload_error = ProviderError(ErrorKind.REJECTED, "upload refused")
    nova = env.add_character(**_TRAITS_A)
    env.characters.set_reference_image(env.project.id, nova.id, _png_bytes())
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[nova.id])
    launched = env.service.confirm(draft.id, accept_partial=True)

    from lody.generation.store import ProductionRepository as PR

    stored = PR(env.path).get(launched.id)
    assert stored.status.value != "ECHEC"  # jamais un échec de toute la production pour ce seul motif
    assert env.connector.submitted[-1].character_reference_asset_id == ""  # jamais prétendu transmis
    assert any("référence" in warning.lower() for warning in stored.warnings)


def test_multiple_characters_no_unique_primary_never_uploads_any_reference(env):  # noqa: F811
    """Mode narration (ambiguïté non résolue, #39 hors scope) : pas de personnage de référence unique,
    donc aucune référence visuelle n'est jamais transmise (MVP mono-référence, cohérent avec la voix #70)."""
    env.connector.supports_reference_images = True
    nova = env.add_character(**_TRAITS_A)
    theo = env.add_character(**_TRAITS_B)
    env.characters.set_reference_image(env.project.id, nova.id, _png_bytes())
    env.characters.set_reference_image(env.project.id, theo.id, _png_bytes())
    script = (
        "Nova avance très prudemment le long de ce couloir désert et parfaitement silencieux, l'oreille aux "
        "aguets, prête à réagir au moindre petit signe suspect. "
        "Theo apparaît enfin tout au bout du couloir, visiblement soulagé de retrouver Nova parfaitement "
        "saine et sauve après cette frayeur inattendue."
    )
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=script,
                                character_ids=[nova.id, theo.id])
    env.service.confirm(draft.id, accept_partial=True)

    assert env.connector.reference_images_received == []
    assert env.connector.submitted[-1].character_reference_asset_id == ""


def test_trace_records_whether_the_reference_was_actually_used(env):  # noqa: F811
    env.connector.supports_reference_images = True
    nova = env.add_character(**_TRAITS_A)
    env.characters.set_reference_image(env.project.id, nova.id, _png_bytes())
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=_SCRIPT_ONE_CHARACTER,
                                character_ids=[nova.id])
    launched = env.service.confirm(draft.id, accept_partial=True)

    from lody.generation.store import ProductionRepository as PR

    stored = PR(env.path).get(launched.id)
    assert stored.trace["reference_image"]["used"] is True
    assert stored.trace["reference_image"]["character_name"] == "Nova"
