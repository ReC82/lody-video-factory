# Paramètres système : clés d'API du moteur (ticket #25)

Permet de saisir/remplacer depuis Lody les clés que le moteur utilise (script OpenAI, images OpenAI, voix et
musique ElevenLabs) sans jamais les rendre visibles sur le site, et sans que quiconque ait à éditer `config.toml`
à la main. Une valeur enregistrée n'est **jamais** réaffichée : ni sur la page, ni dans un journal, ni dans le
rapport de capacités (au-delà de ses 4 derniers caractères, calculés côté hôte et jamais journalisés).

## ⚠️ Verrou de mise en ligne — à lire avant d'activer quoi que ce soit

**`video.lodylands.com` n'a aujourd'hui aucune authentification** (vérifié : le vhost nginx fait un simple
`proxy_pass` vers `127.0.0.1:8601`, sans `auth_basic` ni rien d'équivalent). La page « Paramètres système » ne
doit **jamais** être accessible tant que ce n'est pas corrigé : n'importe qui atteignant le site pourrait sinon
remplacer les clés d'API du moteur.

C'est pourquoi la fonctionnalité est **désactivée par défaut** et gardée à deux niveaux :

1. `LODY_ENABLE_SYSTEM_SETTINGS` (variable d'environnement du conteneur `lody-ui`) : absente/`0` → la route
   `?vue=systeme` redirige silencieusement vers l'accueil (`webui/lody/app.py`).
2. Aucun bouton ni lien, nulle part dans l'interface, ne pointe vers cette page — elle n'est atteignable qu'en
   connaissant l'URL exacte.

**Ne mets `LODY_ENABLE_SYSTEM_SETTINGS: "1"` dans `docker-compose.lody.yml` qu'après avoir ajouté une
authentification devant `video.lodylands.com`** (ex. `auth_basic` nginx, ou une protection équivalente).

## Architecture

```
┌─────────────────────────┐        secrets/secrets.toml (0600)        ┌──────────────────────────────┐
│   Lody (conteneur)      │ ───────── écrit UNIQUEMENT ────────────▶  │  secrets/ (bind mount rw)     │
│  view_system_settings   │                                           │  secrets.toml, .reload, status│
│  secrets_store.py       │ ◀──── lit secrets-status.json (jamais ────┤                               │
└─────────────────────────┘         de secret dedans)                 └──────────────┬────────────────┘
                                                                                       │ secrets.reload modifié
                                                                                       ▼
                                                                        ┌──────────────────────────────┐
                                                                        │ systemd.path (hôte, hors      │
                                                                        │ conteneur) — PathModified=     │
                                                                        └──────────────┬────────────────┘
                                                                                       ▼ déclenche
                                                                        ┌──────────────────────────────┐
                                                                        │ lody-secrets-reload.service    │
                                                                        │  scripts/lody-apply-secrets.sh │
                                                                        │  → apply_secrets.py            │
                                                                        │  1. valide la forme            │
                                                                        │  2. sauvegarde config.toml      │
                                                                        │     (vérifiée par empreinte)   │
                                                                        │  3. patch chirurgical            │
                                                                        │     (toml_patch.py, 1 champ)   │
                                                                        │  4. sudo docker restart api     │
                                                                        │  5. sain ? → rapport, ready    │
                                                                        │     pas sain ? → restaure,      │
                                                                        │     redémarre, revérifie        │
                                                                        │     (rolled_back / critical)   │
                                                                        └──────────────────────────────┘
```

**Ce que Lody (le conteneur) peut et ne peut jamais faire** : il écrit dans `secrets/` (un tout petit dossier,
`rw`), lit `secrets-status.json` (jamais de secret dedans) et lit le rapport de capacités existant (déjà utilisé
pour le préflight). Il n'a et n'aura **jamais** d'accès en écriture à `config.toml`, ni au socket Docker — voir
`docker-compose.lody.yml` (`cap_drop: ALL`, pas de `docker.sock`). Tout ce qui redémarre le moteur tourne
**hors de tout conteneur**, sur l'hôte, avec les mêmes permissions `sudo -n docker` déjà utilisées par les autres
scripts (`lody-repair-render.sh`, etc.).

## Champs gérés (`webui/lody/generation/secrets_fields.py`)

| Champ | Fournisseur | Emplacement dans `config.toml` |
|---|---|---|
| Clé de script | OpenAI | `[app] openai_api_key` |
| Clé d'images | OpenAI | `[app] openai_image_api_keys` (liste à un élément) |
| Fournisseur de script actif | Moteur | `[app] llm_provider` — **liste blanche stricte** : `("openai",)` pour ce MVP |
| Clé voix et musique | ElevenLabs | `[elevenlabs] api_key` |

Volontairement limité aux fournisseurs que le catalogue de Lody (`webui/lody/catalog.py`) propose réellement.
Étendre la liste : ajouter une entrée à `secrets_fields.FIELDS` (et, pour un nouveau `llm_provider`, à
`LLM_PROVIDER_CHOICES` — jamais de valeur libre).

## Installation (une fois, sur l'hôte)

```sh
cd /srv/moneyprinterturbo
mkdir -p secrets
chmod 700 secrets
setfacl -m u:10001:rwx secrets            # 10001 = utilisateur du conteneur lody-ui
sudo cp deploy/systemd/lody-secrets-reload.path deploy/systemd/lody-secrets-reload.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lody-secrets-reload.path
```

Vérifier : `systemctl status lody-secrets-reload.path` (actif, en attente) ; `systemctl start
lody-secrets-reload.service` déclenche une application manuelle (sans effet si `secrets/secrets.toml` est vide —
c'est un « noop » sûr, utile pour diagnostiquer).

## États affichés

`Enregistrement…` → `Redémarrage du moteur…` → puis, par champ :

- **Configurée** (éventuellement « se termine par XXXX ») — le rapport de capacités confirme la clé active.
- **Absente** — rien de configuré.
- **Invalide** — forme refusée (vide, espace, saut de ligne, trop long, ou `llm_provider` hors liste blanche) :
  rien n'a été écrit, l'ancienne valeur reste active.
- **Invalide — ancienne configuration restaurée** — la nouvelle valeur avait une forme correcte mais le moteur
  n'est pas revenu sain après redémarrage : retour arrière **automatique et complet** (voir ci-dessous).
- **Erreur critique** — le moteur ne répond plus même après la tentative de restauration : intervention
  manuelle nécessaire (voir Dépannage).

## Retour arrière automatique (une clé qui casse le démarrage du moteur)

1. Avant toute écriture, `config.toml` est copié tel quel dans `config.toml.rollback` (empreinte SHA-256
   vérifiée : si la copie ne correspond pas, rien n'est modifié — état critique immédiat).
2. Le champ est patché, le moteur redémarre, sa santé est vérifiée (`GET /docs`, jusqu'à 60 s).
3. Si le moteur ne redevient pas sain : `config.toml.rollback` (revérifié par empreinte) remplace `config.toml`,
   le moteur redémarre une seconde fois, sa santé est revérifiée.
4. Sain après restauration → **rolled_back**, le rapport de capacités est régénéré, l'ancienne configuration est
   à nouveau active. Toujours pas sain → **critical**, aucune troisième tentative automatique.

Aucune boucle : au plus deux redémarrages par application. Aucun message, à aucune étape, ne contient la valeur
de la clé (voir `webui/lody/generation/apply_secrets.py`, docstring du module).

## Rollback (revenir en arrière sur la fonctionnalité elle-même)

```sh
sudo systemctl disable --now lody-secrets-reload.path lody-secrets-reload.service
```

`config.toml` n'est jamais modifié par ce rollback (seule l'application de futures clés s'arrête). Redéployer une
image précédente laisse la page inatteignable de toute façon (`LODY_ENABLE_SYSTEM_SETTINGS` resterait absent).

## Dépannage (état critique)

Le moteur ne répond plus après une tentative de restauration automatique :

1. `sudo docker logs moneyprinterturbo-api --tail 100` — la cause n'est presque jamais la clé elle-même (déjà
   restaurée) mais une panne indépendante du redémarrage.
2. `diff config.toml config.toml.rollback` — doivent être identiques après une restauration réussie ; sinon,
   restaurer manuellement : `cp config.toml.rollback config.toml && sudo docker restart moneyprinterturbo-api`.
3. Une fois le moteur sain : `./scripts/lody-engine-report.sh`.

## Limites

- Un seul champ « clé d'images » : plusieurs clés OpenAI Images simultanées ne sont pas prises en charge par
  cette page (le champ `config.toml` reste une liste, mais cette page n'y écrit qu'un seul élément).
- `llm_provider` : un seul choix pour ce MVP (`openai`). Étendre la liste blanche si Lody supporte un jour un
  second fournisseur de script.
- Ne remplace pas l'authentification du site — c'est un prérequis séparé, bloquant.
