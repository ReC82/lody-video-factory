"""Interface d'un moteur de génération vidéo.

Les vues et le service d'orchestration ne connaissent que cette interface. Un autre moteur (ou,
plus tard, des fournisseurs séparés pour le texte, l'image, la voix ou la musique) s'ajoute en
implémentant ces méthodes, sans toucher au reste.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from lody.generation.models import (
    ExternalTask,
    GenerationRequest,
    GenerationResult,
    ReadinessIssue,
    SceneUnits,
    TaskSnapshot,
)

CHARS_PER_WORD = 6  # français, espaces compris : repère d'estimation, pas une mesure
WORDS_PER_MINUTE = {"calme": 130, "normal": 155, "rapide": 175}


def estimate_units(request: GenerationRequest) -> SceneUnits:
    """Quantités attendues, déduites du brief (ou du script fourni, alors exact)."""
    wpm = WORDS_PER_MINUTE.get(request.narration_pace, WORDS_PER_MINUTE["normal"])
    script = request.script.strip()
    if script:
        words = len(script.split())
        seconds = max(1, round(words / wpm * 60))
        span = (seconds, seconds)
        chars = (len(script), len(script))
        text_calls = 0
    else:
        span = (request.duration_min, request.duration_max)
        words = (round(span[0] / 60 * wpm), round(span[1] / 60 * wpm))
        chars = (words[0] * CHARS_PER_WORD, words[1] * CHARS_PER_WORD)
        text_calls = 1
    scenes = (
        max(1, round(span[0] / 60 * request.scenes_per_minute_min)),
        max(1, round(span[1] / 60 * request.scenes_per_minute_max)),
    )
    music = 1 if request.music_provider not in ("none", "library", "") else 0
    return SceneUnits(text_calls=text_calls, scenes=scenes, images=scenes, characters=chars,
                      music_tracks=music, audio_seconds=span)


class VideoGenerationProvider(ABC):
    """Un moteur capable de produire une vidéo complète à partir d'une demande."""

    #: identifiant stable enregistré avec chaque production
    id: str = ""
    #: nom affichable, sans marque de moteur (les crédits sont dans le pied de page)
    display_name: str = ""
    #: vrai uniquement si le moteur sait réellement interrompre une tâche
    supports_cancel: bool = False
    #: vrai pour une simulation : aucun fournisseur payant n'est appelé
    is_demo: bool = False

    def plan_units(self, request: GenerationRequest) -> SceneUnits:
        return estimate_units(request)

    def describe_params(self, request: GenerationRequest) -> dict:
        """Paramètres effectifs envoyés au moteur (sans script, sans prompts, sans secret) — pour archivage."""
        return {}

    @abstractmethod
    def check_ready(self, request: GenerationRequest) -> list[ReadinessIssue]:
        """Vérifie sans coût que la génération peut partir (moteur joignable, clés renseignées)."""

    @abstractmethod
    def write_script(self, request: GenerationRequest) -> str:
        """Écrit le script (appel texte). Lève ``ProviderError``."""

    @abstractmethod
    def submit(self, request: GenerationRequest, idempotency_key: str) -> ExternalTask:
        """Envoie la génération complète (script et prompts visuels fournis). Lève ``ProviderError``."""

    @abstractmethod
    def poll(self, task: ExternalTask) -> TaskSnapshot:
        """Interroge l'état d'une tâche. Lève ``ProviderError`` (moteur injoignable, réponse invalide…)."""

    @abstractmethod
    def recover(self, task: ExternalTask) -> GenerationResult | None:
        """Retrouve un résultat déjà produit quand le moteur a oublié la tâche (sinon ``None``)."""

    @abstractmethod
    def resolve_asset(self, task: ExternalTask, ref: str) -> Path:
        """Chemin local *validé* d'un fichier de la tâche. Lève ``ValueError`` s'il n'appartient pas à la tâche."""
