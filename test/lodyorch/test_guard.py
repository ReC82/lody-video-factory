"""Crochet de garde PreToolUse des sessions LODYORCH (#116) : ce qui est refusé avant exécution."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test.lodyorch.conftest import ORCH_DIR

GUARD = ORCH_DIR / "guard.py"


def run_guard(tool, tool_input, env=None):
    out = subprocess.run([sys.executable, str(GUARD)], input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
                         capture_output=True, text=True, timeout=30, env=env)
    return out.returncode, out.stderr


# Racine fictive hors de /tmp (lui-même inscriptible pour les sessions) : rien n'y est créé.
BASE = Path("/nonexistent/lodyorch-test")


@pytest.fixture
def guard_env():
    return dict(os.environ, LQ_WORKDIR=str(BASE / "wt"), LQ_STATE_DIR=str(BASE / "state"),
                LQ_STATUS_FILE=str(BASE / "state" / "sessions" / "abc.status.json"), HOME=str(BASE / "home"))


@pytest.mark.parametrize("command", [
    "sudo -n true",
    "docker ps",
    "cd x && docker compose -f docker-compose.lody.yml up -d",
    "systemctl restart lodyorch",
    "tmux kill-session -t digorch",
    "pkill -f streamlit",
    "cat /srv/moneyprinterturbo/config.toml",
    "cat \"/srv/moneyprinterturbo/secrets/admin-auth.json\"",
    "ls ~/.claude/.credentials.json",
    "cat ~/.config/gh/hosts.yml",
    "cat config.toml",
    "source .env",
    "gh pr merge 12 --squash",
    "gh api -X PUT repos/ReC82/lody-video-factory/pulls/12/merge",
    "gh issue close 116",
    "gh issue edit 116 --add-label agent:blocked",
    "gh auth token",
    "git push --force origin agent/116-x",
    "git push origin +agent/116-x",
    "git push origin HEAD:main",
    "git push origin main",
    "git push origin --delete agent/116-x",
    "lodyorch resume",
    "lodyorch config set max_sessions_per_day 500",
    "printenv",
    "rm -rf /",
    "echo ghp_" + "a" * 36,
    "cat /home/ubuntu/.local/state/dig-orch/state.db",
])
def test_dangerous_commands_are_denied(guard_env, command):
    code, err = run_guard("Bash", {"command": command}, guard_env)
    assert code == 2, command
    assert "refusée" in err


@pytest.mark.parametrize("command", [
    "uv run --no-sync python -X utf8 -m pytest -q test",
    "git -C /home/ubuntu/lody-orch/repo worktree add -b agent/116-x /home/ubuntu/lody-orch/worktrees/ticket-116 origin/main",
    "git push -u origin agent/116-lodyorch",
    'git commit -m "docs: config.toml et docker ne sont jamais lus (#116)"',
    "gh pr create --base main --head agent/116-x --title 'x' --body-file /tmp/pr.md",
    "gh pr checks 12 --watch --interval 60",
    "gh issue comment 116 --repo ReC82/lody-video-factory --body-file /tmp/c.md",
    'lodyorch phase "tests — pytest"',
    "lodyorch status",
    "git merge origin/main",
    "rm -rf /tmp/build",
])
def test_ordinary_development_commands_are_allowed(guard_env, command):
    code, err = run_guard("Bash", {"command": command}, guard_env)
    assert code == 0, (command, err)


def test_file_tools_write_only_in_worktrees_reports_and_status_file(guard_env):
    tmp_path = BASE
    ok = [("Write", tmp_path / "wt" / "ticket-1" / "a.py"), ("Edit", tmp_path / "state" / "reports" / "ticket-1.md"),
          ("Write", tmp_path / "state" / "sessions" / "abc.status.json"), ("Write", "/tmp/pr.md")]
    for tool, path in ok:
        assert run_guard(tool, {"file_path": str(path), "content": "x"}, guard_env)[0] == 0, path
    denied = [("Write", tmp_path / "state" / "sessions" / "other.status.json"), ("Edit", "/opt/lodyorch/guard.py"),
              ("Write", tmp_path / "state" / "state.db"), ("Read", "/srv/moneyprinterturbo/config.toml"),
              ("Write", tmp_path / "wt" / "ticket-1" / "config.toml"), ("Read", "/etc/dig/dig.env")]
    for tool, path in denied:
        assert run_guard(tool, {"file_path": str(path), "content": "x"}, guard_env)[0] == 2, path


def test_secret_content_and_external_connectors_are_denied(guard_env):
    tmp_path = BASE
    path = str(tmp_path / "wt" / "a.py")
    assert run_guard("Write", {"file_path": path, "content": "KEY='sk-ant-api03-" + "x" * 30 + "'"}, guard_env)[0] == 2
    assert run_guard("mcp__claude_ai_Gmail__send", {}, guard_env)[0] == 2
    assert run_guard("RemoteTrigger", {}, guard_env)[0] == 2
    assert run_guard("Grep", {"pattern": "config.toml", "path": str(tmp_path / "wt")}, guard_env)[0] == 0


def test_unreadable_input_is_denied():
    out = subprocess.run([sys.executable, str(GUARD)], input="pas du json", capture_output=True, text=True, timeout=30)
    assert out.returncode == 2
