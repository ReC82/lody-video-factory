"""File d'intégration (#228) : une PR ouverte en conflit, en échec de CI ou à base périmée est du
travail. Inventaire dynamique des PR, classement par valeur, session INTÉGRATION sur la même branche,
une tentative par état (tête, main), blocage humain explicite, et plus jamais « aucun travail utile »
tant qu'une PR est réparable."""

AUTO = "agent:auto"
GREEN = [{"name": "CI", "status": "completed", "conclusion": "success"}]
RED = [{"name": "CI", "status": "completed", "conclusion": "failure", "html_url": "https://x/run/1"}]
CODE = [{"filename": "app/services/x.py", "status": "modified", "patch": "@@\n+x = 1"}]
DOCS = [{"filename": "docs/episodes/x.md", "status": "added", "patch": "@@\n+# x"}]


def deliver(orch, *numbers, priority="priority:P1"):
    """Tickets livrés par lodyorch : WAITING_OWNER avec la PR fictive 9<n>."""
    orch.monkeypatch.setenv("FAKE_CLAUDE_MODE", "deliver")
    for n in numbers:
        orch.issue(n, [AUTO, priority])
    for _ in numbers:
        assert orch.cycle() is not None
    assert all(orch.status(n) == "WAITING_OWNER" for n in numbers)


def add_pr(orch, num, issue=None, branch=None, sha=None, mergeable=True, state="clean", checks=GREEN,
           files=CODE, draft=False):
    data = orch.gh()
    sha = sha or f"{num:0>4}" + "a" * 36
    branch = branch or f"agent/{issue}-fictif"
    data.setdefault("pulls", {})[str(num)] = {
        "number": num, "state": "open", "merged": False, "draft": draft, "title": f"PR {num}",
        "body": f"Closes #{issue}" if issue else "", "mergeable": mergeable, "mergeable_state": state,
        "head": {"sha": sha, "ref": branch, "repo": {"full_name": "ReC82/lody-video-factory"}},
        "base": {"ref": "main", "repo": {"default_branch": "main"}}, "files": files}
    if checks is not None:
        data.setdefault("checks", {})[sha] = checks
    orch.set_gh(data)
    return sha


def set_main(orch, sha):
    data = orch.gh()
    data["main_sha"] = sha
    orch.set_gh(data)


def inventory(orch):
    return {r["pr"]: r for r in orch.mod.pr_inventory(orch.con, force=True)}


def turn(orch):
    """Un tour de supervise() : ticket neuf, sinon intégration, sinon préparation."""
    mod = orch.mod
    orch.sync()
    t = mod.next_task(orch.con)
    integrate = None
    if t is None:
        mod.pr_inventory(orch.con)
        found = mod.next_integration(orch.con)
        if found:
            t, integrate = found
    if t is None:
        return None
    mod.run_session(orch.con, t, None, integrate)
    return (t["issue"], integrate["pr"] if integrate else None)


def test_inventory_classifies_every_open_pr_dynamically(orch):
    deliver(orch, 1, 2, 3, 4, 5, 6)
    orch.issue(7, [AUTO, "agent:blocked"])
    orch.sync()
    add_pr(orch, 91, 1, mergeable=False, state="dirty")
    add_pr(orch, 92, 2, checks=RED, state="unstable")
    add_pr(orch, 93, 3)
    add_pr(orch, 94, 4, checks=[{"name": "CI", "status": "in_progress"}])
    add_pr(orch, 95, 5, mergeable=None, state="unknown")
    add_pr(orch, 96, 6, draft=True)
    add_pr(orch, 97, 7, mergeable=False, state="dirty")
    add_pr(orch, 138, branch="chore/lodyorch-plafond-50")
    rows = inventory(orch)
    kinds = {n: r["kind"] for n, r in rows.items()}
    assert kinds == {91: "conflict", 92: "ci_failed", 93: "ready", 94: "ci_pending", 95: "computing",
                     96: "draft", 97: "human", 138: "human"}
    assert "hors convention" in rows[138]["reason"] and "PR #138" in rows[138]["action"]
    assert "#7 BLOCKED" in rows[97]["reason"] and "ticket #7" in rows[97]["action"]
    assert rows[93]["action"].startswith("fusionner la PR #93")
    # Une PR fermée entre deux inventaires disparaît de la file.
    data = orch.gh()
    data["pulls"]["93"]["state"] = "closed"
    orch.set_gh(data)
    assert 93 not in inventory(orch)
    assert orch.con.execute("SELECT COUNT(*) c FROM pr_queue").fetchone()["c"] == 7


def test_ready_pr_from_before_ci_watch_is_submitted_to_merge_policy(orch):
    deliver(orch, 3)
    orch.mod.update_task(orch.con, 3, ci_state="")
    add_pr(orch, 93, 3)
    inventory(orch)
    t = orch.mod.task(orch.con, 3)
    assert t["ci_state"] == "success" and t["merge_checked_at"] == ""


def test_conflicting_pr_becomes_an_integration_session_on_the_same_branch(orch):
    deliver(orch, 1, 2, priority="priority:P2")
    orch.issue(3, [AUTO, "priority:P1"])
    orch.cycle()
    add_pr(orch, 91, 1, mergeable=False, state="dirty")
    add_pr(orch, 92, 2, mergeable=False, state="dirty", files=DOCS)
    add_pr(orch, 93, 3, mergeable=False, state="dirty", files=DOCS)
    inventory(orch)
    # Priorité du ticket d'abord (P1 #3), puis code avant documentation (#1 avant #2).
    assert [r["pr"] for _, r in orch.mod.integration_candidates(orch.con)] == [93, 91, 92]
    t, row = orch.mod.next_integration(orch.con)
    prompt = orch.mod.build_prompt(orch.con, t, "sid", "/tmp/x.json", None, row)
    assert "Mode INTÉGRATION" in prompt and "PR #93" in prompt and "agent/3-fictif" in prompt
    assert "git merge origin/main" in prompt and "{{MODE}}" not in prompt and "pull/93" in prompt

    assert turn(orch) == (3, 93)
    s = orch.con.execute("SELECT mode FROM sessions WHERE issue=3 ORDER BY started_at DESC").fetchone()
    assert s["mode"] == "integrate"
    t = orch.mod.task(orch.con, 3)
    assert t["status"] == "WAITING_OWNER" and t["ci_state"] == "pending" and "PR #93" in t["note"]
    q = orch.con.execute("SELECT * FROM pr_queue WHERE pr=93").fetchone()
    assert q["repairs"] == 1 and q["repair_key"] == orch.mod.integration_key(q)
    # Même état (tête, main) : jamais retentée ; les autres PR passent.
    assert turn(orch) == (1, 91)
    assert turn(orch) == (2, 92)
    assert turn(orch) is None


def test_main_moving_allows_a_new_attempt_then_human_after_the_limit(orch):
    deliver(orch, 1)
    add_pr(orch, 91, 1, mergeable=False, state="dirty")
    inventory(orch)
    assert turn(orch) == (1, 91)
    assert turn(orch) is None
    set_main(orch, "d" * 40)  # main a avancé : nouvel état, nouvel essai
    orch.mod.ctl_set(orch.con, "pr_inventory_requested", "1")
    assert turn(orch) == (1, 91)
    set_main(orch, "e" * 40)
    rows = inventory(orch)
    assert rows[91]["kind"] == "human" and "2 tentative(s)" in rows[91]["reason"]
    assert "résoudre à la main la PR #91" in rows[91]["action"]
    assert orch.mod.next_integration(orch.con) is None


def test_successful_repair_resets_the_attempt_counter(orch):
    deliver(orch, 1)
    add_pr(orch, 91, 1, mergeable=False, state="dirty")
    inventory(orch)
    assert turn(orch) == (1, 91)
    # La session a poussé une nouvelle tête, verte et sans conflit.
    add_pr(orch, 91, 1, sha="f" * 40)
    rows = inventory(orch)
    assert rows[91]["kind"] == "ready" and rows[91]["repairs"] == 0


def test_daily_integration_cap_and_disable(orch):
    deliver(orch, 1, 2)
    add_pr(orch, 91, 1, mergeable=False, state="dirty")
    add_pr(orch, 92, 2, mergeable=False, state="dirty")
    orch.mod.ctl_set(orch.con, "cfg.max_integration_sessions_per_day", "1")
    inventory(orch)
    assert turn(orch) == (1, 91)
    assert orch.mod.next_integration(orch.con) is None
    iv = orch.mod.integration_overview(orch.con)
    assert "plafonnées (1/1)" in orch.mod.integration_action(iv)
    orch.mod.ctl_set(orch.con, "cfg.max_integration_sessions_per_day", "0")
    assert orch.mod.next_integration(orch.con) is None
    assert "intégration désactivée" in orch.mod.overview_text(orch.mod.queue_overview(orch.con))


def test_new_ticket_goes_first_and_tickets_in_queue_are_not_integrated(orch):
    deliver(orch, 1)
    add_pr(orch, 91, 1, mergeable=False, state="dirty")
    orch.issue(5, [AUTO, "priority:P3"])
    orch.sync()
    rows = inventory(orch)
    assert rows[91]["kind"] == "conflict"
    assert orch.next_issue() == 5  # un ticket neuf exécutable passe avant l'intégration
    orch.mod.update_task(orch.con, 1, status="READY")
    assert inventory(orch)[91]["kind"] == "in_queue"


def test_github_offline_keeps_previous_inventory_and_throttles(orch):
    deliver(orch, 1)
    add_pr(orch, 91, 1, mergeable=False, state="dirty")
    assert inventory(orch)
    assert orch.mod.pr_inventory(orch.con) is None  # espacé : pas de nouveau passage
    data = orch.gh()
    data["offline"] = True
    orch.set_gh(data)
    assert orch.mod.pr_inventory(orch.con, force=True) is None
    assert orch.con.execute("SELECT kind FROM pr_queue WHERE pr=91").fetchone()["kind"] == "conflict"


def test_idle_mail_counts_prs_conflicts_and_real_blockers(orch, monkeypatch):
    mod = orch.mod
    monkeypatch.setattr(mod, "NOTIFY_SMTP_ENV", "/nonexistent/smtp.env")
    deliver(orch, 1, 2)
    add_pr(orch, 91, 1, mergeable=False, state="dirty")
    add_pr(orch, 92, 2)
    add_pr(orch, 138, branch="chore/lodyorch-plafond-50")
    inventory(orch)

    def mails():
        return orch.con.execute("SELECT subject, body FROM notifications WHERE kind='File à l''arrêt'").fetchall()

    # Une PR est intégrable : ce n'est pas une file à l'arrêt, aucun email.
    mod.notify_idle(orch.con, mod.queue_overview(orch.con))
    assert mails() == []
    ov = mod.queue_overview(orch.con)
    assert "prochaine intégration : PR #91" in mod.overview_text(ov)
    # Plafond atteint : email honnête, sans « aucun travail utile ».
    mod.ctl_set(orch.con, "cfg.max_integration_sessions_per_day", "0")
    mod.notify_idle(orch.con, mod.queue_overview(orch.con))
    (m,) = mails()
    assert "3 PR à traiter, 1 conflit(s), 1 blocage(s) humain(s)" in m["subject"]
    body = m["body"]
    assert "travail utile" not in body
    assert "Conflits avec main : 1" in body and "Action en cours : file d'intégration désactivée" in body
    assert "PR #138" in body and "hors convention" in body and "→ relire puis fusionner ou fermer la PR #138" in body
    assert "PR #92 : fusionner la PR #92" in body


def test_status_shows_next_integration(orch, capsys, monkeypatch):
    import os
    monkeypatch.setattr(orch.mod, "ro_run", lambda args, timeout=5: (0, "active"))
    orch.mod.ctl_set(orch.con, "supervisor_pid", os.getpid())
    deliver(orch, 1)
    add_pr(orch, 91, 1, mergeable=False, state="dirty")
    inventory(orch)
    o = orch.mod.observe(orch.con)
    assert o["integrate_next"]["pr"] == 91 and o["state"] == "AU REPOS"
    assert "intégration de la PR #91" in o["why"]
    orch.mod.cmd_status_tickets(orch.con, None)
    out = capsys.readouterr().out
    assert "File d'intégration (#228" in out and "PR #91 (#1) : conflit avec main" in out


def test_migration_is_additive_and_idempotent(orch):
    con = orch.mod.db()
    cols = {r[1] for r in con.execute("PRAGMA table_info(pr_queue)")}
    assert {"pr", "issue", "kind", "repair_key", "repairs", "head_sha", "main_sha"} <= cols
    assert orch.mod.setting(orch.con, "max_integration_sessions_per_day")[0] == 6
    con.close()
