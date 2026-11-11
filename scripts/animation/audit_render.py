"""Audit de reproductibilité, de cache et de coût d'un rendu 2D (#168) — aucun appel payant.

    # rapport JSON + HTML d'un dossier de rendu (tâche du worker ou démo versionnée)
    uv run --no-sync python scripts/animation/audit_render.py report docs/animation/voltix-150 \\
        --out /tmp/audit-150 [--previous-storyboard ancien.json] [--pricing pricing.toml]

    # ce qu'une retouche ferait refaire (clé actuelle vs clé par dépendance)
    uv run --no-sync python scripts/animation/audit_render.py diff ancien.json nouveau.json

    # mesurer une étape locale (durée, CPU, RAM max) dans <dossier>/metrics.json
    uv run --no-sync python scripts/animation/audit_render.py measure --stage voice --metrics /tmp/t/metrics.json \\
        -- piper --model … --output_file voice.wav

    # intermédiaires temporaires des tâches terminées : simulation par défaut, --apply pour supprimer
    uv run --no-sync python scripts/animation/audit_render.py clean <dossier de données> [--apply]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT)]

from lody.animation import render_audit  # noqa: E402


def _load(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("storyboard"), dict):  # job.json accepté
        data = data["storyboard"]
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="rapport JSON + HTML d'un dossier de rendu")
    report.add_argument("directory")
    report.add_argument("--out", help="dossier où écrire audit.json et audit.html (sinon JSON sur la sortie)")
    report.add_argument("--previous-storyboard", help="storyboard précédent : ajoute l'analyse d'invalidation")
    report.add_argument("--pricing", help="fichier de tarifs (format pricing.toml) pour les estimations")
    diff = sub.add_parser("diff", help="invalidations entre deux storyboards")
    diff.add_argument("old")
    diff.add_argument("new")
    measure = sub.add_parser("measure", help="mesure une commande locale (durée, CPU, RAM max)")
    measure.add_argument("--stage", required=True, choices=[key for key, _, _ in render_audit.STAGES])
    measure.add_argument("--metrics", required=True, help="fichier metrics.json (créé ou complété)")
    measure.add_argument("cmd", nargs=argparse.REMAINDER)
    clean = sub.add_parser("clean", help="intermédiaires temporaires des tâches terminées")
    clean.add_argument("data_dir")
    clean.add_argument("--apply", action="store_true", help="supprimer réellement (sinon simulation)")
    args = parser.parse_args(argv)

    if args.command == "report":
        book = None
        if args.pricing:
            from lody.generation.costing import load_price_book

            book = load_price_book(Path(args.pricing))
        previous = _load(args.previous_storyboard) if args.previous_storyboard else None
        result = render_audit.audit_render(Path(args.directory), book=book, previous_storyboard=previous)
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
            (out / "audit.html").write_text(render_audit.to_html(result), encoding="utf-8")
            print(f"{out / 'audit.json'}\n{out / 'audit.html'}")
            for line in result["recommendations"]:
                print(f"- {line}")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    if args.command == "diff":
        print(json.dumps(render_audit.invalidation(_load(args.old), _load(args.new)), ensure_ascii=False, indent=1))
        return 0
    if args.command == "measure":
        command = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
        if not command:
            parser.error("commande à mesurer manquante (après --)")
        recorder = render_audit.StageRecorder(Path(args.metrics))
        code = render_audit.measure(command, recorder, args.stage)
        print(json.dumps(recorder.data["stages"][args.stage], ensure_ascii=False))
        return code
    result = render_audit.clean(Path(args.data_dir), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
