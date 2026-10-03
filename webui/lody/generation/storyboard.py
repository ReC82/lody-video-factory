"""Storyboard déterministe : découpe le script en scènes et écrit un prompt visuel explicite par scène.

Pas d'appel externe : le résultat dépend uniquement du script, du style et du nombre de scènes,
donc il est reproductible, testable, et identique à l'estimation affichée avant confirmation.
Les prompts sont *dérivés* du script (pas rédigés par un modèle) : ils sont conservés et visibles.

Indications scéniques (#92) : une accolade ``{pose ou action}`` placée juste avant une réplique annote
CETTE réplique pour l'image — jamais un texte à prononcer. ``split_sentences_with_actions`` les extrait et
les retire du texte ; ``strip_action_tags`` donne le texte propre (sans accolades) destiné au TTS et aux
sous-titres. Syntaxe volontairement distincte des balises de pause du moteur (``[pause]``/``(pause)``,
voir ``app/utils/utils.py:PAUSE_TAG_PATTERN``) : aucune collision possible. Entièrement rétrocompatible —
un script sans accolades se comporte exactement comme avant (``action`` toujours vide).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lody.generation.provider import WORDS_PER_MINUTE

_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")
MAX_EXCERPT = 240
MAX_PROMPT = 1600
MAX_SCENES = 30
_PAUSE_TAG = re.compile(r"\[[^\]]{1,20}\]|<[^>]{1,20}>")
_ACTION_TAG = re.compile(r"\{([^{}]{1,160})\}")
_ACTION_MARKER = re.compile(r"\x01(\d+)\x01")


@dataclass(frozen=True)
class Scene:
    index: int
    narration: str
    prompt: str
    seconds: float
    action: str = ""  # indication scénique (pose/cadrage), jamais prononcée — voir le docstring du module

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "narration": self.narration, "prompt": self.prompt, "seconds": self.seconds,
                "action": self.action}


def split_sentences(script: str) -> list[str]:
    cleaned = _PAUSE_TAG.sub(" ", script or "")
    return [" ".join(part.split()) for part in _SENTENCE.split(cleaned) if part and part.strip()]


def _mark_actions(script: str) -> tuple[str, dict[int, str]]:
    """Remplace chaque ``{...}`` par un marqueur opaque (jamais un caractère imprimable, pour ne jamais
    collisionner avec le texte réel) et renvoie (texte marqué, {index: action})."""
    actions: dict[int, str] = {}

    def _replace(match: re.Match[str]) -> str:
        index = len(actions)
        actions[index] = " ".join(match.group(1).split())
        return f"\x01{index}\x01"

    return _ACTION_TAG.sub(_replace, script or ""), actions


def strip_action_tags(script: str) -> str:
    """Texte propre, sans aucune indication scénique ``{...}`` — c'est CE texte qui doit être prononcé par
    le TTS et affiché en sous-titre, jamais le script brut (#92)."""
    marked, _ = _mark_actions(script)
    return " ".join(_ACTION_MARKER.sub(" ", marked).split())


def split_sentences_with_actions(script: str) -> list[tuple[str, str]]:
    """(action, phrase) pour chaque phrase, dans l'ordre. ``action`` = indication(s) scénique(s) placées
    juste avant cette phrase (concaténées si plusieurs), retirées du texte ; ``""`` si aucune. Une phrase
    réduite à une seule accolade (aucun mot prononcé) est ignorée : une indication scénique ne crée jamais
    une « réplique » fantôme.

    Scindé du texte propre EXACTEMENT comme ``split_sentences`` (même nettoyage ``_PAUSE_TAG``, même
    regex de découpe) : un script sans accolade produit les MÊMES phrases, dans le même ordre."""
    marked, actions = _mark_actions(script)
    cleaned = _PAUSE_TAG.sub(" ", marked)
    pairs: list[tuple[str, str]] = []
    for piece in _SENTENCE.split(cleaned):
        if not piece or not piece.strip():
            continue
        found = _ACTION_MARKER.findall(piece)
        text = " ".join(_ACTION_MARKER.sub(" ", piece).split())
        if not text:
            continue
        action = " ".join(dict.fromkeys(actions[int(i)] for i in found if int(i) in actions))
        pairs.append((action, text))
    return pairs


def _group(sentences: list[str], count: int) -> list[list[str]]:
    """Regroupe des phrases contiguës en ``count`` groupes de longueur (mots) proche."""
    count = max(1, min(count, len(sentences)))
    total = sum(len(sentence.split()) for sentence in sentences)
    target = total / count
    groups: list[list[str]] = [[]]
    running = 0.0
    for position, sentence in enumerate(sentences):
        remaining_sentences = len(sentences) - position
        remaining_groups = count - len(groups)
        if groups[-1] and remaining_groups > 0 and (
            running >= target * len(groups) or remaining_sentences <= remaining_groups
        ):
            groups.append([])
        groups[-1].append(sentence)
        running += len(sentence.split())
    return groups


def _group_actions(actions: list[str], group_sizes: list[int]) -> list[str]:
    """Regroupe les actions phrase-par-phrase en ``action`` par scène, dans le même découpage EXACT que
    ``_group`` (mêmes tailles de groupe, jamais recalculé séparément — pour ne jamais désynchroniser
    narration et action)."""
    grouped: list[str] = []
    position = 0
    for size in group_sizes:
        chunk = [item for item in actions[position:position + size] if item]
        grouped.append(" ; ".join(dict.fromkeys(chunk)))
        position += size
    return grouped


def _prompt(excerpt: str, style: str, vertical: bool, rules: str = "", avoid: tuple[str, ...] = (),
           action: str = "") -> str:
    """Prompt d'une scène, construit UNIQUEMENT à partir du profil du projet (style, consignes, liste négative)
    et de l'indication scénique propre à CETTE scène (``action``, #92 — jamais le texte prononcé lui-même)."""
    head = []
    if style.strip():
        head.append(f"Style visuel : {style.strip().rstrip('.')}.")
    if rules.strip():
        head.append(f"Consignes visuelles : {rules.strip().rstrip('.')}.")
    tail = ["Composition verticale, un sujet clair, lumière soignée." if vertical else "Composition claire, un sujet net, lumière soignée.",
            "Aucun texte, aucun sous-titre, aucun logo, aucune marque, aucun filigrane."]
    if avoid:
        tail.append("Ne montre jamais : " + " ; ".join(item.rstrip(".") for item in avoid) + ".")
    action_sentence = f" Pose/action de cette scène : {action.strip().rstrip('.')}." if action.strip() else ""
    fixed = " ".join(head + tail) + action_sentence
    budget = max(80, min(MAX_EXCERPT, MAX_PROMPT - len(fixed) - 90))  # la liste négative n'est jamais tronquée
    text = excerpt if len(excerpt) <= budget else excerpt[: budget - 1].rsplit(" ", 1)[0] + "…"
    scene = f"Illustration cinématographique d’une scène qui accompagne ce passage : « {text} ».{action_sentence}"
    return " ".join([*head, scene, *tail])[:MAX_PROMPT]


def build_storyboard(script: str, scene_count: int, *, visual_style: str = "", aspect: str = "9:16",
                     narration_pace: str = "normal", visual_rules: str = "", visual_avoid: tuple[str, ...] = ()) -> list[Scene]:
    """Scènes ordonnées (une par prompt d'image). Liste vide si le script n'a aucune phrase.

    Les indications scéniques ``{...}`` (#92) sont retirées de ``narration`` (jamais prononcées) et
    deviennent ``Scene.action`` — un script sans accolade produit un ``action`` toujours vide, comportement
    strictement inchangé."""
    pairs = split_sentences_with_actions(script)
    if not pairs:
        return []
    actions, sentences = [pair[0] for pair in pairs], [pair[1] for pair in pairs]
    wpm = WORDS_PER_MINUTE.get(narration_pace, WORDS_PER_MINUTE["normal"])
    groups = _group(sentences, min(scene_count, MAX_SCENES))
    grouped_actions = _group_actions(actions, [len(group) for group in groups])
    scenes: list[Scene] = []
    for index, (group, action) in enumerate(zip(groups, grouped_actions, strict=True), start=1):
        narration = " ".join(group)
        seconds = round(max(len(narration.split()), 1) / wpm * 60, 1)
        prompt = _prompt(narration, visual_style, aspect == "9:16", visual_rules, tuple(visual_avoid), action)
        scenes.append(Scene(index, narration, prompt, seconds, action))
    return scenes
