# Paramètres système : clés d'API du moteur (ticket #25)

Permet de saisir/remplacer depuis Lody les clés que le moteur utilise (script OpenAI, images OpenAI, voix et
musique ElevenLabs) sans jamais les rendre visibles sur le site, et sans que quiconque ait à éditer `config.toml`
à la main. Une valeur enregistrée n'est **jamais** réaffichée : ni sur la page, ni dans un journal, ni dans le
rapport de capacités (au-delà de ses 4 derniers caractères, calculés côté hôte et jamais journalisés).

Trois vérifications indépendantes, dans cet ordre, avant qu'une clé soit annoncée « Configurée » :

1. **forme** (vide, espaces, sauts de ligne, longueur, `llm_provider` en liste blanche) ;
2. **redémarrage sain du moteur** (le processus redémarre sans planter) ;
3. **validité réelle auprès du fournisseur** (appel minimal, gratuit, non générateur) — une clé expirée ou
   inventée passe les étapes 1 et 2 sans problème ; seule l'étape 3 la détecte.

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
┌─────────────────────────┐   secrets/secrets.toml (0640, groupe lody-secrets)  ┌──────────────────────────────┐
│   Lody (conteneur,      │ ───────── écrit UNIQUEMENT ─────────────────────▶   │  secrets/ (bind mount rw,     │
│   uid 10001)             │                                                     │  setgid, 2770)                │
│  view_system_settings   │                                                     │  secrets.toml, .reload, status│
│  secrets_store.py       │ ◀──── lit secrets-status.json (jamais de secret) ───┤                               │
└─────────────────────────┘                                                     └──────────────┬────────────────┘
                                                                                                  │ secrets.reload
                                                                                                  │ modifié
                                                                                                  ▼
                                                                                   ┌──────────────────────────────┐
                                                                                   │ systemd.path (hôte, hors      │
                                                                                   │ conteneur) — PathModified=,   │
                                                                                   │ vérifié fiable sur ce rename  │
                                                                                   │ atomique (voir plus bas)      │
                                                                                   └──────────────┬────────────────┘
                                                                                                  ▼ déclenche
                                                                                   ┌──────────────────────────────┐
                                                                                   │ lody-secrets-reload.service    │
                                                                                   │  User=ubuntu (jamais root)      │
                                                                                   │  → apply_secrets.py            │
                                                                                   │  1. valide la forme            │
                                                                                   │  2. sauvegarde config.toml      │
                                                                                   │     (vérifiée par empreinte)   │
                                                                                   │  3. patch chirurgical (1 champ)│
                                                                                   │  4. sudo docker restart api     │
                                                                                   │  5. sain ? → appel minimal      │
                                                                                   │     auprès du fournisseur       │
                                                                                   │     accepté → ready             │
                                                                                   │     refusé (401/403) → retour   │
                                                                                   │       arrière (comme 6)         │
                                                                                   │     injoignable/délai → gardée, │
                                                                                   │       « non vérifiée »          │
                                                                                   │  6. pas sain → restaure,        │
                                                                                   │     redémarre, revérifie        │
                                                                                   │     (rolled_back / critical)    │
                                                                                   └──────────────────────────────┘
```

**Ce que Lody (le conteneur) peut et ne peut jamais faire** : il écrit dans `secrets/` (un tout petit dossier,
`rw`, groupe partagé — voir *Modèle de permissions* ci-dessous), lit `secrets-status.json` (jamais de secret
dedans) et lit le rapport de capacités existant (déjà utilisé pour le préflight). Il n'a et n'aura **jamais**
d'accès en écriture à `config.toml`, ni au socket Docker — voir `docker-compose.lody.yml` (`cap_drop: ALL`, pas
de `docker.sock`). Tout ce qui redémarre le moteur ou appelle un fournisseur tourne **hors de tout conteneur**,
sur l'hôte, avec les mêmes permissions `sudo -n docker` déjà utilisées par les autres scripts
(`lody-repair-render.sh`, etc.), sous l'utilisateur `ubuntu` — **jamais root**.

## Modèle de permissions (conteneur uid 10001 ↔ service hôte `ubuntu`)

Un simple ACL sur le dossier ne suffit pas : il ne s'applique pas aux NOUVEAUX fichiers créés dedans. Le modèle
retenu est un **groupe système partagé**, avec le bit setgid sur le dossier :

- groupe dédié `lody-secrets` (créé par `scripts/lody-setup-secrets-group.sh`) ;
- `secrets/` : `chmod 2770` (setgid + `rwxrws---`) — **aucun accès pour « autre »** — groupe `lody-secrets` ;
- `ubuntu` (utilisateur du service systemd) membre de `lody-secrets` ;
- le conteneur Lody reçoit le GID de `lody-secrets` comme groupe supplémentaire (`group_add` dans
  `docker-compose.lody.yml`, résolu depuis `LODY_SECRETS_GID` dans `.env` — jamais codé en dur, chaque hôte a
  son propre GID) ;
- tout fichier créé dans `secrets/` (par le conteneur OU par le service hôte) hérite automatiquement du groupe
  `lody-secrets` (setgid) et est écrit en **`0640`** (propriétaire lecture/écriture, groupe lecture seule,
  aucun accès pour « autre ») ;
- le service systemd tourne en `User=ubuntu` — **jamais en root** ; seule la commande `docker restart`, déjà
  autorisée sans mot de passe pour cet utilisateur, a besoin d'un privilège particulier ;
- `config.toml`, le socket Docker et tout le reste du dépôt restent hors d'atteinte du conteneur (inchangé).

### Installation (une fois, sur l'hôte)

```sh
cd /srv/moneyprinterturbo
sudo ./scripts/lody-setup-secrets-group.sh          # crée le groupe, ajoute ubuntu, prépare secrets/ (2770)
# affiche : GID de lody-secrets = <N> — à recopier :
echo "LODY_SECRETS_GID=<N>" >> .env                 # lu par docker-compose.lody.yml (group_add)

sudo cp deploy/systemd/lody-secrets-reload.path deploy/systemd/lody-secrets-reload.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lody-secrets-reload.path
```

**Vérification d'intégration réelle** (pas simulée : reproduit l'uid 10001 du conteneur et un utilisateur tiers
`nobody`) :

```sh
sudo ./scripts/lody-verify-secrets-permissions.sh
```

Prouve qu'un fichier créé avec l'uid 10001 en `0640` **est** lisible par `ubuntu` (membre du groupe) et **n'est
pas** lisible par `nobody` (hors du groupe). Exécuté sur cet hôte au moment d'écrire cette doc : les deux
vérifications passent. À relancer après toute modification du groupe ou des permissions.

Sans `LODY_SECRETS_GID`, `docker compose` refuse de démarrer avec un message explicite plutôt que de démarrer
sans le groupe (vérifié avec un vrai `docker compose config`) — voir
`test_compose_refuses_to_start_without_lody_secrets_gid_set`.

## `systemd.path` déclenche-t-il fiablement sur un remplacement atomique (`os.replace`) ?

Oui — **vérifié empiriquement sur cet hôte**, pas seulement documenté : une unité `.path` de test a surveillé un
fichier avec `PathModified=`, et trois remplacements atomiques consécutifs (fichier temporaire dans le même
dossier + `os.chmod` + `os.replace`, exactement la méthode utilisée par `secrets_store._atomic_write`) ont
chacun déclenché le service en moins d'une seconde, y compris le tout premier remplacement alors que le fichier
surveillé n'existait pas encore. `PathModified=` regarde en réalité le dossier parent (pas seulement l'inode du
fichier), ce qui est précisément ce qui permet de détecter un remplacement par renommage. Aucun changement de
directive n'était donc nécessaire.

## Vérification réelle des clés auprès du fournisseur

Un redémarrage sain du moteur **ne prouve rien** sur la validité d'une clé (le moteur ne l'utilise qu'à la
prochaine génération). Après un redémarrage sain, `apply_secrets.py` fait donc un appel minimal, gratuit et
**qui ne génère jamais rien**, pour chaque clé modifiée :

| Fournisseur | Appel | Ce qu'il prouve |
|---|---|---|
| OpenAI (script, images) | `GET {base_url}/models` avec `Authorization: Bearer <clé>` | authentification acceptée ; liste des modèles, aucune génération |
| ElevenLabs (voix, musique) | `GET https://api.elevenlabs.io/v1/user` avec `xi-api-key: <clé>` | authentification acceptée ; endpoint officiel d'info de compte, aucun audio généré |

Changer `app.llm_provider` vers `openai` déclenche aussi cette vérification sur la clé OpenAI **déjà en place**
(sinon rien ne garantirait qu'elle fonctionne pour ce nouveau fournisseur actif).

**Trois issues, jamais confondues :**

- **acceptée** (2xx) → la clé passe à *Configurée*.
- **refusée** (401/403 précisément) → traité EXACTEMENT comme un moteur pas sain : retour arrière automatique
  complet (voir plus bas). Un 5xx ou une réponse inattendue n'est PAS traité comme un refus (voir ligne
  suivante) : seul un refus d'authentification explicite déclenche le retour arrière.
- **fournisseur injoignable, ou délai dépassé** → **jamais confondu avec une clé invalide**. La nouvelle
  configuration reste appliquée (le moteur, lui, est sain) mais affichée *Configurée (non vérifiée auprès du
  fournisseur)*, avec un message distinct selon qu'il s'agit d'un vrai délai dépassé ou d'une panne/erreur
  réseau. Aucun retour arrière sur ce cas : une panne passagère du fournisseur ne doit pas annuler un changement
  par ailleurs valide.

Contraintes respectées : timeout court (8 s par défaut, `--provider-timeout`) ; la clé ne transite JAMAIS dans
l'URL, toujours dans un en-tête ; aucune redirection HTTP n'est suivie (un en-tête d'authentification ne doit
jamais atteindre un hôte différent de celui prévu — testé avec un serveur local) ; aucun message d'erreur ni de
statut ne contient jamais la clé, ni un extrait de la réponse du fournisseur.

## Champs gérés (`webui/lody/generation/secrets_fields.py`)

| Champ | Fournisseur | Emplacement dans `config.toml` |
|---|---|---|
| Clé de script | OpenAI | `[app] openai_api_key` |
| Clé d'images | OpenAI | `[app] openai_image_api_keys` (liste à un élément) |
| Fournisseur de script actif | Moteur | `[app] llm_provider` — **liste blanche stricte** : `("openai",)` pour ce MVP |
| Clé voix et musique | ElevenLabs | `[elevenlabs] api_key` |

Volontairement limité aux fournisseurs que le catalogue de Lody (`webui/lody/catalog.py`) propose réellement.
Étendre la liste : ajouter une entrée à `secrets_fields.FIELDS` (et, pour un nouveau `llm_provider`, à
`LLM_PROVIDER_CHOICES` — jamais de valeur libre), puis une route de vérification dans `_real_verify_key`.

## États affichés

`Enregistrement…` → `Redémarrage du moteur…` → `Vérification auprès du fournisseur…` → puis, par champ :

- **Configurée** (éventuellement « se termine par XXXX ») — le rapport de capacités confirme la clé active ET
  le fournisseur l'a acceptée.
- **Configurée (non vérifiée auprès du fournisseur)** — moteur sain, mais le fournisseur n'a pas pu confirmer
  la clé (délai dépassé ou injoignable) : appliquée quand même, à re-vérifier plus tard.
- **Absente** — rien de configuré.
- **Invalide** — forme refusée (vide, espace, saut de ligne, trop long, ou `llm_provider` hors liste blanche) :
  rien n'a été écrit, l'ancienne valeur reste active.
- **Refusée par le fournisseur — ancienne configuration restaurée** — la clé a une forme correcte et le moteur
  redémarre, mais le fournisseur répond 401/403 : retour arrière **automatique et complet**.
- **Invalide — ancienne configuration restaurée** — le moteur lui-même n'est pas revenu sain après redémarrage
  (ou une autre clé du même enregistrement a été refusée) : même retour arrière automatique.
- **Erreur critique** — le moteur ne répond plus même après la tentative de restauration : intervention
  manuelle nécessaire (voir Dépannage).

## Retour arrière automatique (une clé qui casse le démarrage du moteur, ou refusée par le fournisseur)

1. Avant toute écriture, `config.toml` est copié tel quel dans `config.toml.rollback` (empreinte SHA-256
   vérifiée : si la copie ne correspond pas, rien n'est modifié — état critique immédiat).
2. Le champ est patché, le moteur redémarre, sa santé est vérifiée (`GET /docs`, jusqu'à 60 s).
3. Si sain : la clé est vérifiée auprès du fournisseur (voir ci-dessus). Un refus (401/403) déclenche le même
   retour arrière que si le moteur n'était pas sain.
4. Si le moteur n'est pas revenu sain, OU si le fournisseur a refusé la clé : `config.toml.rollback` (revérifié
   par empreinte) remplace `config.toml`, le moteur redémarre une seconde fois, sa santé est revérifiée — sans
   revérifier l'ancienne clé auprès du fournisseur (elle fonctionnait déjà avant).
5. Sain après restauration → **rolled_back** (ou **rejected** pour le champ précisément refusé), le rapport de
   capacités est régénéré, l'ancienne configuration est à nouveau active. Toujours pas sain → **critical**,
   aucune troisième tentative automatique.

Aucune boucle : au plus deux redémarrages et un seul appel de vérification fournisseur par application. Aucun
message, à aucune étape, ne contient la valeur de la clé (voir `webui/lody/generation/apply_secrets.py`,
docstring du module).

## Rollback (revenir en arrière sur la fonctionnalité elle-même)

```sh
sudo systemctl disable --now lody-secrets-reload.path lody-secrets-reload.service
```

`config.toml` n'est jamais modifié par ce rollback (seule l'application de futures clés s'arrête). Redéployer une
image précédente laisse la page inatteignable de toute façon (`LODY_ENABLE_SYSTEM_SETTINGS` resterait absent).
Le groupe `lody-secrets` peut rester en place sans risque (inerte si le service est désactivé).

## Dépannage (état critique)

Le moteur ne répond plus après une tentative de restauration automatique :

1. `sudo docker logs moneyprinterturbo-api --tail 100` — la cause n'est presque jamais la clé elle-même (déjà
   restaurée) mais une panne indépendante du redémarrage.
2. `diff config.toml config.toml.rollback` — doivent être identiques après une restauration réussie ; sinon,
   restaurer manuellement : `cp config.toml.rollback config.toml && sudo docker restart moneyprinterturbo-api`.
3. Une fois le moteur sain : `./scripts/lody-engine-report.sh`.
4. Permissions douteuses (le service ne peut plus lire `secrets/`) : `sudo
   ./scripts/lody-verify-secrets-permissions.sh` pour diagnostiquer précisément.

## Limites

- Un seul champ « clé d'images » : plusieurs clés OpenAI Images simultanées ne sont pas prises en charge par
  cette page (le champ `config.toml` reste une liste, mais cette page n'y écrit qu'un seul élément).
- `llm_provider` : un seul choix pour ce MVP (`openai`). Étendre la liste blanche si Lody supporte un jour un
  second fournisseur de script (et ajouter sa vérification dans `_real_verify_key`).
- La vérification fournisseur porte sur l'AUTHENTIFICATION, pas sur le quota ou les droits d'un modèle
  particulier : une clé valide mais sans crédit peut donc passer « Configurée » et échouer plus tard à la
  génération.
- Ne remplace pas l'authentification du site — c'est un prérequis séparé, bloquant.
