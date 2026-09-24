#!/usr/bin/env sh
# Génère le catalogue de voix ElevenLabs pour Lody (nom, voice_id, métadonnées publiques : AUCUN secret).
# Fait un appel réel à l'API ElevenLabs (aucune génération, aucun coût) : à lancer sur l'hôte, par le
# propriétaire de config.toml, quand on veut rafraîchir le catalogue proposé dans l'interface (ticket #55).
#   ./scripts/lody-elevenlabs-voices-report.sh            (depuis la racine du dépôt)
set -eu
cd "$(dirname "$0")/.."
PYTHONPATH=webui python3 -m lody.generation.elevenlabs_voices --config "${LODY_CONFIG:-config.toml}" --out "${LODY_VOICE_REPORT:-engine-report/elevenlabs-voices.json}"
