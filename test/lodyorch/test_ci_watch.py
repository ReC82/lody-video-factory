"""Suivi de la CI par le superviseur (#117 / #127) : aucune session Claude n'attend la CI ; la file
avance sur un autre ticket ; correction bornée ; jamais « prête » si la CI ou la base ne l'est pas."""

AUTO = "agent:auto"
MAIN = {"ref": "main", "repo": {"default_branch": "main"}}


def pr(orch, number, sha, base=None, head_ref=None, **extra):
    data = orch.gh()
    data.setdefault("pulls", {})[str(number)] = dict(dict(number=number, state="open", merged=False,
                                                            head={"sha": sha, "ref": head_ref or f"agent/{number}"},
                                                            base=base or MAIN), **extra)
    orch.set_gh(data)


def checks(orch, sha, *runs):
    data = orch.gh()
    data.setdefault("checks", {})[sha] = [dict(name=n, status=st, conclusion=c, html_url=f"https://ci/{n}")
                                          for n, st, c in runs]
    orch.set_gh(data)


def poll(orch):
    orch.con.execute("UPDATE tasks SET ci_checked_at=''")
    orch.con.commit()
    orch.mod.watch_ci(orch.con)


def deliver(orch, n):
    """Ticket n livré par une session (PR 9n) : CI en attente."""
    assert orch.cycle() == n
    t = orch.mod.task(orch.con, n)
    assert (t["status"], t["ci_state"]) == ("WAITING_OWNER", "pending")
    return t


def test_delivery_is_not_ready_while_ci_runs_and_the_queue_moves_on(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.issue(2, [AUTO, "priority:P1"])
    deliver(orch, 1)
    assert "agent:in-progress" in orch.labels(1) and "status:needs-test" not in orch.labels(1)
    pr(orch, 91, "s1")
    checks(orch, "s1", ("tests", "in_progress", None))
    poll(orch)
    assert orch.mod.task(orch.con, 1)["ci_state"] == "pending"
    # Pendant la CI de #1, la file traite un autre ticket indépendant ; Claude n'attend rien.
    assert orch.cycle() == 2
    assert [c["issue"] for c in orch.claude_calls()] == ["1", "2"]
    checks(orch, "s1", ("tests", "completed", "success"), ("lint", "completed", "skipped"))
    poll(orch)
    t = orch.mod.task(orch.con, 1)
    assert (t["status"], t["ci_state"]) == ("WAITING_OWNER", "success")
    assert "status:needs-test" in orch.labels(1) and "agent:in-progress" not in orch.labels(1)
    assert orch.mod.run_state(t) == "ready_for_review"


def test_ci_is_watched_while_another_session_runs(orch, monkeypatch):
    """Le verdict de CI n'attend pas la fin de la session suivante (constaté en essai à blanc)."""
    orch.issue(1, [AUTO, "priority:P0"])
    orch.issue(2, [AUTO, "priority:P1"])
    deliver(orch, 1)
    pr(orch, 91, "s1")
    checks(orch, "s1", ("tests", "completed", "success"))
    seen = []
    real = orch.mod.watch_ci

    def spy(con):
        real(con)
        seen.append(orch.mod.task(con, 1)["ci_state"])
    monkeypatch.setattr(orch.mod, "watch_ci", spy)
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "3")
    assert orch.cycle() == 2  # la session #2 tourne ~3 s : la CI de #1 est relevée pendant ce temps
    assert "success" in seen
    assert "status:needs-test" in orch.labels(1)


def test_polling_is_rate_limited_per_pr(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    deliver(orch, 1)
    pr(orch, 91, "s1")
    checks(orch, "s1", ("tests", "queued", None))
    orch.mod.watch_ci(orch.con)
    before = orch.gh().get("requests", 0)
    for _ in range(5):
        orch.mod.watch_ci(orch.con)  # moins de LQ_CI_POLL écoulé : aucune requête
    assert orch.gh().get("requests", 0) == before


def test_ci_failure_relaunches_a_bounded_fix_then_blocks_with_a_diagnostic(orch):
    orch.issue(1, [AUTO, "priority:P2"])
    orch.issue(2, [AUTO, "priority:P0"], body="Depends on #99")
    orch.issue(99, ["priority:P0"])
    deliver(orch, 1)
    pr(orch, 91, "s1")
    for attempt in (1, 2):
        checks(orch, "s1", ("Python 3.11 tests", "completed", "failure"))
        poll(orch)
        t = orch.mod.task(orch.con, 1)
        assert (t["status"], t["ci_fixes"]) == ("RUNNING", attempt)
        assert "Python 3.11 tests : failure (https://ci/Python 3.11 tests)" in t["note"]
        assert orch.mod.run_state(t) == "ci_failed"
        assert orch.next_issue() == 1  # la correction passe avant tout nouveau ticket
        assert orch.cycle() == 1
        assert orch.mod.task(orch.con, 1)["ci_state"] == "pending"
    checks(orch, "s1", ("Python 3.11 tests", "completed", "failure"))
    poll(orch)
    t = orch.mod.task(orch.con, 1)
    assert t["status"] == "BLOCKED" and "après 2 corrections" in t["blockers"]
    assert "agent:blocked" in orch.labels(1)
    comments = [c for c in orch.gh()["comments"] if c["issue"] == 1 and "CI en échec" in c["body"]]
    assert len(comments) == 1
    poll(orch)  # plus suivi : pas de boucle de correction ni de commentaire en double
    assert len([c for c in orch.gh()["comments"] if "CI en échec" in c["body"]]) == 1
    assert len(orch.claude_calls()) == 3


def test_stacked_pr_is_not_ready_until_its_base_is_validated(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    deliver(orch, 1)
    pr(orch, 91, "top", base={"ref": "feat/base", "repo": {"default_branch": "main"}})
    pr(orch, 50, "basesha", head_ref="feat/base")
    checks(orch, "top", ("tests", "completed", "success"))
    checks(orch, "basesha", ("tests", "in_progress", None))
    poll(orch)
    t = orch.mod.task(orch.con, 1)
    assert t["ci_state"] == "pending" and "base #50" in t["ci_detail"]
    assert "status:needs-test" not in orch.labels(1)
    checks(orch, "basesha", ("tests", "completed", "success"))
    poll(orch)
    assert orch.mod.task(orch.con, 1)["ci_state"] == "success"


def test_merged_or_closed_pr_stops_the_watch(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.issue(2, [AUTO, "priority:P1"])
    deliver(orch, 1)
    pr(orch, 91, "s1", merged=True, state="closed")
    poll(orch)
    assert orch.mod.task(orch.con, 1)["ci_state"] == "merged"
    deliver(orch, 2)
    pr(orch, 92, "s2", state="closed")
    poll(orch)
    t = orch.mod.task(orch.con, 2)
    assert (t["status"], t["ci_state"]) == ("BLOCKED", "closed")


def test_ci_without_conclusion_times_out_into_blocked(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    deliver(orch, 1)
    pr(orch, 91, "s1")
    orch.mod.update_task(orch.con, 1, ci_since="2026-01-01T00:00:00Z")
    poll(orch)  # aucune vérification signalée depuis longtemps
    t = orch.mod.task(orch.con, 1)
    assert t["status"] == "BLOCKED" and "sans conclusion" in t["blockers"]


def test_dependent_ticket_waits_for_merge_not_for_green_ci(orch):
    orch.issue(1, [AUTO, "priority:P1"])
    orch.issue(2, [AUTO, "priority:P0"], body="Depends on #1")
    orch.issue(3, [AUTO, "priority:P3"])
    deliver(orch, 1)  # dépendance traitée d'abord
    pr(orch, 91, "s1")
    checks(orch, "s1", ("tests", "completed", "success"))
    poll(orch)
    assert orch.next_issue() == 3  # #2 attend la fusion de #1 : la file choisit autre chose


def test_status_and_statusline_distinguish_pr_open_ci_pending_and_validated(orch):
    import os
    import subprocess
    import sys

    from test.lodyorch.conftest import ORCH_DIR
    orch.issue(1, [AUTO, "priority:P0"])
    deliver(orch, 1)
    pr(orch, 91, "s1")
    checks(orch, "s1", ("tests", "in_progress", None))
    poll(orch)
    cli = [sys.executable, str(ORCH_DIR / "lodyorch.py")]
    out = subprocess.run(cli + ["status"], env=dict(os.environ), capture_output=True, text=True, timeout=60).stdout
    assert "CI en attente" in out and "CI : 0/1 terminées" in out
    line = subprocess.run(cli + ["statusline"], capture_output=True, text=True, timeout=60).stdout
    assert "1 CI en cours" in line and "0 PR en revue" in line
    pulse = subprocess.run(cli + ["pulse"], capture_output=True, text=True, timeout=60).stdout
    assert "PR #91 CI ⏳" in pulse
