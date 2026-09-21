"""Isolation stricte par projet : instantané immuable, prompts finaux tracés, aucune contamination. Aucun appel réel."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
from decimal import Decimal

import pytest

from lody import brief as brief_lib
from lody import projects as projects_mod
from lody import seeds
from lody.generation import mpt_connector as mpt
from lody.generation import storyboard
from lody.generation.costing import PriceBook
from lody.generation.models import ErrorKind, ProviderError
from lody.generation.models import ProductionStatus as S
from lody.generation.safety import redact
from lody.generation.service import LaunchError, ProductionService, build_request, make_snapshot
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS
from test.lody.fakes import DeferredExecutor, ScriptedConnector, SyncExecutor

PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.3"), ("music", "elevenlabs"): Decimal("0.1")})
SUBJECT_AV = "Explique simplement la différence entre Fill et Key en télévision."
SUBJECT_CRYPTO = "Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming."
SENTINELS = ("broadcast", "mixing console", "FILL/KEY")


@pytest.fixture(autouse=True)
def _never_read_the_real_engine_config(tmp_path, monkeypatch):
    """Aucun test d'isolation ne peut lire la vraie config.toml de l'hôte (elle contient un gabarit d'images global)."""
    neutral = tmp_path / "neutral-config.toml"
    neutral.write_text('[app]\nllm_provider = "openai"\nopenai_image_prompt_template = ""\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(neutral))
    monkeypatch.setenv("LODY_ENGINE_REPORT", str(tmp_path / "no-report.json"))


class Env:
    def __init__(self, tmp_path, connector=None, executor=None):
        self.path = tmp_path / "lody.sqlite3"
        self.projects = ProjectRepository(self.path)
        self.projects.seed_defaults(SEED_PROJECTS)
        self.connector = connector or ScriptedConnector(tmp_path / "storage")
        self.executor = executor or SyncExecutor()
        self.service = self.new_service()

    def new_service(self, executor=None):
        return ProductionService(ProductionRepository(self.path), {"scripted": self.connector},
                                 executor or self.executor, price_book=lambda: PRICES)

    def project(self, name):
        return next(p for p in self.projects.list_projects() if p.name == name)

    def launch(self, project, subject, service=None):
        service = service or self.service
        draft = service.prepare(project, subject, provider_id="scripted")
        return service.confirm(draft.id, accept_partial=True)

    def dump(self, production_id) -> str:
        """Tout ce qui est enregistré pour une production, en un seul texte."""
        return json.dumps(ProductionRepository(self.path).get(production_id).__dict__, ensure_ascii=False, default=str)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def _add_project(env, name, **overrides):
    from lody.projects import DEFAULTS

    fields = {**copy.deepcopy(DEFAULTS), "name": name, "language": "fr-FR", "text_provider": "openai",
              "visual_provider": "openai_image", "voice_provider": "elevenlabs", "voice_name": "Voix Test",
              "music_provider": "none", **overrides}
    return env.projects.create(**fields)


# -- deux projets indépendants ----------------------------------------------------------------------------------------------
def test_two_seed_projects_have_completely_different_settings(env):
    av, crypto = env.project("Audiovisuel"), env.project("LodyCrypto")
    assert av.id != crypto.id and av.seed_key != crypto.seed_key
    a, c = build_request(av, SUBJECT_AV), build_request(crypto, SUBJECT_CRYPTO)
    for field in ("visual_style", "tone", "audience", "orientation", "structure", "instructions", "visual_rules",
                  "visual_avoid", "music_provider", "narration_pace"):
        assert getattr(a, field) != getattr(c, field), field
    assert "broadcast" in a.visual_style.lower() and "broadcast" not in c.visual_style.lower()


def test_no_audiovisuel_value_in_any_lodycrypto_recorded_data_and_vice_versa(env):
    av, crypto = env.project("Audiovisuel"), env.project("LodyCrypto")
    prod_c = env.launch(crypto, SUBJECT_CRYPTO)
    prod_a = env.launch(av, SUBJECT_AV)  # une génération active par projet : deux projets peuvent tourner ensemble
    text_c, text_a = env.dump(prod_c.id), env.dump(prod_a.id)
    av_only = [av.visual_style, av.description, av.tone, av.settings["example_request"], "Fill et Key", "régie, plateau"]
    for value in av_only:
        assert value not in text_c, f"valeur Audiovisuel dans LodyCrypto : {value!r}"
    crypto_only = [crypto.visual_style, crypto.tone, crypto.settings["example_request"], "Web3 gaming", "crypto"]
    for value in crypto_only:
        assert value not in text_a.replace(SUBJECT_AV, ""), f"valeur LodyCrypto dans Audiovisuel : {value!r}"
    # ni l'une ni l'autre production n'a servi de source à l'autre
    assert env.connector.submitted[0].visual_style == crypto.visual_style
    assert env.connector.submitted[1].visual_style == av.visual_style


def test_payload_of_each_project_built_by_the_real_connector_is_clean(tmp_path):
    env = Env(tmp_path)
    facts = mpt.EngineFacts()
    crypto = build_request(env.project("LodyCrypto"), SUBJECT_CRYPTO, "Un script.").with_updates(visual_prompts=("scène",))
    av = build_request(env.project("Audiovisuel"), SUBJECT_AV, "Un script.").with_updates(visual_prompts=("scène",))
    payload_c = json.dumps(mpt.build_payload(crypto, facts), ensure_ascii=False)
    payload_a = json.dumps(mpt.build_payload(av, facts), ensure_ascii=False)
    assert "Fill" not in payload_c and "broadcast" not in payload_c.lower() and "Web3" in payload_c
    assert "blockchain" not in payload_a and "crypto" not in payload_a.lower() and "Fill" in payload_a
    assert json.loads(payload_c)["bgm_type"] == "elevenlabs" and json.loads(payload_a)["bgm_type"] == "random"


# -- test sentinelle --------------------------------------------------------------------------------------------------------------
def test_sentinel_audiovisuel_words_never_reach_a_blockchain_project(env):
    """Audiovisuel = « broadcast », « mixing console », « FILL/KEY » partout ; LodyCrypto = sujet blockchain."""
    poison = "broadcast mixing console FILL/KEY AV-SENTINEL-7c1"
    av = _add_project(env, "Sentinelle AV", visual_style=poison, tone=poison[:60], description=poison,
                      settings={"example_request": poison, "brief": {
                          "audience": poison, "orientation": poison, "standing_instructions": poison,
                          "visual_rules": poison, "visual_avoid": [poison], "structure": [poison]}})
    crypto = _add_project(env, "Sentinelle Crypto", visual_style="Univers sombre, cyan, crypto et gaming CR-SENTINEL-2d9",
                          settings={"brief": {"audience": "Débutants CR-SENTINEL-2d9"}})
    launched_a = env.launch(av, "Sujet AV " + poison)
    launched_c = env.launch(crypto, SUBJECT_CRYPTO)
    # tout le contexte enregistré + la demande réellement envoyée + le payload du vrai connecteur
    request_sent = env.connector.submitted[1]
    contexts = [env.dump(launched_c.id), json.dumps(request_sent.to_dict(), ensure_ascii=False),
                json.dumps(mpt.build_payload(request_sent, mpt.EngineFacts()), ensure_ascii=False),
                json.dumps(mpt.MoneyPrinterTurboConnector("http://engine:8080", env.path.parent).trace_prompts(request_sent),
                           ensure_ascii=False)]
    for context in contexts:
        for expression in (*SENTINELS, "AV-SENTINEL-7c1"):
            assert expression.lower() not in context.lower(), f"{expression!r} présent dans le contexte LodyCrypto"
    assert "CR-SENTINEL-2d9" in contexts[0] and launched_a.id != launched_c.id
    # et l'inverse : rien de LodyCrypto chez le projet sentinelle AV
    assert "CR-SENTINEL-2d9" not in env.dump(launched_a.id)


def test_seeded_lodycrypto_negative_list_is_its_own_and_the_only_place_with_those_words(env):
    crypto, av = env.project("LodyCrypto"), env.project("Audiovisuel")
    prod = env.launch(crypto, SUBJECT_CRYPTO)
    stored = ProductionRepository(env.path).get(prod.id)
    assert list(build_request(crypto, "x").visual_avoid) == seeds._CRYPTO_VISUAL_AVOID
    assert build_request(av, "x").visual_avoid == () and brief_lib.DEFAULT_BRIEF["visual_avoid"] == []
    for scene in stored.storyboard:
        assert "Ne montre jamais :" in scene["prompt"]
        outside = scene["prompt"].split("Ne montre jamais :")[0]
        for expression in (*SENTINELS, "régie", "SDI", "PTZ", "multiview", "table de mixage"):
            assert expression.lower() not in outside.lower(), (expression, scene["index"])
        assert "aucun studio de télévision" in scene["prompt"] and "Univers sombre" in scene["prompt"]
    assert not any(word in storyboard_text for storyboard_text in [json.dumps(seeds.SEED_PROJECTS[0]["settings"])]
                   for word in ("SDI", "PTZ", "multiview"))   # l'Audiovisuel n'hérite d'aucune règle de LodyCrypto


# -- mutations croisées et défauts partagés -------------------------------------------------------------------------------------------
def test_no_cross_mutation_between_projects_or_defaults(env):
    before_defaults = (copy.deepcopy(brief_lib.DEFAULT_BRIEF), copy.deepcopy(projects_mod.DEFAULTS), copy.deepcopy(SEED_PROJECTS))
    crypto, av = env.project("LodyCrypto"), env.project("Audiovisuel")
    one = brief_lib.brief_settings(crypto.settings)
    one["structure"].append("MUTATION")
    one["visual_avoid"].append("MUTATION")
    crypto.settings["brief"]["structure"].append("MUTATION")
    other = brief_lib.brief_settings(env.project("LodyCrypto").settings)
    assert "MUTATION" not in other["structure"] and "MUTATION" not in other["visual_avoid"]
    assert brief_lib.brief_settings(av.settings)["structure"] == [] and brief_lib.brief_settings({})["visual_avoid"] == []
    assert (brief_lib.DEFAULT_BRIEF, projects_mod.DEFAULTS, SEED_PROJECTS) == before_defaults
    created = _add_project(env, "Neuf")
    created.platforms.append("MUTATION")
    created.settings["x"] = 1
    assert projects_mod.DEFAULTS["platforms"] == [] and projects_mod.DEFAULTS["settings"] == {}
    assert env.projects.get(created.id).platforms == [] and _add_project(env, "Neuf 2").settings == {}


def test_validation_does_not_mutate_the_callers_settings_and_seeds_are_not_aliased(env):
    settings = {"brief": {"structure": ["a"], "audience": "x"}}
    snapshot = copy.deepcopy(settings)
    projects_mod.validate_fields({**copy.deepcopy(projects_mod.DEFAULTS), "name": "Zed", "settings": settings})
    assert settings == snapshot
    created = env.project("LodyCrypto")
    created.settings["brief"]["visual_avoid"].append("MUTATION")
    assert "MUTATION" not in seeds._CRYPTO_VISUAL_AVOID and "MUTATION" not in SEED_PROJECTS[1]["settings"]["brief"]["visual_avoid"]


def test_platform_defaults_are_neutral_no_business_vocabulary(env):
    business = re.compile(r"broadcast|r[ée]gie|fill|key\b|crypto|blockchain|web3|nft|mixing|multiview|\bsdi\b|ptz|t[ée]l[ée]vision|wallet", re.I)
    neutral = json.dumps([projects_mod.DEFAULTS, brief_lib.DEFAULT_BRIEF], ensure_ascii=False)
    assert not business.search(neutral), business.search(neutral)
    blank = build_request(_add_project(env, "Vierge"), "Un sujet quelconque")
    assert blank.visual_style == "" and blank.visual_rules == "" and blank.visual_avoid == () and blank.instructions == ""
    prompt = storyboard.build_storyboard("Une phrase. Une autre phrase.", 2)[0].prompt
    assert not business.search(prompt), prompt
    payload = json.dumps(mpt.build_payload(blank.with_updates(script="x", visual_prompts=("a",)), mpt.EngineFacts()), ensure_ascii=False)
    assert not business.search(payload), business.search(payload)


# -- instantané immuable ----------------------------------------------------------------------------------------------------------------
def test_snapshot_is_kept_when_the_project_changes_afterwards(env):
    crypto = env.project("LodyCrypto")
    draft = env.service.prepare(crypto, SUBJECT_CRYPTO, provider_id="scripted")
    before = copy.deepcopy(draft.snapshot)
    env.projects.update(crypto.id, visual_style="TOUT NOUVEAU STYLE APRÈS PRÉPARATION", tone="Autre ton")
    running = env.service.confirm(draft.id, accept_partial=True)          # la confirmation utilise l'instantané, pas le projet
    sent = env.connector.submitted[0]
    assert sent.visual_style == crypto.visual_style and "NOUVEAU STYLE" not in json.dumps(sent.to_dict())
    stored = ProductionRepository(env.path).get(running.id)
    assert stored.snapshot == before and stored.snapshot["project"]["id"] == crypto.id
    assert all("NOUVEAU STYLE" not in scene["prompt"] for scene in stored.storyboard)
    later = env.service.prepare(env.project("LodyCrypto"), SUBJECT_CRYPTO + " bis", provider_id="scripted")
    assert later.snapshot["version"] != before["version"] and "NOUVEAU STYLE" in later.snapshot["request"]["visual_style"]


def test_snapshot_content_is_a_deep_independent_copy_with_origins(env):
    crypto = env.project("LodyCrypto")
    request = build_request(crypto, SUBJECT_CRYPTO)
    snap = make_snapshot(crypto, request, "2026-09-21T00:00:00+00:00")
    assert snap["project"] == {"id": crypto.id, "name": "LodyCrypto", "updated_at": crypto.updated_at}
    assert snap["origins"]["visual_style"] == "projet" and snap["origins"]["subject"] == "saisi par l’utilisateur"
    assert snap["origins"]["visual_avoid"] == "projet" and len(snap["version"]) == 10
    snap["request"]["structure"].append("MUTATION")
    assert "MUTATION" not in request.structure and "MUTATION" not in json.dumps(build_request(crypto, "x").to_dict())
    other = make_snapshot(env.project("Audiovisuel"), build_request(env.project("Audiovisuel"), SUBJECT_AV), "t")
    assert other["version"] != snap["version"] and other["project"]["id"] != snap["project"]["id"]


def test_v2_inherits_the_previous_snapshot_not_the_current_project(env):
    crypto = env.project("LodyCrypto")
    v1 = env.launch(crypto, SUBJECT_CRYPTO)
    env.connector.queue(env.connector.done("task-0001"))
    v1 = env.service.refresh(v1.id)
    env.projects.update(crypto.id, visual_style="STYLE MODIFIÉ APRÈS V1")
    v2 = env.service.create_v2(v1.id)
    assert v2.snapshot["inherited_from"] == v1.id and v2.snapshot["version"] == v1.snapshot["version"]
    assert "STYLE MODIFIÉ" not in json.dumps(v2.snapshot) and "STYLE MODIFIÉ" not in json.dumps(v2.params)
    assert env.service.repo.get(v1.id).snapshot["inherited_from"] is None


def test_confirmation_and_run_refuse_a_production_that_does_not_match_its_snapshot(env):
    crypto, av = env.project("LodyCrypto"), env.project("Audiovisuel")
    draft = env.service.prepare(crypto, SUBJECT_CRYPTO, provider_id="scripted")
    repo = ProductionRepository(env.path)
    # 1. production rattachée à un autre projet que celui de son instantané
    repo.transition(draft.id, [S.EN_ATTENTE_CONFIRMATION], snapshot={**draft.snapshot, "project": {"id": av.id, "name": "Audiovisuel"}})
    with pytest.raises(LaunchError, match="n’appartient pas au projet"):
        env.service.confirm(draft.id, accept_partial=True)
    # 2. paramètres à envoyer altérés (style d'un autre projet) : refus
    repo.transition(draft.id, [S.EN_ATTENTE_CONFIRMATION], snapshot=draft.snapshot)
    tampered = copy.deepcopy(draft.params)
    tampered["request"]["visual_style"] = av.visual_style
    repo.transition(draft.id, [S.EN_ATTENTE_CONFIRMATION], params=tampered)
    with pytest.raises(LaunchError, match="diffèrent de l’instantané"):
        env.service.confirm(draft.id, accept_partial=True)
    # 3. estimation sans instantané (antérieure à cette version)
    legacy = repo.create(project_id=crypto.id, subject=SUBJECT_CRYPTO, provider="scripted", status=S.EN_ATTENTE_CONFIRMATION,
                         params=draft.params)
    with pytest.raises(LaunchError, match="prépare-la à nouveau"):
        env.service.confirm(legacy.id, accept_partial=True)
    assert env.connector.calls.count("submit") == 0 and env.connector.calls.count("write_script") == 0
    # 4. défense au démarrage du worker : production confirmée mais incohérente → échec, aucun appel
    forced = repo.create(project_id=crypto.id, subject=SUBJECT_CRYPTO, provider="scripted", status=S.CONFIRMEE,
                         params=tampered, snapshot=draft.snapshot)
    env.service.run(forced.id)
    failed = repo.get(forced.id)
    assert failed.status is S.ECHEC and failed.error_code == ErrorKind.REJECTED.value
    assert env.connector.calls.count("submit") == 0


def test_snapshot_never_changes_during_or_after_the_run(env):
    crypto = env.project("LodyCrypto")
    draft = env.service.prepare(crypto, SUBJECT_CRYPTO, provider_id="scripted")
    running = env.service.confirm(draft.id, accept_partial=True)
    env.connector.queue(env.connector.done("task-0001"))
    done = env.service.refresh(running.id)
    assert done.snapshot == draft.snapshot and done.status is S.TERMINEE


# -- prompts finaux tracés ------------------------------------------------------------------------------------------------------------------
def test_final_prompts_and_origins_are_recorded_before_sending_even_if_sending_fails(env):
    env.connector.submit_error = ProviderError(ErrorKind.QUEUE_FULL, "File pleine.")
    failed = env.launch(env.project("LodyCrypto"), SUBJECT_CRYPTO)
    trace = ProductionRepository(env.path).get(failed.id).trace
    assert failed.status is S.ECHEC and trace["snapshot_version"] and trace["project"]["name"] == "LodyCrypto"
    assert trace["script_request"] in ({}, None)                          # le faux connecteur n'écrit pas de requête de script
    assert len(trace["scenes"]) == len(failed.storyboard) and all(s["prompt_sent"] == s["final_prompt"] for s in trace["scenes"])
    assert trace["image_template"]["applied"] is False
    assert {"Prompt éditorial du script", "Prompt système du script", "Gabarit d'images du moteur"} <= {o["item"] for o in trace["origins"]}
    assert all(o["origin"] for o in trace["origins"])


def test_real_connector_trace_shows_the_engine_template_appended_to_every_scene(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[app]\nopenai_image_prompt_template = "professional broadcast television visual explaining {term}, control room"\n',
                      encoding="utf-8")
    connector = mpt.MoneyPrinterTurboConnector("http://engine:8080", tmp_path, config, report_path=tmp_path / "none.json")
    trace = connector.trace_prompts(build_request(_bare_project(tmp_path), "s").with_updates(visual_prompts=("Scène crypto un", "Scène deux")))
    assert trace["image_template"]["applied"] is True and "GLOBAL" in trace["image_template"]["origin"]
    for scene, sent in zip(trace["scenes"], ("Scène crypto un", "Scène deux")):
        assert scene["engine_template_applied"] and scene["final_prompt"] == f"professional broadcast television visual explaining {sent}, control room"


def _bare_project(tmp_path):
    repo = ProjectRepository(tmp_path / "bare.sqlite3")
    repo.seed_defaults(SEED_PROJECTS)
    return next(p for p in repo.list_projects() if p.name == "LodyCrypto")


def test_final_prompts_are_readable_without_any_secret():
    dirty = "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789 et sk-abcdefghijklmnopqrstuvwxyz123456\nligne 2 gardée"
    clean = redact(dirty)
    assert "abcdefghijklmnopqrstuvwxyz" not in clean and "sk-" not in clean and "\nligne 2 gardée" in clean
    assert redact("Prompt normal — l’idée, 9:16.") == "Prompt normal — l’idée, 9:16."


# -- isolation après redémarrage et productions simultanées ----------------------------------------------------------------------
def test_isolation_survives_a_restart(tmp_path):
    env = Env(tmp_path, executor=DeferredExecutor())
    crypto, av = env.project("LodyCrypto"), env.project("Audiovisuel")
    d_c = env.service.prepare(crypto, SUBJECT_CRYPTO, provider_id="scripted")
    d_a = env.service.prepare(av, SUBJECT_AV, provider_id="scripted")
    snapshots = {d_c.id: d_c.snapshot, d_a.id: d_a.snapshot}
    restarted = env.new_service(SyncExecutor())                       # nouveau processus : mémoire vierge
    restarted.confirm(d_c.id, accept_partial=True)
    restarted.confirm(d_a.id, accept_partial=True)
    by_subject = {r.subject: r for r in env.connector.submitted}
    assert by_subject[SUBJECT_CRYPTO].visual_style == crypto.visual_style and by_subject[SUBJECT_AV].visual_style == av.visual_style
    for pid, snap in snapshots.items():
        assert ProductionRepository(env.path).get(pid).snapshot == snap


def test_two_simultaneous_productions_do_not_leak_into_each_other(tmp_path):
    env = Env(tmp_path, executor=DeferredExecutor())
    crypto, av = env.project("LodyCrypto"), env.project("Audiovisuel")
    a = env.service.confirm(env.service.prepare(av, SUBJECT_AV, provider_id="scripted").id, accept_partial=True)
    c = env.service.confirm(env.service.prepare(crypto, SUBJECT_CRYPTO, provider_id="scripted").id, accept_partial=True)
    assert env.service.repo.get(a.id).status is S.CONFIRMEE and env.service.repo.get(c.id).status is S.CONFIRMEE   # en attente ensemble
    threads = [threading.Thread(target=env.service.run, args=(pid,)) for pid in (a.id, c.id, a.id, c.id)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stored = {p.id: p for p in env.service.repo.list_by_status([S.EN_FILE, S.EN_COURS])}
    assert set(stored) == {a.id, c.id} and env.connector.calls.count("submit") == 2   # chaque production envoyée une seule fois
    for sent in env.connector.submitted:
        own = crypto if sent.subject == SUBJECT_CRYPTO else av
        other = av if own is crypto else crypto
        assert sent.visual_style == own.visual_style and other.visual_style not in json.dumps(sent.to_dict(), ensure_ascii=False)
        assert all(other.visual_style not in prompt for prompt in sent.visual_prompts)


def test_service_has_no_hidden_notion_of_the_last_project(env):
    """Aucune API du service ne lit « le dernier projet » : tout passe par le projet et la production explicites."""
    crypto, av = env.project("LodyCrypto"), env.project("Audiovisuel")
    d_c = env.service.prepare(crypto, SUBJECT_CRYPTO, provider_id="scripted")
    env.service.prepare(av, SUBJECT_AV, provider_id="scripted")                   # « dernier projet consulté » = Audiovisuel
    env.service.project_preflight(av, "scripted")
    env.service.confirm(d_c.id, accept_partial=True)
    assert env.connector.submitted[0].subject == SUBJECT_CRYPTO and env.connector.submitted[0].visual_style == crypto.visual_style
    assert "last" not in " ".join(vars(env.service)).lower() and not hasattr(env.service, "current_project")


# -- profil visuel dans les prompts ----------------------------------------------------------------------------------------------------------
def test_scene_prompts_carry_the_project_profile_and_never_truncate_the_negative_list():
    avoid = ("aucune régie de télévision", "aucun équipement SDI")
    long_sentence = "Un passage très long. " * 60
    for scene in storyboard.build_storyboard(long_sentence, 2, visual_style="Style A", visual_rules="Règle A", visual_avoid=avoid):
        assert scene.prompt.startswith("Style visuel : Style A. Consignes visuelles : Règle A.")
        assert scene.prompt.endswith("Ne montre jamais : aucune régie de télévision ; aucun équipement SDI.")
        assert len(scene.prompt) <= storyboard.MAX_PROMPT
    plain = storyboard.build_storyboard("Une phrase.", 1, visual_style="Style A")[0].prompt
    assert "Ne montre jamais" not in plain and "Consignes visuelles" not in plain


# -- migration des paramètres LodyCrypto ---------------------------------------------------------------------------------------------------------
def test_existing_lodycrypto_gets_the_visual_profile_without_overwriting_user_edits(tmp_path):
    repo = ProjectRepository(tmp_path / "m.sqlite3")
    repo.seed_defaults(SEED_PROJECTS)
    crypto = next(p for p in repo.list_projects() if p.name == "LodyCrypto")
    # simule le projet déployé AVANT cette version : brief stocké sans les clés du profil visuel (SQL brut)
    old_brief = {k: v for k, v in crypto.settings["brief"].items() if k not in seeds.ADDITIVE_BRIEF_KEYS}
    with sqlite3.connect(tmp_path / "m.sqlite3") as connection:
        connection.execute("UPDATE projects SET settings = ? WHERE id = ?", (json.dumps({**crypto.settings, "brief": old_brief}), crypto.id))
    assert "visual_avoid" not in repo.get(crypto.id).settings["brief"]
    assert repo.upgrade_seeds(SEED_PROJECTS) == 1                         # migration additive appliquée
    upgraded = repo.get(crypto.id).settings["brief"]
    assert upgraded["visual_avoid"] == seeds._CRYPTO_VISUAL_AVOID and upgraded["visual_rules"] == seeds._CRYPTO_VISUAL_RULES
    assert {k: v for k, v in upgraded.items() if k not in seeds.ADDITIVE_BRIEF_KEYS} == old_brief   # le reste est intact
    assert repo.upgrade_seeds(SEED_PROJECTS) == 0                         # idempotent
    # une valeur saisie par l'utilisateur n'est jamais écrasée (clés présentes, même modifiées ou vides)
    edited = {**repo.get(crypto.id).settings["brief"], "visual_avoid": ["ma liste"], "visual_rules": "mes règles"}
    repo.update(crypto.id, settings={**repo.get(crypto.id).settings, "brief": edited})
    assert repo.upgrade_seeds(SEED_PROJECTS) == 0 and repo.get(crypto.id).settings["brief"]["visual_avoid"] == ["ma liste"]
    av = next(p for p in repo.list_projects() if p.name == "Audiovisuel")
    assert "brief" not in av.settings                                     # Audiovisuel ne reçoit rien de LodyCrypto
