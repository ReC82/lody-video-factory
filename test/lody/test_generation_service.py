"""Service de production avec un faux connecteur : confirmation unique, statuts, reprise, V2, erreurs."""

from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from lody.generation.costing import PriceBook
from lody.generation.models import (
    ErrorKind,
    GenerationResult,
    ProductionStatus as S,
    ProviderError,
    ReadinessIssue,
    RemoteState,
    TaskSnapshot,
)
from lody.generation.provider import estimate_units
from lody.generation.service import (
    AlreadyRunning,
    InvalidInput,
    LaunchError,
    ProductionService,
    request_of,
)
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS
from test.lody.fakes import SCRIPT, DeferredExecutor, ScriptedConnector, SyncExecutor

FULL_BOOK = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                              ("voice", "elevenlabs"): Decimal("0.30"), ("music", "elevenlabs"): Decimal("0.10")})
SUBJECT = "Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming."


class Env:
    def __init__(self, tmp_path, book=FULL_BOOK, executor=None, connector=None):
        self.path = tmp_path / "lody.sqlite3"
        projects = ProjectRepository(self.path)
        projects.seed_defaults(SEED_PROJECTS)
        self.project = next(p for p in projects.list_projects() if p.name == "LodyCrypto")
        self.connector = connector or ScriptedConnector(tmp_path / "storage")
        self.executor = executor or SyncExecutor()
        self.book = book
        self.service = self._service()

    def _service(self, executor=None):
        return ProductionService(ProductionRepository(self.path), {"scripted": self.connector},
                                 executor or self.executor, price_book=lambda: self.book)

    def restart(self, executor=None):
        """Nouveau processus : mêmes données, mémoire vierge."""
        self.service = self._service(executor)
        return self.service


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def _prepare(env, **kwargs):
    return env.service.prepare(env.project, SUBJECT, provider_id="scripted", **kwargs)


# -- estimation et confirmation --------------------------------------------------------------------
def test_prepare_persists_a_draft_with_estimate_and_launches_nothing(env):
    draft = _prepare(env)
    assert draft.status is S.EN_ATTENTE_CONFIRMATION and draft.confirmed_at is None
    assert draft.cost_currency == "EUR" and draft.cost_high > draft.cost_low > 0 and not draft.cost_partial
    assert [line["component"] for line in draft.cost_detail["lines"]] == ["text", "visual", "voice", "music"]
    assert request_of(draft).duration_min == 45 and request_of(draft).voice.name.startswith("Kev")
    assert draft.brief["demande"] == SUBJECT
    assert env.connector.calls == [] and env.executor.jobs == 0  # aucun appel : ni prêt, ni script, ni envoi


def test_preparing_again_updates_the_same_draft(env):
    first = _prepare(env)
    second = env.service.prepare(env.project, SUBJECT + " (version courte)", provider_id="scripted", draft_id=first.id)
    assert second.id == first.id and "courte" in second.subject
    assert len(env.service.repo.list_for_project(env.project.id)) == 1


def test_subject_and_script_validation(env):
    for bad in ("court", "x" * 2001, "ma clé sk-abcdefghijklmnopqrstuvwxyz123456 voilà"):
        with pytest.raises(InvalidInput):
            env.service.prepare(env.project, bad, provider_id="scripted")
    with pytest.raises(InvalidInput):
        env.service.prepare(env.project, SUBJECT, provider_id="scripted", script="trop court")


def test_partial_total_when_a_rate_is_missing_requires_explicit_acknowledgement(tmp_path):
    env = Env(tmp_path, book=PriceBook("EUR", {("visual", "openai_image"): Decimal("0.04")}))
    draft = _prepare(env)
    assert draft.cost_partial and draft.cost_high is not None
    unpriced = [line["component"] for line in draft.cost_detail["lines"] if line["status"] == "unpriced"]
    assert unpriced == ["text", "voice", "music"]
    with pytest.raises(LaunchError):
        env.service.confirm(draft.id)
    assert env.service.repo.get(draft.id).status is S.EN_ATTENTE_CONFIRMATION and env.executor.jobs == 0
    assert env.service.confirm(draft.id, accept_partial=True).status is not S.EN_ATTENTE_CONFIRMATION


def test_no_price_book_at_all_is_a_partial_estimate_without_amounts(tmp_path):
    env = Env(tmp_path, book=PriceBook())
    draft = _prepare(env)
    assert draft.cost_partial and draft.cost_low is None and draft.cost_high is None


def test_blocking_readiness_prevents_confirmation_but_a_warning_does_not(tmp_path):
    env = Env(tmp_path, connector=ScriptedConnector(tmp_path / "s", issues=[ReadinessIssue("engine_unreachable", "Moteur injoignable.")]))
    draft = _prepare(env)
    with pytest.raises(LaunchError) as error:
        env.service.confirm(draft.id)
    assert [i.message for i in error.value.issues] == ["Moteur injoignable."]
    assert env.connector.calls == ["check_ready"] and env.executor.jobs == 0
    env.connector.issues = [ReadinessIssue("voice_model_differs", "Modèle différent.", blocking=False)]
    assert env.service.confirm(draft.id).status is not S.EN_ATTENTE_CONFIRMATION


# -- lancement unique et idempotence --------------------------------------------------------------------
def test_one_confirmation_runs_script_storyboard_and_submit_automatically(env):
    draft = _prepare(env)
    running = env.service.confirm(draft.id)
    assert env.executor.jobs == 1
    assert running.status is S.EN_FILE and running.external_task_id == "task-0001"
    assert running.confirmed_at and running.started_at and running.progress == 0
    assert env.connector.calls.count("write_script") == 1 and env.connector.calls.count("submit") == 1
    stored = env.service.repo.get(draft.id)
    assert stored.script == SCRIPT and stored.script_source == "generated"
    # nombre de scènes calculé sur la durée réelle du script (ici court), dans la fourchette du rythme visuel
    low, high = estimate_units(request_of(stored)).scenes
    assert low <= len(stored.storyboard) <= high + 1 and len(stored.visual_prompts) == len(stored.storyboard)
    assert env.connector.submitted[0].script == SCRIPT and list(env.connector.submitted[0].visual_prompts) == stored.visual_prompts
    assert all("Univers sombre" in prompt for prompt in stored.visual_prompts)


def test_double_click_never_creates_two_paid_tasks(env):
    draft = _prepare(env)
    env.service.confirm(draft.id)
    env.service.confirm(draft.id)
    env.service.confirm(draft.id)
    assert env.connector.calls.count("submit") == 1 and env.executor.jobs == 1


def test_concurrent_confirmations_launch_exactly_once(tmp_path):
    env = Env(tmp_path)
    draft = _prepare(env)
    barrier = threading.Barrier(8)

    def click():
        barrier.wait()
        try:
            env.service.confirm(draft.id)
        except LaunchError:
            pass

    with ThreadPoolExecutor(8) as pool:
        for _ in [pool.submit(click) for _ in range(8)]:
            pass
    assert env.connector.calls.count("submit") == 1 and env.connector.calls.count("write_script") == 1


def test_a_second_generation_is_refused_while_one_is_running_for_the_project(env):
    first = env.service.confirm(_prepare(env).id)
    second = env.service.prepare(env.project, SUBJECT + " autre", provider_id="scripted")
    with pytest.raises(AlreadyRunning) as error:
        env.service.confirm(second.id)
    assert error.value.production_id == first.id and env.connector.calls.count("submit") == 1


def test_supplied_script_skips_the_text_call(env):
    draft = _prepare(env, script=SCRIPT)
    assert draft.script_source == "manual"
    env.service.confirm(draft.id)
    assert "write_script" not in env.connector.calls and env.connector.submitted[0].script == SCRIPT


# -- suivi et statuts -------------------------------------------------------------------------------------------
def test_status_progression_to_success_keeps_script_storyboard_and_metadata(env):
    production = env.service.confirm(_prepare(env).id)
    env.connector.queue(TaskSnapshot(RemoteState.QUEUED, 0, "En attente"),
                        TaskSnapshot(RemoteState.RUNNING, 25, "Génération de la voix"),
                        TaskSnapshot(RemoteState.RUNNING, 45, "Génération des images", warnings=("w1",)),
                        env.connector.done("task-0001", warnings=("elevenlabs_bgm_failed",)))
    seen = []
    for _ in range(4):
        current = env.service.refresh(production.id)
        seen.append((current.status, current.progress, current.current_step))
    assert seen == [(S.EN_FILE, 0, "En attente"), (S.EN_COURS, 25, "Génération de la voix"),
                    (S.EN_COURS, 45, "Génération des images"), (S.TERMINEE, 100, "Terminée")]
    done = env.service.repo.get(production.id)
    assert done.video_ref == "tasks/task-0001/final-1.mp4" and done.video_duration == 52.4 and done.finished_at
    assert done.script == SCRIPT and done.storyboard and done.visual_prompts and done.assets
    assert "elevenlabs_bgm_failed" in done.warnings
    assert env.service.resolve_video(done).read_bytes() == b"fake-mp4"
    # une production terminée n'est plus interrogée
    calls = env.connector.calls.count("poll")
    env.service.refresh(production.id)
    assert env.connector.calls.count("poll") == calls


def test_engine_failure_becomes_a_readable_echec(env):
    production = env.service.confirm(_prepare(env).id)
    error = ProviderError(ErrorKind.QUOTA, "Le fournisseur signale un quota ou un crédit insuffisant. (échec pendant les images).")
    env.connector.queue(TaskSnapshot(RemoteState.FAILED, 40, error=error))
    failed = env.service.refresh(production.id)
    assert failed.status is S.ECHEC and failed.error_code == "quota" and "quota" in failed.error_message and failed.finished_at
    assert failed.script == SCRIPT and failed.storyboard  # rien n'est perdu


@pytest.mark.parametrize("kind", [ErrorKind.KEY_MISSING, ErrorKind.QUOTA, ErrorKind.PROVIDER, ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE])
def test_script_or_submit_errors_fail_cleanly_without_a_task(env, kind):
    env.connector.script_error = ProviderError(kind, "Message lisible.")
    failed = env.service.confirm(_prepare(env).id)
    assert failed.status is S.ECHEC and failed.error_code == kind.value and failed.external_task_id is None
    assert "submit" not in env.connector.calls


def test_submit_error_keeps_the_generated_script(env):
    env.connector.submit_error = ProviderError(ErrorKind.QUEUE_FULL, "La file du moteur est pleine.")
    failed = env.service.confirm(_prepare(env).id)
    assert failed.status is S.ECHEC and failed.script == SCRIPT and failed.storyboard and failed.external_task_id is None


def test_unexpected_exception_never_leaks_its_text(env):
    env.connector.script_error = None

    def boom(request, narrative_block=""):
        raise RuntimeError("Bearer abcdefghijklmnopqrstuvwxyz0123456789 sk-abcdefghijklmnopqrstuvwxyz123456")

    env.connector.write_script = boom
    failed = env.service.confirm(_prepare(env).id)
    assert failed.status is S.ECHEC and "sk-" not in failed.error_message and "Bearer" not in failed.error_message


def test_engine_unreachable_while_polling_keeps_the_production_alive(env):
    production = env.service.confirm(_prepare(env).id)
    env.connector.queue(ProviderError(ErrorKind.UNAVAILABLE, "Le moteur de génération est injoignable pour le moment."))
    kept = env.service.refresh(production.id)
    assert kept.status is S.EN_FILE and kept.error_code == "unavailable" and kept.external_task_id
    env.connector.queue(TaskSnapshot(RemoteState.RUNNING, 25, "Voix"))
    healed = env.service.refresh(production.id)
    assert healed.status is S.EN_COURS and healed.error_code == "" and healed.error_message == ""


def test_finished_task_whose_video_is_missing_is_an_error(env):
    env.connector.write_video = False
    production = env.service.confirm(_prepare(env).id)
    env.connector.queue(env.connector.done("task-0001"))
    failed = env.service.refresh(production.id)
    assert failed.status is S.ECHEC and failed.error_code == "video_missing"


def test_unknown_task_after_engine_restart_is_recovered_from_disk_or_reported(env):
    production = env.service.confirm(_prepare(env).id)
    env.connector.queue(TaskSnapshot(RemoteState.UNKNOWN))
    failed = env.service.refresh(production.id)
    assert failed.status is S.ECHEC and failed.error_code == "unknown_task" and "Rien n’a été relancé" in failed.error_message
    # variante : la vidéo est bien sur disque
    other = Env(env.path.parent / "b")
    running = other.service.confirm(other.service.prepare(other.project, SUBJECT, provider_id="scripted").id)
    other.connector.done("task-0001")  # écrit le fichier
    other.connector.recover_result = GenerationResult("tasks/task-0001/final-1.mp4", None)
    other.connector.queue(TaskSnapshot(RemoteState.UNKNOWN))
    assert other.service.refresh(running.id).status is S.TERMINEE


# -- reprise après redémarrage ---------------------------------------------------------------------------------------
def test_restart_resumes_running_productions_by_asking_the_engine(env):
    production = env.service.confirm(_prepare(env).id)
    env.connector.queue(TaskSnapshot(RemoteState.RUNNING, 30, "Sous-titres"))
    env.restart()
    resumed = env.service.resume_active()
    assert [(p.id, p.status, p.progress) for p in resumed] == [(production.id, S.EN_COURS, 30)]
    env.connector.queue(env.connector.done("task-0001"))
    assert env.service.resume_active() == [env.service.repo.get(production.id)]
    assert env.service.repo.get(production.id).status is S.TERMINEE
    assert env.connector.calls.count("submit") == 1  # jamais de nouvel envoi


def test_restart_with_an_unreachable_engine_does_not_mark_anything_lost(env):
    production = env.service.confirm(_prepare(env).id)
    env.restart()
    env.connector.queue(ProviderError(ErrorKind.UNAVAILABLE, "injoignable"))
    env.service.resume_active()
    assert env.service.repo.get(production.id).status is S.EN_FILE


def test_restart_before_the_send_never_resubmits_a_paid_call(tmp_path):
    env = Env(tmp_path, executor=DeferredExecutor())
    production = env.service.confirm(_prepare(env).id)
    assert env.service.repo.get(production.id).status is S.CONFIRMEE and env.executor.pending  # worker jamais lancé
    env.restart(SyncExecutor())
    env.service.resume_active()
    failed = env.service.repo.get(production.id)
    assert failed.status is S.ECHEC and failed.error_code == "interrupted" and failed.external_task_id is None
    assert "submit" not in env.connector.calls and "write_script" not in env.connector.calls


# -- V2 --------------------------------------------------------------------------------------------------------------------
def _finished_v1(env):
    production = env.service.confirm(_prepare(env).id)
    env.connector.queue(env.connector.done("task-0001"))
    return env.service.refresh(production.id)


def test_v2_links_to_v1_edits_the_script_recomputes_cost_and_needs_a_new_confirmation(env):
    v1 = _finished_v1(env)
    snapshot_before = env.service.repo.get(v1.id)
    submits = env.connector.calls.count("submit")
    v2 = env.service.create_v2(v1.id)
    assert (v2.version, v2.parent_production_id, v2.root_production_id) == (2, v1.id, v1.id)
    assert v2.status is S.BROUILLON and v2.script == v1.script and v2.subject == v1.subject
    assert request_of(v2).to_dict()["voice"] == request_of(v1).to_dict()["voice"]
    assert env.connector.calls.count("submit") == submits  # créer une V2 ne lance rien
    edited = SCRIPT + " Une dernière phrase ajoutée à la main pour la V2."
    prepared = env.service.prepare_v2(v2.id, edited)
    assert prepared.status is S.EN_ATTENTE_CONFIRMATION and prepared.script == edited and prepared.script_source == "manual"
    assert prepared.cost_detail["lines"][0]["status"] == "free"  # script fourni : plus d'appel texte
    assert prepared.cost_high < v1.cost_high
    assert prepared.storyboard and "dernière phrase" in prepared.storyboard[-1]["narration"]
    assert env.connector.calls.count("submit") == submits  # toujours rien : attente de confirmation
    launched = env.service.confirm(prepared.id)
    assert launched.status is S.EN_FILE and launched.external_task_id == "task-0002"
    assert env.connector.calls.count("write_script") == 1  # le script de la V2 n'est pas réécrit
    assert env.connector.submitted[1].script == edited
    assert snapshot_before == env.service.repo.get(v1.id)  # V1 strictement inchangée


def test_v2_with_an_unchanged_script_is_labelled_as_such_and_v1_stays_intact(env):
    v1 = _finished_v1(env)
    v2 = env.service.prepare_v2(env.service.create_v2(v1.id).id, v1.script)
    assert v2.script_source == "previous"
    assert env.service.repo.get(v1.id).status is S.TERMINEE


def test_v2_needs_a_valid_edited_script_and_cannot_be_modified_once_launched(env):
    v1 = _finished_v1(env)
    v2 = env.service.create_v2(v1.id)
    with pytest.raises(InvalidInput):
        env.service.prepare_v2(v2.id, "court")
    prepared = env.service.prepare_v2(v2.id, SCRIPT)
    env.service.confirm(prepared.id)
    with pytest.raises(LaunchError):
        env.service.prepare_v2(v2.id, SCRIPT + " modif tardive")


def test_v2_of_a_running_or_scriptless_production_is_refused(env):
    running = env.service.confirm(_prepare(env).id)
    # en cours mais son script existe déjà : autorisé ; sans script : refusé
    env.service.repo.update(running.id, script="")
    with pytest.raises(LaunchError):
        env.service.create_v2(running.id)


# -- sécurité des données ---------------------------------------------------------------------------------------------------------
def test_video_path_resolution_is_bound_to_the_production(env):
    v1 = _finished_v1(env)
    other_file = env.connector.root / "tasks" / "task-0009" / "final-1.mp4"
    other_file.parent.mkdir(parents=True)
    other_file.write_bytes(b"other")
    env.service.repo.update(v1.id, video_ref="tasks/task-0009/final-1.mp4")
    with pytest.raises(ValueError):
        env.service.resolve_video(env.service.repo.get(v1.id))
    env.service.repo.update(v1.id, video_ref="tasks/task-0001/../task-0009/final-1.mp4")
    with pytest.raises(ValueError):
        env.service.resolve_video(env.service.repo.get(v1.id))


def test_no_secret_reaches_the_database(env):
    v1 = _finished_v1(env)
    env.service.create_v2(v1.id)
    raw = b"".join(path.read_bytes() for path in env.path.parent.glob("lody.sqlite3*"))
    assert not re.search(rb"sk-[A-Za-z0-9]{12,}|api_key|Bearer |FAKE-", raw)


def test_end_to_end_with_the_real_connector_leaks_no_secret_to_database_or_logs(tmp_path, caplog):
    """Service + vrai connecteur + moteur factice, config contenant des valeurs identifiables."""
    from lody.generation import mpt_connector as mpt
    from test.lody.test_generation_connector import CONFIG, TASK, Recorder

    caplog.set_level("DEBUG")
    folder = tmp_path / "storage" / "tasks" / TASK
    folder.mkdir(parents=True)
    (folder / "final-1.mp4").write_bytes(b"video")
    config = tmp_path / "config.toml"
    config.write_text(CONFIG, encoding="utf-8")
    transport = Recorder(
        (200, b'"pong"'),                                                            # check_ready
        (200, {"status": 200, "data": {"video_script": SCRIPT}}),                    # /scripts
        (200, {"status": 200, "data": {"task_id": TASK}}),                           # /videos
        (200, {"status": 200, "data": {"task_id": TASK, "state": 4, "progress": 45}}),
        (200, {"status": 200, "data": {"task_id": TASK, "state": 1, "progress": 100, "audio_duration": 51.0,
                                       "videos": [f"/tasks/{TASK}/final-1.mp4"], "script": SCRIPT}}),
    )
    connector = mpt.MoneyPrinterTurboConnector("http://engine:8080", tmp_path / "storage", config, transport=transport)
    projects = ProjectRepository(tmp_path / "lody.sqlite3")
    projects.seed_defaults(SEED_PROJECTS)
    project = next(p for p in projects.list_projects() if p.name == "LodyCrypto")
    service = ProductionService(ProductionRepository(tmp_path / "lody.sqlite3"), {mpt.PROVIDER_ID: connector},
                                SyncExecutor(), price_book=lambda: FULL_BOOK)
    draft = service.prepare(project, SUBJECT, provider_id=mpt.PROVIDER_ID)
    running = service.confirm(draft.id)
    assert running.status is S.EN_FILE and running.external_task_id == TASK
    assert service.refresh(draft.id).status is S.EN_COURS
    done = service.refresh(draft.id)
    assert done.status is S.TERMINEE and done.video_duration == 51.0
    assert [(m, u.split("8080")[1]) for m, u, *_ in transport.requests] == [
        ("GET", "/ping"), ("POST", "/api/v1/scripts"), ("POST", "/api/v1/videos"),
        ("GET", f"/api/v1/tasks/{TASK}"), ("GET", f"/api/v1/tasks/{TASK}")]
    sent = json.dumps([request[2] for request in transport.requests])
    stored = b"".join(path.read_bytes() for path in tmp_path.glob("lody.sqlite3*")) + repr(done).encode()
    logs = caplog.text
    for value in ("FAKE-CONFIG-VALUE-NOT-A-KEY", "FAKE-IMAGE-VALUE-NOT-A-KEY", "FAKE-ELEVEN-VALUE-NOT-A-KEY"):
        assert value not in sent and value.encode() not in stored and value not in logs


# -- typographie du script : « l’ idée » ne doit jamais atteindre le TTS ni les sous-titres ---------------------------------
def test_generated_script_is_normalised_before_storage_and_sending(env):
    env.connector.write_script = lambda request, narrative_block="": (
        "Mais l’  idée de base est simple, c’ est un registre. Aujourd’ hui, qu’ il n’ y a rien.")
    running = env.service.confirm(_prepare(env).id)
    stored = env.service.repo.get(running.id)
    assert stored.script == "Mais l’idée de base est simple, c’est un registre. Aujourd’hui, qu’il n’y a rien."
    assert env.connector.submitted[0].script == stored.script            # même texte : narration, SRT et affichage
    assert stored.storyboard[0]["narration"].startswith("Mais l’idée")


def test_supplied_and_edited_scripts_are_normalised(env):
    raw = "Voici l’  idée : c’ est simple. " * 3
    draft = _prepare(env, script=raw)
    assert "’ " not in draft.script and draft.script.startswith("Voici l’idée : c’est simple.")
    v1 = _finished_v1(env)
    v2 = env.service.prepare_v2(env.service.create_v2(v1.id).id, SCRIPT + " Et qu’ il n’ y a plus d’ espace.")
    assert v2.script.endswith("Et qu’il n’y a plus d’espace.")


def test_other_languages_keep_their_script_untouched(env):
    from lody.generation.service import build_request  # noqa: F401
    from lody.projects import ProjectRepository

    projects = ProjectRepository(env.path)
    english = projects.update(env.project.id, language="en-US")
    text = "The dogs' toys and l' idea stay as they are, said 'Bob' today."
    draft = env.service.prepare(english, SUBJECT, provider_id="scripted", script=text + " " + text)
    assert draft.script == text + " " + text
