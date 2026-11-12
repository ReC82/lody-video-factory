# Audit de la boucle Docker / Streamlit / CI / déploiement (#192)

Audit réalisé le 12/11/2026 par une session lodyorch, **sans aucune modification** d'infrastructure,
de CI, de sécurité ni de déploiement. Aucun appel payant, aucun rendu vidéo, aucune opération sur la
production.

> **Ce que cette session voyait et ne voyait pas.** Elle a lu le dépôt (`main` à `2089f4c`), interrogé
> l'API GitHub Actions (100 derniers runs, jobs et étapes, journaux horodatés) et lancé lint et tests
> sur l'hôte AWS partagé. Elle n'a **ni Docker, ni sudo, ni accès à `/srv/moneyprinterturbo`** : rien
> de ce qui tourne réellement en production (conteneurs, images, tailles, vhost nginx, `.env`) n'a pu
> être constaté. Toute valeur marquée **« non mesurable ici »** est une inconnue ; la section 8 donne
> les commandes sûres, en lecture seule, pour la mesurer. Aucune durée n'est inventée.

## 1. En bref (pour décider)

1. **Docker n'est pas le goulot.** D'après la CI GitHub, une reconstruction de l'image Lody avec cache
   prend **44 à 62 s** (deux architectures, poussée comprise), et à froid **57 à 81 s**. L'image Lody
   ne contient que Streamlit + Pillow + FFmpeg ; le moteur et le worker 2D sont des images à part.
2. **Le goulot mesuré est la suite pytest de la CI : 10 à 17 min par PR** (médiane 14,3 min sur 49 PR),
   dont **97 %** passés dans `pytest` (≈ 3 150 tests) ; installation et lint : ~15 s.
3. La moitié des PR récentes (**20 sur 40**) ne touchent que `webui/`, `test/lody/`, `docs/` ou
   `resource/` mais relancent quand même les tests du moteur amont (`test/services`, ≈ la moitié du
   temps) et de l'orchestrateur (`test/lodyorch`, ≈ 20 %).
4. **Le changement minimal le plus rentable** : garder Docker en production tel quel, et
   **(a)** rendre la CI de PR ciblée et parallèle (estimation : 14 min → 3 à 5 min, à confirmer par
   une PR d'essai), **(b)** ajouter une instance DEV avec code monté et rechargement Streamlit pour
   voir une modification d'interface en quelques secondes, **(c)** un script de livraison unique
   (sauvegarde SQLite → build → `up -d` → attente santé → affichage du tag de retour arrière).
5. Écart à corriger en premier lieu : **la production tourne en Python 3.12** (`Dockerfile.lody`),
   **la CI teste 3.11 et 3.13**, jamais 3.12.

## 2. Services réels (d'après les fichiers du dépôt)

```
                  Internet (HTTPS)
                        │
                 nginx (hôte AWS, x86_64) ─ TLS, vhost video.lodylands.com   [vhost non lu ici]
          ┌─────────────┼───────────────────────────┐
          │ /           │ /lody-auth/               │ (repli possible : ancienne WebUI 8501)
          ▼             ▼                           ▼
 ┌──────────────────┐ ┌──────────────────┐   ┌───────────────────────────────────────────┐
 │ lody-ui          │ │ lody-auth        │   │ Projet Compose « moneyprinterturbo »       │
 │ 127.0.0.1:8601   │ │ 127.0.0.1:8602   │   │ (docker-compose.engine-local.yml)          │
 │ Streamlit        │─┤ stdlib Python    │   │  api   127.0.0.1:8080  python3 main.py     │
 │ webui/Lody.py    │ │ /verify interne  │   │  webui 127.0.0.1:8501  ancienne WebUI      │
 │ + worker 2D en   │ └──────────────────┘   │  image moneyprinterturbo:local (Dockerfile)│
 │   sous-processus │──── réseau externe moneyprinterturbo_default ──▶ api:8080         │
 └──────────────────┘                        └───────────────────────────────────────────┘
  volumes : config.toml :ro, storage :ro, engine-report :ro, secrets :rw (groupe lody-secrets),
            volume nommé lody-video-factory-data → /data (SQLite lody.sqlite3, WAL)

 Pilote DEV 2D (docker-compose.lody-dev-2d.yml) : lody-dev-2d 127.0.0.1:8611, image lody-video-factory-2d
 (= image Lody + Node + Remotion + Chrome), volume lody-dev-2d-data, sans config.toml ni secrets,
 1,5 CPU / 1,8 Go, système de fichiers en lecture seule. Lancé à la demande (restart: "no").

 Sur l'hôte, hors conteneurs : nginx, unités systemd prévues par deploy/systemd/ (lody-secrets-reload,
 installation non vérifiée), scripts hôte scripts/lody-*.sh, superviseur lodyorch (service systemd, clone dédié).
```

| Élément | Fichier | Image / build | Écrit où |
|---|---|---|---|
| Interface Lody | `docker-compose.lody.yml:20-103`, `Dockerfile.lody` | `lody-video-factory:${LODY_IMAGE_TAG}`, build local, `pull_policy: never` | `/data` (volume), `/secrets` |
| Authentification admin | `docker-compose.lody.yml:108-143`, `Dockerfile.lody-auth` | `lody-auth:${LODY_IMAGE_TAG}`, stdlib seule | rien (mémoire) |
| Moteur MoneyPrinterTurbo | `docker-compose.engine-local.yml`, `Dockerfile` | `moneyprinterturbo:${MPT_IMAGE_TAG}`, Debian 11, `requirements.txt` | `./storage`, `config.toml` (rw) |
| Pilote 2D | `docker-compose.lody-dev-2d.yml`, `Dockerfile.lody-2d` | au-dessus de l'image Lody | `/data` du pilote, `/tmp` tmpfs |
| Image publique GHCR | `.github/workflows/docker-ghcr.yml` | `ghcr.io/rec82/lody-video-factory`, amd64+arm64 | — |

Constats :

- **Ce qui est réellement lancé en production n'a pas pu être vérifié** (lequel des compose du moteur,
  tags en service, présence du pilote 2D). À confirmer avec `docker ps` (section 8).
- L'image GHCR publiée à chaque push sur `main` **n'est pas utilisée** par la production
  (`pull_policy: never`, build local) : elle sert de référence publique, pas au déploiement.
- **Le worker 2D tourne dans le conteneur Lody** (sous-processus lancé par
  `webui/lody/generation/animation_connector.py:93`) : redémarrer l'interface interrompt un rendu 2D
  en cours. Les générations du moteur classique, elles, tournent dans le conteneur `api` et survivent à
  un redémarrage de Lody.
- Hôte partagé (mesuré) : **2 vCPU, 3,8 Go de RAM, disque 96 Go utilisé à 84 % (16 Go libres)**,
  partagé avec d'autres projets. Les images s'accumulent à chaque tag : le disque est la première
  ressource à surveiller.

## 3. Chiffres mesurés

Sources : API GitHub Actions, runs du 06/10/2026 au 12/11/2026 (100 derniers runs) ; 20 derniers runs CI
réussis pour le détail par job ; journal horodaté du run `43216795866` (push `main`) pour la
répartition par fichier de test ; 40 dernières PR fusionnées.

### 3.1 CI GitHub (`.github/workflows/ci.yml`)

| Mesure | Valeur |
|---|---|
| CI complète sur PR (49 runs réussis) | médiane **14,3 min** (10,0 – 16,9) |
| CI complète sur push `main` (18 runs) | médiane **14,1 min** (11,7 – 15,3) |
| Job « Python 3.11 tests » (20 runs) | médiane 806 s (624 – 904) |
| Job « Python 3.13 tests » (20 runs) | médiane 852 s (618 – 993) |
| … dont l'étape `coverage run -m pytest` | 772 s (3.11) / 818 s (3.13) en médiane |
| Job « Windows smoke tests » | médiane 41 s |
| Job « Detect code changes » | médiane 14 s |
| `uv sync --frozen` en CI (cache actif) | ~2 s |
| Attente d'un runner (création → démarrage du job) | 2 à 6 s |
| Run `43216795866` : pytest | 3 117 réussis, 22 ignorés, **588 s** ; checkout → début pytest : 11 s |

Répartition estimée du temps pytest (run `43216795866`, 590 s) — reconstruite à partir des horodatages
des lignes de progression (≈ 72 tests par ligne), donc **approximative à ± une ligne** :

| Dossier | Temps estimé | Remarque |
|---|---|---|
| `test/services` (moteur amont, ancienne WebUI) | ~290 s (49 %) | ne concerne pas une PR qui ne touche que `webui/lody` |
| `test/lody` (application Lody) | ~175 s (30 %) | |
| `test/lodyorch` (orchestrateur) | ~130 s (22 %) | ne concerne que `deploy/lodyorch/` |

Fichiers les plus lents (estimation CI, confirmée localement pour les trois premiers : **82 s** à eux
trois sur l'hôte AWS) : `test/lodyorch/test_consultation.py` (~65 s ; deux tests de 8,5 s avec de vrais
`time.sleep`), `test/services/test_webui_loomloom_regressions.py` (~44 s ; quatre cas paramétrés à ~4 s),
`test/services/test_volcengine_seedance.py` (~39 s), `test_webui_ofox_placement.py`,
`test/lodyorch/test_console.py` (~19 s chacun).

### 3.2 Builds Docker (mesurés en CI, pas sur AWS)

| Mesure | Valeur | Source |
|---|---|---|
| Build + poussée image Lody, 2 architectures, cache GHA | **44 – 62 s** (étape), run complet 1,2 – 1,6 min (médiane 15 runs) | `docker-ghcr.yml` |
| Build image Lody **à froid** (sans cache, amd64) | **57 – 81 s** | `worker-2d.yml`, étape 1 |
| Build image worker 2D à froid (Node, npm ci, Chrome, bundle) | 47 – 67 s | `worker-2d.yml`, étape 2 |
| Préflight + rendu 2D complet d'une scène (réseau coupé) | 22 – 31 s | `worker-2d.yml` |
| Workflow Worker 2D complet | médiane 2,7 min (8 runs) | |

Les runners GitHub ont 4 vCPU et un réseau rapide : **sur l'hôte AWS (2 vCPU partagés) ces durées
seront plus longues, dans une proportion non mesurable ici.**

### 3.3 Boucle de bout en bout

| Étape | Valeur |
|---|---|
| Ouverture → fusion d'une PR (40 dernières) | médiane **0,5 h** (0,2 h – 21,3 h) |
| CI dans cette boucle | ~14 min, soit environ la moitié de la médiane |
| Délai ajouté par le superviseur pour constater la CI | 0 à 5 min (`LQ_CI_POLL=300`, `deploy/lodyorch/lodyorch.env`) |
| Fusion → site à jour | **non mesurable ici** (déploiement manuel sur l'hôte) |
| Build image Lody sur AWS, à chaud / à froid | **non mesurable ici** |
| Redémarrage + santé de `lody-ui` | **non mesurable ici** ; la sonde laisse 15 s de démarrage (`start_period`) puis teste toutes les 30 s |
| Tailles d'images, couches, cache BuildKit, CPU/RAM des conteneurs | **non mesurable ici** |

### 3.4 Boucle locale d'une session (hôte AWS)

| Commande | Durée |
|---|---|
| `uv sync --frozen` (environnement déjà prêt) | 0,03 s |
| `compileall` | 0,6 s |
| `ruff check` | 0,3 s |
| Tests ciblés (`test_compose_drafts.py` + `test_ci_workflow.py`, 27 tests) | 2,8 s |
| Suite complète | arrêtée volontairement : ~2 % après ~1 min, soit plus de 30 min estimées et 2 vCPU pris à la production |

## 4. Chemin d'une modification

« Aujourd'hui » décrit ce que les fichiers et la documentation (`docs/lody-ui.md`) prévoient. Pour
la production, le code est **copié dans l'image** (`Dockerfile.lody:35-43`) : aucune modification
n'est visible sans reconstruire l'image et recréer le conteneur ; le rechargement à chaud de
Streamlit ne joue pas.

| Type de changement | Aujourd'hui | Optimisé (recommandé) |
|---|---|---|
| Libellé / page Streamlit (`webui/lody/*.py`, `styles.css`, `webui/static`) | PR → CI complète ~14 min → fusion → `docker compose -f docker-compose.lody.yml up -d --build` (couches apt/pip en cache, seules les couches de code changent) → redémarrage de `lody-ui` | DEV : rechargement Streamlit en **secondes** sur instance à code monté (option A). PR : CI ciblée `test/lody` + lint (~3-5 min estimées). PROD : script de livraison |
| Module backend Lody (`webui/lody/db.py`, `generation/…`) | Idem | Idem, CI ciblée `test/lody` ; suite complète sur `main` |
| Migration SQLite | Idem ; migration appliquée au premier accès (`webui/lody/db.py:300`) | **Sauvegarde SQLite obligatoire avant** `up -d` (dans le script) ; CI complète |
| Configuration (`config.toml`, `.env`) | Hors image : redémarrage du conteneur concerné suffit (`docker compose … up -d` ou `restart`) — aucun build | Inchangé |
| Clé d'API depuis l'interface | `secrets/` + unité `lody-secrets-reload` sur l'hôte : aucun build | Inchangé |
| Dépendance pip de Lody (`requirements.lody.txt`) | Rebuild de la couche pip (plus long) + workflow Worker 2D | Inchangé ; garder `uv.lock` et `requirements.lody.txt` cohérents (même version de Streamlit vérifiée : 1.59.1) |
| Moteur amont (`app/`, `requirements.txt`) | Rebuild de l'image **moteur** (`Dockerfile`, Debian 11, dépendances lourdes) **et** de l'image Lody (`COPY app`) | Rebuild moteur seulement quand `app/` change ; CI complète |
| Worker Remotion (`renderers/remotion/**`) | Workflow Worker 2D (~2,7 min) + rebuild `lody-video-factory-2d` sur l'hôte | Inchangé : déjà ciblé par filtre de chemins |
| Assets (`resource/`, ~198 Mo) | Couche `COPY resource` invalidée → recopie ~200 Mo | Inchangé (rare) |
| Tests seuls (`test/**`) | CI complète **et** couche `COPY test` de l'image invalidée | Mineur (couche de code, quelques secondes) |
| Orchestrateur (`deploy/lodyorch/`) | CI complète | CI ciblée `test/lodyorch` + lint |
| Documentation seule (`*.md`) | Déjà optimisé : job « Documentation checks » seulement (#127) | Inchangé |

Couplages inutiles repérés :

1. **CI de PR non ciblée** : toute PR non documentaire lance les ~3 150 tests, deux fois (3.11 et 3.13),
   avec couverture. Gain potentiel le plus élevé.
2. **Version Python** : production 3.12, CI 3.11 + 3.13. Une incompatibilité propre à 3.12 passerait.
3. **`COPY test` et `COPY app` dans l'image Lody** : une PR qui ne change que les tests du moteur
   reconstruit quand même la couche de code de Lody. Coût faible (couche de quelques Mo) ;
   `test/` est utilisé par le workflow Worker 2D (`worker-2d.yml:66`), donc à ne retirer qu'avec un
   montage de tests côté CI.
4. **Publication GHCR multi-architecture** à chaque push sur `main` (QEMU + arm64, ~1,5 min) alors
   que l'hôte est x86_64 et ne tire pas l'image. Hors chemin critique (en parallèle de la CI) ;
   à garder si l'image publique reste un engagement.
5. **Rendu 2D dans le conteneur d'interface** : tout déploiement d'interface interrompt un rendu 2D en
   cours. À traiter seulement si le 2D passe en production.

Aucun couplage « UI qui force le rebuild du worker Remotion » : le worker 2D est une image séparée,
reconstruite à la main, et son workflow CI ne se déclenche que sur ses chemins.

## 5. Architecture : comparaison A / B / C

| Critère | **A. Docker partout, code monté en DEV** | B. UI/API sur l'hôte (venv + systemd), worker en Docker | C. Images distinctes UI / API / worker, build ciblé |
|---|---|---|---|
| Boucle d'une modif UI en DEV | **secondes** (`--server.runOnSave=true`) | secondes | inchangée (build de quelques dizaines de s) |
| Changement à faire | un fichier compose DEV (override) | nouvelles unités systemd, venv, permissions, ports, nginx | **déjà en place** pour l'essentiel (lody-ui, lody-auth, moteur, worker 2D) |
| Reproductibilité | identique à PROD (même image, même Python) | dérive de l'hôte (Python système, FFmpeg, bibliothèques) | identique |
| Sécurité | inchangée en PROD (non-root, `cap_drop: ALL`, `no-new-privileges`, secrets :ro) | à reconstruire hors Docker ; hôte partagé avec d'autres projets | inchangée |
| Retour arrière | inchangé (tag d'image) | à inventer (venv versionnés) | inchangé |
| Cohérence DEV/PROD | forte | faible | forte |
| Effort | ~0,5 jour | plusieurs jours + risque | ~0 |

**Recommandation : A pour le DEV, C (l'existant) pour la PROD.** B est écarté : il n'accélère que ce
que A accélère déjà, au prix de la sécurité et du retour arrière. Le build Docker de l'image Lody
coûtant moins d'une minute, le supprimer ne vaut pas la perte de reproductibilité.

## 6. Persistance et sûreté

- **SQLite** (`/data/lody.sqlite3`, volume nommé `lody-video-factory-data`) : migrations ordonnées,
  idempotentes, versionnées par `PRAGMA user_version` (10 migrations à ce jour, dont les brouillons
  #189 en v10), mode WAL. Le volume survit aux rebuilds ; **`docker compose down -v` le détruirait.**
- **Retour arrière** : une ancienne image ouvre une base plus récente tant que les migrations
  n'ont fait qu'ajouter (`docs/lody-ui.md` § Retour arrière). Une migration qui transformerait des
  données casserait cette garantie : sauvegarde systématique avant chaque livraison avec migration.
- **Sauvegarde** : documentée mais manuelle (`docs/lody-ui.md` § Données et secrets). Pas de rotation
  ni de vérification de restauration repérées dans le dépôt.
- **Voix projet/personnage (#191)** : stockées en SQLite ; l'audit ne propose aucun changement
  d'architecture susceptible d'en masquer la cause — le bug est traité dans son propre ticket.
- **Cache TTS et assets** : dans `/data` (pilote 2D) ou `storage/` du moteur ; non inventoriés ici
  (taille non mesurable).
- **Secrets** : jamais dans l'image (`.dockerignore`), `config.toml` monté en lecture seule côté
  Lody, écriture limitée à `secrets/` (groupe dédié). L'option A doit **ne pas monter** `config.toml`
  ni `secrets/` en DEV (comme le pilote 2D).
- **Une seule instance Streamlit** par volume : deux conteneurs sur la même base (WAL) restent
  techniquement possibles mais à éviter ; l'instance DEV doit avoir **son propre volume**.
- **Interruption** : `up -d` recrée `lody-ui` → sessions navigateur reconnectées, brouillon conservé
  (#189), rendu 2D en cours perdu, générations du moteur non affectées.

## 7. Workflow cible et priorités

### 7.1 Trois scénarios pas à pas

**Changement de libellé / UX**
1. DEV : instance à code monté (option A) ; modifier `webui/lody/…` → la page se recharge.
2. `ruff` + tests ciblés `test/lody/<module>` (secondes).
3. PR → CI ciblée (lint, `test/lody`, Python 3.12) → fusion.
4. PROD : `scripts/lody-deploy.sh` (à créer) : tag = SHA, build, `up -d`, attente « healthy ».
5. Test humain sur la page modifiée. Retour arrière : `LODY_IMAGE_TAG=<tag précédent> … up -d --no-build`.

**Bug de persistance Python (ex. #191)**
1. DEV : reproduire sur l'instance DEV (volume DEV, jamais la base de prod).
2. Test unitaire qui échoue puis passe (`test/lody`).
3. PR → CI ciblée ; si migration : CI complète.
4. PROD : sauvegarde SQLite (incluse dans le script) → build → `up -d` → santé.
5. Retour arrière : ancien tag ; si migration additive, la base reste lisible ; sinon restauration de
   la sauvegarde.

**Évolution Remotion**
1. Tests `node --test` + `test_animation_2d_render.py` localement si Docker disponible, sinon par la CI.
2. PR → workflow Worker 2D (déjà ciblé, ~2,7 min) + CI.
3. Rebuild `lody-video-factory-2d` sur l'hôte, préflight, rendu d'une scène de démo (gratuit).
4. Retour arrière : ancien tag de l'image 2D.

### 7.2 Priorités

| # | Action | Effort | Bénéfice attendu | Type |
|---|---|---|---|---|
| 1 | CI de PR ciblée par chemins : `webui/`, `test/lody` → `test/lody` ; `deploy/lodyorch` → `test/lodyorch` ; `app/`, `test/services`, `pyproject.toml`, `uv.lock` → suite complète. Suite complète conservée sur push `main` | 0,5 j | sur 20/40 PR : ~175 s de pytest au lieu de ~590 s (calcul sur le run mesuré) | quick win |
| 2 | `pytest-xdist` (`-n auto`, 4 vCPU sur les runners) après vérification des tests non isolés (ports, fichiers, horloge) | 0,5 – 1 j | **à mesurer** sur une PR d'essai ; l'ordre de grandeur attendu est une division par 2 à 3 | quick win |
| 3 | Ajouter Python **3.12** (version de production) ; 3.11/3.13 et couverture sur `main` seulement | 0,25 j | combler l'écart de version ; un seul job sur PR | quick win |
| 4 | Réduire les vrais `sleep` des tests les plus lents (`test_consultation.py`, `test_webui_loomloom_regressions.py`) par une horloge simulée | 0,5 j | ~1 à 1,5 min par job | quick win |
| 5 | Instance DEV à code monté (`docker-compose.lody-dev.yml`, port distinct, volume distinct, sans secrets, `runOnSave`) | 0,5 j | modification UI visible en secondes, sans CI ni build | structurant léger |
| 6 | Script de livraison PROD (`scripts/lody-deploy.sh`) : sauvegarde SQLite horodatée → build tagué → `up -d` → attente santé → journal des durées → rappel du tag de retour arrière ; nettoyage des vieux tags (disque à 84 %) | 1 j | livraison reproductible **et mesurée** (comble les inconnues de la section 3.3) | structurant |
| 7 | Superviseur : `LQ_CI_POLL` 300 → 60-120 s | 5 min | jusqu'à 4 min de latence en moins par PR | réglage (décision du propriétaire, `deploy/lodyorch/`) |
| 8 | Publication GHCR : amd64 seule, ou seulement sur tag `v*` | 0,25 j | moins de minutes de runner ; pas sur le chemin critique | optionnel |

**Estimation d'économie**, fondée sur les seules mesures CI : une PR « interface Lody » passerait d'une
CI de ~14 min à environ **3 à 5 min** avec (1) + (3), avant même (2) ; soit ~10 min gagnées sur une
boucle médiane d'ouverture → fusion de 30 min. Le gain de (5) (DEV à chaud) et de (6) (livraison)
n'est pas chiffrable sans mesure sur l'hôte.

Tickets indépendants proposés (au propriétaire de les créer) : **(i)** CI de PR ciblée et parallèle +
Python 3.12 (actions 1-4) ; **(ii)** instance DEV à code monté (5) ; **(iii)** script de livraison mesuré
avec sauvegarde et rétention des images (6). Le réglage (7) se fait sans ticket.

## 8. Mesures à faire sur l'hôte (lecture seule, sans perturber la production)

À lancer par le propriétaire (la session n'a pas Docker). Aucune ne redémarre ni ne reconstruit
la production ; aucune n'affiche de secret.

```bash
cd /srv/moneyprinterturbo
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'      # ce qui tourne vraiment
docker image ls --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}' | grep -E 'lody|moneyprinter'
docker history lody-video-factory:<tag-en-service> --format '{{.Size}}\t{{.CreatedBy}}' | head -20
docker system df                                                                 # images, cache BuildKit, volumes
docker stats --no-stream                                                         # CPU / RAM instantanés
docker inspect lody-video-factory-ui --format '{{.State.StartedAt}} {{.State.Health.Status}}'
docker compose -f docker-compose.lody.yml config --services                      # sans afficher l'environnement
# Build À CHAUD sans toucher au conteneur en service (nouvelle étiquette, pas de « up ») :
time docker build -f Dockerfile.lody -t lody-video-factory:mesure-192 .
docker image rm lody-video-factory:mesure-192                                    # ménage
```

Le temps de redémarrage + santé se mesure naturellement lors de la prochaine livraison prévue :
`date; docker compose -f docker-compose.lody.yml up -d; until [ "$(docker inspect -f '{{.State.Health.Status}}' lody-video-factory-ui)" = healthy ]; do sleep 2; done; date`.

## 9. Protocole de validation et retour arrière

- **DEV** : instance distincte (port, volume, aucun secret, aucun fournisseur payant) ; jamais la base
  de production.
- **PR** : CI ciblée verte ; suite complète obligatoire si `app/`, migration, dépendance ou contrat.
- **Avant PROD** : CI complète verte sur `main` (déjà le cas à chaque push) ; sauvegarde SQLite.
- **PROD** : build tagué par SHA, `up -d`, attente « healthy », test humain de la page concernée.
- **Retour arrière** : `LODY_IMAGE_TAG=<tag précédent> docker compose -f docker-compose.lody.yml up -d --no-build`
  (`docs/lody-ui.md` § Retour arrière) ; restauration de la sauvegarde SQLite uniquement si une
  migration non additive a été appliquée.

## 10. Références inspectées

- `Dockerfile.lody`, `Dockerfile.lody.dockerignore`, `Dockerfile.lody-2d`, `Dockerfile.lody-2d.dockerignore`,
  `Dockerfile.lody-auth`, `Dockerfile` (moteur), `requirements.lody.txt`, `pyproject.toml`, `uv.lock`.
- `docker-compose.lody.yml`, `docker-compose.lody-dev-2d.yml`, `docker-compose.engine-local.yml`,
  `docker-compose.yml`.
- `.github/workflows/ci.yml`, `.github/workflows/docker-ghcr.yml`, `.github/workflows/worker-2d.yml`.
- `webui/lody/db.py:285-311` (migrations), `webui/lody/settings.py` (`LODY_DATA_DIR`),
  `webui/lody/generation/animation_connector.py:76-93` (worker 2D en sous-processus).
- `docs/lody-ui.md` (build, sauvegarde, retour arrière), `docs/lodyorch.md` (suivi CI),
  `deploy/lodyorch/lodyorch.env` (`LQ_CI_POLL`).
- Runs GitHub Actions : 100 derniers (06/10 → 12/11/2026) ; détail des jobs des 20 derniers runs CI
  réussis ; journal du job « Python 3.11 tests » du run `43216795866` ; runs Worker 2D `43212364031`,
  `43206169138`, `43204996068`, `43201201482`, `43197462612` ; runs de publication `43257169595`,
  `43216795962`, `43209170124`.
