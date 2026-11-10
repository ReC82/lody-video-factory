#!/usr/bin/env python3
"""LODYORCH — superviseur de sessions Claude Code pour Lody Video Factory (ReC82/lody-video-factory).

Instance du moteur commun aux superviseurs de l'hôte (LQORCH → JCORCH → DIGORCH, copie de
/opt/dig-orch/lqorch.py du 2026-10-30). Différences avec DIGORCH (ticket #116) :
- seuls les tickets portant le label d'éligibilité (LQ_ELIGIBLE_LABEL, `agent:auto`) entrent
  dans la file ; le retrait du label les suspend ;
- labels de mise de côté propres à l'instance (LQ_HOLD_LABELS, `agent:human`…) ; le retrait du
  label de blocage (`agent:blocked`) par le propriétaire remet le ticket en file ;
- une dépendance n'est levée que par les états locaux de LQ_DEPS_DONE_STATES (Lody : ticket
  fermé, donc PR fusionnée, ou DONE) — une PR en attente de revue ne débloque rien ;
- authentification Claude vérifiée avant chaque session (`claude auth status`, sans rien
  afficher) : absente → aucune session ;
- réglages Claude imposés à chaque session (LQ_CLAUDE_SETTINGS : crochet de garde) ;
- masquage des motifs de secrets dans les journaux de session et les événements ;
- phase de travail déclarée par la session (`lodyorch phase …`) et PR affichées par `status`.

Une session Claude n'est qu'un ouvrier : elle traite UN ticket puis se termine. Le superviseur,
lui, survit aux sessions. Il garde l'état dans une base SQLite, choisit le travail suivant,
lance `claude -p` en mode non interactif avec un contexte reconstruit, surveille son activité,
classe la façon dont elle s'est terminée, et enchaîne.

Commandes :
  lodyorch status            état du superviseur, session en cours, plafonds locaux, quota observé
  lodyorch pulse             point d'activité en 2-3 lignes (console, toutes les 5 min), lecture seule
  lodyorch check             contrôles d'installation (authentifications, clone, réglages), lecture seule
  lodyorch config [set NOM VALEUR | unset NOM]   garde-fous (sessions/jour, budget, seuils de quota…)
  lodyorch reload            relire le code installé, entre deux sessions (sans couper la session)
  lodyorch queue             prochains tickets
  lodyorch pause | resume    ne plus démarrer / reprendre
  lodyorch stop [--now]      arrêter (après la session en cours, ou tout de suite)
  lodyorch skip N [raison]   mettre le ticket N de côté (BLOCKED) et passer au suivant
  lodyorch add N [--prio P] [--deps 35,321] [--note "…"]   ajouter / remettre en file
  lodyorch set N champ valeur                               corriger un champ d'un ticket
  lodyorch sync              synchroniser maintenant les tickets GitHub (si LQ_GH_SYNC=1)
  lodyorch phase TEXTE       phase de travail de la session en cours (appelé par la session)
  lodyorch notifications [--retry]   emails envoyés / en attente (si LQ_NOTIFY_SMTP_ENV)
  lodyorch log [N]           derniers événements
  lodyorch sessions          dernières sessions
  lodyorch run               boucle du superviseur (service systemd)

Aucune dépendance hors bibliothèque standard. Aucun secret n'est lu ni écrit ici : Claude Code
et `gh` utilisent leurs propres identifiants.
"""

import argparse
import fcntl
import hashlib
import json
import os
import queue
import re
import smtplib
import ssl
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import threading
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STATE_DIR = Path(os.environ.get("LQ_STATE_DIR", Path.home() / ".local/state/lodyorch"))
DB_PATH = STATE_DIR / "state.db"
LOG_DIR = STATE_DIR / "sessions"
LOCK_PATH = STATE_DIR / "supervisor.lock"
REPO = os.environ.get("LQ_REPO", "ReC82/lody-video-factory")
# Les sessions démarrent dans le dépôt principal (CLAUDE.md et mémoire du projet y sont rattachés)
# mais font TOUT leur travail Git dans une copie dédiée, jamais dans l'arbre du propriétaire.
LAUNCH_DIR = Path(os.environ.get("LQ_LAUNCH_DIR", Path.home() / "lody-orch/repo"))
WORKDIR = Path(os.environ.get("LQ_WORKDIR", Path.home() / "lody-orch/worktrees"))
CLAUDE = os.environ.get("LQ_CLAUDE", str(Path.home() / ".local/bin/claude"))
PROMPT_FILE = Path(os.environ.get("LQ_PROMPT", Path(__file__).with_name("session_prompt.md")))
# Réglages Claude Code ajoutés à chaque session (`--settings`) : crochet de garde PreToolUse et
# règles de refus. Absent ou illisible → aucune session (jamais de session sans garde).
CLAUDE_SETTINGS = os.environ.get("LQ_CLAUDE_SETTINGS", "")
# Authentification Claude existante, vérifiée avant chaque session sans rien afficher.
REQUIRE_AUTH = os.environ.get("LQ_REQUIRE_AUTH", "1") != "0"
STRICT_MCP = os.environ.get("LQ_STRICT_MCP", "1") != "0"

GH = os.environ.get("LQ_GH", "gh")

POLL_SECONDS = int(os.environ.get("LQ_POLL", 30))
TRIAGE_EVERY_SECONDS = int(os.environ.get("LQ_TRIAGE_EVERY", 6 * 3600))
BACKOFF_SCALE = float(os.environ.get("LQ_BACKOFF_SCALE", 1))
QUOTA_MARGIN_SECONDS = int(os.environ.get("LQ_QUOTA_MARGIN", 120))
# Instance à file explicite (Jury Central) : LQ_TRIAGE=0 désactive le tri automatique du backlog,
# LQ_SESSION_ENQUEUE=0 empêche une session d'ajouter elle-même des tickets (simple événement).
TRIAGE_ENABLED = os.environ.get("LQ_TRIAGE", "1") != "0"
SESSION_ENQUEUE = os.environ.get("LQ_SESSION_ENQUEUE", "1") != "0"
SERVICE = os.environ.get("LQ_SERVICE", "lodyorch")
# Synchronisation GitHub (instance Jury Central) : tous les tickets ouverts sont découverts, triés par
# label de priorité P0→P3, et leur statut est tenu à jour par labels. Désactivée par défaut.
GH_SYNC = os.environ.get("LQ_GH_SYNC", "0") == "1"
SYNC_EVERY_SECONDS = int(os.environ.get("LQ_SYNC_EVERY", 600))
BOT_MARKER = os.environ.get("LQ_BOT_MARKER", "<!-- lodyorch -->")
# Priorité d'un ticket sans label P0…P3 valide (P2 par défaut, comme les autres instances).
DEFAULT_PRIORITY_LEVEL = int(os.environ.get("LQ_DEFAULT_PRIORITY", 2))
# Label d'éligibilité : vide = tout ticket ouvert (DIGORCH) ; sinon seuls les tickets qui le portent.
ELIGIBLE_LABEL = os.environ.get("LQ_ELIGIBLE_LABEL", "")
# États locaux qui lèvent une dépendance (en plus d'un ticket fermé sur GitHub).
DEPS_DONE_STATES = tuple(s.strip() for s in os.environ.get("LQ_DEPS_DONE_STATES", "DONE,WAITING_OWNER").split(",")
                         if s.strip())
STATUS_LABELS = {"RUNNING": "statut: en cours", "BLOCKED": "statut: bloqué", "FAILED": "statut: bloqué",
                 "WAITING_OWNER": "statut: à tester", "DONE": "statut: terminé"}
# Surcharge par instance (JSON) ; BLOCKED, WAITING_OWNER et DONE doivent rester présents.
STATUS_LABELS.update(json.loads(os.environ.get("LQ_STATUS_LABELS") or "{}"))
HOLD_LABELS = {"statut: suspendu", "statut: abandonné", "wontfix", "duplicate", "invalid"}
HOLD_LABELS |= {x.strip() for x in os.environ.get("LQ_HOLD_LABELS", "").split(",") if x.strip()}
TRUSTED_AUTHORS = {"OWNER", "MEMBER", "COLLABORATOR"}
DEPS_RE = re.compile(r"(?:d[ée]pend(?:ance)?s?\s+(?:de|du|des|d'|on)|bloqu[ée]e?\s+par|blocked\s+by|"
                     r"pr[ée]requis|requires)\s*:?\s*((?:#\d+(?:\s*(?:,|et|and|&)?\s*)?)+)", re.I)
# Notifications email (réutilise un fichier SMTP existant, lu sans jamais être copié ni affiché).
NOTIFY_SMTP_ENV = os.environ.get("LQ_NOTIFY_SMTP_ENV", "")
NOTIFY_PREFIX = os.environ.get("LQ_NOTIFY_PREFIX", "[Lody]")
SITE_URL = os.environ.get("LQ_SITE_URL", "")
CLI_NAME = os.environ.get("LQ_CLI", "lodyorch")
INSTANCE = os.environ.get("LQ_INSTANCE", "LODYORCH")

# Garde-fous réglables sans toucher au code. Priorité : `lodyorch config set` (base d'état) >
# variable d'environnement > défaut ci-dessous. Relus à chaque tour de boucle : un changement
# s'applique à la frontière de session suivante, jamais à une session déjà lancée.
# nom : (variable d'environnement, défaut, minimum, maximum, description)
SETTINGS = {
    "max_sessions_per_day": ("LQ_MAX_SESSIONS_PER_DAY", 24, 0, 500,
                             "Sessions lancées au plus par jour (fuseau LQ_DAY_TZ, UTC par défaut). 0 = sans plafond, accepté seulement si un "
                             "garde-fou de budget ou de quota fournisseur est actif."),
    "max_cost_usd_per_day": ("LQ_MAX_COST_USD_PER_DAY", 0.0, 0.0, 10000.0,
                             "Plafond du coût estimé du jour (total_cost_usd de Claude Code, prix catalogue : "
                             "une estimation, pas une facture). 0 = désactivé."),
    "max_five_hour_utilization": ("LQ_MAX_FIVE_HOUR_UTILIZATION", 0.9, 0.05, 1.0,
                                  "Aucune nouvelle session si la fenêtre fournisseur de 5 h annoncée par Claude Code "
                                  "atteint ce taux (1 = désactivé)."),
    "max_seven_day_utilization": ("LQ_MAX_SEVEN_DAY_UTILIZATION", 0.8, 0.05, 1.0,
                                  "Idem pour la fenêtre de 7 jours ; laisse une marge pour l'usage interactif."),
    "min_seconds_between_sessions": ("LQ_MIN_GAP", 60, 30, 3600,
                                     "Écart minimal entre deux démarrages de session (anti-boucle)."),
    "max_consecutive_failures": ("LQ_MAX_CONSECUTIVE_FAILURES", 4, 1, 50,
                                 "Disjoncteur : après N sessions consécutives sans rapport, le superviseur se met "
                                 "en pause (lodyorch resume pour réarmer)."),
    "max_attempts": ("LQ_MAX_ATTEMPTS", 3, 1, 10, "Sessions sans rapport tolérées par ticket avant FAILED."),
    "session_max_seconds": ("LQ_SESSION_MAX", 4 * 3600, 600, 12 * 3600, "Durée maximale d'une session."),
    "session_idle_seconds": ("LQ_SESSION_IDLE", 75 * 60, 300, 4 * 3600,
                             "Session coupée après ce délai sans aucune sortie."),
}

STATUSES = ("READY", "RUNNING", "TESTING", "BLOCKED", "WAITING_OWNER", "DONE", "FAILED")
# SUSPENDED : posé seulement par la synchronisation GitHub (label suspendu / abandonné / wontfix…).
# Pseudo-ticket : une session qui relit le backlog et remplit la file quand elle est vide.
TRIAGE = 0


# Motifs de secrets masqués dans les journaux de session et les événements : jetons Anthropic,
# OpenAI, GitHub, AWS, Slack, ElevenLabs, clés privées, et valeurs d'affectations sensibles
# (`api_key = "…"`, `Authorization: Bearer …`). Filet de sécurité, pas une garantie : la règle reste
# de ne jamais lire de secret (crochet de garde, consignes de session).
SECRET_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-+/=]{16,}"),
    re.compile(r"(?i)((?:api[_-]?key|secret|token|password|passwd|access[_-]?key)[A-Za-z0-9_]*\\?[\"']?\s*[:=]\s*\\?[\"']?)"
               r"(?!\*\*\*)([^\s\"'\\,}]{8,})"),
]


def redact(text):
    """Texte avec les motifs de secrets remplacés par « *** » (préfixe d'affectation conservé)."""
    if not text:
        return text
    for pat in SECRET_PATTERNS:
        if pat.groups == 2:
            text = pat.sub(lambda m: m.group(1) + "***", text)
        elif pat.groups == 1:
            text = pat.sub(lambda m: m.group(1) + " ***", text)
        else:
            text = pat.sub("***", text)
    return text


def now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- base

def db():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)  # journaux de session et état : lisibles par le seul compte de service
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS tasks(
        issue INTEGER PRIMARY KEY, title TEXT, status TEXT NOT NULL DEFAULT 'READY',
        priority INTEGER NOT NULL DEFAULT 50, deps TEXT DEFAULT '', branch TEXT DEFAULT '',
        pr TEXT DEFAULT '', commit_sha TEXT DEFAULT '', summary TEXT DEFAULT '', tests TEXT DEFAULT '',
        blockers TEXT DEFAULT '', next_action TEXT DEFAULT '', note TEXT DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0, last_session TEXT DEFAULT '', last_activity TEXT DEFAULT '',
        not_before TEXT DEFAULT '', updated_at TEXT);
    CREATE TABLE IF NOT EXISTS sessions(
        id TEXT PRIMARY KEY, issue INTEGER, started_at TEXT, ended_at TEXT, pid INTEGER,
        outcome TEXT, exit_code INTEGER, detail TEXT, log_path TEXT, cost_usd REAL);
    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, issue INTEGER, kind TEXT, message TEXT);
    CREATE TABLE IF NOT EXISTS control(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS notifications(
        key TEXT PRIMARY KEY, issue INTEGER, kind TEXT, subject TEXT, body TEXT,
        attachment_name TEXT DEFAULT '', attachment TEXT DEFAULT '', created_at TEXT, sent_at TEXT,
        attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT DEFAULT '', last_error TEXT DEFAULT '');
    """)
    for col, decl in (("gh_labels", "TEXT DEFAULT '[]'"), ("gh_since", "TEXT DEFAULT ''"),
                      ("gh_cursor", "INTEGER NOT NULL DEFAULT 0"), ("questions", "TEXT DEFAULT '[]'"),
                      ("test_links", "TEXT DEFAULT '[]'"), ("report_url", "TEXT DEFAULT ''"),
                      ("own_lock", "INTEGER NOT NULL DEFAULT 0")):
        try:
            con.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass
    return con


def ctl_get(con, key, default=""):
    r = con.execute("SELECT value FROM control WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def ctl_set(con, key, value):
    con.execute("INSERT INTO control(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
    con.commit()


def parse_setting(name, raw):
    """Valeur typée et bornée, ou ValueError avec un message lisible."""
    _, default, low, high, _ = SETTINGS[name]
    try:
        value = type(default)(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} attend un nombre {'entier' if isinstance(default, int) else 'décimal'}.") from None
    if not low <= value <= high:
        raise ValueError(f"{name} doit être compris entre {low} et {high}.")
    return value


def setting(con, name):
    """Rend (valeur, origine). Une valeur invalide est ignorée au profit de la source suivante."""
    env, default, *_ = SETTINGS[name]
    for raw, origin in ((ctl_get(con, "cfg." + name, None), "lodyorch config"), (os.environ.get(env), f"env {env}")):
        if raw is not None and raw != "":
            try:
                return parse_setting(name, raw), origin
            except ValueError:
                continue
    return default, "défaut du code"


def policy_error(values):
    """Un mode sans plafond de sessions n'est permis que si un autre garde-fou borne la dépense."""
    if values["max_sessions_per_day"] == 0 and values["max_cost_usd_per_day"] <= 0 \
            and values["max_five_hour_utilization"] >= 1 and values["max_seven_day_utilization"] >= 1:
        return ("Sans plafond de sessions, il faut au moins un garde-fou : max_cost_usd_per_day > 0 "
                "ou un seuil d'utilisation fournisseur < 1.")
    return ""


def settings(con):
    return {name: setting(con, name)[0] for name in SETTINGS}


def event(con, issue, kind, message):
    con.execute("INSERT INTO events(at,issue,kind,message) VALUES(?,?,?,?)", (iso(), issue, kind, redact(message)[:2000]))
    con.commit()


def task(con, issue):
    return con.execute("SELECT * FROM tasks WHERE issue=?", (issue,)).fetchone()


def update_task(con, issue, **fields):
    fields["updated_at"] = iso()
    cols = ", ".join(f"{k}=?" for k in fields)
    con.execute(f"UPDATE tasks SET {cols} WHERE issue=?", (*fields.values(), issue))
    con.commit()


# ---------------------------------------------------------------- GitHub (lecture seule ici)

def gh_json(path):
    try:
        out = subprocess.run([GH, "api", path], capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout) if out.returncode == 0 else None
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None


def issue_state(n):
    data = gh_json(f"repos/{REPO}/issues/{n}")
    if not data:
        return None, ""
    return data.get("state"), data.get("title", "")


def deps_satisfied(con, deps):
    """Une dépendance est levée si le ticket est fermé, ou traité ici (états LQ_DEPS_DONE_STATES)."""
    missing = []
    for raw in [d.strip().lstrip("#") for d in (deps or "").split(",") if d.strip()]:
        if not raw.isdigit():
            continue
        n = int(raw)
        t = task(con, n)
        if t and t["status"] in DEPS_DONE_STATES:
            continue
        state, _ = issue_state(n)
        if state != "closed":
            missing.append(n)
    return missing


# ---------------------------------------------------------------- sélection

def next_task(con):
    """Le prochain travail : une session interrompue d'abord, puis le READY le plus prioritaire."""
    rows = con.execute("""SELECT * FROM tasks WHERE status IN ('RUNNING','TESTING','READY')
                          ORDER BY CASE status WHEN 'READY' THEN 1 ELSE 0 END, priority, issue""").fetchall()
    for t in rows:
        if t["not_before"] and t["not_before"] > iso():
            continue
        if t["issue"] != TRIAGE:
            state, title = issue_state(t["issue"])
            if state == "closed":
                update_task(con, t["issue"], status="DONE", next_action="Ticket fermé sur GitHub.")
                event(con, t["issue"], "closed", "Ticket fermé sur GitHub : retiré de la file.")
                continue
            if title and not t["title"]:
                update_task(con, t["issue"], title=title)
        missing = deps_satisfied(con, t["deps"])
        if missing:
            dep = actionable_dependency(con, missing)
            if dep:
                update_task(con, t["issue"], blockers=f"Attend #{dep['issue']}, traité d'abord (dépendance).")
                return dep
            update_task(con, t["issue"], blockers=f"Dépendances ouvertes non traitables maintenant : {missing}")
            continue
        return task(con, t["issue"])
    return None


def actionable_dependency(con, missing):
    """Une dépendance ouverte qu'on peut traiter tout de suite (file, pas en attente, ses propres
    dépendances levées) ; sinon None. Un seul niveau : pas de récursion, pas de cycle."""
    for n in missing:
        d = task(con, n)
        if not d or d["status"] not in ("READY", "RUNNING", "TESTING"):
            continue
        if d["not_before"] and d["not_before"] > iso():
            continue
        if not deps_satisfied(con, d["deps"]):
            return d
    return None


# ---------------------------------------------------------------- quota

def provider_usage(con):
    """Dernier `rate_limit_info` annoncé par Claude Code, avec son heure d'observation. Ce n'est
    qu'un instantané pris pendant la dernière session : l'usage interactif du propriétaire depuis
    n'y figure pas. Aucune autre source (pourcentages de l'interface claude.ai) n'est lue."""
    raw = ctl_get(con, "provider_usage")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    # Superviseur antérieur à #380 : relire le dernier événement dans le journal le plus récent.
    last = con.execute("SELECT log_path FROM sessions WHERE log_path IS NOT NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    path = Path(last["log_path"]) if last else None
    if not path or not path.exists():
        return {}
    info = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"rate_limit_event"' in line:
                try:
                    info = json.loads(line).get("rate_limit_info") or info
                except json.JSONDecodeError:
                    pass
    return dict(info, observed_at=iso(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))) if info else {}


def day_tz(con=None):
    """Fuseau du « jour » des plafonds : LQ_DAY_TZ, sinon réglage d'état `day_tz` (survit à un
    rechargement sans redémarrage), sinon UTC."""
    name = os.environ.get("LQ_DAY_TZ") or (ctl_get(con, "day_tz") if con is not None else "")
    try:
        return ZoneInfo(name) if name else timezone.utc
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def day_start(con=None):
    tz = day_tz(con)
    return datetime.combine(now().astimezone(tz).date(), dtime(0), tzinfo=tz).astimezone(timezone.utc)


def shared_usage(con):
    """Instantanés `provider_usage` de cette instance et des autres superviseurs du même compte
    Claude (bases d'état lues en lecture seule : LQ_SHARED_USAGE_DBS ou réglage `shared_usage_dbs`).
    Rend [(source, instantané)] du plus récent au plus ancien ; liste vide si aucun n'est connu."""
    found = []
    own = provider_usage(con)
    if own:
        found.append((ctl_get(con, "instance_name") or STATE_DIR.name, own))
    paths = os.environ.get("LQ_SHARED_USAGE_DBS") or ctl_get(con, "shared_usage_dbs")
    for path in [x.strip() for x in (paths or "").split(",") if x.strip()]:
        row = None
        # Base d'un autre superviseur, lue sans jamais l'écrire. Sous lodyorch.service, son dossier
        # est monté en lecture seule : une base WAL au repos (sans -shm) ne s'ouvre alors qu'en
        # mode « immutable » (instantané, suffisant pour un relevé de quota).
        for flags in ("mode=ro", "mode=ro&immutable=1"):
            try:
                other = sqlite3.connect(f"file:{path}?{flags}", uri=True, timeout=5)
                try:
                    row = other.execute("SELECT value FROM control WHERE key='provider_usage'").fetchone()
                finally:
                    other.close()
                break
            except sqlite3.Error:
                continue
        try:
            if row and row[0]:
                found.append((Path(path).parent.name, json.loads(row[0])))
        except json.JSONDecodeError:
            continue
    return sorted(found, key=lambda x: x[1].get("observed_at", ""), reverse=True)


def freshest_usage(con):
    usages = shared_usage(con)
    return usages[0][1] if usages else {}


def next_utc_day(con=None):
    """Début du jour suivant (minuit local du fuseau des plafonds, changements d'heure compris)."""
    tz = day_tz(con)
    return datetime.combine(now().astimezone(tz).date() + timedelta(days=1), dtime(0), tzinfo=tz).astimezone(timezone.utc)


def start_blocker(con):
    """Pourquoi aucune session ne peut démarrer maintenant : (raison, reprise_iso) ou None.
    Les limites du fournisseur passent avant la politique locale et ne sont jamais contournées."""
    if ctl_get(con, "mode", "RUNNING") == "PAUSED":
        return ctl_get(con, "pause_reason") or "Pause demandée (lodyorch resume pour reprendre).", ""
    until = ctl_get(con, "quota_until")
    if until and until > iso():
        return "Limite fournisseur signalée par Claude Code.", until
    cfg = settings(con)
    windows = freshest_usage(con).get("unifiedWindows") or {}
    for window, key, label in (("five_hour", "max_five_hour_utilization", "5 h"),
                               ("seven_day", "max_seven_day_utilization", "7 jours")):
        win = windows.get(window) or {}
        util, reset = win.get("utilization"), win.get("resetsAt")
        if util is not None and reset and reset > time.time() and util >= cfg[key]:
            return (f"Fenêtre fournisseur {label} à {util:.0%} (seuil {cfg[key]:.0%}).",
                    iso(datetime.fromtimestamp(int(reset) + QUOTA_MARGIN_SECONDS, timezone.utc)))
    cap = cfg["max_sessions_per_day"]
    if policy_error(cfg):
        cap = SETTINGS["max_sessions_per_day"][1]
    if cap and sessions_today(con) >= cap:
        return f"Plafond local de {cap} sessions par jour atteint.", iso(next_utc_day(con))
    if cfg["max_cost_usd_per_day"] > 0 and cost_today(con) >= cfg["max_cost_usd_per_day"]:
        return f"Budget estimé du jour atteint ({cfg['max_cost_usd_per_day']:.2f} USD).", iso(next_utc_day(con))
    last = con.execute("SELECT MAX(started_at) m FROM sessions").fetchone()["m"]
    if last:
        ready = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() \
            + cfg["min_seconds_between_sessions"]
        if ready > time.time():
            return "Écart minimal entre deux sessions.", iso(datetime.fromtimestamp(ready, timezone.utc))
    return None


# ---------------------------------------------------------------- session

def build_prompt(con, t, session_id, status_file):
    base = PROMPT_FILE.read_text(encoding="utf-8")
    history = con.execute("""SELECT started_at, outcome, detail FROM sessions WHERE issue=?
                             ORDER BY started_at DESC LIMIT 5""", (t["issue"],)).fetchall()
    hist = "\n".join(f"- {h['started_at']} : {h['outcome']} — {(h['detail'] or '')[:300]}" for h in history) or "- (aucune)"
    state = {k: t[k] for k in t.keys()}
    queue = [dict(issue=r["issue"], status=r["status"], title=r["title"]) for r in con.execute(
        "SELECT issue,status,title FROM tasks ORDER BY priority, issue").fetchall()]
    return (base.replace("{{ISSUE}}", str(t["issue"]))
            .replace("{{TITLE}}", t["title"] or "")
            .replace("{{STATE_JSON}}", json.dumps(state, ensure_ascii=False, indent=2))
            .replace("{{HISTORY}}", hist)
            .replace("{{QUEUE_JSON}}", json.dumps(queue, ensure_ascii=False))
            .replace("{{SESSION_ID}}", session_id)
            .replace("{{STATUS_FILE}}", str(status_file))
            .replace("{{WORKDIR}}", str(WORKDIR))
            .replace("{{LAUNCH_DIR}}", str(LAUNCH_DIR))
            .replace("{{STATE_DIR}}", str(STATE_DIR)))


def classify(exit_code, result, rate_events, killed):
    """Comment la session s'est terminée. Rend (issue_de_session, détail, quota_reprise_iso)."""
    reset = ""
    for ev in rate_events:
        info = ev.get("rate_limit_info", {})
        if info.get("status") not in (None, "allowed", "allowed_warning"):
            ts = info.get("resetsAt")
            if ts:
                reset = iso(datetime.fromtimestamp(int(ts) + QUOTA_MARGIN_SECONDS, timezone.utc))
            return "RATE_LIMIT", f"Limite fournisseur ({info.get('rateLimitType')}) jusqu'à {reset or '?'}", reset
    if killed:
        return ("STOPPED" if killed.startswith("Arrêt immédiat") else "TIMEOUT"), killed, ""
    if result is None:
        return "CRASH", f"Processus terminé sans résultat (code {exit_code}).", ""
    text = str(result.get("result") or "")
    status = result.get("api_error_status")
    if result.get("is_error"):
        low = text.lower()
        if status == 429 or "usage limit" in low or "rate limit" in low:
            return "RATE_LIMIT", text[:300], ""
        if status and int(status) >= 500 or "connection" in low or "overloaded" in low:
            return "TRANSIENT", text[:300], ""
        return "ERROR", text[:300], ""
    return "COMPLETED", text[:500], ""


def run_session(con, t):
    session_id = str(uuid.uuid4())
    log_path = LOG_DIR / f"{iso().replace(':', '')}-{t['issue']}-{session_id[:8]}.jsonl"
    status_file = LOG_DIR / f"{session_id}.status.json"
    prompt = build_prompt(con, t, session_id, status_file)
    con.execute("INSERT INTO sessions(id,issue,started_at,log_path) VALUES(?,?,?,?)",
                (session_id, t["issue"], iso(), str(log_path)))
    update_task(con, t["issue"], status="RUNNING", last_session=session_id, last_activity=iso(),
                attempts=t["attempts"] + 1)
    event(con, t["issue"], "session_start", f"Session {session_id} (tentative {t['attempts'] + 1})")
    if GH_SYNC and t["issue"] != TRIAGE:
        set_status_label(con, t["issue"], "RUNNING")
    session_started = iso()
    env = dict(os.environ, LQ_SESSION_ID=session_id, LQ_STATUS_FILE=str(status_file), LQ_ISSUE=str(t["issue"]))
    cmd = [CLAUDE, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--dangerously-skip-permissions", "--session-id", session_id]
    if CLAUDE_SETTINGS:
        cmd += ["--settings", CLAUDE_SETTINGS]
    if STRICT_MCP:
        # Aucun connecteur MCP (mails, documents, agendas du compte) dans une session de code.
        cmd += ["--strict-mcp-config"]
    set_phase(con, t["issue"], "session lancée")
    max_seconds = setting(con, "session_max_seconds")[0]
    idle_seconds = setting(con, "session_idle_seconds")[0]
    started = time.time()
    last_out = time.time()
    result = None
    rate_events = []
    killed = ""
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=LAUNCH_DIR, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        con.execute("UPDATE sessions SET pid=? WHERE id=?", (proc.pid, session_id))
        con.commit()
        lines = queue.Queue()

        def pump():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        finished = False
        while not finished:
            try:
                line = lines.get(timeout=2)
            except queue.Empty:
                line = ""
            while line is not None and line != "":
                last_out = time.time()
                log.write(redact(line))
                try:
                    msg = json.loads(line)
                    if msg.get("type") == "result":
                        result = msg
                    elif msg.get("type") == "rate_limit_event":
                        rate_events.append(msg)
                        ctl_set(con, "provider_usage", json.dumps(
                            dict(msg.get("rate_limit_info") or {}, observed_at=iso()), ensure_ascii=False))
                except json.JSONDecodeError:
                    pass
                try:
                    line = lines.get_nowait()
                except queue.Empty:
                    line = ""
            if line is None:
                finished = True
            log.flush()
            if time.time() - last_out < 5:
                update_task(con, t["issue"], last_activity=iso())
            if finished:
                proc.wait()
                break
            reason = ""
            if ctl_get(con, "mode") == "STOP_NOW":
                reason = "Arrêt immédiat demandé (stop --now)."
            elif time.time() - started > max_seconds:
                reason = f"Durée maximale dépassée ({max_seconds // 60} min)."
            elif time.time() - last_out > idle_seconds:
                reason = f"Aucune activité depuis {idle_seconds} s."
            if reason:
                killed = reason
                event(con, t["issue"], "session_kill", reason)
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                except ProcessLookupError:
                    pass
                break
    reader.join(timeout=10)
    proc.stdout.close()
    outcome, detail, reset = classify(proc.returncode, result, rate_events, killed)
    report = {}
    if status_file.exists():
        try:
            report = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
    cost = (result or {}).get("total_cost_usd")
    con.execute("UPDATE sessions SET ended_at=?, outcome=?, exit_code=?, detail=?, cost_usd=? WHERE id=?",
                (iso(), outcome, proc.returncode, (report.get("summary") or detail)[:2000], cost, session_id))
    con.commit()
    apply_outcome(con, t["issue"], outcome, detail, reset, report)
    if GH_SYNC and t["issue"] != TRIAGE:
        after_outcome(con, t["issue"], session_id, session_started, report)
    set_phase(con, t["issue"], f"session terminée : {outcome}")
    if outcome != "COMPLETED" or not report:
        ctl_set(con, "last_error", redact(f"{iso()} #{t['issue']} {outcome} — {detail}")[:500])
    return outcome


def set_phase(con, issue, text):
    """Phase de travail courante (affichée par `status` et `pulse`), journalisée comme événement."""
    text = redact(" ".join(str(text).split()))[:200]
    ctl_set(con, "phase", text)
    ctl_set(con, "phase_at", iso())
    ctl_set(con, "phase_issue", issue if issue is not None else "")
    event(con, issue, "phase", text)


def claude_auth_ok():
    """True si Claude Code est authentifié pour ce compte (`claude auth status --json` → loggedIn).
    Seul ce booléen est lu : ni jeton, ni adresse, ni organisation ne sont conservés ou affichés."""
    try:
        out = subprocess.run([CLAUDE, "auth", "status", "--json"], capture_output=True, text=True, timeout=60,
                             stdin=subprocess.DEVNULL)
        return out.returncode == 0 and json.loads(out.stdout or "{}").get("loggedIn") is True
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
        return False


def session_preflight():
    """Pourquoi une session ne peut PAS être lancée (texte), ou "" : réglages de garde illisibles,
    Claude Code introuvable ou non authentifié, clone de lancement absent."""
    if CLAUDE_SETTINGS:
        try:
            json.loads(Path(CLAUDE_SETTINGS).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return f"Réglages de garde illisibles ({CLAUDE_SETTINGS}) : aucune session sans garde."
    if not Path(CLAUDE).exists() and not any((Path(d) / CLAUDE).exists() for d in os.environ.get("PATH", "").split(":")):
        return f"Claude Code introuvable ({CLAUDE})."
    if not (LAUNCH_DIR / ".git").exists():
        return f"Clone de lancement absent ({LAUNCH_DIR}) : voir l'installation."
    if REQUIRE_AUTH and not claude_auth_ok():
        return "Authentification Claude absente ou expirée : `claude auth login` par le propriétaire, puis lodyorch resume."
    return ""


def apply_outcome(con, issue, outcome, detail, reset, report):
    t = task(con, issue)
    event(con, issue, "session_end", f"{outcome} — {detail[:300]}")
    if outcome == "RATE_LIMIT":
        until = reset or iso(datetime.fromtimestamp(time.time() + 3600, timezone.utc))
        ctl_set(con, "quota_until", until)
        # Une limite n'est pas un échec du ticket : la tentative ne compte pas.
        update_task(con, issue, status="READY", attempts=max(0, t["attempts"] - 1),
                    next_action=f"Reprendre après la limite fournisseur ({until}).")
        return
    if outcome == "STOPPED" or report.get("status") in STATUSES:
        ctl_set(con, "consecutive_failures", 0)
    if outcome == "STOPPED":
        # Arrêt volontaire : ni échec, ni tentative ; le ticket sera repris tel quel après « resume ».
        update_task(con, issue, status="RUNNING", attempts=max(0, t["attempts"] - 1), not_before="",
                    next_action="Reprendre : session arrêtée volontairement (stop --now).")
        return
    if report.get("status") in STATUSES:
        fields = {k: report[k] for k in ("branch", "pr", "commit_sha", "summary", "tests", "blockers", "next_action",
                                         "deps")
                  if k in report and report[k] is not None}
        fields = {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) for k, v in fields.items()}
        status = report["status"]
        if status in ("DONE", "WAITING_OWNER", "BLOCKED"):
            fields["attempts"] = 0
        update_task(con, issue, status=status, **fields)
        for extra in report.get("enqueue", []) or []:
            try:
                n = int(str(extra.get("issue")).lstrip("#"))
            except (TypeError, ValueError):
                continue
            if n and not task(con, n) and not SESSION_ENQUEUE:
                event(con, n, "proposed", f"Proposé par la session sur #{issue} (non ajouté : file explicite).")
            elif n and not task(con, n):
                con.execute("INSERT INTO tasks(issue,title,priority,deps,note,updated_at) VALUES(?,?,?,?,?,?)",
                            (n, extra.get("title", ""), int(extra.get("priority", 60)), extra.get("deps", ""),
                             extra.get("note", "ajouté par une session"), iso()))
                con.commit()
                event(con, n, "enqueued", f"Ajouté par la session sur #{issue}")
        return
    # Pas de rapport exploitable : tentative comptée, délai progressif, abandon après max_attempts.
    trip_breaker(con, outcome, detail)
    attempts = t["attempts"]
    if attempts >= setting(con, "max_attempts")[0]:
        update_task(con, issue, status="FAILED", blockers=f"{attempts} sessions sans rapport : {outcome} — {detail[:300]}",
                    next_action="Examiner les journaux de session, puis `lodyorch add` pour relancer.")
        event(con, issue, "failed", f"Abandon après {attempts} tentatives.")
        return
    delay = {"TRANSIENT": 300, "CRASH": 900, "TIMEOUT": 600, "ERROR": 1800}.get(outcome, 900) * attempts * BACKOFF_SCALE
    update_task(con, issue, status="RUNNING" if outcome in ("TIMEOUT", "COMPLETED") else "READY",
                not_before=iso(datetime.fromtimestamp(time.time() + delay, timezone.utc)),
                next_action=f"Reprendre (dernière session : {outcome}).")


def trip_breaker(con, outcome, detail):
    """Compte les sessions consécutives sans rapport, tous tickets confondus ; au seuil, pause
    globale plutôt qu'une suite de sessions qui échouent toutes (panne, quota mal détecté…)."""
    failures = int(ctl_get(con, "consecutive_failures", "0") or 0) + 1
    ctl_set(con, "consecutive_failures", failures)
    limit = setting(con, "max_consecutive_failures")[0]
    if failures >= limit:
        ctl_set(con, "mode", "PAUSED")
        ctl_set(con, "pause_reason", f"Disjoncteur : {failures} sessions consécutives sans rapport "
                                     f"(dernière : {outcome} — {detail[:150]}). lodyorch resume pour réarmer.")
        event(con, None, "breaker", f"Pause automatique après {failures} échecs consécutifs.")


# ---------------------------------------------------------------- GitHub : synchronisation et statuts

def gh_list(path):
    """Liste paginée (100 par page) ; None si GitHub ne répond pas."""
    out, sep = [], "&" if "?" in path else "?"
    for page in range(1, 31):
        data = gh_json(f"{path}{sep}per_page=100&page={page}")
        if not isinstance(data, list):
            return None
        out += data
        if len(data) < 100:
            break
    return out


def gh_call(*args):
    try:
        return subprocess.run([GH, "api", *args], capture_output=True, text=True, timeout=60).returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def priority_level(labels):
    """P0…P3 d'après les labels (« P1 », « priorité: P1 », « priority/P1 »…) ; P2 par défaut."""
    levels = [int(m.group(1)) for name in labels
              for m in [re.search(r"(?:^|[\s:/_-])P([0-3])$", name.strip(), re.I) or
                        re.fullmatch(r"P([0-3])", name.strip(), re.I)] if m]
    return min(levels) if levels else DEFAULT_PRIORITY_LEVEL


def parse_deps(body):
    deps = []
    for m in DEPS_RE.finditer(body or ""):
        deps += [int(x) for x in re.findall(r"#(\d+)", m.group(1))]
    return deps


def session_running(con, issue):
    return con.execute("SELECT 1 FROM sessions WHERE issue=? AND ended_at IS NULL", (issue,)).fetchone() is not None


def set_status_label(con, issue, status):
    """Un seul label « statut: … » à la fois sur le ticket ; READY n'en porte aucun."""
    want = STATUS_LABELS.get(status)
    data = gh_json(f"repos/{REPO}/issues/{issue}")
    current = {lbl["name"] for lbl in (data or {}).get("labels", [])}
    ok = data is not None
    for name in set(STATUS_LABELS.values()) - {want}:
        if name in current:
            ok &= gh_call("-X", "DELETE", f"repos/{REPO}/issues/{issue}/labels/{urllib.parse.quote(name)}")
    if want and want not in current:
        ok &= gh_call("-X", "POST", f"repos/{REPO}/issues/{issue}/labels", "-f", f"labels[]={want}")
    if not ok:
        event(con, issue, "gh_error", f"Label de statut non appliqué ({want or 'aucun'}).")
    elif task(con, issue):
        # Verrou visible : on retient que le label « en cours » vient de nous ; posé par quelqu'un
        # d'autre, il met le ticket de côté (voir sync_github).
        update_task(con, issue, own_lock=int(want == STATUS_LABELS.get("RUNNING")),
                    gh_labels=json.dumps(sorted((current - set(STATUS_LABELS.values())) | ({want} if want else set()))))


def sync_github(con):
    """Découvre les tickets ouverts (hors PR), applique priorités, dépendances déclarées et labels
    posés par le propriétaire, retire les tickets fermés, puis repère les réponses du propriétaire."""
    started = iso(datetime.fromtimestamp(time.time() - 120, timezone.utc))
    issues = gh_list(f"repos/{REPO}/issues?state=open")
    if issues is None:
        event(con, None, "sync_error", "GitHub injoignable : synchronisation reportée.")
        ctl_set(con, "last_sync_ts", time.time())
        ctl_set(con, "sync_requested", "")
        return
    open_nums = set()
    for it in issues:
        if "pull_request" in it:
            continue
        n = it["number"]
        open_nums.add(n)
        labels = sorted(lbl["name"] for lbl in it.get("labels", []))
        prio = priority_level(labels) * 10
        deps = parse_deps(it.get("body"))
        t = task(con, n)
        held = set(labels) & HOLD_LABELS
        eligible = not ELIGIBLE_LABEL or ELIGIBLE_LABEL in labels
        if not t and not eligible:
            continue  # hors file automatique : jamais enregistré, jamais traité
        if not eligible:
            held = held | {f"sans {ELIGIBLE_LABEL}"}
        lock = STATUS_LABELS.get("RUNNING")
        if lock and lock in labels and not (t and t["own_lock"]) and not session_running(con, n):
            held = held | {f"{lock} posé hors de {CLI_NAME}"}  # un autre agent ou une personne le traite
        if not t:
            status = ("SUSPENDED" if held else "BLOCKED" if STATUS_LABELS["BLOCKED"] in labels
                      else "WAITING_OWNER" if STATUS_LABELS["WAITING_OWNER"] in labels
                      else "DONE" if STATUS_LABELS["DONE"] in labels else "READY")
            why = (f"Mis de côté sur GitHub ({', '.join(sorted(held))})." if status == "SUSPENDED" else
                   f"Label « {STATUS_LABELS['BLOCKED']} » présent à la découverte." if status == "BLOCKED" else "")
            con.execute("""INSERT INTO tasks(issue,title,status,priority,deps,note,gh_labels,gh_since,blockers,
                           next_action,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (n, it.get("title", ""), status, prio, ",".join(map(str, deps)),
                         "découvert par synchronisation GitHub", json.dumps(labels), iso(), why, why, iso()))
            con.commit()
            event(con, n, "discovered", f"Ticket découvert ({status}, P{prio // 10}).")
            if status == "READY" and "READY" in STATUS_LABELS:
                set_status_label(con, n, "READY")
            continue
        fields = {"title": it.get("title", "") or t["title"], "gh_labels": json.dumps(labels)}
        if t["priority"] != prio:
            fields["priority"] = prio
            event(con, n, "priority", f"Priorité GitHub : P{prio // 10}.")
        known = {int(x) for x in (t["deps"] or "").replace("#", "").split(",") if x.strip().isdigit()}
        if set(deps) - known:
            fields["deps"] = ",".join(map(str, sorted(known | set(deps))))
        if not session_running(con, n):
            added = set(labels) - set(json.loads(t["gh_labels"] or "[]"))
            if held and t["status"] not in ("SUSPENDED", "DONE"):
                fields.update(status="SUSPENDED", next_action=f"Suspendu sur GitHub ({', '.join(sorted(held))}).")
                event(con, n, "suspended", f"Label {', '.join(sorted(held))} : ticket mis de côté.")
            elif not held and t["status"] == "SUSPENDED":
                fields.update(status="READY", attempts=0, not_before="", next_action="Label de suspension retiré.")
                event(con, n, "resumed", "Suspension levée sur GitHub : remis en file.")
            elif t["status"] in ("BLOCKED", "FAILED") and STATUS_LABELS["BLOCKED"] not in labels \
                    and STATUS_LABELS["BLOCKED"] in json.loads(t["gh_labels"] or "[]"):
                # Le propriétaire a retiré le label de blocage : décision explicite de reprise.
                fields.update(status="READY", attempts=0, not_before="", gh_since=iso(),
                              next_action=f"Label « {STATUS_LABELS['BLOCKED']} » retiré sur GitHub : reprendre.")
                event(con, n, "resumed", f"Label {STATUS_LABELS['BLOCKED']} retiré : ticket remis en file.")
            elif t["status"] in ("READY", "RUNNING"):
                for status in ("BLOCKED", "WAITING_OWNER", "DONE"):
                    if STATUS_LABELS[status] in added:
                        fields.update(status=status, gh_since=iso(),
                                      next_action=f"Label « {STATUS_LABELS[status]} » posé sur GitHub.")
                        event(con, n, "label", f"Statut imposé par label GitHub : {status}.")
                        break
        update_task(con, n, **fields)
    for t in con.execute("SELECT issue FROM tasks WHERE status NOT IN ('DONE') AND issue != ?", (TRIAGE,)).fetchall():
        if t["issue"] not in open_nums and not session_running(con, t["issue"]):
            state, _ = issue_state(t["issue"])
            if state == "closed":
                update_task(con, t["issue"], status="DONE", next_action="Ticket fermé sur GitHub.")
                event(con, t["issue"], "closed", "Ticket fermé sur GitHub : retiré de la file.")
    scan_replies(con)
    ctl_set(con, "last_sync_ts", time.time())
    ctl_set(con, "last_sync", iso())
    ctl_set(con, "sync_requested", "")
    ctl_set(con, "comments_since", started)


def scan_replies(con):
    """Un nouveau commentaire du propriétaire (ou d'un collaborateur) sur un ticket bloqué, à tester
    ou en échec relance ce ticket, une seule fois par commentaire. Ignorés : les commentaires de
    l'orchestrateur (marqueur), ceux d'auteurs externes, ceux antérieurs à l'état d'attente."""
    since = ctl_get(con, "comments_since") or iso(datetime.fromtimestamp(time.time() - 86400, timezone.utc))
    comments = gh_list(f"repos/{REPO}/issues/comments?since={since}&sort=created&direction=asc")
    for c in comments or []:
        try:
            n = int(c["issue_url"].rsplit("/", 1)[1])
        except (KeyError, ValueError):
            continue
        t = task(con, n)
        if not t or c["id"] <= (t["gh_cursor"] or 0):
            continue
        update_task(con, n, gh_cursor=c["id"])
        if BOT_MARKER in (c.get("body") or "") or c.get("author_association") not in TRUSTED_AUTHORS:
            continue
        if t["status"] not in ("BLOCKED", "WAITING_OWNER", "FAILED") or c["created_at"] <= (t["gh_since"] or ""):
            continue
        if session_running(con, n):
            continue
        update_task(con, n, status="READY", attempts=0, not_before="",
                    note=f"Nouveau commentaire du propriétaire après l'état {t['status']} : {c.get('html_url', '')} "
                         f"— le lire et en tenir compte (réponse, décision ou retour de test).",
                    next_action="Reprendre : réponse du propriétaire sur GitHub.")
        event(con, n, "reply", f"Réponse détectée ({c.get('html_url', '')}) : ticket {t['status']} → READY.")
        set_status_label(con, n, "READY")


def after_outcome(con, issue, session_id, session_started, report):
    """Statut GitHub et notification selon l'état final de la session."""
    t = task(con, issue)
    status = t["status"]
    if status in ("BLOCKED", "WAITING_OWNER", "FAILED", "DONE"):
        update_task(con, issue, gh_since=session_started,
                    questions=json.dumps([str(q) for q in report.get("questions") or []], ensure_ascii=False),
                    test_links=json.dumps([str(x) for x in report.get("test_links") or []], ensure_ascii=False),
                    report_url=str(report.get("report_url") or t["report_url"] or ""))
    set_status_label(con, issue, status if status in STATUS_LABELS else "RUNNING")
    if status not in ("WAITING_OWNER", "BLOCKED", "DONE", "FAILED"):
        return
    questions = [str(q) for q in (report.get("questions") or []) if str(q).strip()]
    if status == "BLOCKED" and not questions and t["blockers"]:
        questions = [t["blockers"]]
    if status == "FAILED" and not questions:
        questions = [t["blockers"] or "Échec répété des sessions : voir les journaux."]
    kind = {"WAITING_OWNER": "Livré — en attente de validation", "DONE": "Terminé",
            "FAILED": "Échec — intervention nécessaire"}.get(status, "Question bloquante")
    state = t["commit_sha"] or session_id if status in ("WAITING_OWNER", "DONE") else "\n".join(questions)
    key = f"{issue}:{kind}:{hashlib.sha256(state.encode()).hexdigest()[:16]}"
    links = [str(x) for x in (report.get("test_links") or [])]
    lines = [f"Ticket #{issue} — {t['title']}",
             f"État : {STATE_TEXT.get(status, 'bloqué — décision nécessaire')}",
             "", "Résultat :", t["summary"] or "(non renseigné)", ""]
    if status in ("WAITING_OWNER", "DONE"):
        lines += [f"SHA livré : {t['commit_sha'] or '?'}", "", "Liens à tester :"]
        lines += [f"- {x}" for x in links] or ["- (voir le rapport)"]
        lines.append("")
    if report.get("remaining"):
        lines += ["Reste à faire / limites :", str(report["remaining"]), ""]
    lines += ["Questions :"] + ([f"{i}. {q}" for i, q in enumerate(questions, 1)] or
                                ["Aucune décision utilisateur nécessaire."])
    lines += ["", f"Contrôles : {t['tests'] or '(voir le rapport)'}", "",
              f"Ticket : https://github.com/{REPO}/issues/{issue}",
              f"Rapport : {report.get('report_url') or '(voir le ticket)'}"]
    if SITE_URL:
        lines.append(f"Site : {SITE_URL}")
    lines += ["", "Répondre dans le ticket GitHub : la réponse est détectée et le travail reprend automatiquement."]
    attachment_name, attachment = "", ""
    path = Path(str(report.get("report_file") or ""))
    if report.get("report_file") and path.is_file():
        attachment_name, attachment = path.name, path.read_text(encoding="utf-8", errors="replace")
    elif report.get("report_file"):
        event(con, issue, "notify", f"Rapport à joindre introuvable : {path}")
    queue_notification(con, key, issue, kind, f"{NOTIFY_PREFIX} #{issue} — {kind}", "\n".join(lines),
                       attachment_name, attachment)


# ---------------------------------------------------------------- notifications email

STATE_TEXT = {"WAITING_OWNER": os.environ.get("LQ_WAITING_TEXT", "installé, à tester"),
              "DONE": "terminé", "FAILED": "en échec — intervention nécessaire"}

def queue_notification(con, key, issue, kind, subject, body, attachment_name="", attachment=""):
    """Mise en file (une seule fois par état : clé unique). L'envoi a lieu dans la boucle."""
    if not NOTIFY_SMTP_ENV:
        return
    cur = con.execute("""INSERT OR IGNORE INTO notifications(key,issue,kind,subject,body,attachment_name,attachment,
                         created_at) VALUES(?,?,?,?,?,?,?,?)""",
                      (key, issue, kind, subject, body, attachment_name, attachment, iso()))
    con.commit()
    if cur.rowcount:
        event(con, issue, "notify", f"Email en file : {kind}.")
    flush_notifications(con)


def smtp_settings():
    values = {}
    with open(NOTIFY_SMTP_ENV, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip("'").strip('"')
    get = lambda name: values.get("PLUGADMIN_SMTP_" + name) or values.get("SMTP_" + name)  # noqa: E731
    cfg = {k: get(k) for k in ("HOST", "PORT", "USERNAME", "PASSWORD", "FROM", "TO")}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise ValueError(f"configuration SMTP incomplète ({', '.join(missing)})")
    return cfg


def send_mail(subject, body, attachment_name="", attachment=""):
    """Même transport que l'outil SMTP existant (SMTPS, Date et Message-ID explicites)."""
    cfg = smtp_settings()
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["FROM"], cfg["TO"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=cfg["FROM"].split("@", 1)[-1])
    msg.set_content(body)
    if attachment_name:
        msg.add_attachment(attachment.encode("utf-8"), maintype="text", subtype="markdown", filename=attachment_name)
    with smtplib.SMTP_SSL(cfg["HOST"], int(cfg["PORT"]), context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(cfg["USERNAME"], cfg["PASSWORD"])
        refused = smtp.send_message(msg)
    if refused:
        raise RuntimeError(f"{len(refused)} destinataire(s) refusé(s)")


def flush_notifications(con):
    """Envoie ce qui est en attente ; un échec n'arrête jamais la file : nouvel essai plus tard
    (délai croissant, 30 essais au plus). Le message d'erreur ne contient jamais d'identifiant."""
    for nf in con.execute("""SELECT * FROM notifications WHERE sent_at IS NULL AND attempts < 30
                             AND (next_try = '' OR next_try <= ?) ORDER BY created_at""", (iso(),)).fetchall():
        # Réservation atomique : le superviseur et la commande `notify` peuvent vider la file en même
        # temps ; seul celui qui réserve la ligne l'envoie (évite les emails en double).
        claim = con.execute("""UPDATE notifications SET next_try=? WHERE key=? AND sent_at IS NULL
                               AND (next_try = '' OR next_try <= ?)""",
                            (iso(datetime.fromtimestamp(time.time() + 600, timezone.utc)), nf["key"], iso()))
        con.commit()
        if claim.rowcount != 1:
            continue
        try:
            send_mail(nf["subject"], nf["body"], nf["attachment_name"], nf["attachment"])
        except Exception as exc:  # noqa: BLE001
            attempts = nf["attempts"] + 1
            delay = min(6 * 3600, 120 * 2 ** attempts)
            con.execute("UPDATE notifications SET attempts=?, next_try=?, last_error=? WHERE key=?",
                        (attempts, iso(datetime.fromtimestamp(time.time() + delay, timezone.utc)),
                         type(exc).__name__, nf["key"]))
            con.commit()
            event(con, nf["issue"], "notify_error", f"Échec d'envoi ({type(exc).__name__}), essai {attempts}.")
            continue
        con.execute("UPDATE notifications SET sent_at=?, attempts=attempts+1 WHERE key=?", (iso(), nf["key"]))
        con.commit()
        event(con, nf["issue"], "notify", f"Email envoyé : {nf['subject']}")


# ---------------------------------------------------------------- boucle

def recover_interrupted(con):
    """Après un arrêt brutal du superviseur : une session orpheline est arrêtée (plus personne ne la
    surveille) et notée INTERRUPTED ; son ticket reste RUNNING et sera repris en premier, depuis
    l'état réel (Git, GitHub), jamais en rejouant ce que l'ancienne session disait avoir fait."""
    for s in con.execute("SELECT * FROM sessions WHERE ended_at IS NULL").fetchall():
        if s["pid"] and Path(f"/proc/{s['pid']}").exists():
            try:
                os.killpg(s["pid"], signal.SIGTERM)
                time.sleep(5)
                if Path(f"/proc/{s['pid']}").exists():
                    os.killpg(s["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        con.execute("UPDATE sessions SET ended_at=?, outcome='INTERRUPTED', detail=? WHERE id=?",
                    (iso(), "Superviseur redémarré pendant la session.", s["id"]))
        con.commit()
        event(con, s["issue"], "recovered", f"Session {s['id']} interrompue par un redémarrage du superviseur.")
        ctl_set(con, "last_error", f"{iso()} #{s['issue']} INTERRUPTED — superviseur redémarré pendant la session "
                                   "(reprise du ticket en premier).")


def pause_for(con, seconds):
    """Attente qui rend la main dès qu'un arrêt, une reprise ou un rechargement est demandé."""
    end = time.time() + seconds
    start_mode = ctl_get(con, "mode", "RUNNING")
    while time.time() < end:
        if ctl_get(con, "mode", "RUNNING") != start_mode or ctl_get(con, "reload_requested") \
                or ctl_get(con, "sync_requested"):
            return
        time.sleep(min(2, max(0.1, end - time.time())))


def sessions_today(con):
    return con.execute("SELECT COUNT(*) c FROM sessions WHERE started_at >= ?", (iso(day_start(con)),)).fetchone()["c"]


def cost_today(con):
    return con.execute("SELECT COALESCE(SUM(cost_usd),0) s FROM sessions WHERE started_at >= ?",
                       (iso(day_start(con)),)).fetchone()["s"]


def code_version():
    """Empreinte du fichier exécuté : `status` la compare à celle du superviseur en cours."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def reexec(con):
    """Recharge le code installé dans le même processus (même PID pour systemd). Appelé seulement
    entre deux sessions ; le verrou est libéré par l'exec (descripteur non hérité) puis repris."""
    ctl_set(con, "reload_requested", "")
    event(con, None, "supervisor", "Rechargement du code demandé : ré-exécution entre deux sessions.")
    con.close()
    os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), "run"])


def supervise():
    con = db()
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Un autre superviseur tourne déjà.", file=sys.stderr)
        return 0
    lock.write(str(os.getpid()))
    lock.flush()
    mode = ctl_get(con, "mode", "RUNNING")
    if mode in ("STOP", "STOP_NOW"):
        # Arrêt explicite : le service ne se réactive pas tout seul, même relancé par systemd.
        event(con, None, "supervisor", "Démarrage refusé : arrêt demandé (lodyorch resume pour reprendre).")
        return 0
    recover_interrupted(con)
    ctl_set(con, "supervisor_pid", os.getpid())
    ctl_set(con, "supervisor_started", iso())
    ctl_set(con, "supervisor_version", code_version())
    if not ctl_get(con, "provider_usage"):
        usage = provider_usage(con)
        if usage:
            ctl_set(con, "provider_usage", json.dumps(usage, ensure_ascii=False))
    event(con, None, "supervisor", f"Superviseur démarré (code {code_version()}).")
    while True:
        mode = ctl_get(con, "mode", "RUNNING")
        if mode in ("STOP", "STOP_NOW"):
            event(con, None, "supervisor", "Arrêt demandé : superviseur arrêté.")
            ctl_set(con, "supervisor_pid", "")
            return 0
        if ctl_get(con, "reload_requested"):
            reexec(con)
        if GH_SYNC:
            last_sync = float(ctl_get(con, "last_sync_ts", "0") or 0)
            if time.time() - last_sync > SYNC_EVERY_SECONDS or ctl_get(con, "sync_requested"):
                try:
                    sync_github(con)
                except Exception as exc:  # noqa: BLE001 — une synchro ratée ne doit jamais arrêter la file
                    event(con, None, "sync_error", f"{type(exc).__name__}: {exc}"[:500])
                    ctl_set(con, "last_sync_ts", time.time())
        if NOTIFY_SMTP_ENV:
            flush_notifications(con)
        blocker = start_blocker(con)
        if blocker:
            reason, until = blocker
            ctl_set(con, "idle_reason", reason + (f" Reprise au plus tôt : {until}." if until else ""))
            pause_for(con, POLL_SECONDS)
            continue
        t = next_task(con)
        if t is None:
            last = ctl_get(con, "last_triage")
            if TRIAGE_ENABLED and (not last or time.time() - float(last) > TRIAGE_EVERY_SECONDS):
                ctl_set(con, "last_triage", time.time())
                if not task(con, TRIAGE):
                    con.execute("INSERT INTO tasks(issue,title,priority,updated_at) VALUES(0,'Tri du backlog',99,?)",
                                (iso(),))
                    con.commit()
                update_task(con, TRIAGE, status="READY", attempts=0, not_before="")
                continue
            later = con.execute("""SELECT issue, not_before FROM tasks WHERE status IN ('READY','RUNNING','TESTING')
                                   AND not_before > ? ORDER BY not_before LIMIT 1""", (iso(),)).fetchone()
            ctl_set(con, "idle_reason", f"En attente : #{later['issue']} reprend après {later['not_before']}." if later
                    else "Aucun ticket éligible : tous terminés, bloqués ou en attente du propriétaire.")
            pause_for(con, POLL_SECONDS * 10)
            continue
        refused = session_preflight()
        if refused:
            # Jamais de session sans authentification ni garde : on attend que le propriétaire corrige.
            if ctl_get(con, "preflight_error") != refused:
                event(con, None, "preflight", refused)
                ctl_set(con, "last_error", f"{iso()} {refused}")
            ctl_set(con, "preflight_error", refused)
            ctl_set(con, "idle_reason", f"Session refusée : {refused}")
            pause_for(con, POLL_SECONDS * 10)
            continue
        ctl_set(con, "preflight_error", "")
        ctl_set(con, "idle_reason", "")
        run_session(con, t)
        if GH_SYNC:
            ctl_set(con, "sync_requested", "1")


# ---------------------------------------------------------------- CLI

def local(ts_iso):
    """Horodatage UTC suivi de l'heure locale de la machine, pour le propriétaire."""
    dt = datetime.strptime(ts_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return f"{ts_iso} ({dt.astimezone().strftime('%d/%m %H:%M %Z')})"


def cmd_status(con, _):
    mode = ctl_get(con, "mode", "RUNNING")
    pid = ctl_get(con, "supervisor_pid")
    alive = bool(pid) and Path(f"/proc/{pid}").exists()
    running = ctl_get(con, "supervisor_version")
    print(f"Mode : {mode} — superviseur {'actif (pid ' + pid + ', code ' + (running or '?') + ')' if alive else 'arrêté'}")
    if alive and running != code_version():
        print(f"  ⚠ code installé ({code_version()}) différent de celui en cours : "
              + ("« lodyorch reload » l'appliquera entre deux sessions." if running else
                 "superviseur antérieur à #380, sans rechargement : redémarrage unique nécessaire (voir README)."))
    if ctl_get(con, "reload_requested"):
        print(f"  Rechargement demandé à {ctl_get(con, 'reload_requested')} : appliqué à la fin de la session en cours.")
    cur = con.execute("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    if cur:
        t = task(con, cur["issue"])
        print(f"Session en cours : #{cur['issue']} {t['title'] if t else ''} — depuis {cur['started_at']}, "
              f"dernière activité {t['last_activity'] if t else '?'} — journal {cur['log_path']}")
    blocker = start_blocker(con)
    if blocker:
        print(f"Prochaine session : bloquée — {blocker[0]}" + (f" Reprise au plus tôt : {local(blocker[1])}." if blocker[1] else ""))
    else:
        print("Prochaine session : autorisée" + (" (après la session en cours)." if cur else "."))
    if ctl_get(con, "idle_reason") and not cur:
        print(f"Dernier motif d'attente du superviseur : {ctl_get(con, 'idle_reason')}")
    cfg = {name: setting(con, name) for name in SETTINGS}
    cap, cap_origin = cfg["max_sessions_per_day"]
    budget = cfg["max_cost_usd_per_day"][0]
    print(f"Politique locale (jour {getattr(day_tz(con), 'key', 'UTC')}, remise à zéro {local(iso(next_utc_day(con)))}) :")
    print(f"  sessions aujourd'hui : {sessions_today(con)} / {cap or 'sans plafond'} — origine : {cap_origin}"
          + (" ⚠ ignoré faute de garde-fou, plafond par défaut appliqué" if policy_error(settings(con)) else ""))
    print(f"  coût estimé du jour : {cost_today(con):.2f} USD (prix catalogue, pas une facture) / "
          + (f"{budget:.2f} USD ({cfg['max_cost_usd_per_day'][1]})" if budget > 0 else "pas de plafond"))
    print(f"  disjoncteur : {ctl_get(con, 'consecutive_failures', '0') or 0} / "
          f"{cfg['max_consecutive_failures'][0]} sessions consécutives sans rapport")
    usage = provider_usage(con)
    q = ctl_get(con, "quota_until")
    if q and q > iso():
        print(f"Limite fournisseur atteinte : aucune session avant {local(q)}")
    if usage:
        print(f"Fournisseur — dernière valeur annoncée par Claude Code ({usage.get('observed_at', '?')}) :")
        for window, key, label in (("five_hour", "max_five_hour_utilization", "fenêtre 5 h"),
                                   ("seven_day", "max_seven_day_utilization", "fenêtre 7 j")):
            win = (usage.get("unifiedWindows") or {}).get(window)
            if win and win.get("utilization") is not None:
                reset = iso(datetime.fromtimestamp(int(win["resetsAt"]), timezone.utc)) if win.get("resetsAt") else "?"
                print(f"  {label} : {win['utilization']:.0%} (seuil local {cfg[key][0]:.0%}), "
                      f"remise à zéro {local(reset) if reset != '?' else '?'}")
        print(f"  statut {usage.get('status', '?')}, dépassement payant : {usage.get('overageStatus', '?')}"
              + (f" ({usage['overageDisabledReason']})" if usage.get("overageDisabledReason") else ""))
        print("  Instantané de la dernière session : l'usage interactif depuis n'y figure pas ; "
              "le quota restant réel n'est pas connu.")
    else:
        print("Fournisseur : aucune donnée de quota observée (rien n'est déduit de l'interface claude.ai).")
    counts = con.execute("SELECT status, COUNT(*) c FROM tasks GROUP BY status").fetchall()
    print("Tickets : " + ", ".join(f"{r['status']} {r['c']}" for r in counts))
    if GH_SYNC:
        print(f"Synchronisation GitHub : dernière {ctl_get(con, 'last_sync') or 'jamais'} (toutes les {SYNC_EVERY_SECONDS} s)")
    pending = con.execute("SELECT COUNT(*) c FROM notifications WHERE sent_at IS NULL").fetchone()["c"]
    if NOTIFY_SMTP_ENV or pending:
        print(f"Emails en attente d'envoi : {pending} ({CLI_NAME} notifications)")
    print("Réglages : lodyorch config")


def ago(ts_iso):
    if not ts_iso:
        return "?"
    try:
        sec = time.time() - datetime.strptime(ts_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return "?"
    if sec < 90:
        return f"il y a {int(sec)} s"
    if sec < 5400:
        return f"il y a {int(sec // 60)} min"
    if sec < 2 * 86400:
        return f"il y a {sec / 3600:.1f} h"
    return f"il y a {int(sec // 86400)} j"


def localtime(ts_iso):
    """Heure locale du fuseau des plafonds, pour le propriétaire."""
    try:
        dt = datetime.strptime(ts_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "?"
    return dt.astimezone(day_tz(con_for_display)).strftime("%d/%m %H:%M")


con_for_display = None


def jlist(raw):
    try:
        value = json.loads(raw or "[]")
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def ts_of(ts_iso):
    try:
        return datetime.strptime(ts_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def span(sec):
    """Durée lisible (« 42 s », « 17 min », « 3 h 05 », « 2 j 4 h »)."""
    sec = int(max(0, sec))
    if sec < 90:
        return f"{sec} s"
    if sec < 5400:
        return f"{sec // 60} min"
    h, m = divmod(sec // 60, 60)
    return f"{h} h {m:02d}" if h < 48 else f"{h // 24} j {h % 24} h"


def ro_run(args, timeout=5):
    """Commande de consultation (systemctl show, tmux has-session, journalctl) : (code, sortie)
    ou (None, '') si elle est indisponible. Jamais d'action sur les services."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return out.returncode, out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, ""


# Unités et session tmux affichées par `status` (consultation seulement), par instance.
STATUS_UNITS = [u.strip() for u in os.environ.get("LQ_STATUS_UNITS", "").split(",") if u.strip()]
TMUX_SESSION = os.environ.get("LQ_TMUX_SESSION", "")
# Au-delà de ce délai sans aucune sortie, une session est signalée « à surveiller » (attente
# possible : tests, installation, appel long) ; elle n'est « figée » qu'au seuil de coupure.
QUIET_WARN_SECONDS = int(os.environ.get("LQ_STATUS_QUIET_WARN", "900"))


def ptitle(t):
    """Titre avec sa priorité, sans la répéter si le titre GitHub commence déjà par « [Pn] »."""
    title = (t["title"] or "") if t else ""
    return title if title.startswith("[P") else f"[P{t['priority'] // 10}] {title}" if t else "?"


def status_db():
    """Connexion en LECTURE SEULE pour `status` : ni création de schéma, ni migration, ni écriture."""
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


LIMIT_KINDS = ("Plafond local", "Budget estimé", "Fenêtre fournisseur", "Limite fournisseur")


def last_claude_action(log_path):
    """Dernière action de Claude lue à la fin du journal de session (64 Ko au plus) : outil appelé
    et sa description, ou « réflexion » ; None si rien n'est lisible."""
    try:
        with open(log_path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(log_path) - 65536))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()[1:]
    except (OSError, TypeError):
        return None
    pending = {}
    last = None
    for line in lines:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (msg.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            kind = part.get("type")
            if kind == "tool_use":
                inp = part.get("input") or {}
                what = inp.get("description") or inp.get("command") or inp.get("file_path") or inp.get("pattern") \
                    or inp.get("prompt") or ""
                what = " ".join(str(what).split())[:70]
                pending[part.get("id")] = True
                last = ("outil", part.get("name", "?"), what, part.get("id"))
            elif kind == "tool_result":
                pending.pop(part.get("tool_use_id"), None)
            elif kind == "thinking":
                last = ("réflexion", "", "", None)
            elif kind == "text" and part.get("text", "").strip():
                last = ("texte", "", " ".join(part["text"].split())[:70], None)
    if not last:
        return None
    if last[0] == "outil":
        return f"{'exécute' if last[3] in pending else 'a exécuté'} {last[1]}" + (f" « {last[2]} »" if last[2] else "")
    return "réfléchit" if last[0] == "réflexion" else f"écrit « {last[2]} »"


def observe(con):
    """Photographie en LECTURE SEULE de l'instance, partagée par `status` et `pulse` : même état,
    mêmes raisons, mêmes seuils. Aucune écriture, aucun appel réseau."""
    o = {"nowts": time.time()}
    nowts = o["nowts"]
    cfg = {name: setting(con, name) for name in SETTINGS}
    o["cfg"] = cfg
    o["mode"] = mode = ctl_get(con, "mode", "RUNNING")
    o["pid"] = pid = ctl_get(con, "supervisor_pid")
    o["alive"] = alive = bool(pid) and Path(f"/proc/{pid}").exists()
    rc, svc = ro_run(["systemctl", "show", f"{SERVICE}.service", "--no-pager", "-p", "ActiveState", "--value"])
    o["service_state"] = svc if rc == 0 and svc else "indisponible"
    o["cur"] = cur = con.execute("SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    o["cur_task"] = cur_task = task(con, cur["issue"]) if cur else None
    o["blocker"] = blocker = start_blocker(con)
    o["start"] = iso(day_start(con))
    o["used"] = sessions_today(con)
    cap, cap_origin = cfg["max_sessions_per_day"]
    if policy_error(settings(con)):
        cap, cap_origin = SETTINGS["max_sessions_per_day"][1], "défaut du code (réglage sans garde-fou ignoré)"
    o["cap"], o["cap_origin"] = cap, cap_origin
    o["breaker"] = breaker = int(ctl_get(con, "consecutive_failures", "0") or 0)
    o["breaker_max"] = breaker_max = cfg["max_consecutive_failures"][0]

    # Session en cours : progression réelle (sortie du journal), pas seulement « service actif ».
    h = {"frozen": "", "quiet": None, "action": None}
    if cur:
        spid = cur["pid"]
        h["spid"], h["palive"] = spid, bool(spid) and Path(f"/proc/{spid}").exists()
        log = Path(cur["log_path"]) if cur["log_path"] else None
        last_out = log.stat().st_mtime if log and log.exists() else ts_of(cur_task["last_activity"] if cur_task else "")
        h["quiet"] = nowts - last_out if last_out else None
        h["elapsed"] = nowts - (ts_of(cur["started_at"]) or nowts)
        h["idle_cut"], h["max_cut"] = cfg["session_idle_seconds"][0], cfg["session_max_seconds"][0]
        h["action"] = last_claude_action(log) if log and log.exists() else None
        if not h["palive"]:
            h["frozen"] = f"BLOCAGE CONFIRMÉ : session #{cur['issue']} ouverte mais processus {spid or '?'} absent."
        elif not alive:
            h["frozen"] = f"BLOCAGE CONFIRMÉ : session #{cur['issue']} sans superviseur pour la surveiller."
        elif h["quiet"] is not None and h["quiet"] >= h["idle_cut"] + 120:
            h["frozen"] = (f"BLOCAGE CONFIRMÉ : aucune sortie depuis {span(h['quiet'])}, au-delà du seuil de coupure "
                           f"({span(h['idle_cut'])}) que le superviseur aurait dû appliquer.")
        elif h["elapsed"] >= h["max_cut"] + 300:
            h["frozen"] = f"BLOCAGE CONFIRMÉ : durée {span(h['elapsed'])} au-delà du maximum ({span(h['max_cut'])})."
    o["health"] = h
    frozen = h["frozen"]

    o["last_done"] = con.execute("""SELECT s.*, t.title, t.status AS tstatus FROM sessions s LEFT JOIN tasks t ON t.issue = s.issue
                                    WHERE s.ended_at IS NOT NULL AND s.outcome = 'COMPLETED'
                                    ORDER BY s.ended_at DESC LIMIT 1""").fetchone()
    marks = [(r["m"], label) for r, label in (
        (con.execute("SELECT MAX(ended_at) m FROM sessions").fetchone(), "fin de session"),
        (con.execute("SELECT MAX(started_at) m FROM sessions").fetchone(), "début de session"),
        (con.execute("SELECT MAX(last_activity) m FROM tasks").fetchone(), "sortie d'une session Claude"),
    ) if r["m"]]
    if cur and cur["log_path"] and Path(cur["log_path"]).exists():
        marks.append((iso(datetime.fromtimestamp(Path(cur["log_path"]).stat().st_mtime, timezone.utc)), "sortie de la session en cours"))
    o["last_mark"] = max(marks) if marks else None
    o["queue_rows"] = queue_rows = [
        r for r in con.execute("""SELECT * FROM tasks WHERE status IN ('READY','RUNNING','TESTING') AND issue != ?
                                  ORDER BY CASE status WHEN 'READY' THEN 1 ELSE 0 END, priority, issue""",
                               (TRIAGE,)).fetchall() if not (cur and r["issue"] == cur["issue"])]
    o["blocked"] = blocked = con.execute("SELECT * FROM tasks WHERE status IN ('BLOCKED','FAILED') ORDER BY priority, issue").fetchall()
    o["waiting"] = con.execute("SELECT COUNT(*) c FROM tasks WHERE status='WAITING_OWNER'").fetchone()["c"]
    local_ok = {r["issue"] for r in con.execute(
        f"SELECT issue FROM tasks WHERE status IN ({','.join('?' * len(DEPS_DONE_STATES))})", DEPS_DONE_STATES)}
    o["next"] = None
    for t in queue_rows:
        if t["not_before"] and t["not_before"] > iso():
            continue
        if all(int(d) in local_ok for d in re.findall(r"\d+", t["deps"] or "")):
            o["next"] = t
            break

    # État et catégorie d'inactivité (pulse) : repos normal, quota, session bloquée, erreur, validation.
    if not alive and mode in ("STOP", "STOP_NOW") and not cur:
        state, why, cat = "ARRÊTÉ", (f"arrêt demandé par le propriétaire — {CLI_NAME} resume puis "
                                     f"sudo systemctl start {SERVICE} pour reprendre."), "arrêt demandé"
    elif o["service_state"] not in ("active", "indisponible") or not alive:
        state, why, cat = "EN ERREUR", "superviseur arrêté ou service inactif : aucune session ne peut démarrer.", "erreur"
    elif frozen:
        state, why, cat = "EN ERREUR", frozen, "session bloquée"
    elif mode == "PAUSED" and breaker >= breaker_max:
        state, why, cat = "EN ERREUR", (f"disjoncteur déclenché ({breaker} sessions consécutives sans rapport) — "
                                        f"{CLI_NAME} resume après analyse."), "erreur"
    elif cur:
        state, why, cat = "ACTIF", f"session Claude en cours sur #{cur['issue']}.", ""
    elif blocker and blocker[0].startswith(LIMIT_KINDS):
        state, why, cat = "LIMITÉ", blocker[0], "attente de quota"
    elif mode != "RUNNING":
        state, why, cat = "BLOQUÉ", f"mode {mode} — " + (ctl_get(con, "pause_reason") or f"{CLI_NAME} resume pour reprendre."), \
            "en pause"
    elif not queue_rows and (blocked or o["waiting"]):
        state, why, cat = "BLOQUÉ", (f"aucun ticket traitable : {len(blocked)} bloqué(s) et {o['waiting']} livré(s) "
                                     f"attendent votre réponse ou validation."), "attente de validation"
    elif blocker:
        state, why, cat = "AU REPOS", blocker[0], "repos normal"
    elif not queue_rows:
        state, why, cat = "AU REPOS", "file vide : aucun ticket ouvert à traiter (synchronisation GitHub périodique).", "repos normal"
    else:
        state, why, cat = "AU REPOS", (ctl_get(con, "idle_reason") or "session suivante au prochain tour du superviseur."), \
            "repos normal"
    o["state"], o["why"], o["category"] = state, why, cat
    return o


def pr_ref(pr):
    """« PR #123 » depuis une URL ou un numéro ; « ? » si inconnu."""
    m = re.search(r"(\d+)\s*$", str(pr or ""))
    return f"PR #{m.group(1)}" if m else "PR ?"


def deploy_state():
    """État du déploiement, en lecture seule et sans réseau externe : version servie (route locale
    /api/version), et commits de `origin/develop` (référence locale) pas encore en production."""
    served = "indisponible"
    url = os.environ.get("LQ_STATUS_VERSION_URL", "")
    if url:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                served = json.loads(resp.read().decode()).get("version", "?").split("+")[-1][:7] or "?"
        except (OSError, ValueError):
            served = "injoignable"
    pending = None
    if served not in ("indisponible", "injoignable", "?"):
        rc, out = ro_run(["git", "-C", str(LAUNCH_DIR), "rev-list", "--count", "--no-merges", f"{served}..origin/develop"])
        pending = int(out) if rc == 0 and out.isdigit() else None
    return served, pending


def cmd_pulse(con, _):
    """Point d'activité en 2 ou 3 lignes pour la console (toutes les 5 min) : état, ticket, activité
    RÉELLE de Claude, plafond du jour. Lecture seule : ni email, ni GitHub, ni session lancée."""
    global con_for_display
    con_for_display = con
    o = observe(con)
    h, cur, t = o["health"], o["cur"], o["cur_task"]
    stamp = now().astimezone(day_tz(con)).strftime("%d/%m %H:%M")
    quota = f"{o['used']}/{o['cap']}" if o["cap"] else f"{o['used']} (sans plafond)"
    if o["cap"] and o["used"] >= o["cap"]:
        quota += " ⚠ atteint"
    elif o["cap"] and o["used"] >= 0.8 * o["cap"]:
        quota += f" ⚠ {o['used'] / o['cap']:.0%}"
    quiet = h["quiet"]
    silent = bool(cur) and quiet is not None and quiet >= QUIET_WARN_SECONDS
    state = o["state"] + (f", SANS ACTIVITÉ depuis {span(quiet)}" if silent and not h["frozen"] else "")
    if cur:
        ticket = f"#{cur['issue']} {ptitle(t)[:58]}"
    elif o["next"]:
        ticket = f"prochain : #{o['next']['issue']} {ptitle(o['next'])[:52]}"
    else:
        ticket = "aucun ticket traitable"
    usage = freshest_usage(con)
    wins = usage.get("unifiedWindows") or {}
    parts = []
    for key, label in (("five_hour", "5 h"), ("seven_day", "7 j")):
        util = (wins.get(key) or {}).get("utilization")
        parts.append(f"{label} {util:.0%}" if util is not None else f"{label} ?")
    quota_txt = "quota Claude " + " / ".join(parts) if usage else "quota Claude indisponible"
    q = ctl_get(con, "quota_until")
    if q and q > iso():
        quota_txt += f" ⚠ LIMITE jusqu'à {localtime(q)}"
    print(f"[{stamp}] {INSTANCE} {state} · {ticket} · sessions {quota} · {quota_txt}")
    if cur:
        print(f"  Claude : {h['action'] or 'activité illisible dans le journal'} — dernière activité réelle "
              + (f"il y a {span(quiet)}" if quiet is not None else "inconnue"))
    else:
        mark = o["last_mark"]
        since = span(o["nowts"] - (ts_of(mark[0]) or o["nowts"])) if mark else "?"
        resume = ""
        if o["blocker"] and o["blocker"][1]:
            resume = f" Reprise {localtime(o['blocker'][1])}."
        print(f"  Aucune session Claude — {o['category'] or 'repos'} depuis {since} : {o['why'][:110]}{resume}")
    if os.environ.get("LQ_STATUS_VERSION_URL"):
        served, pending = deploy_state()
        dep = f"  Production {served}" + ("" if pending is None else
                                          (" · develop entièrement déployé" if pending == 0 else f" · develop : {pending} commit(s) non déployé(s)"))
    else:
        prs = [t for t in con.execute("SELECT issue, pr FROM tasks WHERE status='WAITING_OWNER' ORDER BY issue")]
        dep = f"  Phase : {ctl_get(con, 'phase') or '—'} · PR en attente de revue : " \
            + (", ".join(f"#{t['issue']}→{pr_ref(t['pr'])}" for t in prs) or "aucune")
    dep += " · bloqués : " + (", ".join(f"#{t['issue']}" for t in o["blocked"]) or "aucun")
    warn = ""
    if h["frozen"]:
        warn = f"{h['frozen']} Aucun redémarrage automatique — {CLI_NAME} status"
    elif silent:
        warn = (f"aucune activité de Claude depuis {span(quiet)} (> {span(QUIET_WARN_SECONDS)}) : service actif mais session "
                f"sans progrès visible ; pas de redémarrage automatique (coupure à {span(h['idle_cut'])})")
    elif o["state"] == "EN ERREUR":
        warn = f"{o['why'][:140]} — {CLI_NAME} status"
    print(dep + (f" · ⚠ {warn}" if warn else ""))

def cmd_status_tickets(con, _):
    """État complet de l'instance (digorch status) : services, activité, plafonds, tickets, emails,
    santé. Strictement en lecture seule ; ce qui n'est pas connu est affiché comme indisponible."""
    global con_for_display
    con_for_display = con
    W = 100
    o = observe(con)
    nowts = o["nowts"]
    tz = day_tz(con)
    tzname = getattr(tz, "key", "UTC")
    offset = now().astimezone(tz).strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    cfg, mode, pid, alive = o["cfg"], o["mode"], o["pid"], o["alive"]
    cur, cur_task, blocker, start, used = o["cur"], o["cur_task"], o["blocker"], o["start"], o["used"]
    cap, cap_origin, breaker, breaker_max = o["cap"], o["cap_origin"], o["breaker"], o["breaker_max"]
    h = o["health"]

    def head(title):
        print(f"\n══ {title} {'═' * max(3, W - 4 - len(title))}")

    print(f"{INSTANCE} — état au {now().astimezone(tz).strftime('%d/%m/%Y %H:%M:%S')} ({tzname}, {offset}) — "
          f"consultation en lecture seule")

    # ── Services ────────────────────────────────────────────────────────────────────────────
    head("SERVICES")
    units = {}
    for unit in STATUS_UNITS:
        rc, out = ro_run(["systemctl", "show", unit, "--no-pager", "-p",
                          "ActiveState,SubState,ActiveEnterTimestamp,NextElapseUSecRealtime,LastTriggerUSec,Result,LoadState"])
        if rc is None or not out:
            print(f"  {unit:<26} indisponible (systemctl illisible)")
            continue
        p = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        units[unit] = p
        state = f"{p.get('ActiveState', '?')}/{p.get('SubState', '?')}"
        ok = p.get("ActiveState") == "active" and p.get("LoadState") == "loaded"
        line = f"  {'✓' if ok else '⚠'} {unit:<26} {state:<18}"
        if p.get("LoadState") != "loaded":
            line += f" unité {p.get('LoadState', '?')}"
        elif unit.endswith(".timer"):
            line += (f" prochain : {p.get('NextElapseUSecRealtime') or 'inconnu'}"
                     f" ; dernier : {p.get('LastTriggerUSec') or 'jamais'}")
            svc = unit[:-6] + ".service"
            rc2, res = ro_run(["systemctl", "show", svc, "--no-pager", "-p", "Result", "--value"])
            if rc2 == 0 and res:
                line += f" ; résultat {svc} : {res}" + (" ⚠" if res != "success" else "")
        else:
            line += f" depuis {p.get('ActiveEnterTimestamp') or '?'}"
        print(line)
    if not STATUS_UNITS:
        print("  Aucune unité configurée (LQ_STATUS_UNITS).")
    print(f"  superviseur : {'actif, pid ' + pid if alive else 'ARRÊTÉ (aucun processus ' + (pid or '?') + ')'}"
          f" — mode {mode}")
    running = ctl_get(con, "supervisor_version")
    if alive and running and running != code_version():
        req = ctl_get(con, "reload_requested")
        print(f"  Code mis à jour ({code_version()}, superviseur sur {running}) : "
              + (f"rechargement demandé {localtime(req)}, appliqué à la fin de la session en cours, sans la couper."
                 if req else f"« {CLI_NAME} reload » l'appliquera entre deux sessions."))
    if TMUX_SESSION:
        rc, _ = ro_run(["tmux", "has-session", "-t", TMUX_SESSION])
        if rc is None:
            print(f"  tmux {TMUX_SESSION} : indisponible (tmux introuvable)")
        elif rc == 0:
            _, wins = ro_run(["tmux", "list-windows", "-t", TMUX_SESSION, "-F", "#W"])
            print(f"  ✓ tmux {TMUX_SESSION} : présente — fenêtres {', '.join(wins.split()) or '?'} "
                  f"(tmux attach -t {TMUX_SESSION})")
        else:
            print(f"  ⚠ tmux {TMUX_SESSION} : absente (ou non visible pour ce compte)")

    # ── Session Claude en cours et détection de blocage ─────────────────────────────────────
    head("SESSION CLAUDE")
    frozen = h["frozen"]
    if cur:
        t = cur_task
        spid, palive, quiet, elapsed = h["spid"], h["palive"], h["quiet"], h["elapsed"]
        idle_cut, max_cut = h["idle_cut"], h["max_cut"]
        print(f"#{cur['issue']} {ptitle(t)}")
        print(f"  démarrée {localtime(cur['started_at'])} (depuis {span(elapsed)}), tentative {t['attempts'] if t else '?'}, "
              f"processus {spid or '?'} {'vivant' if palive else 'ABSENT'}")
        print("  dernière sortie : " + (f"il y a {span(quiet)}" if quiet is not None else "inconnue")
              + f" — coupure automatique après {span(idle_cut)} sans sortie ou {span(max_cut)} au total")
        print(f"  activité de Claude : {h['action'] or 'indisponible (journal illisible)'}")
        print(f"  phase déclarée : {ctl_get(con, 'phase') or '—'}"
              + (f" ({localtime(ctl_get(con, 'phase_at'))})" if ctl_get(con, "phase_at") else ""))
        print(f"  branche : {(t['branch'] if t else '') or '—'} · PR : {(t['pr'] if t else '') or 'aucune pour l’instant'}")
        if frozen:
            print(f"  ⚠ {frozen}")
        elif quiet is not None and quiet >= QUIET_WARN_SECONDS:
            print(f"  ⚠ À surveiller : silencieuse depuis {span(quiet)}. Attente plausible (tests, installation, appel long) ; "
                  f"blocage confirmé seulement au-delà de {span(idle_cut)}.")
        else:
            print("  Progression normale.")
    else:
        print("Aucune session en cours."
              + (f" Dernière phase : {ctl_get(con, 'phase')} ({localtime(ctl_get(con, 'phase_at'))})."
                 if ctl_get(con, "phase") else ""))
    refused = ctl_get(con, "preflight_error")
    if refused:
        print(f"⚠ Sessions refusées : {refused}")

    # ── Activité ────────────────────────────────────────────────────────────────────────────
    head("ACTIVITÉ")
    last_done, last_mark, queue_rows, blocked = o["last_done"], o["last_mark"], o["queue_rows"], o["blocked"]
    ended_today = con.execute("SELECT outcome, COUNT(*) c FROM sessions WHERE ended_at >= ? "
                              "GROUP BY outcome", (start,)).fetchall()
    n_ended = sum(r["c"] for r in ended_today)

    state, why = o["state"], o["why"]
    print(f"État de l'orchestrateur : {state}")
    print(f"  raison : {why}")
    print("Ticket en cours : " + (f"#{cur['issue']} {ptitle(cur_task)}" if cur and cur_task
                                   else "aucun"))
    print("Dernier ticket terminé : " + (f"#{last_done['issue']} {(last_done['title'] or '')[:60]} — "
                                         f"{localtime(last_done['ended_at'])} ({ago(last_done['ended_at'])}), "
                                         f"état {last_done['tstatus'] or '?'}" if last_done else "aucun"))
    if last_mark:
        print(f"Dernière activité réelle : {localtime(last_mark[0])} ({last_mark[1]})")
        quiet_all = nowts - (ts_of(last_mark[0]) or nowts)
        print("Durée d'inactivité : " + ("aucune (session en cours)" if cur else span(quiet_all)))
    else:
        print("Dernière activité réelle : indisponible (aucune session enregistrée)")
    print(f"Sessions terminées aujourd'hui : {n_ended}"
          + (" (" + ", ".join(f"{r['outcome']} {r['c']}" for r in ended_today) + ")" if ended_today else ""))

    # ── Quotas et limites ───────────────────────────────────────────────────────────────────
    head("QUOTAS ET LIMITES")
    reset_at = next_utc_day(con)
    print(f"Plafond quotidien local : {cap or 'sans plafond'} sessions (origine : {cap_origin})")
    if cap:
        pct = used / cap
        print(f"Sessions consommées aujourd'hui : {used}/{cap} ({pct:.0%}) — restantes : {max(0, cap - used)}")
        if pct >= 1:
            print("  ⚠ Plafond atteint : aucune nouvelle session avant la réinitialisation.")
        elif pct >= 0.8:
            print(f"  ⚠ Avertissement : {pct:.0%} du plafond quotidien consommés.")
    else:
        print(f"Sessions lancées aujourd'hui : {used} (sans plafond)")
    print(f"Réinitialisation : {reset_at.astimezone(tz).strftime('%d/%m/%Y %H:%M')} {tzname} ({offset}) "
          f"= {iso(reset_at)} UTC, dans {span(reset_at.timestamp() - nowts)}")
    budget = cfg["max_cost_usd_per_day"][0]
    print(f"Coût THÉORIQUE du jour : {cost_today(con):.2f} USD (prix catalogue calculé par Claude Code, pas une facture)"
          + (f" — plafond local {budget:.2f} USD" if budget > 0 else " — pas de plafond de coût local"))
    print(f"Garde-fous : disjoncteur {breaker}/{breaker_max} sessions consécutives sans rapport ; "
          f"{cfg['max_attempts'][0]} tentatives par ticket ; {cfg['min_seconds_between_sessions'][0]} s entre deux sessions ; "
          f"session ≤ {span(cfg['session_max_seconds'][0])}")
    q = ctl_get(con, "quota_until")
    usages = shared_usage(con)
    if q and q > iso():
        print(f"⚠ Quota Claude/Anthropic ATTEINT (signalé par Claude Code) : aucune session avant {localtime(q)}.")
    if not usages:
        print("Quota Claude/Anthropic : indisponible (aucune valeur annoncée par Claude Code).")
    else:
        source, usage = usages[0]
        observed = usage.get("observed_at", "")
        fresh = observed and nowts - (ts_of(observed) or 0) < 15 * 60
        print(f"Quota Claude/Anthropic (compte PARTAGÉ, tous superviseurs + usage interactif) — lecture "
              f"{localtime(observed)} ({ago(observed)}, session {source})"
              + ("" if fresh else " — dernière valeur connue, l'usage actuel peut être plus élevé"))
        for window, key, label in (("five_hour", "max_five_hour_utilization", "fenêtre 5 h"),
                                   ("seven_day", "max_seven_day_utilization", "fenêtre 7 j")):
            win = (usage.get("unifiedWindows") or {}).get(window) or {}
            util, reset = win.get("utilization"), win.get("resetsAt")
            reset_txt = localtime(iso(datetime.fromtimestamp(int(reset), timezone.utc))) if reset else "inconnue"
            if util is None:
                print(f"  {label} : indisponible — seuil de pause {cfg[key][0]:.0%}")
            elif reset and reset < nowts:
                print(f"  {label} : réinitialisée le {reset_txt} depuis la lecture ({util:.0%} avant) — valeur actuelle inconnue")
            else:
                print(f"  {label} : {util:.0%} utilisés — seuil de pause {cfg[key][0]:.0%} — réinitialisation {reset_txt}"
                      + (" ⚠ seuil atteint" if util >= cfg[key][0] else ""))
        st = usage.get("status", "?")
        print(f"  statut annoncé : {st}" + ("" if st in ("allowed", "allowed_warning", "?") else " ⚠ limite fournisseur")
              + f" ; dépassement payant : {usage.get('overageStatus', '?')}")
    if cur:
        print("Prochaine reprise : session en cours.")
    elif blocker:
        print(f"Prochaine reprise : {localtime(blocker[1]) + ' (' + tzname + ')' if blocker[1] else 'indéterminée'} — {blocker[0]}")
    else:
        print("Prochaine reprise : immédiate (au prochain tour du superviseur)." if queue_rows
              else "Prochaine reprise : dès qu'un ticket devient traitable.")

    # ── Tickets GitHub ──────────────────────────────────────────────────────────────────────
    head("TICKETS GITHUB")
    print("En cours : " + (f"#{cur['issue']} {ptitle(cur_task)[:75]}" if cur and cur_task
                           else "aucun"))
    done_today = con.execute("""SELECT s.issue, MAX(s.ended_at) m, t.title, t.status FROM sessions s
                                LEFT JOIN tasks t ON t.issue = s.issue
                                WHERE s.ended_at >= ? AND s.outcome = 'COMPLETED' GROUP BY s.issue ORDER BY m""",
                             (start,)).fetchall()
    print(f"Terminés aujourd'hui ({len(done_today)}) :")
    for r in done_today:
        print(f"  #{r['issue']:<4} {(r['title'] or '')[:62]:<62} {localtime(r['m'])} → {r['status'] or '?'}")
    if not done_today:
        print("  aucun")
    testing = con.execute(
        "SELECT t.*, COALESCE(s.ended_at, t.updated_at) AS delivered_at FROM tasks t"
        " LEFT JOIN sessions s ON s.id = t.last_session"
        " WHERE t.status='WAITING_OWNER' ORDER BY delivered_at DESC").fetchall()
    print(f"En attente de votre validation ({len(testing)}) :")
    for t in testing:
        print(f"  #{t['issue']:<4} {ptitle(t)[:63]:<63} livré {localtime(t['delivered_at'])}, "
              f"SHA {(t['commit_sha'] or '?')[:10]}, {t['pr'] or 'PR ?'}")
        if t["report_url"]:
            print(f"        rapport : {t['report_url']}")
    if not testing:
        print("  aucun")
    print(f"Bloqués ({len(blocked)}) :")
    for t in blocked:
        print(f"  #{t['issue']:<4} {ptitle(t)[:63]}  [{'bloqué' if t['status'] == 'BLOCKED' else 'échecs répétés'}]")
        print(f"        raison : {(t['blockers'] or '(non précisée)')[:170]}")
        for j, qtext in enumerate(jlist(t["questions"]), 1):
            print(f"        Q{j}. {qtext[:165]}")
        print(f"        répondre : https://github.com/{REPO}/issues/{t['issue']}")
    if not blocked:
        print("  aucun")
    nxt = o["next"]
    print("Prochain ticket prévu : " + (f"#{nxt['issue']} {ptitle(nxt)[:75]} "
                                        "(estimé sur l'état local, sans appel GitHub)" if nxt else
                                        "indéterminé (file vide ou dépendances à vérifier sur GitHub)"))
    later = [t for t in queue_rows if t is not nxt]
    if later:
        print("Ensuite : " + ", ".join(f"#{t['issue']} P{t['priority'] // 10}" for t in later[:12])
              + (f" … (+{len(later) - 12})" if len(later) > 12 else ""))
    suspended = con.execute("SELECT issue FROM tasks WHERE status='SUSPENDED' ORDER BY issue").fetchall()
    if suspended:
        print("Suspendus (labels GitHub) : " + ", ".join(f"#{r['issue']}" for r in suspended))
    print(f"Synchronisation GitHub : {localtime(ctl_get(con, 'last_sync'))} ({ago(ctl_get(con, 'last_sync'))}), "
          f"toutes les {SYNC_EVERY_SECONDS // 60} min")

    # ── E-mails ─────────────────────────────────────────────────────────────────────────────
    head("E-MAILS")
    if not NOTIFY_SMTP_ENV:
        print("Désactivés.")
    else:
        try:
            smtp_settings()
            print("Configuration SMTP : présente et complète (valeurs non affichées).")
        except (OSError, ValueError) as exc:
            print(f"Configuration SMTP : PROBLÈME — {type(exc).__name__}" + (f" ({exc})" if isinstance(exc, ValueError) else ""))
        sent = con.execute("SELECT COUNT(*) c, MAX(sent_at) m FROM notifications WHERE sent_at IS NOT NULL").fetchone()
        print(f"Rapports envoyés : {sent['c']}" + (f", dernier {localtime(sent['m'])} ({ago(sent['m'])})" if sent["m"] else ""))
        pending = con.execute("SELECT * FROM notifications WHERE sent_at IS NULL ORDER BY created_at").fetchall()
        print(f"En attente : {len(pending)}")
        for nf in pending:
            print(f"  {nf['subject']} — {nf['attempts']} essai(s)"
                  + (f", prochain essai {localtime(nf['next_try'])}" if nf["next_try"] else "")
                  + (f" — abandonné (30 essais), {CLI_NAME} notifications --retry" if nf["attempts"] >= 30 else ""))
        err = con.execute("""SELECT * FROM notifications WHERE COALESCE(last_error,'') != ''
                             ORDER BY COALESCE(next_try, created_at) DESC LIMIT 1""").fetchone()
        print("Dernière erreur SMTP : " + (f"{err['last_error'][:160]} — « {err['subject'][:60]} » "
                                           + ("(envoyé ensuite " + localtime(err['sent_at']) + ")" if err["sent_at"] else "(non résolue)")
                                           if err else "aucune enregistrée"))

    # ── Santé ───────────────────────────────────────────────────────────────────────────────
    head("SANTÉ")
    rc, out = ro_run(["journalctl", "-u", SERVICE, "-p", "warning", "--since", "-24h", "--no-pager", "-q", "-o", "short-iso"])
    if rc is None:
        print("Journal système (24 h) : indisponible.")
    else:
        lines = [x for x in out.splitlines() if x.strip()]
        print(f"Journal système {SERVICE} (24 h, avertissements et erreurs) : {len(lines)}")
        for x in lines[-5:]:
            print(f"  {x[:150]}")
    bad = con.execute("SELECT * FROM sessions WHERE ended_at >= ? AND outcome != 'COMPLETED' ORDER BY ended_at DESC",
                      (iso(datetime.fromtimestamp(nowts - 86400, timezone.utc)),)).fetchall()
    print(f"Sessions en échec (24 h) : {len(bad)}")
    for s in bad[:5]:
        print(f"  #{s['issue']} {localtime(s['ended_at'])} {s['outcome']} — {(s['detail'] or '')[:110]}")
    print(f"Disjoncteur : {breaker}/{breaker_max}" + (" ⚠ DÉCLENCHÉ" if breaker >= breaker_max else ""))
    print(f"Dernière erreur : {ctl_get(con, 'last_error') or 'aucune enregistrée'}")
    print("Dernière exécution réussie : " + (f"session #{last_done['issue']} terminée {localtime(last_done['ended_at'])} "
                                             f"({ago(last_done['ended_at'])})" if last_done else "aucune session réussie enregistrée"))
    print("Détection de session figée : " + (frozen if frozen else
          ("rien à signaler" if cur else "sans objet (aucune session en cours)")))
    print(f"\nDétails : {CLI_NAME} queue | {CLI_NAME} log [N] | {CLI_NAME} sessions | {CLI_NAME} notifications | "
          f"journalctl -u {SERVICE} -f")


def cmd_queue(con, _):
    for t in con.execute("SELECT * FROM tasks ORDER BY CASE status WHEN 'RUNNING' THEN 0 WHEN 'TESTING' THEN 1 "
                         "WHEN 'READY' THEN 2 ELSE 3 END, priority, issue").fetchall():
        extra = f" (après {t['not_before']})" if t["not_before"] and t["not_before"] > iso() else ""
        print(f"#{t['issue']:<4} {t['status']:<13} p{t['priority']:<3} {t['title'][:60]:<60}{extra}"
              f"{'  deps ' + t['deps'] if t['deps'] else ''}")
        if t["next_action"]:
            print(f"       → {t['next_action'][:150]}")


def cmd_mode(con, args):
    mode = {"pause": "PAUSED", "resume": "RUNNING", "stop": "STOP_NOW" if getattr(args, "now", False) else "STOP"}[args.cmd]
    ctl_set(con, "mode", mode)
    if args.cmd == "resume":
        # Réarme le disjoncteur : c'est une décision du propriétaire, jamais automatique.
        ctl_set(con, "pause_reason", "")
        ctl_set(con, "consecutive_failures", 0)
    event(con, None, "control", f"{args.cmd}{' --now' if mode == 'STOP_NOW' else ''}")
    print(f"Mode : {mode}")
    if args.cmd == "resume":
        print(f"Si le service est arrêté : sudo systemctl start {SERVICE}")


def cmd_config(con, args):
    if args.action in ("set", "unset"):
        if args.name not in SETTINGS:
            print(f"Réglage inconnu. Réglages : {', '.join(SETTINGS)}")
            return 2
        if args.action == "set":
            try:
                value = parse_setting(args.name, args.value)
            except (TypeError, ValueError) as exc:
                print(f"Valeur refusée : {exc}")
                return 2
        candidate = settings(con)
        candidate[args.name] = value if args.action == "set" else SETTINGS[args.name][1]
        error = policy_error(candidate)
        if error:
            print(f"Refusé : {error}")
            return 2
        ctl_set(con, "cfg." + args.name, value if args.action == "set" else "")
        event(con, None, "config", f"{args.action} {args.name}" + (f" = {value}" if args.action == "set" else ""))
        print("Enregistré ; appliqué par le superviseur avant la prochaine session (aucun redémarrage).")
    for name, (env, default, low, high, doc) in SETTINGS.items():
        value, origin = setting(con, name)
        print(f"{name:<30} {value!s:<8} ({origin}; défaut {default}, bornes {low}–{high})\n    {doc}")
    return 0


def cmd_reload(con, _):
    ctl_set(con, "reload_requested", iso())
    event(con, None, "control", "reload")
    print("Rechargement demandé : le superviseur relira le code installé à la fin de la session en cours "
          "(ou dans les 2 s s'il est inactif). Aucune session n'est coupée.")


def cmd_skip(con, args):
    if not task(con, args.issue):
        print("Ticket absent de la file.")
        return
    update_task(con, args.issue, status="BLOCKED", blockers=args.reason or "Mis de côté par le propriétaire (skip).")
    event(con, args.issue, "control", "skip")
    print(f"#{args.issue} → BLOCKED")


def cmd_add(con, args):
    t = task(con, args.issue)
    if t:
        update_task(con, args.issue, status="READY", attempts=0, not_before="", priority=args.prio,
                    deps=args.deps if args.deps is not None else t["deps"],
                    note=args.note if args.note is not None else t["note"])
    else:
        state, title = issue_state(args.issue)
        con.execute("INSERT INTO tasks(issue,title,priority,deps,note,updated_at) VALUES(?,?,?,?,?,?)",
                    (args.issue, title, args.prio, args.deps or "", args.note or "", iso()))
        con.commit()
    event(con, args.issue, "control", "add")
    print(f"#{args.issue} → READY (priorité {args.prio})")


def cmd_set(con, args):
    allowed = {"status", "priority", "deps", "branch", "pr", "commit_sha", "summary", "tests", "blockers",
               "next_action", "note", "attempts", "title", "not_before"}
    if args.field not in allowed:
        print("Champ inconnu.")
        return
    update_task(con, args.issue, **{args.field: args.value})
    event(con, args.issue, "control", f"set {args.field}")
    print("ok")


def cmd_log(con, args):
    q = "SELECT * FROM events" + (" WHERE issue=?" if args.issue else "") + " ORDER BY id DESC LIMIT 40"
    for e in reversed(con.execute(q, (args.issue,) if args.issue else ()).fetchall()):
        print(f"{e['at']} #{e['issue'] if e['issue'] is not None else '-':<4} {e['kind']:<14} {e['message'][:160]}")


def cmd_sessions(con, _):
    for s in con.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT 20").fetchall():
        print(f"{s['started_at']} → {s['ended_at'] or '…':<20} #{s['issue']:<4} {s['outcome'] or 'EN COURS':<11} "
              f"{(s['detail'] or '')[:100]}")


def cmd_sync(con, _):
    if not GH_SYNC:
        print("Synchronisation GitHub désactivée pour cette instance (LQ_GH_SYNC).")
        return 2
    ctl_set(con, "sync_requested", "1")
    print("Synchronisation demandée : le superviseur la fera dans les 2 s s'il est inactif, sinon après la session.")
    return 0


def cmd_notifications(con, args):
    if args.retry:
        con.execute("UPDATE notifications SET next_try='' WHERE sent_at IS NULL")
        con.commit()
        print("Nouvel essai demandé pour les emails en attente (au prochain tour du superviseur).")
    for nf in con.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 20").fetchall():
        state = f"envoyé {nf['sent_at']}" if nf["sent_at"] else f"EN ATTENTE ({nf['attempts']} essai(s), {nf['last_error'] or '-'})"
        print(f"{nf['created_at']} {nf['subject'][:60]:<60} {state}"
              + (f"  + {nf['attachment_name']}" if nf["attachment_name"] else ""))
    return 0


def cmd_notify(con, args):
    """Met en file puis envoie tout de suite un email de rapport pour un ticket (même chemin que
    les notifications de fin de session). Affiche le résultat, jamais de valeur SMTP."""
    if not NOTIFY_SMTP_ENV:
        print("Notifications désactivées (LQ_NOTIFY_SMTP_ENV).")
        return 2
    path = Path(args.report)
    if not path.is_file():
        print(f"Rapport introuvable : {path}")
        return 2
    t = task(con, args.issue)
    title = args.title or (t["title"] if t else "")
    body = "\n".join([f"Ticket #{args.issue} — {title}", f"État : {args.state}", "", "Résultat :",
                      args.summary or "(voir le rapport joint)", "",
                      f"Ticket : https://github.com/{REPO}/issues/{args.issue}"]
                     + ([f"Rapport : {args.url}"] if args.url else [])
                     + ([f"Site : {SITE_URL}"] if SITE_URL else [])
                     + ["", "Répondre dans le ticket GitHub : la réponse est détectée et le travail reprend automatiquement."])
    key = f"{args.issue}:manual:{hashlib.sha256((body + path.read_text(errors='replace')).encode()).hexdigest()[:16]}"
    queue_notification(con, key, args.issue, args.kind, f"{NOTIFY_PREFIX} #{args.issue} — {args.kind}", body,
                       path.name, path.read_text(encoding="utf-8", errors="replace"))
    for _ in range(45):  # envoi éventuellement fait par le superviseur (réservation de la ligne)
        nf = con.execute("SELECT * FROM notifications WHERE key=?", (key,)).fetchone()
        if not nf or nf["sent_at"] or nf["last_error"]:
            break
        time.sleep(2)
    if nf and nf["sent_at"]:
        print(f"Email envoyé à {nf['sent_at']} : {nf['subject']} (+ {nf['attachment_name']})")
        return 0
    print(f"Email NON envoyé : {nf['last_error'] if nf else 'non mis en file'} — nouvel essai automatique par le superviseur.")
    return 1


def cmd_phase(con, args):
    """Appelé par la session : déclare sa phase (analyse, implémentation, tests, PR, rapport…)."""
    raw = os.environ.get("LQ_ISSUE") or ctl_get(con, "phase_issue")
    set_phase(con, int(raw) if str(raw).isdigit() else None, " ".join(args.text))
    return 0


def cmd_check(con, _):
    """Contrôles d'installation, en lecture seule : rien n'est démarré, aucun secret n'est affiché.
    Code de sortie 1 si une session serait refusée."""
    bad = 0

    def line(ok, label, detail=""):
        nonlocal bad
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {label}" + (f" — {detail}" if detail else ""))

    print(f"{INSTANCE} — contrôles d'installation (dépôt {REPO})")
    line(os.geteuid() != 0, "exécution hors root", f"uid {os.geteuid()}")
    line(bool(CLAUDE_SETTINGS) and Path(CLAUDE_SETTINGS).is_file(), "réglages de garde Claude", CLAUDE_SETTINGS or "LQ_CLAUDE_SETTINGS vide")
    if CLAUDE_SETTINGS and Path(CLAUDE_SETTINGS).is_file():
        try:
            hooks = json.loads(Path(CLAUDE_SETTINGS).read_text(encoding="utf-8")).get("hooks", {}).get("PreToolUse", [])
            cmds = [h.get("command", "") for m in hooks for h in m.get("hooks", [])]
            line(bool(cmds) and all(Path(c.split()[-1]).is_file() for c in cmds), "crochet de garde PreToolUse",
                 ", ".join(cmds) or "aucun")
        except (OSError, ValueError, AttributeError, IndexError) as exc:
            line(False, "crochet de garde PreToolUse", type(exc).__name__)
    line(claude_auth_ok(), "authentification Claude Code (réutilisée, jamais copiée)", CLAUDE)
    rc, _ = ro_run([GH, "auth", "status"], timeout=20)
    line(rc == 0, "authentification GitHub CLI (gh)")
    rc, url = ro_run(["git", "-C", str(LAUNCH_DIR), "remote", "get-url", "origin"])
    line(rc == 0 and REPO.lower() in url.lower(), "clone de lancement dédié", f"{LAUNCH_DIR} → {url or 'absent'}")
    secrets = [x for x in ("config.toml", "secrets", "storage", ".env") if (LAUNCH_DIR / x).exists()]
    line(not secrets, "clone sans fichier sensible ni volume de production", ", ".join(secrets) or "aucun")
    line(WORKDIR.is_dir(), "dossier des worktrees", str(WORKDIR))
    line(STATE_DIR.is_dir() and (STATE_DIR.stat().st_mode & 0o077) == 0, "dossier d'état privé (0700)", str(STATE_DIR))
    if TMUX_SESSION:
        rc, _ = ro_run(["tmux", "has-session", "-t", f"={TMUX_SESSION}"])
        if rc == 0:
            _, wins = ro_run(["tmux", "list-windows", "-t", f"={TMUX_SESSION}", "-F", "#W"])
            console = "status" in wins.split()
            line(console, f"session tmux {TMUX_SESSION}",
                 "console de l'orchestrateur" if console else
                 f"OCCUPÉE par une autre session (fenêtres : {', '.join(wins.split())}) : "
                 f"tmux rename-session -t {TMUX_SESSION} <autre nom>")
        else:
            print(f"  · session tmux {TMUX_SESSION} absente (créée par le service console)")
    print("Aucune session ne sera refusée." if not bad else f"{bad} point(s) à corriger avant de démarrer.")
    return 1 if bad else 0


def main():
    p = argparse.ArgumentParser(prog=CLI_NAME)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("status", "pulse", "queue", "pause", "resume", "run", "sessions", "reload"):
        sub.add_parser(name)
    s = sub.add_parser("stop")
    s.add_argument("--now", action="store_true")
    s = sub.add_parser("skip")
    s.add_argument("issue", type=int)
    s.add_argument("reason", nargs="?", default="")
    s = sub.add_parser("add")
    s.add_argument("issue", type=int)
    s.add_argument("--prio", type=int, default=50)
    s.add_argument("--deps", default=None)
    s.add_argument("--note", default=None)
    s = sub.add_parser("set")
    s.add_argument("issue", type=int)
    s.add_argument("field")
    s.add_argument("value")
    s = sub.add_parser("config")
    s.add_argument("action", nargs="?", choices=("show", "set", "unset"), default="show")
    s.add_argument("name", nargs="?")
    s.add_argument("value", nargs="?")
    sub.add_parser("sync")
    sub.add_parser("check")
    s = sub.add_parser("phase")
    s.add_argument("text", nargs="+")
    s = sub.add_parser("notifications")
    s.add_argument("--retry", action="store_true")
    s = sub.add_parser("notify")
    s.add_argument("issue", type=int)
    s.add_argument("report")
    s.add_argument("--kind", default="Rapport")
    s.add_argument("--state", default="rapport transmis")
    s.add_argument("--summary", default="")
    s.add_argument("--title", default="")
    s.add_argument("--url", default="")
    s = sub.add_parser("log")
    s.add_argument("issue", type=int, nargs="?")
    args = p.parse_args()
    # `status` et `pulse` : connexion en lecture seule (aucune création, migration ni écriture).
    con = status_db() if args.cmd in ("status", "pulse") and DB_PATH.exists() else db()
    if args.cmd == "run":
        sys.exit(supervise())
    if args.cmd == "config" and args.action != "show" and (not args.name or (args.action == "set") != bool(args.value)):
        p.error("usage : lodyorch config [show | set NOM VALEUR | unset NOM]")
    code = {"status": cmd_status_tickets if GH_SYNC else cmd_status, "pulse": cmd_pulse, "queue": cmd_queue, "pause": cmd_mode, "resume": cmd_mode, "stop": cmd_mode,
            "skip": cmd_skip, "add": cmd_add, "set": cmd_set, "log": cmd_log, "sessions": cmd_sessions,
            "config": cmd_config, "reload": cmd_reload, "sync": cmd_sync,
            "notifications": cmd_notifications, "notify": cmd_notify, "phase": cmd_phase,
            "check": cmd_check}[args.cmd](con, args)
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
