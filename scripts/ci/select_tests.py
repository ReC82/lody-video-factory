#!/usr/bin/env python3
"""#198 : choisit les tests pytest à lancer pour un ensemble de fichiers modifiés.

Utilisé par `.github/workflows/ci.yml` (job « Detect code changes ») et, en local, par les sessions de
développement pour lancer EXACTEMENT la sélection que la CI lancera :

    python3 scripts/ci/select_tests.py --base origin/main          # branche + copie de travail locale
    python3 scripts/ci/select_tests.py --files webui/lody/ui.py    # fichiers donnés

Trois modes :

- ``docs``     : seulement de la documentation Markdown → tests qui lisent la documentation ;
- ``targeted`` : seulement des zones isolées (interface Lody, orchestrateur, docs) → leurs dossiers de tests ;
- ``full``     : tout le reste → suite complète (``test``) et smoke Windows.

Principe de sûreté : **tout fichier non reconnu déclenche la suite complète**. Seuls les chemins listés dans
``ISOLATED_RULES`` sont ciblés ; ils ne sont importés ni par le moteur (`app/`) ni par l'ancienne WebUI. Un
diff illisible, vide, ou un événement autre que `pull_request` (push sur main, programmation, lancement
manuel) donne aussi la suite complète. Stdlib uniquement : le script tourne avant l'installation des
dépendances.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Version de production (Dockerfile.lody : python:3.12-slim) : seule version testée sur PR.
PR_PYTHONS = ["3.12"]
# Compatibilité (requires-python >= 3.11) : sur main, programmation et lancement manuel.
FULL_PYTHONS = ["3.11", "3.12", "3.13"]

DOCS = re.compile(r"^([^/]+\.md|docs/.*\.md)$")

# (motif, dossiers de tests, raison). L'ordre compte : la première règle qui correspond s'applique.
ISOLATED_RULES: list[tuple[re.Pattern[str], tuple[str, ...], str]] = [
    # Interface Lody : paquet `lody` (webui/lody), point d'entrée Streamlit, tests et faux clients Lody.
    (re.compile(r"^webui/lody/"), ("test/lody",), "interface Lody"),
    (re.compile(r"^webui/Lody\.py$"), ("test/lody",), "point d'entrée Lody"),
    (re.compile(r"^test/lody/"), ("test/lody",), "tests Lody"),
    # Authentification admin Lody : testée dans test/lody (test_admin_auth*.py).
    (re.compile(r"^lody_auth/"), ("test/lody",), "authentification Lody"),
    (re.compile(r"^test/lody_auth/"), ("test/lody",), "tests authentification Lody"),
    # Rendu 2D : testé par test/lody (contrats, pilote) ; le rendu réel a son workflow `worker-2d.yml`.
    (re.compile(r"^renderers/remotion/"), ("test/lody",), "moteur de rendu 2D"),
    (re.compile(r"^scripts/animation/"), ("test/lody",), "scripts d'animation 2D"),
    (re.compile(r"^resource/animation-assets/"), ("test/lody",), "catalogue d'assets 2D"),
    # Orchestrateur : code et tests isolés du produit (aucun import de app/ ni de webui/).
    (re.compile(r"^deploy/lodyorch/"), ("test/lodyorch",), "orchestrateur"),
    (re.compile(r"^test/lodyorch/"), ("test/lodyorch",), "tests orchestrateur"),
]

# Fichiers qui imposent la suite complète même s'ils se trouvent dans une zone isolée : leur
# modification change la façon dont TOUS les tests s'exécutent.
CRITICAL = re.compile(r"^(test/__init__\.py|test/conftest\.py|conftest\.py|pytest\.ini|setup\.cfg)$")

# Motif des tests qui lisent la documentation (repris de #127, job « Documentation checks »).
DOC_READERS = re.compile(r"docs/|README|\.md\b|\"docs\"")


def doc_reader_tests(root: Path = ROOT) -> list[str]:
    found = []
    for path in sorted((root / "test").rglob("test_*.py")):
        if DOC_READERS.search(path.read_text(encoding="utf-8", errors="replace")):
            found.append(path.relative_to(root).as_posix())
    return found


def classify_file(path: str) -> tuple[str, tuple[str, ...], str]:
    """Retourne (catégorie, dossiers de tests, raison) ; catégorie ∈ docs | targeted | full."""
    if CRITICAL.match(path):
        return "full", ("test",), "configuration de test partagée"
    if DOCS.match(path):
        return "docs", (), "documentation"
    for pattern, suites, reason in ISOLATED_RULES:
        if pattern.match(path):
            return "targeted", suites, reason
    return "full", ("test",), "fichier partagé ou non classé"


def select(files: list[str] | None, event: str = "pull_request", root: Path = ROOT) -> dict:
    """Sélection pour une liste de fichiers (None = diff illisible)."""
    full = {"mode": "full", "tests": ["test"], "pythons": FULL_PYTHONS, "reasons": []}
    if event != "pull_request":
        return {**full, "reasons": [f"événement {event} : suite complète et matrice de compatibilité"]}
    full["pythons"] = PR_PYTHONS
    if files is None:
        return {**full, "reasons": ["diff illisible : suite complète par sécurité"]}
    files = [f.strip() for f in files if f.strip()]
    if not files:
        return {**full, "reasons": ["aucun fichier détecté : suite complète par sécurité"]}

    reasons, suites, categories = [], [], set()
    for path in files:
        category, paths, reason = classify_file(path)
        categories.add(category)
        reasons.append(f"{path} → {category} ({reason})")
        suites.extend(p for p in paths if p not in suites)
    if "full" in categories:
        return {**full, "reasons": reasons}
    tests = list(suites)
    if "docs" in categories:
        tests += [t for t in doc_reader_tests(root) if not any(t.startswith(s + "/") for s in suites)]
    mode = "targeted" if "targeted" in categories else "docs"
    if not tests:
        return {**full, "reasons": reasons + ["sélection vide : suite complète par sécurité"]}
    return {"mode": mode, "tests": tests, "pythons": PR_PYTHONS, "reasons": reasons}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
                          timeout=60).stdout


def changed_files(base: str, head: str | None) -> list[str] | None:
    """Fichiers modifiés entre la base et `head` (CI) ; sans `head` (usage local), entre le point de
    divergence avec la base et la copie de travail, fichiers non suivis compris."""
    try:
        if head:
            return _git("diff", "--name-only", f"{base}...{head}").splitlines()
        merge_base = _git("merge-base", base, "HEAD").strip()
        tracked = _git("diff", "--name-only", merge_base).splitlines()
        untracked = _git("ls-files", "--others", "--exclude-standard").splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    return sorted(set(tracked) | set(untracked))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--event", default="pull_request")
    parser.add_argument("--base", help="révision de base (ex. origin/main)")
    parser.add_argument("--head", help="révision de tête (CI) ; absent : copie de travail locale")
    parser.add_argument("--files", nargs="*", help="fichiers modifiés (au lieu de git diff)")
    parser.add_argument("--github-output", help="fichier $GITHUB_OUTPUT à compléter")
    args = parser.parse_args(argv)

    if args.files is not None:
        files = args.files
    elif args.event == "pull_request" and args.base:
        files = changed_files(args.base, args.head)
    elif args.event == "pull_request":
        parser.error("--base ou --files requis pour une pull request")
    else:
        files = []
    result = select(files, args.event)

    for line in result["reasons"]:
        print(line)
    print(f"mode={result['mode']}")
    print(f"tests={' '.join(result['tests'])}")
    print(f"pythons={','.join(result['pythons'])}")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(f"mode={result['mode']}\n")
            fh.write(f"tests={' '.join(result['tests'])}\n")
            fh.write(f"pythons={json.dumps(result['pythons'])}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
