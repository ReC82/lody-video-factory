"""Choix de la voix d'un personnage par écoute comparative (ticket #153, Voltix).

Logique pure, sans Streamlit, réutilisée par la fiche personnage (``view_characters``) et, après la fusion
de #151, par le pipeline 2D. AUCUN nouveau système de fiches : la voix choisie est écrite dans la fiche
personnage existante (``voice_provider``/``voice_name``/``external_voice_id`` de #30/#70, réglages de jeu
``voice_direction`` de #92). C'est cette fiche que lisent déjà :

* la production classique (``narrative_context.resolve_voice``, #70) — chaque épisode qui sélectionne le
  personnage reprend sa voix, sans la renseigner à nouveau ;
* le pipeline 2D (``animation.audio.voice_plan`` de la PR #151) — le catalogue d'animation référence le
  personnage par son nom (``"character": "Voltix"``), jamais une seconde copie de la voix.

Garde-fous (exigences du ticket) :

* **aucune voix inventée** : les candidats viennent uniquement du catalogue assaini ElevenLabs
  (``elevenlabs_voices.resolve_catalog``) ou de la voix déjà enregistrée sur la fiche ;
* **aucune attribution sans accord** : ``assign_default_voice`` n'est appelé que par un clic explicite ;
* **coût estimé avant tout appel** (``estimate``) et **accord explicite** exigé par l'interface avant toute
  génération facturée ; un extrait déjà en cache coûte 0 et n'est jamais régénéré ;
* **cache par empreinte** voix/modèle/réglages/texte (``fingerprint``), réutilisable pour les répliques d'un
  épisode (``plan_lines``) : un texte inchangé avec la même voix ne repasse jamais par ElevenLabs ;
* aucun secret : ce module ne lit jamais ``config.toml`` ni une clé, et n'en écrit nulle part.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any

from lody import settings
from lody.generation import costing
from lody.generation.elevenlabs_voices import VoiceInfo

PROVIDER = "elevenlabs"
MAX_CANDIDATES = 3
FINGERPRINT_VERSION = 1

#: Court dialogue SDI/BNC commun à toutes les voix comparées (mêmes mots pour tout le monde : seule la voix ou
#: la prosodie change). Texte seul, aucune balise : compatible avec tous les modèles ElevenLabs proposés.
SAMPLE_DIALOGUE = (
    "Salut, c'est Voltix ! Tu vois ce câble ? C'est du SDI. Au bout, un connecteur BNC : on pousse, on tourne "
    "d'un quart de tour… clic, verrouillé ! Et dedans, un coaxial 75 ohms : l'image voyage sans perdre une miette."
)

MODELS: dict[str, str] = {
    "eleven_multilingual_v2": "Multilingual v2 (stable, réglages fins)",
    "eleven_v3": "Eleven v3 (plus expressif)",
}
DEFAULT_MODEL = "eleven_multilingual_v2"


@dataclass(frozen=True)
class Profile:
    """Variation de prosodie contrôlée. Uniquement des réglages officiellement documentés par ElevenLabs
    (``stability``/``similarity_boost``/``style``/``use_speaker_boost``/``speed``) — mêmes clés que
    ``characters.validate_voice_direction`` (#92), donc enregistrables tels quels sur la fiche."""

    key: str
    label: str
    detail: str
    stability: float
    style: float
    speed: float
    similarity_boost: float = 0.75

    def settings(self, model_id: str) -> dict[str, Any]:
        # eleven_v3 n'accepte que trois niveaux de stabilité (0 créatif / 0,5 naturel / 1 robuste) : la valeur
        # la plus proche est envoyée, jamais une valeur intermédiaire que l'API pourrait refuser.
        stability = min((0.0, 0.5, 1.0), key=lambda level: abs(level - self.stability)) \
            if model_id == "eleven_v3" else self.stability
        return {"model_id": model_id, "stability": stability, "similarity_boost": self.similarity_boost,
                "style": self.style, "use_speaker_boost": True, "speed": self.speed}


PROFILES: tuple[Profile, ...] = (
    Profile("naturel", "Naturel", "Débit normal, jeu posé : la référence pour juger le timbre.",
            stability=0.5, style=0.2, speed=1.0),
    Profile("vif", "Vif", "Plus énergique et un peu plus rapide, toujours articulé (cible Voltix).",
            stability=0.4, style=0.4, speed=1.08),
    Profile("tres_vif", "Très vif", "Rythme rapide et jeu marqué : limite haute avant de perdre en clarté.",
            stability=0.3, style=0.55, speed=1.15),
)
DEFAULT_PROFILE = "vif"


def profile(key: str) -> Profile:
    for item in PROFILES:
        if item.key == key:
            return item
    raise KeyError(key)


# -- candidats : uniquement des voix réelles du catalogue -------------------------------------------------------
_FRENCH_MARKERS = ("fr", "french", "français", "francais", "france")
_MULTI_MARKERS = ("multilingual", "multilingue")


def _label_text(voice: VoiceInfo) -> list[str]:
    return [str(value).casefold() for value in (*voice.labels.keys(), *voice.labels.values())]


def is_french(voice: VoiceInfo) -> bool:
    for key, value in voice.labels.items():
        if key.casefold() in ("language", "accent", "langue") and any(
                marker == str(value).casefold().strip() or marker in str(value).casefold().split()
                for marker in _FRENCH_MARKERS):
            return True
    return False


def is_multilingual(voice: VoiceInfo) -> bool:
    return any(marker in text for text in _label_text(voice) for marker in _MULTI_MARKERS)


def suggest_candidates(voices: Iterable[VoiceInfo], *, current_voice_id: str = "",
                       limit: int = MAX_CANDIDATES) -> list[VoiceInfo]:
    """Propose au plus ``limit`` voix RÉELLES : la voix déjà enregistrée (si présente au catalogue), puis les
    voix françaises, puis les multilingues. Ne complète jamais avec une voix d'une autre langue : mieux vaut
    proposer moins de trois voix qu'une voix inadaptée choisie par défaut. Ordre stable (catalogue)."""
    pool = list(voices)
    chosen: list[VoiceInfo] = []
    for group in ([v for v in pool if current_voice_id and v.voice_id == current_voice_id],
                  [v for v in pool if is_french(v)], [v for v in pool if is_multilingual(v)]):
        for voice in group:
            if voice not in chosen and len(chosen) < limit:
                chosen.append(voice)
    return chosen


# -- empreinte et cache audio ------------------------------------------------------------------------------------
def fingerprint(voice_id: str, settings_: dict[str, Any], text: str, *, provider: str = PROVIDER) -> str:
    """Empreinte stable voix/modèle/réglages/texte. Deux appels identiques donnent la même empreinte, donc le
    même fichier en cache ; changer UN de ces éléments en donne une autre (jamais un audio périmé réutilisé)."""
    payload = {"v": FINGERPRINT_VERSION, "provider": provider, "voice_id": voice_id,
               "settings": {key: settings_[key] for key in sorted(settings_)}, "text": text}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cache_dir() -> Path:
    return settings.data_dir() / "voice-samples"


class AudioCache:
    """Fichiers audio rangés par empreinte (``<racine>/<2 premiers>/<empreinte>.mp3``). Écriture atomique ;
    jamais de suppression automatique (un extrait déjà payé reste réutilisable)."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else cache_dir()

    def path(self, key: str) -> Path:
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
            raise ValueError("empreinte invalide")
        return self.root / key[:2] / f"{key}.mp3"

    def has(self, key: str) -> bool:
        return self.path(key).is_file()

    def get(self, key: str) -> bytes | None:
        try:
            return self.path(key).read_bytes()
        except FileNotFoundError:
            return None

    def put(self, key: str, data: bytes) -> Path:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False, suffix=".tmp") as handle:
            handle.write(data)
            temp = Path(handle.name)
        os.replace(temp, target)
        return target


# -- plan de comparaison et estimation ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Sample:
    voice: VoiceInfo
    profile: Profile
    model_id: str
    text: str

    @property
    def settings(self) -> dict[str, Any]:
        return self.profile.settings(self.model_id)

    @property
    def key(self) -> str:
        return fingerprint(self.voice.voice_id, self.settings, self.text)


def comparison_plan(voices: Sequence[VoiceInfo], profile_keys: Sequence[str], *, model_id: str = DEFAULT_MODEL,
                    text: str = SAMPLE_DIALOGUE) -> list[Sample]:
    """Extraits à comparer : chaque voix (au plus ``MAX_CANDIDATES``) × chaque profil demandé, même texte."""
    if model_id not in MODELS:
        raise ValueError(f"modèle inconnu : {model_id}")
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("texte vide")
    profiles = [profile(key) for key in dict.fromkeys(profile_keys)]
    return [Sample(voice, item, model_id, text) for voice in list(voices)[:MAX_CANDIDATES] for item in profiles]


@dataclass(frozen=True)
class Estimate:
    """Ce qu'il en coûterait AVANT tout appel. ``amount`` est ``None`` si aucun tarif n'est configuré : la
    dépense reste alors à confirmer à l'aveugle — jamais présentée comme gratuite."""

    total: int
    cached: int
    to_generate: int
    characters: int
    amount: Decimal | None
    currency: str
    rate: Decimal | None

    @property
    def free(self) -> bool:
        return self.to_generate == 0

    def describe(self) -> str:
        if self.free:
            return f"{self.total} extrait(s), tous déjà en cache : aucun appel ElevenLabs, coût 0."
        price = (f"≈ {costing.format_money(self.amount, self.currency)} (estimation indicative)"
                 if self.amount is not None else "tarif ElevenLabs non configuré (pricing.toml) : montant inconnu")
        cached = f", {self.cached} déjà en cache (gratuit)" if self.cached else ""
        return (f"{self.to_generate} extrait(s) à générer{cached} — {self.characters} caractères facturés, "
                f"{price}.")


def estimate(texts_to_generate: Sequence[str], total: int, book: costing.PriceBook | None = None) -> Estimate:
    book = book if book is not None else costing.load_price_book()
    characters = sum(len(text) for text in texts_to_generate)
    rate = book.rate("voice", PROVIDER)
    amount = None
    if rate is not None:
        # Même unité que l'estimation des productions : tarif par tranche de 1 000 caractères.
        amount = (rate * Decimal(characters) / Decimal(1000)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    return Estimate(total=total, cached=total - len(texts_to_generate), to_generate=len(texts_to_generate),
                    characters=characters, amount=amount, currency=book.currency, rate=rate)


def estimate_samples(samples: Sequence[Sample], cache: AudioCache,
                     book: costing.PriceBook | None = None) -> Estimate:
    missing = [sample.text for sample in samples if not cache.has(sample.key)]
    return estimate(missing, len(samples), book)


def approval_token(samples: Sequence[Sample]) -> str:
    """Identifie un plan précis : l'accord donné pour CE plan ne vaut plus si une voix, un profil, le modèle ou
    le texte change (l'interface remet alors la case d'accord à zéro)."""
    return hashlib.sha256("|".join(sample.key for sample in samples).encode()).hexdigest()[:16]


@dataclass
class GenerationOutcome:
    generated: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


Synthesize = Callable[[str, str, dict[str, Any]], bytes]


def generate_missing(samples: Sequence[Sample], cache: AudioCache, synthesize: Synthesize, *,
                     approved: bool) -> GenerationOutcome:
    """Génère UNIQUEMENT les extraits absents du cache, et seulement si ``approved`` est vrai (accord explicite
    après affichage de l'estimation). ``synthesize(texte, voice_id, réglages)`` est l'appel payant (en pratique
    ``provider.generate_voice_preview``). Une erreur sur un extrait n'empêche pas les autres."""
    if not approved:
        raise PermissionError("accord de dépense requis avant toute génération facturée")
    outcome = GenerationOutcome()
    for sample in samples:
        key = sample.key
        if cache.has(key):
            outcome.reused.append(key)
            continue
        try:
            data = synthesize(sample.text, sample.voice.voice_id, sample.settings)
        except Exception as error:  # noqa: BLE001 — message du fournisseur affiché, jamais propagé en crash
            outcome.errors[key] = str(getattr(error, "message", error)) or type(error).__name__
            continue
        if not data:
            outcome.errors[key] = "Le moteur n'a renvoyé aucun audio."
            continue
        cache.put(key, data)
        outcome.generated.append(key)
    return outcome


# -- attribution durable à la fiche ------------------------------------------------------------------------------
def assign_default_voice(repo: Any, project_id: str, character_id: str, voice: VoiceInfo,
                         chosen_profile: Profile, model_id: str = DEFAULT_MODEL) -> Any:
    """Enregistre la voix ET ses réglages comme défaut DE CE personnage (choix explicite de l'utilisateur).
    Ne touche à aucun autre champ, personnage ou projet. Réutilise les validations de la fiche (#30/#92)."""
    if model_id not in MODELS:
        raise ValueError(f"modèle inconnu : {model_id}")
    repo.update(project_id, character_id, voice_provider=PROVIDER, voice_name=voice.name,
                external_voice_id=voice.voice_id)
    return repo.set_voice_direction(project_id, character_id, chosen_profile.settings(model_id))


# -- contrat pour le pipeline 2D (branché dans le worker après la fusion de #151) --------------------------------
#: Étapes à recalculer quand la voix d'un personnage change : uniquement ce qui dépend du son et de son timing.
REVOICE_RECOMPUTE = ("voice", "visemes", "subtitles", "mix")
#: Ce qu'un changement de voix ne doit JAMAIS régénérer.
REVOICE_PRESERVE = ("storyboard", "illustrations", "rig", "music")


def character_voice_settings(character: Any) -> tuple[str, dict[str, Any]] | None:
    """(voice_id, réglages) effectivement enregistrés sur la fiche, ou ``None`` si aucune voix ElevenLabs n'y
    est choisie (voix provisoire, à signaler — jamais un identifiant inventé)."""
    provider_ = str(getattr(character, "voice_provider", "") or "").strip().lower()
    voice_id = str(getattr(character, "external_voice_id", "") or "").strip()
    if provider_ != PROVIDER or not voice_id:
        return None
    direction = dict(getattr(character, "voice_direction", {}) or {})
    direction.setdefault("model_id", DEFAULT_MODEL)
    return voice_id, direction


def character_voice_fingerprint(character: Any) -> str:
    """Empreinte de la voix d'un personnage (voix + modèle + réglages, sans texte). Si elle diffère de celle
    enregistrée lors d'un rendu, seules les étapes ``REVOICE_RECOMPUTE`` sont à refaire."""
    resolved = character_voice_settings(character)
    if resolved is None:
        return "provisional"
    voice_id, direction = resolved
    return fingerprint(voice_id, direction, "")


def revoice_plan(previous_fingerprint: str, character: Any) -> dict[str, Any]:
    current = character_voice_fingerprint(character)
    changed = current != previous_fingerprint
    return {"changed": changed, "fingerprint": current,
            "recompute": list(REVOICE_RECOMPUTE) if changed else [], "preserve": list(REVOICE_PRESERVE)}


@dataclass(frozen=True)
class LinePlan:
    text: str
    key: str
    cached: bool


def plan_lines(character: Any, texts: Sequence[str], cache: AudioCache,
               book: costing.PriceBook | None = None) -> tuple[list[LinePlan], Estimate]:
    """Répliques d'un épisode pour la voix de la fiche : lesquelles sont déjà en cache (réutilisées telles
    quelles) et combien coûterait la synthèse des autres. Lève ``ValueError`` si la fiche n'a pas de voix
    ElevenLabs choisie — jamais de repli silencieux vers une voix inventée."""
    resolved = character_voice_settings(character)
    if resolved is None:
        raise ValueError("aucune voix ElevenLabs choisie sur la fiche de ce personnage")
    voice_id, direction = resolved
    lines = []
    for raw in texts:
        text = " ".join(str(raw or "").split())
        key = fingerprint(voice_id, direction, text)
        lines.append(LinePlan(text, key, cache.has(key)))
    return lines, estimate([line.text for line in lines if not line.cached], len(lines), book)
