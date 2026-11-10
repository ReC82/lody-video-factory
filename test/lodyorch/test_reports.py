"""Rapport permanent par tentative (#123) : rédigé par le superviseur, archivé, publié une fois en
commentaire du ticket, envoyé par email (faux SMTP) ; pannes SMTP et GitHub sans perte ni doublon."""

import json

AUTO = "agent:auto"
MAIN = {"ref": "main", "repo": {"default_branch": "main"}}


def reports(orch):
    return orch.con.execute("SELECT * FROM run_reports ORDER BY created_at").fetchall()


def report_comments(orch, n):
    return [c for c in orch.gh().get("comments", [])
            if c["issue"] == n and "<!-- lodyorch:rapport run=" in c["body"]]


def pr(orch, number, sha, **extra):
    data = orch.gh()
    data.setdefault("pulls", {})[str(number)] = dict(number=number, state="open", merged=False,
                                                     head={"sha": sha, "ref": f"agent/{number}"}, base=MAIN, **extra)
    orch.set_gh(data)


def ci(orch, sha, conclusion):
    data = orch.gh()
    status = "completed" if conclusion else "in_progress"
    data.setdefault("checks", {})[sha] = [dict(name="tests", status=status, conclusion=conclusion, html_url="https://ci/t")]
    orch.set_gh(data)


def poll(orch):
    orch.con.execute("UPDATE tasks SET ci_checked_at=''")
    orch.con.commit()
    orch.mod.watch_ci(orch.con)


class FakeSMTP:
    """Faux serveur SMTPS : messages reçus en mémoire ; `fail` simule une panne."""
    sent = []
    fail = False

    def __init__(self, host, port, context=None, timeout=None):
        if FakeSMTP.fail:
            raise OSError("connexion refusée par smtp.example.invalid (mot de passe s3cr3t-NE-PAS-AFFICHER)")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)
        return {}


def smtp(orch, monkeypatch, tmp_path):
    env = tmp_path / "smtp.env"
    env.write_text("\n".join(f"PLUGADMIN_SMTP_{k}={v}" for k, v in dict(
        HOST="smtp.example.invalid", PORT="465", USERNAME="robot", PASSWORD="s3cr3t-NE-PAS-AFFICHER",
        FROM="robot@example.invalid", TO="owner@example.invalid").items()), encoding="utf-8")
    monkeypatch.setattr(orch.mod, "NOTIFY_SMTP_ENV", str(env))
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(orch.mod.smtplib, "SMTP_SSL", FakeSMTP)
    return env


def test_delivery_report_waits_for_ci_then_is_published_once_and_mailed(orch, monkeypatch, tmp_path):
    smtp(orch, monkeypatch, tmp_path)
    orch.issue(1, [AUTO, "priority:P1"], title="Index de la doc")
    monkeypatch.setenv("FAKE_CLAUDE_EXTRA", json.dumps({
        "human_tests": ["Ouvrir docs/README.md sur GitHub", "Cliquer chaque lien"], "risks": "aucun identifié",
        "remaining": "rien"}))
    assert orch.cycle() == 1
    (r,) = reports(orch)
    assert r["state"] == "awaiting_ci" and not report_comments(orch, 1) and not FakeSMTP.sent
    pr(orch, 91, "s1", files=[{"filename": "docs/README.md", "status": "added", "additions": 39, "deletions": 0}],
       commits=[{"sha": "abc1234def", "html_url": "https://github.com/x/commit/abc1234def",
                 "commit": {"message": "docs: index (#1)\n\ncorps"}}])
    ci(orch, "s1", "success")
    poll(orch)
    (r,) = reports(orch)
    assert r["state"] == "ready" and r["comment_url"].endswith("#issuecomment-" + r["comment_url"].rsplit("-", 1)[1])
    name = r["file"].rsplit("/", 1)[1]
    assert name.endswith(f"_lody_issue-1_run-{r['run_id']}.md") and name[:10].count("-") == 2
    (c,) = report_comments(orch, 1)
    body = c["body"]
    assert body.startswith("<!-- lodyorch -->")  # jamais pris pour une réponse du propriétaire
    for part in ("# Rapport lodyorch — Lody Video Factory — #1", "**à tester — CI verte**", "## 1. Résumé",
                 "## 2. Branche, commits, PR, CI et tests", "CI validée", "`docs/README.md` (added, +39 −0)",
                 "docs: index (#1)", "## 4. Risques, coûts et décisions", "aucun identifié",
                 "1. Ouvrir docs/README.md sur GitHub", "## 6. État final", "## 7. Identification",
                 "Rien n'est fusionné ni déployé"):
        assert part in body, part
    orch.mod.flush_notifications(orch.con)
    (msg,) = FakeSMTP.sent
    assert msg["Subject"] == "[Lody Video Factory] #1 — à tester — Index de la doc"
    assert msg["To"] == "owner@example.invalid"
    assert r["comment_url"] in msg.get_body(("plain",)).get_content()
    (att,) = list(msg.iter_attachments())
    assert att.get_filename() == name and "## 6. État final" in att.get_content()
    # Idempotence : nouveaux tours, nouvelle demande explicite → ni second commentaire ni second email.
    orch.mod.flush_reports(orch.con)
    orch.mod.finalize_run(orch.con, r["run_id"])
    orch.mod.record_run(orch.con, 1, r["session_id"], "COMPLETED", "", {})
    orch.mod.flush_notifications(orch.con)
    assert len(report_comments(orch, 1)) == 1 and len(FakeSMTP.sent) == 1 and len(reports(orch)) == 1


def test_blocked_and_crashed_attempts_are_reported_immediately(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.issue(2, [AUTO, "priority:P1"])
    assert orch.cycle("blocked") == 1
    assert orch.cycle("crash") == 2
    by_issue = {r["issue"]: r for r in reports(orch)}
    assert {r["state"] for r in by_issue.values()} == {"ready"}
    (b,) = report_comments(orch, 1)
    assert "**bloqué**" in b["body"] and "Question 1 : Q1 ?" in b["body"] and "Blocage : décision nécessaire" in b["body"]
    (c,) = report_comments(orch, 2)
    assert "**échec — tentative sans livraison, nouvelle tentative automatique**" in c["body"]
    assert "Fin de session | CRASH" in c["body"]


def test_ci_failure_reports_the_attempt_then_the_fix_gets_its_own_report(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle() == 1
    pr(orch, 91, "s1")
    ci(orch, "s1", "failure")
    poll(orch)
    (first,) = report_comments(orch, 1)
    assert "échec — CI rouge, correction relancée automatiquement" in first["body"]
    assert orch.cycle() == 1  # session de correction : nouveau run, nouveau rapport après la CI
    ci(orch, "s1", "success")
    poll(orch)
    runs = reports(orch)
    assert len(runs) == 2 and runs[0]["run_id"] != runs[1]["run_id"]
    assert len(report_comments(orch, 1)) == 2


def test_github_outage_keeps_the_report_and_publishes_later_without_duplicate(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    data = orch.gh()
    data["comments_post_offline"] = True
    orch.set_gh(data)
    assert orch.cycle("blocked") == 1
    (r,) = reports(orch)
    assert r["state"] == "ready" and not r["comment_url"] and r["comment_attempts"] == 1
    assert "## 6. État final" in open(r["file"], encoding="utf-8").read()  # archivé localement
    data = orch.gh()
    data["comments_post_offline"] = False
    orch.set_gh(data)
    orch.mod.flush_reports(orch.con)  # délai de nouvel essai pas écoulé : rien
    assert not report_comments(orch, 1)
    orch.con.execute("UPDATE run_reports SET comment_next=''")
    orch.con.commit()
    orch.mod.flush_reports(orch.con)
    assert len(report_comments(orch, 1)) == 1
    # Crash entre la publication et l'enregistrement : le marqueur est retrouvé, pas de doublon.
    orch.con.execute("UPDATE run_reports SET comment_url=''")
    orch.con.commit()
    orch.mod.flush_reports(orch.con)
    assert len(report_comments(orch, 1)) == 1
    assert orch.mod.publish_report(orch.con, r["run_id"]).startswith("https://github.com/")


def test_smtp_outage_retries_without_redoing_work_and_never_leaks_credentials(orch, monkeypatch, tmp_path):
    smtp(orch, monkeypatch, tmp_path)
    FakeSMTP.fail = True
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle("blocked") == 1
    nf = orch.con.execute("SELECT * FROM notifications").fetchone()
    assert nf["sent_at"] is None and nf["attempts"] == 1 and nf["last_error"] == "OSError"
    events = " ".join(e["message"] for e in orch.con.execute("SELECT message FROM events").fetchall())
    assert "s3cr3t" not in events and "smtp.example.invalid" not in events
    FakeSMTP.fail = False
    orch.con.execute("UPDATE notifications SET next_try=''")
    orch.con.commit()
    orch.mod.flush_notifications(orch.con)
    assert len(FakeSMTP.sent) == 1
    assert len(orch.claude_calls()) == 1  # le travail du ticket n'est pas refait


def test_supervisor_without_smtp_access_only_queues(orch, monkeypatch, tmp_path):
    """Sous systemd, le fichier SMTP est invisible pour le superviseur : rien n'est tenté ni compté ;
    l'unité lodyorch-notify (notifications --send) envoie."""
    env = smtp(orch, monkeypatch, tmp_path)
    env.chmod(0)
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle("blocked") == 1
    nf = orch.con.execute("SELECT * FROM notifications").fetchone()
    assert nf["attempts"] == 0 and not FakeSMTP.sent
    env.chmod(0o600)
    args = type("A", (), {"send": True, "retry": False})()
    assert orch.mod.cmd_notifications(orch.con, args) == 0
    assert len(FakeSMTP.sent) == 1


def test_secrets_are_masked_and_only_reports_dir_files_are_attached(orch, monkeypatch, tmp_path):
    secret = "ghp_" + "B" * 36
    outside = tmp_path / "credentials.json"
    outside.write_text('{"token": "x"}', encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_EXTRA", json.dumps({"summary": f"jeton {secret} trouvé",
                                                        "report_file": str(outside)}))
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle("blocked") == 1
    (c,) = report_comments(orch, 1)
    assert secret not in c["body"] and "jeton ***" in c["body"]
    assert "ghp_" not in c["body"] and "credentials" not in c["body"] and "Annexe" not in c["body"]
    inside = orch.mod.STATE_DIR / "reports" / "ticket-2.md"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("## Décisions\nDétail de la session.", encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_EXTRA", json.dumps({"report_file": str(inside)}))
    orch.issue(2, [AUTO, "priority:P1"])
    assert orch.cycle("blocked") == 2
    (c2,) = report_comments(orch, 2)
    assert "## Annexe — rapport détaillé de la session" in c2["body"] and "Détail de la session." in c2["body"]


def test_voluntary_stops_are_not_attempts_but_interruptions_are(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.sync()
    for outcome in ("STOPPED", "RATE_LIMIT"):
        orch.mod.record_run(orch.con, 1, f"{outcome.lower():0<8}-x", outcome, "", {})
    assert not reports(orch)
    orch.con.execute("INSERT INTO sessions(id, issue, started_at, pid) VALUES('dead0001-s', 1, '2026-11-10T00:00:00Z', 0)")
    orch.con.commit()
    orch.mod.recover_interrupted(orch.con)
    (r,) = reports(orch)
    assert r["outcome"] == "INTERRUPTED"
    (c,) = report_comments(orch, 1)
    assert "Fin de session | INTERRUPTED" in c["body"]


def test_long_report_is_truncated_for_github_but_kept_whole_in_the_archive(orch, monkeypatch):
    inside = orch.mod.STATE_DIR / "reports" / "ticket-1.md"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("ligne de rapport très détaillée\n" * 4000, encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_EXTRA", json.dumps({"report_file": str(inside)}))
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle("blocked") == 1
    (c,) = report_comments(orch, 1)
    assert len(c["body"]) <= orch.mod.REPORT_COMMENT_MAX and "Rapport tronqué ici pour GitHub" in c["body"]
    (r,) = reports(orch)
    assert len(open(r["file"], encoding="utf-8").read()) > orch.mod.REPORT_COMMENT_MAX


def test_report_command_backfills_a_session_finished_before_reports(orch, monkeypatch, capsys):
    monkeypatch.setattr(orch.mod, "RUN_REPORTS", False)
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle("blocked") == 1
    assert not reports(orch)
    monkeypatch.setattr(orch.mod, "RUN_REPORTS", True)
    args = type("A", (), {"issue": 1})()
    assert orch.mod.cmd_report(orch.con, args) == 0
    assert "#issuecomment-" in capsys.readouterr().out
    assert orch.mod.cmd_report(orch.con, args) == 0
    assert len(report_comments(orch, 1)) == 1
    assert orch.mod.cmd_reports(orch.con, None) == 0
