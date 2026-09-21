"""Modèles typés partagés par les connecteurs, le service et les vues (aucun secret, aucun réseau)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ProductionStatus(str, Enum):
    """Statuts internes d'une production, indépendants de ceux du moteur.

    ``ANNULEE`` existe dans le schéma mais n'est jamais produit : le moteur historique n'a pas
    d'endpoint d'annulation (``VideoGenerationProvider.supports_cancel`` est faux).
    """

    BROUILLON = "BROUILLON"
    EN_ATTENTE_CONFIRMATION = "EN_ATTENTE_CONFIRMATION"
    CONFIRMEE = "CONFIRMEE"
    EN_FILE = "EN_FILE"
    EN_COURS = "EN_COURS"
    TERMINEE = "TERMINEE"
    ECHEC = "ECHEC"
    ANNULEE = "ANNULEE"


ACTIVE_STATUSES = frozenset({ProductionStatus.CONFIRMEE, ProductionStatus.EN_FILE, ProductionStatus.EN_COURS})
TERMINAL_STATUSES = frozenset({ProductionStatus.TERMINEE, ProductionStatus.ECHEC, ProductionStatus.ANNULEE})

STATUS_LABELS = {
    ProductionStatus.BROUILLON: "Brouillon",
    ProductionStatus.EN_ATTENTE_CONFIRMATION: "En attente de confirmation",
    ProductionStatus.CONFIRMEE: "Confirmée",
    ProductionStatus.EN_FILE: "En file d’attente",
    ProductionStatus.EN_COURS: "En cours",
    ProductionStatus.TERMINEE: "Terminée",
    ProductionStatus.ECHEC: "Échec",
    ProductionStatus.ANNULEE: "Annulée",
}


class ErrorKind(str, Enum):
    """Nature d'une erreur de connecteur, pour un message clair et un traitement adapté."""

    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    AUTH = "auth"
    KEY_MISSING = "key_missing"
    QUOTA = "quota"
    PROVIDER = "provider"
    REJECTED = "rejected"
    QUEUE_FULL = "queue_full"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN_TASK = "unknown_task"
    VIDEO_MISSING = "video_missing"
    INTERRUPTED = "interrupted"


class ProviderError(Exception):
    """Erreur d'un connecteur. ``message`` est destiné à l'utilisateur et ne contient aucun secret."""

    def __init__(self, kind: ErrorKind, message: str, *, stage: str = ""):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.stage = stage


@dataclass(frozen=True)
class VoiceSpec:
    provider: str = ""
    voice_id: str = ""
    name: str = ""
    model: str = ""


@dataclass(frozen=True)
class GenerationRequest:
    """Demande de génération, indépendante du moteur (sérialisée telle quelle dans ``params``)."""

    subject: str
    language: str = "fr-FR"
    aspect: str = "9:16"
    duration_min: int = 45
    duration_max: int = 60
    tone: str = ""
    audience: str = ""
    orientation: str = ""
    narration_pace: str = "normal"
    structure: tuple[str, ...] = ()
    instructions: str = ""
    visual_style: str = ""
    scenes_per_minute_min: int = 6
    scenes_per_minute_max: int = 8
    voice: VoiceSpec = field(default_factory=VoiceSpec)
    text_provider: str = "openai"
    visual_provider: str = "openai_image"
    music_provider: str = "none"
    # Renseignés au fil de la production (script fourni par l'utilisateur ou écrit par le moteur).
    script: str = ""
    visual_prompts: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["structure"] = list(self.structure)
        data["visual_prompts"] = list(self.visual_prompts)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationRequest:
        known = {name for name in cls.__dataclass_fields__}
        values = {key: value for key, value in data.items() if key in known}
        voice = data.get("voice") or {}
        if not isinstance(voice, VoiceSpec):
            voice = VoiceSpec(**{k: v for k, v in voice.items() if k in VoiceSpec.__dataclass_fields__})
        values["voice"] = voice
        values["structure"] = tuple(data.get("structure") or ())
        values["visual_prompts"] = tuple(data.get("visual_prompts") or ())
        return cls(**values)

    def with_updates(self, **changes: Any) -> GenerationRequest:
        data = self.to_dict()
        data.update(changes)
        return GenerationRequest.from_dict(data)


@dataclass(frozen=True)
class ReadinessIssue:
    """Problème détecté avant lancement. ``blocking`` empêche la confirmation."""

    code: str
    message: str
    blocking: bool = True


class Capability(str, Enum):
    TEXT = "text"
    VISUAL = "visual"
    VOICE = "voice"
    MUSIC = "music"
    ENGINE = "engine"
    STORAGE = "storage"
    SETTINGS = "settings"


class CapabilityState(str, Enum):
    READY = "ready"
    NOT_NEEDED = "not_needed"        # ex. script fourni : aucun appel texte
    DISABLED = "disabled"            # ex. musique désactivée par le projet
    NOT_CONFIGURED = "not_configured"
    UNAVAILABLE = "unavailable"      # configuré mais injoignable / non pris en charge
    UNVERIFIED = "unverified"        # impossible à vérifier : on ne prétend rien


BLOCKING_STATES = frozenset({CapabilityState.NOT_CONFIGURED, CapabilityState.UNAVAILABLE, CapabilityState.UNVERIFIED})
CAPABILITY_LABELS = {
    Capability.TEXT: "Texte", Capability.VISUAL: "Visuels", Capability.VOICE: "Voix", Capability.MUSIC: "Musique",
    Capability.ENGINE: "Moteur", Capability.STORAGE: "Stockage", Capability.SETTINGS: "Paramètres",
}
# Libellés simples (accord compris) : « Texte : prêt », « Voix : prête », « Moteur : disponible »…
_FEMININE = {Capability.VOICE, Capability.MUSIC}


def state_label(capability: Capability, state: CapabilityState) -> str:
    if capability is Capability.ENGINE:
        return "disponible" if state is CapabilityState.READY else (
            "non vérifié" if state is CapabilityState.UNVERIFIED else "indisponible")
    ready = "prête" if capability in _FEMININE else "prêt"
    return {
        CapabilityState.READY: ready,
        CapabilityState.NOT_NEEDED: "non nécessaire",
        CapabilityState.DISABLED: "désactivée" if capability in _FEMININE else "désactivé",
        CapabilityState.NOT_CONFIGURED: "à configurer",
        CapabilityState.UNAVAILABLE: "indisponible",
        CapabilityState.UNVERIFIED: "non vérifié" if capability not in _FEMININE else "non vérifiée",
    }[state]


@dataclass(frozen=True)
class CapabilityStatus:
    """État d'une capacité : ce qui est demandé, ce qui est réellement configuré, et pourquoi.

    ``message`` est destiné à l'utilisateur (aucun nom technique) ; ``admin`` est le détail nettoyé pour
    l'administrateur (noms de champs et de fournisseurs, jamais de valeur de clé).
    """

    capability: Capability
    state: CapabilityState
    requested: str = ""
    configured: str = ""
    model: str = ""
    message: str = ""
    fix: str = ""  # "project" (réglage du projet) | "platform" (administrateur) | ""
    admin: str = ""

    @property
    def blocking(self) -> bool:
        return self.state in BLOCKING_STATES

    @property
    def label(self) -> str:
        return CAPABILITY_LABELS[self.capability]

    @property
    def state_text(self) -> str:
        return state_label(self.capability, self.state)


@dataclass(frozen=True)
class PreflightReport:
    items: tuple[CapabilityStatus, ...]

    @property
    def blocking(self) -> tuple[CapabilityStatus, ...]:
        return tuple(item for item in self.items if item.blocking)

    @property
    def ready(self) -> bool:
        return not self.blocking

    def get(self, capability: Capability) -> CapabilityStatus | None:
        return next((item for item in self.items if item.capability is capability), None)

    def to_issues(self) -> list[ReadinessIssue]:
        return [ReadinessIssue(f"{item.capability.value}_{item.state.value}", item.message or item.label, item.blocking)
                for item in self.items if item.blocking]


@dataclass(frozen=True)
class ExternalTask:
    provider: str
    task_id: str


class RemoteState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GenerationResult:
    video_ref: str
    duration_seconds: float | None = None
    script: str = ""
    assets: tuple[dict[str, str], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskSnapshot:
    state: RemoteState
    # Avancement réel fourni par le moteur, ou None s'il n'est pas connu (jamais inventé).
    progress: int | None = None
    step: str = ""
    error: ProviderError | None = None
    result: GenerationResult | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SceneUnits:
    """Quantités estimées d'une génération, utilisées pour le coût."""

    text_calls: int
    scenes: tuple[int, int]
    images: tuple[int, int]
    characters: tuple[int, int]
    music_tracks: int
    audio_seconds: tuple[int, int]
