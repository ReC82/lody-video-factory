#!/bin/sh
# Installation / mise à jour de LODYORCH sur l'hôte, par le propriétaire (voir docs/lodyorch.md).
#
#   sudo deploy/lodyorch/install.sh            installe ou met à jour (sans démarrer)
#   sudo deploy/lodyorch/install.sh --dry-run  affiche les actions, ne change rien
#
# Ne démarre, n'active ni ne redémarre AUCUN service, ne crée ni ne renomme aucune session tmux,
# et ne touche à aucun autre superviseur (digorch, jury-central, lodyquests). Dans le dépôt de la
# console (/srv/moneyprinterturbo), seul .claude/settings.local.json reçoit les crochets LODYORCH
# (ajoutés, jamais remplacés ; fichier exclu de Git). Démarrage : voir la fin.
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
DEST=/opt/lodyorch
SVC_USER=${LODYORCH_USER:-ubuntu}
SVC_HOME=$(getent passwd "$SVC_USER" | cut -d: -f6)
BASE="$SVC_HOME/lody-orch"
STATE="$SVC_HOME/.local/state/lodyorch"
REPO_URL=https://github.com/ReC82/lody-video-factory.git
CONSOLE_DIR=${LODYORCH_CONSOLE_DIR:-/srv/moneyprinterturbo}
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

run() { if [ "$DRY" = 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }
as_user() { run runuser -u "$SVC_USER" -- "$@"; }

if [ "$DRY" = 0 ] && [ "$(id -u)" != 0 ]; then
    echo "À lancer avec sudo (écrit dans /opt, /usr/local/bin et /etc/systemd/system)." >&2
    exit 1
fi
[ -n "$SVC_HOME" ] || { echo "Compte $SVC_USER introuvable." >&2; exit 1; }
for f in lodyorch.py guard.py session_prompt.md lodyorch.env claude-settings.json labels.json lodyorch \
         console-settings.json console_settings.py systemd/lodyorch.service \
         systemd/lodyorch-notify.service systemd/lodyorch-notify.timer; do
    [ -f "$SRC/$f" ] || { echo "Fichier manquant : $SRC/$f" >&2; exit 1; }
done

echo "1. Sauvegarde de l'installation précédente (si présente)"
if [ -d "$DEST" ]; then
    BK="$SVC_HOME/backups/lodyorch/config-$(date +%Y%m%d-%H%M%S)"
    as_user mkdir -p "$BK"
    run cp -a "$DEST/." "$BK/"
    for u in lodyorch.service lodyorch-notify.service lodyorch-notify.timer; do
        [ -f "/etc/systemd/system/$u" ] && run cp -a "/etc/systemd/system/$u" "$BK/"
    done
    run chown -R "$SVC_USER:$SVC_USER" "$BK"
    echo "   → $BK"
fi

echo "2. Code et configuration dans $DEST (propriété root : non modifiables par les sessions)"
run install -d -o root -g root -m 0755 "$DEST"
for f in lodyorch.py guard.py console_settings.py; do
    run install -o root -g root -m 0755 "$SRC/$f" "$DEST/$f"
done
for f in session_prompt.md lodyorch.env claude-settings.json labels.json console-settings.json; do
    run install -o root -g root -m 0644 "$SRC/$f" "$DEST/$f"
done
run install -o root -g root -m 0755 "$SRC/lodyorch" /usr/local/bin/lodyorch

echo "3. Dossiers du compte $SVC_USER : clone dédié, worktrees, état privé"
as_user mkdir -p "$BASE/worktrees" "$STATE/reports"
run chmod 0700 "$STATE"
if [ ! -d "$BASE/repo/.git" ]; then
    as_user git clone --quiet "$REPO_URL" "$BASE/repo"
else
    as_user git -C "$BASE/repo" fetch --quiet --prune origin
fi

echo "4. Unités systemd : superviseur et envoi des emails (installées, NI activées NI démarrées ; aucune fenêtre tmux)"
for u in lodyorch.service lodyorch-notify.service lodyorch-notify.timer; do
    run install -o root -g root -m 0644 "$SRC/systemd/$u" "/etc/systemd/system/$u"
done
run systemctl daemon-reload

echo "5. Console tmux « lodyorch » = la session Claude interactive existante : crochets de priorité humaine"
if [ -d "$CONSOLE_DIR/.git" ]; then
    if [ "$DRY" = 1 ]; then
        runuser -u "$SVC_USER" -- /usr/bin/python3 "$SRC/console_settings.py" "$CONSOLE_DIR" "$SRC/console-settings.json" --dry-run 2>/dev/null \
            || echo "  [dry-run] fusion des crochets dans $CONSOLE_DIR/.claude/settings.local.json"
    else
        runuser -u "$SVC_USER" -- /usr/bin/python3 "$DEST/console_settings.py" "$CONSOLE_DIR" "$DEST/console-settings.json"
    fi
else
    echo "   $CONSOLE_DIR absent : crochets non installés"
fi

echo "6. Contrôles (lecture seule)"
if [ "$DRY" = 0 ]; then
    runuser -u "$SVC_USER" -- /usr/local/bin/lodyorch check || true
fi

cat <<'TXT'

Installation terminée — rien n'a été démarré. Pour lancer l'orchestrateur :
  lodyorch check                                   # tout doit être ✓
  Dans la console Claude « lodyorch » : /hooks (valider les nouveaux crochets) ou relancer Claude
  lodyorch labels                                  # labels du protocole (#117) ; --apply pour les créer
  sudo systemctl enable --now lodyorch.service      # file automatique, sans fenêtre
  sudo systemctl enable --now lodyorch-notify.timer # emails des rapports (seule unité qui lit le SMTP)
  lodyorch console                                  # rattache LA console (la crée seulement si absente)
Après une mise à jour du code : lodyorch reload (sans couper la session en cours).
TXT
