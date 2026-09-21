# Lody Video Factory — interface et déploiement

Interface Streamlit **indépendante** (`webui/Lody.py`) organisée par **projets** :
création, modification, archivage, page de projet et brief de production. L'ancienne
WebUI MoneyPrinterTurbo (`webui/Main.py`) et l'API du moteur restent dans le dépôt
et peuvent tourner à côté (ports 8501 / 8080), mais ne sont plus l'identité du produit.

| Élément | Valeur |
|---|---|
| Compose | `docker-compose.lody.yml` (projet `lody-video-factory`) |
| Image / conteneur | `lody-video-factory:<tag>` (Debian 13, `Dockerfile.lody`) / `lody-video-factory-ui` |
| Port | `127.0.0.1:8601` (nginx `video.lodylands.com` y pointe) |
| Projets | SQLite `/data/lody.sqlite3`, volume nommé `lody-video-factory-data` |
| Configuration | `./config.toml` monté en lecture seule — **jamais copié dans l'image** |
| Stockage | `./storage` monté en lecture seule (vidéos produites par le moteur de génération) |
| Génération | voir [`lody-generation.md`](lody-generation.md) et [`lody-engine-contract.md`](lody-engine-contract.md) |

## Commandes

```bash
# Build + démarrage (racine du dépôt). Le tag = hash Git, pour pouvoir revenir en arrière.
export LODY_IMAGE_TAG=$(git rev-parse --short HEAD)
docker compose -f docker-compose.lody.yml up -d --build

docker compose -f docker-compose.lody.yml ps                       # état + santé
docker compose -f docker-compose.lody.yml logs -f --tail=100       # logs
docker compose -f docker-compose.lody.yml restart                  # redémarrage
docker compose -f docker-compose.lody.yml down                     # arrêt (SANS -v : le volume garde les projets)
```

`config.toml` doit exister à la racine (`cp config.example.toml config.toml` sur une
installation neuve), sinon Docker crée un *dossier* de ce nom.

## Paramètres d'un projet et brief

Chaque projet a sa page **Paramètres** (`?projet=<id>&vue=parametres`) : nom, description, langue,
format, durée cible, public, orientation, ton, style visuel, rythme de narration et rythme visuel
(scènes par minute), voix ElevenLabs (nom + identifiant, saisis à la main : lister les voix
appellerait le service), structure type et consignes permanentes.

- Stockage : colonnes SQLite pour les champs communs, bloc JSON `settings["brief"]` (validé par
  `webui/lody/brief.py`) pour le reste — **séparément pour chaque projet**, sans changement de schéma.
- La page « Nouvelle production » assemble automatiquement le **brief final** (demande saisie +
  paramètres du projet) et l'affiche avant toute génération, avec une version texte copiable.
  Les repères « mots estimés » et « scènes estimées » sont calculés (débit 130/155/175 mots par
  minute selon le rythme ; scènes = durée × scènes par minute) : ce sont des estimations.
- Mise à niveau : au démarrage, `LodyCrypto` reçoit son bloc `brief` s'il n'en a pas encore ; un champ
  n'est remplacé (ton, style visuel) que s'il porte encore sa valeur d'origine. `Audiovisuel` n'est
  jamais modifié (ses anciennes valeurs sont lues telles quelles).

## Données et secrets

- Les projets (nom, langue, format, ton, style, plateformes, **identifiants** de fournisseurs, voix)
  sont dans SQLite. **Aucune clé API n'y est stockée** : un formulaire ou une API qui en reçoit une
  la refuse (`webui/lody/secrets_guard.py`).
- L'interface n'affiche que « Prêt » / « Clé à configurer » selon la présence d'une clé dans
  `config.toml` ou l'environnement ; jamais la valeur.
- Sauvegarde de la base (copie cohérente dans le volume, puis vers l'hôte) :

```bash
docker compose -f docker-compose.lody.yml exec lody-ui python3 -c \
  "import sqlite3; sqlite3.connect('/data/lody.sqlite3').backup(sqlite3.connect('/data/sauvegarde.sqlite3'))"
docker cp lody-video-factory-ui:/data/sauvegarde.sqlite3 ./sauvegarde-lody.sqlite3
```

## Retour arrière

```bash
docker image ls lody-video-factory                                   # tags disponibles
LODY_IMAGE_TAG=<ancien-tag> docker compose -f docker-compose.lody.yml up -d --no-build
```

Le schéma SQLite est versionné (`PRAGMA user_version`) : une version plus ancienne de
l'application ouvre sans erreur une base créée par une version plus récente tant que
les colonnes existantes ne changent pas.

Repli complet vers l'ancienne WebUI : dans le vhost nginx, remettre
`proxy_pass http://127.0.0.1:8501;` puis `nginx -t && systemctl reload nginx`.

## Développement et tests

```bash
uv run streamlit run webui/Lody.py --server.enableStaticServing=true --theme.base=dark
uv run python -m pytest -q test/lody          # aucun appel réseau ni fournisseur payant
```

Variables : `LODY_DATA_DIR` (défaut `./data`), `LODY_CONFIG_PATH`, `LODY_SEED_DEFAULTS=0`
(pas d'exemples initiaux), `LODY_TIMEZONE` (défaut `Europe/Paris`).

## Structure

- `webui/Lody.py` : point d'entrée · `webui/lody/app.py` : routage par URL (`?projet=…&vue=…`).
- `projects.py` (modèle, validation, dépôt SQLite) — indépendant de Streamlit ;
  `seeds.py` (exemples Audiovisuel / LodyCrypto, insérés une seule fois) ; `catalog.py` (listes de choix) ;
  `provider_status.py`, `secrets_guard.py`, `settings.py`.
- `view_*.py` (pages), `components.py`, `theme.py` + `styles.css`.
- `webui/static/fonts/` : police Inter (SIL OFL 1.1), servie localement.
