"""Réédition ciblée (#121) — outil DEV, local et gratuit : plan d'une V2 et contrôle qualité d'un rendu.

    # Ce qu'une V2 refait / réutilise, et pourquoi (aucun rendu, aucun appel) :
    python scripts/animation/reedit.py plan --before v1.json --after v2.json [--manifest <tâche V1>/manifest.json]
                                            [--voice] [--json]

    # Contrôle qualité d'un dossier de tâche déjà rendu (écrit qa.json, n'altère rien d'autre) :
    python scripts/animation/reedit.py qa <dossier de la tâche> [--json]

``--before`` / ``--after`` acceptent un storyboard ``lody.animation/1`` ou un ``job.json`` de tâche (son
storyboard figé est utilisé). ``--voice`` : une voix enregistrée sera utilisée (décalages de voix signalés).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT)]

from lody.animation import qa, reedit  # noqa: E402
from lody.animation import worker as w  # noqa: E402
from lody.animation.contract import AnimationValidationError, validate_document  # noqa: E402


def load_storyboard(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("schema") == w.JOB_SCHEMA:
        data = data.get("storyboard")
    return validate_document(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="plan de réédition V1 → V2")
    plan.add_argument("--before", type=Path, required=True)
    plan.add_argument("--after", type=Path, required=True)
    plan.add_argument("--manifest", type=Path, help="manifest.json de la V1 (temps de rendu mesurés)")
    plan.add_argument("--voice", action="store_true", help="une voix enregistrée accompagne le rendu")
    plan.add_argument("--json", action="store_true")
    check = commands.add_parser("qa", help="contrôle qualité d'une tâche rendue")
    check.add_argument("directory", type=Path)
    check.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.command == "plan":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
            result = reedit.plan(load_storyboard(args.before), load_storyboard(args.after),
                                 previous_manifest=manifest, has_voice=args.voice)
            print(json.dumps(result, ensure_ascii=False, indent=1) if args.json else reedit.describe(result))
            return 0
        job = w.load_job(args.directory)
        traces = [w.read_json(path) or {} for path in sorted((args.directory / "scenes").glob("[0-9][0-9].json"))]
        report = qa.run(w.ffmpeg_exe() or "", args.directory, job["storyboard"], traces)
        w.write_json(args.directory / "qa.json", report)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=1))
        else:
            print(f"Contrôle qualité : {report['status']} {report['counts']}")
            for item in report["checks"]:
                print(f"  [{item['level']}] {item['message']}")
        return 0 if report["status"] != "error" else 1
    except (OSError, ValueError, AnimationValidationError, w.JobError) as error:
        print(f"erreur : {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
