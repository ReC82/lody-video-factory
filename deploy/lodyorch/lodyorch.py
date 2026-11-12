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
  lodyorch add N [--prio P] [--deps 35,321] [--note "…"]   débloquer / reprioriser (facultatif : agent:auto suffit)
  lodyorch set N champ valeur                               corriger un champ d'un ticket
  lodyorch sync              synchroniser maintenant les tickets GitHub (si LQ_GH_SYNC=1)
  lodyorch phase TEXTE       phase de travail de la session en cours (appelé par la session)
  lodyorch labels [--apply]  labels du dépôt comparés au protocole (#117) ; --apply crée/aligne
  lodyorch console           ouvrir LA console (session tmux interactive, créée seulement si absente)
  lodyorch human busy|idle   appelé par les crochets de la console : priorité au propriétaire
  lodyorch statusline        ligne d'état affichée dans la console Claude
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
import shlex
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
# Protocole de labels (#117) : référentiel des labels attendus sur le dépôt (`lodyorch labels`).
LABELS_FILE = Path(os.environ.get("LQ_LABELS_FILE", Path(__file__).with_name("labels.json")))
# Commentaire de prise en charge publié par le superviseur, une seule fois par ticket.
CLAIM_COMMENT = os.environ.get("LQ_CLAIM_COMMENT", "0") == "1"
CLAIM_MARKER = "<!-- lodyorch:prise-en-charge -->"
# Une livraison (WAITING_OWNER) doit nommer sa PR ; sans PR, le ticket n'est pas considéré livré.
REQUIRE_PR = os.environ.get("LQ_REQUIRE_PR", "0") == "1"
# Suivi de la CI par le superviseur (#117/#127) : aucune session Claude n'attend la CI. Une PR livrée
# passe en « CI en attente » ; le superviseur interroge GitHub toutes les LQ_CI_POLL s, puis la déclare
# prête pour revue, relance une correction bornée (LQ_CI_MAX_FIXES) ou bloque avec un diagnostic.
CI_WATCH = os.environ.get("LQ_CI_WATCH", "0") == "1"
CI_POLL_SECONDS = int(os.environ.get("LQ_CI_POLL", 300))
CI_MAX_FIXES = int(os.environ.get("LQ_CI_MAX_FIXES", 2))
CI_TIMEOUT_SECONDS = int(os.environ.get("LQ_CI_TIMEOUT", 3 * 3600))
CI_FAILED = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
# Fusion automatique sécurisée (#200) par le SUPERVISEUR, jamais par une session Claude.
# Modes : off (aucun appel), dry-run (verdict journalisé, rien n'est fusionné), on (fusion des PR
# éligibles). LQ_AUTO_MERGE est un PLAFOND posé dans lodyorch.env (fichier root, hors de portée des
# sessions) ; le mode effectif est le plus bas entre ce plafond et `lodyorch automerge <mode>`.
MERGE_MODES = ("off", "dry-run", "on")
AUTO_MERGE_CEILING = os.environ.get("LQ_AUTO_MERGE", "off").strip().lower()
if AUTO_MERGE_CEILING not in MERGE_MODES:
    AUTO_MERGE_CEILING = "off"
PRIORITY_RE = re.compile(r"(?i)^priority\s*:\s*(.*)$")
TRUSTED_AUTHORS = {"OWNER", "MEMBER", "COLLABORATOR"}
DEPS_RE = re.compile(r"(?:d[ée]pend(?:ance)?s?\s+(?:de|du|des|d'|on)|bloqu[ée]e?\s+par|blocked\s+by|"
                     r"pr[ée]requis|requires)\s*:?\s*((?:#\d+(?:\s*(?:,|et|and|&)?\s*)?)+)", re.I)
# Notifications email (réutilise un fichier SMTP existant, lu sans jamais être copié ni affiché).
NOTIFY_SMTP_ENV = os.environ.get("LQ_NOTIFY_SMTP_ENV", "")
NOTIFY_PREFIX = os.environ.get("LQ_NOTIFY_PREFIX", "[Lody]")
SITE_URL = os.environ.get("LQ_SITE_URL", "")
# Rapport permanent par tentative (#123) : rédigé par le superviseur après chaque session (succès,
# échec, blocage, interruption récupérée), archivé localement, publié en commentaire du ticket
# (lien permanent) et envoyé par email. Une session arrêtée volontairement ou par une limite
# fournisseur n'est pas une tentative : pas de rapport.
RUN_REPORTS = os.environ.get("LQ_RUN_REPORTS", "0") == "1"
REPORT_PROJECT = os.environ.get("LQ_REPORT_PROJECT", "Lody Video Factory")
REPORT_MARKER = "<!-- lodyorch:rapport run={run} -->"
REPORT_COMMENT_MAX = 60000  # limite GitHub d'un commentaire : 65 536 caractères
REPORT_SKIP_OUTCOMES = ("RATE_LIMIT", "STOPPED")
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
    "max_prep_sessions_per_day": ("LQ_MAX_PREP_SESSIONS_PER_DAY", 3, 0, 50,
                                  "File de préparation (#165) : sessions lancées au plus par jour sur un ticket dont "
                                  "les dépendances ont une PR en revue non fusionnée (contrats, tests, maquettes). "
                                  "Comptées aussi dans max_sessions_per_day. 0 = désactivé."),
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
    "human_quiet_seconds": ("LQ_HUMAN_QUIET", 15 * 60, 0, 24 * 3600,
                            "Console : aucune session de file avant ce délai après la dernière réponse de Claude "
                            "au propriétaire (une conversation n'est pas interrompue par la file)."),
    "human_busy_max_seconds": ("LQ_HUMAN_BUSY_MAX", 2 * 3600, 600, 24 * 3600,
                               "Console : une demande sans fin signalée (Échap, crash) cesse de bloquer la file "
                               "après ce délai."),
    "yield_grace_seconds": ("LQ_YIELD_GRACE", 5 * 60, 30, 3600,
                            "Demande du propriétaire pendant une session de file : délai laissé à la session pour "
                            "commiter et rendre son rapport avant d'être arrêtée (ticket repris ensuite)."),
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
    CREATE TABLE IF NOT EXISTS run_reports(
        run_id TEXT PRIMARY KEY, issue INTEGER, session_id TEXT, outcome TEXT, detail TEXT,
        report TEXT DEFAULT '{}', state TEXT, file TEXT DEFAULT '', created_at TEXT, finalized_at TEXT DEFAULT '',
        comment_url TEXT DEFAULT '', comment_attempts INTEGER NOT NULL DEFAULT 0, comment_next TEXT DEFAULT '',
        comment_error TEXT DEFAULT '');
    """)
    for col, decl in (("gh_labels", "TEXT DEFAULT '[]'"), ("gh_since", "TEXT DEFAULT ''"),
                      ("gh_cursor", "INTEGER NOT NULL DEFAULT 0"), ("questions", "TEXT DEFAULT '[]'"),
                      ("test_links", "TEXT DEFAULT '[]'"), ("report_url", "TEXT DEFAULT ''"),
                      ("own_lock", "INTEGER NOT NULL DEFAULT 0"), ("claimed", "INTEGER NOT NULL DEFAULT 0"),
                      ("label_issues", "TEXT DEFAULT ''"),
                      ("ci_state", "TEXT DEFAULT ''"), ("ci_detail", "TEXT DEFAULT ''"), ("ci_since", "TEXT DEFAULT ''"),
                      ("ci_checked_at", "TEXT DEFAULT ''"), ("ci_fixes", "INTEGER NOT NULL DEFAULT 0")):
        try:
            con.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
        except sqlite3.OperationalError:
            pass
    # #165 : clé de l'état des dépendances au moment de la dernière préparation (une seule
    # préparation par état) et mode de chaque session (« prepare » pour la file de préparation).
    # #200 : fusion automatique — journal d'audit des verdicts et des fusions (idempotent).
    con.executescript("""
    CREATE TABLE IF NOT EXISTS merge_audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, issue INTEGER, pr INTEGER, head_sha TEXT, mode TEXT,
        eligible INTEGER, signature TEXT, refusals TEXT DEFAULT '[]', matrix TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS merge_ops(
        pr INTEGER, head_sha TEXT, issue INTEGER, state TEXT, started_at TEXT, finished_at TEXT DEFAULT '',
        merge_sha TEXT DEFAULT '', error TEXT DEFAULT '', PRIMARY KEY(pr, head_sha));
    """)
    for table, col, decl in (("tasks", "prep_key", "TEXT DEFAULT ''"), ("sessions", "mode", "TEXT DEFAULT ''"),
                             ("tasks", "merge_checked_at", "TEXT DEFAULT ''")):
        try:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
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


# ---------------------------------------------------------------- file de préparation (#165)
#
# Quand aucun ticket n'est exécutable, un ticket dont TOUTES les dépendances ouvertes ont déjà une
# PR en revue (ticket WAITING_OWNER avec PR) peut avancer en « préparation » : contrats, tests,
# maquettes, sur sa propre branche partie d'origin/main, PR en brouillon. Jamais de fusion, jamais
# un ticket bloqué ou suspendu (ils ne sont pas dans la file), une seule préparation par état des
# dépendances, et un plafond quotidien dédié : pas de sessions de remplissage.

def review_pr(con, n):
    """Lien de la PR en revue du ticket n (WAITING_OWNER avec PR), sinon ''."""
    d = task(con, n)
    return str(d["pr"] or "").strip() if d and d["status"] == "WAITING_OWNER" else ""


def prep_key(con, missing):
    """État des dépendances ouvertes : la même préparation n'est jamais relancée pour le même état."""
    return ",".join(f"{n}:{pr_number(review_pr(con, n)) or '?'}" for n in sorted(missing))


def prep_sessions_today(con):
    return con.execute("SELECT COUNT(*) c FROM sessions WHERE mode='prepare' AND started_at >= ?",
                       (iso(day_start(con)),)).fetchone()["c"]


def preparation_candidates(con, missing_of=None):
    """[(ticket, dépendances_ouvertes)] préparables, dans l'ordre de la file. `missing_of` calcule les
    dépendances ouvertes (par défaut deps_satisfied, avec GitHub ; local_missing pour la lecture seule)."""
    missing_of = missing_of or (lambda deps: deps_satisfied(con, deps))
    out = []
    for t in con.execute("""SELECT * FROM tasks WHERE status IN ('RUNNING','TESTING','READY') AND issue != ?
                            AND COALESCE(deps,'') != ''
                            ORDER BY CASE status WHEN 'READY' THEN 1 ELSE 0 END, priority, issue""",
                         (TRIAGE,)).fetchall():
        if t["not_before"] and t["not_before"] > iso():
            continue
        missing = missing_of(t["deps"])
        if not missing or not all(review_pr(con, n) for n in missing):
            continue
        if (t["prep_key"] or "") == prep_key(con, missing):
            continue  # déjà préparé pour cet état des dépendances
        out.append((t, missing))
    return out


def next_preparation(con):
    """Le ticket à préparer quand rien n'est exécutable : (ticket, dépendances_ouvertes) ou None."""
    cap = setting(con, "max_prep_sessions_per_day")[0]
    if not cap or prep_sessions_today(con) >= cap:
        return None
    found = preparation_candidates(con)
    return found[0] if found else None


def local_missing(con, deps):
    """Dépendances ouvertes d'après la seule base locale (lecture seule, sans réseau)."""
    done = {r["issue"] for r in con.execute(
        f"SELECT issue FROM tasks WHERE status IN ({','.join('?' * len(DEPS_DONE_STATES))})", DEPS_DONE_STATES)}
    return [int(d) for d in re.findall(r"\d+", deps or "") if int(d) not in done]


def queue_overview(con):
    """Synthèse de la file, sans réseau : pourquoi on attend, ce qui reste à faire."""
    rows = con.execute("""SELECT * FROM tasks WHERE status IN ('RUNNING','TESTING','READY') AND issue != ?
                          ORDER BY CASE status WHEN 'READY' THEN 1 ELSE 0 END, priority, issue""",
                       (TRIAGE,)).fetchall()
    ov = {"executable": [], "waiting_deps": [], "delayed": [], "preparable": [], "prepared": []}
    for t in rows:
        missing = local_missing(con, t["deps"])
        if t["not_before"] and t["not_before"] > iso():
            ov["delayed"].append((t["issue"], t["not_before"]))
        elif not missing:
            ov["executable"].append(t["issue"])
        else:
            ov["waiting_deps"].append((t["issue"], missing))
            if t["prep_key"] and all(review_pr(con, n) for n in missing) and t["prep_key"] == prep_key(con, missing):
                ov["prepared"].append(t["issue"])
    ov["preparable"] = [t["issue"] for t, _ in preparation_candidates(con, lambda deps: local_missing(con, deps))]
    ov["in_review"] = [(r["issue"], r["pr"], r["ci_state"]) for r in con.execute(
        "SELECT issue, pr, ci_state FROM tasks WHERE status='WAITING_OWNER' ORDER BY priority, issue")]
    ov["blocked"] = [r["issue"] for r in con.execute(
        "SELECT issue FROM tasks WHERE status IN ('BLOCKED','FAILED') ORDER BY priority, issue")]
    cap = setting(con, "max_prep_sessions_per_day")[0]
    ov["prep_used"], ov["prep_cap"] = prep_sessions_today(con), cap
    return ov


def _nums(items, limit=8):
    items = list(items)
    text = ", ".join(f"#{n}" for n in items[:limit])
    return text + (f" (+{len(items) - limit})" if len(items) > limit else "")


def overview_text(ov):
    """Motif d'attente lisible : exécutable, préparation, PR en revue, bloqués, dépendances."""
    parts = []
    if ov["executable"]:
        parts.append(f"{len(ov['executable'])} ticket(s) exécutable(s) : {_nums(ov['executable'])}")
    else:
        parts.append("aucun ticket exécutable")
    if ov["preparable"]:
        if ov["prep_cap"] and ov["prep_used"] < ov["prep_cap"]:
            parts.append(f"prochain à préparer : #{ov['preparable'][0]} "
                         f"(préparations du jour {ov['prep_used']}/{ov['prep_cap']})")
        else:
            parts.append(f"{len(ov['preparable'])} ticket(s) préparable(s) ({_nums(ov['preparable'])}) mais "
                         + ("file de préparation désactivée" if not ov["prep_cap"] else
                            f"plafond de préparation atteint ({ov['prep_used']}/{ov['prep_cap']})"))
    ci = {"pending": "CI en cours", "failed": "CI en échec", "success": "CI verte"}
    if ov["in_review"]:
        detail = ", ".join(f"#{n}" + (f" PR #{pr_number(pr)}" if pr_number(pr) else "")
                           + (f" {ci[c]}" if c in ci else "") for n, pr, c in ov["in_review"][:8])
        more = f" (+{len(ov['in_review']) - 8})" if len(ov["in_review"]) > 8 else ""
        parts.append(f"{len(ov['in_review'])} PR en revue chez le propriétaire : {detail}{more}")
    if ov["blocked"]:
        parts.append(f"{len(ov['blocked'])} bloqué(s) en attente de réponse : {_nums(ov['blocked'])}")
    if ov["waiting_deps"]:
        parts.append("attendent une dépendance non fusionnée : "
                     + ", ".join(f"#{n}→{_nums(m, 4)}" for n, m in ov["waiting_deps"][:6])
                     + (f" ({len(ov['prepared'])} déjà préparé(s) : {_nums(ov['prepared'])})" if ov["prepared"] else ""))
    if ov["delayed"]:
        parts.append("en délai de reprise : " + ", ".join(f"#{n} après {nb}" for n, nb in ov["delayed"][:4]))
    return " ; ".join(parts) + "."


def notify_idle(con, ov):
    """Un seul email quand la file est réellement à l'arrêt (rien d'exécutable ni de préparable) et
    que le propriétaire peut la débloquer (PR à relire, tickets bloqués). Dédoublonné sur l'état de la
    file et espacé d'au moins 12 h : jamais de boucle d'emails identiques."""
    if not NOTIFY_SMTP_ENV or ov["executable"] or ov["delayed"]:
        return
    if ov["preparable"] and ov["prep_cap"] and ov["prep_used"] < ov["prep_cap"]:
        return
    if not ov["in_review"] and not ov["blocked"]:
        return
    state = json.dumps([[n for n, *_ in ov["in_review"]], ov["blocked"], ov["waiting_deps"]])
    digest = hashlib.sha256(state.encode()).hexdigest()[:16]
    last = ctl_get(con, "idle_notice_at")
    if ctl_get(con, "idle_notice_key") == digest or (last and time.time() - float(last) < 12 * 3600):
        return
    ctl_set(con, "idle_notice_key", digest)
    ctl_set(con, "idle_notice_at", time.time())
    lines = [f"{INSTANCE} n'a plus de travail utile : aucune session n'est lancée tant que rien ne change.", "",
             "Synthèse :", overview_text(ov), "", "PR en attente de revue :"]
    lines += [f"- #{n} : {pr or '(PR non renseignée)'}" for n, pr, _ in ov["in_review"]] or ["- aucune"]
    if ov["blocked"]:
        lines += ["", "Tickets bloqués (une réponse dans le ticket les relance) :"]
        lines += [f"- https://github.com/{REPO}/issues/{n}" for n in ov["blocked"]]
    lines += ["", "Aucune fusion automatique : la file reprend dès qu'une PR est fusionnée, qu'un ticket est "
              "débloqué ou qu'un nouveau ticket agent:auto arrive."]
    queue_notification(con, f"idle:{digest}", None, "File à l'arrêt",
                       f"{NOTIFY_PREFIX} File à l'arrêt — {len(ov['in_review'])} PR en revue", "\n".join(lines))


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


def human_state(con):
    """État de la console interactive `lodyorch` (crochets UserPromptSubmit / Stop de la session du
    propriétaire) : ("busy" | "quiet" | "idle", horodatage, fin de la priorité humaine en epoch)."""
    state, at = ctl_get(con, "human_state"), ts_of(ctl_get(con, "human_at"))
    if not state or not at:
        return "idle", "", 0
    if state == "busy":
        end = at + setting(con, "human_busy_max_seconds")[0]
        return ("busy", ctl_get(con, "human_at"), end) if end > time.time() else ("idle", ctl_get(con, "human_at"), 0)
    end = at + setting(con, "human_quiet_seconds")[0]
    return ("quiet", ctl_get(con, "human_at"), end) if end > time.time() else ("idle", ctl_get(con, "human_at"), 0)


def normalize_text(text):
    """Minuscules, sans accents ni ponctuation, espaces réduits : base des classements ci-dessous."""
    import unicodedata
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[`'’\"«»]", " ", text)
    text = re.sub(r"[^a-z0-9#\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Formules de politesse tolérées autour d'une consultation (« stp », « merci »…), retirées avant classement.
_POLITE = re.compile(r"\b(stp|svp|s il te plait|s il vous plait|please|pls|merci|thanks|alors|bon|dis moi|"
                     r"donne moi|montre moi|peux tu me dire|tu peux me dire|rapidement|vite fait|hello|salut|"
                     r"bonjour|bonsoir|hey|coucou)\b")
_READ_COMMANDS = r"(status|statut|etat|pulse|queue|file|sessions|log|logs|journal|notifications|reports?|check|statusline)"
# Consultations reconnues (#139) : la phrase ENTIÈRE doit correspondre, après normalisation. Tout le
# reste reste une vraie demande (priorité humaine), choix prudent par défaut.
CONSULT_PATTERNS = [re.compile(p) for p in (
    rf"^(lodyorch )?{_READ_COMMANDS}( #?\d+)?$",
    r"^(ou|on) en (est|es|sont|sommes)( on| tu| vous| nous| il| elle| la file| le ticket| les tickets| lodyorch"
    r"| l orchestrateur| la ci| les pr| le travail| la session)?( #?\d+)?( on)?( maintenant| actuellement)?$",
    r"^on en est ou( avec #?\d+| sur #?\d+)?$",
    r"^(quel|quelle|c est quoi|qu est ce que c est que) (est |sont )?(l |le |la |les )?"
    r"(etat|statut|status|avancement|situation|progression)( de| du| des)?( la| l| le| les)?"
    r"( file| ticket| tickets| orchestrateur| lodyorch| ci| pr| session| travail)?( #?\d+)?( actuel| actuelle)?$",
    r"^(l |le |la )?(etat|statut|status|avancement|situation|point)( de| du| des)?( la| l| le| les)?"
    r"( file| ticket| tickets| orchestrateur| lodyorch| ci| pr| session| travail)?( #?\d+)?$",
    r"^(fais|fait|faire) (le |un )?point$",
    r"^(quoi de neuf|du nouveau|ca avance|ca avance bien|ca en est ou|ca donne quoi|tu en es ou|tu es ou)$",
    r"^(what s|what is|whats) (the )?(status|state|progress)( of (the )?(queue|ticket|ci|pr))?( #?\d+)?( now)?$",
    r"^(status|progress) (update|check|report)$",
    r"^(any )?(news|update|updates)$",
    r"^(que|qu est ce que|qu est ce qu) (fait|fais) (la file|lodyorch|l orchestrateur|la session|tu)( maintenant| actuellement)?$",
    r"^(quel|quels|quelle|quelles) (est|sont) (le |la |les )?(ticket|tickets|pr|session|sessions|prochain|"
    r"prochains)( en cours| suivant| suivants| en attente| ouverts| ouvertes)?$",
)]


def is_consultation(prompt):
    """Vrai si le message du propriétaire n'est qu'une consultation de l'état (#139) : il ne doit ni
    préempter la session de file, ni relancer le délai de calme. Court, et entièrement reconnu."""
    text = normalize_text(prompt)
    if not text or len(text) > 160:
        return False
    text = re.sub(r"\s+", " ", _POLITE.sub(" ", text)).strip()
    return bool(text) and any(p.match(text) for p in CONSULT_PATTERNS)


# Commentaires GitHub qui n'appellent aucun travail (#139) : remerciement, accusé de réception,
# simple question d'avancement. La phrase ENTIÈRE doit être reconnue ; sinon le ticket est relancé.
ACK_PATTERNS = [re.compile(p) for p in (
    r"^((merci|thanks|thank you|thx|bravo|super|parfait|top|genial|excellent|nickel|cool|great|nice|"
    r"bien recu|recu|vu|ok|okay|d accord|entendu|note|c est note|good job|well done|beau travail|"
    r"bon travail|tres bien|impeccable|felicitations|lu)( beaucoup| bien| a toi| a vous| pour (le|ce|ton|votre|"
    r"cette) (travail|rapport|pr|retour|livraison))?\s*)+$",
    r"^(pour|for) (le|ce|ton|votre|cette|the|your) (travail|rapport|pr|retour|livraison|work|report)$",
)]


def is_acknowledgement(body):
    """Vrai si le commentaire n'est qu'un remerciement / accusé de réception / question de statut."""
    raw = str(body or "")
    stripped = re.sub(r"[\U0001F300-\U0001FAFF☀-➿️]", " ", raw)  # émojis (👍, 🎉, ✅…)
    text = normalize_text(stripped)
    if len(text) > 160:
        return False
    if not text:
        return bool(raw.strip())  # uniquement des émojis
    text = re.sub(r"\s+", " ", _POLITE.sub(" ", text)).strip()
    return not text or any(p.match(text) for p in ACK_PATTERNS) or is_consultation(text)


def start_blocker(con):
    """Pourquoi aucune session ne peut démarrer maintenant : (raison, reprise_iso) ou None.
    Les limites du fournisseur passent avant la politique locale et ne sont jamais contournées."""
    if ctl_get(con, "mode", "RUNNING") == "PAUSED":
        return ctl_get(con, "pause_reason") or "Pause demandée (lodyorch resume pour reprendre).", ""
    human, _, until = human_state(con)
    if human == "busy":
        return "Priorité humaine : le propriétaire travaille dans la console lodyorch.", ""
    if human == "quiet":
        return ("Priorité humaine : conversation récente dans la console, la file reprend après un délai de calme.",
                iso(datetime.fromtimestamp(until, timezone.utc)))
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

PREPARE_TEXT = """## Mode PRÉPARATION (#165) — à respecter strictement

Ce ticket dépend de {deps}, dont la PR est en revue mais **non fusionnée** ({prs}). Rien d'autre
n'est exécutable : le superviseur te confie une **préparation**, qui remplace la règle 5 ci-dessous.
- Avance uniquement ce qui ne dépend pas du code non fusionné : contrats, schémas, tests (marqués
  `skip` si besoin), maquettes, documentation de conception. Si rien d'utile n'est possible, ne
  produis rien et explique-le.
- Branche du ticket partie d'`origin/main`, dans ton worktree : n'empile jamais sur la branche
  d'une dépendance, ne la modifie pas, ne fusionne, n'approuve et ne ferme aucune PR.
- PR en **brouillon** (`gh pr create --draft`), avec la mention « Préparation : à compléter après
  la fusion de {deps} ».
- Rapport de fin : `"status": "READY"`, `"deps": "{deps_csv}"`, la branche et la PR brouillon.
  Le superviseur reprendra le ticket en entier dès la fusion des dépendances.
"""


def build_prompt(con, t, session_id, status_file, prepare=None):
    base = PROMPT_FILE.read_text(encoding="utf-8")
    history = con.execute("""SELECT started_at, outcome, detail FROM sessions WHERE issue=?
                             ORDER BY started_at DESC LIMIT 5""", (t["issue"],)).fetchall()
    hist = "\n".join(f"- {h['started_at']} : {h['outcome']} — {(h['detail'] or '')[:300]}" for h in history) or "- (aucune)"
    state = {k: t[k] for k in t.keys()}
    queue = [dict(issue=r["issue"], status=r["status"], title=r["title"]) for r in con.execute(
        "SELECT issue,status,title FROM tasks ORDER BY priority, issue").fetchall()]
    mode = ""
    if prepare:
        mode = PREPARE_TEXT.format(deps=", ".join(f"#{n}" for n in prepare), deps_csv=",".join(map(str, prepare)),
                                   prs=", ".join(review_pr(con, n) or f"#{n}" for n in prepare))
    if "{{MODE}}" in base:
        base = base.replace("{{MODE}}", mode)
    elif mode:
        base = mode + "\n" + base
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
        return ("STOPPED" if killed.startswith(("Arrêt immédiat", "Priorité humaine")) else "TIMEOUT"), killed, ""
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


def run_session(con, t, prepare=None):
    session_id = str(uuid.uuid4())
    log_path = LOG_DIR / f"{iso().replace(':', '')}-{t['issue']}-{session_id[:8]}.jsonl"
    status_file = LOG_DIR / f"{session_id}.status.json"
    # Présent = le propriétaire a repris la main : lu par le crochet de garde de la session.
    yield_file = LOG_DIR / f"{session_id}.yield"
    yield_at = 0.0
    prompt = build_prompt(con, t, session_id, status_file, prepare)
    key = prep_key(con, prepare) if prepare else ""
    con.execute("INSERT INTO sessions(id,issue,started_at,log_path,mode) VALUES(?,?,?,?,?)",
                (session_id, t["issue"], iso(), str(log_path), "prepare" if prepare else ""))
    update_task(con, t["issue"], status="RUNNING", last_session=session_id, last_activity=iso(),
                attempts=t["attempts"] + 1)
    event(con, t["issue"], "session_start", f"Session {session_id} (tentative {t['attempts'] + 1})"
          + (f" — préparation : dépendances {', '.join(f'#{n}' for n in prepare)} en revue" if prepare else ""))
    if GH_SYNC and t["issue"] != TRIAGE:
        set_status_label(con, t["issue"], "RUNNING")
        if CLAIM_COMMENT:
            claim_issue(con, t["issue"], session_id)
    session_started = iso()
    env = dict(os.environ, LQ_SESSION_ID=session_id, LQ_STATUS_FILE=str(status_file), LQ_ISSUE=str(t["issue"]),
               LQ_YIELD_FILE=str(yield_file))
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
            if CI_WATCH:
                # La CI des PR déjà livrées reste suivie pendant que cette session avance sur un autre ticket.
                try:
                    watch_ci(con)
                except Exception as exc:  # noqa: BLE001 — un suivi de CI raté ne doit jamais tuer la session
                    event(con, None, "ci_error", f"{type(exc).__name__}: {exc}"[:500])
            if GH_SYNC and (time.time() - float(ctl_get(con, "last_sync_ts", "0") or 0) > SYNC_EVERY_SECONDS
                            or ctl_get(con, "sync_requested")):
                # #139 : la file GitHub reste à jour pendant une longue session (nouveaux tickets,
                # priorités, labels) ; le statut du ticket en cours n'est jamais changé par la synchronisation.
                try:
                    sync_github(con)
                except Exception as exc:  # noqa: BLE001 — une synchro ratée ne doit jamais tuer la session
                    event(con, None, "sync_error", f"{type(exc).__name__}: {exc}"[:500])
                    ctl_set(con, "last_sync_ts", time.time())
            reason = ""
            if human_state(con)[0] == "busy":
                # Le propriétaire parle dans la console : la session de file range son travail
                # (le crochet de garde ne lui permet plus que commit, push et rapport), puis s'arrête.
                if not yield_file.exists():
                    yield_file.write_text(iso(), encoding="utf-8")
                    yield_at = time.time()
                    set_phase(con, t["issue"], "pause demandée : priorité à la console")
                elif time.time() - yield_at > setting(con, "yield_grace_seconds")[0]:
                    reason = "Priorité humaine : session de file arrêtée après le délai de rangement."
            elif yield_file.exists():
                # La console est redevenue calme avant la fin du délai : restrictions levées, la session
                # reprend avec toutes ses permissions (pas d'arrêt, pas de nouvelle session consommée).
                # Un nouvel échange reposera le fichier avec un délai de rangement complet.
                yield_file.unlink()
                event(con, t["issue"], "yield", "Console redevenue calme : restrictions levées, la session continue.")
                set_phase(con, t["issue"], "reprise : la console est redevenue calme")
            if reason:
                pass  # priorité humaine : délai de rangement écoulé
            elif ctl_get(con, "mode") == "STOP_NOW":
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
    if yield_file.exists():
        yield_file.unlink()
        event(con, t["issue"], "yield", "Session de file rendue à la console (priorité humaine).")
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
    if prepare and report.get("status") in STATUSES:
        settle_preparation(con, t["issue"], prepare, key)
    if GH_SYNC and t["issue"] != TRIAGE:
        after_outcome(con, t["issue"], session_id, session_started, report)
    if t["issue"] != TRIAGE:
        record_run(con, t["issue"], session_id, outcome, detail, report)
    set_phase(con, t["issue"], f"session terminée : {outcome}")
    if outcome != "COMPLETED" or not report:
        ctl_set(con, "last_error", redact(f"{iso()} #{t['issue']} {outcome} — {detail}")[:500])
    return outcome


def settle_preparation(con, issue, missing, key):
    """Après une préparation rendue : le ticket ne part pas en revue (sa PR est un brouillon) ; il
    garde ses dépendances et redevient READY pour être repris en entier dès leur fusion. La clé
    empêche toute nouvelle préparation tant que l'état des dépendances ne change pas."""
    t = task(con, issue)
    known = {int(x) for x in re.findall(r"\d+", t["deps"] or "")}
    fields = {"prep_key": key, "deps": ",".join(map(str, sorted(known | set(missing))))}
    if t["status"] in ("WAITING_OWNER", "RUNNING", "READY"):
        fields.update(status="READY", ci_state="", attempts=0, not_before="",
                      next_action=f"Préparé ({t['pr'] or 'sans PR'}) : reprise complète après la fusion de "
                                  + ", ".join(f"#{n}" for n in missing) + ".")
    update_task(con, issue, **fields)
    event(con, issue, "prepared", f"Préparation rendue ({t['status']}) : attend la fusion de "
                                  + ", ".join(f"#{n}" for n in missing) + ".")


def claim_issue(con, issue, session_id):
    """Commentaire de prise en charge, UNE fois par ticket : déjà publié (base) → rien ; trouvé
    sur GitHub (crash entre publication et enregistrement) → seulement enregistré."""
    t = task(con, issue)
    if not t or t["claimed"]:
        return
    comments = gh_list(f"repos/{REPO}/issues/{issue}/comments")
    if comments is None:
        event(con, issue, "gh_error", "Commentaires illisibles : prise en charge reportée à la session suivante.")
        return
    if not any(CLAIM_MARKER in (c.get("body") or "") for c in comments):
        body = (f"{BOT_MARKER}\n{CLAIM_MARKER}\n**Prise en charge par {INSTANCE}** (session `{session_id[:8]}`).\n\n"
                "La session publie ici son plan et sa branche, puis le résultat avec la pull request. "
                "Aucune fusion ni aucun déploiement automatique : la PR attend votre revue.\n\n"
                f"Pour l'arrêter : retirer `{ELIGIBLE_LABEL or 'le label'}` ou poser `{STATUS_LABELS['BLOCKED']}`.")
        if not gh_call("-X", "POST", f"repos/{REPO}/issues/{issue}/comments", "-f", f"body={body}"):
            event(con, issue, "gh_error", "Commentaire de prise en charge non publié.")
            return
        event(con, issue, "claim", "Prise en charge publiée sur GitHub.")
    update_task(con, issue, claimed=1)


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
        why = ("priorité à la console du propriétaire" if detail.startswith("Priorité humaine")
               else "session arrêtée volontairement (stop --now)")
        update_task(con, issue, status="RUNNING", attempts=max(0, t["attempts"] - 1), not_before="",
                    next_action=f"Reprendre : {why}.")
        return
    if report.get("status") in STATUSES:
        fields = {k: report[k] for k in ("branch", "pr", "commit_sha", "summary", "tests", "blockers", "next_action",
                                         "deps")
                  if k in report and report[k] is not None}
        fields = {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) for k, v in fields.items()}
        status = report["status"]
        if status == "WAITING_OWNER" and REQUIRE_PR and not str(report.get("pr") or "").strip():
            # Livré sans PR : pas une livraison. Reprise bornée par max_attempts, puis blocage.
            if t["attempts"] >= setting(con, "max_attempts")[0]:
                status = "BLOCKED"
                fields["blockers"] = f"Livraison déclarée sans PR après {t['attempts']} sessions."
            else:
                status = "RUNNING"
                fields["next_action"] = "Livraison déclarée sans PR : ouvrir l'unique PR du ticket (ou la retrouver)."
            event(con, issue, "no_pr", f"Rapport WAITING_OWNER sans PR → {status}.")
        if status in ("DONE", "WAITING_OWNER", "BLOCKED"):
            fields["attempts"] = 0
        if status == "WAITING_OWNER" and CI_WATCH and str(fields.get("pr") or t["pr"] or "").strip():
            fields.update(ci_state="pending", ci_detail="", ci_since=iso(), ci_checked_at="")
            fields.setdefault("next_action", "CI suivie par le superviseur : rien à faire avant son verdict.")
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


def label_anomalies(labels):
    """Labels incohérents d'un ticket éligible, en clair (vide si tout va bien). Rien n'est corrigé
    sur GitHub : la règle appliquée est annoncée (priorité la plus haute, exclusion l'emporte)."""
    out = []
    prios = [(name, PRIORITY_RE.match(name).group(1).strip()) for name in labels if PRIORITY_RE.match(name)]
    valid = sorted({v.upper() for _, v in prios if re.fullmatch(r"(?i)P[0-3]", v)})
    for name, value in prios:
        if not re.fullmatch(r"(?i)P[0-3]", value):
            out.append(f"priorité invalide « {name} » (attendu priority:P0…P3)")
    if len(valid) > 1:
        out.append(f"plusieurs priorités ({', '.join(valid)}) : {valid[0]} retenue")
    if not valid:
        out.append(f"aucune priorité valide : P{DEFAULT_PRIORITY_LEVEL} appliquée")
    held = sorted(set(labels) & HOLD_LABELS)
    if ELIGIBLE_LABEL and ELIGIBLE_LABEL in labels and held:
        out.append(f"{ELIGIBLE_LABEL} avec {', '.join(held)} : exclu de la file")
    known = {ELIGIBLE_LABEL, *HOLD_LABELS, *STATUS_LABELS.values()}
    if ELIGIBLE_LABEL.startswith("agent:"):
        out += [f"label inconnu « {name} »" for name in labels if name.startswith("agent:") and name not in known]
    return out


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
    return ok


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
            issues = "; ".join(label_anomalies(labels))
            if issues:
                update_task(con, n, label_issues=issues)
                event(con, n, "labels", f"Labels incohérents : {issues}.")
            if status == "READY" and "READY" in STATUS_LABELS:
                set_status_label(con, n, "READY")
            continue
        fields = {"title": it.get("title", "") or t["title"], "gh_labels": json.dumps(labels)}
        issues = "; ".join(label_anomalies(labels)) if eligible else ""
        if issues != (t["label_issues"] or ""):
            fields["label_issues"] = issues
            if issues:
                event(con, n, "labels", f"Labels incohérents : {issues}.")
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
            elif t["status"] in ("BLOCKED", "FAILED") and eligible and STATUS_LABELS["BLOCKED"] not in labels:
                # #139 : GitHub est la file. Un blocage purement local (skip, essai, label non posé) est
                # reporté sur le ticket ; le retirer suffit ensuite à reprendre, sans `lodyorch add`.
                if set_status_label(con, n, "BLOCKED"):
                    labels = sorted(set(labels) | {STATUS_LABELS["BLOCKED"]})
                    fields["gh_labels"] = json.dumps(labels)
                    event(con, n, "label", f"Blocage local reporté sur GitHub (« {STATUS_LABELS['BLOCKED']} ») : "
                                           "le retirer remet le ticket en file.")
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


def pr_number(pr):
    m = re.search(r"(\d+)\s*$", str(pr or ""))
    return int(m.group(1)) if m else None


def ci_verdict(sha):
    """("pending" | "success" | "failure" | "none", détail) pour les vérifications d'un commit."""
    data = gh_json(f"repos/{REPO}/commits/{sha}/check-runs?per_page=100")
    if not isinstance(data, dict):
        return "unknown", "GitHub injoignable"
    runs = data.get("check_runs") or []
    if not runs:
        return "none", "aucune vérification signalée"
    failed = [r for r in runs if r.get("status") == "completed" and r.get("conclusion") in CI_FAILED]
    if failed:
        return "failure", "; ".join(f"{r.get('name')} : {r.get('conclusion')} ({r.get('html_url', '')})" for r in failed)[:900]
    waiting = [r.get("name") for r in runs if r.get("status") != "completed"]
    if waiting:
        return "pending", f"{len(runs) - len(waiting)}/{len(runs)} terminées, en cours : {', '.join(waiting)[:200]}"
    return "success", f"{len(runs)} vérification(s) réussie(s)"


def base_validated(pr):
    """Une PR empilée n'est prête que si sa base l'est : branche par défaut, ou PR de base verte."""
    base = (pr.get("base") or {}).get("ref", "")
    default = ((pr.get("base") or {}).get("repo") or {}).get("default_branch") or "main"
    if base == default:
        return True, ""
    owner = REPO.split("/")[0]
    bases = gh_json(f"repos/{REPO}/pulls?state=open&head={owner}:{urllib.parse.quote(base)}")
    if not bases:
        return False, f"base « {base} » sans PR ouverte validée"
    verdict, _ = ci_verdict(bases[0]["head"]["sha"])
    return verdict == "success", "" if verdict == "success" else f"base #{bases[0]['number']} : CI {verdict}"


def watch_ci(con):
    """Suit la CI des PR livrées, à intervalle LQ_CI_POLL par PR (une à deux requêtes GitHub)."""
    limit = iso(datetime.fromtimestamp(time.time() - CI_POLL_SECONDS, timezone.utc))
    for t in con.execute("""SELECT * FROM tasks WHERE status='WAITING_OWNER' AND ci_state='pending'
                            AND (ci_checked_at='' OR ci_checked_at <= ?) ORDER BY priority, issue""", (limit,)).fetchall():
        n = t["issue"]
        update_task(con, n, ci_checked_at=iso())
        num = pr_number(t["pr"])
        pr = gh_json(f"repos/{REPO}/pulls/{num}") if num else None
        if not pr:
            continue  # GitHub injoignable ou PR inconnue : nouvel essai au prochain intervalle
        if pr.get("merged"):
            update_task(con, n, ci_state="merged", ci_detail="PR fusionnée par le propriétaire.")
            event(con, n, "ci", "PR fusionnée : suivi de CI terminé.")
            finalize_pending_runs(con, n)
            continue
        if pr.get("state") == "closed":
            update_task(con, n, status="BLOCKED", ci_state="closed", blockers="PR fermée sans fusion.",
                        next_action="Décision du propriétaire : rouvrir la PR ou retirer le ticket.")
            set_status_label(con, n, "BLOCKED")
            finalize_pending_runs(con, n)
            continue
        verdict, detail = ci_verdict(pr["head"]["sha"])
        waited = time.time() - (ts_of(t["ci_since"]) or time.time())
        if verdict == "success":
            ok, why = base_validated(pr)
            if not ok:
                update_task(con, n, ci_detail=f"CI verte, mais {why} : pas encore prête.")
                continue
            update_task(con, n, ci_state="success", ci_detail=detail, ci_fixes=0,
                        next_action="Revue et fusion par le propriétaire (CI verte).")
            event(con, n, "ci", f"CI verte ({detail}) : PR prête pour revue.")
            set_status_label(con, n, "WAITING_OWNER")
            after_outcome(con, n, t["last_session"], t["gh_since"] or iso(), {})
            finalize_pending_runs(con, n)
            if auto_merge_mode(con)[0] != "off":
                update_task(con, n, merge_checked_at="")  # évaluée tout de suite par watch_merges
        elif verdict == "failure":
            fixes = t["ci_fixes"] + 1
            if fixes <= CI_MAX_FIXES:
                update_task(con, n, status="RUNNING", attempts=0, not_before="", ci_state="failed", ci_fixes=fixes,
                            ci_detail=detail,
                            note=f"CI en échec sur {t['pr']} (commit {pr['head']['sha'][:10]}) : {detail}. "
                                 f"Correction {fixes}/{CI_MAX_FIXES} : lire `gh run view --log-failed`, corriger sur la "
                                 "même branche, pousser, puis rendre WAITING_OWNER (même PR).",
                            next_action=f"Corriger la CI ({fixes}/{CI_MAX_FIXES}).")
                event(con, n, "ci", f"CI en échec : correction {fixes}/{CI_MAX_FIXES} planifiée — {detail[:200]}")
                finalize_pending_runs(con, n)
            else:
                update_task(con, n, status="BLOCKED", ci_state="failed", ci_detail=detail,
                            blockers=f"CI toujours en échec après {CI_MAX_FIXES} corrections : {detail}",
                            next_action="Diagnostic du propriétaire nécessaire (voir le commentaire du ticket).")
                set_status_label(con, n, "BLOCKED")
                body = (f"{BOT_MARKER}\n**CI en échec après {CI_MAX_FIXES} corrections** sur {t['pr']} "
                        f"(commit `{pr['head']['sha'][:10]}`).\n\n{detail}\n\nTicket mis de côté "
                        f"(`{STATUS_LABELS['BLOCKED']}`) : répondre ici ou retirer le label pour relancer.")
                gh_call("-X", "POST", f"repos/{REPO}/issues/{n}/comments", "-f", f"body={body}")
                event(con, n, "ci", "CI en échec : corrections épuisées, ticket bloqué.")
                finalize_pending_runs(con, n)
        elif waited > CI_TIMEOUT_SECONDS:
            update_task(con, n, status="BLOCKED", ci_state="timeout", ci_detail=detail,
                        blockers=f"CI sans conclusion après {span(waited)} ({detail}).",
                        next_action="Vérifier les workflows GitHub Actions de la PR.")
            set_status_label(con, n, "BLOCKED")
            event(con, n, "ci", f"CI sans conclusion après {span(waited)} : ticket bloqué.")
            finalize_pending_runs(con, n)
        else:
            update_task(con, n, ci_detail=detail)


# ---------------------------------------------------------------- fusion automatique (#200)

def auto_merge_mode(con):
    """(mode effectif, explication). Plafond LQ_AUTO_MERGE ∧ choix `lodyorch automerge`. Le mode `on`
    exige en plus un horodatage d'activation (les autorisations antérieures ne valent rien)."""
    chosen = ctl_get(con, "automerge.mode", "") or "dry-run"
    if chosen not in MERGE_MODES:
        chosen = "off"
    mode = MERGE_MODES[min(MERGE_MODES.index(chosen), MERGE_MODES.index(AUTO_MERGE_CEILING))]
    if mode == "on" and not ctl_get(con, "automerge.enabled_at"):
        mode = "dry-run"
    why = f"choisi {chosen}, plafond LQ_AUTO_MERGE={AUTO_MERGE_CEILING}"
    return mode, why


def merge_policy():
    """Module de politique installé à côté de lodyorch.py (import différé : son absence coupe la
    fusion automatique sans arrêter la file)."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import merge_policy as mp  # noqa: PLC0415
    return mp


def merge_audit(con, issue, decision, mode):
    """Journalise le verdict UNE fois par (PR, SHA, mode, critères refusés) : rien de neuf, rien d'écrit.
    Rend True si le verdict est nouveau."""
    refusals = [{"code": c.code, "detail": c.detail} for c in decision.refusals]
    signature = hashlib.sha256(json.dumps([decision.pr, decision.head_sha, mode, sorted(r["code"] for r in refusals)])
                               .encode()).hexdigest()[:20]
    last = con.execute("SELECT signature FROM merge_audit WHERE pr=? ORDER BY id DESC LIMIT 1", (decision.pr,)).fetchone()
    if last and last["signature"] == signature:
        return False
    mp = merge_policy()
    con.execute("""INSERT INTO merge_audit(at,issue,pr,head_sha,mode,eligible,signature,refusals,matrix)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (iso(), issue, decision.pr, decision.head_sha, mode, int(decision.eligible), signature,
                 json.dumps(refusals, ensure_ascii=False), redact(mp.render(decision))))
    con.commit()
    reasons = "; ".join(f"{r['code']} ({r['detail']})" if r["detail"] else r["code"] for r in refusals)
    verdict = ("éligible" + (" — simulation, rien fusionné" if mode == "dry-run" else "")) if decision.eligible \
        else f"refusée — {reasons}"
    event(con, issue, "merge", f"[{mode}] PR #{decision.pr} @ {decision.head_sha[:10]} : {verdict}")
    return True


def merge_comment(con, issue, decision, mode, headline):
    """Commentaire de ticket (verdict lisible + matrice) — seulement si `merge:auto` est demandé."""
    mp = merge_policy()
    body = (f"{BOT_MARKER}\n{headline}\n\nMode `{mode}`, politique v{decision.policy_version}.\n\n"
            f"<details><summary>Matrice de décision</summary>\n\n{mp.render(decision)}\n\n</details>")
    gh_call("-X", "POST", f"repos/{REPO}/issues/{issue}/comments", "-f", f"body={redact(body)}")


def recover_merges(con):
    """Fusion interrompue (arrêt brutal entre l'intention et la réponse GitHub) : on CONSTATE l'état
    réel de la PR au lieu de rejouer la requête — fusionnée → suites normales, sinon intention close."""
    done = []
    for op in con.execute("SELECT * FROM merge_ops WHERE state='intent'").fetchall():
        pr = gh_json(f"repos/{REPO}/pulls/{op['pr']}")
        if pr is None:
            continue  # GitHub injoignable : on réessaiera au prochain tour, sans rien refaire
        if pr.get("merged") and (pr.get("head") or {}).get("sha") == op["head_sha"]:
            con.execute("UPDATE merge_ops SET state='merged', finished_at=?, merge_sha=? WHERE pr=? AND head_sha=?",
                        (iso(), pr.get("merge_commit_sha") or "", op["pr"], op["head_sha"]))
            con.commit()
            event(con, op["issue"], "merge", f"Reprise : PR #{op['pr']} constatée fusionnée.")
            done.append(after_auto_merge(con, op["issue"], op["pr"], op["head_sha"], pr.get("merge_commit_sha") or ""))
        else:
            con.execute("UPDATE merge_ops SET state='interrupted', finished_at=? WHERE pr=? AND head_sha=?",
                        (iso(), op["pr"], op["head_sha"]))
            con.commit()
            event(con, op["issue"], "merge", f"Reprise : fusion de PR #{op['pr']} non effectuée, réévaluation.")
    return [d for d in done if d]


def after_auto_merge(con, issue, num, head_sha, merge_sha):
    """Suites d'une fusion automatique : ticket gardé « à tester » (rouvert si `Closes #N` l'a fermé),
    commentaire, rapport. Aucun déploiement. Rend la ligne de notification groupée."""
    t = task(con, issue)
    update_task(con, issue, ci_state="merged", status="WAITING_OWNER",
                ci_detail=f"Fusionnée automatiquement (merge:auto), commit {merge_sha[:10]} — non déployée.",
                next_action="Tester la fonctionnalité (fusionnée, non déployée) puis fermer le ticket.")
    it = gh_json(f"repos/{REPO}/issues/{issue}")
    if it and it.get("state") == "closed":
        if gh_call("-X", "PATCH", f"repos/{REPO}/issues/{issue}", "-f", "state=open"):
            event(con, issue, "merge", "Ticket rouvert après la fusion (fermé par « Closes ») : test humain requis.")
        else:
            event(con, issue, "gh_error", "Ticket fermé par la fusion et non rouvert : le rouvrir pour le test.")
    set_status_label(con, issue, "WAITING_OWNER")
    gh_call("-X", "POST", f"repos/{REPO}/issues/{issue}/comments", "-f",
            f"body={BOT_MARKER}\n**PR #{num} fusionnée automatiquement** dans `main` (tête `{head_sha[:10]}`, "
            f"fusion `{merge_sha[:10]}`) : CI verte, `merge:auto`, politique revalidée juste avant.\n\n"
            "**Non déployée.** Le ticket reste `à tester` : la fermeture suit votre vérification.")
    finalize_pending_runs(con, issue)
    title = t["title"] if t else ""
    return f"#{issue} — PR #{num} fusionnée ({merge_sha[:10]}) — {title} — test à faire"


def try_auto_merge(con, t, mode):
    """Évalue la PR d'un ticket (CI verte) ; en mode `on`, revalide puis fusionne avec le SHA attendu.
    Rend une ligne de notification si une fusion a eu lieu."""
    mp = merge_policy()
    n, num = t["issue"], pr_number(t["pr"])
    if not num:
        return None
    since = ctl_get(con, "automerge.enabled_at") if mode == "on" else ""
    pr = gh_json(f"repos/{REPO}/pulls/{num}")
    it = gh_json(f"repos/{REPO}/issues/{n}")
    if not pr or not it:
        return None  # GitHub injoignable : nouvel essai au prochain intervalle
    if pr.get("merged"):
        update_task(con, n, ci_state="merged", ci_detail="PR fusionnée par le propriétaire.")
        event(con, n, "ci", "PR fusionnée : suivi terminé.")
        finalize_pending_runs(con, n)
        return None
    if pr.get("state") != "open":
        return None
    asked = mp.AUTHORIZE_LABEL in mp._labels(it)
    head = (pr.get("head") or {}).get("sha", "")
    if not asked and con.execute("SELECT 1 FROM merge_audit WHERE pr=? AND head_sha=? AND mode=?",
                                 (num, head, mode)).fetchone():
        return None  # sans autorisation, un seul verdict complet par SHA : pas de collecte répétée
    try:
        facts = mp.collect(num)
    except Exception as exc:  # noqa: BLE001 — erreur API : aucun verdict, nouvel essai plus tard
        event(con, n, "merge_error", f"PR #{num} : collecte impossible ({type(exc).__name__}: {exc})"[:400])
        return None
    if since:
        facts["authorized_after"] = since
    decision = mp.evaluate(facts)
    decision.dry_run = mode != "on"
    new = merge_audit(con, n, decision, mode)
    if not decision.eligible:
        if new and asked:
            reasons = "\n".join(f"- `{c.code}` : {c.detail or 'non satisfait'}" for c in decision.refusals)
            merge_comment(con, n, decision, mode, f"**Fusion automatique refusée** pour la PR #{num} "
                          f"(`{decision.head_sha[:10]}`) — revue manuelle :\n{reasons}")
        return None
    if mode != "on":
        if new and asked:
            merge_comment(con, n, decision, mode, f"**Simulation** : la PR #{num} (`{decision.head_sha[:10]}`) "
                          "serait fusionnée automatiquement. Rien n'a été fusionné (mode à blanc).")
        return None
    # Revalidation immédiate : nouvelle collecte complète, même SHA, mode toujours `on`.
    if auto_merge_mode(con)[0] != "on":
        return None
    try:
        fresh = mp.collect(num)
    except Exception as exc:  # noqa: BLE001
        event(con, n, "merge_error", f"PR #{num} : revalidation impossible ({type(exc).__name__}) — pas de fusion.")
        return None
    fresh["authorized_after"] = since
    again = mp.revalidate(decision, fresh)
    again.dry_run = False
    if not again.eligible:
        merge_audit(con, n, again, mode)
        return None
    if con.execute("""SELECT 1 FROM merge_ops WHERE pr=? AND (state IN ('intent','merged')
                      OR (head_sha=? AND state='failed'))""", (num, again.head_sha)).fetchone():
        return None  # fusion engagée, faite, ou déjà refusée par GitHub pour ce SHA : jamais de nouvelle requête
    # Intention écrite AVANT la requête : un arrêt brutal est repris par recover_merges, sans rejouer.
    con.execute("INSERT OR REPLACE INTO merge_ops(pr,head_sha,issue,state,started_at) VALUES(?,?,?,?,?)",
                (num, again.head_sha, n, "intent", iso()))
    con.commit()
    try:
        merge_sha = mp.merge(again, title=f"Merge pull request #{num} (fusion automatique lodyorch, #{n})")
    except mp.MergeError as exc:
        con.execute("UPDATE merge_ops SET state='failed', finished_at=?, error=? WHERE pr=? AND head_sha=?",
                    (iso(), redact(str(exc))[:300], num, again.head_sha))
        con.commit()
        why = {405: "PR non fusionnable", 409: "tête modifiée depuis l'évaluation (course de SHA)"}.get(exc.status, "erreur API")
        event(con, n, "merge_error", f"PR #{num} : fusion refusée par GitHub ({why}) : {exc}"[:500])
        return None
    con.execute("UPDATE merge_ops SET state='merged', finished_at=?, merge_sha=? WHERE pr=? AND head_sha=?",
                (iso(), merge_sha, num, again.head_sha))
    con.commit()
    event(con, n, "merge", f"PR #{num} fusionnée automatiquement ({again.head_sha[:10]} → {merge_sha[:10]}).")
    return after_auto_merge(con, n, num, again.head_sha, merge_sha)


def watch_merges(con):
    """Évalue les PR « prêtes pour revue » (CI verte) à intervalle LQ_CI_POLL : un `merge:auto` posé après
    la CI est pris en compte sans session Claude. Au plus UNE fusion par tour (main bouge ensuite)."""
    mode, _ = auto_merge_mode(con)
    if mode == "off":
        return []
    try:
        merged = recover_merges(con)
    except Exception as exc:  # noqa: BLE001
        event(con, None, "merge_error", f"reprise des fusions : {type(exc).__name__}: {exc}"[:400])
        return []
    limit = iso(datetime.fromtimestamp(time.time() - CI_POLL_SECONDS, timezone.utc))
    for t in con.execute("""SELECT * FROM tasks WHERE status='WAITING_OWNER' AND ci_state='success' AND pr != ''
                            AND (merge_checked_at='' OR merge_checked_at <= ?) ORDER BY priority, issue""",
                         (limit,)).fetchall():
        update_task(con, t["issue"], merge_checked_at=iso())
        line = try_auto_merge(con, t, mode)
        if line:
            merged.append(line)
            break
    if merged:
        notify_merges(con, merged)
    return merged


def notify_merges(con, lines):
    """Notification concise et groupée : ce qui a été fusionné, et le test humain qui reste à faire."""
    key = "automerge:" + hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]
    body = "\n".join(["Fusion automatique (non déployée) :", *[f"- {x}" for x in lines], "",
                      "Chaque ticket reste « à tester » : vérifier puis fermer. Aucun déploiement n'a été fait.",
                      f"Arrêt immédiat : {CLI_NAME} automerge off"])
    queue_notification(con, key, None, "Fusion automatique", f"{NOTIFY_PREFIX} {len(lines)} PR fusionnée(s) — à tester",
                       body)


CI_TEXT = {"pending": "CI en attente", "success": "CI validée", "failed": "CI en échec", "merged": "fusionnée",
           "closed": "PR fermée", "timeout": "CI sans conclusion"}


def run_state(t):
    """État lisible d'un ticket (#127) : work_in_progress, ci_pending, ci_failed, ready_for_review…"""
    if t["status"] == "RUNNING":
        return "ci_failed" if t["ci_state"] == "failed" else "work_in_progress"
    if t["status"] == "WAITING_OWNER":
        return {"pending": "ci_pending", "success": "ready_for_review", "merged": "merged"}.get(t["ci_state"], "pr_open")
    if t["status"] in ("BLOCKED", "FAILED"):
        return "blocked"
    if t["status"] == "SUSPENDED":
        return "needs_human"
    return t["status"].lower()


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
        if is_acknowledgement(c.get("body")):
            # #139 : remerciement, accusé de réception ou question d'avancement : aucune session Claude.
            event(con, n, "reply_ignored", f"Commentaire sans demande de travail ({c.get('html_url', '')}) : "
                                           f"ticket laissé {t['status']}, aucune session lancée.")
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
    if status in ("BLOCKED", "WAITING_OWNER", "FAILED", "DONE") and report:
        update_task(con, issue, gh_since=session_started,
                    questions=json.dumps([str(q) for q in report.get("questions") or []], ensure_ascii=False),
                    test_links=json.dumps([str(x) for x in report.get("test_links") or []], ensure_ascii=False),
                    report_url=str(report.get("report_url") or t["report_url"] or ""))
    if status == "WAITING_OWNER" and t["ci_state"] == "pending":
        # PR ouverte, CI en attente : le verrou reste, rien n'est annoncé comme prêt.
        set_status_label(con, issue, "RUNNING")
        return
    set_status_label(con, issue, status if status in STATUS_LABELS else "RUNNING")
    if status not in ("WAITING_OWNER", "BLOCKED", "DONE", "FAILED") or RUN_REPORTS:
        return  # avec les rapports de tentative (#123), l'email part avec le rapport, une fois par run
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
        # Texte UTF-8 déclaré (charset) : sans lui, les accents du rapport arrivent corrompus.
        msg.add_attachment(attachment, subtype="markdown", charset="utf-8", filename=attachment_name)
    with smtplib.SMTP_SSL(cfg["HOST"], int(cfg["PORT"]), context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(cfg["USERNAME"], cfg["PASSWORD"])
        refused = smtp.send_message(msg)
    if refused:
        raise RuntimeError(f"{len(refused)} destinataire(s) refusé(s)")


def flush_notifications(con):
    """Envoie ce qui est en attente ; un échec n'arrête jamais la file : nouvel essai plus tard
    (délai croissant, 30 essais au plus). Le message d'erreur ne contient jamais d'identifiant.
    Fichier SMTP illisible (superviseur isolé par systemd) : rien n'est tenté ni compté, l'envoi
    revient à l'unité dédiée lodyorch-notify qui, elle seule, lit ce fichier."""
    if not os.access(NOTIFY_SMTP_ENV, os.R_OK):
        return
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


# ---------------------------------------------------------------- rapports de tentative (#123)

def record_run(con, issue, session_id, outcome, detail, report):
    """Enregistre la tentative (une seule fois par session) puis rédige son rapport, sauf si la CI
    de la PR livrée est encore en cours : le rapport attend alors son verdict (watch_ci)."""
    if not RUN_REPORTS or outcome in REPORT_SKIP_OUTCOMES or not session_id:
        return
    t = task(con, issue)
    if not t:
        return
    run_id = session_id[:8]
    waiting = t["status"] == "WAITING_OWNER" and t["ci_state"] == "pending"
    cur = con.execute("""INSERT OR IGNORE INTO run_reports(run_id,issue,session_id,outcome,detail,report,state,created_at)
                         VALUES(?,?,?,?,?,?,?,?)""",
                      (run_id, issue, session_id, outcome, detail or "", json.dumps(report or {}, ensure_ascii=False),
                       "awaiting_ci" if waiting else "new", iso()))
    con.commit()
    if not cur.rowcount:
        return
    if waiting:
        event(con, issue, "report", f"Rapport du run {run_id} : rédigé au verdict de la CI.")
    else:
        finalize_run(con, run_id)


def finalize_pending_runs(con, issue):
    for r in con.execute("SELECT run_id FROM run_reports WHERE issue=? AND state='awaiting_ci'", (issue,)).fetchall():
        finalize_run(con, r["run_id"])


def report_state(t, outcome):
    """État final lisible : terminé / à tester / bloqué / échec (+ précision)."""
    st, ci = t["status"], t["ci_state"]
    if st == "DONE":
        return "terminé"
    if st == "WAITING_OWNER":
        return {"success": "à tester — CI verte", "merged": "à tester — PR fusionnée par le propriétaire",
                "pending": "à tester — CI en cours"}.get(ci, "à tester")
    if st == "BLOCKED":
        return "bloqué"
    if st == "FAILED":
        return "échec"
    if ci == "failed":
        return "échec — CI rouge, correction relancée automatiquement"
    if outcome == "INTERRUPTED":
        return "échec — tentative interrompue, ticket repris automatiquement"
    return "échec — tentative sans livraison, nouvelle tentative automatique"


def session_report_text(report):
    """Rapport détaillé écrit par la session : accepté seulement sous STATE_DIR/reports (une session
    ne peut pas faire joindre un autre fichier du compte, par exemple des identifiants)."""
    raw = str((report or {}).get("report_file") or "")
    if not raw:
        return ""
    try:
        path = Path(raw).resolve()
        if not path.is_relative_to((STATE_DIR / "reports").resolve()) or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def as_list(value):
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return [value] if value.strip() else []
    return [str(x) for x in value if str(x).strip()] if isinstance(value, list) else [str(value)]


def render_run_report(con, r, t):
    """Rapport standard (rubriques 1 à 7 du ticket #123), en Markdown, secrets masqués."""
    n, run_id = r["issue"], r["run_id"]
    report = json.loads(r["report"] or "{}")
    s = con.execute("SELECT * FROM sessions WHERE id=?", (r["session_id"],)).fetchone()
    rank = con.execute("SELECT COUNT(*) FROM sessions WHERE issue=? AND started_at <= ?",
                       (n, s["started_at"] if s else iso())).fetchone()[0]
    state = report_state(t, r["outcome"])
    url = f"https://github.com/{REPO}/issues/{n}"
    num = pr_number(t["pr"])
    files = gh_list(f"repos/{REPO}/pulls/{num}/files") if num else None
    commits = gh_list(f"repos/{REPO}/pulls/{num}/commits") if num else None
    deployed = ("PR fusionnée par le propriétaire ; aucun déploiement fait par lodyorch."
                if t["ci_state"] == "merged" else
                "Rien n'est fusionné ni déployé : lodyorch ne fusionne et ne déploie jamais.")
    out = [REPORT_MARKER.format(run=run_id), "",
           f"# Rapport lodyorch — {REPORT_PROJECT} — #{n}", "",
           "| | |", "|---|---|",
           f"| Projet | {REPORT_PROJECT} |",
           f"| Date (UTC) | {r['created_at']} |",
           f"| Ticket | [#{n} — {t['title']}]({url}) |",
           f"| Session lodyorch | `{r['session_id']}` (tentative n° {rank}) |",
           f"| Run | `{run_id}` |",
           f"| Fin de session | {r['outcome']} — {(r['detail'] or '').splitlines()[0][:200] if r['detail'] else '-'} |",
           f"| **État final** | **{state}** |", "",
           "## 1. Résumé", "",
           t["summary"] or str(report.get("summary") or "") or "(aucun résumé : la session n'a pas rendu de rapport)", ""]
    remaining = str(report.get("remaining") or "").strip()
    out += [f"**Reste à faire :** {remaining or 'rien de signalé.'}", "", f"**Déploiement :** {deployed}", "",
            "## 2. Branche, commits, PR, CI et tests", "",
            f"- Branche : `{t['branch']}`" if t["branch"] else "- Branche : aucune",
            f"- Dernier commit : [`{t['commit_sha'][:10]}`](https://github.com/{REPO}/commit/{t['commit_sha']})"
            if t["commit_sha"] else "- Dernier commit : aucun",
            f"- PR : {t['pr']}" if t["pr"] else "- PR : aucune",
            f"- CI : {CI_TEXT.get(t['ci_state'], 'non suivie')}" + (f" — {t['ci_detail']}" if t["ci_detail"] else ""),
            f"- Tests exécutés (déclarés par la session) : {t['tests'] or report.get('tests') or 'non renseignés'}"]
    if commits:
        out += ["", "Commits de la PR :"]
        out += [f"- [`{c['sha'][:10]}`]({c.get('html_url') or ''}) {str(c.get('commit', {}).get('message', '')).splitlines()[0][:120]}"
                for c in commits[:30]]
    out += ["", "## 3. Fichiers modifiés", ""]
    if files:
        out += [f"- `{f['filename']}` ({f.get('status', '')}, +{f.get('additions', 0)} −{f.get('deletions', 0)})"
                for f in files[:100]]
    else:
        out.append("Non disponible (pas de PR, ou GitHub injoignable au moment du rapport).")
    cost = s["cost_usd"] if s else None
    out += ["", "## 4. Risques, coûts et décisions", "",
            f"- Risques : {report.get('risks') or 'voir la PR (rubrique « Risques »).'}",
            f"- Coût de la session : {f'{cost:.2f} USD (prix catalogue estimé par Claude Code, pas une facture)' if cost else 'non disponible'}"]
    questions = as_list(report.get("questions")) or as_list(t["questions"])
    out += [f"- Question {i} : {q}" for i, q in enumerate(questions, 1)] or ["- Aucune décision du propriétaire demandée."]
    out += ["", "## 5. Test humain", ""]
    steps = as_list(report.get("human_tests"))
    links = as_list(report.get("test_links")) or as_list(t["test_links"])
    out += [f"{i}. {x}" for i, x in enumerate(steps, 1)] or ["Aucune instruction de test fournie par la session."]
    out += [f"- Lien : {x}" for x in links]
    out += ["", "## 6. État final", "", f"**{state}**", ""]
    if t["blockers"]:
        out.append(f"- Blocage : {t['blockers']}")
    if t["deps"]:
        out.append(f"- Dépendances : {', '.join('#' + d for d in t['deps'].split(',') if d)}")
    out.append(f"- Prochaine action : {t['next_action'] or '-'}")
    detail = session_report_text(report).strip()
    out += ["", "## 7. Identification", "",
            f"- Run `{run_id}`, session `{r['session_id']}`, ticket #{n}.",
            f"- Fichier : `{report_file_name(r)}` (archive locale du superviseur, aussi jointe à l'email).",
            "- Un nouveau run du même ticket produit un nouveau rapport ; celui-ci n'est jamais réécrit."]
    if detail:
        out += ["", "## Annexe — rapport détaillé de la session", "", detail]
    return redact("\n".join(out)) + "\n"


def report_file_name(r):
    when = r["created_at"].replace("T", " ")
    return f"{when[:10]}_{when[11:13]}{when[14:16]}_lody_issue-{r['issue']}_run-{r['run_id']}.md"


def comment_body(md):
    """Commentaire GitHub = rapport complet ; au-delà de la limite, l'annexe est tronquée (signalé)."""
    body = f"{BOT_MARKER}\n{md}"
    if len(body) <= REPORT_COMMENT_MAX:
        return body
    note = "\n\n_(Rapport tronqué ici pour GitHub ; version intégrale jointe à l'email et archivée par le superviseur.)_\n"
    return body[:REPORT_COMMENT_MAX - len(note)] + note


def finalize_run(con, run_id):
    r = con.execute("SELECT * FROM run_reports WHERE run_id=?", (run_id,)).fetchone()
    t = task(con, r["issue"]) if r else None
    if not r or not t or r["state"] not in ("new", "awaiting_ci"):
        return
    md = render_run_report(con, r, t)
    folder = STATE_DIR / "reports" / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / report_file_name(r)
    path.write_text(md, encoding="utf-8")
    con.execute("UPDATE run_reports SET state='ready', file=?, finalized_at=? WHERE run_id=?", (str(path), iso(), run_id))
    con.commit()
    event(con, r["issue"], "report", f"Rapport du run {run_id} rédigé : {path.name}")
    link = publish_report(con, run_id)
    state = report_state(t, r["outcome"])
    summary = t["summary"] or str(json.loads(r["report"] or "{}").get("summary") or "(aucun résumé)")
    body = "\n".join([f"Projet : {REPORT_PROJECT}", f"Ticket #{r['issue']} — {t['title']}", f"État : {state}",
                      f"Run : {run_id} (session {r['session_id']})", "", "Résumé :", summary, "",
                      f"Rapport complet : {link or 'publication GitHub en attente, il apparaîtra dans le ticket'}",
                      f"Ticket : https://github.com/{REPO}/issues/{r['issue']}"]
                     + ([f"PR : {t['pr']}"] if t["pr"] else [])
                     + ["", "Le rapport complet est joint (Markdown).",
                        "Répondre dans le ticket GitHub : la réponse est détectée et le travail reprend automatiquement."])
    queue_notification(con, f"run:{run_id}", r["issue"], state,
                       f"[{REPORT_PROJECT}] #{r['issue']} — {state.split(' — ')[0]} — {t['title']}",
                       redact(body), path.name, md)


def gh_post_json(path, payload):
    try:
        out = subprocess.run([GH, "api", "-X", "POST", path, "--input", "-"], input=json.dumps(payload),
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout or "{}") if out.returncode == 0 else None
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None


def publish_report(con, run_id):
    """Commentaire du rapport sur le ticket, une seule fois par run : marqueur cherché sur GitHub
    avant publication (pas de doublon après un crash). Échec → nouvel essai, délai croissant."""
    r = con.execute("SELECT * FROM run_reports WHERE run_id=?", (run_id,)).fetchone()
    if not r or r["state"] != "ready" or r["comment_url"]:
        return r["comment_url"] if r else ""
    n, marker, url, error = r["issue"], REPORT_MARKER.format(run=run_id), "", ""
    comments = gh_list(f"repos/{REPO}/issues/{n}/comments")
    if comments is None:
        error = "commentaires du ticket illisibles"
    else:
        found = [c for c in comments if marker in (c.get("body") or "")]
        if found:
            url = found[0].get("html_url") or f"https://github.com/{REPO}/issues/{n}"
        else:
            try:
                md = Path(r["file"]).read_text(encoding="utf-8")
            except OSError:
                md = ""
            res = gh_post_json(f"repos/{REPO}/issues/{n}/comments", {"body": comment_body(md)}) if md else None
            if res is not None:
                url = res.get("html_url") or f"https://github.com/{REPO}/issues/{n}"
            else:
                error = "publication refusée ou GitHub injoignable" if md else "fichier du rapport introuvable"
    if url:
        con.execute("UPDATE run_reports SET comment_url=?, comment_error='' WHERE run_id=?", (url, run_id))
        con.commit()
        event(con, n, "report", f"Rapport du run {run_id} publié : {url}")
        return url
    attempts = r["comment_attempts"] + 1
    delay = min(6 * 3600, 120 * 2 ** attempts)
    con.execute("UPDATE run_reports SET comment_attempts=?, comment_next=?, comment_error=? WHERE run_id=?",
                (attempts, iso(datetime.fromtimestamp(time.time() + delay, timezone.utc)), error, run_id))
    con.commit()
    event(con, n, "report_error", f"Rapport du run {run_id} non publié ({error}), essai {attempts} : conservé localement.")
    return ""


def flush_reports(con):
    for r in con.execute("""SELECT run_id FROM run_reports WHERE state='ready' AND comment_url='' AND comment_attempts < 30
                            AND (comment_next='' OR comment_next <= ?) ORDER BY created_at""", (iso(),)).fetchall():
        publish_report(con, r["run_id"])


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
        if s["issue"] != TRIAGE:
            record_run(con, s["issue"], s["id"], "INTERRUPTED",
                       "Superviseur redémarré pendant la session : ticket repris en premier.", {})
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


def read_env_file(path):
    """Variables d'un fichier `NOM=valeur` (format EnvironmentFile / sh, guillemets shell)."""
    env = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, raw = line.split("=", 1)
            words = shlex.split(raw) if raw else [""]
            env[key.strip()] = words[0] if words else ""
    return env


def reexec(con):
    """Recharge le code installé dans le même processus (même PID pour systemd). Appelé seulement
    entre deux sessions ; le verrou est libéré par l'exec (descripteur non hérité) puis repris.
    La configuration (LQ_ENV_FILE) est relue aussi : sans cela, une variable ajoutée ou modifiée
    dans lodyorch.env n'était prise en compte qu'au redémarrage du service."""
    ctl_set(con, "reload_requested", "")
    env_file = os.environ.get("LQ_ENV_FILE", "")
    if env_file:
        try:
            os.environ.update(read_env_file(env_file))
        except (OSError, ValueError) as exc:
            event(con, None, "supervisor", f"Configuration non relue ({type(exc).__name__}) : valeurs actuelles conservées.")
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
        if CI_WATCH:
            try:
                watch_ci(con)
            except Exception as exc:  # noqa: BLE001 — un suivi de CI raté ne doit jamais arrêter la file
                event(con, None, "ci_error", f"{type(exc).__name__}: {exc}"[:500])
            try:
                watch_merges(con)
            except Exception as exc:  # noqa: BLE001 — idem pour la fusion automatique
                event(con, None, "merge_error", f"{type(exc).__name__}: {exc}"[:500])
        if RUN_REPORTS:
            flush_reports(con)
        if NOTIFY_SMTP_ENV:
            flush_notifications(con)
        blocker = start_blocker(con)
        if blocker:
            reason, until = blocker
            ctl_set(con, "idle_reason", reason + (f" Reprise au plus tôt : {until}." if until else ""))
            pause_for(con, POLL_SECONDS)
            continue
        t = next_task(con)
        prepare = None
        if t is None:
            found = next_preparation(con)
            if found:
                t, prepare = found
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
            ov = queue_overview(con)
            ctl_set(con, "idle_reason", (f"En attente : #{later['issue']} reprend après {later['not_before']}. " if later
                                         else "File à l'arrêt, aucune session lancée : ") + overview_text(ov))
            try:
                notify_idle(con, ov)
            except Exception as exc:  # noqa: BLE001 — un email raté ne doit jamais arrêter la file
                event(con, None, "notify", f"Email de file à l'arrêt non mis en file : {type(exc).__name__}: {exc}"[:500])
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
        run_session(con, t, prepare)
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
    print(f"File : {overview_text(queue_overview(con))}")
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
    o["overview"] = ov = queue_overview(con)
    o["prepare_next"] = (task(con, ov["preparable"][0]) if not o["next"] and ov["preparable"] and ov["prep_cap"]
                         and ov["prep_used"] < ov["prep_cap"] else None)

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
    elif cur and human_state(con)[0] == "busy":
        state, why, cat = "PAUSE EN COURS", (f"le propriétaire parle dans la console : la session sur #{cur['issue']} "
                                             "range son travail puis s'arrête (ticket repris ensuite)."), ""
    elif cur:
        state, why, cat = "ACTIF", f"session Claude en cours sur #{cur['issue']}.", ""
    elif blocker and blocker[0].startswith("Priorité humaine"):
        state, why, cat = "CONSOLE", blocker[0], "priorité humaine"
    elif blocker and blocker[0].startswith(LIMIT_KINDS):
        state, why, cat = "LIMITÉ", blocker[0], "attente de quota"
    elif mode != "RUNNING":
        state, why, cat = "BLOQUÉ", f"mode {mode} — " + (ctl_get(con, "pause_reason") or f"{CLI_NAME} resume pour reprendre."), \
            "en pause"
    elif o["prepare_next"]:
        state, why, cat = "AU REPOS", ("rien d'exécutable, préparation au prochain tour — " + overview_text(ov)), \
            "repos normal"
    elif not o["next"] and not ov["delayed"] and (blocked or o["waiting"]):
        state, why, cat = "BLOQUÉ", ("en attente du propriétaire, aucune session lancée — " + overview_text(ov)), \
            "attente de validation"
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
    elif o["prepare_next"]:
        ticket = f"préparation : #{o['prepare_next']['issue']} {ptitle(o['prepare_next'])[:48]}"
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
        prs = [t for t in con.execute("SELECT issue, pr, ci_state FROM tasks WHERE status='WAITING_OWNER' ORDER BY issue")]
        mark = {"pending": " CI ⏳", "success": " CI ✓", "failed": " CI ✗"}
        dep = f"  Phase : {ctl_get(con, 'phase') or '—'} · PR en attente de revue : " \
            + (", ".join(f"#{t['issue']}→{pr_ref(t['pr'])}{mark.get(t['ci_state'], '')}" for t in prs) or "aucune")
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
        print(f"  {console_state()}")
    human, human_at, _ = human_state(con)
    print("  console : " + {"busy": "le propriétaire a la main (demande en cours)",
                             "quiet": "conversation récente, file en attente du délai de calme",
                             "idle": "libre, la file peut travailler"}[human]
          + (f" — dernier échange {localtime(human_at)}" if human_at else " — aucun échange signalé (crochets installés ?)")
          + (f" ; dernière consultation {localtime(ctl_get(con, 'human_consult_at'))} (sans pause)"
             if ctl_get(con, "human_consult_at") else ""))

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
              f"SHA {(t['commit_sha'] or '?')[:10]}, {t['pr'] or 'PR ?'}"
              + (f" — {CI_TEXT.get(t['ci_state'], t['ci_state'])}" if t["ci_state"] else ""))
        if t["ci_state"] in ("pending", "failed") and t["ci_detail"]:
            print(f"        CI : {t['ci_detail'][:150]}")
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
    ov = o["overview"]
    if o["prepare_next"]:
        print(f"Prochain ticket à préparer (#165) : #{o['prepare_next']['issue']} {ptitle(o['prepare_next'])[:60]} "
              f"— préparations du jour {ov['prep_used']}/{ov['prep_cap']}")
    print(f"Synthèse de la file : {overview_text(ov)}")
    later = [t for t in queue_rows if t is not nxt]
    if later:
        print("Ensuite : " + ", ".join(f"#{t['issue']} P{t['priority'] // 10}" for t in later[:12])
              + (f" … (+{len(later) - 12})" if len(later) > 12 else ""))
    odd = con.execute("""SELECT issue, label_issues FROM tasks WHERE COALESCE(label_issues,'') != ''
                         AND status NOT IN ('DONE') ORDER BY issue""").fetchall()
    if odd:
        print("Labels incohérents (règle appliquée entre parenthèses) :")
        for r in odd:
            print(f"  #{r['issue']:<4} {r['label_issues'][:150]}")
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
        print(f"#{t['issue']:<4} {run_state(t):<17} p{t['priority']:<3} {t['title'][:60]:<60}{extra}"
              f"{'  deps ' + t['deps'] if t['deps'] else ''}")
        if t["next_action"]:
            print(f"       → {t['next_action'][:150]}")
        if t["label_issues"]:
            print(f"       ⚠ labels : {t['label_issues'][:150]}")


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
    if GH_SYNC and args.issue != TRIAGE and not set_status_label(con, args.issue, "BLOCKED"):
        print("Label GitHub non posé maintenant : il le sera à la prochaine synchronisation.")
    print(f"#{args.issue} → BLOCKED" + (f" (label {STATUS_LABELS['BLOCKED']} : le retirer sur GitHub remet en file)"
                                         if GH_SYNC else ""))


def cmd_add(con, args):
    """Remise en file explicite (déblocage, repriorisation). Facultative depuis #139 : les tickets
    `agent:auto` entrent seuls dans la file à la synchronisation. Sans --prio, la priorité GitHub
    (labels priority:P0…P3) est conservée ; avec la synchronisation, le label de blocage est retiré."""
    t = task(con, args.issue)
    prio = args.prio
    if t:
        prio = t["priority"] if prio is None else prio
        update_task(con, args.issue, status="READY", attempts=0, not_before="", priority=prio,
                    deps=args.deps if args.deps is not None else t["deps"],
                    note=args.note if args.note is not None else t["note"])
    else:
        data = gh_json(f"repos/{REPO}/issues/{args.issue}") or {}
        if prio is None:
            prio = priority_level([lbl.get("name", "") for lbl in data.get("labels", [])]) * 10 if data else 50
        con.execute("INSERT INTO tasks(issue,title,priority,deps,note,updated_at) VALUES(?,?,?,?,?,?)",
                    (args.issue, data.get("title", ""), prio, args.deps or "", args.note or "", iso()))
        con.commit()
    event(con, args.issue, "control", "add")
    if GH_SYNC and args.issue != TRIAGE:
        set_status_label(con, args.issue, "READY")
    print(f"#{args.issue} → READY (priorité {prio})")


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
    if args.send:
        # Unité lodyorch-notify : seule à lire le fichier SMTP ; n'affiche que des compteurs.
        if not NOTIFY_SMTP_ENV or not os.access(NOTIFY_SMTP_ENV, os.R_OK):
            print("Fichier SMTP illisible ou non configuré (LQ_NOTIFY_SMTP_ENV) : rien envoyé.")
            return 2
        flush_notifications(con)
        left = con.execute("SELECT COUNT(*) FROM notifications WHERE sent_at IS NULL AND attempts < 30").fetchone()[0]
        print(f"Emails en attente après envoi : {left}.")
        return 0
    if args.retry:
        con.execute("UPDATE notifications SET next_try='' WHERE sent_at IS NULL")
        con.commit()
        print("Nouvel essai demandé pour les emails en attente (au prochain tour du superviseur).")
    for nf in con.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 20").fetchall():
        state = f"envoyé {nf['sent_at']}" if nf["sent_at"] else f"EN ATTENTE ({nf['attempts']} essai(s), {nf['last_error'] or '-'})"
        print(f"{nf['created_at']} {nf['subject'][:60]:<60} {state}"
              + (f"  + {nf['attachment_name']}" if nf["attachment_name"] else ""))
    return 0


def cmd_report(con, args):
    """Rapport de la dernière session du ticket : rédigé s'il manque (session antérieure à #123,
    rapport perdu), publié s'il attend GitHub ; jamais réécrit."""
    s = con.execute("SELECT * FROM sessions WHERE issue=? AND ended_at IS NOT NULL ORDER BY started_at DESC LIMIT 1",
                    (args.issue,)).fetchone()
    if not s:
        print(f"#{args.issue} : aucune session terminée.")
        return 2
    if not RUN_REPORTS:
        print("Rapports désactivés (LQ_RUN_REPORTS=1 pour les activer).")
        return 2
    report = {}
    status_file = LOG_DIR / f"{s['id']}.status.json"
    if status_file.is_file():
        try:
            report = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
    record_run(con, args.issue, s["id"], s["outcome"] or "COMPLETED", s["detail"] or "", report)
    r = con.execute("SELECT * FROM run_reports WHERE run_id=?", (s["id"][:8],)).fetchone()
    if not r:
        print(f"#{args.issue} : la session {s['id'][:8]} ({s['outcome']}) n'est pas une tentative, pas de rapport.")
        return 0
    if r["state"] == "awaiting_ci":
        print(f"#{args.issue} run {r['run_id']} : rapport rédigé au verdict de la CI (suivie par le superviseur).")
        return 0
    url = publish_report(con, r["run_id"])
    r = con.execute("SELECT * FROM run_reports WHERE run_id=?", (r["run_id"],)).fetchone()
    print(f"#{args.issue} run {r['run_id']} : {Path(r['file']).name if r['file'] else '?'} — "
          + (url or f"publication GitHub en attente ({r['comment_error'] or 'nouvel essai automatique'})"))
    return 0


def cmd_reports(con, _):
    rows = con.execute("SELECT * FROM run_reports ORDER BY created_at DESC LIMIT 20").fetchall()
    if not rows:
        print("Aucun rapport de tentative.")
    for r in rows:
        nf = con.execute("SELECT sent_at, attempts FROM notifications WHERE key=?", (f"run:{r['run_id']}",)).fetchone()
        mail = ("email envoyé" if nf and nf["sent_at"] else f"email en attente ({nf['attempts']} essai(s))" if nf
                else "email non configuré" if not NOTIFY_SMTP_ENV else "email pas encore en file")
        where = r["comment_url"] or ("en attente de la CI" if r["state"] == "awaiting_ci"
                                     else f"GitHub en attente : {r['comment_error'] or '-'}")
        print(f"{r['created_at']} #{r['issue']:<5} run {r['run_id']} {r['outcome']:<12} {where} · {mail}")
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


def cmd_automerge(con, args):
    """Mode de la fusion automatique (#200) et derniers verdicts. `off` = arrêt immédiat (kill switch)."""
    if args.mode:
        previous = ctl_get(con, "automerge.mode", "") or "dry-run"
        ctl_set(con, "automerge.mode", args.mode)
        if args.mode == "on" and previous != "on":
            # Toute autorisation `merge:auto` antérieure à cet instant est ignorée : rien de rétroactif.
            ctl_set(con, "automerge.enabled_at", iso())
        if args.mode != "on":
            ctl_set(con, "automerge.enabled_at", "")
        event(con, None, "control", f"automerge {args.mode}")
    mode, why = auto_merge_mode(con)
    print(f"Fusion automatique : {mode} ({why})")
    if ctl_get(con, "automerge.enabled_at"):
        print(f"Activée le {ctl_get(con, 'automerge.enabled_at')} : seules les autorisations `merge:auto` "
              "posées après comptent.")
    if args.mode == "on" and mode != "on":
        print("Plafond LQ_AUTO_MERGE plus bas dans lodyorch.env : le mode reste limité (voir docs/lody-fusion-auto.md).")
    rows = con.execute("SELECT * FROM merge_audit ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
    for r in rows:
        refusals = [x["code"] for x in json.loads(r["refusals"] or "[]")]
        print(f"  {local(r['at'])}  #{r['issue']:<4} PR #{r['pr']:<4} {r['head_sha'][:8]} [{r['mode']}] "
              + ("éligible" if r["eligible"] else "refusée : " + ", ".join(refusals))[:160])
    for op in con.execute("SELECT * FROM merge_ops ORDER BY started_at DESC LIMIT 5").fetchall():
        print(f"  fusion PR #{op['pr']} {op['head_sha'][:8]} : {op['state']}"
              + (f" → {op['merge_sha'][:10]}" if op["merge_sha"] else "") + (f" ({op['error'][:100]})" if op["error"] else ""))
    return 0


def cmd_labels(con, args):
    """Compare les labels du dépôt au référentiel (labels.json). Lecture seule, sauf --apply qui
    crée les labels manquants et aligne couleur/description (jamais de suppression)."""
    try:
        wanted = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Référentiel illisible ({LABELS_FILE}) : {type(exc).__name__}")
        return 2
    current = gh_list(f"repos/{REPO}/labels")
    if current is None:
        print("GitHub injoignable.")
        return 2
    have = {lbl["name"]: lbl for lbl in current}
    todo = 0
    for name, spec in wanted.items():
        lbl = have.get(name)
        if lbl and (lbl.get("color", "").lower(), lbl.get("description") or "") == (spec["color"].lower(), spec["description"]):
            print(f"  ✓ {name}")
            continue
        action, ok = ("à mettre à jour" if lbl else "manquant"), False
        if args.apply:
            path = f"repos/{REPO}/labels" + (f"/{urllib.parse.quote(name)}" if lbl else "")
            ok = gh_call("-X", "PATCH" if lbl else "POST", path, *([] if lbl else ["-f", f"name={name}"]),
                         "-f", f"color={spec['color']}", "-f", f"description={spec['description']}")
            action = ("mis à jour" if lbl else "créé") if ok else f"ÉCHEC ({action})"
            event(con, None, "labels", f"{name} : {action}")
        todo += not ok
        print(f"  {'✓' if ok else '✗'} {name} — {action}")
    print("Référentiel complet." if not todo else
          f"{todo} label(s) à corriger" + ("" if args.apply else f" : {CLI_NAME} labels --apply"))
    return 1 if todo else 0
CONSOLE_DIR = Path(os.environ.get("LQ_CONSOLE_DIR", "/srv/moneyprinterturbo"))
CONSOLE_SETTINGS = CONSOLE_DIR / ".claude" / "settings.local.json"
CONSOLE_CLAUDE = os.environ.get("LQ_CONSOLE_CLAUDE", "claude --dangerously-skip-permissions")


def console_state():
    """La console = la session tmux interactive du propriétaire (jamais créée par le service)."""
    rc, out = ro_run(["tmux", "list-panes", "-s", "-t", f"={TMUX_SESSION}", "-F", "#{pane_current_command}"])
    if rc is None:
        return f"console tmux {TMUX_SESSION} : tmux indisponible"
    if rc != 0:
        return f"console tmux {TMUX_SESSION} : absente — {CLI_NAME} console pour l'ouvrir"
    cmds = out.split()
    return (f"console tmux {TMUX_SESSION} : présente, Claude interactif "
            + ("actif" if any(c == "claude" or c.startswith("claude") for c in cmds) else "NON lancé")
            + f" (tmux attach -t {TMUX_SESSION})")


def cmd_console(con, _):
    """Ouvre LA console : rattache la session tmux existante, ou la crée avec Claude interactif dans
    le dépôt. Ne renomme, ne remplace et ne tue jamais une session existante."""
    exists = ro_run(["tmux", "has-session", "-t", f"={TMUX_SESSION}"])[0] == 0
    if not exists:
        subprocess.run(["tmux", "new-session", "-d", "-s", TMUX_SESSION, "-c", str(CONSOLE_DIR)], check=True)
        subprocess.run(["tmux", "send-keys", "-t", f"={TMUX_SESSION}", CONSOLE_CLAUDE, "Enter"], check=True)
        print(f"Console {TMUX_SESSION} créée ({CONSOLE_DIR}, {CONSOLE_CLAUDE}).")
    if os.environ.get("TMUX"):
        print(f"Déjà dans tmux : tmux switch-client -t {TMUX_SESSION}")
        return 0
    os.execvp("tmux", ["tmux", "attach", "-t", f"={TMUX_SESSION}"])


def hook_prompt():
    """Message du propriétaire transmis par le crochet UserPromptSubmit (JSON sur l'entrée standard,
    champ `prompt`). None si rien de lisible (appel manuel, terminal, JSON invalide)."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        raw = sys.stdin.read(200_000)
        data = json.loads(raw) if raw.strip() else {}
        return data.get("prompt") if isinstance(data, dict) and isinstance(data.get("prompt"), str) else None
    except (OSError, ValueError):
        return None


def cmd_human(con, args):
    """Appelé par les crochets de la console : `busy` à chaque demande du propriétaire
    (UserPromptSubmit), `idle` quand Claude a répondu (Stop). Ne bloque jamais la console : toute
    erreur est ignorée. Sur `busy`, une ligne de contexte signale à Claude la session de file en cours.

    #139 : une simple CONSULTATION (`lodyorch status`, `queue`, « où en est-on ? »…) n'est pas une
    prise de main : ni priorité humaine, ni délai de calme, ni rangement de la session de file. Le
    tour est noté « consult » pour que le `idle` qui suit ne relance pas non plus le délai de calme.
    Toute autre demande (ou un message illisible) garde la priorité humaine : choix prudent."""
    try:
        if args.state == "busy":
            prompt = hook_prompt()
            if prompt is not None and is_consultation(prompt):
                ctl_set(con, "human_turn", "consult")
                ctl_set(con, "human_consult_at", iso())
                cur = con.execute("SELECT issue FROM sessions WHERE ended_at IS NULL LIMIT 1").fetchone()
                print("[lodyorch] Consultation en lecture seule : la file continue sans pause"
                      + (f" (session en cours sur #{cur['issue']}, non interrompue)" if cur else "")
                      + f". Réponds avec `{CLI_NAME} status` (ou queue / log / sessions), sans rien modifier.")
                return 0
            ctl_set(con, "human_turn", "work")
            ctl_set(con, "human_state", "busy")
            ctl_set(con, "human_at", iso())
            cur = con.execute("SELECT issue FROM sessions WHERE ended_at IS NULL LIMIT 1").fetchone()
            if cur:
                print(f"[lodyorch] Une session de file travaille sur #{cur['issue']} : elle range son travail et "
                      f"s'arrête pour te laisser la main ; elle reprendra après la conversation.")
            return 0
        if ctl_get(con, "human_turn") == "consult":
            ctl_set(con, "human_turn", "")  # fin d'une consultation : rien n'a changé
            return 0
        ctl_set(con, "human_state", "quiet")
        ctl_set(con, "human_at", iso())
    except sqlite3.Error:
        pass
    return 0


def cmd_statusline(con, _):
    """Ligne d'état de la console Claude (réglage statusLine) : file, ticket, phase, PR."""
    try:
        cur = con.execute("SELECT issue FROM sessions WHERE ended_at IS NULL LIMIT 1").fetchone()
        mode = ctl_get(con, "mode", "RUNNING")
        human = human_state(con)[0]
        waiting = con.execute("SELECT COUNT(*) c FROM tasks WHERE status='WAITING_OWNER' AND ci_state!='pending'").fetchone()["c"]
        ci = con.execute("SELECT COUNT(*) c FROM tasks WHERE status='WAITING_OWNER' AND ci_state='pending'").fetchone()["c"]
        ready = con.execute("SELECT COUNT(*) c FROM tasks WHERE status IN ('READY','RUNNING')").fetchone()["c"]
        if cur:
            what = f"#{cur['issue']} {ctl_get(con, 'phase')[:40]}"
        elif mode != "RUNNING":
            what = f"file {mode.lower()}"
        elif human != "idle":
            what = "file en attente (console)"
        else:
            what = "file au repos"
        print(f"lodyorch · {what} · {ready} à faire · " + (f"{ci} CI en cours · " if ci else "") + f"{waiting} PR en revue")
    except sqlite3.Error:
        print("lodyorch · état indisponible")
    return 0


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
        print(f"  · {console_state()}")
    if RUN_REPORTS:
        print(f"  · rapports de tentative : actifs ({STATE_DIR / 'reports' / 'runs'})")
    if NOTIFY_SMTP_ENV:
        if os.access(NOTIFY_SMTP_ENV, os.R_OK):
            try:
                smtp_settings()
                line(True, "configuration SMTP réutilisée (valeurs jamais affichées)", NOTIFY_SMTP_ENV)
            except (OSError, ValueError) as exc:
                line(False, "configuration SMTP", str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        else:
            line(False, "configuration SMTP lisible par le compte de service", NOTIFY_SMTP_ENV)
        _, state = ro_run(["systemctl", "is-active", f"{SERVICE}-notify.timer"])
        print(f"  · envoi des emails : {SERVICE}-notify.timer {state or 'absent'}"
              + ("" if state == "active" else f" (sudo systemctl enable --now {SERVICE}-notify.timer)"))
    hooks = CONSOLE_SETTINGS.read_text(encoding="utf-8") if CONSOLE_SETTINGS.is_file() else ""
    line(f"{CLI_NAME} human busy" in hooks and f"{CLI_NAME} human idle" in hooks,
         "crochets de la console (priorité humaine)", str(CONSOLE_SETTINGS))
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
    s.add_argument("--prio", type=int, default=None)
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
    s = sub.add_parser("labels")
    s.add_argument("--apply", action="store_true")
    s = sub.add_parser("automerge")
    s.add_argument("mode", nargs="?", choices=MERGE_MODES)
    s.add_argument("--limit", type=int, default=10)
    sub.add_parser("console")
    sub.add_parser("statusline")
    s = sub.add_parser("human")
    s.add_argument("state", choices=("busy", "idle"))
    s = sub.add_parser("phase")
    s.add_argument("text", nargs="+")
    s = sub.add_parser("notifications")
    s.add_argument("--retry", action="store_true")
    s.add_argument("--send", action="store_true")
    s = sub.add_parser("report")
    s.add_argument("issue", type=int)
    sub.add_parser("reports")
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
    con = status_db() if args.cmd in ("status", "pulse", "statusline") and DB_PATH.exists() else db()
    if args.cmd == "run":
        sys.exit(supervise())
    if args.cmd == "config" and args.action != "show" and (not args.name or (args.action == "set") != bool(args.value)):
        p.error("usage : lodyorch config [show | set NOM VALEUR | unset NOM]")
    code = {"status": cmd_status_tickets if GH_SYNC else cmd_status, "pulse": cmd_pulse, "queue": cmd_queue, "pause": cmd_mode, "resume": cmd_mode, "stop": cmd_mode,
            "skip": cmd_skip, "add": cmd_add, "set": cmd_set, "log": cmd_log, "sessions": cmd_sessions,
            "config": cmd_config, "reload": cmd_reload, "sync": cmd_sync,
            "notifications": cmd_notifications, "notify": cmd_notify, "phase": cmd_phase, "labels": cmd_labels,
            "automerge": cmd_automerge,
            "report": cmd_report, "reports": cmd_reports,
            "console": cmd_console, "human": cmd_human, "statusline": cmd_statusline,
            "check": cmd_check}[args.cmd](con, args)
    sys.exit(code or 0)


if __name__ == "__main__":
    main()
