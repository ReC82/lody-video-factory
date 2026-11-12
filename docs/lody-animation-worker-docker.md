# Worker 2D Remotion en Docker — pilote DEV protégé (#154)

> Ticket #154. Suite de #120 (fournisseur et worker), #150 (moteur `renderers/remotion`) et #152.
> **Pilote de DEV uniquement** : ce document ne change ni l'image, ni le compose, ni le volume de production,
> et ne demande aucun redémarrage de l'application vidéo en service.

## Ce qui est livré

| Fichier | Rôle |
|---|---|
| `Dockerfile.lody-2d` | Image du worker 2D, construite **au-dessus** de l'image Lody (`Dockerfile.lody`, inchangé) : Node 22 épinglé par empreinte, `npm ci` sur le `package-lock.json`, Chrome headless shell de la version fixée par Remotion, bundle webpack préconstruit, polices Noto, utilisateur `lody` non-root. Jamais publiée. |
| `Dockerfile.lody-2d.dockerignore` | Contexte de build : celui de l'image Lody (sans `config.toml`, `secrets/`, `.env`, `storage/`, `data/`), sans les `node_modules` locaux, plus le seul storyboard de la démo #150. |
| `docker-compose.lody-dev-2d.yml` | Instance Lody **séparée** : projet `lody-dev-2d`, conteneur `lody-dev-2d`, volume `lody-dev-2d-data`, port `127.0.0.1:8611`. |
| `webui/lody/animation/preflight.py` | Préflight du worker : `python -m lody.animation.preflight [--json]`. |
| `webui/lody/animation/pilot.py`, `webui/lody/view_animation_pilot.py` | Atelier 2D de l'interface : import de catalogue, démo Voltix #150, préflight. |
| `.github/workflows/worker-2d.yml` | CI : build des deux images, préflight sous plafonds, tests du moteur, **un rendu 2D complet** sans réseau. |

## Isolement du pilote

- **Aucun secret** : ni `config.toml`, ni `secrets/`, ni `.env`, ni `storage/` du moteur historique ne sont montés ;
  le pilote ne rejoint pas le réseau `moneyprinterturbo_default`. Il ne peut appeler aucun fournisseur payant.
  Le préflight le vérifie (noms de variables sensibles, jamais leurs valeurs ; `config.toml` ; `/secrets`).
- **Aucun autre projet** : base SQLite, catalogue d'assets et dossiers de rendu vivent dans le volume
  `lody-dev-2d-data`, distinct de `lody-video-factory-data`. Le worker #120 isole en plus chaque tâche par projet.
- **Système de fichiers en lecture seule** sauf `/data` (volume du pilote), `/tmp` (tmpfs 1 Go) et
  `/home/lody` (tmpfs 64 Mo) ; `cap_drop: ALL`, `no-new-privileges`.
- Le moteur de scène reçoit un environnement minimal (`PATH`, `HOME`, langue) : aucune variable de l'application.

## Ressources, journaux, arrêt et reprise

- Plafonds du conteneur : **1,5 CPU, 1,8 Go (sans swap), 256 processus**. Le worker rend une scène à la fois
  (verrou global), en priorité basse (`nice`), avec un délai par scène (`LODY_ANIMATION_SCENE_TIMEOUT`, 900 s).
- Journaux : `worker.log` (nettoyé, sans secret) et `manifest.json` dans le dossier de chaque tâche
  (`/data/animation/<projet>/<tâche>/`) ; journaux Docker tournants `json-file` 3 × 10 Mo.
- **Annulation** : drapeau `cancel` posé dans le dossier de la tâche → arrêt entre deux scènes (état `cancelled`).
- **Reprise** : `docker compose … stop` (SIGTERM) → état `interrupted` ; au redémarrage, les scènes déjà rendues
  et intactes (empreinte vérifiée) ne sont jamais refaites.
- Le moteur ne télécharge rien à l'exécution : Chrome et le bundle sont dans l'image. Correctif #154 :
  `lody-remotion` se place dans son propre dossier avant de lancer Remotion, qui cherchait sinon Chrome à côté
  du dossier de la tâche et l'aurait retéléchargé (~100 Mo) à chaque rendu.

### Mesures (11/11/2026, hôte de l'orchestrateur : 2 vCPU, 3,8 Go, hors Docker)

Démo Voltix #150 complète (3 scènes, 19,5 s, 586 images 1080×1920) par la vraie chaîne
fournisseur → worker → `renderers/remotion`, une scène à la fois :

| Mesure | Valeur |
|---|---|
| Préparation (`--prepare` : Chrome présent, bundle construit) | 28 s, 125 % CPU, 0,65 Go |
| Rendu complet | **132 s** (≈ 6,8 × le temps réel), ≈ 99 % CPU (un cœur) |
| Pic mémoire (worker + Node + Chrome + ffmpeg, somme RSS) | **≈ 1,13 Go** |
| Scènes | 37 s (5,6 s), 30 s (5,6 s), 45 s (8,4 s) |
| Arrêt SIGTERM pendant la scène 2, puis reprise | scène 1 reprise du cache (`cached: true`), scènes 2–3 rendues ; 96 s |
| Processus orphelins après l'arrêt | aucun (Chrome arrêté avec le moteur) |

Le plafond de 1,8 Go laisse ≈ 0,6 Go de marge sur ce pic. La mesure dans le conteneur plafonné (1,5 CPU)
est faite par le workflow « Worker 2D » (une scène) : voir la CI de la PR.

## Licence Remotion — décision du propriétaire

**Jamais supposée gratuite.** Usage prévu : rendu automatisé côté serveur de vidéos 2D avec `@remotion/renderer`
4.0.542 pour les productions Lody ; pilote DEV en évaluation, aucune vidéo publiée. La licence Remotion n'est
gratuite que dans certains cas (particuliers, très petites structures, organisations à but non lucratif, selon
les termes en vigueur) ; au-delà, une licence entreprise est exigée, avec une offre spécifique pour le rendu
automatisé. Conditions à relire sur <https://www.remotion.dev/license> au moment de la décision.

| `LODY_REMOTION_LICENSE` | `LODY_REMOTION_EVALUATION` | Effet |
|---|---|---|
| vide | absent | **Rendu refusé** (préflight « Licence Remotion à décider par le propriétaire »). Cas de la production. |
| vide | `1` | Rendu permis pour **évaluation DEV**, avertissement affiché. Posé par `docker-compose.lody-dev-2d.yml`. |
| `free:<motif>` | — | Le propriétaire a vérifié l'éligibilité à l'usage gratuit (motif écrit, ex. date de vérification). |
| `company:<référence>` | — | Licence entreprise acquise (référence de commande, jamais une clé). |
| autre valeur | — | Refusée (jamais interprétée comme un accord). |

## Installation DEV (à lancer par le propriétaire, sur une machine de DEV)

```bash
git switch agent/154-worker-remotion-docker          # ou main une fois la PR fusionnée
docker build -f Dockerfile.lody -t lody-video-factory:local .          # image de base (non déployée)
docker compose -f docker-compose.lody-dev-2d.yml build
docker compose -f docker-compose.lody-dev-2d.yml run --rm lody-dev-2d python -m lody.animation.preflight
docker compose -f docker-compose.lody-dev-2d.yml up -d
ssh -L 8611:127.0.0.1:8611 <hôte>                     # puis http://127.0.0.1:8611
docker compose -f docker-compose.lody-dev-2d.yml down # arrêt ; le volume lody-dev-2d-data est conservé
```

Sur un hôte où tourne la production, ces commandes ne touchent ni `lody-video-factory-ui`, ni
`lody-video-factory-data`, ni le port 8601 ; elles construisent toutefois des images et consomment CPU/RAM
pendant le rendu : à lancer seulement avec l'accord du propriétaire, en dehors des heures de production.

Sans Docker (poste de DEV) : `cd renderers/remotion && npm ci && node bin/lody-remotion.mjs --prepare`, puis
`export LODY_ANIMATION_RENDERER="node $PWD/bin/lody-remotion.mjs" LODY_REMOTION_EVALUATION=1`.

## Prévisualiser la démo #150 depuis l'interface

1. Créer un projet, puis **Paramètres → Général → Mode de rendu : Animation 2D** (ou Hybride).
2. **Nouvelle production → Atelier 2D** :
   - « Vérifier le worker 2D » : liste des contrôles (moteur, Chrome, bundle, ffmpeg, ressources, isolement,
     licence) ; attendu : aucun point bloquant, licence en avertissement « évaluation DEV » ;
   - « Installer la démo Voltix #150 » : importe le catalogue Voltix (#136) et le schéma coaxial, fichiers
     recopiés depuis l'image après vérification d'empreinte ; le storyboard de la démo devient celui de la
     production ;
   - ou « Importer le catalogue » avec un fichier `lody.animation-catalog` exporté d'un autre projet.
3. Saisir un sujet, « Préparer la génération » (préflight du fournisseur : tout prêt, coût 0 €), puis confirmer.
4. Suivi : la vidéo (≈ 2 à 3 min de rendu) apparaît comme une production classique.

Un storyboard **figé dans un autre projet** (comme `docs/animation/voltix-150/storyboard.json`) peut aussi être
importé tel quel : ses assets sont retrouvés dans le projet par **empreinte de contenu** (jamais par nom).

## Limites

- Démo **muette** dans Lody : la voix provisoire (Piper) et la musique de la vidéo de référence #150 étaient
  déposées par le script de démo ; la voix dans le fournisseur 2D dépend de #153 (ElevenLabs, accord de coût).
- L'image n'a pas été construite sur l'hôte de l'orchestrateur (pas de Docker) : le build et le rendu en
  conteneur sont vérifiés par le workflow « Worker 2D » de la PR.
- Arrêt d'une tâche depuis l'interface (bouton) non livré : drapeau `cancel` ou arrêt du conteneur.
