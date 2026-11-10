"""Console unique LODYORCH (#116, décision du 10/11/2026) : la session tmux `lodyorch` est la
session Claude interactive du propriétaire ; la file travaille en arrière-plan et lui cède la main."""

import json
import os
import subprocess
import sys
import time

from test.lodyorch.conftest import ORCH_DIR

sys.path.insert(0, str(ORCH_DIR))
import console_settings  # noqa: E402

AUTO = "agent:auto"
WANTED = json.loads((ORCH_DIR / "console-settings.json").read_text(encoding="utf-8"))


# -- réglages de la console -------------------------------------------------------------------

def test_console_hooks_are_added_without_replacing_existing_settings():
    current = {"permissions": {"allow": ["Bash(ls)"]}, "statusLine": {"type": "command", "command": "mine"},
               "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send fini"}]}]}}
    merged, changes = console_settings.merge(current, WANTED)
    assert merged["permissions"] == current["permissions"]
    assert merged["statusLine"]["command"] == "mine"  # la ligne d'état du propriétaire est conservée
    stop = [h["command"] for g in merged["hooks"]["Stop"] for h in g["hooks"]]
    assert stop[0] == "notify-send fini" and "lodyorch human idle" in stop[1]
    assert "lodyorch human busy" in merged["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    again, changes2 = console_settings.merge(merged, WANTED)
    assert again == merged and all("conservée" in c for c in changes2)  # idempotent


def test_console_settings_script_writes_local_file_and_excludes_it_from_git(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    out = subprocess.run([sys.executable, str(ORCH_DIR / "console_settings.py"), str(repo),
                          str(ORCH_DIR / "console-settings.json")], capture_output=True, text=True, check=True)
    data = json.loads((repo / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert data["statusLine"]["command"].endswith("lodyorch statusline 2>/dev/null")
    assert "/.claude/settings.local.json" in (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True,
                          text=True).stdout == ""
    second = subprocess.run([sys.executable, str(ORCH_DIR / "console_settings.py"), str(repo),
                             str(ORCH_DIR / "console-settings.json")], capture_output=True, text=True, check=True)
    assert "déjà à jour" in second.stdout and "exclude" not in second.stdout
    assert "mis à jour" in out.stdout


def test_console_hook_commands_never_fail_the_prompt():
    for event in ("UserPromptSubmit", "Stop"):
        cmd = WANTED["hooks"][event][0]["hooks"][0]["command"]
        assert cmd.endswith("|| true")


# -- commande `lodyorch console` -------------------------------------------------------------

def fake_tmux(tmp_path, exists):
    log = tmp_path / "tmux.log"
    script = tmp_path / "fakebin" / "tmux"
    script.parent.mkdir(exist_ok=True)
    script.write_text("#!/bin/sh\n" f'echo "$@" >> "{log}"\n'
                      f'case "$1" in has-session) exit {0 if exists else 1} ;; esac\nexit 0\n', encoding="utf-8")
    script.chmod(0o755)
    return dict(os.environ, PATH=f"{script.parent}:{os.environ['PATH']}", TMUX="/tmp/fake,1,0"), log


def test_console_command_reuses_the_existing_session_without_renaming(orch, tmp_path):
    env, log = fake_tmux(tmp_path, exists=True)
    out = subprocess.run([sys.executable, str(ORCH_DIR / "lodyorch.py"), "console"], env=env,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0 and "switch-client -t lodyorch" in out.stdout
    calls = log.read_text(encoding="utf-8")
    assert "new-session" not in calls and "rename" not in calls and "kill" not in calls


def test_console_command_creates_the_interactive_session_only_when_absent(orch, tmp_path):
    env, log = fake_tmux(tmp_path, exists=False)
    subprocess.run([sys.executable, str(ORCH_DIR / "lodyorch.py"), "console"], env=env, check=True,
                   capture_output=True, timeout=30)
    calls = log.read_text(encoding="utf-8")
    assert "new-session -d -s lodyorch -c /srv/moneyprinterturbo" in calls
    assert "send-keys -t =lodyorch claude --dangerously-skip-permissions Enter" in calls


# -- priorité humaine -------------------------------------------------------------------------

def human(orch, state):
    return subprocess.run([sys.executable, str(ORCH_DIR / "lodyorch.py"), "human", state],
                          capture_output=True, text=True, timeout=30)


def test_owner_request_holds_the_queue_then_it_resumes_after_quiet_delay(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    human(orch, "busy")
    blocker = orch.mod.start_blocker(orch.con)
    assert blocker[0].startswith("Priorité humaine") and blocker[1] == ""
    human(orch, "idle")
    reason, until = orch.mod.start_blocker(orch.con)
    assert "délai de calme" in reason and until > orch.mod.iso()
    orch.mod.ctl_set(orch.con, "cfg.human_quiet_seconds", 0)
    assert orch.mod.start_blocker(orch.con) is None or "Priorité" not in orch.mod.start_blocker(orch.con)[0]


def test_unfinished_owner_request_stops_blocking_after_busy_max(orch):
    orch.mod.ctl_set(orch.con, "human_state", "busy")
    orch.mod.ctl_set(orch.con, "human_at", "2026-01-01T00:00:00Z")
    assert orch.mod.human_state(orch.con)[0] == "idle"


def test_owner_request_during_a_session_makes_it_wrap_up_and_resume_later(orch, monkeypatch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.issue(2, [AUTO, "priority:P1"])
    orch.mod.ctl_set(orch.con, "cfg.yield_grace_seconds", 30)
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "60")
    orch.sync()

    import threading
    t = threading.Timer(3, lambda: human(orch, "busy"))
    t.start()
    started = time.time()
    # Délai de rangement réduit pour le test : la session (60 s) est arrêtée au bout de ~30 s.
    assert orch.cycle() == 1
    t.join()
    assert time.time() - started < 55
    s = orch.con.execute("SELECT outcome, detail FROM sessions").fetchone()
    assert s["outcome"] == "STOPPED" and "Priorité humaine" in s["detail"]
    task = orch.mod.task(orch.con, 1)
    assert task["status"] == "RUNNING" and task["attempts"] == 0  # repris en premier, sans pénalité
    assert not list(orch.mod.LOG_DIR.glob("*.yield"))
    # Tant que le propriétaire a la main, aucune session ne démarre ; ensuite #1 passe avant #2.
    assert orch.mod.start_blocker(orch.con)[0].startswith("Priorité humaine")
    assert orch.next_issue() == 1


def test_busy_hook_tells_the_console_which_session_is_yielding(orch):
    orch.con.execute("INSERT INTO sessions(id,issue,started_at,pid) VALUES('s',7,'2026-11-10T10:00:00Z',1)")
    orch.con.commit()
    out = human(orch, "busy")
    assert out.returncode == 0 and "#7" in out.stdout and "reprendra" in out.stdout
    assert human(orch, "idle").stdout == ""


def test_statusline_summarises_queue_for_the_console(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.issue(2, [AUTO, "priority:P1"])
    orch.cycle()
    orch.sync()
    out = subprocess.run([sys.executable, str(ORCH_DIR / "lodyorch.py"), "statusline"], capture_output=True,
                         text=True, timeout=30).stdout
    assert out.startswith("lodyorch · ") and "1 à faire" in out and "1 CI en cours" in out and "0 PR en revue" in out


def test_quiet_console_lifts_the_wrap_up_restrictions_and_the_session_finishes_normally(orch, monkeypatch):
    """Incident du 10/11/2026 (#118) : la console était redevenue calme, mais le fichier de rangement
    restait posé ; la session ne pouvait plus travailler, puis a été coupée au message suivant."""
    import threading
    orch.issue(1, [AUTO, "priority:P0"])
    orch.mod.ctl_set(orch.con, "cfg.yield_grace_seconds", 30)
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "14")
    orch.sync()
    seen = []
    busy = threading.Timer(3, lambda: human(orch, "busy"))
    probe = threading.Timer(6, lambda: seen.append(bool(list(orch.mod.LOG_DIR.glob("*.yield")))))
    idle = threading.Timer(7, lambda: human(orch, "idle"))
    for timer in (busy, probe, idle):
        timer.start()
    assert orch.cycle() == 1
    for timer in (busy, probe, idle):
        timer.join()
    assert seen == [True]  # restrictions bien posées pendant l'échange
    (s,) = orch.con.execute("SELECT outcome FROM sessions").fetchall()
    assert s["outcome"] == "COMPLETED"  # pas d'arrêt, pas de seconde session
    assert orch.mod.task(orch.con, 1)["status"] == "WAITING_OWNER"
    assert len(orch.claude_calls()) == 1
    assert not list(orch.mod.LOG_DIR.glob("*.yield"))
    messages = [e["message"] for e in orch.con.execute("SELECT message FROM events WHERE kind='yield'")]
    assert any("restrictions levées" in m for m in messages)


def test_a_new_exchange_after_the_lift_gets_a_full_wrap_up_delay(orch, monkeypatch):
    import threading
    orch.issue(1, [AUTO, "priority:P0"])
    orch.mod.ctl_set(orch.con, "cfg.yield_grace_seconds", 30)
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "90")
    orch.sync()
    timers = [threading.Timer(2, lambda: human(orch, "busy")), threading.Timer(5, lambda: human(orch, "idle")),
              threading.Timer(12, lambda: human(orch, "busy"))]
    for timer in timers:
        timer.start()
    started = time.time()
    assert orch.cycle() == 1
    for timer in timers:
        timer.join()
    elapsed = time.time() - started
    # Arrêt compté depuis le second échange (12 s + 30 s), jamais depuis le premier (2 s + 30 s).
    assert 40 <= elapsed < 75, elapsed
    (s,) = orch.con.execute("SELECT outcome, detail FROM sessions").fetchall()
    assert s["outcome"] == "STOPPED" and "Priorité humaine" in s["detail"]


def test_status_is_strictly_read_only_and_never_interrupts_a_session(orch):
    """`lodyorch status` lancé depuis un terminal SSH : connexion en lecture seule, aucun état modifié,
    aucune priorité humaine déclarée, aucun fichier de rangement (seuls les crochets de la console le font)."""
    import sqlite3
    orch.issue(1, [AUTO, "priority:P0"])
    orch.sync()
    orch.con.execute("INSERT INTO sessions(id, issue, started_at, pid) VALUES('s-1', 1, '2026-11-10T00:00:00Z', ?)",
                     (os.getpid(),))
    orch.con.commit()

    def snapshot():
        con = sqlite3.connect(f"file:{orch.mod.DB_PATH}?mode=ro", uri=True)
        return [list(con.execute(f"SELECT * FROM {t} ORDER BY 1")) for t in
                ("tasks", "sessions", "events", "control", "notifications", "run_reports")]
    before = snapshot()
    env = dict(os.environ)
    for cmd in ("status", "pulse", "statusline", "queue", "sessions"):
        out = subprocess.run([sys.executable, str(ORCH_DIR / "lodyorch.py"), cmd], env=env,
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, (cmd, out.stderr[-500:])
    assert snapshot() == before
    assert orch.mod.human_state(orch.con)[0] == "idle"
    assert not list(orch.mod.LOG_DIR.glob("*.yield"))
    ro = orch.mod.status_db()
    try:
        ro.execute("UPDATE control SET value='x'")
        raise AssertionError("écriture acceptée par la connexion de status")
    except sqlite3.OperationalError as exc:
        assert "readonly" in str(exc)
