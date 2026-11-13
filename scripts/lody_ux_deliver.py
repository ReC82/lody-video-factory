#!/usr/bin/env python3
"""#221 — Livreur UX automatique de Lody : de `main` vert à « EN LIGNE — prêt à tester », avec preuve.

Composant d'exploitation INDÉPENDANT du superviseur et des sessions Claude : il tourne sur l'hôte, dans
le clone de production, sous un compte qui a accès à Docker (unité systemd `lody-ux-deliver`, installée
par le propriétaire). Il ne fusionne rien, ne lit aucun secret et ne touche qu'à l'application Lody, en
déléguant la livraison elle-même à `scripts/lody-deploy.sh` (#209 : verrou, sonde d'activité,
sauvegarde SQLite, santé, journal des releases, retour arrière).

    python3 scripts/lody_ux_deliver.py                # SIMULATION : dit ce qui serait fait, ne change rien
    python3 scripts/lody_ux_deliver.py --apply        # livre si TOUTES les conditions sont réunies
    python3 scripts/lody_ux_deliver.py --status       # dernières entrées du journal de livraison

Conditions (toutes, sinon rien n'est livré) :
1. pas d'interrupteur d'arrêt (`<ops>/ux-deliver.off`), verrou propre au livreur libre ;
2. une release #209 VALIDÉE existe (sinon : première livraison manuelle, jamais à l'aveugle) ;
3. `origin/main` descend de la version servie ; le clone n'a aucune modification suivie ;
4. la plage « version servie → main » est ENTIÈREMENT `ux-safe` selon `deploy/lodyorch/ux_safe.py`
   (diff réel lu par git, pas un label) ;
5. CI verte sur le SHA exact de `main` (check-runs GitHub, lecture publique) ;
6. aucun rendu ni production en cours (sonde de `lody-deploy.sh` : sinon report, jamais d'interruption).

Après livraison : release validée = SHA cible, révision de l'image du conteneur = SHA cible, santé HTTP
du site public ; sinon retour arrière automatique vers la release précédente. Chaque transition est
ajoutée à `<ops>/ux-deliveries.jsonl` (ajout seul) ; le superviseur en tire les mails « Déploiement en
cours », « EN LIGNE — prêt à tester », « Déploiement échoué / retour arrière ». Une cible déjà traitée
(en ligne, refusée, échouée) n'est jamais retentée automatiquement : `--retry` explicite.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for _folder in (ROOT / "scripts", ROOT / "deploy" / "lodyorch"):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))

import lody_release  # noqa: E402
import ux_safe  # noqa: E402

JOURNAL = "ux-deliveries.jsonl"
KILL_SWITCH = "ux-deliver.off"
LOCK = "ux-deliver.lock"
FINAL_STATES = ("online", "refused", "failed", "rolled_back")
TICKET_REF = re.compile(r"\(#(\d+)\)")
DEFAULT_REPO = os.environ.get("LODY_GITHUB_REPO", "ReC82/lody-video-factory")
DEFAULT_SITE = os.environ.get("LODY_SITE_URL", "https://video.lodylands.com")
UI = os.environ.get("LODY_UI_CONTAINER", "lody-video-factory-ui")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- accès hôte (remplaçables)

@dataclass
class Host:
    """Toutes les actions sur l'hôte passent par ici : les tests les remplacent (aucun Docker, aucun réseau)."""
    repo: Path
    github_repo: str = DEFAULT_REPO
    sleep: Callable[[float], None] = time.sleep

    def git(self, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args[:3])} : {proc.stderr.strip()[:300]}")
        return proc.stdout.strip()

    def git_ok(self, *args: str) -> bool:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, timeout=300).returncode == 0

    def changed_files(self, base: str, target: str) -> list[dict[str, Any]]:
        return ux_safe.git_files(base, target, str(self.repo))

    def deploy(self, *args: str) -> int:
        """`scripts/lody-deploy.sh` du clone (0 validé, 3 activité/verrou : report, autre : échec)."""
        return subprocess.run([str(self.repo / "scripts" / "lody-deploy.sh"), *args], cwd=self.repo,
                              timeout=3600).returncode

    def container_revision(self) -> str:
        proc = subprocess.run(["docker", "container", "inspect", "--format",
                               '{{ index .Config.Labels "org.opencontainers.image.revision" }}', UI],
                              capture_output=True, text=True, timeout=60)
        return proc.stdout.strip() if proc.returncode == 0 else ""

    def http_get(self, url: str) -> tuple[int, str]:
        req = urllib.request.Request(url, headers={"User-Agent": "lody-ux-deliver"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — URL de configuration
                return resp.status, resp.read(4096).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except (OSError, ValueError):
            return 0, ""

    def check_runs(self, sha: str) -> list[dict[str, Any]] | None:
        """Check-runs du SHA (API publique, lecture seule, sans jeton). None si GitHub est injoignable."""
        status, body = self._api(f"https://api.github.com/repos/{self.github_repo}/commits/{sha}/check-runs?per_page=100")
        if status != 200:
            return None
        try:
            return json.loads(body).get("check_runs") or []
        except json.JSONDecodeError:
            return None

    def _api(self, url: str) -> tuple[int, str]:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "lody-ux-deliver"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""
        except (OSError, ValueError):
            return 0, ""


# --------------------------------------------------------------------------- journal

def load_journal(ops: Path) -> list[dict[str, Any]]:
    path = ops / JOURNAL
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def append_journal(ops: Path, entry: dict[str, Any]) -> None:
    ops.mkdir(parents=True, exist_ok=True)
    with (ops / JOURNAL).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    # Lisible par le superviseur (aucun secret dans le journal) : il en tire les mails d'état.
    try:
        os.chmod(ops / JOURNAL, 0o644)
    except OSError:
        pass


def last_state(journal: list[dict[str, Any]], target: str) -> str:
    states = [e.get("state") for e in journal if e.get("target_sha") == target]
    return states[-1] if states else ""


# --------------------------------------------------------------------------- décision

@dataclass
class Outcome:
    state: str            # up-to-date | disabled | busy | deferred | refused | online | rolled_back | failed | plan
    detail: str = ""
    target_sha: str = ""
    served_sha: str = ""
    tickets: list[int] = field(default_factory=list)
    proof: dict[str, str] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return {"up-to-date": 0, "online": 0, "plan": 0, "disabled": 0, "already": 0,
                "deferred": 2, "busy": 2, "refused": 3}.get(self.state, 1)


@dataclass
class Config:
    ops: Path
    apply: bool = False
    retry: bool = False
    site_url: str = DEFAULT_SITE
    health_attempts: int = 10
    health_interval: float = 6.0
    branch: str = "main"


def tickets_of(host: Host, base: str, target: str) -> list[int]:
    try:
        subjects = host.git("log", "--format=%s", f"{base}..{target}")
    except RuntimeError:
        return []
    return sorted({int(n) for n in TICKET_REF.findall(subjects)})


def ci_verdict(host: Host, sha: str, files: list[dict[str, Any]]) -> tuple[str, str]:
    """('success'|'pending'|'failed', détail) — mêmes règles que la fusion automatique (#200)."""
    import merge_policy  # noqa: PLC0415 — même dossier que ux_safe
    runs = host.check_runs(sha)
    if runs is None:
        return "pending", "GitHub injoignable : CI non vérifiable, nouvel essai plus tard"
    if not runs:
        return "pending", "aucune vérification encore publiée sur ce SHA"
    check = merge_policy.evaluate_ci(runs, files)
    if check.ok:
        return "success", check.detail
    return ("pending" if check.detail.startswith("en cours") else "failed"), check.detail


def site_healthy(host: Host, cfg: Config) -> tuple[bool, dict[str, str]]:
    """Santé HTTP du site public : `/_stcore/health` = ok et page d'accueil 200 (plusieurs essais)."""
    base = cfg.site_url.rstrip("/")
    last: dict[str, str] = {}
    for attempt in range(max(1, cfg.health_attempts)):
        code_h, body_h = host.http_get(f"{base}/_stcore/health")
        code_p, _ = host.http_get(f"{base}/")
        last = {"health": f"{base}/_stcore/health → {code_h} {body_h.strip()[:20]!r}", "page": f"{base}/ → {code_p}"}
        if code_h == 200 and body_h.strip() == "ok" and code_p == 200:
            return True, last
        if attempt + 1 < cfg.health_attempts:
            host.sleep(cfg.health_interval)
    return False, last


def deliver(cfg: Config, host: Host) -> Outcome:
    if (cfg.ops / KILL_SWITCH).exists():
        return Outcome("disabled", f"interrupteur d'arrêt présent ({cfg.ops / KILL_SWITCH})")
    cfg.ops.mkdir(parents=True, exist_ok=True)
    with open(cfg.ops / LOCK, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return Outcome("busy", "une autre exécution du livreur est en cours")
        return _deliver_locked(cfg, host)


def _deliver_locked(cfg: Config, host: Host) -> Outcome:
    journal = load_journal(cfg.ops)

    def note(out: Outcome) -> Outcome:
        """Journalise une transition (jamais en simulation ; pas deux fois de suite le même état par cible)."""
        if not cfg.apply or out.state in ("up-to-date", "disabled", "busy", "plan", "already"):
            return out
        if last_state(journal, out.target_sha) == out.state:
            return out
        entry = {"id": out.target_sha[:12] or "aucune-cible", "at": now_iso(), "state": out.state,
                 "target_sha": out.target_sha, "served_sha": out.served_sha, "tickets": out.tickets,
                 "detail": out.detail, "proof": out.proof, "site_url": cfg.site_url}
        append_journal(cfg.ops, entry)
        journal.append(entry)
        return out

    try:
        current = lody_release.current_release(lody_release.load_releases(cfg.ops))
    except ValueError as exc:
        return note(Outcome("refused", f"journal des releases illisible : {exc}"))
    if not current or not current.get("git_sha"):
        return Outcome("refused", "aucune release #209 validée sur cet hôte : première livraison manuelle "
                                  "par scripts/lody-deploy.sh (docs/lody-livraison-ux.md), jamais à l'aveugle")
    served, served_tag = current["git_sha"], current["tag"]
    host.git("fetch", "--quiet", "origin", cfg.branch)
    target = host.git("rev-parse", f"origin/{cfg.branch}")
    base = Outcome("", served_sha=served, target_sha=target)
    if target == served:
        return Outcome("up-to-date", f"version servie = origin/{cfg.branch} ({served[:12]})", target, served)
    previous = last_state(journal, target)
    if previous in FINAL_STATES and not cfg.retry:
        return Outcome("already", f"cible {target[:12]} déjà traitée ({previous}) : --retry pour réessayer", target, served)
    base.tickets = tickets_of(host, served, target)

    def outcome(state: str, detail: str, **extra: Any) -> Outcome:
        return Outcome(state, detail, target, served, base.tickets, **extra)

    if not host.git_ok("merge-base", "--is-ancestor", served, target):
        return note(outcome("refused", f"origin/{cfg.branch} ne descend pas de la version servie {served[:12]}"))
    head = host.git("rev-parse", "HEAD")
    if head not in (served, target):
        return note(outcome("refused", f"clone de production sur {head[:12]} ≠ version servie {served[:12]} "
                                       f"et ≠ cible : intervention manuelle"))
    if host.git("status", "--porcelain", "--untracked-files=no"):
        return note(outcome("refused", "fichiers suivis modifiés dans le clone de production"))
    files = host.changed_files(served, target)
    verdict = ux_safe.classify(files)
    if not verdict.eligible:
        return note(outcome("refused", f"plage {served[:12]}..{target[:12]} {verdict.summary()} — "
                                       "mise en ligne manuelle (revue du propriétaire)"))
    ci, ci_detail = ci_verdict(host, target, files)
    if ci == "pending":
        return note(outcome("deferred", f"CI non terminée sur {target[:12]} : {ci_detail}"))
    if ci != "success":
        return note(outcome("refused", f"CI rouge sur {target[:12]} : {ci_detail}"))
    mode_args = ["--2d"] if current.get("mode") == "2d" else []
    if not cfg.apply:
        return outcome("plan", f"SIMULATION — serait livré : {verdict.summary()} ; CI verte ({ci_detail}) ; "
                               f"lody-deploy.sh {' '.join(mode_args)} puis contrôle, retour arrière vers "
                               f"{served_tag} si KO. Relancer avec --apply.")

    note(outcome("deploying", f"{verdict.summary()} ; CI verte ; release précédente {served_tag}"))
    if head != target:
        host.git("merge", "--ff-only", "--quiet", target)
    rc = host.deploy(*mode_args)
    if rc == 3:
        # Rendu ou production en cours, ou autre livraison : rien n'a changé côté conteneurs. Report.
        return note(outcome("deferred", "rendu/production en cours ou livraison concurrente : report au prochain "
                                        "passage (aucun rendu interrompu)"))
    proof: dict[str, str] = {}
    ok = rc == 0
    if ok:
        records = lody_release.load_releases(cfg.ops)
        now = lody_release.current_release(records) or {}
        revision = host.container_revision()
        proof["release"] = f"{now.get('tag', '?')} ({'validée' if now.get('validated') else 'non validée'})"
        proof["révision du conteneur"] = revision or "illisible"
        ok = now.get("git_sha") == target and revision == target
        healthy, http = site_healthy(host, cfg)
        proof.update(http)
        ok = ok and healthy
    else:
        proof["lody-deploy.sh"] = f"code {rc} (santé des conteneurs KO ou erreur de build)"
    if ok:
        return note(outcome("online", f"version {target[:12]} servie et vérifiée sur {cfg.site_url}", proof=proof))
    # Retour arrière automatique vers la release précédente (validée, vérifiée par lody-deploy.sh).
    rb = host.deploy("--rollback", served_tag)
    proof["retour arrière"] = f"lody-deploy.sh --rollback {served_tag} → code {rb}"
    if rb == 0:
        healthy, http = site_healthy(host, cfg)
        proof.update({f"après retour arrière, {k}": v for k, v in http.items()})
        if healthy:
            return note(outcome("rolled_back", f"livraison de {target[:12]} non conforme : retour à {served_tag}",
                                proof=proof))
    return note(outcome("failed", f"livraison de {target[:12]} non conforme ET retour arrière vers {served_tag} "
                                  "non vérifié — INTERVENTION MANUELLE (docs/lody-retention.md)", proof=proof))


def main(argv: list[str] | None = None, host: Host | None = None) -> int:
    ap = argparse.ArgumentParser(description="Livreur UX automatique de Lody (#221) — simulation par défaut.")
    ap.add_argument("--apply", action="store_true", help="livrer réellement (sinon simulation)")
    ap.add_argument("--retry", action="store_true", help="réessayer une cible déjà traitée")
    ap.add_argument("--status", action="store_true", help="afficher les dernières entrées du journal")
    ap.add_argument("--ops-dir", type=Path, default=None)
    ap.add_argument("--site-url", default=DEFAULT_SITE)
    args = ap.parse_args(argv)
    ops = args.ops_dir or lody_release.default_ops_dir()
    if args.status:
        for e in load_journal(ops)[-15:]:
            print(f"{e.get('at')}  {e.get('state'):12} {str(e.get('target_sha'))[:12]}  "
                  f"{' '.join('#' + str(n) for n in e.get('tickets') or [])}  {e.get('detail', '')[:120]}")
        return 0
    cfg = Config(ops=ops, apply=args.apply, retry=args.retry, site_url=args.site_url)
    try:
        out = deliver(cfg, host or Host(ROOT))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"state": out.state, "detail": out.detail, "target": out.target_sha[:12],
                      "served": out.served_sha[:12], "tickets": out.tickets, "proof": out.proof},
                     ensure_ascii=False, indent=2))
    return out.exit_code


if __name__ == "__main__":
    sys.exit(main())
