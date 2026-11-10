"""Fixtures LODYORCH : superviseur chargé avec la VRAIE configuration d'instance
(deploy/lodyorch/lodyorch.env), chemins redirigés vers tmp_path, faux `gh` et faux `claude`.
Aucun réseau, aucun appel au modèle, aucune écriture hors de tmp_path."""

import importlib.util
import json
import shlex
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORCH_DIR = ROOT / "deploy" / "lodyorch"
HERE = Path(__file__).resolve().parent


def instance_env():
    """Variables de deploy/lodyorch/lodyorch.env (format `NOM=valeur`, guillemets shell)."""
    env = {}
    for line in (ORCH_DIR / "lodyorch.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, raw = line.split("=", 1)
            env[key] = shlex.split(raw)[0] if raw else ""
    return env


def shim(path, script):
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


class Harness:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.gh_state = tmp_path / "gh.json"
        self.calls = tmp_path / "claude-calls.jsonl"
        self.gh_state.write_text(json.dumps({"issues": {}, "comments": []}), encoding="utf-8")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (tmp_path / "repo" / ".git").mkdir(parents=True)
        env = instance_env()
        env.update(
            LQ_STATE_DIR=str(tmp_path / "state"),
            LQ_LAUNCH_DIR=str(tmp_path / "repo"),
            LQ_WORKDIR=str(tmp_path / "worktrees"),
            LQ_PROMPT=str(ORCH_DIR / "session_prompt.md"),
            LQ_CLAUDE_SETTINGS=str(ORCH_DIR / "claude-settings.json"),
            LQ_CLAUDE=str(shim(bindir / "claude", HERE / "fake_claude.py")),
            LQ_GH=str(shim(bindir / "gh", HERE / "fake_gh.py")),
            LQ_SHARED_USAGE_DBS="",
            LQ_NOTIFY_SMTP_ENV="",
            FAKE_GH_STATE=str(self.gh_state),
            FAKE_CLAUDE_CALLS=str(self.calls),
        )
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        self.monkeypatch = monkeypatch
        self.mod = self.load()
        self.con = self.mod.db()

    def load(self):
        spec = importlib.util.spec_from_file_location(f"lodyorch_{uuid.uuid4().hex}", ORCH_DIR / "lodyorch.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    # -- faux GitHub
    def gh(self):
        return json.loads(self.gh_state.read_text(encoding="utf-8"))

    def set_gh(self, data):
        self.gh_state.write_text(json.dumps(data), encoding="utf-8")

    def issue(self, n, labels=(), body="", title=None, **extra):
        data = self.gh()
        data["issues"][str(n)] = dict(number=n, title=title or f"Ticket {n}", state="open",
                                       labels=list(labels), body=body, **extra)
        self.set_gh(data)

    def labels(self, n):
        return self.gh()["issues"][str(n)]["labels"]

    def edit_labels(self, n, add=(), remove=()):
        data = self.gh()
        it = data["issues"][str(n)]
        it["labels"] = [x for x in it["labels"] if x not in remove] + [x for x in add if x not in it["labels"]]
        self.set_gh(data)

    def close(self, n):
        data = self.gh()
        data["issues"][str(n)]["state"] = "closed"
        self.set_gh(data)

    # -- superviseur
    def sync(self):
        self.mod.sync_github(self.con)

    def status(self, n):
        t = self.mod.task(self.con, n)
        return t["status"] if t else None

    def next_issue(self):
        t = self.mod.next_task(self.con)
        return t["issue"] if t else None

    def cycle(self, mode="deliver"):
        """Un tour du superviseur sans les temporisations : synchro, sélection, session."""
        self.monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
        self.sync()
        t = self.mod.next_task(self.con)
        if t is None or self.mod.session_preflight():
            return None
        self.mod.run_session(self.con, t)
        return t["issue"]

    def claude_calls(self):
        if not self.calls.exists():
            return []
        return [json.loads(x) for x in self.calls.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def orch(tmp_path, monkeypatch):
    h = Harness(tmp_path, monkeypatch)
    yield h
    h.con.close()
