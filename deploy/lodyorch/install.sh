#!/bin/sh
# Installation / mise à jour de LODYORCH sur l'hôte, par le propriétaire (voir docs/lodyorch.md).
#
#   sudo deploy/lodyorch/install.sh            installe ou met à jour (sans démarrer)
#   sudo deploy/lodyorch/install.sh --dry-run  affiche les actions, ne change rien
#   sudo deploy/lodyorch/install.sh --isolation [--dry-run]
#                                              + séparation des permissions (#207) : utilisateur
#                                              lodyorch-merge, /etc/lodyorch, broker, wrappers gh/git
#                                              (docs/lody-isolation-permissions.md). Idempotent ; ne
#                                              dépose, n'affiche ni n'écrase AUCUN secret.
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
ISOLATION=0
for arg in "$@"; do
    case $arg in
        --dry-run) DRY=1 ;;
        --isolation) ISOLATION=1 ;;
        *) echo "Option inconnue : $arg" >&2; exit 2 ;;
    esac
done
BROKER_USER=lodyorch-merge
BROKER_DIR=/var/lib/lodyorch-merge
BROKER_ETC=/etc/lodyorch

run() { if [ "$DRY" = 1 ]; then echo "  [dry-run] $*"; else "$@"; fi; }
as_user() { run runuser -u "$SVC_USER" -- "$@"; }

if [ "$DRY" = 0 ] && [ "$(id -u)" != 0 ]; then
    echo "À lancer avec sudo (écrit dans /opt, /usr/local/bin et /etc/systemd/system)." >&2
    exit 1
fi
[ -n "$SVC_HOME" ] || { echo "Compte $SVC_USER introuvable." >&2; exit 1; }
for f in lodyorch.py guard.py merge_policy.py session_prompt.md lodyorch.env claude-settings.json labels.json lodyorch \
         console-settings.json console_settings.py systemd/lodyorch.service \
         systemd/lodyorch-notify.service systemd/lodyorch-notify.timer \
         merge_broker.py merge-broker.env bin/gh bin/git-credential-lodyorch \
         systemd/lodyorch-merge.service systemd/lodyorch-merge.timer; do
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
for f in lodyorch.py guard.py merge_policy.py console_settings.py; do
    run install -o root -g root -m 0755 "$SRC/$f" "$DEST/$f"
done
for f in session_prompt.md lodyorch.env claude-settings.json labels.json console-settings.json; do
    run install -o root -g root -m 0644 "$SRC/$f" "$DEST/$f"
done
run install -o root -g root -m 0755 "$SRC/lodyorch" /usr/local/bin/lodyorch
# #207 : broker (exécuté par lodyorch-merge, code root non modifiable) et wrappers de l'identité
# « application GitHub » mis en tête du PATH du service. Sans jeton d'agent, ils se comportent
# comme gh / git d'origine : leur seule présence ne change rien avant --isolation.
run install -o root -g root -m 0755 "$SRC/merge_broker.py" "$DEST/merge_broker.py"
run install -d -o root -g root -m 0755 "$DEST/bin"
for f in gh git-credential-lodyorch; do
    run install -o root -g root -m 0755 "$SRC/bin/$f" "$DEST/bin/$f"
done

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

if [ "$ISOLATION" = 1 ]; then
    echo "5b. Isolation des permissions (#207) : identité privilégiée sous $BROKER_USER (rien n'est démarré)"
    SVC_GROUP=$(id -gn "$SVC_USER")
    if getent passwd "$BROKER_USER" >/dev/null 2>&1; then
        echo "   utilisateur $BROKER_USER : déjà présent"
    else
        run useradd --system --user-group --no-create-home --home-dir "$BROKER_DIR" \
            --shell /usr/sbin/nologin "$BROKER_USER"
    fi
    # Secrets : dossier et fichiers lisibles du seul broker (jamais du compte $SVC_USER).
    run install -d -o root -g "$BROKER_USER" -m 0750 "$BROKER_ETC"
    if [ -f "$BROKER_ETC/merge-broker.env" ]; then
        echo "   $BROKER_ETC/merge-broker.env : conservé (jamais écrasé ; comparer avec $SRC/merge-broker.env)"
    else
        run install -o root -g "$BROKER_USER" -m 0640 "$SRC/merge-broker.env" "$BROKER_ETC/merge-broker.env"
    fi
    for s in merge-token app-key.pem; do
        if [ -f "$BROKER_ETC/$s" ]; then
            run chown root:"$BROKER_USER" "$BROKER_ETC/$s"
            run chmod 0640 "$BROKER_ETC/$s"
            echo "   $BROKER_ETC/$s : présent, droits remis à 0640 root:$BROKER_USER (contenu non lu)"
        else
            echo "   $BROKER_ETC/$s : À DÉPOSER par le propriétaire (docs/lody-isolation-permissions.md §4)"
        fi
    done
    # État : journal privé ; demandes déposées par le superviseur ; réponses et jeton d'agent lisibles par lui.
    run install -d -o root -g root -m 0755 "$BROKER_DIR"
    run install -d -o "$BROKER_USER" -g "$BROKER_USER" -m 0700 "$BROKER_DIR/state"
    run install -d -o "$BROKER_USER" -g "$SVC_GROUP" -m 0770 "$BROKER_DIR/requests"
    run install -d -o "$BROKER_USER" -g "$BROKER_USER" -m 0755 "$BROKER_DIR/results"
    run install -d -o "$BROKER_USER" -g "$SVC_GROUP" -m 0750 "$BROKER_DIR/agent"
    for u in lodyorch-merge.service lodyorch-merge.timer; do
        run install -o root -g root -m 0644 "$SRC/systemd/$u" "/etc/systemd/system/$u"
    done
    run systemctl daemon-reload
    # Pousser avec le jeton de l'application : assistant d'identifiants LOCAL au clone dédié (et à
    # ses worktrees), qui remplace tout assistant hérité (ex. « gh auth git-credential »).
    if [ -d "$BASE/repo/.git" ]; then
        as_user git -C "$BASE/repo" config --local --replace-all credential.https://github.com.helper ""
        as_user git -C "$BASE/repo" config --local --add credential.https://github.com.helper "$DEST/bin/git-credential-lodyorch"
    else
        echo "  clone dédié $BASE/repo absent : assistant git-credential-lodyorch à configurer après le clonage (relancer --isolation)"
    fi
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
Isolation des permissions (#207) : voir docs/lody-isolation-permissions.md (secrets à déposer,
  sudo systemctl enable --now lodyorch-merge.timer, puis redémarrage du superviseur ENTRE deux sessions).
TXT
