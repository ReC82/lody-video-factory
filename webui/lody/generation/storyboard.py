"""Storyboard déterministe : découpe le script en scènes et écrit un prompt visuel explicite par scène.

Pas d'appel externe : le résultat dépend uniquement du script, du style et du nombre de scènes,
donc il est reproductible, testable, et identique à l'estimation affichée avant confirmation.
Les prompts sont *dérivés* du script (pas rédigés par un modèle) : ils sont conservés et visibles.
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


@dataclass(frozen=True)
class Scene:
    index: int
    narration: str
    prompt: str
    seconds: float

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "narration": self.narration, "prompt": self.prompt, "seconds": self.seconds}


def split_sentences(script: str) -> list[str]:
    cleaned = _PAUSE_TAG.sub(" ", script or "")
    return [" ".join(part.split()) for part in _SENTENCE.split(cleaned) if part and part.strip()]


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


def _prompt(excerpt: str, style: str, vertical: bool, rules: str = "", avoid: tuple[str, ...] = ()) -> str:
    """Prompt d'une scène, construit UNIQUEMENT à partir du profil du projet (style, consignes, liste négative)."""
    head = []
    if style.strip():
        head.append(f"Style visuel : {style.strip().rstrip('.')}.")
    if rules.strip():
        head.append(f"Consignes visuelles : {rules.strip().rstrip('.')}.")
    tail = ["Composition verticale, un sujet clair, lumière soignée." if vertical else "Composition claire, un sujet net, lumière soignée.",
            "Aucun texte, aucun sous-titre, aucun logo, aucune marque, aucun filigrane."]
    if avoid:
        tail.append("Ne montre jamais : " + " ; ".join(item.rstrip(".") for item in avoid) + ".")
    fixed = " ".join(head + tail)
    budget = max(80, min(MAX_EXCERPT, MAX_PROMPT - len(fixed) - 90))  # la liste négative n'est jamais tronquée
    text = excerpt if len(excerpt) <= budget else excerpt[: budget - 1].rsplit(" ", 1)[0] + "…"
    scene = f"Illustration cinématographique d’une scène qui accompagne ce passage : « {text} »."
    return " ".join([*head, scene, *tail])[:MAX_PROMPT]


def build_storyboard(script: str, scene_count: int, *, visual_style: str = "", aspect: str = "9:16",
                     narration_pace: str = "normal", visual_rules: str = "", visual_avoid: tuple[str, ...] = ()) -> list[Scene]:
    """Scènes ordonnées (une par prompt d'image). Liste vide si le script n'a aucune phrase."""
    sentences = split_sentences(script)
    if not sentences:
        return []
    wpm = WORDS_PER_MINUTE.get(narration_pace, WORDS_PER_MINUTE["normal"])
    scenes: list[Scene] = []
    for index, group in enumerate(_group(sentences, min(scene_count, MAX_SCENES)), start=1):
        narration = " ".join(group)
        seconds = round(max(len(narration.split()), 1) / wpm * 60, 1)
        scenes.append(Scene(index, narration, _prompt(narration, visual_style, aspect == "9:16", visual_rules, tuple(visual_avoid)), seconds))
    return scenes
