"""Fournisseur « Animation 2D », worker de rendu et modes de projet (#120).

Aucun réseau, aucun fournisseur payant, aucun vrai config.toml : le moteur de scène est un faux (ffmpeg local
qui produit une mire de la bonne durée) ; ffmpeg est celui du système ou d'imageio-ffmpeg.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from lody.animation import modes
from lody.animation import worker as w
from lody.animation.assets import AnimationAssetRepository
from lody.animation.freeze import freeze_document
from lody.generation.animation_connector import AnimationConnector, spawn_worker
from lody.generation.models import (
    Capability,
    CapabilityState,
    ExternalTask,
    GenerationRequest,
    ProductionStatus as S,
    RemoteState,
)
from lody.generation.service import ProductionService, build_request, make_snapshot, request_of
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository, ProjectValidationError
from test.lody.fakes import SyncExecutor
from test.lody.test_generation_service import FULL_BOOK, SUBJECT

FFMPEG = w.ffmpeg_exe()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg indisponible")
FPS, WIDTH, HEIGHT = 24, 320, 568
FAKE_VALUE = "FAKE-APP-VALUE-NOT-A-KEY"


# -- fixtures ----------------------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _remotion_evaluation(monkeypatch):
    """#154 : licence Remotion non décidée → rendu permis seulement en évaluation DEV explicite."""
    monkeypatch.delenv("LODY_REMOTION_LICENSE", raising=False)
    monkeypatch.setenv("LODY_REMOTION_EVALUATION", "1")


def _file(root: Path, relative: str, content: bytes | None = None) -> dict:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        Image.new("RGB", (64, 112), (40, 120, 60)).save(path, format="PNG")
    else:
        path.write_bytes(content)
    return {"path": relative, "sha256": w.sha256_file(path)}


class World:
    """Base, données et catalogue d'un ou plusieurs projets, avec un fournisseur 2D sans processus détaché."""

    def __init__(self, tmp_path: Path):
        self.data = tmp_path / "data"
        self.db = tmp_path / "lody.sqlite3"
        self.projects = ProjectRepository(self.db)
        self.assets = AnimationAssetRepository(self.db)
        self.launched: list[Path] = []
        self.connector = AnimationConnector(root=self.data, asset_repo=self.assets, launcher=self.launched.append,
                                            renderer=f"{sys.executable} -c pass", ffmpeg=FFMPEG)

    def project(self, name: str, mode: str = modes.ANIMATION):
        return self.projects.create(name=name, settings={modes.SETTINGS_KEY: mode})

    def catalog(self, project_id: str, tag: str) -> tuple[str, str]:
        hero = self.assets.create(project_id, kind="character", name=f"Héros {tag}")
        svg = _file(self.data, f"animation-assets/{tag}/hero.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
        self.assets.add_version(project_id, hero.id, {
            "format": "svg", "width": 600, "height": 900, "expressions": ["joie"], "gestures": ["salut"],
            "files": [{"role": "base", **svg}]})
        decor = self.assets.create(project_id, kind="background", name=f"Décor {tag}")
        png = _file(self.data, f"animation-assets/{tag}/decor.png")
        self.assets.add_version(project_id, decor.id, {"format": "png", "width": 64, "height": 112,
                                                       "files": [{"role": "base", **png}]})
        return hero.id, decor.id

    def storyboard(self, project_id: str, tag: str = "a") -> dict:
        hero, decor = self.catalog(project_id, tag)

        def scene(scene_id: str, narration: str, fallback: dict) -> dict:
            return {"id": scene_id, "duration_frames": FPS, "intent": {"narration": narration},
                    "render": {"layers": [
                        {"id": "decor", "objects": [{"id": "fond", "type": "asset",
                                                     "asset": {"id": decor, "version": "latest"},
                                                     "position": [{"frame": 0, "x": 160, "y": 284}]}]},
                        {"id": "perso", "objects": [{"id": "heros", "type": "asset",
                                                     "asset": {"id": hero, "version": "latest"},
                                                     "position": [{"frame": 0, "x": 100, "y": 400}],
                                                     "gestures": [{"start": 0, "end": 12, "name": "salut"}]}]}],
                        "speech": {"speaker": "heros", "cues": [{"text": narration.split()[0], "start": 2, "end": 20}]},
                        "fallback": fallback}}

        raw = {"schema": "lody.animation/1", "canvas": {"fps": FPS, "width": WIDTH, "height": HEIGHT}, "scenes": [
            scene("accueil", "Bonjour tout le monde, voici une animation de test.", {"type": "still", "frame": 5}),
            scene("suite", "Ensuite une deuxième scène pour vérifier la reprise.",
                  {"type": "asset", "asset": {"id": decor, "version": "latest"}}),
        ]}
        return freeze_document(self.assets, project_id, raw)

    def request(self, project, storyboard: dict, mode: str | None = None) -> GenerationRequest:
        return build_request(project, SUBJECT).with_updates(
            render_mode=mode or modes.project_render_mode(project.settings),
            animation={"project_id": project.id, "storyboard": storyboard})


class FakeRenderer:
    """Moteur de scène factice : une mire ffmpeg de la durée exacte de la scène ; échecs programmables."""

    def __init__(self, fail: set[int] = frozenset(), on_render=None):
        self.fail = set(fail)
        self.rendered: list[int] = []
        self.on_render = on_render

    def render(self, scene_input, output, timeout, tick):
        data = json.loads(Path(scene_input).read_text(encoding="utf-8"))
        tick()
        assert all(Path(p).is_file() and str(p).startswith(str(Path(scene_input).parents[1]))
                   for files in data["assets"].values() for p in files.values())  # fichiers DU dossier de la tâche
        if data["index"] in self.fail:
            raise w.SceneRenderError("échec simulé")
        canvas = data["canvas"]
        seconds = data["scene"]["duration_frames"] / canvas["fps"]
        import subprocess

        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"testsrc2=size={canvas['width']}x{canvas['height']}:rate={canvas['fps']}",
                        "-t", f"{seconds:.3f}", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                        str(output)], check=True, capture_output=True, timeout=60)
        self.rendered.append(data["index"])
        if self.on_render:
            self.on_render(data["index"])

    def still(self, scene_input, frame, output, timeout, tick):
        raise w.SceneRenderError("pas d'image fixe")


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _submit(world: World, project, storyboard, mode=None, key="prd_000000000001-run"):
    task = world.connector.submit(world.request(project, storyboard, mode), key)
    return task, w.find_job_dir(world.data, task.task_id)


# -- modes de projet et non-régression du pipeline historique ---------------------------------------------------
def test_render_mode_defaults_to_classic_and_maps_providers():
    assert modes.project_render_mode({}) == modes.CLASSIC
    assert modes.project_render_mode({"render_mode": "inconnu"}) == modes.CLASSIC
    assert modes.provider_for_mode(modes.CLASSIC, "historique") == "historique"
    assert modes.provider_for_mode(modes.ANIMATION, "historique") == modes.provider_for_mode(modes.HYBRID, "x") == "animation_2d"


def test_project_render_mode_is_validated_and_stored(tmp_path):
    projects = ProjectRepository(tmp_path / "lody.sqlite3")
    project = projects.create(name="Hybride", settings={"render_mode": modes.HYBRID})
    assert modes.project_render_mode(projects.get(project.id).settings) == modes.HYBRID
    with pytest.raises(ProjectValidationError) as error:
        projects.update(project.id, settings={"render_mode": "3d"})
    assert "render_mode" in error.value.errors


def test_classic_request_and_snapshot_are_unchanged(tmp_path):
    projects = ProjectRepository(tmp_path / "lody.sqlite3")
    project = projects.create(name="Classique")
    before = make_snapshot(project, build_request(project, SUBJECT), "t")
    assert "render_mode" not in before["request"] and "animation" not in before["request"]  # mêmes clés qu'avant #120
    explicit = projects.update(project.id, settings={**project.settings, "render_mode": modes.CLASSIC})
    after = make_snapshot(explicit, build_request(explicit, SUBJECT), "t")
    assert after["request"] == before["request"] and after["version"] == before["version"]
    animated = projects.update(project.id, settings={**project.settings, "render_mode": modes.ANIMATION})
    assert build_request(animated, SUBJECT).to_dict()["render_mode"] == modes.ANIMATION


# -- préflight -----------------------------------------------------------------------------------------------------
def test_preflight_explains_what_is_missing_with_the_setting_to_fix(world):
    project = world.project("Sans storyboard")
    report = world.connector.preflight(build_request(project, SUBJECT))
    visual = report.get(Capability.VISUAL)
    assert visual.state is CapabilityState.NOT_CONFIGURED and visual.fix == "project" and "storyboard" in visual.message
    assert not report.ready

    unconfigured = AnimationConnector(root=world.data, asset_repo=world.assets, renderer="", ffmpeg=FFMPEG)
    storyboard = world.storyboard(project.id)
    engine = unconfigured.preflight(world.request(project, storyboard)).get(Capability.ENGINE)
    assert engine.state is CapabilityState.NOT_CONFIGURED and engine.fix == "platform"
    assert "LODY_ANIMATION_RENDERER" in engine.admin and "LODY_" not in engine.message

    ready = world.connector.preflight(world.request(project, storyboard))
    assert ready.ready, [(i.capability, i.message) for i in ready.blocking]
    assert ready.get(Capability.VOICE).state is CapabilityState.DISABLED  # annoncé : vidéo muette, pas caché


def test_preflight_never_assumes_a_free_remotion_license(world, monkeypatch):
    project = world.project("Licence")
    request = world.request(project, world.storyboard(project.id, "lic"))
    monkeypatch.delenv("LODY_REMOTION_EVALUATION")
    engine = world.connector.preflight(request).get(Capability.ENGINE)
    assert engine.state is CapabilityState.UNAVAILABLE and engine.fix == "platform"
    assert "propriétaire" in engine.message and "LODY_REMOTION_LICENSE" in engine.admin
    monkeypatch.setenv("LODY_REMOTION_LICENSE", "company:commande-2026-001")
    engine = world.connector.preflight(request).get(Capability.ENGINE)
    assert engine.state is CapabilityState.READY and "licence entreprise" in engine.message
    monkeypatch.setenv("LODY_REMOTION_LICENSE", "gratuit")  # forme inconnue : jamais interprétée comme accord
    assert world.connector.preflight(request).get(Capability.ENGINE).state is CapabilityState.UNAVAILABLE


def test_preflight_detects_a_modified_asset_file(world):
    project = world.project("Altéré")
    storyboard = world.storyboard(project.id, "alt")
    (world.data / "animation-assets/alt/decor.png").write_bytes(b"modifie")
    visual = world.connector.preflight(world.request(project, storyboard)).get(Capability.VISUAL)
    assert visual.state is CapabilityState.UNAVAILABLE and "modifié" in visual.message


def test_estimate_is_free_and_counts_the_storyboard_scenes(world):
    project = world.project("Estimation")
    request = world.request(project, world.storyboard(project.id))
    units = world.connector.plan_units(request)
    assert units.scenes == (2, 2) and units.images == (0, 0) and units.characters == (0, 0)
    params = world.connector.describe_params(request)
    assert params["frames"] == 2 * FPS and params["estimated_render_seconds"] == 14
    assert "Bonjour" in world.connector.write_script(request)


# -- worker : rendu, traces, repli, reprise, arrêt ----------------------------------------------------------------
@needs_ffmpeg
def test_render_produces_mp4_with_scene_traces_and_subtitles(world):
    project = world.project("Rendu")
    task, directory = _submit(world, project, world.storyboard(project.id))
    assert world.launched == [directory]
    assert world.connector.poll(task).state is RemoteState.QUEUED
    renderer = FakeRenderer()
    state = w.run_job(directory, renderer, ffmpeg=FFMPEG)
    assert state["state"] == "done", state
    manifest = json.loads((directory / "manifest.json").read_text())
    assert [s["status"] for s in manifest["scenes"]] == ["animated", "animated"]
    assert all(abs(s["duration_seconds"] - 1.0) < 0.1 for s in manifest["scenes"])  # durée RÉELLE mesurée
    assert abs(manifest["duration_seconds"] - 2.0) < 0.15 and manifest["sha256"] == w.sha256_file(directory / "out.mp4")
    snapshot = world.connector.poll(task)
    assert snapshot.state is RemoteState.DONE and snapshot.result.video_ref.endswith("/out.mp4")
    assert world.connector.resolve_asset(task, snapshot.result.video_ref).is_file()
    srt = world.connector.read_subtitles(task)
    assert "Bonjour" in srt and "00:00:01,083" in srt  # repère de la 2e scène décalé de 24 images


@needs_ffmpeg
def test_hybrid_replaces_a_failed_scene_with_its_fallback_and_says_so(world):
    project = world.project("Hybride", modes.HYBRID)
    task, directory = _submit(world, project, world.storyboard(project.id))
    state = w.run_job(directory, FakeRenderer(fail={2}), ffmpeg=FFMPEG)
    assert state["state"] == "done"
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["fallback_scenes"] == [2] and manifest["scenes"][1]["fallback_source"] == "décor du catalogue"
    snapshot = world.connector.poll(task)
    assert snapshot.state is RemoteState.DONE and any("Scène 2" in warning for warning in snapshot.warnings)


@needs_ffmpeg
def test_strict_animation_mode_fails_on_a_failed_scene(world):
    project = world.project("Strict")
    task, directory = _submit(world, project, world.storyboard(project.id))
    assert w.run_job(directory, FakeRenderer(fail={1}), ffmpeg=FFMPEG)["state"] == "failed"
    snapshot = world.connector.poll(task)
    assert snapshot.state is RemoteState.FAILED and "accueil" in snapshot.error.message
    assert not (directory / "out.mp4").exists()


@needs_ffmpeg
def test_stop_then_resume_never_renders_a_finished_scene_twice(world):
    project = world.project("Reprise")
    _, directory = _submit(world, project, world.storyboard(project.id))
    stop = {"flag": False}
    first = FakeRenderer(on_render=lambda index: stop.update(flag=True))
    state = w.run_job(directory, first, ffmpeg=FFMPEG, stop_requested=lambda: stop["flag"])
    assert state["state"] == "interrupted" and first.rendered == [1]
    second = FakeRenderer()
    assert w.run_job(directory, second, ffmpeg=FFMPEG)["state"] == "done"
    assert second.rendered == [2]  # la scène 1 (empreinte vérifiée) vient du cache


def test_cancel_flag_stops_the_job(world):
    project = world.project("Arrêt")
    task, directory = _submit(world, project, world.storyboard(project.id))
    assert world.connector.cancel(task)
    renderer = FakeRenderer()
    assert w.run_job(directory, renderer, ffmpeg=FFMPEG or "ffmpeg")["state"] == "cancelled"
    assert renderer.rendered == [] and world.connector.poll(task).state is RemoteState.FAILED


def test_only_one_render_at_a_time(world):
    project = world.project("File")
    _, directory = _submit(world, project, world.storyboard(project.id))
    calls = {"n": 0}

    def stop_after_waiting():
        calls["n"] += 1
        return calls["n"] > 2

    renderer = FakeRenderer()
    with w.file_lock(w.jobs_root(world.data) / ".render.lock", blocking=True):  # un autre rendu occupe le créneau
        state = w.run_job(directory, renderer, ffmpeg=FFMPEG or "ffmpeg", stop_requested=stop_after_waiting)
    assert renderer.rendered == [] and state["state"] == "interrupted"


def test_jobs_are_isolated_per_project(world):
    first, second = world.project("Projet A"), world.project("Projet B")
    task_a, dir_a = _submit(world, first, world.storyboard(first.id, "a"), key="prd_aaaaaaaaaaaa-run")
    task_b, dir_b = _submit(world, second, world.storyboard(second.id, "b"), key="prd_bbbbbbbbbbbb-run")
    assert dir_a.parent.name == first.id and dir_b.parent.name == second.id
    job_a = json.loads((dir_a / "job.json").read_text())
    job_b = json.loads((dir_b / "job.json").read_text())
    assert job_a["assets"] and not set(job_a["assets"]) & set(job_b["assets"])
    (dir_b / "out.mp4").write_bytes(b"video b")
    foreign = f"animation/{second.id}/{task_b.task_id}/out.mp4"
    with pytest.raises(ValueError):  # une tâche ne résout jamais le fichier d'un autre projet
        world.connector.resolve_asset(task_a, foreign)
    assert world.connector.resolve_asset(task_b, foreign).is_file()
    # Un storyboard ne peut pas viser les assets d'un autre projet.
    stolen = world.request(first, world.storyboard(second.id, "c"))
    assert world.connector.preflight(stolen).get(Capability.VISUAL).state is CapabilityState.UNAVAILABLE


def test_submit_is_idempotent(world):
    project = world.project("Idempotence")
    storyboard = world.storyboard(project.id)
    task, _ = _submit(world, project, storyboard)
    again, _ = _submit(world, project, storyboard)
    assert task == again and len(world.launched) == 1


def test_a_vanished_worker_is_relaunched_then_abandoned(world):
    project = world.project("Relance")
    task, directory = _submit(world, project, world.storyboard(project.id))
    for attempt in range(1, 4):
        state = w.read_json(directory / "state.json")
        state.update(state="running", heartbeat_at=time.time() - 3600)
        w.write_json(directory / "state.json", state)
        assert world.connector.poll(task).state is not RemoteState.FAILED
        assert len(world.launched) == 1 + attempt
    state = w.read_json(directory / "state.json")
    state.update(heartbeat_at=time.time() - 3600)
    w.write_json(directory / "state.json", state)
    snapshot = world.connector.poll(task)
    assert snapshot.state is RemoteState.FAILED and "3 fois" in snapshot.error.message


def test_scene_engine_never_receives_application_settings(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_VALUE)
    monkeypatch.setenv("LODY_CONFIG_PATH", "/tmp/config.toml")
    env = w.minimal_env()
    assert "OPENAI_API_KEY" not in env and not any(key.startswith("LODY_") for key in env)
    assert FAKE_VALUE not in env.values()


@needs_ffmpeg
def test_detached_worker_process_renders_with_a_command_engine(world, tmp_path, monkeypatch):
    """Bout en bout : vrai processus worker détaché, vrai moteur de scène en ligne de commande."""
    engine = tmp_path / "engine.py"
    engine.write_text(
        "import json, os, subprocess, sys\n"
        "args = dict(zip(sys.argv[1::2], sys.argv[2::2]))\n"
        f"assert {FAKE_VALUE!r} not in os.environ.values()\n"
        "data = json.load(open(args['--input'], encoding='utf-8'))\n"
        "c = data['canvas']; s = data['scene']['duration_frames'] / c['fps']\n"
        f"subprocess.run([{FFMPEG!r}, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',\n"
        "  f\"color=c=blue:s={c['width']}x{c['height']}:r={c['fps']}\", '-t', str(s), '-pix_fmt', 'yuv420p',\n"
        "  args['--output']], check=True)\n", encoding="utf-8")
    monkeypatch.setenv(w.RENDERER_ENV, f"{sys.executable} {engine}")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_VALUE)
    connector = AnimationConnector(root=world.data, asset_repo=world.assets, launcher=spawn_worker, ffmpeg=FFMPEG)
    project = world.project("Processus")
    task = connector.submit(world.request(project, world.storyboard(project.id)), "prd_cccccccccccc-run")
    deadline = time.time() + 120
    snapshot = connector.poll(task)
    while snapshot.state in (RemoteState.QUEUED, RemoteState.RUNNING) and time.time() < deadline:
        time.sleep(0.5)
        snapshot = connector.poll(task)
    directory = w.find_job_dir(world.data, task.task_id)
    log = (directory / "worker.log").read_text() if (directory / "worker.log").exists() else ""
    assert snapshot.state is RemoteState.DONE, log
    assert "rendu terminé" in log and FAKE_VALUE not in log


# -- service : préparation, gel, lancement, suivi ---------------------------------------------------------------------
@needs_ffmpeg
def test_service_runs_an_animated_production_end_to_end(world):
    project = world.project("Service animé")
    renderer = FakeRenderer()
    connector = AnimationConnector(root=world.data, asset_repo=world.assets, renderer=f"{sys.executable} -c pass",
                                   ffmpeg=FFMPEG, launcher=lambda d: w.run_job(d, renderer, ffmpeg=FFMPEG))
    service = ProductionService(ProductionRepository(world.db), {"animation_2d": connector}, SyncExecutor(),
                                price_book=lambda: FULL_BOOK, animation_asset_repo=world.assets)
    raw = world.storyboard(project.id, "x")
    raw.pop("frozen")
    draft = service.prepare(project, SUBJECT, provider_id="animation_2d", animation=raw)
    assert draft.animation["frozen"]["sha256"] and "animation" not in draft.params["request"]
    assert draft.cost_high == 0 and not draft.cost_partial  # aucun fournisseur payant
    assert service.preflight(draft).ready
    reused = service.prepare(project, SUBJECT + " bis", provider_id="animation_2d", draft_id=draft.id)
    assert reused.id == draft.id and reused.animation == draft.animation
    launched = service.confirm(draft.id)
    assert launched.status is S.EN_FILE and launched.external_task_id.startswith("anim-")
    assert "animation" not in launched.params["request"] and launched.params["request"]["render_mode"] == modes.ANIMATION
    finished = service.refresh(launched.id)
    assert finished.status is S.TERMINEE and finished.video_duration and service.resolve_video(finished).is_file()
    assert request_of(finished).animation["storyboard"] == finished.animation


def test_service_refuses_an_invalid_storyboard_with_a_readable_message(world):
    from lody.generation.service import InvalidInput

    project = world.project("Refus")
    service = ProductionService(ProductionRepository(world.db), {"animation_2d": world.connector}, SyncExecutor(),
                                price_book=lambda: FULL_BOOK, animation_asset_repo=world.assets)
    with pytest.raises(InvalidInput, match="Storyboard animé refusé"):
        service.prepare(project, SUBJECT, provider_id="animation_2d", animation={"schema": "autre"})
    draft = service.prepare(project, SUBJECT, provider_id="animation_2d")  # sans storyboard : préflight bloquant
    assert draft.animation == {} and not service.preflight(draft).ready


def test_classic_tasks_are_not_seen_by_the_animation_provider(world):
    assert world.connector.poll(ExternalTask("animation_2d", "task-0001")).state is RemoteState.UNKNOWN
    assert world.connector.recover(ExternalTask("animation_2d", "../etc")) is None
