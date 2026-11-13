"""Cycle de vie des tickets et paire d'emails DÉMARRAGE / FIN (#215) : démarrage réel déclaré par la
session (`lodyorch phase --plan`), jalons horodatés idempotents, EN ATTENTE CI quand Claude rend la
main, INTERROMPU quand une paire resterait ouverte, durées distinctes (session / CI / file) dans le
rapport #123 et `lodyorch metrics`. Faux SMTP, faux Claude, faux GitHub : aucun réseau."""

import json
from argparse import Namespace

from .test_reports import FakeSMTP, ci, poll, pr, reports, smtp

AUTO = "agent:auto"
PLAN = ["Ajouter un retour immédiat au projet", "Simplifier la liste des personnages"]


def mails(orch):
    return orch.con.execute("SELECT * FROM notifications ORDER BY created_at, rowid").fetchall()


def kinds(orch):
    return [m["key"].split(":", 1)[0] for m in mails(orch)]


def marks(orch, n, run):
    return {r["kind"]: r["at"] for r in orch.con.execute("SELECT * FROM lifecycle WHERE issue=? AND run_id=?",
                                                         (n, run)).fetchall()}


def sessions(orch, n):
    return orch.con.execute("SELECT * FROM sessions WHERE issue=? ORDER BY started_at, rowid", (n,)).fetchall()


def test_normal_run_sends_one_start_mail_then_ci_transition_then_end_with_same_run(orch, monkeypatch, tmp_path):
    smtp(orch, monkeypatch, tmp_path)
    orch.issue(212, [AUTO, "priority:P1"], title="[P1][UX mobile][Lot] Retour rapide au projet")
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps(PLAN))
    assert orch.cycle() == 212
    (s,) = sessions(orch, 212)
    run = s["id"][:8]
    assert kinds(orch) == ["start", "wait"]
    start, wait = mails(orch)
    assert start["subject"] == f"[Lody Video Factory] DÉMARRAGE #212 — Retour rapide au projet · run {run}"
    body = start["body"]
    for part in ("Catégorie : UX mobile · Priorité P1", "- Ajouter un retour immédiat au projet",
                 "- Simplifier la liste des personnages", "(Bruxelles)", " UTC",
                 "https://github.com/ReC82/lody-video-factory/issues/212", f"Run : {run}", "tentative n° 1"):
        assert part in body, part
    assert wait["subject"].startswith("[Lody Video Factory] EN ATTENTE CI #212 — PR ouverte, CI en cours")
    assert wait["subject"].endswith(f"run {run}") and "Claude ne travaille pas pendant ce temps" in wait["body"]
    m = marks(orch, 212, run)
    assert {"queued", "launched", "started", "plan", "ended", "ci_started"} <= set(m)
    assert m["queued"] <= m["launched"] <= m["started"] <= m["plan"] <= m["ended"] <= m["ci_started"]
    # Verdict de CI : rapport FIN apparié (même run), durée de CI séparée du temps de session.
    pr(orch, 9212, "s1", created_at="2026-11-13T10:00:00Z")
    ci(orch, "s1", "success")
    poll(orch)
    assert kinds(orch) == ["start", "wait", "run"]
    fin = mails(orch)[-1]
    assert fin["subject"] == f"[Lody Video Factory] FIN #212 — à tester — Retour rapide au projet · run {run}"
    assert "Temps de session Claude" in fin["body"] and "Attente de CI après la session" in fin["body"]
    assert "ci_ended" in marks(orch, 212, run)
    assert orch.con.execute("SELECT at FROM lifecycle WHERE issue=212 AND run_id='' AND kind='pr_opened'"
                            ).fetchone()[0] == "2026-11-13T10:00:00Z"
    (r,) = reports(orch)
    md = open(r["file"], encoding="utf-8").read()
    assert "## Début, fin et durées" in md and "Plan annoncé : " in md and "mail DÉMARRAGE : envoyé" in md
    # Idempotence : redémarrage, nouveaux tours, envoi → ni doublon ni nouveau jalon.
    orch.mod.lc_tick(orch.con, check_lost=True)
    orch.mod.lc_tick(orch.con)
    orch.mod.flush_notifications(orch.con)
    assert len(FakeSMTP.sent) == 3 and kinds(orch) == ["start", "wait", "run"]
    assert [x["Subject"].split(" #")[0].rsplit(" ", 1)[-1] for x in FakeSMTP.sent][0] == "DÉMARRAGE"


def test_claim_without_real_work_sends_no_start_mail(orch, monkeypatch, tmp_path):
    """Ticket rendu bloqué tout de suite, sans plan : aucun DÉMARRAGE, le rapport le dit."""
    smtp(orch, monkeypatch, tmp_path)
    orch.issue(5, [AUTO, "priority:P2"], title="Sans étiquette métier")
    assert orch.cycle("blocked") == 5
    assert kinds(orch) == ["run"]
    assert "DÉMARRAGE" not in mails(orch)[0]["subject"] and "FIN #5 — bloqué" in mails(orch)[0]["subject"]
    assert "mail DÉMARRAGE : non envoyé" in mails(orch)[0]["body"]
    assert orch.mod.ticket_category("Sans étiquette métier", [AUTO, "priority:P2"]) == "non classé"


def test_crash_after_plan_keeps_the_pair_start_then_end(orch, monkeypatch, tmp_path):
    smtp(orch, monkeypatch, tmp_path)
    orch.issue(7, [AUTO, "priority:P0"], title="[P0][Lodyorch] Mail au démarrage")
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps(["Instrumenter le cycle de vie"]))
    assert orch.cycle("crash") == 7
    run = sessions(orch, 7)[0]["id"][:8]
    assert kinds(orch) == ["start", "run"]
    assert "Catégorie : Orchestrateur · Priorité P0" in mails(orch)[0]["body"]
    assert mails(orch)[1]["subject"].startswith("[Lody Video Factory] FIN #7 — échec") and run in mails(orch)[1]["subject"]


def test_two_attempts_have_two_distinct_pairs(orch, monkeypatch, tmp_path):
    smtp(orch, monkeypatch, tmp_path)
    orch.issue(8, [AUTO, "priority:P1"], title="[CI] Tests ciblés")
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps(["Corriger"]))
    assert orch.cycle("crash") == 8
    orch.con.execute("UPDATE tasks SET not_before='' WHERE issue=8")
    orch.con.commit()
    assert orch.cycle("blocked") == 8
    s1, s2 = sessions(orch, 8)
    starts = [m for m in mails(orch) if m["key"].startswith("start:")]
    assert [m["key"] for m in starts] == [f"start:{s1['id'][:8]}", f"start:{s2['id'][:8]}"]
    assert "tentative n° 2" in starts[1]["body"]
    # La deuxième tentative est remise en file à la fin de la première, pas à la découverte.
    m2 = marks(orch, 8, s2["id"][:8])
    assert m2["queued"] >= s1["ended_at"]
    assert {m["key"] for m in mails(orch)} >= {f"run:{s1['id'][:8]}", f"run:{s2['id'][:8]}"}


def test_provider_limit_after_start_sends_interrupted_without_report(orch, monkeypatch, tmp_path):
    smtp(orch, monkeypatch, tmp_path)
    orch.issue(9, [AUTO, "priority:P1"])
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps(["Faire quelque chose"]))
    assert orch.cycle("limit") == 9
    run = sessions(orch, 9)[0]["id"][:8]
    assert kinds(orch) == ["start", "end"] and not reports(orch)
    end = mails(orch)[1]
    assert end["subject"].startswith("[Lody Video Factory] INTERROMPU #9 — limite du fournisseur Claude atteinte")
    assert end["subject"].endswith(f"run {run}")


def test_dead_supervisor_is_detected_without_claude_and_report_follows_restart(orch, monkeypatch, tmp_path):
    smtp(orch, monkeypatch, tmp_path)
    orch.issue(11, [AUTO, "priority:P1"], title="[Infra] Reprise")
    orch.sync()
    old = "2026-11-13T08:00:00Z"
    orch.con.execute("INSERT INTO sessions(id,issue,started_at,pid,log_path) VALUES(?,?,?,?,?)",
                     ("deadbeef-0000-0000-0000-000000000000", 11, old, 999999999, ""))
    orch.con.commit()
    orch.mod.lc_launch(orch.con, 11, "deadbeef-0000-0000-0000-000000000000")
    orch.mod.lc_mark(orch.con, 11, "deadbeef", "plan", "2026-11-13T08:05:00Z", json.dumps(["Plan"]))
    orch.mod.ctl_set(orch.con, "supervisor_pid", "999999999")
    monkeypatch.setattr(orch.mod.time, "time", lambda: orch.mod.ts_of("2026-11-13T09:00:00Z"))
    monkeypatch.setattr(orch.mod, "iso", lambda dt=None: (dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt
                                                          else "2026-11-13T09:00:00Z"))
    orch.mod.lc_tick(orch.con, check_lost=True)
    orch.mod.lc_tick(orch.con, check_lost=True)
    assert kinds(orch) == ["start", "lost"]
    assert "superviseur arrêté pendant la session" in mails(orch)[1]["subject"]
    # Redémarrage : la session orpheline devient un rapport FIN ; pas d'INTERROMPU supplémentaire.
    orch.mod.recover_interrupted(orch.con)
    orch.mod.lc_tick(orch.con, check_lost=True)
    assert kinds(orch) == ["start", "lost", "run"]


def test_start_mail_after_grace_when_session_never_declares_a_plan(orch, monkeypatch, tmp_path):
    smtp(orch, monkeypatch, tmp_path)
    orch.issue(12, [AUTO, "priority:P2"])
    orch.sync()
    orch.con.execute("INSERT INTO sessions(id,issue,started_at,log_path) VALUES(?,?,?,?)",
                     ("cafebabe-0000-0000-0000-000000000000", 12, "2026-11-13T08:00:00Z", ""))
    orch.con.commit()
    orch.mod.ctl_set(orch.con, "supervisor_pid", "")
    monkeypatch.setattr(orch.mod.time, "time", lambda: orch.mod.ts_of("2026-11-13T08:05:00Z"))
    orch.mod.lc_tick(orch.con)
    assert not mails(orch)  # 5 min < délai de grâce : pas encore
    monkeypatch.setattr(orch.mod.time, "time", lambda: orch.mod.ts_of("2026-11-13T08:20:00Z"))
    orch.mod.lc_tick(orch.con)
    (m,) = mails(orch)
    assert m["key"] == "start:cafebabe" and "Plan non annoncé par la session" in m["body"]


def test_smtp_outage_never_blocks_the_session_and_sends_each_mail_once(orch, monkeypatch, tmp_path):
    smtp(orch, monkeypatch, tmp_path)
    FakeSMTP.fail = True
    orch.issue(13, [AUTO, "priority:P1"])
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps(["Plan"]))
    assert orch.cycle("blocked") == 13 and orch.status(13) == "BLOCKED"
    assert kinds(orch) == ["start", "run"] and not FakeSMTP.sent
    assert all(m["attempts"] >= 1 and m["sent_at"] is None for m in mails(orch))
    FakeSMTP.fail = False
    orch.con.execute("UPDATE notifications SET next_try=''")
    orch.con.commit()
    orch.mod.flush_notifications(orch.con)
    orch.mod.flush_notifications(orch.con)
    assert [x["Subject"].split(" — ")[0] for x in FakeSMTP.sent] == ["[Lody Video Factory] DÉMARRAGE #13",
                                                                    "[Lody Video Factory] FIN #13"]
    assert len(orch.claude_calls()) == 1  # aucune session relancée pour un email


def test_long_ci_does_not_inflate_active_time_and_metrics_split_durations(orch, monkeypatch, tmp_path, capsys):
    smtp(orch, monkeypatch, tmp_path)
    orch.issue(14, [AUTO, "priority:P1", "type:UI/CSS"], title="Refonte CSS")
    orch.issue(15, [AUTO, "priority:P1"], title="[Produit 2D] Essais Voltix")
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", json.dumps(["Plan"]))
    assert orch.cycle() == 14
    s = sessions(orch, 14)[0]
    run = s["id"][:8]
    # CI de 3 h : comptée comme attente de CI, jamais comme temps actif.
    orch.con.execute("UPDATE lifecycle SET at='2026-11-13T09:00:00Z' WHERE run_id=? AND kind='ci_started'", (run,))
    orch.con.execute("INSERT INTO lifecycle(issue,run_id,kind,at) VALUES(14,?,'ci_ended','2026-11-13T12:00:00Z')", (run,))
    orch.con.commit()
    tm = orch.mod.lc_timing(orch.con, s)
    assert tm["ci"] == 3 * 3600 and tm["session"] < 60 and not tm["ci_running"]
    assert orch.cycle("blocked") == 15
    assert orch.mod.cmd_metrics(orch.con, Namespace(days=None, top=5)) == 0
    out = capsys.readouterr().out
    assert "UI/CSS" in out and "Produit 2D" in out and "3 h 00" in out
    assert "Total : 2 tickets, 2 runs" in out
    assert "Paires DÉMARRAGE sans FIN : aucune" in out


def test_categories_and_mail_time(orch):
    mod = orch.mod
    assert mod.ticket_category("[P1][UX mobile][Lot] X", ["priority:P1"]) == "UX mobile"
    assert mod.ticket_category("[P1][UI Design System] X", ["bug"]) == "Bug"
    assert mod.ticket_category("X", ["area:CI"]) == "CI"
    assert mod.ticket_category("[Lot][Animation 2D] X", []) == "Animation 2D"
    assert mod.mail_time("2026-11-13T15:50:00Z") == "13/11/2026 16:50 (Bruxelles) — 15:50 UTC"
    assert mod.short_title("[P0][Lodyorch] Mail au démarrage") == "Mail au démarrage"
