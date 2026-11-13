#!/usr/bin/env python3
"""Classification `ux-safe` d'un changement (ticket #221) : décidée par le CODE, jamais par un label.

Un changement est `ux-safe` seulement s'il ne touche que l'interface (vues, CSS, textes, navigation)
et ses tests, sans rien qui puisse modifier des données, des droits, de l'infrastructure ou déclencher
un traitement payant. Liste BLANCHE de fichiers + contrôle du contenu AJOUTÉ (diff) + bornes de taille.
Tout ce qui n'est pas prouvé sûr est refusé (faux positifs acceptés : ils renvoient en revue humaine).

Utilisé par :
- la politique de fusion (`merge_policy.py`) pour autoriser une PR sans `merge:auto` quand le
  propriétaire a activé la livraison UX automatique ;
- le livreur côté hôte (`scripts/lody_ux_deliver.py`) sur la plage « version servie → main » :
  une seule modification non sûre dans la plage → aucune mise en ligne automatique.

    python3 deploy/lodyorch/ux_safe.py diff <ancêtre> <cible> [--repo DIR] [--json]   # lecture git seule

Module pur (aucune dépendance hors bibliothèque standard) : il est importé par le superviseur ET par
le livreur, avec des faits sous la même forme que l'API GitHub (`filename`, `status`, `patch`).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field

VERSION = 1

# Fichiers d'interface éligibles. Les vues qui lancent un traitement payant (production, rendu 2D,
# réédition, V2, estimation de coût) ou qui gèrent réglages / clés / système en sont EXCLUES.
UI_FILES = ("webui/lody/styles.css", "webui/lody/theme.py", "webui/lody/nav.py", "webui/lody/components.py",
            "webui/lody/project_list.py", "webui/lody/view_home.py", "webui/lody/view_project.py",
            "webui/lody/view_characters.py", "webui/lody/view_locations.py", "webui/lody/view_form.py",
            "webui/lody/view_kit.py", "webui/lody/view_tracking.py")
TEST_FILES = ("test/lody/test_*.py",)
DOC_FILES = ("docs/*.md",)
EXCLUDED_VIEWS = ("webui/lody/view_production.py", "webui/lody/view_animation_pilot.py", "webui/lody/view_reedit.py",
                  "webui/lody/view_v2.py", "webui/lody/view_estimate.py", "webui/lody/view_settings.py",
                  "webui/lody/view_system_settings.py", "webui/lody/app.py")

MAX_FILES = 15
MAX_CHANGED_LINES = 800

# Imports autorisés dans les lignes AJOUTÉES d'un fichier Python d'interface.
SAFE_IMPORTS = re.compile(
    r"^(__future__|streamlit(\.\w+)*|html|re|math|datetime|typing|dataclasses|functools|itertools|collections"
    r"|textwrap|unicodedata|string|enum|lody\.(components|theme|nav|project_list|catalog|projects|characters"
    r"|locations|templates|brief))$")
IMPORT_LINE = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\s+(.+)|import\s+(.+))$")
# Contenu ajouté interdit (insensible à la casse) : données, réseau, système, secrets, suppression,
# traitement payant. Volontairement large : un faux positif renvoie simplement en revue humaine.
FORBIDDEN = {
    "sql-ou-base": r"\b(select|insert|update|delete|create|alter|drop|pragma)\s+(from|into|table|index|\w+\s+set)\b"
                   r"|\bsqlite3?\b|\.execute(many|script)?\s*\(|\bdb\.|\bmigrat",
    "systeme-ou-reseau": r"\bsubprocess\b|\bos\.(system|popen|remove|unlink|rmdir|environ|getenv)\b|\bshutil\b"
                         r"|\brequests\b|\bhttpx\b|\burllib\b|\bsocket\b|\bboto3?\b|https?://(?!github\.com/ReC82)",
    "fichiers": r"\bopen\s*\(|\.write_(text|bytes)\s*\(|\.unlink\s*\(|\brmtree\b|\bPath\s*\(",
    "suppression": r"\b(delete|remove|purge|erase|drop)_\w*\s*\(",
    "code-dynamique": r"(?<!\.)\b(eval|exec|compile)\s*\(|\b(__import__|importlib|pickle|marshal)\b",
    "secrets-auth": r"\bst\.secrets\b|\bsecret|\bpassword|\bapi[_-]?key|\btoken\b|\bcredential|\bauth\w*\s*\(",
    "traitement-payant": r"\b(openai|elevenlabs|anthropic|generation|animation|voice_\w+|synthes\w*|render_video"
                         r"|start_production|launch_\w+|enqueue\w*|submit_\w+)\b",
    "html-actif": r"<\s*(script|iframe|object|embed|form)\b|\bon(click|load|error)\s*=|javascript:"
                  r"|unsafe_allow_javascript|components\.v1|\bst\.components\b",
}
FORBIDDEN_RE = {k: re.compile(v, re.I) for k, v in FORBIDDEN.items()}
# Dans les tests et la documentation, seuls les motifs de vrais secrets sont recherchés.
SECRET_LIKE = re.compile(r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)")


@dataclass
class Verdict:
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    changed_lines: int = 0
    version: int = VERSION

    def summary(self):
        if self.eligible:
            return f"ux-safe : {len(self.files)} fichier(s), {self.changed_lines} ligne(s) — interface seule"
        return "non ux-safe : " + "; ".join(self.reasons[:6]) + (" …" if len(self.reasons) > 6 else "")

    def to_dict(self):
        return asdict(self)


def _match(path, patterns):
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def kind_of(path):
    """`ui`, `test`, `doc` ou None (hors liste blanche)."""
    if path in EXCLUDED_VIEWS:
        return None
    if path in UI_FILES:
        return "ui"
    if _match(path, TEST_FILES):
        return "test"
    if _match(path, DOC_FILES):
        return "doc"
    return None


def _added(patch):
    return [line[1:] for line in (patch or "").splitlines() if line.startswith("+") and not line.startswith("+++")]


def _removed_count(patch):
    return sum(1 for line in (patch or "").splitlines() if line.startswith("-") and not line.startswith("---"))


def _imports(line):
    m = IMPORT_LINE.match(line)
    if not m:
        return []
    names = [part.strip().split(" ")[0] for part in (m.group(2) or m.group(3)).strip("()\\ ").split(",")
             if part.strip().strip("()")]
    if not m.group(1):
        return names
    # `from lody import catalog` : chaque nom importé est un module du paquet, vérifié un par un.
    return [f"lody.{n}" for n in names] if m.group(1) == "lody" else [m.group(1)]


def _strip_comment(line):
    """Commentaire Python retiré (approximatif) : un mot interdit dans un commentaire n'exécute rien."""
    return line.split("#", 1)[0] if "#" in line and not line.lstrip().startswith(("'", '"')) else line


def classify(files):
    """Verdict sur une liste de fichiers modifiés (`filename`, `status`, `patch`, éventuellement
    `previous_filename`). Tous les motifs de refus sont collectés (pas d'arrêt au premier)."""
    reasons, names, changed = [], [], 0
    if not files:
        return Verdict(False, ["aucun fichier modifié"], [], 0)
    for f in files:
        path = f.get("filename") or ""
        names.append(path)
        status = f.get("status") or "modified"
        patch = f.get("patch")
        kind = kind_of(path)
        if kind is None:
            reasons.append(f"{path} : hors liste blanche UI/CSS/texte")
            continue
        if status in ("removed", "renamed") or f.get("previous_filename"):
            reasons.append(f"{path} : {status} (suppression/renommage exclus)")
            continue
        if patch is None:
            reasons.append(f"{path} : diff illisible (binaire ou trop gros)")
            continue
        added = _added(patch)
        changed += len(added) + _removed_count(patch)
        text = "\n".join(added)
        if SECRET_LIKE.search(text):
            reasons.append(f"{path} : motif de secret")
        if kind != "ui" or not path.endswith(".py"):
            if kind == "ui" and FORBIDDEN_RE["html-actif"].search(text):
                reasons.append(f"{path} : html-actif")
            continue
        bad_imports = sorted({mod for line in added for mod in _imports(line) if not SAFE_IMPORTS.match(mod)})
        if bad_imports:
            reasons.append(f"{path} : import non autorisé ({', '.join(bad_imports[:4])})")
        code = "\n".join(_strip_comment(line) for line in added if not IMPORT_LINE.match(line))
        hits = sorted(k for k, rx in FORBIDDEN_RE.items() if rx.search(code))
        if hits:
            reasons.append(f"{path} : contenu sensible ({', '.join(hits)})")
    if not any(kind_of(n) == "ui" for n in names):
        reasons.append("aucun fichier d'interface : rien à mettre en ligne")
    if len(files) > MAX_FILES:
        reasons.append(f"{len(files)} fichiers (max {MAX_FILES})")
    if changed > MAX_CHANGED_LINES:
        reasons.append(f"{changed} lignes modifiées (max {MAX_CHANGED_LINES})")
    return Verdict(not reasons, reasons, names, changed)


# --- Lecture git (livreur côté hôte, et vérification manuelle) -----------------------------------

def git_files(base, target, repo=".", git="git"):
    """Fichiers de `base..target` sous la forme de l'API GitHub, depuis le dépôt local (lecture seule).
    `-M` détecte les renommages ; `--no-ext-diff --no-textconv` : aucun programme tiers exécuté."""
    def run(*args):
        return subprocess.run([git, "-C", repo, *args], capture_output=True, text=True, check=True,
                              timeout=120).stdout
    out = []
    for row in run("diff", "--no-ext-diff", "--no-textconv", "-M", "--name-status", base, target).splitlines():
        parts = row.split("\t")
        code = parts[0][:1]
        status = {"A": "added", "M": "modified", "D": "removed", "R": "renamed", "C": "copied", "T": "modified"
                  }.get(code, "modified")
        f = {"filename": parts[-1], "status": status}
        if code in ("R", "C"):
            f["previous_filename"] = parts[1]
        if code != "D":
            patch = run("diff", "--no-ext-diff", "--no-textconv", "-U0", base, target, "--", parts[-1])
            f["patch"] = None if "Binary files" in patch and "@@" not in patch else patch
        out.append(f)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Classification ux-safe d'une plage git (lecture seule, #221).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff")
    d.add_argument("base")
    d.add_argument("target")
    d.add_argument("--repo", default=".")
    d.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    verdict = classify(git_files(args.base, args.target, args.repo))
    print(json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2) if args.json else verdict.summary())
    return 0 if verdict.eligible else 1


if __name__ == "__main__":
    sys.exit(main())
