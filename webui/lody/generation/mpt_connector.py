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
import socket
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lody import settings
from lody.generation.models import (
    ErrorKind,
    ExternalTask,
    GenerationRequest,
    GenerationResult,
    ProviderError,
    ReadinessIssue,
    RemoteState,
    TaskSnapshot,
)
from lody.generation.provider import WORDS_PER_MINUTE, VideoGenerationProvider
from lody.generation.safety import ALLOWED_VIDEO_SUFFIXES, classify_engine_error, is_safe_task_id, resolve_within, sanitize

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


# Préférences de sous-titres utilisées quand la configuration du moteur n'est pas lisible
# (polices fournies avec le moteur, compatibles caractères latins).
_UI_FALLBACK: dict[str, Any] = {
    "font_name": "MicrosoftYaHeiBold.ttc", "font_size": 58, "stroke_color": "#000000", "stroke_width": 1.8,
    "text_fore_color": "#FFFFFF", "subtitle_position": "bottom",
}


class EngineConfigFlags:
    """Ce que Lody a le droit de savoir de config.toml : des booléens et des préférences non secrètes.

    ``readable`` est faux quand le fichier ne peut pas être lu (droits, absence, TOML invalide) : Lody ne
    peut alors *rien affirmer* sur les clés et laisse le moteur juge.
    """

    def __init__(self, raw: dict[str, Any] | None):
        self.readable = raw is not None
        raw = raw or {}
        app = raw.get("app") if isinstance(raw.get("app"), dict) else {}
        eleven = raw.get("elevenlabs") if isinstance(raw.get("elevenlabs"), dict) else {}
        ui = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}

        def filled(value: Any) -> bool:
            if isinstance(value, str):
                return bool(value.strip())
            return isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value)

        self.llm_provider = _text(app.get("llm_provider")).lower()
        key_name = f"{self.llm_provider}_api_key"
        self.llm_key_known = key_name in app or self.llm_provider in _KEYLESS_LLM
        self.llm_key_filled = filled(app.get(key_name)) or self.llm_provider in _KEYLESS_LLM
        self.image_endpoint_set = bool(_text(app.get("openai_image_base_url")) and _text(app.get("openai_image_model")))
        self.image_key_filled = filled(app.get("openai_image_api_keys"))
        self.image_public_openai = "openai.com" in _text(app.get("openai_image_base_url"))
        self.elevenlabs_key_filled = filled(eleven.get("api_key")) or bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
        self.elevenlabs_model = _text(eleven.get("model_id"))
        self.ui: dict[str, Any] = {} if self.readable else dict(_UI_FALLBACK)
        for source, target in _UI_FIELDS.items():
            value = ui.get(source)
            if isinstance(value, (str, int, float, bool)):
                self.ui[target] = value
        if ui.get("subtitle_background_enabled") is True and _text(ui.get("subtitle_background_color")):
            self.ui["text_background_color"] = _text(ui.get("subtitle_background_color"))


def read_engine_flags(path: Path | None = None) -> EngineConfigFlags:
    target = path or settings.config_path()
    try:
        with target.open("rb") as handle:
            return EngineConfigFlags(tomllib.load(handle))
    except (OSError, tomllib.TOMLDecodeError):
        return EngineConfigFlags(None)


def script_prompt(request: GenerationRequest, limit: int = 2000) -> str:
    """Consignes envoyées au moteur pour écrire le script (borné à la limite du moteur)."""
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
    return text[:limit]


def build_payload(request: GenerationRequest, flags: EngineConfigFlags) -> dict[str, Any]:
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
        **flags.ui,
    }
    return payload


class MoneyPrinterTurboConnector(VideoGenerationProvider):
    id = PROVIDER_ID
    display_name = "Moteur de génération"
    supports_cancel = False  # aucun endpoint d'annulation dans le contrat

    def __init__(self, base_url: str = DEFAULT_URL, storage_root: str | Path = DEFAULT_STORAGE,
                 config_path: Path | None = None, api_key: str = "", transport: Transport = urllib_transport,
                 clock: Callable[[], float] = time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.storage_root = Path(storage_root)
        self.config_path = config_path
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
        payload = build_payload(request, read_engine_flags(self.config_path))
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

    def check_ready(self, request: GenerationRequest) -> list[ReadinessIssue]:
        try:
            self._ping()
        except ProviderError as error:
            return [ReadinessIssue("engine_unreachable", error.message + " Réessaie dans un instant ou contacte l’administrateur.")]
        flags = read_engine_flags(self.config_path)
        if not flags.readable:
            return [ReadinessIssue(
                "config_unreadable",
                "Lody ne peut pas lire la configuration du moteur : les clés ne sont pas vérifiées à l’avance. "
                "Si l’une manque, la génération s’arrêtera dès l’étape concernée, avant tout appel payant.",
                blocking=False)]
        issues: list[ReadinessIssue] = []
        if not request.script.strip() and request.text_provider != "manual":
            if request.text_provider == "openai" and flags.llm_provider != "openai":
                issues.append(ReadinessIssue(
                    "text_engine_mismatch",
                    "Le moteur écrit ses scripts avec un autre fournisseur que celui du projet (OpenAI). "
                    "L’administrateur doit régler `llm_provider = \"openai\"` dans la configuration du moteur, "
                    "ou tu peux fournir ton propre script."))
            elif flags.llm_key_known and not flags.llm_key_filled:
                issues.append(ReadinessIssue("text_key_missing", "La clé du fournisseur de texte n’est pas configurée côté serveur."))
        if request.visual_provider == "openai_image":
            if not flags.image_endpoint_set:
                issues.append(ReadinessIssue("image_endpoint_missing", "La génération d’images n’est pas configurée côté serveur."))
            elif not flags.image_key_filled and flags.image_public_openai:
                issues.append(ReadinessIssue("image_key_missing", "La clé du fournisseur d’images n’est pas configurée côté serveur."))
        elif request.visual_provider not in _VIDEO_SOURCES:
            issues.append(ReadinessIssue("visual_unsupported", "Ce type de visuels n’est pas encore pris en charge par le moteur."))
        if request.voice.provider == "elevenlabs" and not flags.elevenlabs_key_filled:
            issues.append(ReadinessIssue("voice_key_missing", "La clé du fournisseur de voix n’est pas configurée côté serveur."))
        if request.music_provider == "elevenlabs" and not flags.elevenlabs_key_filled:
            issues.append(ReadinessIssue("music_key_missing", "La clé du fournisseur de musique n’est pas configurée côté serveur."))
        if (request.voice.provider == "elevenlabs" and request.voice.model and flags.elevenlabs_model
                and request.voice.model != flags.elevenlabs_model):
            issues.append(ReadinessIssue(
                "voice_model_differs",
                f"Le projet demande le modèle de voix « {request.voice.model} » mais le moteur utilisera "
                f"« {flags.elevenlabs_model} » (configuration du serveur).", blocking=False))
        return issues

    def write_script(self, request: GenerationRequest) -> str:
        payload = {
            "video_subject": request.subject, "video_language": request.language, "paragraph_number": 1,
            "video_script_prompt": script_prompt(request),
        }
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
        payload = build_payload(request, read_engine_flags(self.config_path))
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
