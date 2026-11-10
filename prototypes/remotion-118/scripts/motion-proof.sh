#!/usr/bin/env bash
# Preuve du mouvement : pour chaque calque animé (zone de l'image), différence absolue entre
# images successives (ffmpeg tblend=difference + signalstats). Imprime, par zone, le nombre de
# paires d'images successives où la zone change (YAVG > 0.05), et la différence moyenne/maximale.
# Usage : scripts/motion-proof.sh out/prototype-118.mp4
set -euo pipefail
in="${1:-out/prototype-118.mp4}"
# nom  largeur:hauteur:x:y
zones=(
  "yeux 280:150:400:910"
  "bouche 160:110:460:1060"
  "bras_droit 360:420:640:900"
  "ciel_nuages 1080:300:0:330"
  "herbe 1080:120:0:1580"
  "graphique 420:380:600:470"
  "titre 920:220:80:110"
  "sous_titres 1000:110:40:1760"
)
printf "%-12s %8s %10s %10s\n" zone "paires" "moyenne" "max"
for z in "${zones[@]}"; do
  name=${z%% *}; crop=${z#* }
  ffmpeg -v error -i "$in" -vf "crop=$crop,format=gray,tblend=all_mode=difference,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-" -an -f null - \
    | awk -v n="$name" -F= '/YAVG/ {v=$2+0; t++; s+=v; if (v>0.05) c++; if (v>m) m=v} END {printf "%-12s %4d/%-4d %10.3f %10.2f\n", n, c, t, s/t, m}'
done
