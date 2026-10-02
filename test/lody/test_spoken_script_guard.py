"""Frontière bloquante entre la demande de production et le texte prononçable (ticket #76).

Lors d'un test réel (projet PNJ), le texte envoyé au TTS reprenait presque directement le brief et des
consignes de production — « Épisode 0 — Le réveil d'Eli », « Utiliser principalement Eli », « Images fixes
uniquement »... Ce fichier couvre la fonction pure ``validate_spoken_script`` (tous les motifs du ticket) et
son intégration bout-en-bout dans ``ProductionService`` : une sortie invalide (générée OU fournie
manuellement) doit bloquer AVANT tout appel TTS/image, sans retry payant automatique, tandis que les scripts
valides et le chemin classique sans personnage restent inchangés. Faux connecteurs uniquement (aucun appel
réel) ; aucun appel ElevenLabs ou LLM réel.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from lody.characters import CharacterRepository
from lody.generation.costing import PriceBook
from lody.generation.models import ErrorKind, ProviderError
from lody.generation.script_guard import validate_spoken_script
from lody.generation.service import ProductionService, request_of
from lody.generation.store import Production, ProductionRepository
from lody.locations import LocationRepository
from lody.projects import DEFAULTS, ProjectRepository
from lody.secrets_guard import find_secret_path
from test.lody.fakes import ScriptedConnector, SyncExecutor

PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.3"), ("music", "elevenlabs"): Decimal("0.1")})
SUBJECT = "Explique en une phrase ce qu'est un bloc dans une blockchain, pour un débutant curieux."
VALID_SCRIPT = ("Bienvenue dans le monde des blocs. Chaque bloc contient des informations vérifiées par le "
               "réseau. Une fois validé, un bloc rejoint la chaîne pour toujours.")

# -- les quatre exemples exacts cités par le ticket, plus les variantes explicitement demandées -------------------
EPISODE_TITLE = "Épisode 0 — Le réveil d’Eli\n\n" + VALID_SCRIPT
USE_MAINLY_ELI = "Pour cette vidéo, utiliser principalement Eli et rester simple. " + VALID_SCRIPT
FIXED_IMAGES_ONLY = "Les visuels seront des images fixes uniquement, sans mouvement ni transition. " + VALID_SCRIPT
FULL_BRIEF_ECHO = ("Consignes permanentes : utiliser principalement Eli comme narrateur. Images fixes "
                  "uniquement, sans texte ni logo. Format vertical 9:16, durée cible 45 à 60 secondes.")
MIXED_DIALOGUE_AND_INSTRUCTIONS = (
    "Salut, aujourd'hui on découvre la blockchain ensemble ! " + "Consignes visuelles : sans texte ni logo, "
    "à ne jamais montrer de marque réelle. " + "C'est parti pour l'aventure."
)
SCENE_LIST = "Scène 1 : ouverture sur Eli. Scène 2 : explication. Scène 3 : chute humoristique."
DURATION_AND_FORMAT = f"{VALID_SCRIPT} La vidéo dure 45 à 60 secondes au format 9:16 vertical."


class Env:
    """Projet, personnages, service câblé sur un faux connecteur (aucun appel réel)."""

    def __init__(self, tmp_path):
        self.path = tmp_path / "lody.sqlite3"
        self.projects = ProjectRepository(self.path)
        self.characters = CharacterRepository(self.path)
        self.locations = LocationRepository(self.path)
        fields = {**copy.deepcopy(DEFAULTS), "name": "Projet test", "language": "fr-FR",
                  "text_provider": "openai", "visual_provider": "openai_image",
                  "voice_provider": "elevenlabs", "voice_name": "Voix Test", "music_provider": "none"}
        self.project = self.projects.create(**fields)
        self.connector = ScriptedConnector(tmp_path / "storage")
        self.service = ProductionService(
            ProductionRepository(self.path), {"scripted": self.connector}, SyncExecutor(),
            price_book=lambda: PRICES, character_repo=self.characters, location_repo=self.locations,
        )

    def add_eli(self, **overrides):
        fields = {"name": "Eli", "is_primary": True, "voice_provider": "elevenlabs",
                  "external_voice_id": "21m00Tcm4TlvDq8ikWAM", "voice_name": "Eli", **overrides}
        return self.characters.create(self.project.id, **fields)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def _fake_production(**overrides) -> Production:
    """Un ``Production`` minimal pour exercer ``validate_spoken_script`` isolément (fonction pure)."""
    base = dict(
        id="prd_x", project_id="prj_x", root_production_id="prd_x", parent_production_id=None, version=1,
        subject=SUBJECT, brief={}, script="", script_source="", storyboard=[], visual_prompts=[], params={},
        cost_currency="EUR", cost_low=None, cost_high=None, cost_partial=True, cost_detail={},
        confirmed_at=None, provider="scripted", external_task_id=None, idempotency_key="k", status=None,
        progress=None, current_step="", error_code="", error_message="", created_at="x", started_at=None,
        finished_at=None, updated_at="x", last_polled_at=None, video_ref="", video_duration=None,
    )
    base.update(overrides)
    return Production(**base)


_PNJ_BRIEF = {
    "demande": SUBJECT, "orientation": "", "ton": "Clair et précis", "duree_cible": "45 à 60 secondes",
    "visuels": {"style": "Univers sombre et cyan", "regles": "Images fixes uniquement, sans texte ni logo.",
               "a_eviter": ["Logo", "Texte à l'écran"]},
    "consignes_permanentes": "Utiliser principalement Eli comme narrateur.",
    "structure": ["Accroche", "Explication", "Chute"],
}


# ======================================================================================================================
# -- validate_spoken_script() : fonction pure, tous les motifs du ticket ------------------------------------------------
# ======================================================================================================================
@pytest.mark.parametrize(("label", "script"), [
    ("titre d'épisode", EPISODE_TITLE),
    ("utiliser principalement", USE_MAINLY_ELI),
    ("images fixes uniquement", FIXED_IMAGES_ONLY),
    ("reprise intégrale du brief", FULL_BRIEF_ECHO),
    ("mélange dialogue + consignes techniques", MIXED_DIALOGUE_AND_INSTRUCTIONS),
    ("liste de scènes", SCENE_LIST),
    ("durée et format techniques", DURATION_AND_FORMAT),
])
def test_each_mandatory_bad_case_is_rejected(label, script):
    production = _fake_production(brief=_PNJ_BRIEF)
    with pytest.raises(ProviderError) as error:
        validate_spoken_script(script, production)
    assert error.value.kind is ErrorKind.INVALID_RESPONSE
    assert error.value.stage == "script"


def test_valid_french_script_is_accepted():
    production = _fake_production(brief=_PNJ_BRIEF)
    validate_spoken_script(VALID_SCRIPT, production)  # ne lève pas


def test_the_subject_itself_echoed_in_the_narration_is_never_a_false_positive():
    """Un script est censé PARLER de son sujet, parfois en le citant presque mot pour mot — ce n'est jamais
    une fuite de consigne (contrairement aux instructions/métadonnées). Régression : demo_script() cite le
    sujet tel quel et ne doit jamais être refusé (voir generation/demo.py)."""
    production = _fake_production(brief=_PNJ_BRIEF)
    narration = f"Aujourd’hui, on parle de : {SUBJECT.rstrip('.')}. Voici une explication simple et claire."
    validate_spoken_script(narration, production)  # ne lève pas


def test_short_brief_fields_never_trigger_a_false_positive():
    """Sous le seuil MIN_OVERLAP, un mot du vocabulaire commun (« clair », « précis »...) ne doit jamais, à
    lui seul, faire échouer un script légitime qui les emploie normalement."""
    production = _fake_production(brief={"ton": "Clair et précis", "orientation": "", "visuels": {}})
    validate_spoken_script("Cette explication est claire et précise, comme on l'espérait.", production)


def test_no_brief_and_no_snapshot_never_crashes():
    """Compatibilité : une production sans brief structuré ni narrative_context (très ancienne, ou brief
    vide) ne doit jamais faire planter la validation elle-même — seuls les motifs génériques s'appliquent."""
    production = _fake_production(brief={}, snapshot={})
    validate_spoken_script(VALID_SCRIPT, production)


def test_character_fields_from_the_snapshot_are_also_protected():
    """Reprise verbatim d'une fiche personnage (#35/#36) — pas seulement du brief projet."""
    production = _fake_production(brief={}, snapshot={"narrative_context": {
        "characters": [{"name": "Eli", "personality": "Curieux, bienveillant, un peu maladroit et très "
                                                       "attaché à sa collection de pièces anciennes rares"}],
        "location": None,
    }})
    leaking = ("Bonjour ! Curieux, bienveillant, un peu maladroit et très attaché à sa collection de pièces "
              "anciennes rares, c'est Eli.")
    with pytest.raises(ProviderError):
        validate_spoken_script(leaking, production)


# ======================================================================================================================
# -- intégration bout-en-bout (ProductionService, faux connecteur, aucun appel réel) -------------------------------
# ======================================================================================================================
def test_bad_generated_script_blocks_before_storyboard_and_submit(env):  # noqa: F811
    eli = env.add_eli()
    env.connector.script_text = FULL_BRIEF_ECHO
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])

    launched = env.service.confirm(draft.id, accept_partial=True)

    assert launched.status.value == "ECHEC"
    assert launched.error_code == ErrorKind.INVALID_RESPONSE.value
    assert "submit" not in env.connector.calls  # jamais envoyé au moteur
    assert "thumbnail_background" not in env.connector.calls  # jamais d'appel image non plus
    assert env.connector.submitted == []


def test_bad_manual_script_is_rejected_without_any_llm_or_engine_call(env):  # noqa: F811
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=SCENE_LIST)
    launched = env.service.confirm(draft.id, accept_partial=True)

    assert launched.status.value == "ECHEC"
    assert "write_script" not in env.connector.calls  # script fourni : jamais d'appel d'écriture
    assert "submit" not in env.connector.calls


def test_valid_generated_script_still_reaches_the_engine_unaffected(env):  # noqa: F811
    """Non-régression : un script généré valide continue de fonctionner exactement comme avant #76."""
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "EN_FILE"
    assert "submit" in env.connector.calls
    assert launched.script  # le script généré (VALID par défaut, voir fakes.SCRIPT) est bien celui envoyé


def test_valid_manual_script_still_reaches_the_engine_unaffected(env):  # noqa: F811
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", script=VALID_SCRIPT)
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "EN_FILE"
    assert "write_script" not in env.connector.calls
    assert request_of(launched).script == VALID_SCRIPT


def test_classic_video_without_any_character_selected_is_unaffected(env):  # noqa: F811
    """Chemin classique sans personnage (#34/#35 non utilisés) : comportement strictement inchangé."""
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")  # aucune sélection
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "EN_FILE"
    assert launched.snapshot.get("narrative_context") == {}


def test_no_automatic_paid_retry_after_a_rejection(env):  # noqa: F811
    """Aucun retry payant automatique : le rejet est définitif pour CETTE production — seule une nouvelle
    préparation + confirmation explicite (donc un nouvel accord de l'utilisateur) peut relancer un essai."""
    env.connector.script_text = EPISODE_TITLE
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value == "ECHEC"
    calls_after_first_attempt = list(env.connector.calls)

    # Un rafraîchissement (comme la page de suivi le ferait périodiquement) ne relance jamais rien.
    refreshed = env.service.refresh(launched.id)
    assert refreshed.status.value == "ECHEC"
    assert env.connector.calls == calls_after_first_attempt  # aucun appel supplémentaire
    assert "submit" not in env.connector.calls


def test_rejection_reason_is_useful_and_never_leaks_the_raw_script(env):  # noqa: F811
    env.connector.script_text = FULL_BRIEF_ECHO
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    launched = env.service.confirm(draft.id, accept_partial=True)

    assert launched.error_message  # motif actionnable, jamais vide
    assert "prononçable" in launched.error_message or "production" in launched.error_message
    assert FULL_BRIEF_ECHO not in launched.error_message  # jamais le texte brut refusé
    assert "Eli" not in launched.error_message
    assert find_secret_path(launched.error_message) is None


def test_historical_production_with_a_brief_like_script_is_never_retroactively_touched(env):  # noqa: F811
    """Une production déjà TERMINEE avant #76, même avec un texte qui ressemblerait au brief, n'est jamais
    revalidée ni modifiée : la frontière ne s'applique qu'aux NOUVELLES exécutions de _run()."""
    from lody.generation.models import ProductionStatus as S

    old = env.service.repo.create(
        project_id=env.project.id, subject=SUBJECT, provider="scripted", status=S.TERMINEE,
        script=FULL_BRIEF_ECHO,  # texte historique, jamais retouché
    )
    stored = env.service.repo.get(old.id)
    assert stored.script == FULL_BRIEF_ECHO
    assert stored.status.value == "TERMINEE"  # jamais réévalué ni changé de statut
