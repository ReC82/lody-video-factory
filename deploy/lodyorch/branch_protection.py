#!/usr/bin/env python3
"""Protection de la branche `main` exigeant une CI réellement stable (ticket #200).

    python3 deploy/lodyorch/branch_protection.py            # À BLANC : état actuel + requête proposée
    python3 deploy/lodyorch/branch_protection.py --apply    # applique (propriétaire, compte admin)

Règle des checks requis : on n'exige QUE des vérifications présentes sur CHAQUE PR, quel que soit le
classement des fichiers. Les jobs conditionnels (`Documentation checks`, `Python 3.x tests`, `Windows
smoke tests`) changent de nom ou disparaissent selon la PR et selon #198 : les exiger bloquerait des
PR pour un check « absent ». L'agrégateur `CI result` de #198 (PR #201), lancé `if: always()`, est le
seul candidat stable ; tant qu'il n'est pas sur `main`, le script refuse d'appliquer (sauf `--contexts`).

`enforce_admins` est activé : lodyorch utilise le compte du propriétaire (administrateur) ; sans ce
réglage, la protection ne s'appliquerait ni au propriétaire ni au superviseur. Aucune revue requise
(un compte ne peut pas approuver sa propre PR). Jamais lancé par une session Claude.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPO = os.environ.get("LQ_REPO", "ReC82/lody-video-factory")
BRANCH = "main"
STABLE_CANDIDATES = ("CI result",)


def gh(*args, payload=None):
    proc = subprocess.run([os.environ.get("LQ_GH", "gh"), "api", *args], capture_output=True, text=True,
                          timeout=60, input=json.dumps(payload) if payload is not None else None)
    return proc.returncode, proc.stdout, proc.stderr


def main_check_names():
    code, out, err = gh("-X", "GET", f"repos/{REPO}/commits/{BRANCH}/check-runs?per_page=100")
    if code != 0:
        raise SystemExit(f"Lecture des checks de {BRANCH} impossible : {err.strip()[:200]}")
    return {r.get("name") for r in json.loads(out).get("check_runs") or []}


def protection_payload(contexts):
    return {
        "required_status_checks": {"strict": False, "contexts": list(contexts)},
        "enforce_admins": True,
        "required_pull_request_reviews": None,
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_linear_history": False,
    }


def choose_contexts(names, forced=None):
    """Checks requis : forcés explicitement, sinon les candidats stables présents sur main."""
    if forced:
        return list(forced)
    return [c for c in STABLE_CANDIDATES if c in names]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Protection de main (#200) — à blanc par défaut.")
    ap.add_argument("--apply", action="store_true", help="appliquer réellement (compte administrateur)")
    ap.add_argument("--contexts", nargs="+", help="checks requis imposés (sinon : agrégateur stable)")
    args = ap.parse_args(argv)
    code, out, _ = gh("-X", "GET", f"repos/{REPO}/branches/{BRANCH}/protection")
    print(f"Protection actuelle de {BRANCH} : " + ("aucune" if code != 0 else out.strip()[:500]))
    names = main_check_names()
    contexts = choose_contexts(names, args.contexts)
    if not contexts:
        print(f"Aucun check stable sur {BRANCH} ({', '.join(STABLE_CANDIDATES)} absent) : attendre la fusion de "
              "#198 (agrégateur « CI result ») ou imposer --contexts. Rien n'est appliqué.")
        return 2
    payload = protection_payload(contexts)
    print("Requête proposée : PUT " + f"repos/{REPO}/branches/{BRANCH}/protection\n" + json.dumps(payload, indent=2))
    if not args.apply:
        print("À blanc : rien n'est modifié (relancer avec --apply).")
        return 0
    code, _, err = gh("-X", "PUT", f"repos/{REPO}/branches/{BRANCH}/protection", "--input", "-", payload=payload)
    print("Protection appliquée." if code == 0 else f"Échec : {err.strip()[:300]}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
