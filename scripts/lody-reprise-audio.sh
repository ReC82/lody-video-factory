#!/usr/bin/env sh
# Reprend UNIQUEMENT la voix d'une production terminée (#92, direction vocale par réplique) : un seul appel
# fournisseur réel (ElevenLabs), les images déjà générées sont réutilisées, jamais régénérées. La production
# d'origine n'est jamais modifiée ; le résultat devient une NOUVELLE VERSION, pour comparer à l'écoute.
#
#   DOCKER="sudo -n docker" ./scripts/lody-reprise-audio.sh <id-de-production> <fichier-texte-balisé> \
#       <fichier-texte-propre> <voice-id> <model-id> <stability> <similarity-boost> <style> <speed> <speaker-boost>
set -eu
PROD="${1:?usage: $0 <id-de-production> <texte-balisé> <texte-propre> <voice-id> <model-id> <stability> <similarity-boost> <style> <speed> <speaker-boost>}"
SPOKEN_FILE="${2:?fichier texte avec balises de jeu requis}"
SUBTITLE_FILE="${3:?fichier texte propre (sans balise) requis}"
VOICE_ID="${4:?voice-id requis}"
MODEL_ID="${5:?model-id requis}"
STABILITY="${6:?stability requis}"
SIMILARITY="${7:?similarity-boost requis}"
STYLE="${8:?style requis}"
SPEED="${9:?speed requis}"
SPEAKER_BOOST="${10:?speaker-boost requis (true/false)}"
DOCKER="${DOCKER:-docker}"
ENGINE="${LODY_ENGINE_CONTAINER:-moneyprinterturbo-api}"
LODY="${LODY_UI_CONTAINER:-lody-video-factory-ui}"
DIR="$(cd "$(dirname "$0")/.." && pwd)/webui/lody/generation"

TASK="$($DOCKER exec -i "$LODY" python3 - "$PROD" <<'PY'
import sqlite3, sys
row = sqlite3.connect("file:/data/lody.sqlite3?mode=ro", uri=True).execute(
    "select status, external_task_id from productions where id = ?", (sys.argv[1],)).fetchone()
if not row or row[0] != "TERMINEE" or not row[1]:
    sys.exit("production introuvable ou non terminée")
print(row[1])
PY
)"
echo "Production $PROD -> tâche d'origine $TASK"

$DOCKER exec "$ENGINE" mkdir -p /tmp/lody-reprise
$DOCKER cp "$DIR/reprise_audio.py" "$ENGINE:/tmp/lody-reprise/reprise_audio.py"
$DOCKER cp "$SPOKEN_FILE" "$ENGINE:/tmp/lody-reprise/spoken.txt"
$DOCKER cp "$SUBTITLE_FILE" "$ENGINE:/tmp/lody-reprise/subtitle.txt"
trap '$DOCKER exec "$ENGINE" rm -rf /tmp/lody-reprise' EXIT

REPORT="$($DOCKER exec -w /MoneyPrinterTurbo -e PYTHONPATH=/tmp/lody-reprise:/MoneyPrinterTurbo "$ENGINE" \
  python3 /tmp/lody-reprise/reprise_audio.py run --task-dir "/MoneyPrinterTurbo/storage/tasks/$TASK" \
    --spoken-text-file /tmp/lody-reprise/spoken.txt --subtitle-text-file /tmp/lody-reprise/subtitle.txt \
    --voice-id "$VOICE_ID" --model-id "$MODEL_ID" --stability "$STABILITY" --similarity-boost "$SIMILARITY" \
    --style "$STYLE" --speed "$SPEED" --speaker-boost "$SPEAKER_BOOST" | tail -n 1)"
echo "$REPORT" | python3 -c "import json,sys; r=json.load(sys.stdin); print('Nouvelle tâche :', r['new_task_id'], '| clips réutilisés :', r['reused_clip_count'], '| appels fournisseur :', r['provider_calls'], '| durée audio :', r['audio_duration_s'], 's')"
# Dans l'image de Lody, le paquet « lody » vit sous /MoneyPrinterTurbo/webui.
echo "$REPORT" | $DOCKER exec -i -e PYTHONPATH=/MoneyPrinterTurbo/webui "$LODY" python3 -m lody.generation.reprise_audio register --production "$PROD"
