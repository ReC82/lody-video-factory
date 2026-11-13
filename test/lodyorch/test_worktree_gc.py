"""#209 : nettoyage sûr des worktrees LODYORCH — vrais dépôts Git temporaires, faux `gh`, aucun réseau."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("worktree_gc", ROOT / "deploy" / "lodyorch" / "worktree_gc.py")
worktree_gc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worktree_gc)


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.origin = tmp_path / "ReC82" / "lody-video-factory.git"
        self.origin.parent.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        self.launch = tmp_path / "repo"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.launch)], check=True, capture_output=True)
        for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
            git(self.launch, "config", key, value)
        (self.launch / "README").write_text("x")
        git(self.launch, "add", "-A")
        git(self.launch, "commit", "-q", "-m", "init")
        git(self.launch, "push", "-q", "origin", "main")
        self.workdir = tmp_path / "worktrees"
        self.workdir.mkdir()
        self.state = tmp_path / "state"
        self.state.mkdir()
        db = sqlite3.connect(self.state / "state.db")
        db.execute("CREATE TABLE tasks(issue INTEGER PRIMARY KEY, status TEXT)")
        db.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, issue INTEGER, ended_at TEXT)")
        db.commit()
        db.close()
        # GitHub simulé : état des PR par branche et des tickets.
        self.prs: dict[str, list[dict]] = {}
        self.issues: dict[int, str] = {}
        self.gh_down = False
        self.calls: list[list[str]] = []

    def env(self):
        return {"HOME": str(self.launch.parent), "LQ_WORKDIR": str(self.workdir), "LQ_STATE_DIR": str(self.state),
                "LQ_LAUNCH_DIR": str(self.launch), "LQ_REPO": "ReC82/lody-video-factory", "LQ_GH": "gh"}

    def worktree(self, issue, *, push=True, merged=False, pr_state=None, issue_state="OPEN"):
        branch = f"agent/{issue}-test"
        path = self.workdir / f"ticket-{issue}"
        git(self.launch, "worktree", "add", "-q", "-b", branch, str(path), "origin/main")
        (path / f"f{issue}").write_text(str(issue))
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", f"travail {issue}")
        if push:
            git(path, "push", "-q", "origin", branch)
        if merged:
            git(self.launch, "fetch", "-q", "origin")
            git(self.launch, "merge", "-q", "--no-edit", f"origin/{branch}")
            git(self.launch, "push", "-q", "origin", "HEAD:main")
        self.prs[branch] = [{"number": 1000 + issue, "state": pr_state}] if pr_state else []
        self.issues[issue] = issue_state
        return path

    def task(self, issue, status, open_session=False):
        db = sqlite3.connect(self.state / "state.db")
        db.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?)", (issue, status))
        if open_session:
            db.execute("INSERT INTO sessions VALUES (?, ?, NULL)", (f"s{issue}", issue))
        db.commit()
        db.close()

    def runner(self, args):
        args = list(args)
        self.calls.append(args)
        if args[0] == "gh":
            if self.gh_down:
                return subprocess.CompletedProcess(args, 1, "", "network down")
            if args[1:3] == ["pr", "list"]:
                branch = args[args.index("--head") + 1]
                return subprocess.CompletedProcess(args, 0, json.dumps(self.prs.get(branch, [])), "")
            if args[1:3] == ["issue", "view"]:
                return subprocess.CompletedProcess(args, 0, json.dumps({"state": self.issues.get(int(args[3]), "OPEN")}),
                                                   "")
            raise AssertionError(args)
        return worktree_gc.run_command(args)

    def run(self, *args):
        return worktree_gc.main(["--min-idle-hours", "0", "--json", *args], runner=self.runner, env=self.env())


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def decisions(world):
    lines = (world.state / "worktree-gc.jsonl").read_text().splitlines()
    return {Path(r["path"]).name: r for r in json.loads(lines[-1])["results"]}


def test_only_closed_clean_pushed_worktrees_are_eligible(world, capsys):
    world.worktree(1, merged=True, pr_state="MERGED", issue_state="CLOSED")       # éligible
    world.worktree(2, pr_state="OPEN")                                            # PR ouverte
    dirty = world.worktree(3, merged=True, pr_state="MERGED", issue_state="CLOSED")
    (dirty / "brouillon.txt").write_text("non suivi")                             # modifications locales
    world.worktree(4, push=False, pr_state="CLOSED", issue_state="CLOSED")        # commits non poussés
    world.worktree(5, merged=True, pr_state="MERGED", issue_state="CLOSED")
    world.task(5, "WAITING_OWNER", open_session=True)                             # session ouverte
    world.worktree(6, merged=True, pr_state="MERGED")
    world.task(6, "RUNNING")                                                      # ticket en cours
    world.worktree(7, pr_state="CLOSED", issue_state="OPEN")                      # ticket ouvert, rien fusionné
    world.worktree(8, pr_state=None, issue_state="CLOSED")                        # ticket fermé, branche poussée
    assert world.run() == 0
    result = decisions(world)
    assert {k for k, v in result.items() if v["eligible"]} == {"ticket-1", "ticket-8"}
    assert "PR ouverte #1002" in result["ticket-2"]["reasons"]
    assert result["ticket-3"]["reasons"][0].startswith("modifications locales")
    assert "commits non poussés" in result["ticket-4"]["reasons"][0]
    assert result["ticket-5"]["reasons"][0].startswith("session ouverte")
    assert result["ticket-6"]["reasons"][0] == "ticket RUNNING dans le superviseur"
    assert "ouvert sans PR fusionnée" in result["ticket-7"]["reasons"][0]
    # Simulation : rien n'a bougé.
    assert all((world.workdir / f"ticket-{n}").is_dir() for n in range(1, 9))


def test_apply_removes_without_force_keeps_the_branch_and_can_recreate(world, capsys):
    path = world.worktree(1, merged=True, pr_state="MERGED", issue_state="CLOSED")
    world.worktree(2, pr_state="OPEN")
    assert world.run("--apply") == 0
    assert not path.exists() and (world.workdir / "ticket-2").is_dir()
    removals = [c for c in world.calls if c[:1] == ["git"] and "worktree" in c and "remove" in c]
    assert len(removals) == 1 and "--force" not in removals[0] and "-f" not in removals[0]
    assert git(world.launch, "branch", "--list", "agent/1-test")  # branche conservée
    entry = json.loads((world.state / "worktree-gc.jsonl").read_text().splitlines()[-1])
    recreate = entry["actions"][0]["recreate"]
    subprocess.run(recreate.split(), check=True, capture_output=True)
    assert (path / "f1").read_text() == "1"


def test_recent_activity_protects(world, capsys):
    world.worktree(1, merged=True, pr_state="MERGED", issue_state="CLOSED")
    assert worktree_gc.main(["--json"], runner=world.runner, env=world.env()) == 0
    assert decisions(world)["ticket-1"]["reasons"][0].startswith("activité récente")


def test_github_unreachable_keeps_everything(world, capsys):
    world.worktree(1, merged=True, pr_state="MERGED", issue_state="CLOSED")
    world.gh_down = True
    assert world.run("--apply") == 0
    assert (world.workdir / "ticket-1").is_dir()
    assert "GitHub injoignable" in decisions(world)["ticket-1"]["reasons"][0]


def test_failed_fetch_keeps_everything(world, capsys):
    world.worktree(1, merged=True, pr_state="MERGED", issue_state="CLOSED")
    git(world.launch, "remote", "set-url", "origin", str(world.launch.parent / "ReC82" / "lody-video-factory-absent"))
    git(world.workdir / "ticket-1", "remote", "set-url", "origin",
        str(world.launch.parent / "ReC82" / "lody-video-factory-absent"))
    assert world.run("--apply") == 0
    assert (world.workdir / "ticket-1").is_dir()
    assert decisions(world)["ticket-1"]["reasons"][0] == "git fetch impossible : état distant inconnu"


def test_other_supervisors_worktrees_are_never_touched(world, capsys):
    other = world.workdir.parent / "Dig" / "dig.git"
    other.parent.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(other)], check=True)
    path = world.worktree(1, merged=True, pr_state="MERGED", issue_state="CLOSED")
    git(path, "remote", "set-url", "origin", str(other))
    (world.workdir / "ticket-2").mkdir()  # simple dossier, pas un worktree
    assert world.run("--apply") == 0
    result = decisions(world)
    assert path.is_dir() and "jamais touché" in result["ticket-1"]["reasons"][0]
    assert result["ticket-2"]["reasons"] == ["pas un worktree Git : ignoré"]


def test_missing_supervisor_database_means_caution(world, capsys):
    world.worktree(1, merged=True, pr_state="MERGED", issue_state="CLOSED")
    (world.state / "state.db").unlink()
    assert world.run() == 0
    assert "base du superviseur absente" in decisions(world)["ticket-1"]["reasons"][0]


def test_concurrent_run_is_refused(world, capsys):
    with (world.state / "worktree-gc.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert world.run("--apply") == 3
