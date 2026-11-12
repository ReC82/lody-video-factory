"""Pilote DEV du worker 2D Remotion (#154) : préflight, licence, import de catalogue, démo #150, fichiers Docker.

Aucun rendu réel ici (voir ``test_animation_2d_render.py``), aucun réseau, aucun fournisseur payant."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from lody.animation import pilot
from lody.animation import preflight as pf
from lody.animation.assets import AnimationAssetRepository
from lody.animation.contract import AnimationValidationError
from lody.animation.freeze import freeze_document
from lody.projects import ProjectRepository

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repos(tmp_path):
    db = tmp_path / "lody.sqlite3"
    return ProjectRepository(db), AnimationAssetRepository(db), tmp_path / "data"


# -- licence Remotion ---------------------------------------------------------------------------------------------
def test_license_is_never_assumed_free():
    pending = pf.license_check({})
    assert pending.state == pf.DECISION and pending.blocking
    assert "propriétaire" in pending.message and pf.LICENSE_URL in pending.detail and "Usage prévu" in pending.detail
    evaluation = pf.license_check({pf.EVALUATION_ENV: "1"})
    assert evaluation.state == pf.WARNING and not evaluation.blocking and "évaluation DEV" in evaluation.message
    for raw in ("oui", "free", "free:", "gratuit:perso", "company: "):
        assert pf.license_check({pf.LICENSE_ENV: raw, pf.EVALUATION_ENV: "1"}).state == pf.ERROR, raw
    assert pf.license_check({pf.LICENSE_ENV: "free:particulier, vérifié le 2026-11-11"}).state == pf.OK
    assert pf.license_allows_render({pf.LICENSE_ENV: "company:PO-42"}) == (True, "Licence Remotion : licence entreprise (PO-42).")


# -- environnement ------------------------------------------------------------------------------------------------
def _fake_renderer(root: Path, *, browser: bool = True, bundle: bool = True) -> list[str]:
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "lody-remotion.mjs").write_text("// faux moteur\n")
    (root / "package.json").write_text("{}")
    module = root / "node_modules" / "@remotion" / "renderer"
    module.mkdir(parents=True)
    (module / "package.json").write_text(json.dumps({"version": "4.0.542"}))
    if browser:
        shell = root / "node_modules" / ".remotion" / "chrome-headless-shell" / "linux64" / "chrome-headless-shell-linux64"
        shell.mkdir(parents=True)
        (shell / "chrome-headless-shell").write_text("")
    if bundle:
        (root / ".bundle" / "abc").mkdir(parents=True)
        (root / ".bundle" / "abc" / "index.html").write_text("")
    return [sys.executable, str(root / "bin" / "lody-remotion.mjs")]


def test_engine_checks_report_each_missing_piece(tmp_path):
    assert pf.engine_checks([], {})[0].state == pf.ERROR
    assert pf.engine_checks(["/nulle/part/node"], {})[0].state == pf.ERROR
    ready = pf.engine_checks(_fake_renderer(tmp_path / "ok"), {})
    assert [c.state for c in ready] == [pf.OK] * 4 and "4.0.542" in ready[1].message
    command = _fake_renderer(tmp_path / "nu", browser=False, bundle=False)
    dev = {c.key: c.state for c in pf.engine_checks(command, {})}
    assert dev["browser"] == pf.WARNING and dev["bundle"] == pf.WARNING  # poste de DEV : téléchargé au besoin
    image = {c.key: c.state for c in pf.engine_checks(command, {pf.IMAGE_ENV: "1"})}
    assert image["browser"] == pf.ERROR and image["bundle"] == pf.ERROR  # image : jamais de téléchargement


def test_resource_checks_read_container_limits(tmp_path):
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "cpu.max").write_text("150000 100000\n")
    (cgroup / "memory.max").write_text(str(1800 * 1024 ** 2))
    resources, disk = pf.resource_checks(tmp_path, cgroup)
    assert resources.state == pf.OK and "1.5 CPU (plafond)" in resources.message and "(plafond)" in resources.message
    assert disk.key == "disk"
    (cgroup / "memory.max").write_text(str(512 * 1024 ** 2))
    assert pf.resource_checks(tmp_path, cgroup)[0].state == pf.WARNING


def test_isolation_names_visible_secrets_without_their_values(tmp_path):
    clean = pf.isolation_checks({"PATH": "/bin", pf.LICENSE_ENV: "company:x"}, tmp_path / "absent.toml", tmp_path / "s")
    assert clean[0].state == pf.OK
    leaked = pf.isolation_checks({"OPENAI_API_KEY": "sk-NE-PAS-AFFICHER"}, tmp_path / "absent.toml", tmp_path / "s")[0]
    assert leaked.state == pf.WARNING and "OPENAI_API_KEY" in leaked.message and "sk-" not in leaked.message
    config = tmp_path / "config.toml"
    config.write_text("[app]\n")
    assert "config.toml" in pf.isolation_checks({}, config, tmp_path / "s")[0].message


def test_preflight_cli_exit_code_follows_blocking_points(monkeypatch, capsys):
    monkeypatch.setattr(pf, "run_checks", lambda env=None: [pf.Check("x", "X", pf.OK, "bien")])
    assert pf.main(["--json"]) == 0 and json.loads(capsys.readouterr().out)["ready"] is True
    monkeypatch.setattr(pf, "run_checks", lambda env=None: [pf.license_check({})])
    assert pf.main([]) == 1 and "DÉCISION" in capsys.readouterr().out


# -- catalogue et démo ---------------------------------------------------------------------------------------------
def test_seed_files_copies_only_files_with_the_declared_digest(tmp_path):
    bundled = tmp_path / "resource"
    (bundled / "animation-assets" / "a").mkdir(parents=True)
    (bundled / "animation-assets" / "a" / "x.svg").write_text("<svg/>")
    good = pilot.animation_worker.sha256_file(bundled / "animation-assets" / "a" / "x.svg")
    catalog = {"assets": [{"versions": [{"manifest": {"files": [
        {"path": "animation-assets/a/x.svg", "sha256": good},
        {"path": "animation-assets/a/x.svg", "sha256": "0" * 64},
        {"path": "../../etc/passwd", "sha256": good},
        {"path": "animation-assets/a/absent.svg", "sha256": good}]}}]}]}
    result = pilot.seed_files(catalog, tmp_path / "data", bundled)
    assert result.copied == 1 and (tmp_path / "data" / "animation-assets" / "a" / "x.svg").read_text() == "<svg/>"
    assert result.missing == ["animation-assets/a/x.svg", "../../etc/passwd", "animation-assets/a/absent.svg"]
    assert not (tmp_path / "etc").exists()
    assert pilot.seed_files(catalog, tmp_path / "data", bundled).present == 1  # idempotent


def test_voltix_demo_installs_once_and_freezes_with_the_reference_assets(repos):
    projects, assets, data = repos
    project = projects.create(name="Pilote 2D")
    storyboard, notes = pilot.install_voltix_demo(assets, project.id, data)
    assert any("Catalogue Voltix importé" in note for note in notes) and any("muette" in note for note in notes)
    frozen = freeze_document(assets, project.id, storyboard)
    reference = json.loads(pilot.DEMO_STORYBOARD.read_text(encoding="utf-8"))
    # Mêmes contenus (empreintes de version) que la vidéo de référence #150, avec les identifiants du projet.
    assert sorted(a["sha256"] for a in frozen["frozen"]["assets"]) == sorted(a["sha256"] for a in reference["frozen"]["assets"])
    assert {a["id"] for a in frozen["frozen"]["assets"]} <= {a.id for a in assets.list_for_project(project.id)}
    count = len(assets.list_for_project(project.id))
    _, again = pilot.install_voltix_demo(assets, project.id, data)
    assert len(assets.list_for_project(project.id)) == count and "déjà présents" in again[0]


def test_adopt_storyboard_refuses_assets_missing_from_the_project(repos):
    projects, assets, _ = repos
    project = projects.create(name="Vide")
    reference = json.loads(pilot.DEMO_STORYBOARD.read_text(encoding="utf-8"))
    with pytest.raises(AnimationValidationError) as error:
        pilot.adopt_storyboard(assets, project.id, reference)
    assert "importe d'abord son catalogue" in next(iter(error.value.errors.values()))
    unfrozen = {key: value for key, value in reference.items() if key != "frozen"}
    assert pilot.adopt_storyboard(assets, project.id, unfrozen) is unfrozen  # rien à adapter


# -- fichiers Docker du pilote --------------------------------------------------------------------------------------
def test_dev_compose_is_isolated_from_production():
    compose = (ROOT / "docker-compose.lody-dev-2d.yml").read_text(encoding="utf-8")
    production = (ROOT / "docker-compose.lody.yml").read_text(encoding="utf-8")
    body = "\n".join(line.split("#")[0] for line in compose.splitlines())
    for forbidden in ("config.toml", "/secrets", "./secrets", "./storage", "moneyprinterturbo_default",
                      "lody-video-factory-data", "LODY_SECRETS", "8601:8601"):
        assert forbidden not in body, forbidden
    assert "name: lody-dev-2d" in body and "name: lody-dev-2d-data" in body and "lody-dev-2d-data" not in production
    assert '"127.0.0.1:8611:8601"' in body and "read_only: true" in body and "cap_drop" in body
    assert "cpus: 1.5" in body and "mem_limit: 1800m" in body and "pids_limit" in body and "max-size" in body
    assert 'LODY_REMOTION_EVALUATION: "1"' in body


def test_worker_image_is_pinned_and_never_ships_secrets():
    dockerfile = (ROOT / "Dockerfile.lody-2d").read_text(encoding="utf-8")
    assert re.search(r"NODE_IMAGE=node:\d+\.\d+\.\d+-trixie-slim@sha256:[0-9a-f]{64}", dockerfile)
    assert "npm ci" in dockerfile and "--prepare" in dockerfile and "USER lody" in dockerfile
    assert "npm install" not in dockerfile
    ignore = (ROOT / "Dockerfile.lody-2d.dockerignore").read_text(encoding="utf-8").splitlines()
    for entry in ("config.toml", "secrets", ".env", ".env.*", "storage", "data", "renderers/remotion/node_modules"):
        assert entry in ignore, entry
    assert (ROOT / "Dockerfile.lody").read_text(encoding="utf-8").count("node") == 0  # image de production inchangée
