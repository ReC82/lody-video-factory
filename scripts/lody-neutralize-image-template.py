#!/usr/bin/env python3
"""Neutralise le gabarit d'images GLOBAL du moteur (``[app].openai_image_prompt_template``).

Ce gabarit est ajouté par le moteur à chaque image de chaque projet : un gabarit « broadcast » influence alors
aussi les projets crypto. Ce script le remplace par une chaîne vide, de façon sûre :

* sauvegarde datée de config.toml (0600, ignorée par Git) avant toute écriture ;
* la nouvelle config est validée (TOML) et toutes les AUTRES valeurs doivent être strictement identiques ;
* écriture atomique, permissions conservées ; aucune valeur n'est affichée.

Puis : redémarrer l'API du moteur et relancer ``./scripts/lody-engine-report.sh``.

    python3 scripts/lody-neutralize-image-template.py [--config config.toml] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path

KEY = "openai_image_prompt_template"
_LINE = re.compile(rf"^(\s*{KEY}\s*=\s*)(\"(?:[^\"\\]|\\.)*\"|'[^']*')(\s*(?:#.*)?)$", re.MULTILINE)


def neutralize(text: str) -> tuple[str, bool]:
    """(nouveau texte, changé ?). Ne touche qu'à la ligne du gabarit ; la valeur n'est jamais retournée."""
    match = _LINE.search(text)
    if not match:
        return text, False
    already = match.group(2)[1:-1].strip() == ""
    if already:
        return text, False
    return text[: match.start()] + f'{match.group(1)}""{match.group(3)}' + text[match.end():], True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--dry-run", action="store_true", help="vérifie sans rien écrire")
    args = parser.parse_args(argv)
    try:
        original = args.config.read_text(encoding="utf-8")
        before = tomllib.loads(original)
    except (OSError, tomllib.TOMLDecodeError) as error:
        print(f"Impossible de lire {args.config} : {type(error).__name__}")
        return 1
    updated, changed = neutralize(original)
    if not changed:
        present = KEY in before.get("app", {})
        print("Rien à faire : " + ("le gabarit d'images est déjà vide." if present else "aucun gabarit d'images n'est défini."))
        return 0
    after = tomllib.loads(updated)
    expected = {**before, "app": {**before["app"], KEY: ""}}
    if after != expected:  # toute autre différence = on n'écrit rien
        print("Vérification échouée : la modification toucherait autre chose que le gabarit. Rien n'a été écrit.")
        return 1
    if args.dry_run:
        print("Simulation : le gabarit d'images serait remplacé par une chaîne vide ; tout le reste est identique.")
        return 0
    backup = args.config.with_name(f"{args.config.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(args.config, backup)
    os.chmod(backup, 0o600)
    mode = args.config.stat().st_mode & 0o777
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.config.parent, delete=False, suffix=".tmp") as handle:
        handle.write(updated)
        temp = Path(handle.name)
    os.chmod(temp, mode)
    os.replace(temp, args.config)
    print(f"Gabarit d'images neutralisé. Sauvegarde : {backup}")
    print("Suite : redémarrer l'API du moteur, puis ./scripts/lody-engine-report.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
