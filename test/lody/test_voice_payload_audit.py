"""Bug voix — garantir l'identifiant snapshoté jusqu'au payload TTS (ticket #75).

Contexte réel : une production du projet PNJ a été envoyée au moteur avec l'ancienne voix de Gaston alors
qu'Eli (voix ElevenLabs valide, `external_voice_id="goccsFDjQ0kcbRoOsQ2r"`) était sélectionné comme
personnage principal. Ce fichier prouve, avec le VRAI connecteur (`mpt_connector.MoneyPrinterTurboConnector`,
faux transport HTTP qui capture le payload sans jamais appeler un service réel), que le payload
``POST /api/v1/videos`` contient exactement la voix snapshotée d'Eli — jamais la voix projet, utilisée ici
comme SENTINELLE volontairement distincte (« ancienne voix de Gaston »).

Complète (sans le remplacer) ``test_voice_resolution.py`` (#70, résolution de la voix) en auditant la SUITE
du chemin : ``narrative_context`` → résolution → snapshot → ``GenerationRequest`` → ``build_payload()`` →
payload envoyé. Faux moteur uniquement (transport HTTP factice) : aucun appel réel à ElevenLabs.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from lody.characters import CharacterRepository
from lody.generation import mpt_connector as mpt
from lody.generation.costing import PriceBook
from lody.generation.models import VoiceSpec
from lody.generation.service import LaunchError, ProductionService, request_of
from lody.generation.store import ProductionRepository
from lody.locations import LocationRepository
from lody.projects import DEFAULTS, ProjectRepository
from lody.secrets_guard import find_secret_path
from test.lody.fakes import DeferredExecutor, SyncExecutor
from test.lody.test_generation_connector import CONFIG, TASK, _connector

PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.3"), ("music", "elevenlabs"): Decimal("0.1")})
SUBJECT = "Contrôle #75 : explique en une phrase ce qu'est un bloc dans une blockchain, pour un débutant curieux."
SCRIPT = ("Bienvenue dans le monde des blocs. Chaque bloc contient des informations vérifiées par le réseau. "
         "Une fois validé, un bloc rejoint la chaîne pour toujours.")

ELI_VOICE_ID = "goccsFDjQ0kcbRoOsQ2r"
ELI_VOICE_NAME = "Eli — PNJ Conscient"
GASTON_SENTINEL_ID = "GASTON-OLD-SENTINEL9"  # voix PROJET : ne doit JAMAIS apparaître dans le payload d'Eli
GASTON_SENTINEL_NAME = "Gaston — ancienne voix (SENTINELLE, ne doit jamais être envoyée)"

PING_RESPONSE = (200, {})  # GET /ping : réponse du préflight, appelée par confirm() AVANT tout envoi
VIDEOS_RESPONSE = (200, {"status": 200, "data": {"task_id": TASK}})


class Env:
    """Projet PNJ-like, voix projet = SENTINELLE Gaston, service câblé sur le VRAI connecteur moteur avec un
    faux transport HTTP (capture chaque requête, ne contacte jamais un service réel)."""

    def __init__(self, tmp_path, responses=None):
        self.tmp_path = tmp_path
        self.path = tmp_path / "lody.sqlite3"
        self.projects = ProjectRepository(self.path)
        self.characters = CharacterRepository(self.path)
        self.locations = LocationRepository(self.path)
        fields = {**copy.deepcopy(DEFAULTS), "name": "PNJ", "language": "fr-FR", "text_provider": "openai",
                  "visual_provider": "openai_image", "voice_provider": "elevenlabs",
                  "voice_name": GASTON_SENTINEL_NAME, "music_provider": "none",
                  "settings": {"brief": {"voice_id": GASTON_SENTINEL_ID, "voice_model": "eleven_multilingual_v2"}}}
        self.project = self.projects.create(**fields)
        # Ordre exact des appels HTTP d'une confirmation réussie : GET /ping (préflight, dans confirm() lui
        # même) PUIS POST /api/v1/videos (submit(), dans _run()) — jamais l'inverse, jamais fusionnés.
        self.connector, self.transport = _connector(tmp_path, *(responses or [PING_RESPONSE, VIDEOS_RESPONSE]),
                                                     config=CONFIG)
        self.executor = SyncExecutor()
        self.service = self._service()

    def _service(self, executor=None):
        return ProductionService(
            ProductionRepository(self.path), {mpt.PROVIDER_ID: self.connector}, executor or self.executor,
            price_book=lambda: PRICES, character_repo=self.characters, location_repo=self.locations,
        )

    def restart(self, executor=None):
        """Nouveau processus : mêmes données, mémoire vierge."""
        self.service = self._service(executor)
        return self.service

    def add_eli(self, **overrides):
        fields = {"name": "Eli", "is_primary": True, "voice_provider": "elevenlabs",
                  "external_voice_id": ELI_VOICE_ID, "voice_name": ELI_VOICE_NAME, **overrides}
        return self.characters.create(self.project.id, **fields)

    def videos_payload(self):
        """Le corps JSON de la (dernière) requête POST /api/v1/videos capturée — jamais /api/v1/scripts."""
        calls = [call for call in self.transport.requests if call[1].endswith("/api/v1/videos")]
        assert calls, "aucune requête POST /api/v1/videos capturée"
        return calls[-1][2]


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# ======================================================================================================================
# -- scénario de régression exact du ticket : Eli principal, sentinelle Gaston côté projet -----------------------------
# ======================================================================================================================
def test_eli_primary_with_gaston_sentinel_produces_a_payload_with_elis_voice(env):  # noqa: F811
    """LE test demandé par le ticket #75. Preuve que le payload POST /api/v1/videos contient
    exactement goccsFDjQ0kcbRoOsQ2r — jamais la sentinelle projet."""
    eli = env.add_eli()
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[eli.id])
    assert request_of(draft).voice.voice_id == ELI_VOICE_ID  # déjà correct avant confirmation (#70)

    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "EN_FILE"  # bien envoyé, aucun refus

    payload = env.videos_payload()
    assert payload["voice_name"] == f"elevenlabs:{ELI_VOICE_ID}:{ELI_VOICE_NAME}"
    assert ELI_VOICE_ID in payload["voice_name"]
    assert GASTON_SENTINEL_ID not in payload["voice_name"]
    assert "Gaston" not in payload["voice_name"]

    # Concordance aperçu / snapshot / params.request.voice / trace / payload — tous identiques.
    stored = env.service.repo.get(launched.id)
    assert stored.snapshot["request"]["voice"]["voice_id"] == ELI_VOICE_ID
    assert stored.params["request"]["voice"]["voice_id"] == ELI_VOICE_ID
    assert stored.trace["voice"]["voice_id"] == ELI_VOICE_ID
    assert stored.trace["voice"]["source"] == "character"
    assert stored.params["voice_resolution"]["character_name"] == "Eli"


def test_eli_alone_without_is_primary_still_uses_his_voice_in_the_payload(env):  # noqa: F811
    eli = env.add_eli(is_primary=False)
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[eli.id])
    env.service.confirm(draft.id, accept_partial=True)
    assert ELI_VOICE_ID in env.videos_payload()["voice_name"]


def test_several_characters_one_primary_the_primary_s_voice_reaches_the_payload(env):  # noqa: F811
    eli = env.add_eli()  # principal
    zoe = env.characters.create(env.project.id, name="Zoé", is_primary=False, voice_provider="elevenlabs",
                                external_voice_id="ZOEZOEZOEZOEZOEZOEZ", voice_name="Zoé")
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[eli.id, zoe.id])
    env.service.confirm(draft.id, accept_partial=True)
    payload_voice = env.videos_payload()["voice_name"]
    assert ELI_VOICE_ID in payload_voice
    assert "ZOEZOEZOEZOEZOEZOEZ" not in payload_voice


def test_incomplete_character_voice_falls_back_to_the_project_sentinel_safely(env):  # noqa: F811
    """Repli sûr et EXPLICITE : la voix projet (même « sentinelle ») est un repli légitime quand le
    personnage n'a pas de voix exploitable — seul le cas Eli/principal ne doit jamais y retomber."""
    incomplet = env.characters.create(env.project.id, name="Contrôle incomplet", is_primary=True,
                                      voice_provider="", external_voice_id="")
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[incomplet.id])
    assert draft.params["voice_resolution"]["fallback"] is True
    env.service.confirm(draft.id, accept_partial=True)
    assert GASTON_SENTINEL_ID in env.videos_payload()["voice_name"]


def test_unsupported_provider_falls_back_to_the_project_voice_in_the_payload(env):  # noqa: F811
    """Fournisseur non reconnu (donnée historique/corrompue) : repli, jamais un échec, jamais un envoi avec
    un fournisseur invalide."""
    import sqlite3

    eli = env.add_eli(voice_provider="elevenlabs")  # créé valide...
    connection = sqlite3.connect(env.path)
    connection.execute("UPDATE characters SET voice_provider = 'myspace-voice' WHERE id = ?", (eli.id,))
    connection.commit()
    connection.close()
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[eli.id])
    assert draft.params["voice_resolution"]["fallback"] is True
    env.service.confirm(draft.id, accept_partial=True)
    assert GASTON_SENTINEL_ID in env.videos_payload()["voice_name"]


# ======================================================================================================================
# -- immuabilité, V2, reprise ---------------------------------------------------------------------------------------
# ======================================================================================================================
def test_snapshot_immutable_after_editing_eli_s_sheet_payload_still_carries_the_original_voice(env):  # noqa: F811
    eli = env.add_eli()
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[eli.id])
    env.characters.update(env.project.id, eli.id, voice_provider="edge", external_voice_id="",
                          voice_name="Voix totalement différente")
    env.characters.deactivate(env.project.id, eli.id)

    env.service.confirm(draft.id, accept_partial=True)
    assert ELI_VOICE_ID in env.videos_payload()["voice_name"]


def test_v2_payload_carries_the_same_voice_as_v1(env):  # noqa: F811
    eli = env.add_eli()
    v1 = env.service.confirm(
        env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                            character_ids=[eli.id]).id,
        accept_partial=True,
    )
    v1_voice_payload = env.videos_payload()["voice_name"]
    assert ELI_VOICE_ID in v1_voice_payload

    before = len(env.transport.requests)
    v2 = env.service.create_v2(v1.id)  # ne lit/copie que des données déjà en base : aucun appel au moteur
    assert len(env.transport.requests) == before
    assert request_of(v2).voice.voice_id == ELI_VOICE_ID


def _sent_paths(transport):
    """Chemins des requêtes HTTP capturées, dans l'ordre — pour distinguer un ping (préflight, gratuit)
    d'un envoi réel (script ou vidéo, ce qui compte pour « aucun appel payant »)."""
    return [url.removeprefix("http://engine:8080") for _method, url, _body, _headers in transport.requests]


def test_restart_before_send_never_double_submits_or_loses_the_voice(tmp_path):  # noqa: F811
    """Reprise après redémarrage (exécuteur différé, comme test_generation_service.py) : le worker n'a
    jamais tourné avant le redémarrage — la reprise la marque ECHEC/interrompue plutôt que de la rejouer,
    jamais un second appel, jamais une voix perdue. ``confirm()`` déclenche un GET /ping (préflight, gratuit
    — préparé séparément) ; ni /api/v1/scripts ni /api/v1/videos ne doivent jamais être appelés ici."""
    env = Env(tmp_path, responses=[PING_RESPONSE])  # jamais de réponse /api/v1/videos : elle ne doit pas servir
    env.executor = DeferredExecutor()
    env.service = env._service()
    eli = env.add_eli()
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[eli.id])
    env.service.confirm(draft.id, accept_partial=True)  # préflight (ping) exécuté ; _run() mis en file, jamais lancé
    assert _sent_paths(env.transport) == ["/ping"]  # aucun envoi réel avant la reprise

    env.restart(executor=SyncExecutor())
    env.service.resume_active()
    resumed = env.service.repo.get(draft.id)
    assert resumed.status.value == "ECHEC" and resumed.error_code == "interrupted"
    assert _sent_paths(env.transport) == ["/ping"]  # toujours aucun /api/v1/scripts ni /api/v1/videos
    assert request_of(resumed).voice.voice_id == ELI_VOICE_ID  # jamais perdue, même sur une reprise échouée


# ======================================================================================================================
# -- non-régression du chemin historique sans sélection -----------------------------------------------------------
# ======================================================================================================================
def test_no_selection_payload_is_strictly_identical_to_before_69_70_75(env):  # noqa: F811
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT)
    env.service.confirm(draft.id, accept_partial=True)
    payload_voice = env.videos_payload()["voice_name"]
    assert payload_voice == f"elevenlabs:{GASTON_SENTINEL_ID}:{GASTON_SENTINEL_NAME}"


def test_no_secret_anywhere_in_the_preview_trace_or_payload(env):  # noqa: F811
    eli = env.add_eli()
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[eli.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    stored = env.service.repo.get(launched.id)
    assert find_secret_path(stored.trace) is None
    assert find_secret_path(stored.params) is None
    assert find_secret_path(env.videos_payload()) is None


# ======================================================================================================================
# -- la vérification anti-divergence elle-même (#75) : bloque avant tout envoi, jamais un appel ----------------------
# ======================================================================================================================
def test_a_corrupted_snapshot_voice_is_intercepted_by_check_isolation_before_any_call(env):  # noqa: F811
    """Simule directement l'invariant rompu (un futur bug hypothétique) en corrompant le snapshot ENTRE la
    préparation et la confirmation — la seule fenêtre où le snapshot reste modifiable (voir
    store.SNAPSHOT_WRITABLE_FROM).

    ``_check_isolation`` (garde-fou déjà existant, comparant l'ensemble des paramètres « projet » — voix
    comprise — entre ``params.request`` et ``snapshot.request``) intercepte cette divergence EN PREMIER,
    dès ``confirm()``, avant même le préflight (ping) — donc avant que ``_run()``/``_assert_voice_unchanged``
    (#75) n'aient la moindre chance de s'exécuter. ``confirm()`` lève directement (jamais un échec silencieux
    enregistré en base) : le brouillon reste EN_ATTENTE_CONFIRMATION, aucune requête HTTP n'a lieu — ni
    /ping, ni /api/v1/scripts, ni /api/v1/videos."""
    eli = env.add_eli()
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[eli.id])
    corrupted_snapshot = copy.deepcopy(draft.snapshot)
    corrupted_snapshot["request"]["voice"] = {"provider": "elevenlabs", "voice_id": GASTON_SENTINEL_ID,
                                              "name": GASTON_SENTINEL_NAME, "model": "eleven_multilingual_v2"}
    assert env.service.repo.transition(draft.id, [draft.status], snapshot=corrupted_snapshot)

    with pytest.raises(LaunchError, match="instantané"):
        env.service.confirm(draft.id, accept_partial=True)

    untouched = env.service.repo.get(draft.id)
    assert untouched.status.value == "EN_ATTENTE_CONFIRMATION"  # jamais transitionné
    assert env.transport.requests == []  # AUCUN appel, ni /ping, ni /api/v1/scripts, ni /api/v1/videos


def test_assert_voice_unchanged_raises_on_divergence_and_accepts_a_match(env):  # noqa: F811
    """La fonction pure elle-même, isolée : lève sur divergence, ne lève pas quand tout concorde. Exercée
    sur un VRAI brouillon (construire un ``Production`` à la main serait fragile face à ses nombreux champs)."""
    eli = env.add_eli()
    draft = env.service.prepare(env.project, SUBJECT, provider_id=mpt.PROVIDER_ID, script=SCRIPT,
                                character_ids=[eli.id])
    request = request_of(draft)

    ProductionService._assert_voice_unchanged(draft, request)  # concorde : ne lève pas

    diverging = request.with_updates(voice=VoiceSpec("elevenlabs", GASTON_SENTINEL_ID, GASTON_SENTINEL_NAME,
                                                      request.voice.model))
    with pytest.raises(LaunchError):
        ProductionService._assert_voice_unchanged(draft, diverging)
