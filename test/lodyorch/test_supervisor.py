"""Superviseur LODYORCH (#116) : verrou d'exclusion, reprise après crash sans doublon, refus sans
authentification ni garde, journaux sans secret, état affiché par `status`."""

import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

from test.lodyorch.conftest import ORCH_DIR

AUTO = "agent:auto"


def test_session_is_launched_with_guard_settings_and_without_mcp(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle() == 1
    argv = orch.claude_calls()[0]["argv"]
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--settings") + 1] == str(ORCH_DIR / "claude-settings.json")
    assert "--strict-mcp-config" in argv
    t = orch.mod.task(orch.con, 1)
    assert (t["status"], t["pr"]) == ("WAITING_OWNER", "https://github.com/ReC82/lody-video-factory/pull/91")
    assert "status:needs-test" in orch.labels(1) and "agent:in-progress" not in orch.labels(1)


def test_one_session_at_a_time_second_supervisor_exits(orch):
    orch.mod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(orch.mod.LOCK_PATH, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert orch.mod.supervise() == 0  # rend la main tout de suite, sans session
    assert orch.claude_calls() == []
    assert orch.con.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"] == 0


def test_explicit_stop_survives_a_restart(orch):
    orch.mod.ctl_set(orch.con, "mode", "STOP")
    assert orch.mod.supervise() == 0
    msg = orch.con.execute("SELECT message FROM events ORDER BY id DESC LIMIT 1").fetchone()["message"]
    assert "Démarrage refusé" in msg


def test_crash_recovery_resumes_the_same_ticket_first_without_duplicate(orch):
    orch.issue(1, [AUTO, "priority:P2"])
    orch.issue(2, [AUTO, "priority:P0"])
    orch.sync()
    # Superviseur tué pendant une session sur #1 : ligne de session ouverte, processus disparu.
    orch.mod.update_task(orch.con, 1, status="RUNNING", attempts=1)
    orch.con.execute("INSERT INTO sessions(id,issue,started_at,pid) VALUES('s-old',1,'2026-11-10T10:00:00Z',999999)")
    orch.con.commit()
    orch.mod.recover_interrupted(orch.con)
    s = orch.con.execute("SELECT outcome, ended_at FROM sessions WHERE id='s-old'").fetchone()
    assert s["outcome"] == "INTERRUPTED" and s["ended_at"]
    assert "INTERRUPTED" in orch.mod.ctl_get(orch.con, "last_error")
    # Le ticket interrompu passe avant un P0 neuf : jamais deux tickets entamés en parallèle.
    assert orch.next_issue() == 1
    assert orch.cycle() == 1
    assert orch.status(1) == "WAITING_OWNER"
    assert orch.cycle() == 2
    assert [c["issue"] for c in orch.claude_calls()] == ["1", "2"]
    # Rejouer la synchro et la sélection ne relance rien : aucun doublon de session ni de PR.
    assert orch.cycle() is None
    assert len(orch.claude_calls()) == 2


def test_crash_without_report_backs_off_then_fails_after_max_attempts(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    for _ in range(3):
        orch.mod.update_task(orch.con, 1, not_before="")
        assert orch.cycle(mode="crash") == 1
    t = orch.mod.task(orch.con, 1)
    assert t["status"] == "FAILED" and "3 sessions sans rapport" in t["blockers"]
    assert "agent:blocked" in orch.labels(1)
    assert "CRASH" in orch.mod.ctl_get(orch.con, "last_error")


def test_crash_sets_a_delay_before_retry(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle(mode="crash") == 1
    t = orch.mod.task(orch.con, 1)
    assert t["status"] == "READY" and t["not_before"] > orch.mod.iso()
    assert orch.next_issue() is None


def test_breaker_pauses_after_consecutive_failures(orch):
    for n in range(1, 6):
        orch.issue(n, [AUTO, "priority:P1"])
    for _ in range(4):
        orch.cycle(mode="crash")
    assert orch.mod.ctl_get(orch.con, "mode") == "PAUSED"
    assert "Disjoncteur" in orch.mod.start_blocker(orch.con)[0]


def test_no_session_without_claude_authentication(orch, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", "0")
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle() is None
    assert orch.claude_calls() == []
    assert "Authentification Claude absente" in orch.mod.session_preflight()


def test_no_session_without_guard_settings(orch, monkeypatch, tmp_path):
    monkeypatch.setattr(orch.mod, "CLAUDE_SETTINGS", str(tmp_path / "absent.json"))
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle() is None
    assert "Réglages de garde illisibles" in orch.mod.session_preflight()


def test_session_logs_and_events_are_redacted(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.cycle(mode="secret")
    log = Path(orch.con.execute("SELECT log_path FROM sessions").fetchone()["log_path"]).read_text(encoding="utf-8")
    assert "ghp_" + "A" * 36 not in log and "zzzzzzzzzzzz" not in log
    assert "***" in log
    for line in log.splitlines():
        json.loads(line)  # le masquage garde un journal JSON lisible
    orch.mod.event(orch.con, 1, "test", "clé sk-ant-api03-" + "B" * 30)
    assert "BBBB" not in orch.con.execute("SELECT message FROM events ORDER BY id DESC").fetchone()["message"]


def test_state_dir_is_private(orch):
    assert oct(orch.mod.STATE_DIR.stat().st_mode & 0o777) == "0o700"


def test_phase_and_status_show_ticket_phase_pr_and_last_error(orch, monkeypatch):
    orch.issue(1, [AUTO, "priority:P0"], title="Installer lodyorch")
    orch.issue(2, [AUTO, "priority:P1"])
    orch.cycle()
    orch.cycle(mode="crash")
    env = dict(os.environ, LQ_ISSUE="2")
    cli = [sys.executable, str(ORCH_DIR / "lodyorch.py")]
    subprocess.run(cli + ["phase", "tests", "—", "pytest", "ciblé"], env=env, check=True, capture_output=True)
    assert orch.mod.ctl_get(orch.con, "phase") == "tests — pytest ciblé"
    out = subprocess.run(cli + ["status"], env=env, capture_output=True, text=True, timeout=60).stdout
    assert "LODYORCH" in out
    assert "tmux lodyorch" in out
    assert "Dernière phase : tests — pytest ciblé" in out
    assert "#1" in out and "pull/91" in out
    assert "Dernière erreur" in out and "CRASH" in out
    pulse = subprocess.run(cli + ["pulse"], env=env, capture_output=True, text=True, timeout=60).stdout
    assert "PR en attente de revue : #1→PR #91" in pulse
    assert "x@example.invalid" not in out + pulse  # rien de l'authentification n'est affiché


def test_check_reports_missing_pieces_without_secrets(orch):
    env = dict(os.environ)
    out = subprocess.run([sys.executable, str(ORCH_DIR / "lodyorch.py"), "check"], env=env,
                         capture_output=True, text=True, timeout=60)
    assert "authentification Claude Code" in out.stdout
    assert "x@example.invalid" not in out.stdout
    # Faux clone sans remote : le contrôle échoue, code 1.
    assert out.returncode == 1 and "clone de lancement dédié" in out.stdout
