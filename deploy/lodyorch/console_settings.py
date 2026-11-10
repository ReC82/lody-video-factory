#!/usr/bin/env python3
"""Ajoute les crochets LODYORCH aux réglages LOCAUX de la console (`.claude/settings.local.json` du
dépôt où tourne la session tmux `lodyorch`), sans rien retirer ni remplacer :
- UserPromptSubmit → `lodyorch human busy` (le propriétaire a la main : la file se range) ;
- Stop → `lodyorch human idle` (Claude a répondu : la file reprend après le délai de calme) ;
- statusLine → `lodyorch statusline`, seulement si aucune ligne d'état n'est déjà configurée.
Le fichier est exclu de Git (`.git/info/exclude`) s'il ne l'est pas déjà. Idempotent.

    console_settings.py <dépôt> <console-settings.json> [--dry-run]
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def merge(current, wanted):
    """Réglages fusionnés et liste des changements (vide si rien à faire)."""
    out = json.loads(json.dumps(current))
    changes = []
    hooks = out.setdefault("hooks", {})
    for event, groups in wanted.get("hooks", {}).items():
        have = {h.get("command") for g in hooks.get(event, []) for h in g.get("hooks", [])}
        for group in groups:
            if all(h["command"] in have for h in group["hooks"]):
                continue
            hooks.setdefault(event, []).append(group)
            changes.append(f"crochet {event} ajouté")
    if "statusLine" in wanted:
        if "statusLine" not in out:
            out["statusLine"] = wanted["statusLine"]
            changes.append("ligne d'état ajoutée")
        elif out["statusLine"] != wanted["statusLine"]:
            changes.append("ligne d'état existante conservée (non remplacée)")
    return out, changes


def main(argv):
    repo, wanted_path = Path(argv[0]), Path(argv[1])
    dry = "--dry-run" in argv
    target = repo / ".claude" / "settings.local.json"
    current = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    merged, changes = merge(current, json.loads(wanted_path.read_text(encoding="utf-8")))
    real = [c for c in changes if "conservée" not in c]
    for c in changes:
        print(f"   {c}")
    if not real:
        print(f"   {target} : déjà à jour")
    elif not dry:
        target.parent.mkdir(exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        print(f"   {target} mis à jour")
    ignored = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", str(target)]).returncode == 0
    if not ignored and (repo / ".git").is_dir():
        exclude = repo / ".git" / "info" / "exclude"
        print(f"   {exclude} : ajout de /.claude/settings.local.json")
        if not dry:
            exclude.parent.mkdir(exist_ok=True)
            with open(exclude, "a", encoding="utf-8") as fh:
                fh.write("\n/.claude/settings.local.json\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
