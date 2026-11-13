#!/usr/bin/env python3
"""#209 : nettoyage SÛR des worktrees LODYORCH (`~/lody-orch/worktrees/ticket-<n>`). Simulation par défaut.

    python3 deploy/lodyorch/worktree_gc.py            # plan : chaque worktree, décision et raisons
    python3 deploy/lodyorch/worktree_gc.py --apply    # retire les seuls worktrees éligibles

Outil séparé du superviseur (aucune modification de ``lodyorch.py``, en cours de refonte par #207) : il lit
la même configuration (``LQ_WORKDIR``, ``LQ_STATE_DIR``, ``LQ_REPO``, ``LQ_LAUNCH_DIR``, ``LQ_GH``) et la
base d'état du superviseur en LECTURE SEULE.

Un worktree n'est retiré que si TOUT est vrai :

- c'est un worktree Git du dépôt de l'instance (``LQ_REPO`` ; jamais celui d'un autre superviseur, ex. Digorch) ;
- aucune session ouverte sur ce ticket et le ticket n'est pas ``RUNNING`` dans la base du superviseur ;
- copie propre (``git status --porcelain`` vide, fichiers non suivis compris) sur une branche nommée ;
- tous ses commits sont sur ``origin`` (branche poussée) ou déjà dans ``origin/main`` ;
- aucune PR OUVERTE pour la branche, et le ticket est fermé ou une PR de la branche a été fusionnée ;
- aucune activité depuis ``--min-idle-hours`` (24 h par défaut).

Au moindre doute (GitHub injoignable, base illisible…), le worktree est conservé. Retrait par
``git worktree remove`` SANS ``--force`` (Git refuse lui-même un worktree modifié) ; la BRANCHE est
conservée : ``git worktree add <chemin> <branche>`` recrée la copie à l'identique pour une reprise.
Verrou ``<LQ_STATE_DIR>/worktree-gc.lock`` ; journal ``<LQ_STATE_DIR>/worktree-gc.jsonl``.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
TICKET_DIR = re.compile(r"^ticket-(\d+)$")


def run_command(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


class Config:
    def __init__(self, env: Optional[dict[str, str]] = None) -> None:
        env = dict(os.environ if env is None else env)
        home = Path(env.get("HOME", str(Path.home())))
        self.workdir = Path(env.get("LQ_WORKDIR") or home / "lody-orch/worktrees")
        self.state_dir = Path(env.get("LQ_STATE_DIR") or home / ".local/state/lodyorch")
        self.repo = env.get("LQ_REPO") or "ReC82/lody-video-factory"
        self.launch_dir = Path(env.get("LQ_LAUNCH_DIR") or home / "lody-orch/repo")
        self.gh = env.get("LQ_GH") or "gh"


def _git(runner: Runner, path: Path, *args: str) -> "subprocess.CompletedProcess[str]":
    return runner(["git", "-C", str(path), *args])


def session_state(state_db: Path, issue: int) -> tuple[bool, str, Optional[str]]:
    """(protégé ?, raison, statut) d'après la base du superviseur, ouverte en lecture seule."""
    if not state_db.exists():
        return True, f"base du superviseur absente ({state_db}) : prudence", None
    try:
        connection = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=5)
        try:
            open_session = connection.execute(
                "SELECT id FROM sessions WHERE issue=? AND ended_at IS NULL", (issue,)).fetchone()
            row = connection.execute("SELECT status FROM tasks WHERE issue=?", (issue,)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        return True, f"base du superviseur illisible : {error}", None
    status = row[0] if row else None
    if open_session:
        return True, f"session ouverte {open_session[0]}", status
    if status == "RUNNING":
        return True, "ticket RUNNING dans le superviseur", status
    return False, "", status


def github_state(runner: Runner, gh: str, repo: str, branch: str, issue: int) -> tuple[bool, str]:
    """(éligible ?, explication). Toute erreur GitHub → non éligible."""
    prs = runner([gh, "pr", "list", "--repo", repo, "--head", branch, "--state", "all",
                  "--json", "number,state", "--limit", "50"])
    issue_view = runner([gh, "issue", "view", str(issue), "--repo", repo, "--json", "state"])
    try:
        pulls = json.loads(prs.stdout) if prs.returncode == 0 else None
        ticket = json.loads(issue_view.stdout) if issue_view.returncode == 0 else None
    except json.JSONDecodeError:
        pulls = ticket = None
    if pulls is None or ticket is None:
        return False, "GitHub injoignable ou réponse illisible : conservé"
    open_prs = [p["number"] for p in pulls if str(p.get("state")).upper() == "OPEN"]
    if open_prs:
        return False, "PR ouverte " + ", ".join(f"#{n}" for n in open_prs)
    merged = [p["number"] for p in pulls if str(p.get("state")).upper() == "MERGED"]
    closed_issue = str(ticket.get("state")).upper() == "CLOSED"
    if not (closed_issue or merged):
        return False, f"ticket #{issue} ouvert sans PR fusionnée"
    parts = []
    if closed_issue:
        parts.append(f"ticket #{issue} fermé")
    if merged:
        parts.append("PR fusionnée " + ", ".join(f"#{n}" for n in merged))
    return True, " ; ".join(parts)


def inspect_worktree(path: Path, issue: int, config: Config, runner: Runner, now: float,
                     min_idle_hours: float) -> dict[str, Any]:
    reasons: list[str] = []
    info: dict[str, Any] = {"path": str(path), "issue": issue, "branch": None, "eligible": False}
    if not (path / ".git").exists():
        info["reasons"] = ["pas un worktree Git : ignoré"]
        return info
    remote = _git(runner, path, "remote", "get-url", "origin")
    if remote.returncode != 0 or config.repo.lower() not in remote.stdout.strip().lower():
        info["reasons"] = [f"dépôt différent de {config.repo} ({remote.stdout.strip() or '?'}) : jamais touché"]
        return info
    protected, why, status = session_state(config.state_dir / "state.db", issue)
    info["task_status"] = status
    if protected:
        reasons.append(why)
    branch = _git(runner, path, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch.returncode != 0:
        reasons.append("HEAD détachée : conservé")
    else:
        info["branch"] = branch.stdout.strip()
    dirty = _git(runner, path, "status", "--porcelain", "--untracked-files=all")
    if dirty.returncode != 0:
        reasons.append("git status impossible")
    elif dirty.stdout.strip():
        reasons.append(f"modifications locales ({len(dirty.stdout.splitlines())} fichier(s))")
    head = _git(runner, path, "rev-parse", "HEAD").stdout.strip()
    if info["branch"]:
        pushed = any(
            _git(runner, path, "merge-base", "--is-ancestor", head, ref).returncode == 0
            for ref in (f"origin/{info['branch']}", "origin/main"))
        if not pushed:
            reasons.append("commits non poussés (absents d'origin/<branche> et d'origin/main)")
    last = _git(runner, path, "log", "-1", "--format=%ct")
    try:
        last_activity = max(float(last.stdout.strip() or 0), path.stat().st_mtime)
    except (OSError, ValueError):
        last_activity = now
    idle_hours = (now - last_activity) / 3600
    info["idle_hours"] = round(idle_hours, 1)
    if idle_hours < min_idle_hours:
        reasons.append(f"activité récente ({idle_hours:.1f} h < {min_idle_hours:g} h)")
    if not reasons and info["branch"]:
        eligible, why = github_state(runner, config.gh, config.repo, info["branch"], issue)
        info["github"] = why
        if not eligible:
            reasons.append(why)
    info["eligible"] = not reasons
    info["reasons"] = reasons or [info.get("github", "")]
    return info


def plan(config: Config, runner: Runner = run_command, now: Optional[float] = None,
         min_idle_hours: float = 24.0) -> list[dict[str, Any]]:
    now = time.time() if now is None else now
    results = []
    if not config.workdir.is_dir():
        return results
    for entry in sorted(config.workdir.iterdir()):
        match = TICKET_DIR.match(entry.name)
        if entry.is_dir() and match:
            results.append(inspect_worktree(entry, int(match.group(1)), config, runner, now, min_idle_hours))
    return results


def acquire_lock(state_dir: Path):
    import fcntl

    state_dir.mkdir(parents=True, exist_ok=True)
    handle = (state_dir / "worktree-gc.lock").open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        if error.errno in (errno.EAGAIN, errno.EACCES):
            return None
        raise
    return handle


def apply(results: list[dict[str, Any]], config: Config, runner: Runner) -> list[dict[str, Any]]:
    actions = []
    for item in results:
        if not item["eligible"]:
            continue
        removed = _git(runner, config.launch_dir, "worktree", "remove", item["path"])
        actions.append({"path": item["path"], "branch": item["branch"],
                        "outcome": "retiré" if removed.returncode == 0 else "refusé par git",
                        "detail": removed.stderr.strip()[:300],
                        "recreate": f"git -C {config.launch_dir} worktree add {item['path']} {item['branch']}"})
    return actions


def main(argv: Optional[list[str]] = None, runner: Runner = run_command, env: Optional[dict[str, str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Nettoyage sûr des worktrees LODYORCH (#209)")
    parser.add_argument("--apply", action="store_true", help="retire les worktrees éligibles (sinon simulation)")
    parser.add_argument("--min-idle-hours", type=float, default=24.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    config = Config(env)
    lock = acquire_lock(config.state_dir)
    if lock is None:
        print("ALERTE : un autre nettoyage tient le verrou — rien n'est fait", file=sys.stderr)
        return 3
    try:
        # Références distantes à jour (lecture seule pour les worktrees) ; sans elles, rien n'est éligible.
        fetch = _git(runner, config.launch_dir, "fetch", "--quiet", "origin")
        results = plan(config, runner, min_idle_hours=args.min_idle_hours)
        if fetch.returncode != 0:
            for item in results:
                item["eligible"] = False
                item["reasons"] = ["git fetch impossible : état distant inconnu"] + item["reasons"]
        actions = apply(results, config, runner) if args.apply else []
    finally:
        lock.close()
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "mode": "apply" if args.apply else "dry-run", "results": results, "actions": actions}
    with (config.state_dir / "worktree-gc.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if args.json:
        print(json.dumps(entry, ensure_ascii=False, indent=2))
    else:
        mode = "APPLICATION" if args.apply else "SIMULATION (rien n'est retiré)"
        print(f"== Worktrees {config.workdir} — {mode}")
        for item in results:
            mark = "RETIRABLE" if item["eligible"] else "CONSERVÉ "
            print(f"  {mark} {Path(item['path']).name:12} {item.get('branch') or '-':40} "
                  + " ; ".join(r for r in item["reasons"] if r))
        for action in actions:
            print(f"  {action['outcome']:14} {action['path']} — recréer : {action['recreate']}")
        print(f"{sum(1 for i in results if i['eligible'])} éligible(s) sur {len(results)}.")
    return 1 if any(a["outcome"] != "retiré" for a in actions) else 0


if __name__ == "__main__":
    sys.exit(main())
