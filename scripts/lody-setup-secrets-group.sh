#!/usr/bin/env sh
# Crée (si besoin) le groupe système partagé entre le conteneur Lody (uid 10001) et le service hôte
# lody-secrets-reload.service (utilisateur ubuntu), et prépare secrets/ avec les bonnes permissions.
# Idempotent : peut être relancé sans risque.
#
#   sudo ./scripts/lody-setup-secrets-group.sh [utilisateur_hote] [groupe]
#
# Affiche le GID à la fin : à placer dans .env (LODY_SECRETS_GID=<gid>), lu par docker-compose.lody.yml
# (group_add). Voir docs/lody-secrets.md.
set -eu
cd "$(dirname "$0")/.."
HOST_USER="${1:-ubuntu}"
GROUP="${2:-lody-secrets}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Ce script doit être lancé avec sudo (création de groupe, chgrp)." >&2
    exit 1
fi

if ! getent group "$GROUP" >/dev/null 2>&1; then
    groupadd --system "$GROUP"
    echo "Groupe créé : $GROUP"
else
    echo "Groupe déjà présent : $GROUP"
fi
GID="$(getent group "$GROUP" | cut -d: -f3)"

if ! id -nG "$HOST_USER" | tr ' ' '\n' | grep -qx "$GROUP"; then
    usermod -aG "$GROUP" "$HOST_USER"
    echo "$HOST_USER ajouté au groupe $GROUP (une nouvelle session shell le verra ; systemd le voit immédiatement)."
else
    echo "$HOST_USER est déjà membre de $GROUP."
fi

mkdir -p secrets
chown "$HOST_USER:$GROUP" secrets
chmod 2770 secrets  # setgid + rwxrws--- : autre = aucun accès, groupe = lecture/écriture/traverse
echo "secrets/ : $(stat -c '%U:%G %a' secrets)"

echo
echo "GID de $GROUP = $GID"
echo "Ajoute (ou vérifie) dans .env, à la racine du dépôt :"
echo "  LODY_SECRETS_GID=$GID"
