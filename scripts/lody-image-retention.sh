#!/usr/bin/env bash
# #209 — Rétention des images Docker de Lody. SIMULATION par défaut ; --apply explicite.
#
#   ./scripts/lody-image-retention.sh                  # plan détaillé, rien n'est supprimé
#   ./scripts/lody-image-retention.sh --apply          # retire les seules candidates (fail closed)
#   ./scripts/lody-image-retention.sh report           # disque / images / alertes 75-85 %
#   ./scripts/lody-image-retention.sh inventory > inv.json
#
# À lancer sur l'hôte, à la racine du clone de production, par un compte qui a accès à Docker
# (ex. `sudo -n -E ./scripts/lody-image-retention.sh`). Journal : ${LODY_OPS_DIR:-.lody-ops}/retention.jsonl.
# Jamais de `prune`, jamais de `-f` : voir docs/lody-retention.md.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$here/lody_retention.py" "$@"
