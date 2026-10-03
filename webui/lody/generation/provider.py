"""Interface d'un moteur de génération vidéo.

Les vues et le service d'orchestration ne connaissent que cette interface. Un autre moteur (ou,
plus tard, des fournisseurs séparés pour le texte, l'image, la voix ou la musique) s'ajoute en
implémentant ces méthodes, sans toucher au reste.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from pathlib import Path

from lody.generation.models import (
    ErrorKind,
    ExternalTask,
    GenerationRequest,
    GenerationResult,
    PreflightReport,
    ProviderError,
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
    #: vrai si le moteur sait générer UNE image seule (fond de miniature dédié, payant). Faux pour le moteur historique
    #: (son API n'expose aucune génération d'image isolée) : la miniature utilise alors une image de scène existante.
    supports_thumbnail_background: bool = False
    #: vrai si le moteur sait réellement recevoir une image de référence pour la génération visuelle (#38).
    #: Faux par défaut : ni le moteur historique (``mpt_connector``) ni la simulation (``demo``) ne le
    #: permettent aujourd'hui — une sélection avec image de référence bascule alors explicitement sur le texte
    #: seul (voir ``narrative_context.reference_images_status``), jamais une transmission silencieusement
    #: ignorée mais présentée comme utilisée.
    supports_reference_images: bool = False
    #: vrai si le moteur sait recevoir un audio DÉJÀ synthétisé pour le montage final, sans refaire le
    #: TTS (#86, prérequis multi-locuteurs #39). Faux par défaut : ``upload_audio_asset`` est alors
    #: refusé — jamais une simulation silencieuse d'un support absent.
    supports_audio_assets: bool = False
    #: vrai si le moteur sait générer UN clip vocal isolé avec des réglages explicites, pour comparer le
    #: jeu vocal d'une voix déjà configurée (#92, diagnostic). Faux par défaut : ``generate_voice_preview``
    #: est alors refusé — jamais une simulation silencieuse.
    supports_voice_preview: bool = False

    def plan_units(self, request: GenerationRequest) -> SceneUnits:
        units = estimate_units(request)
        return replace(units, thumbnails=1) if self.supports_thumbnail_background else units

    def list_scene_images(self, task: ExternalTask) -> list[dict[str, str]]:
        """Images de scène déjà générées pour la tâche (fonds de miniature GRATUITS) : [{"ref": …, "name": …}], dans l'ordre."""
        return []

    def read_subtitles(self, task: ExternalTask) -> str | None:
        """Contenu (SRT) des sous-titres déjà produits par le moteur, ou ``None`` : jamais d'appel payant pour l'obtenir."""
        return None

    def generate_thumbnail_background(self, request: GenerationRequest, prompt: str) -> bytes:
        """Génère UNE image de fond sans texte (payant). Lève ``ProviderError`` si le moteur ne le permet pas."""
        raise ProviderError(ErrorKind.REJECTED, "Ce moteur ne sait pas générer une image de fond de miniature.")

    def upload_audio_asset(self, audio_bytes: bytes, filename: str, scope: str) -> str:
        """Transfère un audio DÉJÀ synthétisé (#86, prérequis #39) : AUCUN appel TTS, AUCUNE génération —
        seulement le transfert. Renvoie un identifiant opaque, consommable UNE SEULE FOIS par un appel
        ultérieur à ``submit()`` dont la requête porte EXACTEMENT le même ``scope`` (voir
        ``GenerationRequest.audio_asset_scope``, jamais le ``production_id`` lui-même — voir
        ``lody.generation.safety.new_audio_asset_scope``). Lève ``ProviderError`` si le moteur ne le permet pas.
        """
        raise ProviderError(ErrorKind.REJECTED, "Ce moteur ne sait pas recevoir un audio déjà synthétisé.")

    def upload_reference_image(self, image_bytes: bytes, scope: str) -> str:
        """Transfère une image de référence DÉJÀ VALIDÉE pour un personnage (#92, continuité visuelle) :
        aucune génération, seulement le transfert. Renvoie un identifiant opaque, RÉSOLUBLE PLUSIEURS FOIS
        (une fois par scène du personnage, contrairement à ``upload_audio_asset``) par les appels à
        ``submit()`` ultérieurs dont la requête porte EXACTEMENT le même ``scope`` (voir
        ``GenerationRequest.image_asset_scope``). Lève ``ProviderError`` si le moteur ne le permet pas —
        voir ``supports_reference_images``, jamais un repli silencieux sur le texte seul à cet endroit
        (c'est à l'appelant de décider, voir ``narrative_context.reference_images_status``).
        """
        raise ProviderError(ErrorKind.REJECTED, "Ce moteur ne sait pas recevoir une image de référence.")

    def generate_reference_proposal(self, prompt: str) -> bytes:
        """Génère UNE image isolée (payant), pour proposer une référence visuelle de personnage à partir
        de sa fiche — jamais liée à une tâche vidéo, jamais enregistrée tant qu'elle n'est pas validée
        explicitement par l'utilisateur (l'enregistrement lui-même reste hors de ce module, voir la
        frontière #35 : cette interface ne connaît aucun personnage, seulement un texte et des octets).
        Lève ``ProviderError`` si le moteur ne le permet pas.
        """
        raise ProviderError(ErrorKind.REJECTED, "Ce moteur ne sait pas générer une proposition de référence.")

    def generate_voice_preview(self, text: str, voice_id: str, settings: dict) -> bytes:
        """Génère UN clip vocal isolé (payant) avec des réglages EXPLICITES (#92, diagnostic du jeu
        vocal) — jamais lié à une tâche vidéo, jamais utilisé pour changer la voix réellement configurée
        d'une production (c'est à l'appelant de proposer le choix, jamais silencieusement). ``settings``
        ne porte que des champs déjà documentés comme officiellement compatibles avec le modèle employé
        (voir ``mpt_connector.generate_voice_preview``) — jamais une balise non confirmée compatible.
        Lève ``ProviderError`` si le moteur ne le permet pas.
        """
        raise ProviderError(ErrorKind.REJECTED, "Ce moteur ne sait pas générer un essai vocal isolé.")

    def describe_script_request(self, request: GenerationRequest, narrative_block: str = "",
                                dialogue_character: str = "") -> dict:
        """Ce qui est envoyé au fournisseur pour ÉCRIRE le script (sujet, langue, prompt éditorial) — pour archivage.

        ``narrative_block`` (#36) : contexte narratif déjà rendu (voir ``narrative_context.render_prompt_block``),
        ou chaîne vide sans sélection — auquel cas le comportement est strictement celui d'avant #36.

        ``dialogue_character`` (#77) : nom du personnage de référence en mode dialogue mono-personnage (voir
        ``narrative_context.script_mode``), ou chaîne vide (par défaut) pour la narration externe historique
        — un connecteur qui ignore ce paramètre se comporte exactement comme avant #77.
        """
        return {}

    def trace_prompts(self, request: GenerationRequest) -> dict:
        """Prompts réellement transmis par scène, avec ce que le moteur y ajoute de son côté (gabarit global…).

        Par défaut le moteur n'ajoute rien : le prompt final d'une scène est celui envoyé par Lody.
        """
        return {"scenes": [{"index": i, "prompt_sent": prompt, "final_prompt": prompt, "engine_template_applied": False}
                           for i, prompt in enumerate(request.visual_prompts, 1)],
                "image_template": {"applied": False, "text": "", "origin": "aucun"}}

    def describe_params(self, request: GenerationRequest) -> dict:
        """Paramètres effectifs envoyés au moteur (sans script, sans prompts, sans secret) — pour archivage."""
        return {}

    @abstractmethod
    def preflight(self, request: GenerationRequest) -> PreflightReport:
        """Contrôle complet, sans aucun coût, de ce dont la génération a besoin (texte, visuels, voix, musique,
        moteur, stockage, paramètres) : ce qui est demandé, ce qui est réellement configuré, et pourquoi."""

    def check_ready(self, request: GenerationRequest) -> list[ReadinessIssue]:
        """Problèmes bloquants issus du preflight (compatibilité)."""
        return self.preflight(request).to_issues()

    @abstractmethod
    def write_script(self, request: GenerationRequest, narrative_block: str = "", dialogue_character: str = "") -> str:
        """Écrit le script (appel texte). Lève ``ProviderError``.

        ``narrative_block``/``dialogue_character`` (#36/#77) : voir ``describe_script_request``. Un
        connecteur qui ignore ces paramètres se comporte exactement comme avant #36/#77 (aucune obligation
        de les utiliser)."""

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
