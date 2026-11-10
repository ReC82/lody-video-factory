"""#127 : la CI ne relance pas la matrice complète pour une PR purement documentaire, sans jamais
sauter les tests pour du code, des tests, le déploiement ou les workflows."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def classify_script():
    (step,) = [s for s in CI["jobs"]["changes"]["steps"] if s.get("id") == "diff"]
    return step["run"]


def classify(tmp_path, files, event="pull_request", diff_ok=True):
    fakebin = tmp_path / "bin"
    fakebin.mkdir(exist_ok=True)
    listing = tmp_path / "files.txt"
    listing.write_text("".join(f"{f}\n" for f in files), encoding="utf-8")
    (fakebin / "git").write_text(f'#!/bin/sh\n{"cat " + str(listing) if diff_ok else "exit 128"}\n', encoding="utf-8")
    (fakebin / "git").chmod(0o755)
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    env = dict(os.environ, PATH=f"{fakebin}:{os.environ['PATH']}", GITHUB_OUTPUT=str(out), EVENT=event,
               BASE_SHA="a", HEAD_SHA="b")
    subprocess.run(["bash", "-e", "-c", classify_script()], env=env, check=True, capture_output=True, timeout=30)
    return dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").split())["code"]


@pytest.mark.parametrize("files", [["docs/lodyorch.md"], ["README.md", "docs/animation/notes.md"]])
def test_documentation_only_pr_skips_the_full_matrix(tmp_path, files):
    assert classify(tmp_path, files) == "false"


@pytest.mark.parametrize("files", [
    ["docs/lodyorch.md", "deploy/lodyorch/lodyorch.py"],
    ["deploy/lodyorch/session_prompt.md"],  # lu par l'orchestrateur et ses tests
    ["docs/skill/mpt_agent.py"], ["docs/animation/catalog.json"], ["test/README.md"],
    [".github/workflows/ci.yml"], ["app/services/voice.py"], [],
])
def test_any_other_change_runs_the_full_pipeline(tmp_path, files):
    assert classify(tmp_path, files) == "true"


def test_push_and_unreadable_diff_always_run_the_full_pipeline(tmp_path):
    assert classify(tmp_path, ["docs/x.md"], event="push") == "true"
    assert classify(tmp_path, ["docs/x.md"], diff_ok=False) == "true"


def test_full_jobs_depend_on_the_classification_and_docs_checks_cover_the_rest():
    jobs = CI["jobs"]
    for name in ("tests", "windows-smoke"):
        assert jobs[name]["needs"] == "changes"
        assert jobs[name]["if"] == "needs.changes.outputs.code == 'true'"
    assert jobs["docs-checks"]["if"] == "needs.changes.outputs.code == 'false'"
    assert "pytest" in jobs["docs-checks"]["steps"][-1]["run"]
    assert CI["permissions"] == {"contents": "read"}
