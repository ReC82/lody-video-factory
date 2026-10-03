"""Indications scéniques {...} dans le storyboard (#92) : séparées du texte prononcé.

Cause établie sur un essai réel : sans canal distinct pour la pose/l'action d'une scène, la seule façon
d'influencer le prompt d'image était d'écrire l'action DANS le dialogue lui-même (« Je croise les bras... »)
— ce que le TTS lisait alors mot pour mot. Une accolade ``{pose ou action}`` placée juste avant une réplique
l'annote pour l'image SANS jamais rejoindre le texte prononcé ni les sous-titres (``Scene.narration`` reste
le texte propre, utilisé pour le script envoyé au moteur).

Fonctions pures, aucun appel réseau.
"""

from __future__ import annotations

from lody.generation import storyboard
from lody.generation.storyboard import Scene, build_storyboard, split_sentences_with_actions, strip_action_tags


# -- strip_action_tags -------------------------------------------------------------------------------
def test_strip_action_tags_removes_braces_and_keeps_the_rest():
    assert strip_action_tags("{bras croisés} Encore toi ?") == "Encore toi ?"
    assert strip_action_tags("Phrase sans accolade.") == "Phrase sans accolade."
    assert strip_action_tags("") == ""
    assert strip_action_tags("Avant. {pensif} Après.") == "Avant. Après."


# -- split_sentences_with_actions --------------------------------------------------------------------
def test_split_sentences_with_actions_matches_split_sentences_without_any_tag():
    script = "Première phrase. Deuxième phrase ! Troisième phrase ?"
    pairs = split_sentences_with_actions(script)
    assert [text for _, text in pairs] == storyboard.split_sentences(script)
    assert all(action == "" for action, _ in pairs)  # rétrocompatibilité : jamais d'action inventée


def test_split_sentences_with_actions_attaches_the_tag_to_the_following_sentence_only():
    script = "Encore toi ? {bras croisés} Je ne comprends pas. Je repars."
    pairs = split_sentences_with_actions(script)
    assert pairs == [
        ("", "Encore toi ?"),
        ("bras croisés", "Je ne comprends pas."),
        ("", "Je repars."),
    ]


def test_split_sentences_with_actions_joins_several_consecutive_tags():
    script = "{penché} {l'air agacé} Tu sautes encore ?"
    pairs = split_sentences_with_actions(script)
    assert pairs == [("penché l'air agacé", "Tu sautes encore ?")]


def test_split_sentences_with_actions_ignores_a_tag_with_no_following_sentence():
    script = "Je pars. {regarde au loin}"
    pairs = split_sentences_with_actions(script)
    assert pairs == [("", "Je pars.")]  # l'indication scénique seule ne crée jamais de réplique fantôme


def test_action_text_never_leaks_into_the_sentence_text():
    script = "{bras croisés} J'attends une réponse."
    _, text = split_sentences_with_actions(script)[0]
    assert "bras croisés" not in text
    assert "{" not in text and "}" not in text


# -- build_storyboard : Scene.action et Scene.narration -----------------------------------------------
def test_build_storyboard_separates_action_from_narration():
    script = "{bras croisés} Encore toi ? Je ne comprends pas."
    scenes = build_storyboard(script, 1)
    assert len(scenes) == 1
    assert scenes[0].action == "bras croisés"
    assert "bras croisés" not in scenes[0].narration
    assert scenes[0].narration == "Encore toi ? Je ne comprends pas."


def test_build_storyboard_without_any_tag_leaves_action_empty_and_behaviour_unchanged():
    script = "Première phrase. Deuxième phrase. Troisième phrase."
    scenes = build_storyboard(script, 3)
    assert all(scene.action == "" for scene in scenes)
    assert [scene.narration for scene in scenes] == storyboard.split_sentences(script)


def test_build_storyboard_groups_actions_the_same_way_as_sentences():
    """Plusieurs phrases regroupées dans UNE scène (scene_count < nombre de phrases) : les actions de
    chaque phrase du groupe sont conservées, jamais perdues ni mélangées avec un autre groupe."""
    script = "{pose A} Phrase un très longue pour peser dans le groupe. {pose B} Phrase deux également longue pour peser pareil."
    scenes = build_storyboard(script, 1)  # un seul groupe : les deux phrases, donc les deux poses
    assert len(scenes) == 1
    assert "pose A" in scenes[0].action and "pose B" in scenes[0].action


def test_build_storyboard_prompt_includes_the_action_sentence_only_when_present():
    with_action = build_storyboard("{bras croisés} Encore toi ?", 1)[0]
    without_action = build_storyboard("Encore toi ?", 1)[0]
    assert "Pose/action de cette scène : bras croisés." in with_action.prompt
    assert "Pose/action de cette scène" not in without_action.prompt


def test_scene_to_dict_includes_action():
    scene = Scene(1, "Texte.", "prompt", 3.0, "assis")
    assert scene.to_dict()["action"] == "assis"
    assert Scene(1, "Texte.", "prompt", 3.0).to_dict()["action"] == ""
