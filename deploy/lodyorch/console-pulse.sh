#!/bin/sh
# Console LODYORCH : point d'activité toutes les 5 minutes, alignées sur l'horloge (hh:00, hh:05…).
# Lecture seule (« lodyorch pulse ») : aucun commentaire GitHub, aucune session lancée.
INTERVAL=${LODYORCH_PULSE_INTERVAL:-300}
echo "Activité LODYORCH — un point toutes les $((INTERVAL / 60)) min ; rapport détaillé : lodyorch status"
sleep 1  # laisse la console prendre sa taille avant le premier point
while :; do
    lodyorch pulse 2>&1 || echo "[$(date '+%d/%m %H:%M')] point d'activité indisponible (voir lodyorch status)"
    echo
    sleep $((INTERVAL - $(date +%s) % INTERVAL))
done
