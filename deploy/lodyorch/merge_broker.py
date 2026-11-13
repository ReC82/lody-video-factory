#!/usr/bin/env python3
"""Broker de fusion et de jetons de LODYORCH (#207) — SEUL détenteur des identifiants privilégiés.

Tourne sous l'utilisateur Unix système `lodyorch-merge` (unités `lodyorch-merge.service/.timer`),
jamais sous le compte des sessions Claude ni du superviseur. Il lit, et lui seul :

- le jeton fine-grained du propriétaire (`LQ_BROKER_TOKEN_FILE`, droit de fusion) ;
- la clé privée de l'application GitHub des sessions (`LQ_APP_KEY_FILE`).

Deux tâches, à chaque tour du minuteur (`tick`) :

1. `mint` : jeton d'installation de l'application (1 h, permissions réduites à la demande) écrit dans
   `LQ_AGENT_TOKEN_FILE` (0640, groupe du compte de service). C'est l'identité des sessions ET du
   superviseur : elle peut pousser une branche, ouvrir une PR, commenter, poser des labels — mais
   ses actions sont attribuées à `<app>[bot]`, jamais au propriétaire, et un ruleset « Restrict
   updates » sur `main` lui interdit toute fusion.
2. `process` : demandes de fusion déposées dans `<spool>/requests/` (par le superviseur, ou par
   n'importe quel processus du compte de service : le broker ne fait confiance à AUCUNE demande).
   Chaque demande est revalidée intégralement ici, avec la politique installée par root
   (`merge_policy.py`), le plafond root (`LQ_AUTO_MERGE` de ce fichier de configuration) et un
   armement non rétroactif (date de modification de la configuration root). Fusion `PUT …/merge`
   avec le SHA attendu ; journal idempotent : jamais deux requêtes de fusion pour un même SHA.

Aucun secret n'est jamais affiché, journalisé ni écrit ailleurs que dans le fichier du jeton d'agent.

    merge_broker.py tick      # mint si nécessaire, puis process (minuteur systemd)
    merge_broker.py status    # configuration et état, sans aucune valeur secrète
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

CONF = Path(os.environ.get("LQ_BROKER_CONF", "/etc/lodyorch/merge-broker.env"))
MODES = ("off", "dry-run", "on")
API = "https://api.github.com"
# Permissions demandées pour le jeton des sessions : jamais administration, workflows ni secrets.
AGENT_PERMISSIONS = {"contents": "write", "pull_requests": "write", "issues": "write",
                     "checks": "read", "actions": "read", "metadata": "read"}
FORBIDDEN_AGENT_PERMISSIONS = ("administration", "workflows", "secrets", "environments", "organization_administration")
REQUEST_NAME = re.compile(r"^(\d{1,7})-([0-9a-f]{40})-([0-9a-f]{8,32})\.json$")
MAX_REQUESTS_PER_TICK = 5
TOKEN_REFRESH_SECONDS = 30 * 60


def iso(ts=None):
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_conf(path=CONF):
    """`NOM=valeur` (sans interprétation shell). Le fichier n'est modifiable que par root."""
    conf = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip().strip("'\"")
    return conf


class Broker:
    def __init__(self, conf_path=CONF, api=None):
        self.conf_path = Path(conf_path)
        self.conf = read_conf(self.conf_path)
        c = self.conf
        self.repo = c.get("LQ_REPO", "ReC82/lody-video-factory")
        ceiling = c.get("LQ_AUTO_MERGE", "off").lower()
        self.ceiling = ceiling if ceiling in MODES else "off"
        base = Path(c.get("LQ_BROKER_DIR", "/var/lib/lodyorch-merge"))
        self.requests = base / "requests"
        self.results = base / "results"
        self.state = base / "state"
        self.token_file = Path(c.get("LQ_BROKER_TOKEN_FILE", "/etc/lodyorch/merge-token"))
        self.app_id = c.get("LQ_APP_ID", "")
        self.installation_id = c.get("LQ_APP_INSTALLATION_ID", "")
        self.app_key = Path(c.get("LQ_APP_KEY_FILE", "/etc/lodyorch/app-key.pem"))
        self.agent_token = Path(c.get("LQ_AGENT_TOKEN_FILE", str(base / "agent" / "token")))
        self.api_base = c.get("LQ_GITHUB_API", API).rstrip("/")
        self._api = api  # tests : faux GitHub (méthode, chemin, corps) → (statut, json)
        # La politique est celle installée par root à côté de ce fichier, avec la configuration du
        # broker (acteurs de confiance, auteurs de PR) — jamais celle de l'environnement appelant.
        for key in ("LQ_REPO", "LQ_MERGE_TRUSTED", "LQ_MERGE_PR_AUTHORS"):
            if key in c:
                os.environ[key] = c[key]
            else:
                os.environ.pop(key, None)
        # Copie privée du module (jamais un `merge_policy` déjà importé ni trouvé ailleurs dans sys.path).
        import importlib.util  # noqa: PLC0415
        name = f"merge_policy_broker_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().with_name("merge_policy.py"))
        self.mp = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.mp  # dataclasses : module enregistré
        spec.loader.exec_module(self.mp)

    # ------------------------------------------------------------------ GitHub (jeton privilégié)
    def _token(self):
        return self.token_file.read_text(encoding="utf-8").strip()

    def api(self, method, path, body=None, token=None, bearer=False):
        if self._api is not None:
            return self._api(method, path, body)
        req = urllib.request.Request(f"{self.api_base}/{path.lstrip('/')}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "lodyorch-merge-broker")
        req.add_header("Authorization", f"{'Bearer' if bearer else 'token'} {token or self._token()}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — URL fixe (API GitHub)
                raw = resp.read()
                return resp.status, json.loads(raw or b"null")
        except urllib.error.HTTPError as exc:
            try:
                data = json.loads(exc.read() or b"null")
            except ValueError:
                data = None
            return exc.code, data

    def get(self, path):
        status, data = self.api("GET", path)
        if status != 200:
            raise RuntimeError(f"GET {path} : HTTP {status}")
        return data

    # ------------------------------------------------------------------ jeton des sessions
    def jwt(self, now=None):
        """JWT RS256 de l'application, signé par `openssl` (aucune dépendance Python)."""
        now = int(time.time() if now is None else now)

        def b64(raw):
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        body = b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(self.app_id)}, separators=(",", ":")).encode())
        signing = f"{head}.{body}".encode()
        proc = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(self.app_key)], input=signing,
                              capture_output=True, timeout=30, check=False)
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError("signature du JWT impossible (clé de l'application illisible ou invalide)")
        return f"{head}.{body}.{b64(proc.stdout)}"

    def token_age(self):
        try:
            return time.time() - self.agent_token.stat().st_mtime
        except OSError:
            return None

    def mint(self, force=False):
        """Écrit un jeton d'installation neuf (0640) ; rend un message lisible, sans le jeton."""
        if not (self.app_id and self.installation_id):
            return "application GitHub non configurée (LQ_APP_ID / LQ_APP_INSTALLATION_ID) : aucun jeton"
        age = self.token_age()
        if not force and age is not None and age < TOKEN_REFRESH_SECONDS:
            return f"jeton d'agent récent ({int(age)} s) : conservé"
        name = self.repo.split("/", 1)[1]
        status, data = self.api("POST", f"app/installations/{self.installation_id}/access_tokens",
                                {"repositories": [name], "permissions": AGENT_PERMISSIONS},
                                token=self.jwt(), bearer=True)
        if status != 201 or not isinstance(data, dict) or not data.get("token"):
            raise RuntimeError(f"création du jeton d'installation refusée (HTTP {status})")
        granted = data.get("permissions") or {}
        bad = [p for p in FORBIDDEN_AGENT_PERMISSIONS if p in granted]
        if bad:
            raise RuntimeError(f"jeton d'agent trop large ({', '.join(bad)}) : non écrit")
        self.agent_token.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.agent_token.with_name(f".{self.agent_token.name}.{uuid.uuid4().hex}")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        try:
            os.write(fd, (data["token"] + "\n").encode())
        finally:
            os.close(fd)
        os.chmod(tmp, 0o640)
        os.replace(tmp, self.agent_token)
        return f"jeton d'agent renouvelé (expire {data.get('expires_at', '?')}, permissions {sorted(granted)})"

    # ------------------------------------------------------------------ fusions
    def armed_at(self):
        """Activation non rétroactive : date de la dernière modification (root) de la configuration
        du broker. Une demande ne peut que la RETARDER (`authorized_after`), jamais l'avancer."""
        if self.ceiling != "on":
            return ""
        return iso(self.conf_path.stat().st_mtime)

    def journal(self):
        path = self.state / "journal.jsonl"
        out = {}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                out[f"{row.get('pr')}-{row.get('head_sha')}"] = row
        return out

    def log(self, row):
        self.state.mkdir(parents=True, exist_ok=True)
        with open(self.state / "journal.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(row, at=iso()), ensure_ascii=False) + "\n")

    def answer(self, name, result):
        self.results.mkdir(parents=True, exist_ok=True)
        tmp = self.results / f".{name}.{uuid.uuid4().hex}"
        tmp.write_text(json.dumps(dict(result, at=iso()), ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, self.results / name)

    def handle(self, name, req):
        """Traite UNE demande validée. Rend le résultat (dict) ; aucune exception ne sort."""
        m = REQUEST_NAME.match(name)
        pr, sha = int(m.group(1)), m.group(2)
        if req.get("pr") != pr or req.get("head_sha") != sha:
            return {"state": "refused", "refusals": [{"code": "requete-invalide", "detail": "nom et contenu discordants"}]}
        key = f"{pr}-{sha}"
        done = self.journal().get(key)
        if done and done.get("state") in ("merged", "failed", "intent"):
            # Idempotence : jamais de seconde requête de fusion pour ce SHA (une intention sans issue
            # se constate sur GitHub, elle ne se rejoue pas).
            if done["state"] == "intent":
                status, live = self.api("GET", f"repos/{self.repo}/pulls/{pr}")
                if status == 200 and live.get("merged") and (live.get("head") or {}).get("sha") == sha:
                    self.log({"pr": pr, "head_sha": sha, "state": "merged", "merge_sha": live.get("merge_commit_sha", "")})
                    return {"state": "merged", "merge_sha": live.get("merge_commit_sha", "")}
                return {"state": "failed", "detail": "fusion interrompue précédemment : non rejouée"}
            return {k: done.get(k) for k in ("state", "merge_sha", "detail") if done.get(k) is not None}
        requested = str(req.get("mode") or "")
        mode = MODES[min(MODES.index(requested) if requested in MODES else 0, MODES.index(self.ceiling))]
        if mode != "on":
            return {"state": "refused", "mode": mode,
                    "refusals": [{"code": "mode", "detail": f"demande {requested or '?'}, plafond root {self.ceiling}"}]}
        try:
            facts = self.mp.collect(pr, get=self.get)
        except Exception as exc:  # noqa: BLE001
            return {"state": "error", "detail": f"collecte impossible ({type(exc).__name__}: {str(exc)[:200]})"}
        after = max(self.armed_at(), str(req.get("authorized_after") or ""))
        facts["authorized_after"] = after
        decision = self.mp.evaluate(facts)
        if decision.head_sha != sha:
            decision.checks.append(self.mp.Check("sha-attendu", False, f"tête {decision.head_sha[:10]} ≠ {sha[:10]}"))
            decision.eligible = False
        decision.dry_run = False
        refusals = [{"code": c.code, "detail": c.detail} for c in decision.refusals]
        if not decision.eligible:
            return {"state": "refused", "refusals": refusals, "policy_version": decision.policy_version}
        self.log({"pr": pr, "head_sha": sha, "issue": decision.ticket, "state": "intent"})
        merge = decision.merge_request()
        fields = dict(merge["fields"], commit_title=f"Merge pull request #{pr} (fusion automatique lodyorch, #{decision.ticket})")
        status, data = self.api("PUT", merge["path"], fields)
        if status == 200 and isinstance(data, dict) and data.get("merged"):
            self.log({"pr": pr, "head_sha": sha, "state": "merged", "merge_sha": data.get("sha", "")})
            return {"state": "merged", "merge_sha": data.get("sha", "")}
        detail = f"HTTP {status} : {((data or {}).get('message') if isinstance(data, dict) else '') or 'refus'}"[:300]
        self.log({"pr": pr, "head_sha": sha, "state": "failed", "detail": detail, "status": status})
        return {"state": "failed", "status": status, "detail": detail}

    def process(self):
        """Vide la file des demandes (au plus MAX_REQUESTS_PER_TICK) : chacune est retirée et reçoit
        une réponse, même invalide (rien ne reste à rejouer)."""
        self.requests.mkdir(parents=True, exist_ok=True)
        lines = []
        for path in sorted(self.requests.iterdir())[:MAX_REQUESTS_PER_TICK * 4]:
            if len(lines) >= MAX_REQUESTS_PER_TICK:
                break
            name = path.name
            try:
                if path.is_symlink() or not path.is_file() or not REQUEST_NAME.match(name) or path.stat().st_size > 4096:
                    raise ValueError("requête invalide")
                req = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(req, dict):
                    raise ValueError("requête invalide")
            except (OSError, ValueError):
                self._discard(path)
                lines.append(f"{name[:60]} : ignorée (invalide)")
                continue
            self._discard(path)
            try:
                result = self.handle(name, req)
            except Exception as exc:  # noqa: BLE001 — une demande ne fait jamais tomber le broker
                result = {"state": "error", "detail": type(exc).__name__}
            self.answer(name, result)
            lines.append(f"PR #{req.get('pr')} @ {str(req.get('head_sha'))[:10]} : {result.get('state')}")
        return lines

    @staticmethod
    def _discard(path):
        try:
            if path.is_dir() and not path.is_symlink():
                path.rmdir()
            else:
                path.unlink()
        except OSError:
            pass

    def status(self):
        out = [f"Broker de fusion — dépôt {self.repo} — plafond root {self.ceiling}"
               + (f" (armé le {self.armed_at()})" if self.ceiling == "on" else ""),
               f"  jeton privilégié lisible : {'oui' if os.access(self.token_file, os.R_OK) else 'non'} ({self.token_file})",
               f"  application : {'configurée' if self.app_id and self.installation_id else 'non configurée'}"
               f" ; clé lisible : {'oui' if os.access(self.app_key, os.R_OK) else 'non'}",
               "  jeton d'agent : " + (f"âge {int(self.token_age())} s" if self.token_age() is not None else "absent"),
               f"  demandes en attente : {len(list(self.requests.glob('*.json'))) if self.requests.is_dir() else 0}"]
        last = list(self.journal().values())[-5:]
        out += [f"  {r.get('at', '?')} PR #{r.get('pr')} @ {str(r.get('head_sha'))[:10]} : {r.get('state')}" for r in last]
        return "\n".join(out)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "status"
    if cmd not in ("tick", "mint", "process", "status"):
        print("usage : merge_broker.py tick|mint|process|status", file=sys.stderr)
        return 2
    if os.geteuid() == 0 and cmd != "status":
        print("Refus : le broker ne tourne jamais en root (utilisateur lodyorch-merge).", file=sys.stderr)
        return 2
    broker = Broker()
    if cmd == "status":
        print(broker.status())
        return 0
    code = 0
    if cmd in ("tick", "mint"):
        try:
            print(broker.mint(force=cmd == "mint"))
        except Exception as exc:  # noqa: BLE001 — message sans secret
            print(f"jeton d'agent : échec ({exc})", file=sys.stderr)
            code = 1
    if cmd in ("tick", "process"):
        for line in broker.process():
            print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
