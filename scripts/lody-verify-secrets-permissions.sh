#!/usr/bin/env sh
# Vérification d'intégration RÉELLE (pas simulée) du modèle de permissions de secrets/ : reproduit exactement
# l'UID du conteneur Lody (10001) et prouve, avec de vrais appels système, que :
#   1. un fichier créé par l'uid 10001 en 0640, groupe lody-secrets, EST lisible par l'utilisateur hôte
#      (celui qui fait tourner lody-secrets-reload.service) ;
#   2. il n'est PAS lisible par un utilisateur tiers (nobody) ;
#   3. secrets/ elle-même n'est pas accessible à « autre ».
#
# Ne touche à rien de réel : écrit et supprime un fichier jetable. Nécessite sudo. À relancer après toute
# modification du groupe ou des permissions (voir docs/lody-secrets.md).
#
#   sudo ./scripts/lody-verify-secrets-permissions.sh [utilisateur_hote] [groupe]
set -eu
cd "$(dirname "$0")/.."
HOST_USER="${1:-ubuntu}"
GROUP="${2:-lody-secrets}"
MARKER="secrets/.permission-check-$$"
FAIL=0

if [ "$(id -u)" -ne 0 ]; then
    echo "Lance ce script avec sudo (simulation de l'uid 10001, tests en tant qu'un autre utilisateur)." >&2
    exit 1
fi

cleanup() { rm -f "$MARKER"; }
trap cleanup EXIT

echo "== secrets/ =="
DIR_PERMS="$(stat -c '%a' secrets)"
DIR_GROUP="$(stat -c '%G' secrets)"
echo "permissions=$DIR_PERMS groupe=$DIR_GROUP"
case "$DIR_PERMS" in
    2770) echo "OK : setgid + rwxrws--- (autre = aucun accès)" ;;
    *) echo "ÉCHEC : attendu 2770, obtenu $DIR_PERMS" >&2; FAIL=1 ;;
esac
[ "$DIR_GROUP" = "$GROUP" ] || { echo "ÉCHEC : groupe attendu $GROUP, obtenu $DIR_GROUP" >&2; FAIL=1; }

echo
echo "== fichier créé par l'uid 10001 (comme le conteneur Lody) =="
install -o 10001 -g "$GROUP" -m 0640 /dev/null "$MARKER"
FILE_PERMS="$(stat -c '%a' "$MARKER")"
FILE_GROUP="$(stat -c '%G' "$MARKER")"
echo "permissions=$FILE_PERMS groupe=$FILE_GROUP"
[ "$FILE_PERMS" = "640" ] || { echo "ÉCHEC : attendu 640, obtenu $FILE_PERMS" >&2; FAIL=1; }
[ "$FILE_GROUP" = "$GROUP" ] || { echo "ÉCHEC : groupe attendu $GROUP, obtenu $FILE_GROUP" >&2; FAIL=1; }

echo
echo "== lecture par $HOST_USER (doit réussir : membre de $GROUP) =="
if sudo -n -u "$HOST_USER" test -r "$MARKER"; then
    echo "OK : $HOST_USER peut lire le fichier"
else
    echo "ÉCHEC : $HOST_USER NE PEUT PAS lire le fichier (vérifie qu'il est bien membre de $GROUP)" >&2
    FAIL=1
fi

echo
echo "== lecture par nobody (doit échouer : pas dans $GROUP) =="
if sudo -n -u nobody test -r "$MARKER" 2>/dev/null; then
    echo "ÉCHEC : nobody peut lire le fichier — le dossier ou le fichier est trop ouvert" >&2
    FAIL=1
else
    echo "OK : nobody ne peut pas lire le fichier"
fi

echo
if [ "$FAIL" -eq 0 ]; then
    echo "Toutes les vérifications ont réussi."
else
    echo "Au moins une vérification a échoué (voir ci-dessus)." >&2
    exit 1
fi
