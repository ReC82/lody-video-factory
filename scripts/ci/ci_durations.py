#!/usr/bin/env python3
"""#198 : benchmark reproductible des durées de CI par classe de PR, à partir de l'API GitHub Actions.

    python3 scripts/ci/ci_durations.py --repo ReC82/lody-video-factory --runs 60

Lecture seule (`gh api`, authentification de `gh`). Pour chaque run du workflow CI terminé avec succès,
la classe est lue dans le nom de l'étape « Run selected tests (docs|targeted) » ou déduite de l'étape
« Run full test suite with coverage » ; les runs antérieurs à #198 (pas de job « CI result ») sont
classés « avant-198 ». Affiche, par événement et par classe : nombre de runs, médiane et étendue de la
durée totale (création du run → fin du dernier job) et du job de tests le plus long. N'invente rien :
une classe sans run n'apparaît pas.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import datetime


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def gh_api(path: str) -> dict:
    out = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True, timeout=120).stdout
    return json.loads(out)


def run_class(jobs: list[dict]) -> str:
    names = {j["name"] for j in jobs}
    if "CI result" not in names:
        return "avant-198"
    for job in jobs:
        for step in job.get("steps") or []:
            if step["name"].startswith("Run selected tests (") and step.get("conclusion") == "success":
                return step["name"][len("Run selected tests ("):-1]
            if step["name"] == "Run full test suite with coverage" and step.get("conclusion") == "success":
                return "full"
    return "inconnu"


def summarize(runs: list[tuple[dict, list[dict]]]) -> dict[tuple[str, str], dict]:
    groups: dict[tuple[str, str], dict[str, list[float]]] = {}
    for run, jobs in runs:
        done = [j for j in jobs if j.get("completed_at") and j.get("started_at")]
        if not done:
            continue
        total = (max(_ts(j["completed_at"]) for j in done) - _ts(run["created_at"])).total_seconds()
        tests = [(_ts(j["completed_at"]) - _ts(j["started_at"])).total_seconds() for j in done
                 if j["name"].startswith("Python ")]
        group = groups.setdefault((run["event"], run_class(jobs)), {"total": [], "tests": []})
        group["total"].append(total)
        if tests:
            group["tests"].append(max(tests))
    return {key: {name: (len(v), statistics.median(v), min(v), max(v)) if v else (0, 0, 0, 0)
                  for name, v in values.items()} for key, values in sorted(groups.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--runs", type=int, default=60)
    parser.add_argument("--workflow", default="ci.yml")
    args = parser.parse_args(argv)

    listing = gh_api(f"repos/{args.repo}/actions/workflows/{args.workflow}/runs"
                     f"?status=success&per_page={min(args.runs, 100)}")
    runs = []
    for run in listing.get("workflow_runs", [])[: args.runs]:
        jobs = gh_api(f"repos/{args.repo}/actions/runs/{run['id']}/jobs?per_page=50").get("jobs", [])
        runs.append((run, jobs))
    print("| Événement | Classe | Runs | Durée totale médiane (min) | Étendue | Job de tests médian (min) |")
    print("|---|---|---|---|---|---|")
    for (event, klass), stats in summarize(runs).items():
        n, med, lo, hi = stats["total"]
        _, tmed, _, _ = stats["tests"]
        print(f"| {event} | {klass} | {n} | {med / 60:.1f} | {lo / 60:.1f} – {hi / 60:.1f} | {tmed / 60:.1f} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
