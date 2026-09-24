"""Voix MVP mono-voix (ticket #70, prérequis technique de #39).

Priorité de résolution, uniquement depuis le ``narrative_context`` déjà figé (jamais la fiche personnage
actuelle) :

1. le personnage sélectionné marqué ``is_primary`` — seulement s'il y en a EXACTEMENT un ;
2. sinon, si un seul personnage est sélectionné, celui-ci ;
3. sinon (aucun personnage sélectionné, ou plusieurs sans principal unique), la voix du projet.

Si le personnage de référence n'a pas ``voice_provider`` ET ``external_voice_id``, ou que le fournisseur
n'est pas reconnu (``catalog.VOICE_PROVIDERS``), repli sûr et TOUJOURS consigné vers la voix du projet.

Faux connecteurs uniquement (``ScriptedConnector``) : aucun appel ElevenLabs réel, aucun appel payant.
"""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from lody import settings
from lody.characters import CharacterRepository
from lody.generation.costing import PriceBook
from lody.generation.models import VoiceSpec
from lody.generation.narrative_context import resolve_voice
from lody.generation.service import ProductionService, build_request, request_of
from lody.generation.store import ProductionRepository
from lody.locations import LocationRepository
from lody.projects import DEFAULTS, ProjectRepository
from lody.secrets_guard import find_secret_path
from test.lody.fakes import DeferredExecutor, ScriptedConnector, SyncExecutor
from test.lody.test_generation_ui import (  # noqa: F401
    _button,
    _fresh_resources,
    _prepare,
    _project,
    _run,
    _text,
    engine,
)

pytest.importorskip("streamlit.testing.v1")

PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.3"), ("music", "elevenlabs"): Decimal("0.1")})
SUBJECT = "Explique simplement comment fonctionne une pile solaire à quelqu'un qui débute."
PROJECT_VOICE = VoiceSpec("elevenlabs", "voix-du-projet", "Voix du projet", "eleven_multilingual_v2")


class Env:
    """Un projet (voix par défaut connue), ses personnages, et un service câblé sur un faux connecteur."""

    def __init__(self, tmp_path, executor=None):
        self.path = tmp_path / "lody.sqlite3"
        self.projects = ProjectRepository(self.path)
        self.characters = CharacterRepository(self.path)
        self.locations = LocationRepository(self.path)
        fields = {**copy.deepcopy(DEFAULTS), "name": "Projet voix", "language": "fr-FR",
                  "text_provider": "openai", "visual_provider": "openai_image",
                  "voice_provider": PROJECT_VOICE.provider, "voice_name": PROJECT_VOICE.name,
                  "music_provider": "elevenlabs",
                  "settings": {"brief": {"voice_id": PROJECT_VOICE.voice_id, "voice_model": PROJECT_VOICE.model}}}
        self.project = self.projects.create(**fields)
        self.connector = ScriptedConnector(tmp_path / "storage")
        self.executor = executor or SyncExecutor()
        self.service = self._service()

    def _service(self, executor=None):
        return ProductionService(
            ProductionRepository(self.path), {"scripted": self.connector}, executor or self.executor,
            price_book=lambda: PRICES, character_repo=self.characters, location_repo=self.locations,
        )

    def restart(self, executor=None):
        """Nouveau processus : mêmes données, mémoire vierge."""
        self.service = self._service(executor)
        return self.service

    def add_character(self, **overrides):
        fields = {"name": "Eli", **overrides}
        return self.characters.create(self.project.id, **fields)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def _elevenlabs_voice(**overrides):
    return {"voice_provider": "elevenlabs", "external_voice_id": "21m00Tcm4TlvDq8ikWAM",
           "voice_name": "Voix d’Eli", **overrides}


# ======================================================================================================================
# -- resolve_voice() : fonction pure, tous les cas du ticket -----------------------------------------------------------
# ======================================================================================================================
def test_no_character_keeps_the_project_voice():
    voice, origin = resolve_voice(PROJECT_VOICE, {})
    assert voice == PROJECT_VOICE
    assert origin == {"source": "project", "fallback": False, "character_name": "",
                      "reason": "aucun personnage sélectionné"}


def test_none_narrative_context_keeps_the_project_voice():
    """Productions antérieures à #35 : la clé n'existe même pas."""
    voice, origin = resolve_voice(PROJECT_VOICE, None)
    assert voice == PROJECT_VOICE and origin["source"] == "project"


def test_eli_with_a_valid_voice_is_used_as_the_single_selected_character():
    narrative = {"characters": [{"name": "Eli", "is_primary": False, **_elevenlabs_voice()}], "location": None}
    voice, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert voice == VoiceSpec("elevenlabs", "21m00Tcm4TlvDq8ikWAM", "Voix d’Eli", PROJECT_VOICE.model)
    assert origin == {"source": "character", "fallback": False, "character_name": "Eli",
                      "reason": "voix de Eli (seul personnage sélectionné)"}


def test_character_without_a_provider_falls_back_to_the_project_voice():
    narrative = {"characters": [{"name": "Eli", "is_primary": False, "voice_provider": "",
                                "external_voice_id": "21m00Tcm4TlvDq8ikWAM"}], "location": None}
    voice, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert voice == PROJECT_VOICE
    assert origin["fallback"] is True and origin["character_name"] == "Eli"
    assert "incomplète" in origin["reason"]


def test_character_without_an_external_id_falls_back_to_the_project_voice():
    narrative = {"characters": [{"name": "Eli", "is_primary": False, "voice_provider": "elevenlabs",
                                "external_voice_id": ""}], "location": None}
    voice, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert voice == PROJECT_VOICE
    assert origin["fallback"] is True and "incomplète" in origin["reason"]


def test_unsupported_provider_falls_back_to_the_project_voice():
    """Un fournisseur ne figurant pas dans catalog.VOICE_PROVIDERS (donnée historique/corrompue : la
    validation normale de characters.py l'empêche désormais, voir #63) : repli sûr, jamais un échec."""
    narrative = {"characters": [{"name": "Eli", "is_primary": False, "voice_provider": "myspace-voice",
                                "external_voice_id": "abc"}], "location": None}
    voice, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert voice == PROJECT_VOICE
    assert origin["fallback"] is True and "non reconnu" in origin["reason"]


def test_edge_provider_without_an_external_id_is_incomplete_not_unsupported():
    """« edge » EST reconnu (catalog.VOICE_PROVIDERS) mais n'a normalement pas d'identifiant externe : sans
    external_voice_id, c'est le cas générique « voix incomplète », pas « fournisseur non reconnu »."""
    narrative = {"characters": [{"name": "Eli", "is_primary": False, "voice_provider": "edge",
                                "external_voice_id": ""}], "location": None}
    voice, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert voice == PROJECT_VOICE and origin["fallback"] is True and "incomplète" in origin["reason"]


def test_several_characters_with_one_primary_uses_the_primary_s_voice():
    narrative = {"characters": [
        {"name": "Eli", "is_primary": True, **_elevenlabs_voice(external_voice_id="EliID")},
        {"name": "Zoé", "is_primary": False, **_elevenlabs_voice(external_voice_id="ZoeID")},
    ], "location": None}
    voice, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert voice.voice_id == "EliID"
    assert origin == {"source": "character", "fallback": False, "character_name": "Eli",
                      "reason": "voix de Eli (personnage principal sélectionné)"}


def test_several_characters_without_a_primary_keeps_the_project_voice():
    narrative = {"characters": [
        {"name": "Eli", "is_primary": False, **_elevenlabs_voice(external_voice_id="EliID")},
        {"name": "Zoé", "is_primary": False, **_elevenlabs_voice(external_voice_id="ZoeID")},
    ], "location": None}
    voice, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert voice == PROJECT_VOICE
    assert origin["fallback"] is False and origin["source"] == "project"
    assert "aucun principal" in origin["reason"]


def test_several_primaries_selected_together_is_ambiguous_keeps_the_project_voice():
    """« Au maximum un personnage de référence » : deux principaux à la fois n'est jamais résolu vers l'un
    d'eux au hasard — repli sur la voix du projet, comme « plusieurs sans principal »."""
    narrative = {"characters": [
        {"name": "Eli", "is_primary": True, **_elevenlabs_voice(external_voice_id="EliID")},
        {"name": "Zoé", "is_primary": True, **_elevenlabs_voice(external_voice_id="ZoeID")},
    ], "location": None}
    voice, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert voice == PROJECT_VOICE and origin["source"] == "project"


def test_a_location_alone_never_provides_a_voice():
    narrative = {"characters": [], "location": {"name": "Atelier"}}
    voice, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert voice == PROJECT_VOICE and origin["reason"] == "aucun personnage sélectionné"


def test_voice_resolution_never_leaks_a_secret_looking_value():
    narrative = {"characters": [{"name": "Eli", "is_primary": False, **_elevenlabs_voice()}], "location": None}
    _, origin = resolve_voice(PROJECT_VOICE, narrative)
    assert find_secret_path(origin) is None


# ======================================================================================================================
# -- intégration bout-en-bout (ProductionService, faux connecteur, aucun appel payant) ---------------------------------
# ======================================================================================================================
def test_no_selection_produces_the_exact_same_voice_payload_as_before_70(env):  # noqa: F811
    """Non-régression du chemin mono-voix historique."""
    baseline = build_request(env.project, SUBJECT).voice
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted")
    assert request_of(draft).voice == baseline == PROJECT_VOICE
    assert draft.params["voice_resolution"]["source"] == "project"


def test_eli_selected_alone_uses_his_voice_end_to_end(env):  # noqa: F811
    eli = env.add_character(**_elevenlabs_voice())
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    request = request_of(draft)
    assert request.voice.provider == "elevenlabs" and request.voice.voice_id == "21m00Tcm4TlvDq8ikWAM"
    assert draft.params["voice_resolution"]["source"] == "character"
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert request_of(launched).voice.voice_id == "21m00Tcm4TlvDq8ikWAM"  # inchangé jusqu'au moteur


def test_incomplete_character_voice_falls_back_without_failing(env):  # noqa: F811
    eli = env.add_character(name="Eli")  # aucune voix renseignée
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    assert request_of(draft).voice == PROJECT_VOICE
    assert draft.params["voice_resolution"]["fallback"] is True
    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value in ("EN_FILE", "TERMINEE", "CONFIRMEE")  # jamais un échec


def test_snapshot_is_used_even_after_eli_s_voice_sheet_changes(env):  # noqa: F811
    eli = env.add_character(**_elevenlabs_voice())
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    launched = env.service.confirm(draft.id, accept_partial=True)
    voice_before = request_of(launched).voice

    env.characters.update(env.project.id, eli.id, voice_provider="edge", external_voice_id="",
                          voice_name="Une tout autre voix")
    env.characters.deactivate(env.project.id, eli.id)

    stored = env.service.repo.get(launched.id)
    assert request_of(stored).voice == voice_before  # jamais relu depuis la fiche personnage
    assert stored.params["voice_resolution"]["character_name"] == "Eli"


def test_v2_inherits_the_same_resolved_voice(env):  # noqa: F811
    eli = env.add_character(**_elevenlabs_voice())
    v1 = env.service.confirm(
        env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id]).id,
        accept_partial=True,
    )
    env.connector.queue(env.connector.done("task-0001"))
    v1 = env.service.refresh(v1.id)
    env.characters.update(env.project.id, eli.id, voice_name="Renommée")

    v2 = env.service.create_v2(v1.id)
    assert request_of(v2).voice == request_of(v1).voice
    assert v2.params.get("voice_resolution") == v1.params.get("voice_resolution")


def test_restart_before_send_never_resolves_the_voice_twice_or_double_bills(tmp_path):  # noqa: F811
    """Reprise après redémarrage, comme test_generation_service.py::
    test_restart_before_the_send_never_resubmits_a_paid_call : le worker n'a jamais tourné avant le
    redémarrage (exécuteur différé), donc la reprise la marque ECHEC/interrompue plutôt que de la rejouer —
    jamais un second appel. La voix déjà figée dans params.request au moment de prepare() reste exactement
    celle-là, jamais recalculée."""
    env = Env(tmp_path, executor=DeferredExecutor())
    eli = env.add_character(**_elevenlabs_voice())
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    resolved_voice = request_of(draft).voice
    env.service.confirm(draft.id, accept_partial=True)  # mis en file (preflight gratuit), DeferredExecutor ne l'exécute pas
    assert "write_script" not in env.connector.calls and "submit" not in env.connector.calls  # aucun appel payant avant la reprise

    env.restart(executor=SyncExecutor())  # nouveau processus : mémoire vierge, mêmes données SQLite
    env.service.resume_active()
    resumed = env.service.repo.get(draft.id)
    assert resumed.status.value == "ECHEC" and resumed.error_code == "interrupted"
    assert "write_script" not in env.connector.calls and "submit" not in env.connector.calls  # toujours aucun appel payant
    assert request_of(resumed).voice == resolved_voice  # jamais recalculée, même sur une reprise échouée


def test_preparing_the_same_draft_twice_resolves_the_voice_identically(env):  # noqa: F811
    """Idempotence : re-préparer le MÊME brouillon (même sélection) redonne exactement la même voix."""
    eli = env.add_character(**_elevenlabs_voice())
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    again = env.service.prepare(env.project, SUBJECT, provider_id="scripted", draft_id=draft.id,
                                character_ids=[eli.id])
    assert draft.id == again.id
    assert request_of(again).voice == request_of(draft).voice
    assert env.connector.calls == []  # aucun appel, jamais une double facturation


def test_isolation_between_projects_never_mixes_up_a_voice(env):  # noqa: F811
    other = env.projects.create(name="Autre projet", language="fr-FR", text_provider="openai",
                                visual_provider="openai_image", voice_provider="edge", music_provider="elevenlabs")
    other_char = env.characters.create(other.id, name="Personnage de l'autre projet", **_elevenlabs_voice(external_voice_id="AutreID"))

    eli = env.add_character(**_elevenlabs_voice(external_voice_id="EliID"))
    draft_a = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    assert request_of(draft_a).voice.voice_id == "EliID"

    draft_b = env.service.prepare(other, SUBJECT, provider_id="scripted", character_ids=[other_char.id])
    assert request_of(draft_b).voice.voice_id == "AutreID"
    assert request_of(draft_a).voice.voice_id == "EliID"  # non affecté par la préparation de l'autre projet


def test_voice_resolution_survives_the_production_actually_running(env):  # noqa: F811
    """Régression : _run() réécrivait params={"request":..., "engine":...} en écrasant ENTIÈREMENT le
    dictionnaire posé par prepare() — "voice_resolution" (#70) ET "reference_images" (#38) disparaissaient
    dès qu'une production dépassait l'étape d'écriture du script. Corrigé en fusionnant (jamais un
    écrasement complet) : les deux clés doivent survivre au lancement réel, pas seulement au brouillon."""
    eli = env.add_character(**_elevenlabs_voice())
    draft = env.service.prepare(env.project, SUBJECT, provider_id="scripted", character_ids=[eli.id])
    assert "voice_resolution" in draft.params and "reference_images" in draft.params

    launched = env.service.confirm(draft.id, accept_partial=True)
    assert launched.status.value in ("EN_FILE", "TERMINEE")  # la génération a bien tourné (script écrit)
    assert launched.params["voice_resolution"]["source"] == "character"
    assert launched.params["voice_resolution"]["character_name"] == "Eli"
    assert "reference_images" in launched.params  # clé de #38 également préservée

    stored = env.service.repo.get(launched.id)  # relu depuis SQLite : pas seulement l'objet en mémoire
    assert stored.params["voice_resolution"]["source"] == "character"


# ======================================================================================================================
# -- écran (AppTest) : voix visible avant confirmation et dans le diagnostic, aucun secret -----------------------------
# ======================================================================================================================
def test_estimate_panel_announces_the_character_s_voice_before_confirmation(engine):  # noqa: F811
    project = _project()
    characters = CharacterRepository(settings.db_path())
    eli = characters.create(project.id, name="Eli", **_elevenlabs_voice())

    app = _run({"projet": project.id, "vue": "production"})
    app.multiselect(key=f"chars_sel_{project.id}").set_value([eli.id]).run()
    app = _prepare(app, project)
    assert not app.exception
    text = _text(app)
    assert "Voix effective" in text
    assert "Eli" in text and "seul personnage sélectionné" in text


def test_tracking_page_shows_the_voice_diagnostic_block(engine):  # noqa: F811
    project = _project()
    characters = CharacterRepository(settings.db_path())
    eli = characters.create(project.id, name="Eli", **_elevenlabs_voice())

    app = _run({"projet": project.id, "vue": "production"})
    app.multiselect(key=f"chars_sel_{project.id}").set_value([eli.id]).run()
    app = _prepare(app, project)
    app = _button(app, "Confirmer et générer la vidéo").click().run()
    assert not app.exception

    production = ProductionRepository(settings.db_path()).list_for_project(project.id)[0]
    app = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    text = _text(app)
    assert "Voix (MVP mono-voix, #70)" in text
    assert "Fournisseur de voix" in text and "Origine" in text and "Repli appliqué" in text
    assert "21m00Tcm4TlvDq8ikWAM" in text
    for secret in ("FAKE-CONFIG-VALUE-NOT-A-KEY", "sk-", "api_key"):
        assert secret not in text


def test_no_selection_shows_the_project_voice_as_before_ticket_70(engine):  # noqa: F811
    """Non-régression du chemin mono-voix historique, côté écran cette fois."""
    project = _project()
    app = _run({"projet": project.id, "vue": "production"})
    app = _prepare(app, project)
    text = _text(app)
    assert "Voix effective" in text
    assert "aucun personnage sélectionné" in text


def test_the_button_still_switches_to_confirm_after_preparing_with_a_resolved_character_voice(engine):  # noqa: F811
    """Régression : fresh_draft() comparait le brouillon déjà enregistré (voix résolue vers celle d'Eli) à
    un ``build_request()`` fraîchement recalculé (voix NUE du projet) — toujours différents dès qu'une voix
    de personnage était effectivement utilisée, donc le brouillon semblait perpétuellement périmé et le
    bouton restait bloqué sur « Préparer la génération » au lieu de proposer « Confirmer et générer la
    vidéo ». Corrigé en ignorant la voix dans cette comparaison (déjà vérifiée séparément par la
    correspondance de sélection, juste en dessous dans fresh_draft())."""
    project = _project()
    characters = CharacterRepository(settings.db_path())
    eli = characters.create(project.id, name="Eli", **_elevenlabs_voice())

    app = _run({"projet": project.id, "vue": "production"})
    app.multiselect(key=f"chars_sel_{project.id}").set_value([eli.id]).run()
    app = _prepare(app, project)
    labels = [b.label for b in app.button]
    assert "Confirmer et générer la vidéo" in labels
    assert "Préparer la génération" not in labels
