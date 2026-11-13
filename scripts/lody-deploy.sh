#!/usr/bin/env bash
# #209 — Livraison PROD de Lody, traçable et réversible. À lancer sur l'hôte, à la racine du clone de
# production, par un compte qui a accès à Docker (ex. `sudo -n -E ./scripts/lody-deploy.sh --2d`).
#
#   ./scripts/lody-deploy.sh [--2d] [--dry-run]           livre HEAD (tag immuable = SHA Git court)
#   ./scripts/lody-deploy.sh --rollback <tag>             revient à une release VALIDÉE du journal (mode 2D repris du journal)
#   ./scripts/lody-deploy.sh --list                        journal des releases
#
# Options : --2d (interface avec le moteur Remotion : docker-compose.lody-2d.yml), --dry-run (affiche
# les commandes qui modifieraient quelque chose sans les lancer), --retention-apply (rétention réelle
# après une livraison validée ; sinon simulation), --no-retention, --allow-newer-schema (retour arrière
# vers une release au schéma SQLite plus ancien que la base, après lecture de l'avertissement).
#
# Étapes : verrou (flock) → aucune production/rendu actif (sonde) → sauvegarde SQLite → build tagué
# (lody-auth seulement si ses sources ont changé) → `up -d --no-build` → attente « healthy » →
# enregistrement de la release (validée ou non) → rétention (simulation par défaut) → rapport disque.
# Rien n'est supprimé par ce script ; la rétention réelle exige --retention-apply. Voir docs/lody-retention.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OPS_DIR="${LODY_OPS_DIR:-$ROOT/.lody-ops}"
export LODY_OPS_DIR="$OPS_DIR"
UI="${LODY_UI_CONTAINER:-lody-video-factory-ui}"
AUTH="${LODY_AUTH_CONTAINER:-lody-video-factory-auth}"
HEALTH_TIMEOUT="${LODY_HEALTH_TIMEOUT:-180}"
HEALTH_INTERVAL="${LODY_HEALTH_INTERVAL:-3}"
PYTHON="${PYTHON:-python3}"

mode=classic; dry=0; retention=plan; rollback=""; allow_newer=0
while [ $# -gt 0 ]; do
  case "$1" in
    --2d) mode=2d ;;
    --dry-run) dry=1 ;;
    --retention-apply) retention=apply ;;
    --no-retention) retention=none ;;
    --rollback) rollback="${2:?--rollback attend un tag}"; shift ;;
    --allow-newer-schema) allow_newer=1 ;;
    --list) exec "$PYTHON" scripts/lody_release.py list ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "option inconnue : $1" >&2; exit 64 ;;
  esac
  shift
done

mkdir -p "$OPS_DIR/backups"
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$OPS_DIR/deploy.log" >&2; }
run() {
  if [ "$dry" = 1 ]; then log "[simulation] $*"; else log "+ $*"; "$@"; fi
}
compose_files() {
  local files=(-f docker-compose.lody.yml)
  [ "$1" = 2d ] && files+=(-f docker-compose.lody-2d.yml)
  printf '%s\n' "${files[@]}"
}

# 1. Verrou de déploiement, partagé avec la rétention (scripts/lody_retention.py).
exec 9>>"$OPS_DIR/deploy.lock"
if ! flock -n 9; then
  log "ALERTE : une autre livraison ou une rétention tient $OPS_DIR/deploy.lock — abandon"
  exit 3
fi
export LODY_DEPLOY_LOCK_HELD=1

ui_running() { [ "$(docker container inspect --format '{{.State.Status}}' "$UI" 2>/dev/null || true)" = running ]; }

# 2. Aucune production ni aucun rendu actif dans l'interface (fail closed : « indéterminé » = actif).
check_idle() {
  ui_running || { log "conteneur $UI non démarré : aucun rendu en cours"; return 0; }
  local out rc=0
  out="$(docker exec -i "$UI" python3 - < scripts/lody_activity_probe.py)" || rc=$?
  log "sonde d'activité : $out"
  if [ "$rc" != 0 ]; then
    if [ "$dry" = 1 ]; then log "[simulation] livraison REFUSÉE en réel : activité en cours ou indéterminée"; return 0; fi
    log "ALERTE : activité en cours ou indéterminée (code $rc) — livraison refusée, rien n'a changé"
    exit 3
  fi
}

db_version() {
  ui_running || { echo ""; return 0; }
  docker exec "$UI" python3 -c "import sqlite3; print(sqlite3.connect('file:/data/lody.sqlite3?mode=ro', uri=True).execute('PRAGMA user_version').fetchone()[0])" 2>/dev/null || echo ""
}

# 3. Sauvegarde cohérente (API backup de SQLite) écrite sur l'hôte, hors du volume.
backup_db() {
  ui_running || { echo ""; return 0; }
  local target
  target="$OPS_DIR/backups/lody-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
  if [ "$dry" = 1 ]; then log "[simulation] sauvegarde SQLite → $target"; echo ""; return 0; fi
  docker exec "$UI" python3 -c "
import os, sqlite3, sys, tempfile
fd, path = tempfile.mkstemp(suffix='.sqlite3'); os.close(fd)
source = sqlite3.connect('file:/data/lody.sqlite3?mode=ro', uri=True); copy = sqlite3.connect(path)
source.backup(copy); copy.close(); source.close()
sys.stdout.buffer.write(open(path, 'rb').read()); os.remove(path)
" > "$target"
  [ -s "$target" ] || { log "ERREUR : sauvegarde vide ($target) — livraison arrêtée"; exit 1; }
  log "sauvegarde SQLite : $target ($(wc -c < "$target") octets)"
  echo "$target"
}

# Construit une image seulement si le tag n'existe pas ; refuse un tag existant d'une autre révision
# (un tag immuable ne doit jamais changer de contenu).
build_once() {
  local ref="$1" label="$2" expected="$3"; shift 3
  if docker image inspect "$ref" >/dev/null 2>&1; then
    local found
    found="$(docker image inspect --format "{{ index .Config.Labels \"$label\" }}" "$ref" 2>/dev/null || true)"
    if [ -n "$found" ] && [ "$found" != "$expected" ]; then
      log "ERREUR : $ref existe déjà pour $label=$found (attendu $expected) — tag immuable, livraison arrêtée"
      exit 1
    fi
    log "$ref déjà construite ($label=${found:-?}) : réutilisée, aucun build"
    return 0
  fi
  run docker build "$@" -t "$ref" .
}

wait_healthy() {
  local container="$1" waited=0 status
  [ "$dry" = 1 ] && { log "[simulation] attente « healthy » de $container"; return 0; }
  while [ "$waited" -lt "$HEALTH_TIMEOUT" ]; do
    status="$(docker container inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || echo absent)"
    [ "$status" = healthy ] && { log "$container : healthy (${waited}s)"; return 0; }
    [ "$status" = unhealthy ] && break
    sleep "$HEALTH_INTERVAL"; waited=$((waited + HEALTH_INTERVAL))
  done
  log "ERREUR : $container n'est pas « healthy » (dernier état : ${status:-?})"
  return 1
}

record() {  # record <kind> <validated yes|no> <health> <db_version> <backup> <mode> <images...>
  local kind="$1" validated="$2" health="$3" dbv="$4" backup="$5" rmode="$6"; shift 6
  local args=(record --tag "$TAG" --git-sha "$GIT_SHA" --mode "$rmode" --validated "$validated" --kind "$kind"
              --health "$health")
  [ -n "$dbv" ] && args+=(--db-version "$dbv")
  [ -n "$backup" ] && args+=(--backup "$backup")
  for image in "$@"; do args+=(--image "$image"); done
  if [ "$dry" = 1 ]; then log "[simulation] lody_release.py ${args[*]}"; return 0; fi
  "$PYTHON" scripts/lody_release.py "${args[@]}"
}

post_checks() {
  case "$retention" in
    none) log "rétention : désactivée (--no-retention)" ;;
    plan) "$PYTHON" scripts/lody_retention.py plan || log "ALERTE : simulation de rétention en échec (livraison non affectée)" ;;
    apply)
      if [ "$dry" = 1 ]; then log "[simulation] lody_retention.py --apply"
      else "$PYTHON" scripts/lody_retention.py plan --apply || log "ALERTE : rétention refusée ou partielle (voir le plan ci-dessus)"; fi ;;
  esac
  "$PYTHON" scripts/lody_retention.py report || true
}

if [ -n "$rollback" ]; then
  # ---------------------------------------------------------------- retour arrière
  TAG="$rollback"
  dbv="$(db_version)"
  check_args=(rollback-check --tag "$TAG")
  [ -n "$dbv" ] && check_args+=(--db-version "$dbv")
  [ "$allow_newer" = 1 ] && check_args+=(--allow-newer-schema)
  verdict="$("$PYTHON" scripts/lody_release.py "${check_args[@]}")" || {
    printf '%s\n' "$verdict" >&2
    log "ALERTE : retour arrière vers $TAG refusé — rien n'a changé"; exit 3; }
  printf '%s\n' "$verdict" >&2
  read -r rmode GIT_SHA LODY_IMAGE_TAG LODY_AUTH_IMAGE_TAG images < <("$PYTHON" -c '
import json, sys
v = json.load(sys.stdin)
print(v["mode"], v.get("git_sha") or "-", v["env"]["LODY_IMAGE_TAG"], v["env"].get("LODY_AUTH_IMAGE_TAG", v["env"]["LODY_IMAGE_TAG"]),
      ",".join(f"{k}={r}" for k, r in v["images"].items()))' <<<"$verdict")
  export LODY_IMAGE_TAG LODY_AUTH_IMAGE_TAG
  check_idle
  backup="$(backup_db)"
  mapfile -t files < <(compose_files "$rmode")
  run docker compose "${files[@]}" up -d --no-build
  health=ok; wait_healthy "$UI" && wait_healthy "$AUTH" || health=ko
  IFS=, read -r -a image_list <<<"$images"
  record rollback "$([ "$health" = ok ] && echo yes || echo no)" "$health" "$dbv" "$backup" "$rmode" "${image_list[@]}"
  [ "$health" = ok ] || { log "ALERTE : retour arrière vers $TAG non sain — intervention manuelle (docs/lody-retention.md)"; exit 1; }
  log "retour arrière vers $TAG validé"
  exit 0
fi

# -------------------------------------------------------------------- livraison
if ! git diff --quiet HEAD --; then
  if [ "$dry" = 1 ]; then log "[simulation] fichiers suivis modifiés : la livraison réelle serait refusée"
  else log "ERREUR : fichiers suivis modifiés (git status) — on ne livre que des commits"; exit 1; fi
fi
GIT_SHA="$(git rev-parse HEAD)"
TAG="$(git rev-parse --short=12 HEAD)"
AUTH_HASH="$(printf '%s %s' "$(git rev-parse HEAD:lody_auth)" "$(git rev-parse HEAD:Dockerfile.lody-auth)" | sha256sum | cut -c1-12)"
export LODY_IMAGE_TAG="$TAG" LODY_AUTH_IMAGE_TAG="src-$AUTH_HASH"
previous="$("$PYTHON" scripts/lody_release.py current 2>/dev/null || true)"
log "livraison $TAG (mode $mode, lody-auth src-$AUTH_HASH) ; release précédente : ${previous:-aucune}"

check_idle
dbv="$(db_version)"
backup="$(backup_db)"

build_once "lody-video-factory:$TAG" org.opencontainers.image.revision "$GIT_SHA" \
  -f Dockerfile.lody --build-arg "LODY_GIT_SHA=$GIT_SHA"
images=("ui=lody-video-factory:$TAG" "auth=lody-auth:src-$AUTH_HASH")
if [ "$mode" = 2d ]; then
  build_once "lody-video-factory-2d:$TAG" org.opencontainers.image.revision "$GIT_SHA" \
    -f Dockerfile.lody-2d --build-arg "LODY_BASE_IMAGE=lody-video-factory:$TAG" --build-arg "LODY_GIT_SHA=$GIT_SHA"
  images+=("2d=lody-video-factory-2d:$TAG")
fi
build_once "lody-auth:src-$AUTH_HASH" lody.source-hash "$AUTH_HASH" \
  -f Dockerfile.lody-auth --build-arg "LODY_SOURCE_HASH=$AUTH_HASH"

mapfile -t files < <(compose_files "$mode")
run docker compose "${files[@]}" up -d --no-build
health=ok; wait_healthy "$UI" && wait_healthy "$AUTH" || health=ko
record deploy "$([ "$health" = ok ] && echo yes || echo no)" "$health" "$dbv" "$backup" "$mode" "${images[@]}"
if [ "$health" != ok ]; then
  log "ALERTE : livraison $TAG NON validée. Retour arrière : ./scripts/lody-deploy.sh --rollback ${previous:-<tag>}"
  exit 1
fi
log "livraison $TAG validée"
post_checks
