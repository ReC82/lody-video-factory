# Rétention Docker, versions reproductibles et nettoyage des worktrees (#209)

> **État** : outils livrés et testés en CI (sans Docker), **jamais exécutés sur l'hôte** par la session
> qui les a écrits : celle-ci n'a ni Docker, ni sudo, ni accès à `/srv/moneyprinterturbo`. Les chiffres de
> l'audit du 13/11/2026 sont repris comme **données à confronter**, pas comme mesures de cette livraison.
> Rien n'est supprimé tant que les phases A et B n'ont pas été validées sur l'hôte (voir « Ordre de mise en
> service »).

## 1. Ce qui est en place dans le dépôt et ce qui ne l'est pas

| Élément | Fichier versionné | Image | Constat |
|---|---|---|---|
| Interface Lody | `docker-compose.lody.yml`, `Dockerfile.lody` | `lody-video-factory:<tag>` | build local, `pull_policy: never` |
| Authentification | `docker-compose.lody.yml`, `Dockerfile.lody-auth` | `lody-auth:<tag>` | reconstruite à **chaque** livraison avant #209 (même sans changement) |
| Interface + rendu 2D (PROD) | **aucun** avant #209 : override racine `docker-compose.lody-2d.override.yml` **non versionné** (audit) | `lody-video-factory-2d:local` | tag **mutable** : retour arrière non reproductible |
| Pilote DEV 2D | `docker-compose.lody-dev-2d.yml` | `lody-video-factory-2d:${LODY_IMAGE_TAG:-local}` | instance séparée (#154) |
| Moteur MoneyPrinterTurbo | `docker-compose.engine-local.yml`, `Dockerfile` | `moneyprinterturbo:<tag>` ; amont `ghcr.io/harry0703/moneyprinterturbo:latest` | partagé API/WebUI : **hors rétention automatique** |
| Image publique | `.github/workflows/docker-ghcr.yml` | `ghcr.io/rec82/lody-video-factory` | non utilisée par la production |

Avant #209, il n'y avait **aucun script de livraison** : le « tag SHA à chaque déploiement » de l'audit vient de la
commande manuelle de `docs/lody-ui.md` (`LODY_IMAGE_TAG=$(git rev-parse --short HEAD)`), sans journal ni rétention.

## 2. Phase A — versions reproductibles et retour arrière

### Ce qui change

- **Tag immuable = SHA Git** (12 caractères) pour `lody-video-factory` et `lody-video-factory-2d`. Le script de
  livraison refuse de réutiliser un tag existant qui porterait une autre révision.
- **Provenance gravée dans l'image** (labels) : `org.opencontainers.image.revision` (SHA complet), `lody.component`
  (`ui`, `2d`, `auth`), `lody.base-image` (image Lody exacte sous la 2D), `lody.source-hash` (auth).
- **`lody-auth` n'est reconstruit que si ses sources changent** : tag `lody-auth:src-<empreinte de lody_auth/ et
  du Dockerfile>` (variable `LODY_AUTH_IMAGE_TAG`). Une livraison qui ne touche pas `lody_auth/` ne crée ni image ni
  nouveau conteneur d'authentification (les sessions admin ne sont plus coupées pour rien). Sans cette variable, le
  Compose garde l'ancien comportement (`LODY_IMAGE_TAG`).
- **Override 2D versionné** `docker-compose.lody-2d.yml` : image `lody-video-factory-2d:${LODY_IMAGE_TAG}` avec un
  tag **obligatoire** (pas de `local` par défaut), construite au-dessus de `lody-video-factory:<même SHA>`.
- **Journal des releases** `.lody-ops/releases.jsonl` (hors Git, hors images) : une ligne par livraison avec les
  identifiants d'image `sha256:…` (adressés par le contenu : ils font foi, pas les tags), la recette (empreintes des
  Dockerfiles, image Node épinglée, `package-lock.json` Remotion), la version du schéma SQLite du code, la version
  de la base AVANT livraison, la sauvegarde prise juste avant, et `validated` (vrai seulement si « healthy »).

### Livraison : `scripts/lody-deploy.sh`

```bash
cd /srv/moneyprinterturbo                         # clone de production, sur le commit à livrer
sudo -n -E ./scripts/lody-deploy.sh --2d --dry-run  # affiche ce qui serait fait, ne change rien
sudo -n -E ./scripts/lody-deploy.sh --2d            # livraison réelle (sans --2d : interface classique)
./scripts/lody-deploy.sh --list                     # journal des releases (* = en service)
```

Étapes : verrou `flock` `.lody-ops/deploy.lock` (partagé avec la rétention) → **refus si une production est
`CONFIRMEE`/`EN_FILE`/`EN_COURS` ou si un processus Remotion/Chrome/ffmpeg tourne dans l'interface** (sonde
`scripts/lody_activity_probe.py`, en lecture seule ; « indéterminé » = refus) → sauvegarde SQLite cohérente
(`.lody-ops/backups/lody-<date>.sqlite3`) → builds tagués → `up -d --no-build` → attente « healthy » (180 s) →
enregistrement de la release → rétention **en simulation** → rapport disque.

Codes : `0` livré et validé ; `1` erreur ou livraison non saine (release enregistrée « NON validée », commande de
retour arrière affichée) ; `3` refus sans aucun changement (verrou, activité, retour arrière non sûr).

### Retour arrière

```bash
./scripts/lody-deploy.sh --list
sudo -n -E ./scripts/lody-deploy.sh --rollback <tag>
```

Le retour arrière n'accepte **que des releases validées du journal** ; il vérifie que chaque image existe encore et
porte toujours le même identifiant (un tag réutilisé est refusé), puis compare le schéma :

- base au même schéma ou plus ancien que la release : retour direct ;
- base **plus récente** (une migration a eu lieu depuis) : refus, sauf `--allow-newer-schema`. Les migrations Lody
  sont additives (`webui/lody/db.py`, `PRAGMA user_version` : une ancienne version ignore les colonnes et tables
  ajoutées), mais les données saisies depuis n'y sont pas toutes visibles. Le message indique la **sauvegarde
  cohérente avec la release visée** (celle prise juste avant la livraison suivante) si une restauration est préférée.

**Les images d'octobre ne sont pas des retours arrière sûrs** : elles ne figurent pas au journal, n'ont pas de
provenance et n'ont jamais été validées avec le schéma actuel (v10). Elles ne sont ni proposées ni protégées comme
telles ; la rétention les garde tant que la phase A n'a pas produit deux retours arrière validés.

### Migration depuis l'override racine non versionné

Ne pas modifier ni supprimer `docker-compose.lody-2d.override.yml` avant la fin de cette procédure.

```bash
cd /srv/moneyprinterturbo
cp -p docker-compose.lody-2d.override.yml ~/docker-compose.lody-2d.override.yml.sauvegarde-$(date +%F)
export LODY_IMAGE_TAG=$(git rev-parse --short=12 HEAD)
docker compose -f docker-compose.lody.yml -f docker-compose.lody-2d.override.yml config > /tmp/ancien.yml
docker compose -f docker-compose.lody.yml -f docker-compose.lody-2d.yml config > /tmp/nouveau.yml
diff -u /tmp/ancien.yml /tmp/nouveau.yml
```

`config` affiche l'environnement résolu : relire le diff **soi-même** et ne pas le copier dans un ticket.
Seules différences attendues : le tag de l'image 2D (`local` → SHA) et, éventuellement, la configuration `build`. Toute autre
différence (variable d'environnement, volume, `shm_size`…) doit être reportée dans `docker-compose.lody-2d.yml` par
une PR **avant** la première livraison `--2d`. Ensuite seulement : `./scripts/lody-deploy.sh --2d`, puis vérification
du rendu 2D. L'override racine reste en place (inutilisé) jusqu'à la troisième livraison validée.

## 3. Phase B — rétention des images : `scripts/lody-image-retention.sh`

**Commande unique de validation humaine du plan** (simulation, ne supprime rien) :

```bash
cd /srv/moneyprinterturbo && sudo -n -E ./scripts/lody-image-retention.sh
```

Autres formes : `--json` (plan complet), `inventory > inventaire.json` (inventaire en lecture seule, rejouable
hors ligne avec `--inventory-file inventaire.json`, jamais applicable), `report` (observabilité), `--apply`.

### Politique

Périmètre : `lody-video-factory`, `lody-video-factory-2d`, `lody-auth`, et les images sans tag portant le label
`lody.component` (images construites après #209). Le moteur MoneyPrinterTurbo, les images sans tag non attribuables
et **tout autre projet** (Dig, Jury Central, RPG Quest…) sont seulement comptés.

Une image est conservée si **au moins une** raison s'applique (le plan affiche toutes les raisons) :

1. utilisée par **un conteneur, actif ou arrêté** (comparaison par identifiant d'image) ;
2. release en service, ou l'un des **2 derniers retours arrière validés** (`--min-rollbacks`, jamais moins de 2) ;
3. **base** d'une image conservée (ses couches sont un préfixe des couches de l'image conservée) ;
4. tag protégé `local` / `latest`, ou aussi étiquetée sous un autre nom (ex. `ghcr.io/…`) ;
5. parmi les **3 plus récentes** de son dépôt (`--keep-recent`) ou âgée de **moins de 7 jours** (`--min-age-days`).

**Fail closed** — `--apply` est refusé (code 3, plan affiché, ligne `ALERTE`) si : aucune release validée au
journal ; moins de 2 retours arrière validés ; le conteneur d'interface en service ne correspond pas à la release
courante (livraison manuelle hors script) ; inventaire incomplet (code 2) ; verrou de livraison tenu ; production ou
rendu actif ou indéterminé ; inventaire hors ligne.

Application : un second inventaire est fait juste avant d'agir (seules les images candidates aux DEUX inventaires
sont retirées), puis `docker image rm <dépôt:tag>` **tag par tag, sans `-f`** : Docker refuse lui-même de retirer une
image utilisée. Une image déjà absente compte comme faite (idempotent) ; un refus est journalisé (code 1). Jamais de
`docker system prune`, `docker image prune`, `docker volume prune` ni `rm -f`.

### Gain : estimé ≠ mesuré

- **Estimé** (avant) : borne basse = somme des tailles **exclusives** (`UniqueSize` de `docker system df -v`, couches
  qui ne servent qu'à cette image) ; borne haute = somme des tailles des images ; plus le nombre de couches qui ne
  serviraient plus à aucune image restante. Les couches partagées (Python, dépendances) ne sont donc pas comptées.
- **Mesuré** (après `--apply`) : différence `df` du disque de Docker avant/après. D'autres projets écrivant sur le
  même disque, la mesure est indicative.

Les 6,6 Go de l'audit restent une **estimation non garantie** jusqu'au premier plan réel.

### Journal

`.lody-ops/retention.jsonl` : une ligne par exécution (mode, release courante, refus, candidates, estimation,
actions, `df` avant/après, sonde d'activité). `.lody-ops/deploy.log` : journal du script de livraison.

## 4. Phase C — intégration post-livraison et observabilité

- Après chaque livraison **validée**, `lody-deploy.sh` lance la rétention **en simulation** sous le même verrou ;
  `--retention-apply` la rend réelle (toujours soumise aux garde-fous ci-dessus), `--no-retention` la saute. Une
  livraison n'échoue jamais à cause de la rétention.
- `./scripts/lody-image-retention.sh report [--worktrees-dir ~ubuntu/lody-orch/worktrees] [--alert-exit]` : disque
  (`df`), images Lody (tags / images uniques), candidates et gain estimé, taille des worktrees ; **alerte à 75 %,
  critique à 85 %** (`--warn`, `--critical`) avec suggestions. `--alert-exit` renvoie 10 (alerte) / 11 (critique)
  pour une supervision.
- Proposition (non installée, décision administrateur) : rapport quotidien par cron root, sans suppression :
  `15 7 * * * cd /srv/moneyprinterturbo && ./scripts/lody-image-retention.sh report --alert-exit >> .lody-ops/report.log 2>&1`.

## 5. Phase D — activation de `--apply`

Conditions : trois livraisons validées par `lody-deploy.sh` (courante + 2 retours arrière), un plan relu par le
propriétaire, le rendu 2D vérifié sur la release courante. Puis :

```bash
sudo -n -E ./scripts/lody-image-retention.sh            # relire le plan
sudo -n -E ./scripts/lody-image-retention.sh --apply    # retrait ; gain mesuré affiché et journalisé
```

Réversibilité : une image retirée se reconstruit depuis son SHA (`git checkout <sha>` puis `lody-deploy.sh`), mais pas
à l'identique au bit près (paquets Debian/PyPI résolus au moment du build) ; c'est pourquoi deux retours arrière
validés restent toujours sur le disque.

## 6. Worktrees LODYORCH : `deploy/lodyorch/worktree_gc.py`

Outil **séparé** du superviseur : `lodyorch.py` est en cours de refonte (#207) et n'est pas modifié ; il lit la même
configuration (`LQ_WORKDIR`, `LQ_STATE_DIR`, `LQ_REPO`, `LQ_LAUNCH_DIR`, `LQ_GH`) et la base d'état en lecture seule.

```bash
python3 ~/lody-orch/repo/deploy/lodyorch/worktree_gc.py            # simulation : décision et raisons par worktree
python3 ~/lody-orch/repo/deploy/lodyorch/worktree_gc.py --apply    # retrait des seuls worktrees éligibles
```

Éligible seulement si : worktree Git du dépôt Lody (jamais Digorch ni un autre dépôt) ; aucune session ouverte et
ticket non `RUNNING` ; copie propre (non suivis compris) sur une branche ; commits présents sur `origin/<branche>` ou
`origin/main` ; **aucune PR ouverte** et ticket fermé ou PR fusionnée ; inactif depuis 24 h. GitHub injoignable,
`git fetch` en échec ou base illisible ⇒ tout est conservé. Retrait par `git worktree remove` **sans `--force`** ; la
branche est conservée et la commande de recréation est journalisée (`worktree-gc.jsonl`). Verrou `worktree-gc.lock`.

Les 14 worktrees de l'audit ont tous une PR en attente : aucun n'est éligible aujourd'hui, c'est attendu. Un
déclenchement automatique (timer du superviseur) est à décider avec #205/#207, une fois la séparation des
permissions en place.

## 7. Hors périmètre — propositions soumises à décision

- **Cache BuildKit (audit : 9,6 Go, 0,33 Go déclarés récupérables)** : partagé par tous les projets de l'hôte. Aucun
  changement de `/etc/docker/daemon.json`, aucune purge globale. Proposition : d'abord `docker buildx du --verbose`
  et `docker buildx ls` (le builder par défaut gère-t-il un GC ?) ; si le propriétaire l'approuve, une purge bornée
  `docker builder prune --filter until=168h --keep-storage 5GB` hors des heures de build des autres projets. Risque :
  builds plus lents pour tous les projets ; aucun risque pour les conteneurs en service.
- **Images MoneyPrinterTurbo** (5 builds locaux + amont `:latest`) : `report` les liste avec leurs conteneurs. Décision
  attendue : quel tag fait foi pour `moneyprinterturbo-api` / `-webui`, et combien de versions garder ; ensuite seulement,
  une extension explicite de la liste des dépôts (non livrée).
- **`/tmp/dig-*` (≈ 16 Go)** : relève de Dig, jamais touché ici.

## 8. Commandes administrateur minimales et risques

| Commande (hôte, root ou groupe docker) | Effet | Risque |
|---|---|---|
| `./scripts/lody-deploy.sh --2d --dry-run` | aucun | aucun |
| procédure « Migration depuis l'override racine » | lecture + copie de sauvegarde | aucun |
| `./scripts/lody-deploy.sh --2d` | livraison tracée | redémarre l'interface (comme avant) ; refusé si rendu actif |
| `./scripts/lody-image-retention.sh` | aucun (plan) | aucun |
| `./scripts/lody-image-retention.sh --apply` | retrait d'images candidates | refusé tant que la phase A n'est pas faite |
| `python3 deploy/lodyorch/worktree_gc.py --apply` | retrait de worktrees éligibles | branche conservée, recréation journalisée |

## 9. Tests

`test/ops/` (rétention, journal des releases, sonde d'activité, script de livraison de bout en bout sur un dépôt Git
temporaire avec un faux `docker`) et `test/lodyorch/test_worktree_gc.py` (vrais dépôts Git temporaires, faux `gh`).
Aucun Docker, aucun réseau, aucun droit root.
