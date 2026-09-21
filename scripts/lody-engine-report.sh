#!/usr/bin/env sh
# Génère le rapport de capacités du moteur pour Lody (booléens et noms de modèles : AUCUN secret).
# À lancer sur l'hôte, par le propriétaire de config.toml, après chaque modification de config.toml.
#   ./scripts/lody-engine-report.sh            (depuis la racine du dépôt)
set -eu
cd "$(dirname "$0")/.."
PYTHONPATH=webui python3 -m lody.generation.engine_facts --config "${LODY_CONFIG:-config.toml}" --out "${LODY_REPORT:-engine-report/engine-capabilities.json}"
