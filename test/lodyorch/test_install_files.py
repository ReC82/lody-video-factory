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


def test_smtp_file_is_read_only_by_the_isolated_notify_unit():
    """#123 : le superviseur et ses sessions ne voient pas le fichier SMTP ; seule l'unité d'envoi le
    lit, sans accès à Claude, à GitHub ni au dépôt."""
    env = instance_env()
    smtp = env["LQ_NOTIFY_SMTP_ENV"]
    assert smtp == "/home/ubuntu/.config/plugadmin/smtp.env" and env["LQ_RUN_REPORTS"] == "1"
    assert "-/home/ubuntu/.config/plugadmin" in dict(directives(SERVICE))["InaccessiblePaths"].split()
    notify = dict(directives((ORCH_DIR / "systemd" / "lodyorch-notify.service").read_text(encoding="utf-8")))
    assert notify["Type"] == "oneshot" and notify["User"] == "ubuntu" and notify["NoNewPrivileges"] == "yes"
    assert notify["ExecStart"] == "/usr/bin/python3 /opt/lodyorch/lodyorch.py notifications --send"
    assert notify["ProtectSystem"] == "strict" and notify["ProtectHome"] == "read-only"
    assert notify["ReadWritePaths"] == "/home/ubuntu/.local/state/lodyorch"
    hidden = notify["InaccessiblePaths"].split()
    for path in ("-/home/ubuntu/.claude", "-/home/ubuntu/.config/gh", "-/srv/moneyprinterturbo", "-/home/ubuntu/lody-orch"):
        assert path in hidden
    timer = (ORCH_DIR / "systemd" / "lodyorch-notify.timer").read_text(encoding="utf-8")
    assert "OnUnitInactiveSec=2min" in timer and "WantedBy=timers.target" in timer


def test_sessions_are_resource_capped_to_protect_production():
    d = dict(directives(SERVICE))
    assert (d["MemoryHigh"], d["MemoryMax"], d["CPUQuota"]) == ("1400M", "1800M", "150%")
    assert d["CPUWeight"] == "50" and d["IOWeight"] == "50"


def test_no_second_tmux_console_is_installed():
    # La console est la session interactive du propriétaire : aucun service ne crée de fenêtre.
    assert not (ORCH_DIR / "systemd" / "lodyorch-console.service").exists()
    assert not (ORCH_DIR / "console.sh").exists()
    assert "tmux" not in SERVICE.split("[Service]")[1].split("ExecStart")[1].splitlines()[0]
    install = (ORCH_DIR / "install.sh").read_text(encoding="utf-8")
    assert "new-session" not in install and "rename-session" not in install


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
    assert env["LQ_CLAIM_COMMENT"] == "1" and env["LQ_REQUIRE_PR"] == "1"
    assert env["LQ_LABELS_FILE"] == "/opt/lodyorch/labels.json"
    text = (ORCH_DIR / "lodyorch.env").read_text(encoding="utf-8")
    assert not re.search(r"(?im)^\s*\w*(TOKEN|PASSWORD|SECRET|API_KEY)\w*\s*=", text)


def test_claude_settings_hook_points_to_the_installed_guard():
    cfg = json.loads((ORCH_DIR / "claude-settings.json").read_text(encoding="utf-8"))
    hooks = cfg["hooks"]["PreToolUse"]
    assert hooks[0]["matcher"] == "*"
    assert hooks[0]["hooks"][0]["command"] == "/usr/bin/python3 /opt/lodyorch/guard.py"
    assert "Bash(sudo:*)" in cfg["permissions"]["deny"]


def test_install_dry_run_changes_nothing_and_starts_nothing(tmp_path):
    env = dict(os.environ, LODYORCH_USER=getpass.getuser())
    out = subprocess.run(["sh", str(ORCH_DIR / "install.sh"), "--dry-run"], env=env, capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    actions = [line for line in out.stdout.splitlines() if "[dry-run]" in line]
    assert any("/opt/lodyorch/guard.py" in a for a in actions)
    assert any("/usr/local/bin/lodyorch" in a for a in actions)
    assert any("/opt/lodyorch/labels.json" in a for a in actions)
    assert not any(re.search(r"systemctl (enable|start|restart)|tmux", a) for a in actions)
    assert "lodyorch-console" not in out.stdout
    assert "rien n'a été démarré" in out.stdout
    assert Path(ORCH_DIR / "install.sh").stat().st_mode & 0o111


def test_merge_policy_is_installed_root_owned_next_to_the_supervisor():
    """#200 : lodyorch.py importe merge_policy.py depuis son dossier ; plafond de fusion dans l'env root."""
    script = (ORCH_DIR / "install.sh").read_text(encoding="utf-8")
    assert re.search(r"for f in lodyorch\.py guard\.py merge_policy\.py ux_safe\.py console_settings\.py; do", script)
    assert instance_env()["LQ_AUTO_MERGE"] == "dry-run"
