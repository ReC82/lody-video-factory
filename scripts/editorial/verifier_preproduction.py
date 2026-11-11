#!/usr/bin/env python3
"""Contrôle éditorial d'un épisode AVANT génération (#167) — hors réseau, aucune correction automatique.

Usage :
    python scripts/editorial/verifier_preproduction.py docs/editorial/hdmi-vers-sdi-novice.preprod.json
    python scripts/editorial/verifier_preproduction.py storyboard.json --brief brief.json   # lody.animation/1
    … --json            # résultat machine (verdict, alertes, durées)
    … --sortie rapport.md

Code de sortie : 0 = prêt ou à relire, 1 = à corriger (alerte bloquante), 2 = fichier invalide.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT)]

from lody.editorial.checks import check_document  # noqa: E402
from lody.editorial.preprod import (  # noqa: E402
    ANIMATION_SCHEMA,
    PreprodError,
    from_storyboard,
    storyboard_path,
    validate_preprod,
)
from lody.editorial.report import to_markdown  # noqa: E402


def load(path: Path, brief_path: Path | None) -> tuple[dict, bool]:
    """(document de préproduction, vient d'un storyboard animé ?)"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and raw.get("schema") == ANIMATION_SCHEMA:
        from lody.animation.contract import validate_document

        if brief_path is None:
            raise PreprodError({"--brief": "un storyboard animé se contrôle avec son brief éditorial (--brief)."})
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
        return from_storyboard(validate_document(raw), brief.get("brief", brief)), True
    return validate_preprod(raw), False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fichier", type=Path, help="document lody.preprod/1 ou storyboard lody.animation/1")
    parser.add_argument("--brief", type=Path, help="brief éditorial (JSON) pour un storyboard animé")
    parser.add_argument("--json", action="store_true", help="sortie JSON au lieu du Markdown")
    parser.add_argument("--sortie", type=Path, help="écrire le résultat dans ce fichier")
    args = parser.parse_args(argv)
    try:
        document, from_animation = load(args.fichier, args.brief)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Fichier invalide : {exc}", file=sys.stderr)
        return 2
    report = check_document(document)
    if from_animation:  # les alertes renvoient aux champs du storyboard d'origine
        report.findings = [replace(f, path=storyboard_path(f.path)) for f in report.findings]
    text = (json.dumps(report.as_dict(), ensure_ascii=False, indent=2) if args.json
            else to_markdown(document, report))
    if args.sortie:
        args.sortie.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 1 if report.verdict == "à corriger" else 0


if __name__ == "__main__":
    sys.exit(main())
