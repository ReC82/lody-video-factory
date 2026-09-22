"""Premier connecteur : l'API du socle historique MoneyPrinterTurbo (MIT).

**Connecteur hérité** — c'est le seul module de Lody qui connaît ce contrat (voir
``docs/lody-engine-contract.md``). Il ne lit jamais de valeur de clé : le moteur lit lui-même son
``config.toml``. Ici, ``config.toml`` sert uniquement à savoir si une clé est *renseignée* (booléen)
et à reprendre des préférences d'affichage des sous-titres ; aucune valeur secrète n'est conservée.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lody import settings
from lody.generation import typography
from lody.generation.engine_facts import PROBLEM_LABELS, EngineFacts, resolve
from lody.generation.models import (
    Capability,
    CapabilityState,
    CapabilityStatus,
    ErrorKind,
    ExternalTask,
    GenerationRequest,
    GenerationResult,
    PreflightReport,
    ProviderError,
    RemoteState,
    TaskSnapshot,
)
from lody.generation.provider import WORDS_PER_MINUTE, VideoGenerationProvider
from lody.generation.safety import ALLOWED_VIDEO_SUFFIXES, classify_engine_error, is_safe_task_id, resolve_within, sanitize
from lody.generation.secrets_store import key_blocked

logger = logging.getLogger("lody.connector")

PROVIDER_ID = "moneyprinterturbo"
DEFAULT_URL = "http://moneyprinterturbo-api:8080"
DEFAULT_STORAGE = "/MoneyPrinterTurbo/storage"
MAX_RESPONSE_BYTES = 5_000_000
PING_TTL = 10.0

Transport = Callable[[str, str, bytes | None, dict[str, str], float], tuple[int, bytes]]

_VIDEO_SOURCES = {"openai_image": "openai_image"}
_VOICE_RATE = {"calme": 0.95, "normal": 1.0, "rapide": 1.1}
_KEYLESS_LLM = frozenset({"ollama", "claude_code", "litellm", "pollinations"})
# Préférences de sous-titres reprises de [ui] (non secrètes) : clé de config → champ de la requête.
_UI_FIELDS = {
    "font_name": "font_name", "font_size": "font_size", "stroke_color": "stroke_color",
    "stroke_width": "stroke_width", "text_fore_color": "text_fore_color",
    "subtitle_position": "subtitle_position", "subtitle_animation": "subtitle_animation",
    "rounded_subtitle_background": "rounded_subtitle_background",
}
# Étape *en cours* d'après le dernier jalon de progression atteint par le moteur.
_STEPS = ((5, "Préparation"), (20, "Génération de la voix"), (30, "Création des sous-titres"),
          (40, "Génération des images"), (50, "Montage de la vidéo"))


def urllib_transport(method: str, url: str, body: bytes | None, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (URL de configuration interne)
            return response.status, response.read(MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        return error.code, error.read(MAX_RESPONSE_BYTES)


def step_for_progress(progress: int) -> str:
    label = "En attente dans la file du moteur"
    for milestone, name in _STEPS:
        if progress >= milestone:
            label = name
    return label


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


# Préférences de sous-titres utilisées quand la configuration du moteur est inconnue (police latine fournie
# avec le moteur : une police CJK dessine l'apostrophe ’ en pleine largeur, voir typography.py).
_UI_FALLBACK: dict[str, Any] = {
    "font_name": typography.LATIN_SAFE_FONT, "font_size": 58, "stroke_color": "#000000", "stroke_width": 1.8,
    "text_fore_color": "#FFFFFF", "subtitle_position": "bottom",
}
# Fournisseur de texte du projet → identifiant du fournisseur de texte du moteur (llm_provider).
_ENGINE_TEXT_PROVIDER = {"openai": "openai"}
_TEXT_LABELS = {"openai": "OpenAI", "moonshot": "Moonshot", "deepseek": "DeepSeek", "gemini": "Google Gemini",
                "anthropic": "Anthropic", "azure": "Azure OpenAI", "qwen": "Qwen", "groq": "Groq",
                "openrouter": "OpenRouter", "ollama": "Ollama", "grok": "Grok", "mistral": "Mistral"}
_VOICE_LABELS = {"elevenlabs": "ElevenLabs", "edge": "Voix gratuite (Edge)"}
_ASPECTS = ("9:16", "16:9", "1:1")


def _label(provider: str) -> str:
    return _TEXT_LABELS.get(provider, provider or "aucun")


def apply_image_template(template: str, term: str) -> str:
    """Prompt final d'une image côté moteur : ``template.replace("{term}", term)`` (comme material._openai_image_prompt)."""
    template = (template or "").strip()
    if not template or "{term}" not in template:
        return term
    return template.replace("{term}", term)


def script_prompt(request: GenerationRequest, narrative_block: str = "", limit: int = 2000) -> str:
    """Consignes envoyées au moteur pour écrire le script (borné à la limite du moteur).

    ``narrative_block`` (#36) : bloc narratif déjà rendu depuis le snapshot de production (voir
    ``narrative_context.render_prompt_block``), ajouté à la fin, séparé du reste. Chaîne vide par défaut :
    le prompt reste alors identique à avant #36 (aucune sélection, ou production antérieure à #35).
    """
    words = tuple(round(seconds / 60 * WORDS_PER_MINUTE.get(request.narration_pace, 155))
                  for seconds in (request.duration_min, request.duration_max))
    head = [
        "Écris uniquement le texte parlé de la narration : pas de titre, pas d’indication de scène, pas de liste.",
        f"Durée cible : {request.duration_min} à {request.duration_max} secondes, soit environ {words[0]} à {words[1]} mots.",
    ]
    for label, value in (("Ton", request.tone), ("Public", request.audience), ("Orientation", request.orientation)):
        if value:
            head.append(f"{label} : {value[:200]}")
    if request.structure:
        head.append("Structure, dans cet ordre : " + " ; ".join(step[:120] for step in request.structure[:12]) + ".")
    head_text = "\n".join(head)[:850]
    rules = request.instructions.strip()
    budget = max(limit - len(head_text) - 20, 0)
    if len(rules) > budget:
        rules = rules[: max(budget - 1, 0)].rstrip() + "…"
    text = head_text + ("\nConsignes permanentes :\n" + rules if rules else "")
    if narrative_block:
        text += "\n\n" + narrative_block
    return text[:limit]


def build_payload(request: GenerationRequest, facts: EngineFacts) -> dict[str, Any]:
    """Requête ``POST /api/v1/videos`` construite depuis les paramètres du projet."""
    scenes = max(len(request.visual_prompts), 1)
    clip = max(2, min(15, -(-int(request.duration_max * 1.15) // scenes)))  # couvre la voix, plafonné
    voice = request.voice
    voice_name = (f"elevenlabs:{voice.voice_id}:{voice.name}" if voice.provider == "elevenlabs" and voice.voice_id
                  else voice.name)
    music = request.music_provider
    payload: dict[str, Any] = {
        "video_subject": request.subject,
        "video_script": request.script,
        "video_terms": list(request.visual_prompts),
        "video_language": request.language,
        "video_aspect": request.aspect,
        "video_source": _VIDEO_SOURCES.get(request.visual_provider, request.visual_provider),
        "video_concat_mode": "sequential",
        "match_materials_to_script": True,
        "video_clip_duration": clip,
        "video_count": 1,
        "voice_name": voice_name,
        "voice_rate": _VOICE_RATE.get(request.narration_pace, 1.0),
        "voice_volume": 1.0,
        "bgm_type": "elevenlabs" if music == "elevenlabs" else ("random" if music == "library" else ""),
        "bgm_volume": 0.2 if music in ("elevenlabs", "library") else 0.0,
        "video_music_prompt": (f"Musique de fond instrumentale, discrète. {request.visual_style}"[:600]
                               if music == "elevenlabs" else ""),
        "subtitle_enabled": True,
        "subtitle_display_mode": "sentence",
        **(facts.ui if facts.known else _UI_FALLBACK),
    }
    # Police de sous-titres : une langue latine ne doit pas hériter d'une police CJK à apostrophe pleine largeur.
    font, _ = typography.pick_subtitle_font(str(payload.get("font_name", "")), request.language, settings.fonts_dir())
    if font:
        payload["font_name"] = font
    return payload


class MoneyPrinterTurboConnector(VideoGenerationProvider):
    id = PROVIDER_ID
    display_name = "Moteur de génération"
    supports_cancel = False  # aucun endpoint d'annulation dans le contrat

    def __init__(self, base_url: str = DEFAULT_URL, storage_root: str | Path = DEFAULT_STORAGE,
                 config_path: Path | None = None, report_path: Path | None = None, api_key: str = "", transport: Transport = urllib_transport,
                 clock: Callable[[], float] = time.monotonic, secrets_status_path: Path | None = None):
        self.base_url = base_url.rstrip("/")
        self.storage_root = Path(storage_root)
        self.config_path = config_path
        self.report_path = report_path
        # Explicite plutôt que la valeur par défaut de settings.secrets_status_path() : mêmes garanties d'isolation
        # en test que config_path/report_path (voir docs/lody-secrets.md et l'historique de ce fichier).
        self.secrets_status_path = secrets_status_path
        self._api_key = api_key
        self._transport = transport
        self._clock = clock
        self._ping_ok_until = 0.0

    # -- HTTP ---------------------------------------------------------------
    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 10.0) -> tuple[int, dict[str, Any]]:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._api_key:
            headers["x-api-key"] = self._api_key
        try:
            status, raw = self._transport(method, self.base_url + path, body, headers, timeout)
        except (TimeoutError, socket.timeout):
            raise ProviderError(ErrorKind.TIMEOUT, "Le moteur de génération n’a pas répondu à temps.") from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise ProviderError(ErrorKind.TIMEOUT, "Le moteur de génération n’a pas répondu à temps.") from None
            raise ProviderError(ErrorKind.UNAVAILABLE, "Le moteur de génération est injoignable pour le moment.") from None
        except OSError:
            raise ProviderError(ErrorKind.UNAVAILABLE, "Le moteur de génération est injoignable pour le moment.") from None
        try:
            envelope = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            envelope = None
        if not isinstance(envelope, dict):
            if status >= 500:
                raise ProviderError(ErrorKind.PROVIDER, "Le moteur de génération a rencontré une erreur interne.")
            raise ProviderError(ErrorKind.INVALID_RESPONSE, "Réponse inattendue du moteur de génération.")
        return status, envelope

    def _raise_for_status(self, status: int, envelope: dict[str, Any], stage: str) -> None:
        if status == 200 and envelope.get("status", 200) == 200:
            return
        message = sanitize(envelope.get("message", ""))
        logger.warning("moteur : HTTP %s (%s) %s", status, stage, message)
        if status == 401:
            raise ProviderError(ErrorKind.AUTH, "Le moteur refuse l’accès : sa clé d’API n’est pas transmise par Lody.")
        if status == 404:
            raise ProviderError(ErrorKind.UNKNOWN_TASK, "Le moteur ne connaît pas cette tâche.")
        if status == 429:
            raise ProviderError(ErrorKind.QUEUE_FULL, "La file du moteur est pleine : réessaie dans quelques minutes.")
        if status == 400:
            raise ProviderError(ErrorKind.REJECTED, "Le moteur a refusé la demande (paramètres invalides).")
        raise ProviderError(ErrorKind.PROVIDER, "Le moteur de génération a rencontré une erreur.")

    # -- API du VideoGenerationProvider --------------------------------------
    def describe_params(self, request: GenerationRequest) -> dict[str, Any]:
        payload = build_payload(request, resolve(self.config_path, self.report_path))
        return {key: value for key, value in payload.items() if key not in ("video_script", "video_terms", "video_subject")}

    def _ping(self) -> None:
        if self._clock() < self._ping_ok_until:
            return
        status, _ = self._call_ping()
        if status != 200:
            raise ProviderError(ErrorKind.UNAVAILABLE, "Le moteur de génération ne répond pas correctement.")
        self._ping_ok_until = self._clock() + PING_TTL

    def _call_ping(self) -> tuple[int, Any]:
        try:
            status, raw = self._transport("GET", self.base_url + "/ping", None, {"Accept": "application/json"}, 3.0)
        except (TimeoutError, socket.timeout):
            raise ProviderError(ErrorKind.TIMEOUT, "Le moteur de génération n’a pas répondu à temps.") from None
        except OSError:
            raise ProviderError(ErrorKind.UNAVAILABLE, "Le moteur de génération est injoignable pour le moment.") from None
        return status, raw

    # -- preflight complet (aucun appel payant) ------------------------------------
    def preflight(self, request: GenerationRequest) -> PreflightReport:
        facts = resolve(self.config_path, self.report_path)
        C = Capability
        items = [self._engine_status(), self._storage_status(), self._settings_status(request)]
        items.append(self._text_status(request, facts))
        items.append(self._visual_status(request, facts))
        items.append(self._voice_status(request, facts))
        items.append(self._music_status(request, facts))
        order = (C.TEXT, C.VISUAL, C.VOICE, C.MUSIC, C.ENGINE, C.STORAGE, C.SETTINGS)
        items.sort(key=lambda item: order.index(item.capability))
        return PreflightReport(tuple(items))

    def _engine_status(self) -> CapabilityStatus:
        try:
            self._ping()
        except ProviderError as error:
            return CapabilityStatus(Capability.ENGINE, CapabilityState.UNAVAILABLE, "moteur", "",
                                    message=error.message + " Réessaie dans un instant.", fix="platform",
                                    admin=f"GET /ping sur {self.base_url} : {error.kind.value}")
        return CapabilityStatus(Capability.ENGINE, CapabilityState.READY, "moteur", "moteur", message="Le moteur répond.")

    def _storage_status(self) -> CapabilityStatus:
        root = self.storage_root
        ok = root.is_dir() and os.access(root, os.R_OK | os.X_OK)
        if ok:
            return CapabilityStatus(Capability.STORAGE, CapabilityState.READY, message="Le stockage des vidéos est accessible.")
        return CapabilityStatus(Capability.STORAGE, CapabilityState.UNAVAILABLE,
                                message="Le stockage des vidéos n’est pas accessible.", fix="platform",
                                admin=f"dossier de stockage {root} absent ou illisible dans le conteneur")

    def _settings_status(self, request: GenerationRequest) -> CapabilityStatus:
        missing = []
        if request.aspect not in _ASPECTS:
            missing.append("le format vidéo")
        if not request.language:
            missing.append("la langue")
        if not (0 < request.duration_min <= request.duration_max):
            missing.append("la durée cible")
        if request.voice.provider == "elevenlabs" and not request.voice.voice_id:
            missing.append("la voix (identifiant de voix)")
        if missing:
            return CapabilityStatus(Capability.SETTINGS, CapabilityState.NOT_CONFIGURED,
                                    message="Paramètres du projet à compléter : " + ", ".join(missing) + ".", fix="project",
                                    admin="paramètres obligatoires manquants ou invalides : " + ", ".join(missing))
        return CapabilityStatus(Capability.SETTINGS, CapabilityState.READY, message="Les paramètres obligatoires sont présents.")

    @staticmethod
    def _unverified(capability: Capability, requested: str, facts: EngineFacts) -> CapabilityStatus:
        reasons = " ; ".join(PROBLEM_LABELS.get(code, code) for code in facts.problems) or "configuration du moteur inconnue"
        return CapabilityStatus(
            capability, CapabilityState.UNVERIFIED, requested,
            message="Impossible de vérifier ce fournisseur : la configuration du moteur n’est pas accessible à Lody.",
            fix="platform", admin=reasons)

    def _text_status(self, request: GenerationRequest, facts: EngineFacts) -> CapabilityStatus:
        C, St = Capability.TEXT, CapabilityState
        if request.script.strip():
            return CapabilityStatus(C, St.NOT_NEEDED, request.text_provider, message="Script fourni : aucun appel texte.")
        wanted = _ENGINE_TEXT_PROVIDER.get(request.text_provider)
        if request.text_provider == "manual" or wanted is None:
            return CapabilityStatus(C, St.NOT_CONFIGURED, request.text_provider,
                                    message="Aucun fournisseur de texte utilisable : fournis ton script ou choisis un fournisseur dans les paramètres du projet.",
                                    fix="project", admin=f"fournisseur de texte du projet « {request.text_provider} » sans équivalent côté moteur")
        if not facts.known:
            return self._unverified(C, request.text_provider, facts)
        engine = facts.llm_provider
        details = (f"projet={request.text_provider} ; moteur llm_provider={engine or '(vide)'} ; "
                   f"clé {wanted}={'renseignée' if facts.key_present(wanted) else 'vide/absente'} ; "
                   f"clé {engine or '?'}={'renseignée' if facts.key_present(engine) else 'vide/absente'} ; "
                   f"modèle {wanted}={facts.llm_model.get(wanted) or 'défaut du moteur'} (source : {facts.source})")
        if engine != wanted:
            return CapabilityStatus(
                C, St.NOT_CONFIGURED, request.text_provider, engine, facts.llm_model.get(engine, ""),
                message=(f"Le moteur écrit ses scripts avec « {_label(engine)} », pas avec « {_label(wanted)} » choisi pour ce projet. "
                         "Fournis ton script, ou demande à l’administrateur de régler le moteur sur ce fournisseur."),
                fix="platform", admin=details)
        if not facts.key_present(wanted):
            return CapabilityStatus(C, St.NOT_CONFIGURED, request.text_provider, engine,
                                    message=f"La clé du fournisseur de texte « {_label(wanted)} » n’est pas configurée côté serveur.",
                                    fix="platform", admin=details)
        if wanted == "openai" and (blocked := key_blocked("app.openai_api_key", status_path=self.secrets_status_path)):
            # Un redémarrage sain ne prouve pas qu'une clé fonctionne : voir apply_secrets.py. Relit un fichier
            # de statut déjà écrit, aucun nouvel appel au fournisseur ici.
            return CapabilityStatus(C, St.UNAVAILABLE, request.text_provider, engine, message=blocked,
                                    fix="platform", admin=details)
        model = facts.llm_model.get(wanted, "")
        return CapabilityStatus(C, St.READY, request.text_provider, engine, model or "modèle par défaut du moteur",
                                message=f"Script écrit par « {_label(engine)} »" + ("" if model else " (modèle par défaut du moteur)") + ".",
                                admin=details)

    def _visual_status(self, request: GenerationRequest, facts: EngineFacts) -> CapabilityStatus:
        C, St = Capability.VISUAL, CapabilityState
        if request.visual_provider not in _VIDEO_SOURCES:
            return CapabilityStatus(C, St.UNAVAILABLE, request.visual_provider, message="Ce type de visuels n’est pas encore pris en charge par le moteur.",
                                    fix="project", admin=f"source visuelle « {request.visual_provider} » non gérée par le connecteur")
        if not facts.known:
            return self._unverified(C, request.visual_provider, facts)
        details = (f"endpoint+modèle d'images={'oui' if facts.image_endpoint else 'non'} ; clé images={'renseignée' if facts.image_key else 'vide'} ; "
                   f"modèle={facts.image_model or '(vide)'} ; gabarit d'images global du moteur="
                   f"{'aucun' if not facts.image_template else ('neutre' if facts.image_template_neutral else 'ACTIF : ' + facts.image_template)} "
                   f"(source : {facts.source})")
        if not facts.image_template_neutral:  # isolation par projet : le moteur ne doit rien ajouter au prompt du projet
            return CapabilityStatus(
                C, St.NOT_CONFIGURED, request.visual_provider, "openai_image", facts.image_model,
                message=("Le moteur ajoute un gabarit d’images global à tous les projets : les visuels d’un projet peuvent alors "
                         "être influencés par un autre univers. L’administrateur doit le neutraliser pour que chaque projet garde son propre style."),
                fix="platform", admin=details + " ; correction : `openai_image_prompt_template = \"\"` dans la section [app] de config.toml, "
                                                 "redémarrer l'API, puis ./scripts/lody-engine-report.sh")
        if not facts.image_endpoint:
            return CapabilityStatus(C, St.NOT_CONFIGURED, request.visual_provider, message="La génération d’images n’est pas configurée côté serveur.",
                                    fix="platform", admin=details)
        if not facts.image_key and facts.image_public_openai:
            return CapabilityStatus(C, St.NOT_CONFIGURED, request.visual_provider, "openai_image", facts.image_model,
                                    message="La clé du fournisseur d’images n’est pas configurée côté serveur.", fix="platform", admin=details)
        if facts.image_public_openai and (blocked := key_blocked("app.openai_image_api_keys", status_path=self.secrets_status_path)):
            return CapabilityStatus(C, St.UNAVAILABLE, request.visual_provider, "openai_image", facts.image_model,
                                    message=blocked, fix="platform", admin=details)
        return CapabilityStatus(C, St.READY, request.visual_provider, "openai_image", facts.image_model,
                                message=f"Images générées avec « {facts.image_model} ».", admin=details)

    def _voice_status(self, request: GenerationRequest, facts: EngineFacts) -> CapabilityStatus:
        C, St, voice = Capability.VOICE, CapabilityState, request.voice
        if voice.provider != "elevenlabs":
            return CapabilityStatus(C, St.READY, voice.provider, voice.provider, message="Voix gratuite : aucune clé nécessaire.")
        if not voice.voice_id:
            return CapabilityStatus(C, St.NOT_CONFIGURED, voice.provider, message="Aucune voix n’est choisie pour ce projet.", fix="project",
                                    admin="identifiant de voix ElevenLabs absent des paramètres du projet")
        if not facts.known:
            return self._unverified(C, voice.provider, facts)
        details = (f"clé elevenlabs={'renseignée' if facts.eleven_key else 'vide'} ; modèle du moteur={facts.eleven_model or '(défaut)'} ; "
                   f"modèle du projet={voice.model or '(non précisé)'} (source : {facts.source})")
        if not facts.eleven_key:
            return CapabilityStatus(C, St.NOT_CONFIGURED, voice.provider, message="La clé du fournisseur de voix n’est pas configurée côté serveur.",
                                    fix="platform", admin=details)
        if blocked := key_blocked("elevenlabs.api_key", status_path=self.secrets_status_path):
            return CapabilityStatus(C, St.UNAVAILABLE, voice.provider, "elevenlabs", message=blocked, fix="platform", admin=details)
        note = ""
        if voice.model and facts.eleven_model and voice.model != facts.eleven_model:
            note = f" Le moteur utilisera le modèle « {facts.eleven_model} » (réglage serveur), pas « {voice.model} »."
        return CapabilityStatus(C, St.READY, voice.provider, "elevenlabs", facts.eleven_model or voice.model,
                                message=f"Voix « {voice.name or voice.voice_id} » sélectionnée (son existence chez le fournisseur ne peut pas être vérifiée sans appel).{note}",
                                admin=details)

    def _music_status(self, request: GenerationRequest, facts: EngineFacts) -> CapabilityStatus:
        C, St, music = Capability.MUSIC, CapabilityState, request.music_provider
        if music in ("none", ""):
            return CapabilityStatus(C, St.DISABLED, music, message="Aucune musique de fond.")
        if music == "library":
            return CapabilityStatus(C, St.READY, music, "library", message="Musique de la bibliothèque du moteur.")
        if not facts.known:
            return self._unverified(C, music, facts)
        details = f"clé elevenlabs (musique)={'renseignée' if facts.eleven_key else 'vide'} (source : {facts.source})"
        if not facts.eleven_key:
            return CapabilityStatus(C, St.NOT_CONFIGURED, music, message="La clé du fournisseur de musique n’est pas configurée côté serveur.",
                                    fix="platform", admin=details)
        if blocked := key_blocked("elevenlabs.api_key", status_path=self.secrets_status_path):
            return CapabilityStatus(C, St.UNAVAILABLE, music, "elevenlabs", message=blocked, fix="platform", admin=details)
        return CapabilityStatus(C, St.READY, music, "elevenlabs", message="Musique générée par le fournisseur choisi.", admin=details)

    def describe_script_request(self, request: GenerationRequest, narrative_block: str = "") -> dict[str, Any]:
        """Requête d'écriture du script (POST /api/v1/scripts) : aucun prompt système personnalisé n'est envoyé."""
        return {"video_subject": request.subject, "video_language": request.language, "paragraph_number": 1,
                "video_script_prompt": script_prompt(request, narrative_block),
                "custom_system_prompt": "(non envoyé : prompt système par défaut du moteur)"}

    def trace_prompts(self, request: GenerationRequest) -> dict[str, Any]:
        facts = resolve(self.config_path, self.report_path)
        template = facts.image_template if facts.known else ""
        applied = not facts.image_template_neutral if facts.known else False
        scenes = []
        for index, term in enumerate(request.visual_prompts, 1):
            final = apply_image_template(template, term) if applied else term
            scenes.append({"index": index, "prompt_sent": term, "final_prompt": final, "engine_template_applied": final != term})
        return {"scenes": scenes, "image_template": {
            "applied": applied, "text": template if applied else "",
            "origin": "moteur — config.toml, gabarit GLOBAL partagé par tous les projets" if applied
            else ("inconnu (configuration du moteur non vérifiable)" if not facts.known else "aucun")}}

    def write_script(self, request: GenerationRequest, narrative_block: str = "") -> str:
        payload = {key: value for key, value in self.describe_script_request(request, narrative_block).items()
                  if key != "custom_system_prompt"}
        status, envelope = self._call("POST", "/api/v1/scripts", payload, timeout=240.0)
        self._raise_for_status(status, envelope, "script")
        data = envelope.get("data")
        script = _text(data.get("video_script")) if isinstance(data, dict) else ""
        if not script or "Error: " in script:
            kind, message = classify_engine_error("script", script or "empty")
            raise ProviderError(kind, message, stage="script")
        return script

    def submit(self, request: GenerationRequest, idempotency_key: str) -> ExternalTask:
        # Le moteur n'a pas de clé d'idempotence : l'unicité est garantie en amont par Lody
        # (transition d'état atomique) et le lancement n'est jamais rejoué automatiquement.
        payload = build_payload(request, resolve(self.config_path, self.report_path))
        status, envelope = self._call("POST", "/api/v1/videos", payload, timeout=30.0)
        self._raise_for_status(status, envelope, "submit")
        data = envelope.get("data")
        task_id = data.get("task_id") if isinstance(data, dict) else None
        if not is_safe_task_id(task_id):
            raise ProviderError(ErrorKind.INVALID_RESPONSE, "Le moteur a répondu sans identifiant de tâche exploitable.")
        return ExternalTask(PROVIDER_ID, task_id)

    def poll(self, task: ExternalTask) -> TaskSnapshot:
        if not is_safe_task_id(task.task_id):
            raise ProviderError(ErrorKind.INVALID_RESPONSE, "Identifiant de tâche invalide.")
        status, envelope = self._call("GET", f"/api/v1/tasks/{urllib.parse.quote(task.task_id, safe='')}")
        if status == 404:
            return TaskSnapshot(RemoteState.UNKNOWN)
        self._raise_for_status(status, envelope, "poll")
        data = envelope.get("data")
        if not isinstance(data, dict) or "state" not in data:
            raise ProviderError(ErrorKind.INVALID_RESPONSE, "Réponse inattendue du moteur de génération.")
        state = data.get("state")
        warnings = tuple(sanitize(item, 120) for item in (data.get("warnings") or []) if isinstance(item, (str, int)))[:10]
        progress = data.get("progress") if isinstance(data.get("progress"), int) and not isinstance(data.get("progress"), bool) else None
        if state == -1:
            kind, message = classify_engine_error(_text(data.get("failed_stage")), data.get("error"))
            logger.error("tâche %s en échec (étape %s) : %s", task.task_id, data.get("failed_stage"), sanitize(data.get("error")))
            return TaskSnapshot(RemoteState.FAILED, progress, error=ProviderError(kind, message, stage=_text(data.get("failed_stage"))))
        if state == 1:
            return TaskSnapshot(RemoteState.DONE, 100, "Terminée", result=self._result(task, data), warnings=warnings)
        if state == 4:
            progress = max(0, min(int(progress or 0), 99))
            queued = progress < 5
            return TaskSnapshot(RemoteState.QUEUED if queued else RemoteState.RUNNING, progress,
                                step_for_progress(progress), warnings=warnings)
        raise ProviderError(ErrorKind.INVALID_RESPONSE, "Statut inconnu renvoyé par le moteur de génération.")

    def _ref_from(self, task: ExternalTask, value: Any) -> str | None:
        """Référence relative ``tasks/<id>/<fichier>`` extraite d'une URI ou d'un chemin du moteur."""
        if not isinstance(value, str):
            return None
        marker = f"/tasks/{task.task_id}/"
        index = value.find(marker)
        if index < 0:
            return None
        return "tasks/" + value[index + len("/tasks/"):].split("?", 1)[0]

    def _result(self, task: ExternalTask, data: dict[str, Any]) -> GenerationResult:
        videos = data.get("videos") if isinstance(data.get("videos"), list) else []
        ref = next((r for r in (self._ref_from(task, item) for item in videos) if r), None)
        if not ref:
            raise ProviderError(ErrorKind.VIDEO_MISSING, "Le moteur annonce la tâche terminée sans fournir de vidéo.")
        assets: list[dict[str, str]] = []
        candidates = [("audio", data.get("audio_file")), ("subtitle", data.get("subtitle_path"))]
        candidates += [("clip", item) for item in (data.get("materials") or []) if isinstance(item, str)]
        for kind, value in candidates:
            asset_ref = self._ref_from(task, value)
            if asset_ref:
                try:
                    self.resolve_asset(task, asset_ref, suffixes=())
                except ValueError:
                    continue
                assets.append({"kind": kind, "ref": asset_ref})
        duration = data.get("audio_duration")
        return GenerationResult(
            video_ref=ref,
            duration_seconds=float(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None,
            script=_text(data.get("script")),
            assets=tuple(assets[:40]),
            warnings=tuple(sanitize(item, 120) for item in (data.get("warnings") or []) if isinstance(item, (str, int)))[:10],
        )

    def list_scene_images(self, task: ExternalTask) -> list[dict[str, str]]:
        """Images de scène (PNG) de la tâche, dans l'ordre de génération : fonds de miniature gratuits, déjà payés avec la vidéo."""
        if not is_safe_task_id(task.task_id):
            return []
        folder = self.storage_root / "tasks" / task.task_id
        try:
            entries = [entry for entry in os.scandir(folder) if entry.is_file() and re.fullmatch(r"openai-image-[0-9a-f]+\.png", entry.name)]
        except OSError:
            return []
        images = []
        for entry in sorted(entries, key=lambda item: (item.stat().st_mtime_ns, item.name)):
            ref = f"tasks/{task.task_id}/{entry.name}"
            try:
                self.resolve_asset(task, ref, suffixes=(".png",))
            except ValueError:
                continue
            images.append({"ref": ref, "name": entry.name})
        return images[:40]

    def read_subtitles(self, task: ExternalTask) -> str | None:
        """SRT déjà produit par le moteur (aucun appel) ; ``None`` s'il est absent ou illisible."""
        if not is_safe_task_id(task.task_id):
            return None
        try:
            path = self.resolve_asset(task, f"tasks/{task.task_id}/subtitle.srt", suffixes=(".srt",))
            return path.read_text(encoding="utf-8")[:1_000_000]
        except (ValueError, OSError, UnicodeDecodeError):
            return None

    def recover(self, task: ExternalTask) -> GenerationResult | None:
        """Après un redémarrage du moteur (état en mémoire perdu) : la vidéo est peut-être sur disque."""
        if not is_safe_task_id(task.task_id):
            return None
        ref = f"tasks/{task.task_id}/final-1.mp4"
        try:
            self.resolve_asset(task, ref)
        except ValueError:
            return None
        return GenerationResult(video_ref=ref, warnings=("Résultat retrouvé sur le disque après un redémarrage du moteur.",))

    def resolve_asset(self, task: ExternalTask, ref: str, suffixes: tuple[str, ...] = ALLOWED_VIDEO_SUFFIXES) -> Path:
        if not is_safe_task_id(task.task_id):
            raise ValueError("identifiant de tâche invalide")
        return resolve_within(self.storage_root, ref, prefix=f"tasks/{task.task_id}", suffixes=suffixes)


def build_from_environment() -> MoneyPrinterTurboConnector:
    return MoneyPrinterTurboConnector(
        base_url=os.environ.get("LODY_ENGINE_URL", "").strip() or DEFAULT_URL,
        storage_root=os.environ.get("LODY_ENGINE_STORAGE", "").strip() or DEFAULT_STORAGE,
        api_key=os.environ.get("LODY_ENGINE_API_KEY", "").strip(),
    )
