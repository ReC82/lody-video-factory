"""Mode démonstration : génération simulée de bout en bout, sans aucun fournisseur."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from lody.generation import demo
from lody.generation.demo import DemoConnector
from lody.generation.models import ProductionStatus as S
from lody.generation.service import ProductionService
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS
from test.lody.fakes import SyncExecutor

SUBJECT = "Explique simplement ce qu’est une blockchain aux débutants."


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _setup(tmp_path, clock):
    path = tmp_path / "lody.sqlite3"
    projects = ProjectRepository(path)
    projects.seed_defaults(SEED_PROJECTS)
    project = next(p for p in projects.list_projects() if p.name == "LodyCrypto")

    def service():  # un « processus » neuf à chaque appel
        connector = DemoConnector(tmp_path, clock=clock, step_seconds=1.0,
                                  make_video=lambda target: target.write_bytes(b"demo-video"))
        return ProductionService(ProductionRepository(path), {demo.PROVIDER_ID: connector}, SyncExecutor())

    return project, service


def test_full_simulated_generation_survives_a_restart(tmp_path):
    clock = Clock()
    project, service = _setup(tmp_path, clock)
    first = service()
    draft = first.prepare(project, SUBJECT, provider_id=demo.PROVIDER_ID)
    assert draft.status is S.EN_ATTENTE_CONFIRMATION and first.readiness(draft) == []
    running = first.confirm(draft.id)
    assert running.status is S.EN_FILE and running.external_task_id.startswith("demo-")
    stored = first.repo.get(draft.id)
    assert "démonstration" in stored.script and stored.storyboard and stored.visual_prompts
    seen = []
    for _ in range(3):
        clock.now += 1.0
        current = first.refresh(draft.id)
        seen.append((current.status, current.progress))
    assert seen == [(S.EN_COURS, 5), (S.EN_COURS, 20), (S.EN_COURS, 30)]
    restarted = service()  # redémarrage du conteneur en plein milieu
    resumed = restarted.resume_active()
    assert [p.status for p in resumed] == [S.EN_COURS]
    clock.now += 5.0
    done = restarted.refresh(draft.id)
    assert done.status is S.TERMINEE and done.video_duration == demo.DEMO_VIDEO_SECONDS and done.finished_at
    assert restarted.resolve_video(done).read_bytes() == b"demo-video"


def test_simulated_v2_full_cycle_keeps_v1_intact(tmp_path):
    clock = Clock()
    project, service = _setup(tmp_path, clock)
    svc = service()
    v1 = svc.confirm(svc.prepare(project, SUBJECT, provider_id=demo.PROVIDER_ID).id)
    clock.now += 10
    v1 = svc.refresh(v1.id)
    v2 = svc.create_v2(v1.id)
    edited = v1.script + " Et une phrase ajoutée pour la deuxième version."
    ready = svc.prepare_v2(v2.id, edited)
    launched = svc.confirm(ready.id)
    assert launched.external_task_id != v1.external_task_id and launched.status is S.EN_FILE
    clock.now += 10
    v2_done = svc.refresh(v2.id)
    assert v2_done.status is S.TERMINEE and v2_done.script == edited and v2_done.parent_production_id == v1.id
    assert svc.repo.get(v1.id) == v1


def test_same_key_never_creates_two_simulations(tmp_path):
    clock = Clock()
    connector = DemoConnector(tmp_path, clock=clock, make_video=lambda t: t.write_bytes(b"x"))
    from lody.generation.models import GenerationRequest

    first = connector.submit(GenerationRequest(subject="sujet"), "cle-1")
    clock.now += 100
    assert connector.submit(GenerationRequest(subject="sujet"), "cle-1") == first
    assert connector.poll(first).state.value == "done"  # la date d'origine est conservée


def test_unknown_or_tampered_demo_tasks_are_safe(tmp_path):
    from lody.generation.models import ExternalTask, RemoteState

    connector = DemoConnector(tmp_path, make_video=lambda t: t.write_bytes(b"x"))
    assert connector.poll(ExternalTask("demo", "demo-doesnotexist")).state is RemoteState.UNKNOWN
    assert connector.poll(ExternalTask("demo", "../../etc")).state is RemoteState.UNKNOWN
    with pytest.raises(ValueError):
        connector.resolve_asset(ExternalTask("demo", "demo-abcdef123456"), "demo/demo-abcdef123456/../../lody.sqlite3")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg absent de cette machine")
def test_default_video_maker_produces_a_real_short_video(tmp_path):
    target = tmp_path / "final-1.mp4"
    demo.default_video_maker(target)
    assert target.stat().st_size > 1000
    probe = shutil.which("ffprobe")
    if probe:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(target)],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert 7 <= float(out) <= 9
