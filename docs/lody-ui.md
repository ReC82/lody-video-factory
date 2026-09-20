# Lody Video Factory — interface prototype

Nouvelle entrée Streamlit **indépendante** (`webui/Lody.py`) : l'ancienne WebUI
(`webui/Main.py`, port 8501) et l'API (port 8080) ne sont ni modifiées ni arrêtées.
Ce prototype n'appelle **aucun fournisseur** : le bouton « Générer la vidéo » compose
seulement un brouillon de demande en mémoire de session.

| Élément | Valeur |
|---|---|
| Fichier Compose | `docker-compose.lody.yml` (projet `lody-video-factory`) |
| Image / conteneur | `lody-video-factory:<tag>` / `lody-video-factory-ui` |
| Port | `127.0.0.1:8601` (local uniquement ; aucun changement nginx) |
| Volumes | `./config.toml` et `./storage` montés en lecture seule, non versionnés |

## Prérequis

`config.toml` doit exister à la racine (`cp config.example.toml config.toml` sur une
installation neuve, sinon Docker crée un *dossier* de ce nom) et `storage/` aussi.

## Commandes

```bash
# Build + démarrage (depuis la racine du dépôt)
export LODY_IMAGE_TAG=$(git rev-parse --short HEAD)
docker compose -f docker-compose.lody.yml up -d --build

# État, santé et logs
docker compose -f docker-compose.lody.yml ps
docker compose -f docker-compose.lody.yml logs -f --tail=100

# Accès local : http://127.0.0.1:8601   (ex. via tunnel SSH : ssh -L 8601:127.0.0.1:8601 <serveur>)

# Redémarrage
docker compose -f docker-compose.lody.yml restart

# Arrêt (l'ancien service n'est pas concerné : projet Compose distinct)
docker compose -f docker-compose.lody.yml down
```

## Retour arrière

Chaque build est étiqueté avec le hash Git. Pour revenir à une version précédente :

```bash
docker image ls lody-video-factory                      # tags disponibles
LODY_IMAGE_TAG=<ancien-tag> docker compose -f docker-compose.lody.yml up -d --no-build
```

Supprimer complètement le prototype : `down`, puis `docker image rm lody-video-factory:<tag>`.

## Développement local (sans Docker)

```bash
uv run streamlit run webui/Lody.py --server.enableStaticServing=true --theme.base=dark
uv run python -m pytest -q test/lody
```

## Structure

- `webui/Lody.py` : point d'entrée.
- `webui/lody/` : `home.py` (écran), `profiles.py` (projets de démonstration statiques),
  `theme.py` + `styles.css` (thème isolé).
- `webui/static/fonts/` : police Inter (SIL OFL 1.1, licence incluse), servie localement.
