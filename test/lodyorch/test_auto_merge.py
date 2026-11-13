"""Fusion automatique sécurisée par le superviseur (#200, phase 2) : faux GitHub, aucun réseau.
Le superviseur évalue sans session Claude, journalise une fois par verdict, ne fusionne qu'en mode `on`
avec revalidation et SHA attendu, sans fusion rétroactive ni double fusion, et ne déploie rien."""

import argparse
import json

import pytest

AUTO = "agent:auto"
SHA = "a" * 40
BEFORE, AFTER = "2000-01-01T00:00:00Z", "2999-01-01T00:00:00Z"
CODE_CHECKS = ("Detect code changes", "Python 3.11 tests", "Python 3.13 tests", "Windows smoke tests")
SAFE_FILES = [{"filename": "webui/lody/view_home.py", "status": "modified", "patch": "@@\n+x = 1"},
              {"filename": "test/lody/test_home.py", "status": "modified", "patch": "@@\n+def test_x(): pass"}]


def mp(orch):
    return orch.mod.merge_policy()


def owner(orch):
    return mp(orch).TRUSTED_ACTORS[0]


def label_event(orch, n, label="merge:auto", actor=None, at=AFTER, event="labeled"):
    data = orch.gh()
    data.setdefault("events", {}).setdefault(str(n), []).append(
        {"event": event, "label": {"name": label}, "actor": {"login": actor or owner(orch)}, "created_at": at})
    orch.set_gh(data)


def set_checks(orch, sha, *runs):
    data = orch.gh()
    data.setdefault("checks", {})[sha] = [dict(name=name, status=status, conclusion=concl, started_at="2026-11-12T10:00:00Z",
                                               html_url=f"https://ci/{name}") for name, status, concl in runs]
    orch.set_gh(data)


def ready(orch, n=1, files=None, labels=(AUTO, "priority:P0", "merge:auto"), authorized_at=AFTER, **pr_extra):
    """Ticket livré (PR 9n), CI verte constatée par watch_ci : « prêt pour revue »."""
    orch.issue(n, [AUTO, "priority:P0"])
    assert orch.cycle() == n
    num = 90 + n
    data = orch.gh()
    pr = dict(number=num, state="open", merged=False, draft=False, mergeable=True, mergeable_state="clean",
              user={"login": owner(orch)}, body=f"Résumé\n\nCloses #{n}\n",
              head={"sha": SHA, "ref": f"agent/{n}-petit-correctif", "repo": {"full_name": mp(orch).REPO}},
              base={"ref": "main", "repo": {"default_branch": "main"}}, additions=10, deletions=2,
              files=files if files is not None else SAFE_FILES, commits=[{"sha": SHA, "parents": [{}], "commit": {"message": "fix: correctif"}}], reviews=[])
    pr["changed_files"] = len(pr["files"])
    pr.update(pr_extra)
    data.setdefault("pulls", {})[str(num)] = pr
    data["issues"][str(n)]["labels"] = list(labels)
    data["merge_closes_issue"] = True
    orch.set_gh(data)
    if "merge:auto" in labels and authorized_at:
        label_event(orch, n, at=authorized_at)
    set_checks(orch, SHA, *[(c, "completed", "success") for c in CODE_CHECKS])
    orch.con.execute("UPDATE tasks SET ci_checked_at=''")
    orch.con.commit()
    orch.mod.watch_ci(orch.con)
    assert orch.mod.task(orch.con, n)["ci_state"] == "success"
    return num


def poll(orch):
    orch.con.execute("UPDATE tasks SET merge_checked_at=''")
    orch.con.commit()
    return orch.mod.watch_merges(orch.con)


def automerge(orch, mode, ceiling=None, monkeypatch=None):
    if ceiling:
        monkeypatch.setattr(orch.mod, "AUTO_MERGE_CEILING", ceiling)
    orch.mod.cmd_automerge(orch.con, argparse.Namespace(mode=mode, limit=10))


def merges(orch):
    return orch.gh().get("merges", [])


def comments(orch, n, needle):
    return [c for c in orch.gh().get("comments", []) if c["issue"] == n and needle in c["body"]]


def audit(orch):
    return orch.con.execute("SELECT * FROM merge_audit ORDER BY id").fetchall()


# --- modes, plafond, kill switch ------------------------------------------------------------------

def test_instance_ceiling_is_dry_run_and_mode_is_the_lowest(orch, monkeypatch):
    assert orch.mod.AUTO_MERGE_CEILING == "dry-run"  # lodyorch.env livré : simulation seulement
    assert orch.mod.auto_merge_mode(orch.con)[0] == "dry-run"
    automerge(orch, "on")
    assert orch.mod.auto_merge_mode(orch.con)[0] == "dry-run"  # plafond plus bas : pas de fusion
    monkeypatch.setattr(orch.mod, "AUTO_MERGE_CEILING", "on")
    assert orch.mod.auto_merge_mode(orch.con)[0] == "on"
    automerge(orch, "off")
    assert orch.mod.auto_merge_mode(orch.con)[0] == "off"
    orch.mod.ctl_set(orch.con, "automerge.mode", "on")  # « on » sans horodatage d'activation : refusé
    assert orch.mod.auto_merge_mode(orch.con)[0] == "dry-run"


def test_dry_run_logs_verdict_once_comments_once_and_never_merges(orch):
    num = ready(orch)
    poll(orch)
    rows = audit(orch)
    assert len(rows) == 1 and rows[0]["eligible"] == 1 and rows[0]["mode"] == "dry-run" and rows[0]["pr"] == num
    assert len(comments(orch, 1, "**Simulation**")) == 1
    for _ in range(3):  # idempotent : rien de neuf, rien de journalisé ni commenté
        poll(orch)
    assert len(audit(orch)) == 1 and len(comments(orch, 1, "**Simulation**")) == 1
    assert merges(orch) == [] and orch.mod.task(orch.con, 1)["ci_state"] == "success"


def test_kill_switch_off_makes_no_github_request(orch, monkeypatch):
    ready(orch)
    automerge(orch, "off", ceiling="on", monkeypatch=monkeypatch)
    before = orch.gh().get("requests", 0)
    assert poll(orch) == []
    assert orch.gh().get("requests", 0) == before and audit(orch) == []


def test_unauthorized_ready_pr_is_evaluated_once_per_sha(orch):
    ready(orch, labels=(AUTO, "priority:P0"))
    poll(orch)
    rows = audit(orch)
    assert len(rows) == 1 and not rows[0]["eligible"]
    assert "autorisation-merge-auto" in rows[0]["refusals"]
    before = orch.gh().get("requests", 0)
    poll(orch)  # même SHA, toujours sans autorisation : 2 lectures légères, pas de collecte complète
    assert orch.gh().get("requests", 0) - before <= 2
    assert comments(orch, 1, "Fusion automatique") == []  # pas de bruit sans demande


# --- fusion réelle (mode on) ------------------------------------------------------------------

def test_on_merges_authorized_pr_with_expected_sha_keeps_ticket_to_test(orch, monkeypatch):
    num = ready(orch)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    lines = poll(orch)
    assert merges(orch) == [{"pr": num, "sha": SHA, "merge_method": "merge",
                             "commit_title": f"Merge pull request #{num} (fusion automatique lodyorch, #1)"}]
    assert lines and "PR #91 fusionnée" in lines[0] and "pas encore en ligne" in lines[0]
    t = orch.mod.task(orch.con, 1)
    assert (t["status"], t["ci_state"]) == ("WAITING_OWNER", "merged") and "non déployée" in t["ci_detail"]
    gh = orch.gh()
    assert gh["issues"]["1"]["state"] == "open"  # rouvert après « Closes #1 » : test humain requis
    assert "status:needs-test" in orch.labels(1)
    assert len(comments(orch, 1, "fusionnée automatiquement")) == 1
    assert orch.con.execute("SELECT state FROM merge_ops").fetchone()["state"] == "merged"
    poll(orch)  # plus rien à faire : aucune seconde fusion
    assert len(merges(orch)) == 1


def test_authorization_before_activation_is_never_retroactive(orch, monkeypatch):
    ready(orch, authorized_at=BEFORE)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    poll(orch)
    assert merges(orch) == []
    assert "autorisation-apres-activation" in audit(orch)[-1]["refusals"]
    assert len(comments(orch, 1, "**Fusion automatique refusée**")) == 1
    label_event(orch, 1, event="unlabeled", at=AFTER)
    label_event(orch, 1, at=AFTER)  # le propriétaire repose le label après l'activation
    poll(orch)
    assert len(merges(orch)) == 1


@pytest.mark.parametrize("case, expected", [
    ("untrusted-actor", "autorisation-merge-auto"),
    ("veto", "aucun-veto"),
    ("sensitive", "aucune-zone-exclue"),
    ("migration", "aucune-migration-additive"),
    ("fork", "pas-de-fork"),
    ("draft", "pr-non-draft"),
    ("conflict", "sans-conflit"),
    ("changes-requested", "aucune-demande-de-modification"),
])
def test_on_refuses_unsafe_or_unauthorized_pr_with_intelligible_reason(orch, monkeypatch, case, expected):
    files, labels, extra = None, (AUTO, "priority:P0", "merge:auto"), {}
    if case == "veto":
        labels = (*labels, "merge:manual")
    if case == "sensitive":
        files = [{"filename": "deploy/lodyorch/lodyorch.py", "status": "modified", "patch": "@@\n+x"},
                 {"filename": "test/lodyorch/test_x.py", "status": "modified", "patch": "@@\n+y"}]
    if case == "migration":
        files = [{"filename": "webui/lody/projects.py", "status": "modified",
                  "patch": "@@\n+con.execute('ALTER TABLE projects ADD COLUMN x TEXT')"}, SAFE_FILES[1]]
    if case == "fork":
        extra["head"] = {"sha": SHA, "ref": "agent/1-x", "repo": {"full_name": "someone/fork"}}
    if case == "draft":
        extra["draft"] = True
    if case == "conflict":
        extra.update(mergeable=False, mergeable_state="dirty")
    if case == "changes-requested":
        extra["reviews"] = [{"user": {"login": "relecteur"}, "state": "CHANGES_REQUESTED"}]
    ready(orch, files=files, labels=labels, authorized_at=None if case == "untrusted-actor" else AFTER, **extra)
    if case == "untrusted-actor":
        label_event(orch, 1, actor="inconnu")
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    poll(orch)
    assert merges(orch) == []
    assert expected in audit(orch)[-1]["refusals"]
    refusal = comments(orch, 1, "**Fusion automatique refusée**")
    assert len(refusal) == 1 and f"`{expected}`" in refusal[0]["body"]


def test_partially_green_ci_is_refused(orch, monkeypatch):
    ready(orch)
    set_checks(orch, SHA, *[(c, "completed", "success") for c in CODE_CHECKS[:-1]],
               ("Windows smoke tests", "in_progress", None))
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    poll(orch)
    assert merges(orch) == [] and "\"ci\"" in audit(orch)[-1]["refusals"]


def test_aggregate_ci_result_of_198_is_the_required_check_when_present(orch, monkeypatch):
    ready(orch)
    set_checks(orch, SHA, ("Detect code changes", "completed", "success"), ("Python 3.12 tests", "completed", "success"),
               ("Windows smoke tests", "completed", "skipped"), ("CI result", "completed", "success"))
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    poll(orch)
    assert len(merges(orch)) == 1


def test_base_behind_is_accepted_only_without_common_files(orch, monkeypatch):
    ready(orch)
    data = orch.gh()
    data["compare"] = {SHA: {"ahead_by": 2, "files": [{"filename": "webui/lody/view_home.py"}]}}
    orch.set_gh(data)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    poll(orch)
    assert merges(orch) == [] and "base-sans-chevauchement" in audit(orch)[-1]["refusals"]
    data = orch.gh()
    data["compare"] = {SHA: {"ahead_by": 2, "files": [{"filename": "docs/autre.md"}]}}
    orch.set_gh(data)
    poll(orch)
    assert len(merges(orch)) == 1


def test_sha_race_between_evaluation_and_revalidation_blocks_the_merge(orch, monkeypatch):
    ready(orch)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    policy = mp(orch)
    real, calls = policy.collect, []

    def collect(num, *a, **kw):
        calls.append(num)
        if len(calls) == 2:  # un push arrive juste avant la revalidation
            data = orch.gh()
            pr = data["pulls"][str(num)]
            pr["head"]["sha"] = "c" * 40
            pr["commits"] = [{"sha": "c" * 40, "parents": [{}], "commit": {"message": "fix: push"}}]
            orch.set_gh(data)
            set_checks(orch, "c" * 40, *[(c, "completed", "success") for c in CODE_CHECKS])
        return real(num, *a, **kw)
    monkeypatch.setattr(policy, "collect", collect)
    poll(orch)
    assert merges(orch) == []
    assert "sha-attendu" in audit(orch)[-1]["refusals"]


def test_github_409_is_recorded_and_never_retried_for_the_same_sha(orch, monkeypatch):
    ready(orch)
    data = orch.gh()
    data["merge_error"] = "Head branch was modified. Review and try the merge again. (HTTP 409)"
    orch.set_gh(data)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    poll(orch)
    op = orch.con.execute("SELECT * FROM merge_ops").fetchone()
    assert op["state"] == "failed" and "409" in op["error"]
    assert orch.con.execute("SELECT 1 FROM events WHERE kind='merge_error' AND message LIKE '%course de SHA%'").fetchone()
    poll(orch)
    assert len(merges(orch)) == 1  # une seule tentative pour ce SHA
    assert orch.mod.task(orch.con, 1)["ci_state"] == "success"  # reste en revue manuelle


def test_api_errors_do_not_crash_nor_merge(orch, monkeypatch):
    ready(orch)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    data = orch.gh()
    data["offline"] = True
    orch.set_gh(data)
    assert poll(orch) == []
    assert audit(orch) == [] and merges(orch) == []
    policy = mp(orch)
    data["offline"] = False
    orch.set_gh(data)

    def boom(num, *a, **kw):
        raise RuntimeError("gh api : HTTP 502")
    monkeypatch.setattr(policy, "collect", boom)
    assert poll(orch) == []
    assert orch.con.execute("SELECT 1 FROM events WHERE kind='merge_error' AND message LIKE '%collecte impossible%'").fetchone()


def test_restart_after_intent_reads_real_state_and_never_replays(orch, monkeypatch):
    num = ready(orch)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    # Arrêt brutal après l'écriture de l'intention : la PR n'a pas été fusionnée.
    orch.con.execute("INSERT INTO merge_ops(pr,head_sha,issue,state,started_at) VALUES(?,?,?,?,?)",
                     (num, SHA, 1, "intent", "2026-11-12T00:00:00Z"))
    orch.con.commit()
    assert orch.mod.recover_merges(orch.con) == []
    assert orch.con.execute("SELECT state FROM merge_ops").fetchone()["state"] == "interrupted"
    assert merges(orch) == []
    # Second cas : la fusion avait abouti côté GitHub avant l'arrêt.
    orch.con.execute("UPDATE merge_ops SET state='intent'")
    orch.con.commit()
    data = orch.gh()
    data["pulls"][str(num)].update(merged=True, state="closed", merge_commit_sha="d" * 40)
    orch.set_gh(data)
    lines = orch.mod.recover_merges(orch.con)
    assert lines and merges(orch) == []  # constatée, jamais rejouée
    assert orch.mod.task(orch.con, 1)["ci_state"] == "merged"


def test_at_most_one_merge_per_cycle(orch, monkeypatch):
    ready(orch, n=1)
    ready(orch, n=2)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    poll(orch)
    assert len(merges(orch)) == 1
    poll(orch)
    assert len(merges(orch)) == 2


def test_cli_status_lists_verdicts(orch, capsys):
    ready(orch)
    poll(orch)
    orch.mod.cmd_automerge(orch.con, argparse.Namespace(mode=None, limit=10))
    out = capsys.readouterr().out
    assert "Fusion automatique : dry-run" in out and "PR #91" in out and "éligible" in out
    assert json.loads(audit(orch)[0]["refusals"]) == []


def test_sessions_cannot_drive_or_perform_merges():
    import subprocess
    import sys

    from test.lodyorch.conftest import ORCH_DIR
    for cmd in ("lodyorch automerge on", "lodyorch automerge off",
                "gh api -X PUT repos/ReC82/lody-video-factory/pulls/12/merge -f sha=abc",
                "python3 deploy/lodyorch/merge_policy.py evaluate 3 && gh pr merge 3"):
        call = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
        proc = subprocess.run([sys.executable, str(ORCH_DIR / "guard.py")], input=call, capture_output=True, text=True)
        assert proc.returncode == 2, cmd
