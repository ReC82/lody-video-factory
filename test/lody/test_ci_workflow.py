"""#127 puis #198 : la CI choisit ses tests selon les fichiers de la PR, sans jamais sauter la suite complète
pour du code partagé, des dépendances, le déploiement ou les workflows, et expose un check agrégateur
unique qui ne peut pas être vert si un job nécessaire a échoué, été annulé ou n'a pas tourné."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
SCRIPT = ROOT / "scripts" / "ci" / "select_tests.py"

sys.path.insert(0, str(SCRIPT.parent))
import select_tests  # noqa: E402


def select(files, event="pull_request"):
    return select_tests.select(files, event)


# --- Classification ------------------------------------------------------------------------------------

@pytest.mark.parametrize("files", [["docs/lodyorch.md"], ["README.md", "docs/animation/notes.md"]])
def test_documentation_only_pr_runs_only_the_tests_reading_the_documentation(files):
    result = select(files)
    assert result["mode"] == "docs"
    assert "test/lody/test_docs_index.py" in result["tests"]
    assert "test" not in result["tests"] and "test/services" not in result["tests"]
    assert result["pythons"] == ["3.12"]


@pytest.mark.parametrize("files", [
    ["webui/lody/ui.py"], ["webui/lody/styles.css"], ["webui/Lody.py"], ["test/lody/test_projects.py"],
    ["test/lody/fakes.py"], ["lody_auth/server.py"], ["renderers/remotion/src/index.ts"],
    ["scripts/animation/reedit.py"], ["resource/animation-assets/catalog.json"],
])
def test_interface_only_pr_runs_only_the_lody_tests(files):
    result = select(files)
    assert result == {"mode": "targeted", "tests": ["test/lody"], "pythons": ["3.12"], "reasons": result["reasons"]}


@pytest.mark.parametrize("files", [["deploy/lodyorch/lodyorch.py"], ["deploy/lodyorch/session_prompt.md"],
                                   ["test/lodyorch/test_console.py"]])
def test_orchestrator_only_pr_runs_only_the_orchestrator_tests(files):
    result = select(files)
    assert (result["mode"], result["tests"]) == ("targeted", ["test/lodyorch"])


def test_mixed_isolated_areas_and_docs_union_their_suites_without_duplicates():
    result = select(["webui/lody/ui.py", "deploy/lodyorch/lodyorch.py", "docs/lody-ui.md", "test/lody/x.py"])
    assert result["mode"] == "targeted"
    assert result["tests"][:2] == ["test/lody", "test/lodyorch"]
    extra = result["tests"][2:]
    assert extra and all(t.startswith("test/services/") or t == "test/test_main.py" for t in extra), extra
    assert len(set(result["tests"])) == len(result["tests"])


@pytest.mark.parametrize("files", [
    # Moteur, ancienne WebUI et code partagé.
    ["app/services/voice.py"], ["webui/Main.py"], ["webui/i18n/fr.json"], ["webui/styles.css"],
    ["webui/static/logo.png"], ["cli.py"], ["main.py"], ["test/services/test_task.py"], ["test/test_main.py"],
    # Dépendances, CI, Docker, déploiement hors orchestrateur, scripts d'exploitation.
    ["pyproject.toml"], ["uv.lock"], ["requirements.lody.txt"], ["requirements.txt"],
    [".github/workflows/ci.yml"], [".github/workflows/worker-2d.yml"], ["scripts/ci/select_tests.py"],
    ["Dockerfile.lody"], ["Dockerfile.lody-2d"], ["docker-compose.lody.yml"], ["deploy/nginx/lody-auth.conf"],
    ["deploy/systemd/lody-secrets-reload.service"], ["scripts/lody-apply-secrets.sh"], ["config.example.toml"],
    # Configuration de test partagée, ressources du moteur, fichiers inconnus, documentation non Markdown.
    ["test/__init__.py"], ["test/conftest.py"], ["test/README.md"], ["resource/fonts/x.ttf"], ["prototypes/a.js"],
    ["docs/skill/mpt_agent.py"], ["docs/animation/catalog.json"], ["nouveau_dossier/fichier.py"],
    # Un seul fichier partagé suffit à imposer la suite complète.
    ["webui/lody/ui.py", "app/models/schema.py"], ["docs/x.md", "uv.lock"],
])
def test_shared_critical_or_unknown_files_run_the_full_suite(files):
    result = select(files)
    assert (result["mode"], result["tests"]) == ("full", ["test"])


def test_ambiguous_inputs_fall_back_to_the_full_suite():
    assert select(None)["mode"] == "full"  # diff illisible
    assert select([])["mode"] == "full"  # diff vide
    assert select(["  ", ""])["mode"] == "full"


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
def test_main_schedule_and_manual_runs_are_full_with_the_compatibility_matrix(event):
    result = select(["docs/x.md"], event=event)
    assert result["mode"] == "full" and result["tests"] == ["test"]
    assert result["pythons"] == ["3.11", "3.12", "3.13"]


def test_full_suite_on_pull_request_uses_production_python_only():
    assert select(["app/x.py"])["pythons"] == ["3.12"]
    dockerfile = (ROOT / "Dockerfile.lody").read_text(encoding="utf-8")
    assert "FROM python:3.12-" in dockerfile, "PR_PYTHONS doit suivre la version Python de production"


def test_isolated_areas_are_not_imported_by_the_engine_or_the_legacy_webui():
    """Garde-fou de la classification : si app/, l'ancienne WebUI ou les tests du moteur se mettaient à
    importer l'interface Lody, une PR « interface » ne lancerait plus les tests concernés."""
    needles = ("from lody", "import lody", "webui.lody", "lody_auth", "renderers/remotion")
    sources = [*(ROOT / "app").rglob("*.py"), *(ROOT / "test" / "services").rglob("*.py"),
               ROOT / "webui" / "Main.py", ROOT / "cli.py", ROOT / "main.py"]
    offenders = [p.relative_to(ROOT).as_posix() for p in sources
                 if any(n in p.read_text(encoding="utf-8", errors="replace") for n in needles)]
    assert offenders == []


# --- Script en ligne de commande (tel que lancé par la CI) -------------------------------------------

def run_script(tmp_path, *args):
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), *args, "--github-output", str(out)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60, check=True)
    values = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    return values, proc.stdout


def test_cli_writes_the_github_outputs_and_explains_each_file(tmp_path):
    values, stdout = run_script(tmp_path, "--files", "webui/lody/ui.py")
    assert values == {"mode": "targeted", "tests": "test/lody", "pythons": '["3.12"]'}
    assert "webui/lody/ui.py → targeted (interface Lody)" in stdout


def test_cli_with_an_unreadable_diff_or_a_push_selects_the_full_suite(tmp_path):
    values, _ = run_script(tmp_path, "--base", "révision-inexistante", "--head", "HEAD")
    assert values["mode"] == "full"
    values, _ = run_script(tmp_path, "--event", "push", "--base", "", "--head", "")
    assert (values["mode"], json.loads(values["pythons"])) == ("full", ["3.11", "3.12", "3.13"])


# --- Workflow --------------------------------------------------------------------------------------------

def test_workflow_uses_the_selection_script_for_every_event():
    assert set(CI[True]) == {"pull_request", "push", "schedule", "workflow_dispatch"}  # `on` lu comme True
    (step,) = [s for s in CI["jobs"]["changes"]["steps"] if s.get("id") == "select"]
    assert "scripts/ci/select_tests.py" in step["run"] and '--event "$EVENT"' in step["run"]
    assert CI["jobs"]["changes"]["steps"][0]["with"]["fetch-depth"] == 0
    assert CI["permissions"] == {"contents": "read"}


def test_tests_job_runs_selected_tests_or_full_suite_with_coverage():
    job = CI["jobs"]["tests"]
    assert job["needs"] == "changes" and "if" not in job  # toujours lancé, quel que soit le mode
    assert job["strategy"]["matrix"]["python-version"] == "${{ fromJSON(needs.changes.outputs.pythons) }}"
    steps = {s["name"]: s for s in job["steps"]}
    targeted = next(s for n, s in steps.items() if n.startswith("Run selected tests"))
    assert targeted["if"] == "needs.changes.outputs.mode != 'full'" and "$TESTS" in targeted["run"]
    assert "-n auto --maxprocesses 4" in targeted["run"]  # parallélisme borné
    full = steps["Run full test suite with coverage"]
    assert full["if"] == "needs.changes.outputs.mode == 'full'"
    assert "coverage run -m pytest" in full["run"] and full["run"].rstrip().endswith(" test")
    assert " -n " not in full["run"]
    assert "ruff check" in steps["Lint Python sources"]["run"]
    assert "compileall" in steps["Compile Python sources"]["run"]
    assert CI["jobs"]["windows-smoke"]["if"] == "needs.changes.outputs.mode == 'full'"


def aggregate(tmp_path, mode, changes="success", tests="success", windows="skipped"):
    script = CI["jobs"]["ci-result"]["steps"][0]["run"]
    env = dict(os.environ, MODE=mode, CHANGES=changes, TESTS=tests, WINDOWS=windows)
    return subprocess.run(["bash", "-e", "-c", script], env=env, capture_output=True, timeout=30).returncode


def test_aggregator_is_always_run_and_depends_on_every_job():
    job = CI["jobs"]["ci-result"]
    assert job["name"] == "CI result" and job["if"] == "always()"
    assert set(job["needs"]) == set(CI["jobs"]) - {"ci-result"}


@pytest.mark.parametrize("mode,changes,tests,windows", [
    ("targeted", "success", "success", "skipped"),
    ("docs", "success", "success", "skipped"),
    ("full", "success", "success", "success"),
])
def test_aggregator_is_green_only_when_every_needed_job_succeeded(tmp_path, mode, changes, tests, windows):
    assert aggregate(tmp_path, mode, changes, tests, windows) == 0


@pytest.mark.parametrize("mode,changes,tests,windows", [
    ("", "failure", "skipped", "skipped"),  # classification en échec : rien n'a tourné
    ("", "success", "success", "skipped"),  # sortie absente
    ("full", "success", "failure", "success"),
    ("full", "success", "success", "failure"),
    ("full", "success", "success", "skipped"),  # suite complète sans smoke Windows
    ("full", "success", "cancelled", "success"),
    ("targeted", "success", "skipped", "skipped"),  # tests sautés = pas de vert artificiel
    ("targeted", "success", "failure", "skipped"),
])
def test_aggregator_fails_on_any_failed_cancelled_or_skipped_requirement(tmp_path, mode, changes, tests, windows):
    assert aggregate(tmp_path, mode, changes, tests, windows) != 0


def test_xdist_is_a_pinned_dev_dependency():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"pytest-xdist==' in pyproject
    assert 'name = "pytest-xdist"' in (ROOT / "uv.lock").read_text(encoding="utf-8")


# --- Benchmark reproductible (scripts/ci/ci_durations.py) ------------------------------------------------

import ci_durations  # noqa: E402


def _job(name, start, end, steps=()):
    return {"name": name, "started_at": f"2026-11-12T10:{start}Z", "completed_at": f"2026-11-12T10:{end}Z",
            "steps": [{"name": s, "conclusion": "success"} for s in steps]}


def test_benchmark_classifies_runs_by_the_step_that_actually_ran():
    before = [_job("Python 3.11 tests", "00:10", "14:00")]
    targeted = [_job("Detect code changes", "00:05", "00:20"),
                _job("Python 3.12 tests", "00:30", "03:30", ["Run selected tests (targeted)"]),
                _job("CI result", "03:35", "03:40")]
    full = [_job("Python 3.12 tests", "00:30", "12:00", ["Run full test suite with coverage"]),
            _job("CI result", "12:05", "12:10")]
    assert ci_durations.run_class(before) == "avant-198"
    assert ci_durations.run_class(targeted) == "targeted"
    assert ci_durations.run_class(full) == "full"
    run = {"event": "pull_request", "created_at": "2026-11-12T10:00:00Z"}
    stats = ci_durations.summarize([(run, before), (run, targeted), (run, full)])
    assert stats[("pull_request", "targeted")]["total"] == (1, 220.0, 220.0, 220.0)
    assert stats[("pull_request", "targeted")]["tests"][1] == 180.0
    assert stats[("pull_request", "avant-198")]["total"][1] == 840.0
