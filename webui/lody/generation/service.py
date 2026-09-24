"""Orchestration d'une production : préparation, confirmation unique, exécution, suivi, V2.

Indépendant de Streamlit et de tout moteur précis (il ne parle qu'à ``VideoGenerationProvider``).

Cycle de vie (statuts internes) :

    BROUILLON → EN_ATTENTE_CONFIRMATION → CONFIRMEE → EN_COURS (script, scènes)
        → EN_FILE (envoyée au moteur) → EN_COURS → TERMINEE | ECHEC

La confirmation est une transition SQL atomique : un double clic ne lance qu'une fois. Un lancement
n'est **jamais rejoué automatiquement** (chaque appel est potentiellement payant) : une production
interrompue avant l'envoi devient ECHEC et se relance par une nouvelle confirmation.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any, Protocol

from lody import brief as brief_lib
from lody import catalog
from lody.characters import CharacterRepository
from lody.generation import kit_files, publication, thumbnail, typography
from lody.generation.costing import CostEstimate, PriceBook, estimate_cost, load_price_book
from lody.generation.models import (
    ACTIVE_STATUSES,
    Capability,
    CapabilityState,
    ErrorKind,
    ExternalTask,
    GenerationRequest,
    GenerationResult,
    PreflightReport,
    ProductionStatus,
    ProviderError,
    ReadinessIssue,
    RemoteState,
    TaskSnapshot,
    VoiceSpec,
)
from lody.generation.narrative_context import (
    NarrativeContextError,
    enrich_visual_prompts,
    reference_images_status,
    render_prompt_block,
    resolve_narrative_context,
    resolve_voice,
)
from lody.generation.provider import VideoGenerationProvider
from lody.generation.safety import sanitize
from lody.generation.storyboard import MAX_SCENES, build_storyboard
from lody.generation.store import Production, ProductionRepository
from lody.locations import LocationRepository
from lody.projects import Project
from lody.secrets_guard import SECRET_MESSAGE, find_secret_path

logger = logging.getLogger("lody.production")

SUBJECT_MIN, SUBJECT_MAX = 8, 2000
SCRIPT_MIN, SCRIPT_MAX = 40, 8000
EDITABLE_STATUSES = (ProductionStatus.BROUILLON, ProductionStatus.EN_ATTENTE_CONFIRMATION)


class LaunchError(Exception):
    """Refus lisible : le message est destiné à l'utilisateur."""

    def __init__(self, message: str, issues: list[ReadinessIssue] | None = None):
        super().__init__(message)
        self.message = message
        self.issues = issues or []


class InvalidInput(LaunchError):
    pass


class AlreadyRunning(LaunchError):
    def __init__(self, message: str, production_id: str):
        super().__init__(message)
        self.production_id = production_id


class Executor(Protocol):
    def submit(self, fn: Callable[..., Any], *args: Any) -> Any: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_request(project: Project, subject: str, script: str = "") -> GenerationRequest:
    """Demande indépendante du moteur, construite depuis les paramètres du projet."""
    settings = brief_lib.brief_settings(project.settings)
    return GenerationRequest(
        subject=subject.strip(), language=project.language, aspect=project.format,
        duration_min=settings["duration_min"], duration_max=settings["duration_max"],
        tone=project.tone, audience=settings["audience"], orientation=settings["orientation"],
        narration_pace=settings["narration_pace"], structure=tuple(settings["structure"]),
        instructions=settings["standing_instructions"], visual_style=project.visual_style,
        visual_rules=settings["visual_rules"], visual_avoid=tuple(settings["visual_avoid"]),
        scenes_per_minute_min=settings["scenes_per_minute_min"], scenes_per_minute_max=settings["scenes_per_minute_max"],
        voice=VoiceSpec(project.voice_provider, settings["voice_id"], project.voice_name, settings["voice_model"]),
        text_provider=project.text_provider, visual_provider=project.visual_provider,
        music_provider=project.music_provider, script=script.strip(),
    )


_PER_RUN_FIELDS = ("subject", "script", "visual_prompts")  # propres à la production, pas au projet
_PROJECT_ORIGIN = "projet"
_ORIGIN_OF_RUN_FIELD = {"subject": "saisi par l’utilisateur", "script": "écrit par le moteur, ou fourni par l’utilisateur",
                        "visual_prompts": "dérivés du script par Lody"}


def project_part(request: dict[str, Any]) -> dict[str, Any]:
    """Les paramètres du projet contenus dans une demande (sans sujet, script ni prompts de la production)."""
    return {key: value for key, value in request.items() if key not in _PER_RUN_FIELDS}


def make_snapshot(project: Project, request: GenerationRequest, captured_at: str, inherited_from: str | None = None,
                   narrative_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Instantané IMMUABLE des paramètres du projet au moment de la préparation.

    Copie profonde (JSON) : aucun objet n'est partagé avec le projet ni avec une autre production. ``origins``
    dit d'où vient chaque valeur ; ``version`` est l'empreinte des seuls paramètres du projet.

    ``narrative_context`` (ticket #35) : instantané déjà résolu des personnages et du lieu sélectionnés, ou
    ``{}``/``None`` sans sélection — auquel cas le reste du snapshot est fonctionnellement identique à avant #35.
    """
    import copy
    import hashlib
    import json

    data = copy.deepcopy(request.to_dict())
    frozen = project_part(data)
    version = hashlib.sha256(json.dumps({"project": project.id, "params": frozen}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]
    origins = {key: _PROJECT_ORIGIN for key in frozen}
    origins.update({key: origin for key, origin in _ORIGIN_OF_RUN_FIELD.items() if key in data})
    return {"version": version, "captured_at": captured_at, "inherited_from": inherited_from,
            "project": {"id": project.id, "name": project.name, "updated_at": project.updated_at},
            "request": data, "origins": origins, "narrative_context": copy.deepcopy(narrative_context) or {}}


def request_of(production: Production) -> GenerationRequest:
    return GenerationRequest.from_dict(production.params.get("request", {}))


def validate_subject(subject: str) -> str:
    text = " ".join(str(subject or "").split())
    if len(text) < SUBJECT_MIN:
        raise InvalidInput("Décris ta vidéo en quelques mots (au moins 8 caractères).")
    if len(text) > SUBJECT_MAX:
        raise InvalidInput("Le sujet est trop long (2 000 caractères au maximum).")
    if find_secret_path(text):
        raise InvalidInput(SECRET_MESSAGE)
    return text


def validate_script(script: str) -> str:
    text = str(script or "").strip()
    if len(text) < SCRIPT_MIN:
        raise InvalidInput("Le script est trop court pour produire une vidéo.")
    if len(text) > SCRIPT_MAX:
        raise InvalidInput("Le script est trop long (8 000 caractères au maximum).")
    if find_secret_path(text):
        raise InvalidInput(SECRET_MESSAGE)
    return text


class ProductionService:
    def __init__(self, repo: ProductionRepository, providers: dict[str, VideoGenerationProvider],
                 executor: Executor, price_book: Callable[[], PriceBook] = load_price_book,
                 clock: Callable[[], str] = _now, character_repo: CharacterRepository | None = None,
                 location_repo: LocationRepository | None = None):
        self.repo = repo
        self._providers = providers
        self._executor = executor
        self._price_book = price_book
        self._clock = clock
        # Facultatifs (#35) : sans eux, prepare() n'accepte aucune sélection de personnage/lieu (comportement
        # inchangé). Aucun écran ne les propose encore (#34) ; runtime.build_service() les fournit déjà.
        self._character_repo = character_repo
        self._location_repo = location_repo
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    # -- accès ----------------------------------------------------------------
    def provider(self, provider_id: str) -> VideoGenerationProvider:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise LaunchError("Ce moteur de génération n’est pas disponible.") from None

    def preflight(self, production: Production) -> PreflightReport:
        """Contrôle complet (sans coût) de ce que cette production exige."""
        return self.provider(production.provider).preflight(request_of(production))

    def project_preflight(self, project: Project, provider_id: str, script: str = "") -> PreflightReport:
        """Même contrôle, pour les paramètres du projet (avant tout sujet)."""
        return self.provider(provider_id).preflight(build_request(project, "", script))

    def option_states(self, project: Project | None, provider_id: str) -> dict[tuple[str, str], CapabilityState]:
        """État réel de chaque fournisseur proposé (configuré / à configurer / indisponible / non vérifié).

        Chaque option est contrôlée telle quelle, sans jamais en substituer une autre à l'utilisateur.
        """
        base = build_request(project, "", "") if project else GenerationRequest(
            subject="", voice=VoiceSpec("elevenlabs", "probe", "probe", ""))
        provider = self.provider(provider_id)
        table: dict[tuple[str, str], CapabilityState] = {}
        for kind, options, field, capability in (
            ("text", catalog.TEXT_PROVIDERS, "text_provider", Capability.TEXT),
            ("visual", catalog.VISUAL_PROVIDERS, "visual_provider", Capability.VISUAL),
            ("voice", catalog.VOICE_PROVIDERS, "voice", Capability.VOICE),
            ("music", catalog.MUSIC_PROVIDERS, "music_provider", Capability.MUSIC),
        ):
            for option in options:
                if kind == "text" and option.value == "manual":
                    table[(kind, option.value)] = CapabilityState.NOT_NEEDED  # « je fournis mon script » : toujours possible
                    continue
                voice = VoiceSpec(option.value, base.voice.voice_id or "probe", base.voice.name, base.voice.model)
                request = base.with_updates(voice=voice) if kind == "voice" else base.with_updates(**{field: option.value})
                item = provider.preflight(request).get(capability)
                table[(kind, option.value)] = item.state if item else CapabilityState.UNVERIFIED
        return table

    def readiness(self, production: Production) -> list[ReadinessIssue]:
        return self.preflight(production).to_issues()

    # -- préparation (aucun appel payant) -------------------------------------
    def _plan(self, provider: VideoGenerationProvider, request: GenerationRequest) -> tuple[CostEstimate, dict[str, Any]]:
        estimate = estimate_cost(request, provider.plan_units(request), self._price_book())
        fields = {
            "cost_currency": estimate.currency, "cost_low": estimate.total_low, "cost_high": estimate.total_high,
            "cost_partial": estimate.partial, "cost_detail": estimate.to_detail(),
        }
        return estimate, fields

    def _scene_count(self, provider: VideoGenerationProvider, request: GenerationRequest) -> int:
        low, high = provider.plan_units(request).scenes
        return max(1, min(MAX_SCENES, round((low + high) / 2)))

    def _resolve_narrative_context(self, project_id: str, character_ids: Sequence[str],
                                    location_id: str | None) -> dict[str, Any]:
        """Traduit la sélection facultative (#35) en snapshot immuable, ou lève ``LaunchError`` si invalide."""
        if not character_ids and not location_id:
            return {}  # cas le plus courant : comportement strictement inchangé
        if self._character_repo is None or self._location_repo is None:
            raise LaunchError("La sélection de personnages ou de lieux n'est pas disponible pour cette production.")
        try:
            return resolve_narrative_context(project_id, character_ids, location_id,
                                             self._character_repo, self._location_repo)
        except NarrativeContextError as error:
            raise LaunchError(str(error)) from error

    def prepare(self, project: Project, subject: str, *, provider_id: str, script: str = "",
                draft_id: str | None = None, retry_of: str | None = None,
                character_ids: Sequence[str] = (), location_id: str | None = None) -> Production:
        """Crée (ou met à jour) un brouillon avec son estimation : EN_ATTENTE_CONFIRMATION.

        ``retry_of`` = une production échouée du même projet : la nouvelle tentative lui est *liée* (elle ne
        l'écrase jamais) et repart d'une estimation et d'une confirmation neuves.

        ``character_ids``/``location_id`` (#35, facultatifs) : sélection résolue dans le projet courant et
        copiée dans l'instantané (``snapshot.narrative_context``). Non modifiée par les éditions ultérieures
        du projet. Sans sélection (le cas de toute l'interface actuelle, #34 n'existant pas encore) : aucun
        changement de comportement.
        """
        text = validate_subject(subject)
        script_text = typography.normalize_for_language(validate_script(script), project.language) if script.strip() else ""
        provider = self.provider(provider_id)
        request = build_request(project, text, script_text)
        narrative_context = self._resolve_narrative_context(project.id, character_ids, location_id)
        # #70 (MVP mono-voix, prérequis de #39) : au maximum un personnage de référence parmi les
        # SÉLECTIONNÉS remplace la voix du projet, avec repli sûr et toujours consigné — voir
        # narrative_context.resolve_voice. Appliqué AVANT _plan()/make_snapshot() : l'estimation et
        # l'instantané reflètent la voix EFFECTIVEMENT prévue, jamais recalculée après confirmation.
        resolved_voice, voice_origin = resolve_voice(request.voice, narrative_context)
        if resolved_voice != request.voice:
            request = request.with_updates(voice=resolved_voice)
        _, cost = self._plan(provider, request)
        fields: dict[str, Any] = {
            "brief": brief_lib.build_brief(project, text), "script": script_text,
            "script_source": "manual" if script_text else "",
            "params": {"request": request.to_dict(), "engine": provider.describe_params(request),
                      # #38 : jamais une transmission simulée pour un fournisseur qui ne la supporte pas —
                      # voir narrative_context.reference_images_status et provider.supports_reference_images.
                      "reference_images": reference_images_status(narrative_context, provider.supports_reference_images),
                      # #70 : origine de la voix effective (personnage ou projet) et éventuel repli — jamais
                      # un secret, voir narrative_context.resolve_voice.
                      "voice_resolution": voice_origin},
            "storyboard": [], "visual_prompts": [], "current_step": "En attente de confirmation",
            "error_code": "", "error_message": "", "trace": {},
            "snapshot": make_snapshot(project, request, self._clock(), inherited_from=retry_of,
                                      narrative_context=narrative_context), **cost,
        }
        if retry_of:
            failed = self.repo.get(retry_of)
            if failed.project_id != project.id or failed.status is not ProductionStatus.ECHEC:
                retry_of = None
        existing = None
        if draft_id:
            try:
                existing = self.repo.get(draft_id)
            except LookupError:
                existing = None
        if (existing and existing.project_id == project.id and existing.status in EDITABLE_STATUSES
                and existing.parent_production_id == retry_of and existing.provider == provider_id):
            if self.repo.transition(existing.id, EDITABLE_STATUSES, subject=text,
                                    status=ProductionStatus.EN_ATTENTE_CONFIRMATION, **fields):
                return self.repo.get(existing.id)
        return self.repo.create(project_id=project.id, subject=text, provider=provider_id,
                                status=ProductionStatus.EN_ATTENTE_CONFIRMATION,
                                parent_production_id=retry_of, **fields)

    # -- V2 ---------------------------------------------------------------------
    def create_v2(self, parent_id: str) -> Production:
        """Brouillon lié à la version précédente (qui reste intacte)."""
        parent = self.repo.get(parent_id)
        if parent.status is not ProductionStatus.TERMINEE and not parent.script:
            raise LaunchError("Seule une production terminée (ou dont le script existe) peut avoir une V2.")
        import copy

        inherited = copy.deepcopy(parent.snapshot) or {
            "version": "", "captured_at": parent.created_at, "project": {"id": parent.project_id, "name": ""},
            "request": copy.deepcopy(parent.params.get("request", {})), "origins": {}, "narrative_context": {}}
        inherited["inherited_from"] = parent.id  # la V2 reprend les paramètres de sa version précédente, pas ceux du projet actuel
        return self.repo.create(
            project_id=parent.project_id, subject=parent.subject, provider=parent.provider,
            status=ProductionStatus.BROUILLON, parent_production_id=parent.id, brief=parent.brief,
            script=parent.script, script_source="previous", params=parent.params, storyboard=parent.storyboard,
            visual_prompts=parent.visual_prompts, snapshot=inherited, cost_currency=parent.cost_currency, cost_low=parent.cost_low,
            cost_high=parent.cost_high, cost_partial=parent.cost_partial, cost_detail=parent.cost_detail,
            current_step="Brouillon de V2",
        )

    def prepare_v2(self, production_id: str, script: str) -> Production:
        """Enregistre le script modifié, recalcule durée et coût, attend une NOUVELLE confirmation."""
        draft = self.repo.get(production_id)
        if draft.status not in EDITABLE_STATUSES or not draft.parent_production_id:
            raise LaunchError("Cette version ne peut plus être modifiée.")
        provider = self.provider(draft.provider)
        text = typography.normalize_for_language(validate_script(script), request_of(draft).language)
        request = request_of(draft).with_updates(script=text, visual_prompts=())
        _, cost = self._plan(provider, request)
        parent = self.repo.get(draft.parent_production_id)
        scenes = build_storyboard(text, self._scene_count(provider, request), visual_style=request.visual_style,
                                  aspect=request.aspect, narration_pace=request.narration_pace,
                                  visual_rules=request.visual_rules, visual_avoid=request.visual_avoid)
        fields = {
            "script": text, "script_source": "previous" if text == parent.script.strip() else "manual",
            "params": {"request": request.to_dict(), "engine": provider.describe_params(request)},
            "storyboard": [scene.to_dict() for scene in scenes],
            "visual_prompts": [scene.prompt for scene in scenes],
            "current_step": "En attente de confirmation", "error_code": "", "error_message": "", **cost,
        }
        if not self.repo.transition(draft.id, EDITABLE_STATUSES, status=ProductionStatus.EN_ATTENTE_CONFIRMATION, **fields):
            raise LaunchError("Cette version vient d’être lancée ou modifiée ailleurs.")
        return self.repo.get(draft.id)

    @staticmethod
    def _check_isolation(production: Production) -> None:
        """Instantané présent, du bon projet, et cohérent avec les paramètres qui seront réellement envoyés."""
        snapshot = production.snapshot
        if not snapshot:
            raise LaunchError("Cette estimation date d’avant l’instantané des paramètres du projet : prépare-la à nouveau.")
        if snapshot.get("project", {}).get("id") != production.project_id:
            raise LaunchError("Cette production n’appartient pas au projet de son instantané : lancement refusé.")
        sent = project_part(production.params.get("request", {}))
        if sent != project_part(snapshot.get("request", {})):
            raise LaunchError("Les paramètres à envoyer diffèrent de l’instantané du projet : lancement refusé.")

    # -- confirmation unique -----------------------------------------------------
    def confirm(self, production_id: str, *, accept_partial: bool = False) -> Production:
        """Lance la génération. Idempotent : rappelée sur une production déjà lancée, elle ne relance rien."""
        production = self.repo.get(production_id)
        if production.status is not ProductionStatus.EN_ATTENTE_CONFIRMATION:
            return production
        self._check_isolation(production)
        # Une simulation n'a aucun coût réel : l'avertissement de total partiel ne s'y applique pas.
        if production.cost_partial and not accept_partial and not self.provider(production.provider).is_demo:
            raise LaunchError("Le total est partiel (tarif non configuré) : confirme en acceptant explicitement cet avertissement.")
        report = self.preflight(production)
        if not report.ready:  # rien n'est enregistré ni lancé : la production reste en attente de confirmation
            raise LaunchError("La génération ne peut pas partir : la configuration de production est incomplète.",
                              report.to_issues())
        for other in self.repo.active_for_project(production.project_id):
            if other.id != production.id:
                raise AlreadyRunning("Une génération est déjà en cours pour ce projet : attends sa fin avant d’en lancer une autre.", other.id)
        with self._lock:
            self._inflight.add(production.id)
        launched = self.repo.transition(
            production.id, [ProductionStatus.EN_ATTENTE_CONFIRMATION], status=ProductionStatus.CONFIRMEE,
            confirmed_at=self._clock(), current_step="Confirmée — démarrage", progress=None,
            error_code="", error_message="")
        if not launched:  # quelqu'un d'autre a confirmé dans l'intervalle
            with self._lock:
                self._inflight.discard(production.id)
            return self.repo.get(production.id)
        self._executor.submit(self.run, production.id)
        return self.repo.get(production.id)

    # -- exécution (worker) --------------------------------------------------------
    def run(self, production_id: str) -> None:
        try:
            self._run(production_id)
        finally:
            with self._lock:
                self._inflight.discard(production_id)

    def _run(self, production_id: str) -> None:
        if not self.repo.transition(production_id, [ProductionStatus.CONFIRMEE], status=ProductionStatus.EN_COURS,
                                    started_at=self._clock(), current_step="Écriture du script", progress=None):
            return
        production = self.repo.get(production_id)
        provider = self.provider(production.provider)
        try:
            self._check_isolation(production)  # les paramètres envoyés sont ceux de l'instantané de CE projet, jamais d'un autre
        except LaunchError as error:
            self._fail(production_id, ErrorKind.REJECTED, error.message)
            return
        try:
            request = request_of(production)
            # #36 : rendu UNIQUEMENT depuis le snapshot déjà figé de CETTE production (jamais les tables
            # characters/locations, qui peuvent avoir changé depuis) ; "" sans sélection ou snapshot antérieur
            # à #35 — le prompt reste alors identique à avant #36 (voir render_prompt_block). N'affecte ni la
            # voix ni les prompts d'images : seul script_request/write_script le reçoit, jamais build_payload.
            narrative_block = render_prompt_block(production.snapshot.get("narrative_context"))
            script_request = provider.describe_script_request(request, narrative_block) if not request.script else None
            if not request.script:
                script = typography.normalize_for_language(
                    provider.write_script(request, narrative_block).strip(), request.language)
                if not script:
                    raise ProviderError(ErrorKind.INVALID_RESPONSE, "Le script reçu est vide.", stage="script")
                request = request.with_updates(script=script)
                # Fusionne (jamais un écrasement complet) : préserve les clés de diagnostic déjà posées par
                # prepare() — "reference_images" (#38), "voice_resolution" (#70) — qu'aucun appel ultérieur
                # ne doit faire disparaître silencieusement.
                self.repo.update(production_id, script=script, script_source="generated",
                                 params={**production.params, "request": request.to_dict(),
                                        "engine": production.params.get("engine", {})})
            self.repo.update(production_id, current_step="Préparation des scènes")
            scenes = build_storyboard(request.script, self._scene_count(provider, request),
                                      visual_style=request.visual_style, aspect=request.aspect,
                                      narration_pace=request.narration_pace, visual_rules=request.visual_rules,
                                      visual_avoid=request.visual_avoid)
            if not scenes:
                raise ProviderError(ErrorKind.INVALID_RESPONSE, "Le script ne contient aucune phrase exploitable.", stage="script")
            # #37 : enrichit les prompts d'image des scènes déjà construites (mêmes scènes, même narration,
            # même nombre — donc même estimation de coût) avec la continuité visuelle du snapshot. "" sans
            # sélection ou snapshot antérieur à #35 : storyboard/prompts strictement inchangés (voir docstring).
            scenes = enrich_visual_prompts(scenes, production.snapshot.get("narrative_context"))
            request = request.with_updates(visual_prompts=[scene.prompt for scene in scenes])
            self.repo.update(
                production_id, storyboard=[scene.to_dict() for scene in scenes],
                visual_prompts=list(request.visual_prompts), current_step="Envoi au moteur",
                # Fusionne, comme ci-dessus : préserve "reference_images"/"voice_resolution" déjà posés par
                # prepare() (``production`` est le même objet en mémoire depuis le début de _run() : ses
                # clés de diagnostic n'ont jamais été perdues, même si l'écriture SQL ci-dessus les a déjà
                # réécrites une première fois).
                params={**production.params, "request": request.to_dict(), "engine": provider.describe_params(request)},
                trace=self._trace(production, provider, request, script_request))  # avant l'envoi : conservée même en cas d'échec
            task = provider.submit(request, production.idempotency_key)
            self.repo.update(production_id, external_task_id=task.task_id, status=ProductionStatus.EN_FILE,
                             current_step="Dans la file du moteur", progress=0, last_polled_at=self._clock())
            self._initial_thumbnail_background(production_id, provider, request)
        except ProviderError as error:
            self._fail(production_id, error.kind, error.message)
        except Exception as error:  # jamais de trace brute vers l'utilisateur ; détail nettoyé dans les logs
            logger.error("production %s : erreur inattendue (%s) %s", production_id, type(error).__name__, sanitize(error))
            self._fail(production_id, ErrorKind.PROVIDER, "Une erreur inattendue est survenue pendant la préparation.")

    def _initial_thumbnail_background(self, production_id: str, provider: VideoGenerationProvider, request: GenerationRequest) -> None:
        """Fond de miniature dédié : un appel d'image, INCLUS dans l'estimation initiale et la confirmation unique.

        Uniquement pour un moteur qui sait générer une image seule. Un échec n'arrête jamais la vidéo : la miniature
        retombe sur une image de scène (gratuite) et l'avertissement l'explique.
        """
        if not provider.supports_thumbnail_background:
            return
        production = self.repo.get(production_id)
        try:
            topic = publication.generate_metadata(publication.PublicationInput(
                project_name=production.snapshot.get("project", {}).get("name", ""), subject=request.subject,
                script=request.script, language=request.language))["title"]
            prompt = thumbnail.background_prompt(topic=topic, visual_style=request.visual_style, visual_rules=request.visual_rules,
                                                 visual_avoid=request.visual_avoid, aspect=request.aspect)
            ref = kit_files.save_background(production_id, provider.generate_thumbnail_background(request, prompt))
        except (ProviderError, ValueError, OSError) as error:
            message = error.message if isinstance(error, ProviderError) else "image de fond invalide"
            self.repo.update(production_id, warnings=[*production.warnings,
                                                      "Fond de miniature non généré (" + sanitize(message, 120) + ") : une image de scène sera utilisée."])
            return
        self.repo.update(production_id, assets=[*production.assets, {"kind": "thumbnail_background", "ref": ref, "root": "data"}],
                         trace={**production.trace, "thumbnail_background": {"prompt": prompt, "ref": ref}})

    def _trace(self, production: Production, provider: VideoGenerationProvider, request: GenerationRequest,
               script_request: dict[str, Any] | None) -> dict[str, Any]:
        """Ce qui est RÉELLEMENT envoyé, et d'où vient chaque élément (sans clé : aucun champ secret n'existe ici)."""
        detail = provider.trace_prompts(request)
        engine_template = detail.get("image_template", {})
        narrative_present = bool(production.snapshot.get("narrative_context"))  # #36/#37 : pour l'audit de l'injection
        script_origin = "projet — durée, ton, public, orientation, structure, consignes permanentes"
        scene_origin = "projet — style visuel, consignes visuelles, liste négative + passage du script"
        if narrative_present:
            script_origin += " ; personnages et lieu sélectionnés — instantané de production (#34/#35/#36)"
            scene_origin += " ; continuité visuelle des personnages/lieu présents — instantané de production (#34/#35/#37)"
        origins = [
            {"item": "Prompt éditorial du script", "origin": script_origin
             if script_request else "script fourni ou repris : aucun appel texte"},
            {"item": "Prompt système du script", "origin": "moteur — prompt par défaut (aucun prompt système personnalisé n'est envoyé)"},
            {"item": "Prompts de scène (envoyés par Lody)", "origin": scene_origin},
            {"item": "Gabarit d'images du moteur", "origin": engine_template.get("origin", "aucun")},
            {"item": "Police et réglages de sous-titres", "origin": "plateforme (connecteur) et moteur (section [ui] de la configuration)"},
            {"item": "Voix, langue, format, durée", "origin": "projet"},
        ]
        return {"recorded_at": self._clock(), "project": production.snapshot.get("project", {}),
                "snapshot_version": production.snapshot.get("version", ""), "script_request": script_request,
                "scenes": detail.get("scenes", []), "image_template": engine_template,
                "engine_params": provider.describe_params(request), "origins": origins}

    def _fail(self, production_id: str, kind: ErrorKind, message: str) -> None:
        self.repo.transition(
            production_id, [ProductionStatus.CONFIRMEE, ProductionStatus.EN_COURS, ProductionStatus.EN_FILE],
            status=ProductionStatus.ECHEC, error_code=kind.value, error_message=sanitize(message),
            finished_at=self._clock(), current_step="Échec")

    # -- suivi ---------------------------------------------------------------------
    def refresh(self, production_id: str) -> Production:
        """Interroge le moteur et met à jour la production. Sans effet sur une production terminée."""
        production = self.repo.get(production_id)
        if production.status not in ACTIVE_STATUSES:
            return production
        if not production.external_task_id:
            with self._lock:
                alive = production.id in self._inflight
            if not alive:  # process redémarré avant l'envoi : on ne relance jamais un appel payant seul
                self._fail(production.id, ErrorKind.INTERRUPTED,
                           "La génération a été interrompue avant l’envoi au moteur (redémarrage). "
                           "Rien n’a été relancé automatiquement : relance-la avec une nouvelle confirmation.")
                return self.repo.get(production.id)
            return production
        provider = self.provider(production.provider)
        task = ExternalTask(production.provider, production.external_task_id)
        try:
            snapshot = provider.poll(task)
        except ProviderError as error:
            # Moteur injoignable, délai, réponse invalide : la production n'est PAS perdue.
            self.repo.update(production.id, last_polled_at=self._clock(), error_code=error.kind.value,
                             error_message=sanitize(error.message))
            return self.repo.get(production.id)
        except Exception as error:
            logger.error("suivi %s : erreur inattendue (%s)", production.id, type(error).__name__)
            return production
        return self._apply(production, provider, task, snapshot)

    def _apply(self, production: Production, provider: VideoGenerationProvider, task: ExternalTask,
               snapshot: TaskSnapshot) -> Production:
        common = {"last_polled_at": self._clock(), "error_code": "", "error_message": ""}
        if snapshot.state is RemoteState.UNKNOWN:
            recovered = provider.recover(task)
            if recovered:
                return self._complete(production, provider, task, GenerationResult(
                    recovered.video_ref, recovered.duration_seconds, recovered.script, recovered.assets,
                    recovered.warnings))
            self._fail(production.id, ErrorKind.UNKNOWN_TASK,
                       "Le moteur ne connaît plus cette tâche (il a probablement redémarré) et aucune vidéo n’a été retrouvée. "
                       "Rien n’a été relancé automatiquement.")
        elif snapshot.state is RemoteState.FAILED and snapshot.error:
            self._fail(production.id, snapshot.error.kind, snapshot.error.message)
        elif snapshot.state is RemoteState.DONE and snapshot.result:
            return self._complete(production, provider, task, snapshot.result, snapshot.warnings)
        else:
            status = ProductionStatus.EN_FILE if snapshot.state is RemoteState.QUEUED else ProductionStatus.EN_COURS
            self.repo.transition(production.id, ACTIVE_STATUSES, status=status, progress=snapshot.progress,
                                 current_step=snapshot.step or production.current_step,
                                 warnings=list(dict.fromkeys([*production.warnings, *snapshot.warnings])), **common)
        return self.repo.get(production.id)

    def _complete(self, production: Production, provider: VideoGenerationProvider, task: ExternalTask,
                  result: GenerationResult, extra_warnings: tuple[str, ...] = ()) -> Production:
        try:
            provider.resolve_asset(task, result.video_ref)
        except ValueError:
            self._fail(production.id, ErrorKind.VIDEO_MISSING,
                       "La génération est annoncée terminée mais le fichier vidéo est introuvable ou non autorisé.")
            return self.repo.get(production.id)
        warnings = [sanitize(item, 160) for item in (*result.warnings, *extra_warnings)]
        self.repo.transition(
            production.id, ACTIVE_STATUSES, status=ProductionStatus.TERMINEE, progress=100, current_step="Terminée",
            finished_at=self._clock(), last_polled_at=self._clock(), error_code="", error_message="",
            video_ref=result.video_ref, video_duration=result.duration_seconds,
            assets=[*(dict(asset) for asset in production.assets if asset.get("kind") == "thumbnail_background"),
                    *(dict(asset) for asset in result.assets)],
            warnings=list(dict.fromkeys([*production.warnings, *warnings])))
        return self.repo.get(production.id)

    def resume_active(self) -> list[Production]:
        """Au démarrage : redemande l'état de chaque production non terminée (jamais « perdue » d'office)."""
        return [self.refresh(production.id) for production in self.repo.list_by_status(ACTIVE_STATUSES)]

    def resolve_asset(self, production: Production, ref: str):
        """Chemin local validé d'un fichier de CETTE production (ex. rendu corrigé), sinon ``ValueError``."""
        if not production.external_task_id:
            raise ValueError("aucune tâche")
        return self.provider(production.provider).resolve_asset(
            ExternalTask(production.provider, production.external_task_id), ref)

    def resolve_video(self, production: Production):
        """Chemin local validé de la vidéo finale de CETTE production, sinon ``ValueError``."""
        if production.status is not ProductionStatus.TERMINEE or not production.external_task_id or not production.video_ref:
            raise ValueError("aucune vidéo")
        return self.provider(production.provider).resolve_asset(
            ExternalTask(production.provider, production.external_task_id), production.video_ref)
