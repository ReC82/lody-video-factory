#!/usr/bin/env python3
"""Crochet PreToolUse des sessions LODYORCH (ticket #116) : refuse les actions hors du périmètre
d'une session de développement, AVANT leur exécution, même en `--dangerously-skip-permissions`.

Lit l'appel d'outil (JSON sur l'entrée standard). Code 0 = autorisé ; code 2 = refusé, la raison
(sur la sortie d'erreur) est rendue à Claude, qui doit changer d'approche. En cas de doute (entrée
illisible), l'appel est refusé.

Ce crochet est un garde-fou contre les erreurs, pas un bac à sable : le compte de service garde
ses droits Unix. Les règles de fond sont dans session_prompt.md ; l'isolation forte (compte dédié
sans sudo ni docker) relève du propriétaire de l'hôte, voir docs/lodyorch.md.
"""

import json
import os
import re
import sys
from pathlib import Path

HOME = str(Path.home())
WORKDIR = os.environ.get("LQ_WORKDIR", f"{HOME}/lody-orch/worktrees")
STATE_DIR = os.environ.get("LQ_STATE_DIR", f"{HOME}/.local/state/lodyorch")
# Écriture permise : worktrees, rapports, /tmp, et le seul fichier de statut de la session en cours.
WRITE_ROOTS = [WORKDIR, f"{STATE_DIR}/reports", "/tmp/"] + [
    x for x in [os.environ.get("LQ_STATUS_FILE", "")] + os.environ.get("LQ_GUARD_WRITE_ROOTS", "").split(",") if x]

# Chemins jamais lus ni écrits : arbre de production Lody (config.toml, secrets, volumes), autres
# projets et superviseurs, identifiants Claude / GitHub / SMTP, configuration système.
# STRICT : motifs en forme de chemin, cherchés partout (même entre guillemets).
PROTECTED_STRICT = [
    (r"/srv/", "arbre du propriétaire et autres projets (/srv/…) : travailler dans le worktree du ticket"),
    (rf"{re.escape(HOME)}/(Dig|LodyLand)\b", "autre projet de l'hôte"),
    (r"/opt/(?!lodyorch\b)", "autre logiciel ou superviseur installé (/opt/…)"),
    (r"/var/lib/docker|lody-video-factory-data", "volumes Docker de production"),
    (r"\.credentials\.json|\.claude\.json\b|\.config/gh\b|hosts\.yml|\.git-credentials|\.netrc|\.ssh/",
     "identifiants"),
    (r"\.local/state/(?!lodyorch\b)", "état d'un autre superviseur"),
]
# LOOSE : noms qu'une prose peut citer (message de commit…) ; cherchés dans les chemins des outils
# fichiers et, pour Bash, hors texte entre guillemets et hors document « here ».
PROTECTED_LOOSE = [
    (r"/etc/", "configuration système"),
    (r"config\.toml(?!\.example)", "configuration du moteur (contient des clés d'API)"),
    (r"(^|[\s/=])\.env(\b|$)|smtp\.env|openai\.env|/secrets(/|\b)|lody-secrets|id_rsa|id_ed25519",
     "fichier de secrets"),
]

# Commandes interdites dans une session (motif sur la commande Bash complète).
FORBIDDEN_COMMANDS = [
    (r"(^|[\s;&|(`])(sudo|su|doas|pkexec)\s", "élévation de privilèges"),
    (r"(^|[\s;&|(`])(docker|podman|ctr|nerdctl|kubectl)(\s|$)", "Docker et conteneurs : services de production"),
    (r"(^|[\s;&|(`])(systemctl|service|crontab|shutdown|reboot|halt|poweroff|loginctl)(\s|$)",
     "services et planification système"),
    (r"(^|[\s;&|(`])(pkill|killall)\s", "arrêt de processus par nom (peut viser d'autres projets)"),
    (r"(^|[\s;&|(`])tmux(\s|$)", "tmux : sessions des autres projets et console de l'orchestrateur"),
    (r"(^|[\s;&|(`])(mkfs\S*|fdisk|parted|dd)\s", "opération disque"),
    (r"rm\s+(-\w*\s+)*(-\w*[rR]\w*\s+)(-\w*\s+)*(/|~|\$HOME|\*)(\s|$)", "suppression récursive de la racine"),
    (r"\bgh\s+pr\s+merge\b|pulls/\d+/merge\b|/merges\b|\bgh\s+pr\s+review\b[^\n]*--approve",
     "fusion / approbation : réservée au propriétaire"),
    (r"\bgh\s+(issue|pr)\s+close\b|\bgh\s+issue\s+(delete|transfer|lock)\b", "fermeture : réservée au propriétaire"),
    (r"\bgh\s+(issue|pr)\s+edit\b[^\n]*--(add|remove)-label|/labels\b", "labels : gérés par le superviseur"),
    (r"\bgh\s+(secret|variable|ssh-key|gpg-key|release|repo\s+(delete|edit|rename|archive))\b",
     "réglages du dépôt"),
    (r"\bgh\s+auth\s+(token|login|logout|refresh|setup-git)\b|--show-token|\bclaude\s+(setup-token|auth\s+log)",
     "identifiants"),
    (r"\bgit\s+push\b[^\n]*(\s--force\b|\s-f\b|\s--force-with-lease\b|\s--mirror\b|\s--delete\b|\s-d\s|\s\+)",
     "push forcé ou destructif"),
    (r"\bgit\s+push\b[^\n]*(\s|:)(refs/heads/)?(main|master)(\s|$)", "push sur main : uniquement par PR fusionnée par le propriétaire"),
    (r"\bgit\s+(config\s+--global|filter-branch|filter-repo)\b", "configuration globale ou réécriture d'historique"),
    (r"\blodyorch\s+(?!(status|pulse|queue|log|sessions|phase|check)\b)\S+", "pilotage du superviseur : réservé au propriétaire"),
    (r"(^|[\s;&|(`])(printenv|env)\s*($|[|;&>])|/proc/\d+/environ", "dump de l'environnement"),
]

STRONG_SECRETS = re.compile(
    r"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_\-]{10,}|(?<![A-Za-z0-9])sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{20,}"
    r"|(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}\b"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")

# Outils jamais utiles à une session de code : connecteurs externes (mails, documents…), tâches
# planifiées, notifications, publication.
FORBIDDEN_TOOLS = re.compile(r"^(mcp__.*|RemoteTrigger|CronCreate|CronDelete|PushNotification|Artifact\w*|SendFeedback)$")


def deny(reason):
    print(f"LODYORCH — action refusée par le crochet de garde : {reason}. "
          "Reste dans le worktree du ticket et dans les règles de la session.", file=sys.stderr)
    sys.exit(2)


def strip_quoted(cmd):
    """Commande sans documents « here » ni texte entre guillemets (messages, corps de PR…)."""
    cmd = re.sub(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2(\n|$)", " ", cmd, flags=re.S)
    cmd = re.sub(r"'[^']*'", "''", cmd)
    return re.sub(r'"(\\.|[^"\\])*"', '""', cmd)


def check_path(text, loose=None):
    for pat, why in PROTECTED_STRICT:
        if re.search(pat, text):
            deny(f"{why} ({pat})")
    for pat, why in PROTECTED_LOOSE:
        if re.search(pat, text if loose is None else loose):
            deny(f"{why} ({pat})")


def main():
    try:
        call = json.load(sys.stdin)
        tool = call.get("tool_name") or ""
        inp = call.get("tool_input") or {}
    except (ValueError, AttributeError):
        deny("appel illisible")
    if FORBIDDEN_TOOLS.match(tool):
        deny(f"outil {tool} hors périmètre")
    if tool == "Bash":
        cmd = str(inp.get("command") or "")
        if STRONG_SECRETS.search(cmd):
            deny("la commande contient un motif de secret")
        bare = strip_quoted(cmd)
        for pat, why in FORBIDDEN_COMMANDS:
            if re.search(pat, bare):
                deny(why)
        check_path(cmd, loose=bare)
        return
    for key in ("file_path", "path", "notebook_path"):
        if inp.get(key):
            check_path(os.path.realpath(os.path.expanduser(str(inp[key]))))
    if tool == "Glob" and inp.get("pattern"):
        check_path(str(inp["pattern"]), loose="")  # motif : seuls les chemins stricts comptent
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = os.path.realpath(os.path.expanduser(str(inp.get("file_path") or inp.get("notebook_path") or "")))
        if not any(target == r.rstrip("/") or target.startswith(r.rstrip("/") + "/") for r in WRITE_ROOTS):
            deny(f"écriture hors du worktree et des rapports ({target})")
        if STRONG_SECRETS.search(str(inp.get("content") or "") + str(inp.get("new_string") or "")):
            deny("le contenu écrit contient un motif de secret")


if __name__ == "__main__":
    main()
