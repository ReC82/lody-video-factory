#!/usr/bin/env sh
# Répare le rendu d'une production terminée : refait UNIQUEMENT l'incrustation des sous-titres, à partir des
# assets déjà générés (aucun appel OpenAI / ElevenLabs / fournisseur, réseau bloqué pendant le montage).
# Le rendu original est conservé ; le rendu corrigé est ajouté à la production comme « réparation technique ».
#
#   DOCKER="sudo -n docker" ./scripts/lody-repair-render.sh <id-de-production>     (ex. prd_ea6e63ae142a)
set -eu
PROD="${1:?usage: $0 <id-de-production>}"
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
echo "Production $PROD -> tâche $TASK"

$DOCKER exec "$ENGINE" mkdir -p /tmp/lody-repair
$DOCKER cp "$DIR/typography.py" "$ENGINE:/tmp/lody-repair/typography.py"
$DOCKER cp "$DIR/repair_render.py" "$ENGINE:/tmp/lody-repair/repair_render.py"
trap '$DOCKER exec "$ENGINE" rm -rf /tmp/lody-repair' EXIT

REPORT="$($DOCKER exec -w /MoneyPrinterTurbo -e PYTHONPATH=/tmp/lody-repair:/MoneyPrinterTurbo "$ENGINE" \
  python3 /tmp/lody-repair/repair_render.py run --task-dir "/MoneyPrinterTurbo/storage/tasks/$TASK" | tail -n 1)"
echo "$REPORT" | python3 -c "import json,sys; r=json.load(sys.stdin); print('Rendu corrigé :', r['repaired']['file'], '| appels fournisseur :', r['provider_calls'], '| police :', r['font']['before'], '->', r['font']['after'], '| original intact :', r['original']['untouched'])"
# Dans l'image de Lody, le paquet « lody » vit sous /MoneyPrinterTurbo/webui.
echo "$REPORT" | $DOCKER exec -i -e PYTHONPATH=/MoneyPrinterTurbo/webui "$LODY" python3 -m lody.generation.repair_render register --production "$PROD"
