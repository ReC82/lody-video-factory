"""Politique de fusion automatique (#193, phase 1) : évaluation À BLANC sur des PR factices.
Aucun réseau : les faits GitHub sont construits ici ; aucune fusion n'est jamais effectuée."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("merge_policy", ROOT / "deploy" / "lodyorch" / "merge_policy.py")
mp = importlib.util.module_from_spec(spec)
sys.modules.setdefault("merge_policy", mp)  # requis par @dataclass
spec.loader.exec_module(mp)

SHA = "a" * 40
OWNER = mp.TRUSTED_ACTORS[0]
CODE_RUNS = [mp.CHECK_DETECT, *mp.CHECKS_CODE]


def run(name, conclusion="success", status="completed", started="2026-11-12T10:00:00Z"):
    return {"name": name, "status": status, "conclusion": conclusion if status == "completed" else None,
            "started_at": started, "completed_at": "2026-11-12T10:10:00Z" if status == "completed" else None}


def facts(**over):
    f = {
        "pr": {"number": 300, "state": "open", "merged": False, "draft": False, "mergeable": True,
               "mergeable_state": "clean", "user": {"login": OWNER}, "body": "Résumé\n\nCloses #299\n",
               "head": {"sha": SHA, "ref": "agent/299-petit-correctif", "repo": {"full_name": mp.REPO}},
               "base": {"ref": "main"}, "additions": 30, "deletions": 4, "changed_files": 2},
        "files": [{"filename": "webui/lody/view_home.py", "status": "modified", "patch": "@@\n+x = 1\n-y = 2"},
                  {"filename": "test/lody/test_home.py", "status": "modified", "patch": "@@\n+def test_x(): pass"}],
        "commits": [{"sha": "b" * 40, "parents": [{}]}, {"sha": SHA, "parents": [{}]}],
        "reviews": [],
        "check_runs": [run(n) for n in CODE_RUNS] + [run(mp.CHECK_DOCS, "skipped")],
        "ticket": {"number": 299, "state": "open", "labels": [{"name": "agent:auto"}, {"name": "merge:auto"}]},
        "ticket_events": [{"event": "labeled", "label": {"name": "merge:auto"}, "actor": {"login": OWNER}}],
    }
    for key, value in over.items():
        f[key] = value
    return f


def refused(decision):
    return {c.code for c in decision.refusals}


def with_pr(**changes):
    f = facts()
    f["pr"].update(changes)
    return f


def test_nominal_low_risk_pr_is_eligible_but_nothing_is_merged():
    d = mp.evaluate(facts())
    assert d.eligible, d.refusals
    assert d.dry_run is True and d.ticket == 299
    req = d.merge_request()
    # L'activation future fusionnerait avec SHA attendu (course de SHA rejetée par GitHub).
    assert req["method"] == "PUT" and req["fields"]["sha"] == SHA
    assert "ÉLIGIBLE (simulation" in mp.render(d)
    assert json.loads(json.dumps(d.to_dict()))["eligible"] is True


def test_module_has_no_write_path_to_github(monkeypatch):
    """Phase 1 : seules des requêtes GET sont émises, même par la CLI."""
    seen = []

    class Proc:
        returncode, stderr = 0, ""
        stdout = json.dumps(facts()["pr"])

    def fake_run(cmd, **_kw):
        seen.append(cmd)
        return Proc()

    monkeypatch.setattr(mp.subprocess, "run", fake_run)
    mp.gh_get("repos/x/pulls/1")
    assert seen[0][1:4] == ["api", "-X", "GET"]
    src = (ROOT / "deploy" / "lodyorch" / "merge_policy.py").read_text(encoding="utf-8")
    assert '"-X", "PUT"' not in src and "gh pr merge" not in src


@pytest.mark.parametrize("change, code", [
    ({"draft": True}, "pr-non-draft"),
    ({"merged": True, "state": "closed"}, "pr-ouverte"),
    ({"mergeable": False, "mergeable_state": "dirty"}, "sans-conflit"),
    ({"mergeable_state": "behind"}, "base-a-jour"),
    ({"mergeable": None, "mergeable_state": "unknown"}, "sans-conflit"),
    ({"base": {"ref": "develop"}}, "base-main"),
    ({"user": {"login": "inconnu"}}, "auteur-de-confiance"),
    ({"body": "Pas de référence"}, "ticket-lie"),
    ({"body": "Closes #12"}, "ticket-lie"),
    ({"changed_files": 80}, "perimetre-borne"),
    ({"additions": 5000}, "perimetre-borne"),
])
def test_each_pr_criterion_refuses(change, code):
    assert code in refused(mp.evaluate(with_pr(**change)))


def test_fork_pr_is_refused():
    f = facts()
    f["pr"]["head"]["repo"] = {"full_name": "quelquun/fork"}
    assert "pas-de-fork" in refused(mp.evaluate(f))
    f["pr"]["head"]["repo"] = None
    assert "pas-de-fork" in refused(mp.evaluate(f))


def test_authorization_label_must_be_present_and_set_by_trusted_actor():
    f = facts(ticket={"state": "open", "labels": [{"name": "agent:auto"}]})
    assert "autorisation-merge-auto" in refused(mp.evaluate(f))
    f = facts(ticket_events=[{"event": "labeled", "label": {"name": "merge:auto"}, "actor": {"login": "tiers"}}])
    assert "autorisation-merge-auto" in refused(mp.evaluate(f))
    # Posé par le propriétaire, retiré puis reposé par un tiers : l'autorisation ne vaut plus.
    f = facts(ticket_events=[
        {"event": "labeled", "label": {"name": "merge:auto"}, "actor": {"login": OWNER}},
        {"event": "unlabeled", "label": {"name": "merge:auto"}, "actor": {"login": OWNER}},
        {"event": "labeled", "label": {"name": "merge:auto"}, "actor": {"login": "tiers"}}])
    assert "autorisation-merge-auto" in refused(mp.evaluate(f))


@pytest.mark.parametrize("veto", mp.VETO_LABELS)
def test_veto_labels_win_on_ticket_or_pr(veto):
    f = facts()
    f["ticket"]["labels"].append({"name": veto})
    assert "aucun-veto" in refused(mp.evaluate(f))
    f = facts()
    f["pr"]["labels"] = [{"name": veto}]
    assert "aucun-veto" in refused(mp.evaluate(f))


def test_ticket_must_be_open_and_queued():
    f = facts()
    f["ticket"]["state"] = "closed"
    f["ticket"]["labels"] = [{"name": "merge:auto"}]
    assert {"ticket-ouvert", "ticket-agent-auto"} <= refused(mp.evaluate(f))


def test_changes_requested_review_blocks_until_approved_by_same_reviewer():
    rq = {"user": {"login": "r"}, "state": "CHANGES_REQUESTED"}
    assert "aucune-demande-de-modification" in refused(mp.evaluate(facts(reviews=[rq])))
    ok = {"user": {"login": "r"}, "state": "APPROVED"}
    assert mp.evaluate(facts(reviews=[rq, {"user": {"login": "r"}, "state": "COMMENTED"}, ok])).eligible


@pytest.mark.parametrize("runs, why", [
    ([], "aucune vérification"),
    ([run(n) for n in CODE_RUNS[:2]], "manquantes"),  # un seul job vert ne suffit pas
    ([run(n) for n in CODE_RUNS[:-1]] + [run(CODE_RUNS[-1], status="in_progress")], "en cours"),
    ([run(n) for n in CODE_RUNS[:-1]] + [run(CODE_RUNS[-1], "failure")], "failure"),
    ([run(n) for n in CODE_RUNS[:-1]] + [run(CODE_RUNS[-1], "skipped")], "skipped"),
    ([run(n) for n in CODE_RUNS] + [run("Build worker 2D image and render", "failure")], "worker 2D"),
])
def test_ci_must_be_fully_green_on_head_sha(runs, why):
    d = mp.evaluate(facts(check_runs=runs))
    ci = next(c for c in d.checks if c.code == "ci")
    assert not ci.ok and not d.eligible
    assert why.split()[0].lower() in ci.detail.lower() or why == "worker 2D"


def test_ci_rerun_supersedes_earlier_failure():
    runs = [run(n) for n in CODE_RUNS] + [run(CODE_RUNS[1], "failure", started="2026-11-12T09:00:00Z")]
    assert mp.evaluate(facts(check_runs=runs)).eligible


def test_docs_only_pr_requires_documentation_job_and_no_test_change():
    f = facts(files=[{"filename": "docs/lody-ui.md", "status": "modified", "patch": "+texte"}],
              check_runs=[run(mp.CHECK_DETECT), run(mp.CHECK_DOCS)] + [run(n, "skipped") for n in mp.CHECKS_CODE])
    f["pr"]["changed_files"] = 1
    assert mp.evaluate(f).eligible
    f["check_runs"] = [run(mp.CHECK_DETECT)] + [run(n, "skipped") for n in mp.CHECKS_CODE]
    assert "ci" in refused(mp.evaluate(f))


def test_code_change_without_test_change_is_refused():
    f = facts(files=[facts()["files"][0]])
    f["pr"]["changed_files"] = 1
    assert "tests-modifies" in refused(mp.evaluate(f))


@pytest.mark.parametrize("path, category", [
    (".github/workflows/ci.yml", "infra-ci-cd"),
    ("Dockerfile.lody", "infra-ci-cd"),
    ("docker-compose.lody.yml", "infra-ci-cd"),
    ("deploy/lodyorch/guard.py", "infra-ci-cd"),
    ("scripts/lody-apply-secrets.sh", "auth-secrets"),
    ("uv.lock", "dependances"),
    ("lody_auth/app.py", "auth-secrets"),
    ("webui/lody/secrets_guard.py", "auth-secrets"),
    ("webui/lody/db.py", "donnees-migrations"),
    ("app/services/voice.py", "api-payantes-video"),
    ("webui/lody/generation/runner.py", "api-payantes-video"),
    ("webui/lody/animation/render.py", "api-payantes-video"),
])
def test_default_exclusions_by_path(path, category):
    assert category in mp.excluded_categories(path)
    f = facts()
    f["files"].append({"filename": path, "status": "modified", "patch": "+x"})
    f["pr"]["changed_files"] = 3
    assert "aucune-zone-exclue" in refused(mp.evaluate(f))


def test_documentation_is_never_an_excluded_zone_by_itself():
    assert mp.excluded_categories("docs/lody-auth.md") == []
    assert mp.excluded_categories("README.md") == []
    assert mp.excluded_categories("webui/lody/view_home.py") == []


@pytest.mark.parametrize("added, code", [
    ("con.execute('DROP TABLE drafts')", "aucune-migration-destructive"),
    ("ALTER TABLE projects DROP COLUMN x", "aucune-migration-destructive"),
    ("DELETE FROM projects WHERE 1", "aucune-migration-destructive"),
    ("ALTER TABLE projects ADD COLUMN y TEXT", "aucune-migration-additive"),
    ("CREATE TABLE IF NOT EXISTS z (id INTEGER)", "aucune-migration-additive"),
    ("url = 'https://api.elevenlabs.io/v1/tts'", "aucun-appel-payant-ou-aws"),
    ("import boto3", "aucun-appel-payant-ou-aws"),
    ("KEY = 'sk-" + "A" * 24 + "'", "aucun-secret"),
])
def test_sensitive_content_in_added_lines(added, code):
    f = facts()
    f["files"][0]["patch"] = f"@@\n+{added}\n"
    assert code in refused(mp.evaluate(f))


def test_removed_lines_and_removed_files():
    f = facts()
    f["files"][0]["patch"] = "@@\n-DROP TABLE ancienne\n+x = 1"  # SQL retiré, pas ajouté
    assert mp.evaluate(f).eligible
    f["files"].append({"filename": "webui/lody/vieux.py", "status": "removed"})
    f["pr"]["changed_files"] = 3
    assert "aucune-suppression-de-fichier" in refused(mp.evaluate(f))


def test_unreadable_or_truncated_diff_is_refused():
    f = facts()
    f["files"][0]["patch"] = None
    assert "diff-lisible" in refused(mp.evaluate(f))
    f = facts()
    f["pr"]["changed_files"] = 3  # liste de fichiers incomplète
    assert "perimetre-borne" in refused(mp.evaluate(f))


def test_conflict_loop_is_refused():
    merges = [{"sha": f"{i}" * 40, "parents": [{}, {}]} for i in range(4)]
    f = facts(commits=merges + [{"sha": SHA, "parents": [{}]}])
    assert "pas-de-boucle-de-conflits" in refused(mp.evaluate(f))


def test_head_moving_during_collection_or_before_merge_is_refused():
    f = facts()
    f["commits"][-1]["sha"] = "c" * 40
    assert "sha-de-tete-coherent" in refused(mp.evaluate(f))
    first = mp.evaluate(facts())
    moved = copy.deepcopy(facts())
    moved["pr"]["head"]["sha"] = "d" * 40
    moved["commits"][-1]["sha"] = "d" * 40
    again = mp.revalidate(first, moved)
    assert not again.eligible and "sha-attendu" in refused(again)
    assert mp.revalidate(first, facts()).eligible


def test_every_criterion_is_reported_not_only_the_first():
    f = with_pr(draft=True, mergeable_state="behind")
    f["ticket"]["labels"] = [{"name": "agent:auto"}, {"name": "merge:manual"}]
    assert {"pr-non-draft", "base-a-jour", "aucun-veto", "autorisation-merge-auto"} <= refused(mp.evaluate(f))


def test_collect_reads_paginated_github_facts():
    calls = []
    f = facts()

    def get(path):
        calls.append(path)
        if path.endswith("pulls/300"):
            return f["pr"]
        if "/files" in path:
            return f["files"]
        if "/commits?" in path or path.endswith("/commits"):
            return f["commits"]
        if "/reviews" in path:
            return []
        if "/check-runs" in path:
            return {"check_runs": f["check_runs"]}
        if path.endswith("issues/299"):
            return f["ticket"]
        if "/events" in path:
            return f["ticket_events"]
        if f"/compare/{SHA}...main" in path:
            return {"ahead_by": 0, "files": []}
        raise AssertionError(path)

    got = mp.collect(300, get)
    assert mp.evaluate(got).eligible
    assert any(f"commits/{SHA}/check-runs" in c for c in calls)
    assert got["base_compare"] == {"ahead_by": 0, "files": []}


def test_metrics_measure_wait_between_green_ci_and_merge():
    prs = [{"number": 1, "title": "a", "merged_at": "2026-11-12T10:30:00Z", "head": {"sha": "1"}},
           {"number": 2, "title": "b", "merged_at": None, "head": {"sha": "2"}},
           {"number": 3, "title": "c", "merged_at": "2026-11-12T10:20:00Z", "head": {"sha": "3"}}]

    def get(path):
        if "pulls?state=closed" in path:
            return prs
        return {"check_runs": [run("x")]}  # terminé à 10:10

    m = mp.metrics(10, get)
    assert [r["pr"] for r in m["prs"]] == [1, 3]
    assert m["median_wait_s"] == 900 and m["total_wait_s"] == 1800


# --- Phase 2 (#200) --------------------------------------------------------------------------------

def test_aggregate_ci_result_replaces_conditional_jobs_when_present():
    runs = [run(mp.CHECK_DETECT), run("Python 3.12 tests"), run("Windows smoke tests", "skipped"), run(mp.CHECK_AGGREGATE)]
    assert mp.evaluate(facts(check_runs=runs)).eligible
    runs[-1] = run(mp.CHECK_AGGREGATE, "failure")
    assert "ci" in refused(mp.evaluate(facts(check_runs=runs)))
    # Sans agrégateur, les jobs historiques restent exigés (avant la fusion de #198).
    assert mp.required_checks(facts()["files"], {mp.CHECK_DETECT}) == (mp.CHECK_DETECT, *mp.CHECKS_CODE)


@pytest.mark.parametrize("compare, ok", [
    ({"ahead_by": 0, "files": []}, True),
    ({"ahead_by": 3, "files": [{"filename": "docs/x.md"}]}, True),
    ({"ahead_by": 3, "files": [{"filename": "webui/lody/view_home.py"}]}, False),
    ({"ahead_by": mp.MAX_BEHIND + 1, "files": []}, False),
    (None, False),
])
def test_base_behind_main_only_without_common_files(compare, ok):
    d = mp.evaluate(facts(base_compare=compare))
    assert ("base-sans-chevauchement" not in refused(d)) is ok


def test_authorization_must_follow_activation():
    events = [{"event": "labeled", "label": {"name": "merge:auto"}, "actor": {"login": OWNER},
               "created_at": "2026-11-12T09:00:00Z"}]
    late = mp.evaluate(facts(ticket_events=events, authorized_after="2026-11-12T10:00:00Z"))
    assert refused(late) == {"autorisation-apres-activation"}
    assert mp.evaluate(facts(ticket_events=events, authorized_after="2026-11-12T08:00:00Z")).eligible
    assert mp.last_label_event(events + [{"event": "unlabeled", "label": {"name": "merge:auto"}}], "merge:auto") is None


def test_merge_refuses_dry_run_or_ineligible_decision_without_calling_github(monkeypatch):
    monkeypatch.setattr(mp.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("appel")))
    d = mp.evaluate(facts())
    assert d.eligible and d.dry_run
    with pytest.raises(mp.MergeError):
        mp.merge(d)
    d.dry_run, d.eligible = False, False
    with pytest.raises(mp.MergeError):
        mp.merge(d)


def test_merge_sends_expected_sha_and_maps_http_errors(monkeypatch):
    seen = []

    class Proc:
        def __init__(self, code, out="", err=""):
            self.returncode, self.stdout, self.stderr = code, out, err
    answers = [Proc(0, json.dumps({"merged": True, "sha": "m" * 40})),
               Proc(1, err="gh: Head branch was modified. (HTTP 409)")]
    monkeypatch.setattr(mp.subprocess, "run", lambda args, **k: seen.append(args) or answers.pop(0))
    d = mp.evaluate(facts())
    d.dry_run = False
    assert mp.merge(d, title="t") == "m" * 40
    assert seen[0][2:5] == ["-X", "PUT", f"repos/{mp.REPO}/pulls/300/merge"] and f"sha={SHA}" in seen[0]
    with pytest.raises(mp.MergeError) as err:
        mp.merge(d)
    assert err.value.status == 409
