#!/usr/bin/env sh
# Procédure d'activation initiale de l'administrateur (ou changement de mot de passe) : lit un mot de passe de
# façon interactive, JAMAIS en argument de ligne de commande (visible dans l'historique du shell et dans la
# liste des processus). Écrit secrets/admin-auth.json (0640, groupe lody-secrets, hash scrypt — jamais le mot
# de passe en clair). Voir docs/lody-auth.md.
#
#   ./scripts/lody-auth-set-password.sh [identifiant]     (par défaut : admin)
set -eu
cd "$(dirname "$0")/.."
USERNAME="${1:-admin}"

printf 'Nouveau mot de passe pour « %s » : ' "$USERNAME" >&2
stty -echo 2>/dev/null || true
IFS= read -r PASSWORD
stty echo 2>/dev/null || true
printf '\n' >&2
printf 'Confirme : ' >&2
stty -echo 2>/dev/null || true
IFS= read -r CONFIRM
stty echo 2>/dev/null || true
printf '\n' >&2

if [ "$PASSWORD" != "$CONFIRM" ]; then
    echo "Les deux saisies ne correspondent pas : rien n'a été écrit." >&2
    exit 1
fi
if [ "${#PASSWORD}" -lt 12 ]; then
    echo "Au moins 12 caractères recommandés pour un compte administrateur unique." >&2
fi

PYTHONPATH=. LODY_AUTH_USERNAME="$USERNAME" LODY_AUTH_PASSWORD="$PASSWORD" python3 - <<'PY'
import os
from pathlib import Path
from lody_auth.store import set_admin_password

path = Path(os.environ.get("LODY_AUTH_ADMIN_FILE", "secrets/admin-auth.json"))
set_admin_password(path, os.environ["LODY_AUTH_USERNAME"], os.environ["LODY_AUTH_PASSWORD"])
print(f"Identifiant écrit : {path} (0640, groupe lody-secrets).")
PY
unset PASSWORD CONFIRM
echo "Redémarre le service : sudo -n docker compose -f docker-compose.lody.yml restart lody-auth"
