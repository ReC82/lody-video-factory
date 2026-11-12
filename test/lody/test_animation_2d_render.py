"""Intégration (#154) : rendu 2D COMPLET par la vraie chaîne, avec le vrai moteur Remotion.

projet en mode Animation 2D → démo Voltix #150 installée par l'atelier (catalogue + fichiers) → service de
production (gel du storyboard) → préflight du fournisseur → worker → moteur ``renderers/remotion`` → out.mp4.

Lancé seulement si le moteur est installé (``LODY_ANIMATION_RENDERER`` et ses modules) : dans l'image Docker
du worker 2D (workflow « Worker 2D ») ou sur un poste de DEV. Ignoré dans la CI Python, qui n'a ni Node ni
Chrome. Une seule scène de la démo (≈ 5,6 s, 167 images 1080×1920) pour rester court.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from lody.animation import modes, pilot
from lody.animation import preflight as pf
from lody.animation import worker as w
from lody.animation.assets import AnimationAssetRepository
from lody.generation.animation_connector import AnimationConnector
from lody.animation.freeze import freeze_document
from lody.characters import CharacterRepository
from lody.generation.models import Capability, CapabilityState
from lody.generation.service import build_request
from lody.projects import ProjectRepository


def _renderer_ready() -> bool:
    command = w.renderer_command()
    if not command or not shutil.which(command[0]):
        return False
    return not any(check.state == pf.ERROR for check in pf.engine_checks(command, {}))


# LODY_REQUIRE_2D_RENDER=1 (workflow « Worker 2D ») : jamais ignoré, un moteur absent fait échouer le test.
pytestmark = pytest.mark.skipif(os.environ.get("LODY_REQUIRE_2D_RENDER") != "1"
                                and (not _renderer_ready() or not w.ffmpeg_exe()),
                                reason="moteur 2D Remotion non installé (LODY_ANIMATION_RENDERER)")


def test_full_2d_render_from_the_dev_pilot(tmp_path, monkeypatch):
    monkeypatch.setenv(pf.EVALUATION_ENV, "1")
    monkeypatch.delenv(pf.LICENSE_ENV, raising=False)
    db, data = tmp_path / "lody.sqlite3", tmp_path / "data"
    projects, assets = ProjectRepository(db), AnimationAssetRepository(db)
    project = projects.create(name="Pilote 2D — intégration", settings={modes.SETTINGS_KEY: modes.ANIMATION})
    storyboard, _ = pilot.install_voltix_demo(assets, project.id, data)
    storyboard = {**storyboard, "scenes": storyboard["scenes"][:1]}
    frozen = freeze_document(assets, project.id, storyboard)
    # Base de test pour les fiches personnages aussi (#164) : jamais settings.db_path(), illisible dans l'image.
    connector = AnimationConnector(root=data, asset_repo=assets, character_repo=CharacterRepository(db),
                                   launcher=lambda directory: w.run_job(directory, w.CommandRenderer(w.renderer_command())))
    request = build_request(project, "Voltix dit bonjour").with_updates(
        render_mode=modes.ANIMATION, animation={"project_id": project.id, "storyboard": frozen})
    report = connector.preflight(request)
    assert report.ready, [(item.capability, item.message) for item in report.blocking]
    assert report.get(Capability.ENGINE).state is CapabilityState.READY

    task = connector.submit(request, "integration-154")
    directory = w.find_job_dir(data, task.task_id)
    state = w.read_json(directory / "state.json") or {}
    log = (directory / "worker.log").read_text(encoding="utf-8") if (directory / "worker.log").is_file() else ""
    assert state.get("state") == "done", f"{state.get('error')}\n{log}"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scenes"][0]["status"] == "animated"
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        probe = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=width,height", "-show_entries", "format=duration", "-of", "json",
                                str(directory / "out.mp4")], capture_output=True, text=True, check=True)
        info = json.loads(probe.stdout)
        assert (info["streams"][0]["width"], info["streams"][0]["height"]) == (1080, 1920)
        assert abs(float(info["format"]["duration"]) - frozen["scenes"][0]["duration_frames"] / 30) < 0.5
