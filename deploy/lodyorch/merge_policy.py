#!/usr/bin/env python3
"""Politique de fusion automatique des PR (ticket #193 phase 1, #200 phase 2).

Ce module DÉCIDE si une PR est fusionnable automatiquement selon la politique de
`docs/lody-fusion-auto.md`. En ligne de commande il ne lit GitHub qu'en lecture seule (`gh api` en
GET) et ne fusionne rien. Seul le superviseur lodyorch (`watch_ci`, mode `on`) appelle `merge()`,
après `revalidate()` et avec le SHA attendu ; jamais une session Claude (crochet de garde), jamais
de déploiement.

    python3 deploy/lodyorch/merge_policy.py evaluate 190          # matrice de décision d'une PR
    python3 deploy/lodyorch/merge_policy.py evaluate 190 --json   # même chose, en JSON
    python3 deploy/lodyorch/merge_policy.py metrics --limit 20    # attente « CI verte → fusion »

Toutes les règles sont des fonctions pures (`evaluate(facts)`), testées sur des PR factices.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime

POLICY_VERSION = 2
REPO = os.environ.get("LQ_REPO", "ReC82/lody-video-factory")
DEFAULT_BRANCH = "main"

# Autorisation : label posé sur le TICKET par un acteur de confiance (journal des événements GitHub).
AUTHORIZE_LABEL = "merge:auto"
# Signaux humains bloquants : sur le ticket OU la PR, ils prévalent toujours sur l'autorisation.
VETO_LABELS = ("merge:manual", "agent:blocked", "agent:human", "do-not-merge")
REQUIRED_TICKET_LABEL = "agent:auto"
# Comptes autorisés à poser `merge:auto` et à auteur·ice·s de PR acceptées (compte de lodyorch).
TRUSTED_ACTORS = tuple(a for a in os.environ.get("LQ_MERGE_TRUSTED", REPO.split("/")[0]).split(",") if a)

# Vérifications CI requises sur le SHA de tête (noms des jobs de .github/workflows/ci.yml).
CHECK_DETECT = "Detect code changes"
CHECKS_CODE = ("Python 3.11 tests", "Python 3.13 tests", "Windows smoke tests")
CHECK_DOCS = "Documentation checks"
# Agrégateur de #198 (PR #201) : quand il est présent sur le SHA de tête, c'est LUI le verdict CI
# (jobs conditionnels résumés en un check toujours présent) ; sinon, jobs historiques ci-dessus.
CHECK_AGGREGATE = "CI result"
CHECK_OK = ("success",)
CHECK_OK_IF_NOT_REQUIRED = ("success", "skipped", "neutral")
# Même classement que ci.yml : seuls ces fichiers rendent une PR « purement documentaire ».
DOCS_ONLY = re.compile(r"^([^/]+\.md|docs/.*\.md)$")

# Exclusions par défaut (validation explicite du propriétaire, même avec CI verte).
EXCLUDED_PATHS = {
    "infra-ci-cd": (".github/*", "Dockerfile*", "docker-compose*", "deploy/*", "scripts/*",
                    "renderers/*", "*.sh", "*.service"),
    "dependances": ("pyproject.toml", "uv.lock", "requirements*.txt", "package.json", "package-lock.json"),
    "auth-secrets": ("lody_auth/*", "*secret*", "*auth*", "*password*", "*credential*", "*token*",
                     "config.example.toml", "app/config/*"),
    "donnees-migrations": ("webui/lody/db.py", "*migration*", "*.sql", "app/services/state.py"),
    "api-payantes-video": ("app/services/llm.py", "app/services/voice.py", "app/services/elevenlabs*",
                           "app/services/material*", "app/services/twelvelabs.py", "app/services/volcengine*",
                           "app/services/metaso*", "app/services/ofox.py", "app/services/sonilo.py",
                           "app/services/loomloom.py", "app/services/upload_post.py", "app/services/video.py",
                           "app/services/task.py", "webui/lody/generation/*", "webui/lody/animation/*",
                           "webui/lody/voice_*", "*elevenlabs*", "*openai*"),
}
# Contenu ajouté qui impose une validation explicite, quel que soit le fichier.
DESTRUCTIVE_SQL = re.compile(r"\b(DROP\s+(TABLE|COLUMN|INDEX)|DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE\s+\w+\s+"
                             r"(RENAME|DROP))\b", re.I)
ADDITIVE_SQL = re.compile(r"\b(CREATE\s+TABLE|ALTER\s+TABLE\s+\w+\s+ADD|CREATE\s+(UNIQUE\s+)?INDEX)\b", re.I)
PAID_HOSTS = re.compile(r"api\.(openai|elevenlabs|anthropic)\.|amazonaws\.com|\bboto3\b", re.I)
SECRET_LIKE = re.compile(r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)")

MAX_FILES = 40           # au-delà : changement de périmètre, revue humaine
MAX_CHANGED_LINES = 1500
MAX_MAIN_MERGES = 3      # « boucle de conflits » : main ré-intégré trop souvent dans la branche
MAX_BEHIND = 30          # base en retard tolérée (sans fichier commun) ; au-delà, mise à jour exigée
CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)\b", re.I)
BRANCH_TICKET = re.compile(r"^agent/(\d+)-")


@dataclass
class Check:
    code: str
    ok: bool
    detail: str


@dataclass
class Decision:
    pr: int
    head_sha: str
    ticket: int | None
    eligible: bool
    checks: list[Check] = field(default_factory=list)
    policy_version: int = POLICY_VERSION
    dry_run: bool = True  # phase 1 : jamais de fusion, quel que soit le verdict

    @property
    def refusals(self):
        return [c for c in self.checks if not c.ok]

    def merge_request(self):
        """Requête QU'UNE activation future enverrait (non exécutée ici) : `sha` rend la fusion atomique
        contre une course de SHA — GitHub refuse (409) si la tête a bougé depuis l'évaluation."""
        if not self.eligible:
            return None
        return {"method": "PUT", "path": f"repos/{REPO}/pulls/{self.pr}/merge",
                "fields": {"sha": self.head_sha, "merge_method": "merge"}}

    def to_dict(self):
        d = asdict(self)
        d["merge_request"] = self.merge_request()
        return d


def _labels(obj):
    return {(lbl.get("name") if isinstance(lbl, dict) else str(lbl)) for lbl in (obj or {}).get("labels") or []}


def ticket_of(pr):
    """Ticket lié : `Closes #N` du corps ET numéro de la branche `agent/N-…` doivent concorder."""
    body_refs = {int(n) for n in CLOSES.findall(pr.get("body") or "")}
    m = BRANCH_TICKET.match(((pr.get("head") or {}).get("ref")) or "")
    branch_ref = int(m.group(1)) if m else None
    if branch_ref is not None and branch_ref in body_refs:
        return branch_ref, ""
    if branch_ref is None:
        return None, "branche hors convention `agent/<ticket>-…`"
    return None, f"corps sans `Closes #{branch_ref}` (références : {sorted(body_refs) or 'aucune'})"


def excluded_categories(path):
    if DOCS_ONLY.match(path):  # documentation (racine, docs/) : jamais une zone sensible en soi
        return []
    return sorted(cat for cat, patterns in EXCLUDED_PATHS.items()
                  if any(fnmatch.fnmatch(path.lower(), p.lower()) for p in patterns))


def added_lines(patch):
    return "\n".join(line[1:] for line in (patch or "").splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def last_label_event(events, label):
    """DERNIER ajout encore valable de `label` (un retrait ultérieur l'annule), ou None."""
    found = None
    for ev in events or []:
        if ((ev.get("label") or {}).get("name")) != label:
            continue
        if ev.get("event") == "labeled":
            found = ev
        elif ev.get("event") == "unlabeled":
            found = None
    return found


def last_label_actor(events, label):
    """Auteur du DERNIER ajout encore valable de `label`."""
    ev = last_label_event(events, label)
    return ((ev or {}).get("actor") or {}).get("login")


def required_checks(files, names=()):
    if CHECK_AGGREGATE in names:
        return (CHECK_DETECT, CHECK_AGGREGATE)
    docs_only = bool(files) and all(DOCS_ONLY.match(f.get("filename", "")) for f in files)
    return (CHECK_DETECT, CHECK_DOCS) if docs_only else (CHECK_DETECT, *CHECKS_CODE)


def evaluate_base(compare, files):
    """Base en retard : tolérée seulement si `main` n'a touché AUCUN fichier de la PR depuis le point
    de divergence (pas de conflit sémantique possible sur ces fichiers) et dans une limite de commits.
    `compare` = `GET compare/<tête>...main` (`ahead_by` = commits de main absents de la PR)."""
    if not isinstance(compare, dict) or "ahead_by" not in compare:
        return Check("base-sans-chevauchement", False, "comparaison avec main indisponible")
    behind = int(compare.get("ahead_by") or 0)
    if behind == 0:
        return Check("base-sans-chevauchement", True, "branche à jour avec main")
    main_files = {f.get("filename") for f in compare.get("files") or []}
    if behind > MAX_BEHIND or len(main_files) >= 300:  # 300 : liste tronquée par GitHub
        return Check("base-sans-chevauchement", False, f"{behind} commit(s) de retard sur main (max {MAX_BEHIND}) : "
                                                       "intégrer main")
    common = sorted(main_files & {f.get("filename") for f in files})
    if common:
        return Check("base-sans-chevauchement", False,
                     f"{behind} commit(s) de retard ; main a modifié aussi : {', '.join(common[:5])}")
    return Check("base-sans-chevauchement", True, f"{behind} commit(s) de retard, aucun fichier commun")


def evaluate_ci(runs, files):
    """CI entièrement verte sur le SHA de tête : jobs requis réussis, aucun autre en échec ou en cours."""
    if not runs:
        return Check("ci", False, "aucune vérification sur le SHA de tête")
    latest = {}
    for r in runs:  # une relance remplace la précédente : garder la plus récente par nom
        name = r.get("name", "")
        if name not in latest or (r.get("started_at") or "") >= (latest[name].get("started_at") or ""):
            latest[name] = r
    pending = sorted(n for n, r in latest.items() if r.get("status") != "completed")
    if pending:
        return Check("ci", False, f"en cours : {', '.join(pending)}")
    required = required_checks(files, latest)
    missing = [n for n in required if n not in latest]
    bad_required = [f"{n}={latest[n].get('conclusion')}" for n in required
                    if n in latest and latest[n].get("conclusion") not in CHECK_OK]
    bad_other = [f"{n}={r.get('conclusion')}" for n, r in latest.items()
                 if n not in required and r.get("conclusion") not in CHECK_OK_IF_NOT_REQUIRED]
    if missing or bad_required or bad_other:
        parts = ([f"manquantes : {', '.join(missing)}"] if missing else []) + bad_required + bad_other
        return Check("ci", False, "; ".join(parts))
    return Check("ci", True, f"{len(latest)} vérification(s), requises vertes : {', '.join(required)}")


def ux_safe_verdict(files):
    """Classification `ux-safe` (#221), module installé à côté de celui-ci (import différé)."""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import ux_safe  # noqa: PLC0415
    return ux_safe.classify(files)


def evaluate(facts):
    """Décision cumulative. `facts` : pr, files, commits, reviews, check_runs, ticket, ticket_events,
    et facultativement `base_compare` (base en retard), `authorized_after` (horodatage ISO : une
    autorisation antérieure ne vaut rien — pas de fusion rétroactive à l'activation) et `ux_safe_auto`
    (#221 : une PR classée `ux-safe` par le code vaut autorisation, voir `ux_safe.py`).
    Chaque critère est évalué (pas d'arrêt au premier refus) pour une matrice complète et lisible."""
    pr = facts.get("pr") or {}
    files = facts.get("files") or []
    commits = facts.get("commits") or []
    ticket = facts.get("ticket") or {}
    head_sha = (pr.get("head") or {}).get("sha", "")
    n, why_ticket = ticket_of(pr)
    out = []

    def add(code, ok, detail="", info=False):
        """`detail` explique un refus ; `info=True` le garde aussi quand le critère est satisfait."""
        out.append(Check(code, bool(ok), detail if (info or not ok) else ""))

    # 1. État de la PR
    add("pr-ouverte", pr.get("state") == "open" and not pr.get("merged"),
        "déjà fusionnée" if pr.get("merged") else f"état {pr.get('state')}")
    add("pr-non-draft", not pr.get("draft"), "brouillon" if pr.get("draft") else "")
    base = pr.get("base") or {}
    add("base-main", base.get("ref") == DEFAULT_BRANCH, f"base {base.get('ref')}", info=True)
    head_repo = ((pr.get("head") or {}).get("repo") or {}).get("full_name")
    add("pas-de-fork", head_repo == REPO, f"tête dans {head_repo or 'dépôt inconnu'}", info=True)
    author = (pr.get("user") or {}).get("login")
    add("auteur-de-confiance", author in TRUSTED_ACTORS, f"auteur {author}", info=True)
    state = pr.get("mergeable_state")
    add("sans-conflit", pr.get("mergeable") is True and state not in ("dirty",),
        f"mergeable={pr.get('mergeable')} ({state})", info=True)
    add("base-a-jour", state == "clean", f"mergeable_state={state} (clean exigé : base à jour, rien de bloquant)", info=True)
    if "base_compare" in facts:
        out.append(evaluate_base(facts.get("base_compare"), facts.get("files") or []))

    # 2. Ticket et autorisation
    add("ticket-lie", n is not None, why_ticket or f"#{n}", info=True)
    t_labels = _labels(ticket)
    add("ticket-ouvert", ticket.get("state") == "open", f"ticket {ticket.get('state')}", info=True)
    add("ticket-agent-auto", REQUIRED_TICKET_LABEL in t_labels, f"labels ticket : {sorted(t_labels)}", info=True)
    auth = last_label_event(facts.get("ticket_events"), AUTHORIZE_LABEL) or {}
    actor = (auth.get("actor") or {}).get("login")
    by_label = AUTHORIZE_LABEL in t_labels and actor in TRUSTED_ACTORS
    after = facts.get("authorized_after")
    # #221 : livraison UX automatique activée par le propriétaire (`ux_safe_auto`) — une PR classée
    # `ux-safe` PAR LE CODE (diff réel) vaut autorisation sans `merge:auto`. Non rétroactif : la PR
    # doit être ouverte après l'activation du mode `on`. Vetos, CI et toutes les autres règles restent.
    ux = ux_safe_verdict(files) if facts.get("ux_safe_auto") and not by_label else None
    if ux is not None:
        add("classification-ux-safe", ux.eligible, ux.summary(), info=True)
        if after:
            opened = pr.get("created_at") or ""
            add("ux-safe-apres-activation", bool(opened) and opened >= after,
                f"PR ouverte le {opened or '?'} ; activation le {after} : aucune PR antérieure fusionnée sans `merge:auto`")
        after = None
    add("autorisation-merge-auto", by_label or bool(ux and ux.eligible),
        f"`{AUTHORIZE_LABEL}` " + (f"posé par {actor}" if actor else "absent ou posé par un compte non autorisé")
        + (" ; changement classé `ux-safe` par le code" if ux and ux.eligible else ""), info=True)
    if after:
        when = auth.get("created_at") or ""
        add("autorisation-apres-activation", bool(when) and when >= after,
            f"`{AUTHORIZE_LABEL}` posé le {when or '?'} ; activation le {after} : reposer le label pour autoriser")
    vetoes = sorted((t_labels | _labels(pr)) & set(VETO_LABELS))
    add("aucun-veto", not vetoes, f"veto : {', '.join(vetoes)}" if vetoes else "")
    latest_review = {}
    for rv in facts.get("reviews") or []:
        if rv.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest_review[(rv.get("user") or {}).get("login")] = rv.get("state")
    blocking = sorted(u for u, s in latest_review.items() if s == "CHANGES_REQUESTED")
    add("aucune-demande-de-modification", not blocking, f"demandées par {', '.join(blocking)}" if blocking else "")

    # 3. CI sur le SHA de tête
    out.append(evaluate_ci(facts.get("check_runs"), files))
    add("tests-modifies", any(f.get("filename", "").startswith("test/") for f in files)
        or all(DOCS_ONLY.match(f.get("filename", "")) for f in files),
        "code modifié sans test ajouté ou modifié")

    # 4. Périmètre et contenu sensible
    cats = {}
    for f in files:
        for cat in excluded_categories(f.get("filename", "")):
            cats.setdefault(cat, []).append(f["filename"])
    add("aucune-zone-exclue", not cats, "; ".join(f"{c} : {', '.join(v[:4])}" for c, v in sorted(cats.items())))
    removed = [f["filename"] for f in files if f.get("status") == "removed"]
    add("aucune-suppression-de-fichier", not removed, ", ".join(removed[:5]))
    text = "\n".join(added_lines(f.get("patch")) for f in files)
    no_patch = [f["filename"] for f in files if f.get("patch") is None and f.get("status") != "removed"]
    add("diff-lisible", not no_patch, f"diff non fourni (binaire/trop gros) : {', '.join(no_patch[:5])}")
    add("aucune-migration-destructive", not DESTRUCTIVE_SQL.search(text), "SQL destructif ajouté")
    add("aucune-migration-additive", not ADDITIVE_SQL.search(text),
        "migration additive : sauvegarde + tests + accord explicite exigés (politique à valider)")
    add("aucun-appel-payant-ou-aws", not PAID_HOSTS.search(text), "hôte payant/AWS ajouté")
    add("aucun-secret", not SECRET_LIKE.search(text), "motif de secret dans le diff")
    changed = int(pr.get("additions") or 0) + int(pr.get("deletions") or 0)
    count = int(pr.get("changed_files") or len(files))
    add("perimetre-borne", count <= MAX_FILES and changed <= MAX_CHANGED_LINES and count == len(files),
        f"{count} fichier(s), {changed} ligne(s) (max {MAX_FILES} / {MAX_CHANGED_LINES}, liste complète exigée)", info=True)
    main_merges = sum(1 for c in commits if len(c.get("parents") or []) > 1)
    add("pas-de-boucle-de-conflits", main_merges <= MAX_MAIN_MERGES,
        f"{main_merges} intégration(s) de main dans la branche (max {MAX_MAIN_MERGES})", info=True)
    add("sha-de-tete-coherent", bool(head_sha) and (not commits or (commits[-1].get("sha") == head_sha)),
        "la tête a bougé pendant la collecte : réévaluer")

    return Decision(pr=int(pr.get("number") or 0), head_sha=head_sha, ticket=n,
                    eligible=all(c.ok for c in out), checks=out)


def revalidate(decision, fresh_facts):
    """Revalidation IMMÉDIATE avant une future fusion : nouvelle évaluation + même SHA de tête."""
    again = evaluate(fresh_facts)
    if again.head_sha != decision.head_sha:
        again.checks.append(Check("sha-attendu", False,
                                  f"tête {again.head_sha[:10]} ≠ SHA évalué {decision.head_sha[:10]}"))
        again.eligible = False
    return again


def render(decision):
    verdict = ("REFUSÉE" if not decision.eligible else
               "ÉLIGIBLE (simulation : aucune fusion effectuée)" if decision.dry_run else "ÉLIGIBLE")
    lines = [f"PR #{decision.pr} — ticket #{decision.ticket} — tête {decision.head_sha[:10]} — "
             f"politique v{decision.policy_version}", f"Verdict : {verdict}", "",
             "| Critère | Résultat | Détail |", "|---|---|---|"]
    for c in decision.checks:
        lines.append(f"| {c.code} | {'ok' if c.ok else 'REFUS'} | {c.detail.replace('|', '/')} |")
    return "\n".join(lines)


# --- Lecture GitHub (GET uniquement) -------------------------------------------------------------

def gh_get(path):
    """`gh api` en lecture : la méthode est forcée à GET, aucun champ n'est envoyé."""
    proc = subprocess.run([os.environ.get("LQ_GH", "gh"), "api", "-X", "GET", path],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {path} : {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "null")


def _pages(get, path, limit=10):
    items = []
    for page in range(1, limit + 1):
        sep = "&" if "?" in path else "?"
        batch = get(f"{path}{sep}per_page=100&page={page}") or []
        if isinstance(batch, dict):  # check-runs
            batch = batch.get("check_runs") or []
        items.extend(batch)
        if len(batch) < 100:
            break
    return items


def collect(number, get=gh_get):
    pr = get(f"repos/{REPO}/pulls/{number}")
    sha = pr["head"]["sha"]
    n, _ = ticket_of(pr)
    facts = {
        "pr": pr,
        "files": _pages(get, f"repos/{REPO}/pulls/{number}/files", limit=30),
        "commits": _pages(get, f"repos/{REPO}/pulls/{number}/commits"),
        "reviews": _pages(get, f"repos/{REPO}/pulls/{number}/reviews"),
        "check_runs": _pages(get, f"repos/{REPO}/commits/{sha}/check-runs"),
        "ticket": get(f"repos/{REPO}/issues/{n}") if n else {},
        "ticket_events": _pages(get, f"repos/{REPO}/issues/{n}/events") if n else [],
        "base_compare": get(f"repos/{REPO}/compare/{sha}...{(pr.get('base') or {}).get('ref') or DEFAULT_BRANCH}"),
    }
    return facts


# --- Fusion (superviseur uniquement, mode `on`) ---------------------------------------------------

class MergeError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status  # code HTTP (405 non fusionnable, 409 tête modifiée…) ou None


def merge(decision, title=""):
    """`PUT …/pulls/<n>/merge` avec `sha` = SHA évalué : GitHub refuse (409) si la tête a bougé et
    (405) si la PR n'est plus fusionnable ou déjà fusionnée — aucune double fusion possible. Rend le
    SHA du commit de fusion ; lève MergeError sinon. Uniquement sur une décision éligible et non à blanc."""
    if not decision.eligible or decision.dry_run:
        raise MergeError(None, "décision non éligible ou à blanc : aucune fusion")
    req = decision.merge_request()
    args = [os.environ.get("LQ_GH", "gh"), "api", "-X", req["method"], req["path"]]
    for key, value in req["fields"].items():
        args += ["-f", f"{key}={value}"]
    if title:
        args += ["-f", f"commit_title={title}"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=90)
    except (subprocess.SubprocessError, OSError) as exc:
        raise MergeError(None, f"appel GitHub impossible : {type(exc).__name__}") from None
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        m = re.search(r"HTTP (\d{3})", err)
        raise MergeError(int(m.group(1)) if m else None, err[:300] or "échec sans message")
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    if not data.get("merged"):
        raise MergeError(None, f"réponse inattendue : {(proc.stdout or '')[:200]}")
    return data.get("sha", "")


def _ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def merge_wait(pr, runs):
    """Secondes entre la fin de la dernière vérification sur la tête fusionnée et la fusion."""
    merged = _ts(pr.get("merged_at"))
    done = [_ts(r.get("completed_at")) for r in runs if r.get("completed_at")]
    if not merged or not done:
        return None
    return max(0.0, (merged - max(done)).total_seconds())


def metrics(limit=20, get=gh_get):
    rows = []
    for pr in get(f"repos/{REPO}/pulls?state=closed&sort=updated&direction=desc&per_page={min(limit, 100)}") or []:
        if not pr.get("merged_at"):
            continue
        runs = _pages(get, f"repos/{REPO}/commits/{pr['head']['sha']}/check-runs", limit=1)
        wait = merge_wait(pr, runs)
        if wait is not None:
            rows.append({"pr": pr["number"], "title": pr.get("title", "")[:70], "wait_s": round(wait)})
    waits = [r["wait_s"] for r in rows]
    return {"prs": rows, "count": len(rows),
            "median_wait_s": round(statistics.median(waits)) if waits else None,
            "total_wait_s": sum(waits)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Politique de fusion automatique — évaluation à blanc (#193).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("evaluate", help="matrice de décision d'une PR (lecture seule)")
    ev.add_argument("pr", type=int)
    ev.add_argument("--json", action="store_true")
    me = sub.add_parser("metrics", help="attente entre CI verte et fusion sur les PR fusionnées récentes")
    me.add_argument("--limit", type=int, default=20)
    args = ap.parse_args(argv)
    if args.cmd == "evaluate":
        decision = evaluate(collect(args.pr))
        print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2) if args.json else render(decision))
        return 0
    m = metrics(args.limit)
    for r in m["prs"]:
        print(f"#{r['pr']:<5} {r['wait_s'] / 60:7.1f} min  {r['title']}")
    if m["count"]:
        print(f"{m['count']} PR — attente médiane {m['median_wait_s'] / 60:.1f} min, "
              f"cumul {m['total_wait_s'] / 3600:.1f} h entre CI verte et fusion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
