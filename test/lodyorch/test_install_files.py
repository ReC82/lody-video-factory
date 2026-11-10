"""Fichiers d'installation LODYORCH (#116) : service non root et isolé, noms distincts des autres
superviseurs, console tmux qui ne touche jamais une autre session, installation sans démarrage."""

import getpass
import json
import os
import re
import subprocess
from pathlib import Path

from test.lodyorch.conftest import ORCH_DIR, instance_env

SERVICE = (ORCH_DIR / "systemd" / "lodyorch.service").read_text(encoding="utf-8")
CONSOLE = (ORCH_DIR / "systemd" / "lodyorch-console.service").read_text(encoding="utf-8")


def directives(unit):
    return [line.split("=", 1) for line in unit.splitlines() if "=" in line and not line.startswith("#")]


def test_supervisor_service_runs_unprivileged_and_isolated():
    d = dict(directives(SERVICE))
    assert d["User"] == "ubuntu"
    assert d["ExecStart"] == "/usr/bin/python3 /opt/lodyorch/lodyorch.py run"
    assert d["EnvironmentFile"] == "/opt/lodyorch/lodyorch.env"
    assert d["Restart"] == "on-failure"  # reprise après crash, pas après « lodyorch stop »
    assert d["NoNewPrivileges"] == "yes"  # ni sudo, ni docker, ni systemctl dans les sessions
    assert "-/srv/moneyprinterturbo" in d["InaccessiblePaths"].split()
    assert "-/home/ubuntu/Dig" in d["ReadOnlyPaths"].split()
    assert d["ReadWritePaths"].split()[0] == "/home/ubuntu/.local/state/lodyorch"


def test_console_service_delegates_to_the_conflict_safe_script():
    d = dict(directives(CONSOLE))
    assert d["ExecStart"] == "/opt/lodyorch/console.sh start"
    assert d["ExecStop"] == "/opt/lodyorch/console.sh stop"
    assert "kill-session" not in CONSOLE


def test_names_never_collide_with_other_supervisors():
    env = instance_env()
    assert env["LQ_TMUX_SESSION"] == "lodyorch"
    assert env["LQ_SERVICE"] == "lodyorch" and env["LQ_CLI"] == "lodyorch"
    assert env["LQ_STATE_DIR"].endswith("/.local/state/lodyorch")
    assert env["LQ_BOT_MARKER"] == "<!-- lodyorch -->"
    taken = {"dig", "digorch", "dig-orch", "jury-central", "jury-central-orch", "rpgquest", "lodyquests-orch",
             "lqorch", "jcorch"}
    for key in ("LQ_TMUX_SESSION", "LQ_SERVICE", "LQ_CLI"):
        assert env[key] not in taken
    for key in ("LQ_STATE_DIR", "LQ_LAUNCH_DIR", "LQ_WORKDIR"):
        assert not any(f"/{name}" in env[key] for name in taken - {"dig"}), key
        assert "/srv/moneyprinterturbo" not in env[key] and "/home/ubuntu/Dig" not in env[key]
    # Les autres bases ne sont que lues (quota partagé), jamais la nôtre à leur place.
    assert all("lodyorch" not in p for p in env["LQ_SHARED_USAGE_DBS"].split(","))


def test_instance_config_selects_agent_auto_and_contains_no_secret():
    env = instance_env()
    assert env["LQ_ELIGIBLE_LABEL"] == "agent:auto"
    assert "agent:human" in env["LQ_HOLD_LABELS"].split(",")
    labels = json.loads(env["LQ_STATUS_LABELS"])
    assert labels["RUNNING"] == "agent:in-progress" and labels["BLOCKED"] == "agent:blocked"
    assert env["LQ_DEPS_DONE_STATES"] == "DONE"
    assert env["LQ_REQUIRE_AUTH"] == "1" and env["LQ_STRICT_MCP"] == "1"
    text = (ORCH_DIR / "lodyorch.env").read_text(encoding="utf-8")
    assert not re.search(r"(?im)^\s*\w*(TOKEN|PASSWORD|SECRET|API_KEY)\w*\s*=", text)


def test_claude_settings_hook_points_to_the_installed_guard():
    cfg = json.loads((ORCH_DIR / "claude-settings.json").read_text(encoding="utf-8"))
    hooks = cfg["hooks"]["PreToolUse"]
    assert hooks[0]["matcher"] == "*"
    assert hooks[0]["hooks"][0]["command"] == "/usr/bin/python3 /opt/lodyorch/guard.py"
    assert "Bash(sudo:*)" in cfg["permissions"]["deny"]


def fake_tmux(tmp_path, exists, windows):
    log = tmp_path / "tmux.log"
    script = tmp_path / "bin" / "tmux"
    script.parent.mkdir(exist_ok=True)
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        'case "$1" in\n'
        f'  has-session) exit {0 if exists else 1} ;;\n'
        f'  list-windows) printf "{windows}" ; exit {0 if exists else 1} ;;\n'
        "esac\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    env = dict(os.environ, PATH=f"{script.parent}:{os.environ['PATH']}")
    return env, log


def test_console_refuses_a_foreign_lodyorch_session_and_never_kills_it(tmp_path):
    env, log = fake_tmux(tmp_path, exists=True, windows="claude\\n")
    start = subprocess.run(["sh", str(ORCH_DIR / "console.sh"), "start"], env=env, capture_output=True, text=True)
    assert start.returncode == 1 and "tmux rename-session" in start.stderr
    stop = subprocess.run(["sh", str(ORCH_DIR / "console.sh"), "stop"], env=env, capture_output=True, text=True)
    assert stop.returncode == 0
    calls = log.read_text(encoding="utf-8")
    assert "new-session" not in calls and "kill-session" not in calls


def test_console_creates_its_windows_when_free_and_stops_only_itself(tmp_path):
    env, log = fake_tmux(tmp_path, exists=False, windows="")
    assert subprocess.run(["sh", str(ORCH_DIR / "console.sh"), "start"], env=env).returncode == 0
    calls = log.read_text(encoding="utf-8")
    assert "new-session -d -x 200 -y 50 -s lodyorch -n status" in calls
    for window in ("journal", "events", "activite"):
        assert f"-n {window}" in calls
    (tmp_path / "console").mkdir()
    env2, log2 = fake_tmux(tmp_path / "console", exists=True, windows="status\\njournal\\n")
    subprocess.run(["sh", str(ORCH_DIR / "console.sh"), "stop"], env=env2, check=True)
    assert "kill-session -t =lodyorch" in log2.read_text(encoding="utf-8")


def test_install_dry_run_changes_nothing_and_starts_nothing(tmp_path):
    env = dict(os.environ, LODYORCH_USER=getpass.getuser())
    out = subprocess.run(["sh", str(ORCH_DIR / "install.sh"), "--dry-run"], env=env, capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    actions = [line for line in out.stdout.splitlines() if "[dry-run]" in line]
    assert any("/opt/lodyorch/guard.py" in a for a in actions)
    assert any("/usr/local/bin/lodyorch" in a for a in actions)
    assert not any(re.search(r"systemctl (enable|start|restart)|tmux", a) for a in actions)
    assert "rien n'a été démarré" in out.stdout
    assert Path(ORCH_DIR / "install.sh").stat().st_mode & 0o111
