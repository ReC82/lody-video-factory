"""Résolution de configuration + preflight complet : aucun appel payant, aucun secret."""

from __future__ import annotations

import json
import os
import stat

import pytest

from lody.generation import engine_facts as ef
from lody.generation import mpt_connector as mpt
from lody.generation.models import Capability as Cap
from lody.generation.models import CapabilityState as CS
from lody.generation.models import ProductionStatus as S
from lody.generation.models import ProviderError, ErrorKind
from lody.generation.service import LaunchError, ProductionService
from lody.generation.store import ProductionRepository
from lody.generation.costing import PriceBook
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS
from test.lody.fakes import ScriptedConnector, SyncExecutor
from test.lody.test_generation_connector import CONFIG, Recorder, _crypto_request

SECRETS = ("FAKE-CONFIG-VALUE-NOT-A-KEY", "FAKE-IMAGE-VALUE-NOT-A-KEY", "FAKE-ELEVEN-VALUE-NOT-A-KEY")
TECHNICAL = ("llm_provider", "api_key", "config.toml", "_key", "openai_image", "elevenlabs.")
PONG = (200, b'"pong"')
not_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignore les permissions de fichier")


def _connector(tmp_path, config=CONFIG, responses=(PONG,), *, storage=True, report=None):
    config_path = tmp_path / "config.toml"
    if config is not None:
        config_path.write_text(config, encoding="utf-8")
    if storage:
        (tmp_path / "storage").mkdir(exist_ok=True)
    transport = Recorder(*responses)
    connector = mpt.MoneyPrinterTurboConnector("http://engine:8080", tmp_path / "storage", config_path,
                                               report_path=report or tmp_path / "engine-report.json", transport=transport)
    return connector, transport


def _app(config, line):
    """Ajoute une ligne dans la section [app] (et non en fin de fichier, donc dans [ui])."""
    return config.replace("[app]\n", "[app]\n" + line + "\n", 1)


def _status(report, capability):
    return report.get(capability)


def _no_secret(*texts):
    blob = " ".join(str(text) for text in texts)
    for secret in SECRETS:
        assert secret not in blob, "valeur de clé exposée"


# -- faits : la clé est cherchée sous son nom historique, seule sa présence est retenue -------------------
def test_key_present_under_the_historical_name_is_seen_and_never_stored(tmp_path):
    facts = ef.resolve(_cfg(tmp_path, CONFIG), tmp_path / "none.json")
    assert facts.source == "config" and facts.llm_provider == "openai"
    assert facts.key_present("openai") is True and facts.image_key and facts.eleven_key
    _no_secret(repr(facts), facts.to_report())


def _cfg(tmp_path, content):
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    return path


def test_empty_absent_and_list_keys(tmp_path):
    raw = _app(CONFIG.replace('openai_api_key = "FAKE-CONFIG-VALUE-NOT-A-KEY"', 'openai_api_key = ""'), 'moonshot_api_key = "  "')
    facts = ef.resolve(_cfg(tmp_path, raw), tmp_path / "none.json")
    assert facts.key_present("openai") is False          # vide
    assert facts.key_present("moonshot") is False        # espaces = vide
    assert facts.key_present("deepseek") is None         # champ inexistant
    assert facts.key_present("ollama") is True           # fournisseur sans clé
    assert ef.facts_from_config({"app": {"openai_image_api_keys": []}}).image_key is False


def test_report_contains_only_booleans_and_names_and_is_world_readable(tmp_path):
    config = _cfg(tmp_path, CONFIG)
    out = tmp_path / "out" / "engine-capabilities.json"
    assert ef.main(["--config", str(config), "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    _no_secret(text)
    data = json.loads(text)
    assert data["facts"]["llm_key"]["openai"] is True and data["facts"]["llm_provider"] == "openai"
    assert stat.S_IMODE(out.stat().st_mode) == 0o644
    assert ef.main(["--config", str(tmp_path / "absent.toml"), "--out", str(out)]) == 1


@not_root
def test_unreadable_config_falls_back_to_a_fresh_report(tmp_path):
    config = _cfg(tmp_path, CONFIG)
    report = tmp_path / "engine-report.json"
    ef.main(["--config", str(config), "--out", str(report)])
    config.chmod(0o000)
    try:
        facts = ef.resolve(config, report)
        assert facts.source == "report" and facts.key_present("openai") is True and facts.eleven_key
    finally:
        config.chmod(0o600)


@not_root
def test_report_is_refused_when_the_config_changed_since(tmp_path):
    config = _cfg(tmp_path, CONFIG)
    report = tmp_path / "engine-report.json"
    ef.main(["--config", str(config), "--out", str(report)])
    config.write_text(CONFIG.replace('llm_provider = "openai"', 'llm_provider = "moonshot"'), encoding="utf-8")
    config.chmod(0o000)
    try:
        facts = ef.resolve(config, report)
        assert not facts.known and "report_stale" in facts.problems
    finally:
        config.chmod(0o600)


@not_root
@pytest.mark.parametrize("content,code", [("{ pas du json", "report_invalid"), (None, "report_missing")])
def test_missing_or_invalid_report_is_reported(tmp_path, content, code):
    config = _cfg(tmp_path, CONFIG)
    report = tmp_path / "r.json"
    if content:
        report.write_text(content, encoding="utf-8")
    config.chmod(0o000)
    try:
        facts = ef.resolve(config, report)
        assert not facts.known and code in facts.problems and "config_unreadable" in facts.problems
    finally:
        config.chmod(0o600)


def test_config_mounted_elsewhere_is_reported_even_if_a_report_exists(tmp_path):
    config = _cfg(tmp_path, CONFIG)
    report = tmp_path / "r.json"
    ef.main(["--config", str(config), "--out", str(report)])
    facts = ef.resolve(tmp_path / "ailleurs" / "config.toml", report)
    assert not facts.known and facts.problems == ("config_missing",)  # on ne croit pas un rapport sans son fichier


# -- preflight du connecteur ------------------------------------------------------------------------------------
def test_full_preflight_succeeds_and_shows_requested_vs_configured(tmp_path):
    connector, transport = _connector(tmp_path)
    report = connector.preflight(_crypto_request())
    assert report.ready and not report.blocking
    text = _status(report, Cap.TEXT)
    assert (text.state, text.requested, text.configured) == (CS.READY, "openai", "openai")
    assert "modèle par défaut du moteur" in text.message
    assert _status(report, Cap.VISUAL).model == "gpt-image" and _status(report, Cap.VISUAL).state is CS.READY
    voice = _status(report, Cap.VOICE)
    assert voice.state is CS.READY and "Kev" in voice.message and voice.model == "eleven_multilingual_v2"
    assert _status(report, Cap.MUSIC).state is CS.READY
    assert [s.state for s in (report.get(Cap.ENGINE), report.get(Cap.STORAGE), report.get(Cap.SETTINGS))] == [CS.READY] * 3
    assert [(m, u.split("8080")[1]) for m, u, *_ in transport.requests] == [("GET", "/ping")]  # seul appel : santé, gratuit
    _no_secret(report)


def test_wrong_text_provider_blocks_without_silent_fallback_and_explains_both_sides(tmp_path):
    config = _app(CONFIG.replace('llm_provider = "openai"', 'llm_provider = "moonshot"'), 'moonshot_api_key = ""')
    connector, _ = _connector(tmp_path, config)
    report = connector.preflight(_crypto_request())
    text = _status(report, Cap.TEXT)
    assert (text.state, text.requested, text.configured, text.fix) == (CS.NOT_CONFIGURED, "openai", "moonshot", "platform")
    assert "Moonshot" in text.message and "OpenAI" in text.message
    assert not report.ready and report.blocking == (text,)
    for word in TECHNICAL:  # aucun nom technique dans le message utilisateur
        assert word not in text.message
    assert "llm_provider=moonshot" in text.admin and "clé openai=renseignée" in text.admin and "clé moonshot=vide/absente" in text.admin
    _no_secret(text)


def test_engine_provider_with_its_own_key_is_still_not_used_for_an_openai_project(tmp_path):
    config = _app(CONFIG.replace('llm_provider = "openai"', 'llm_provider = "moonshot"'), 'moonshot_api_key = "FAKE-CONFIG-VALUE-NOT-A-KEY"')
    connector, _ = _connector(tmp_path, config)
    assert _status(connector.preflight(_crypto_request()), Cap.TEXT).state is CS.NOT_CONFIGURED  # jamais de bascule silencieuse


def test_empty_text_key_blocks_with_the_missing_setting_named_for_admins_only(tmp_path):
    config = CONFIG.replace('openai_api_key = "FAKE-CONFIG-VALUE-NOT-A-KEY"', 'openai_api_key = ""')
    connector, _ = _connector(tmp_path, config)
    text = _status(connector.preflight(_crypto_request()), Cap.TEXT)
    assert text.state is CS.NOT_CONFIGURED and "clé du fournisseur de texte" in text.message
    assert "openai_api_key" not in text.message and "clé openai=vide/absente" in text.admin


def test_supplied_script_does_not_need_the_text_provider(tmp_path):
    config = CONFIG.replace('llm_provider = "openai"', 'llm_provider = "moonshot"')
    connector, _ = _connector(tmp_path, config)
    report = connector.preflight(_crypto_request(script="Un script fourni."))
    assert _status(report, Cap.TEXT).state is CS.NOT_NEEDED and report.ready


def test_missing_voice_and_music_keys_and_image_endpoint(tmp_path):
    config = (CONFIG.replace('api_key = "FAKE-ELEVEN-VALUE-NOT-A-KEY"', 'api_key = ""')
              .replace('openai_image_base_url = "https://api.openai.com/v1"', 'openai_image_base_url = ""'))
    connector, _ = _connector(tmp_path, config)
    report = connector.preflight(_crypto_request())
    assert {i.capability for i in report.blocking} == {Cap.VISUAL, Cap.VOICE, Cap.MUSIC}
    assert all(i.state is CS.NOT_CONFIGURED and i.fix == "platform" for i in report.blocking)
    _no_secret(report)


def test_no_voice_selected_points_to_the_project_settings(tmp_path):
    from lody.generation.models import VoiceSpec

    connector, _ = _connector(tmp_path)
    report = connector.preflight(_crypto_request(voice=VoiceSpec("elevenlabs", "", "", "")))
    assert {i.capability for i in report.blocking} == {Cap.VOICE, Cap.SETTINGS}
    assert _status(report, Cap.VOICE).fix == "project" and "voix" in _status(report, Cap.SETTINGS).message


def test_configured_providers_but_engine_unreachable_blocks(tmp_path):
    connector, _ = _connector(tmp_path, responses=(ConnectionRefusedError(),))
    report = connector.preflight(_crypto_request())
    assert not report.ready and [i.capability for i in report.blocking] == [Cap.ENGINE]
    assert _status(report, Cap.ENGINE).state is CS.UNAVAILABLE and _status(report, Cap.TEXT).state is CS.READY


def test_storage_not_accessible_blocks(tmp_path):
    connector, _ = _connector(tmp_path, storage=False)
    report = connector.preflight(_crypto_request())
    assert [i.capability for i in report.blocking] == [Cap.STORAGE]


def test_config_mounted_elsewhere_makes_every_provider_unverified_and_blocking(tmp_path):
    connector, _ = _connector(tmp_path, config=None)
    report = connector.preflight(_crypto_request())
    assert {i.capability for i in report.blocking} == {Cap.TEXT, Cap.VISUAL, Cap.VOICE, Cap.MUSIC}
    assert all(i.state is CS.UNVERIFIED for i in report.blocking)
    assert "introuvable" in _status(report, Cap.TEXT).admin and "config.toml" not in _status(report, Cap.TEXT).message


@not_root
def test_unreadable_config_with_a_fresh_report_gives_the_exact_same_verdict(tmp_path):
    connector, _ = _connector(tmp_path)
    direct = connector.preflight(_crypto_request())
    ef.main(["--config", str(tmp_path / "config.toml"), "--out", str(tmp_path / "engine-report.json")])
    (tmp_path / "config.toml").chmod(0o000)
    try:
        connector2, _ = _connector(tmp_path, config=None, responses=(PONG,))
        (tmp_path / "config.toml").chmod(0o000)
        via_report = connector2.preflight(_crypto_request())
    finally:
        (tmp_path / "config.toml").chmod(0o600)
    assert [(i.capability, i.state, i.configured, i.model) for i in via_report.items] == \
           [(i.capability, i.state, i.configured, i.model) for i in direct.items]


def test_voice_model_difference_is_information_not_a_blocker(tmp_path):
    connector, _ = _connector(tmp_path, CONFIG.replace("eleven_multilingual_v2", "eleven_turbo_v2_5"))
    report = connector.preflight(_crypto_request())
    assert report.ready and "eleven_turbo_v2_5" in _status(report, Cap.VOICE).message


def test_music_variants_and_unsupported_visuals(tmp_path):
    connector, _ = _connector(tmp_path, responses=(PONG,) * 3)
    assert _status(connector.preflight(_crypto_request(music_provider="none")), Cap.MUSIC).state is CS.DISABLED
    assert _status(connector.preflight(_crypto_request(music_provider="library")), Cap.MUSIC).state is CS.READY
    report = connector.preflight(_crypto_request(visual_provider="local"))
    assert _status(report, Cap.VISUAL).state is CS.UNAVAILABLE and not report.ready


# -- service : blocage avant toute confirmation ------------------------------------------------------------------------
SUBJECT = "Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming."


def _service_with_real_connector(tmp_path, config):
    connector, transport = _connector(tmp_path, config, responses=(PONG,) * 20)
    projects = ProjectRepository(tmp_path / "lody.sqlite3")
    projects.seed_defaults(SEED_PROJECTS)
    project = next(p for p in projects.list_projects() if p.name == "LodyCrypto")
    service = ProductionService(ProductionRepository(tmp_path / "lody.sqlite3"), {mpt.PROVIDER_ID: connector}, SyncExecutor(),
                                price_book=lambda: PriceBook())
    return service, project, transport


def test_confirmation_is_refused_and_nothing_is_recorded_or_called_when_a_capability_is_missing(tmp_path, caplog):
    caplog.set_level("DEBUG")
    config = CONFIG.replace('llm_provider = "openai"', 'llm_provider = "moonshot"')
    service, project, transport = _service_with_real_connector(tmp_path, config)
    draft = service.prepare(project, SUBJECT, provider_id=mpt.PROVIDER_ID)
    with pytest.raises(LaunchError) as error:
        service.confirm(draft.id, accept_partial=True)
    assert [i.blocking for i in error.value.issues] == [True]
    stored = service.repo.get(draft.id)
    assert stored.status is S.EN_ATTENTE_CONFIRMATION and stored.confirmed_at is None and stored.external_task_id is None
    assert all(url.endswith("/ping") for _, url, *_ in transport.requests)   # ni /scripts ni /videos : aucun appel payant
    assert service.repo.list_by_status([S.CONFIRMEE, S.EN_COURS, S.EN_FILE]) == []
    raw = b"".join(p.read_bytes() for p in tmp_path.glob("lody.sqlite3*")).decode("utf-8", "ignore")
    _no_secret(raw, caplog.text, repr(error.value.issues))


def test_option_states_report_each_provider_as_configured_or_not_without_choosing_for_the_user(tmp_path):
    config = CONFIG.replace('llm_provider = "openai"', 'llm_provider = "moonshot"')
    service, project, _ = _service_with_real_connector(tmp_path, config)
    states = service.option_states(project, mpt.PROVIDER_ID)
    assert states[("text", "openai")] is CS.NOT_CONFIGURED and states[("text", "manual")] is CS.NOT_NEEDED
    assert states[("visual", "openai_image")] is CS.READY and states[("voice", "elevenlabs")] is CS.READY
    assert states[("visual", "local")] is CS.UNAVAILABLE and states[("music", "none")] is CS.DISABLED
    assert project.text_provider == "openai"  # le projet garde SON choix
    fresh = service.option_states(None, mpt.PROVIDER_ID)  # nouveau projet : mêmes états, sans projet
    assert fresh[("text", "openai")] is CS.NOT_CONFIGURED


# -- nouvelle tentative après échec -------------------------------------------------------------------------------------------
class Env:
    def __init__(self, tmp_path):
        self.path = tmp_path / "lody.sqlite3"
        projects = ProjectRepository(self.path)
        projects.seed_defaults(SEED_PROJECTS)
        self.project = next(p for p in projects.list_projects() if p.name == "LodyCrypto")
        self.connector = ScriptedConnector(tmp_path / "storage")
        self.prices = {"book": PriceBook()}
        self.service = ProductionService(ProductionRepository(self.path), {"scripted": self.connector}, SyncExecutor(),
                                         price_book=lambda: self.prices["book"])


def test_a_failed_production_stays_and_a_retry_is_a_new_linked_attempt_needing_a_new_confirmation(tmp_path):
    env = Env(tmp_path)
    env.connector.script_error = ProviderError(ErrorKind.KEY_MISSING, "Une clé d’API nécessaire à cette génération n’est pas configurée côté serveur.")
    failed = env.service.confirm(env.service.prepare(env.project, SUBJECT, provider_id="scripted").id, accept_partial=True)
    assert failed.status is S.ECHEC and failed.confirmed_at
    snapshot = env.service.repo.get(failed.id)
    env.connector.script_error = None  # la configuration est corrigée
    from decimal import Decimal

    env.prices["book"] = PriceBook("EUR", {("visual", "openai_image"): Decimal("0.04")})
    retry = env.service.prepare(env.project, SUBJECT, provider_id="scripted", retry_of=failed.id)
    assert retry.id != failed.id and retry.parent_production_id == failed.id and retry.version == 2
    assert retry.status is S.EN_ATTENTE_CONFIRMATION and retry.confirmed_at is None   # aucune confirmation reprise
    assert retry.cost_high is not None and failed.cost_high is None                   # coût recalculé, pas repris
    assert retry.subject == failed.subject and retry.external_task_id is None
    assert env.service.repo.get(failed.id) == snapshot                                # l'échec n'est pas écrasé
    assert "submit" not in env.connector.calls
    launched = env.service.confirm(retry.id, accept_partial=True)                     # nouvelle confirmation explicite
    assert launched.status is S.EN_FILE and env.service.repo.get(failed.id) == snapshot
    assert [p.version for p in env.service.repo.chain(failed.root_production_id)] == [1, 2]


def test_retry_of_a_non_failed_or_foreign_production_is_ignored(tmp_path):
    env = Env(tmp_path)
    ok = env.service.confirm(env.service.prepare(env.project, SUBJECT, provider_id="scripted").id, accept_partial=True)
    assert ok.status is not S.ECHEC
    draft = env.service.prepare(env.project, SUBJECT + " autre", provider_id="scripted", retry_of=ok.id)
    assert draft.parent_production_id is None


# -- gabarit d'images GLOBAL du moteur : contamination entre projets ---------------------------------------------------------
BROADCAST = ('professional broadcast television visual explaining {term}, realistic studio or control room environment, '
             'use broadcast monitors, vision mixer, lower thirds, alpha matte')


def _with_template(template: str) -> str:
    return CONFIG.replace("[app]\n", f'[app]\nopenai_image_prompt_template = "{template}"\n', 1)


def test_a_global_engine_image_template_blocks_real_generation_for_every_project(tmp_path):
    connector, _ = _connector(tmp_path, _with_template(BROADCAST))
    report = connector.preflight(_crypto_request())
    visual = _status(report, Cap.VISUAL)
    assert visual.state is CS.NOT_CONFIGURED and visual.fix == "platform" and not report.ready
    assert "à tous les projets" in visual.message and "neutraliser" in visual.message
    for word in TECHNICAL:
        assert word not in visual.message
    assert "ACTIF" in visual.admin and "broadcast television" in visual.admin and "openai_image_prompt_template" in visual.admin
    _no_secret(visual)


@pytest.mark.parametrize("template", ["", "{term}", "  {term}  ", "un texte sans placeholder, ignoré par le moteur"])
def test_neutral_or_ineffective_engine_templates_do_not_block(tmp_path, template):
    connector, _ = _connector(tmp_path, _with_template(template))
    assert _status(connector.preflight(_crypto_request()), Cap.VISUAL).state is CS.READY


def test_template_travels_in_the_report_and_an_old_report_is_refused(tmp_path):
    config = _cfg(tmp_path, _with_template(BROADCAST))
    report = tmp_path / "engine-report.json"
    ef.main(["--config", str(config), "--out", str(report)])
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["version"] == 2 and data["facts"]["image_template"].startswith("professional broadcast")
    _no_secret(report.read_text(encoding="utf-8"))
    data["version"] = 1
    report.write_text(json.dumps(data), encoding="utf-8")
    if os.geteuid() != 0:
        config.chmod(0o000)
        try:
            assert "report_invalid" in ef.resolve(config, report).problems      # rapport v1 : sans le gabarit, donc refusé
        finally:
            config.chmod(0o600)
