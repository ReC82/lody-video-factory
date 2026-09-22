#!/usr/bin/env sh
# Applique les clés en attente (secrets.toml) dans config.toml, avec retour arrière automatique si le moteur
# ne redevient pas sain. À lancer sur l'HÔTE UNIQUEMENT (jamais dans un conteneur) : c'est lui qui redémarre
# moneyprinterturbo-api, jamais le conteneur de Lody.
#
# Déclenché automatiquement par le service systemd lody-secrets-reload.service, lui-même réveillé par
# lody-secrets-reload.path (surveillance de secrets/secrets.reload) : voir deploy/systemd/. Ne fait rien
# (« noop ») si aucune clé n'est en attente — sûr à relancer à la main pour diagnostiquer.
#
#   ./scripts/lody-apply-secrets.sh
set -eu
cd "$(dirname "$0")/.."
PYTHONPATH=webui python3 -m lody.generation.apply_secrets \
  --config "${LODY_CONFIG:-config.toml}" \
  --secrets-dir "${LODY_SECRETS_DIR:-secrets}" \
  --report "${LODY_REPORT:-engine-report/engine-capabilities.json}" \
  --container "${LODY_ENGINE_CONTAINER:-moneyprinterturbo-api}" \
  --docker "${LODY_DOCKER_BIN:-sudo -n docker}"
