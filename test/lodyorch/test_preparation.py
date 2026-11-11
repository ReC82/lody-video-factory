"""File de préparation et file vide (#165) : un ticket dépendant d'une PR en revue avance en
préparation quand rien n'est exécutable ; synthèse « pourquoi on attend » ; un seul email
d'impasse ; plafond dédié ; reprise après crash ; jamais de ticket bloqué ni de fusion."""

import os

AUTO = "agent:auto"


def cycle(orch, mode="deliver"):
    """Un tour du superviseur comme `supervise()` : exécutable d'abord, sinon préparation."""
    orch.monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    orch.sync()
    t = orch.mod.next_task(orch.con)
    prepare = None
    if t is None:
        found = orch.mod.next_preparation(orch.con)
        if found:
            t, prepare = found
    if t is None:
        return None, None
    orch.mod.run_session(orch.con, t, prepare)
    return t["issue"], prepare


def deliver_reviews(orch, numbers):
    for n in numbers:
        orch.issue(n, [AUTO, "priority:P1"])
    for _ in numbers:
        cycle(orch)
    assert all(orch.status(n) == "WAITING_OWNER" for n in numbers)


def test_ten_prs_in_review_do_not_hide_an_available_ticket(orch):
    deliver_reviews(orch, range(1, 11))
    orch.issue(50, [AUTO, "priority:P2"])
    orch.sync()
    assert orch.next_issue() == 50
    ov = orch.mod.queue_overview(orch.con)
    assert ov["executable"] == [50] and len(ov["in_review"]) == 10
    text = orch.mod.overview_text(ov)
    assert "1 ticket(s) exécutable(s) : #50" in text and "10 PR en revue" in text


def test_ticket_depending_on_a_pr_in_review_is_prepared_once_then_resumed_after_merge(orch):
    deliver_reviews(orch, [11])
    orch.issue(10, [AUTO, "priority:P0"], body="Depends on #11")
    orch.sync()
    assert orch.next_issue() is None  # LQ_DEPS_DONE_STATES=DONE : la PR en revue ne débloque rien
    t, missing = orch.mod.next_preparation(orch.con)
    assert (t["issue"], missing) == (10, [11])
    prompt = orch.mod.build_prompt(orch.con, t, "sid", "/tmp/x.json", missing)
    assert "Mode PRÉPARATION" in prompt and "#11" in prompt and "--draft" in prompt
    assert "{{MODE}}" not in prompt
    assert "{{MODE}}" not in orch.mod.build_prompt(orch.con, t, "sid", "/tmp/x.json")

    assert cycle(orch) == (10, [11])
    t = orch.mod.task(orch.con, 10)
    # PR brouillon : le ticket ne part pas en revue, il attend la fusion de sa dépendance.
    assert t["status"] == "READY" and t["deps"] == "11" and t["prep_key"] == "11:911"
    assert t["ci_state"] == "" and "#11" in t["next_action"]
    mode = orch.con.execute("SELECT mode FROM sessions WHERE issue=10").fetchone()["mode"]
    assert mode == "prepare"
    # Aucune deuxième préparation pour le même état : file réellement à l'arrêt.
    assert cycle(orch) == (None, None)
    ov = orch.mod.queue_overview(orch.con)
    assert ov["prepared"] == [10] and ov["preparable"] == []
    # Fusion de la dépendance (ticket fermé) : reprise complète, en mode normal.
    orch.close(11)
    assert cycle(orch) == (10, None)
    assert orch.status(10) == "WAITING_OWNER"


def test_blocked_or_suspended_tickets_are_never_prepared(orch):
    deliver_reviews(orch, [11])
    orch.issue(20, [AUTO, "priority:P0", "agent:blocked"], body="Depends on #11")
    orch.issue(21, [AUTO, "priority:P0", "agent:human"], body="Depends on #11")
    orch.sync()
    assert orch.mod.next_preparation(orch.con) is None
    assert cycle(orch) == (None, None)


def test_dependency_not_in_review_is_not_a_preparation(orch):
    orch.issue(11, [AUTO, "priority:P3", "agent:blocked"])
    orch.issue(10, [AUTO, "priority:P0"], body="Depends on #11")
    orch.sync()
    assert orch.mod.next_preparation(orch.con) is None
    # Dépendance éligible et traitable : elle passe d'abord, pas de préparation.
    orch.edit_labels(11, remove=["agent:blocked"])
    orch.sync()
    assert cycle(orch) == (11, None)


def test_daily_preparation_cap_and_disable(orch):
    deliver_reviews(orch, [11])
    orch.issue(10, [AUTO, "priority:P1"], body="Depends on #11")
    orch.issue(12, [AUTO, "priority:P2"], body="Depends on #11")
    orch.mod.ctl_set(orch.con, "cfg.max_prep_sessions_per_day", "1")
    assert cycle(orch) == (10, [11])
    assert orch.mod.next_preparation(orch.con) is None  # plafond du jour atteint
    text = orch.mod.overview_text(orch.mod.queue_overview(orch.con))
    assert "plafond de préparation atteint (1/1)" in text
    orch.mod.ctl_set(orch.con, "cfg.max_prep_sessions_per_day", "0")
    assert orch.mod.next_preparation(orch.con) is None
    assert "file de préparation désactivée" in orch.mod.overview_text(orch.mod.queue_overview(orch.con))
    orch.mod.ctl_set(orch.con, "cfg.max_prep_sessions_per_day", "3")
    assert cycle(orch) == (12, [11])


def test_crash_during_preparation_does_not_mark_it_done_and_resumes(orch):
    deliver_reviews(orch, [11])
    orch.issue(10, [AUTO, "priority:P1"], body="Depends on #11")
    assert cycle(orch, mode="crash") == (10, [11])
    t = orch.mod.task(orch.con, 10)
    assert t["prep_key"] == "" and t["not_before"]  # délai de reprise, pas de préparation enregistrée
    orch.mod.update_task(orch.con, 10, not_before="")
    # Superviseur tué pendant une préparation : la session orpheline est close, le ticket repris.
    orch.mod.update_task(orch.con, 10, status="RUNNING")
    orch.con.execute("INSERT INTO sessions(id,issue,started_at,pid,mode) "
                     "VALUES('s-old',10,'2026-11-11T21:00:00Z',999999,'prepare')")
    orch.con.commit()
    orch.mod.recover_interrupted(orch.con)
    assert cycle(orch) == (10, [11])
    assert orch.mod.task(orch.con, 10)["prep_key"] == "11:911"


def test_new_review_state_allows_a_new_preparation(orch):
    deliver_reviews(orch, [11, 12])
    orch.issue(10, [AUTO, "priority:P1"], body="Depends on #11")
    assert cycle(orch) == (10, [11])
    # Nouvelle dépendance déclarée, elle aussi en revue : l'état change, une préparation est permise.
    orch.issue(10, [AUTO, "priority:P1"], body="Depends on #11 Depends on #12")
    orch.sync()
    t, missing = orch.mod.next_preparation(orch.con)
    assert (t["issue"], missing) == (10, [11, 12])


def test_idle_email_once_per_state_and_never_when_work_remains(orch, monkeypatch):
    mod = orch.mod
    monkeypatch.setattr(mod, "NOTIFY_SMTP_ENV", "/nonexistent/smtp.env")
    monkeypatch.setattr(mod, "flush_notifications", lambda con: None)
    deliver_reviews(orch, [1, 2, 3])

    def mails():
        return orch.con.execute("SELECT key, subject, body FROM notifications WHERE kind='File à l''arrêt'").fetchall()

    before = len(orch.con.execute("SELECT * FROM notifications").fetchall())
    mod.notify_idle(orch.con, mod.queue_overview(orch.con))
    mod.notify_idle(orch.con, mod.queue_overview(orch.con))
    assert len(mails()) == 1
    assert "3 PR en revue" in mails()[0]["subject"]
    assert "pull/91" in mails()[0]["body"]
    # Un autre état dans les 12 h : pas de deuxième email.
    orch.issue(4, [AUTO, "agent:blocked"])
    orch.sync()
    mod.notify_idle(orch.con, mod.queue_overview(orch.con))
    assert len(mails()) == 1
    # Du travail exécutable ou préparable : jamais d'email d'impasse.
    mod.ctl_set(orch.con, "idle_notice_at", "0")
    orch.issue(5, [AUTO], body="Depends on #1")
    orch.sync()
    mod.notify_idle(orch.con, mod.queue_overview(orch.con))
    orch.issue(6, [AUTO])
    orch.sync()
    mod.notify_idle(orch.con, mod.queue_overview(orch.con))
    assert len(mails()) == 1
    assert len(orch.con.execute("SELECT * FROM notifications").fetchall()) == before + 1


def test_status_explains_why_it_waits(orch, capsys, monkeypatch):
    # Superviseur « vivant » (notre propre pid) et service actif, sans systemctl réel.
    monkeypatch.setattr(orch.mod, "ro_run", lambda args, timeout=5: (0, "active"))
    orch.mod.ctl_set(orch.con, "supervisor_pid", os.getpid())
    deliver_reviews(orch, [1, 2])
    orch.issue(9, [AUTO, "agent:blocked"])
    orch.issue(10, [AUTO], body="Depends on #1")
    orch.sync()
    o = orch.mod.observe(orch.con)
    assert o["prepare_next"]["issue"] == 10
    assert "prochain à préparer : #10" in o["why"] and "2 PR en revue" in o["why"]
    orch.mod.update_task(orch.con, 10, prep_key=orch.mod.prep_key(orch.con, [1]))
    o = orch.mod.observe(orch.con)
    assert o["state"] == "BLOQUÉ" and o["prepare_next"] is None
    assert "1 bloqué(s)" in o["why"] and "#10→#1" in o["why"]
    orch.mod.cmd_status(orch.con, None)
    assert "File : aucun ticket exécutable" in capsys.readouterr().out


def test_instance_isolation_and_migration_are_idempotent(orch):
    assert orch.mod.REPO == "ReC82/lody-video-factory"
    assert str(orch.mod.DB_PATH).startswith(str(orch.tmp))
    assert "dig-orch" not in str(orch.mod.STATE_DIR)
    assert orch.mod.setting(orch.con, "max_prep_sessions_per_day") == (3, "env LQ_MAX_PREP_SESSIONS_PER_DAY")
    con = orch.mod.db()  # deuxième ouverture : migration additive sans erreur
    cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
    assert "mode" in cols
    assert "prep_key" in {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
    con.close()
