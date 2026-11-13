# Isolation des permissions de LODYORCH, sans second compte GitHub (#207)

> **Statut : code livré par PR, rien n'est installé ni activé.** Le mode réel de la fusion
> automatique (`on`) reste interdit tant que la recette du §6 n'est pas passée sur AWS. Sans broker
> installé, le superviseur plafonne lui-même `on` à `dry-run`.
>
> Contexte : [lody-fusion-auto.md](lody-fusion-auto.md) (politique, #193/#200) et
> [lody-fusion-auto-mise-en-service.md](lody-fusion-auto-mise-en-service.md) (constat #205).

## 1. Problème

Avant #207, le superviseur `lodyorch` **et** les sessions Claude tournent sous le compte Unix
`ubuntu`, avec le même `gh` authentifié comme `ReC82`, **administrateur** du dépôt. Le crochet de
garde (`guard.py`) refuse des motifs de commande, mais ce n'est pas une frontière de privilèges :
le processus d'une session peut techniquement lire le jeton et appeler `PUT …/merge`, poser
`merge:auto` (indiscernable du propriétaire) ou modifier la protection de `main`.

## 2. Modèle retenu

| Identité | Qui l'utilise | Où est le secret | Peut | Ne peut pas |
|---|---|---|---|---|
| **Application GitHub** `lody-agent-rec82` (appartient à `ReC82`, ce n'est pas un compte) | sessions Claude **et** superviseur (`/opt/lodyorch/bin/gh`, assistant Git) | jeton d'installation d'1 h dans `/var/lib/lodyorch-merge/agent/token` (0640, lisible par `ubuntu`), renouvelé par le broker | pousser des branches `agent/*`, ouvrir/mettre à jour des PR, commenter, poser les labels de suivi, lire la CI | mettre à jour `main` (ruleset §3), administrer le dépôt (rulesets, protection, collaborateurs, secrets), pousser `.github/workflows/*` ; **ses labels sont attribués à `lody-agent-rec82[bot]`, jamais à `ReC82`** |
| **Jeton fine-grained de `ReC82`** (broker) | uniquement l'utilisateur Unix système `lodyorch-merge` | `/etc/lodyorch/merge-token` (0640 `root:lodyorch-merge`) | lire la PR, sa CI, le journal du ticket ; `PUT …/merge` avec SHA attendu | rien d'autre : aucun commentaire, aucun label, aucune administration |
| **`ReC82` humain** | le propriétaire, depuis le navigateur / son poste | hors de l'hôte | poser `merge:auto` / `merge:manual`, fusionner, administrer | — |

Pourquoi l'**application** (option A du ticket) plutôt qu'un **second jeton fine-grained** de
`ReC82` pour les sessions (option B) : un jeton fine-grained agit **en tant que `ReC82`** ; un
label posé par une session serait indiscernable de celui du propriétaire, et un jeton ayant
*Contents : write* (nécessaire pour pousser les branches) peut fusionner une PR. L'application rend
l'origine discernable (`[bot]`) **et** reste soumise au ruleset, sans compte supplémentaire.

### 2.1 Permissions exactes

- **Application** (réglages de l'app, installation « Only select repositories » →
  `lody-video-factory`) : *Contents : Read and write* ; *Pull requests : Read and write* ;
  *Issues : Read and write* ; *Checks : Read* ; *Actions : Read* ; *Metadata : Read*.
  **Pas** d'*Administration*, de *Workflows*, de *Secrets*, d'*Environments*. Le broker demande
  en plus ces permissions **explicitement** à chaque jeton (restreint au seul dépôt) et refuse
  d'écrire un jeton qui en aurait davantage.
  *Contents : write* reste nécessaire : les sessions doivent pousser leurs branches.
  Conséquence : une session ne peut pas pousser une modification de `.github/workflows/` ; le
  propriétaire le fait lui-même (ou accorde *Workflows : write* en connaissance de cause).
- **Jeton du broker** (fine-grained, propriétaire `ReC82`, dépôt `lody-video-factory` seul,
  expiration ≤ 1 an) : *Contents : Read and write* (exigé par `PUT …/merge`) ; *Pull requests :
  Read* ; *Issues : Read* ; *Checks : Read* ; *Metadata : Read*.

### 2.2 Qui peut poser ou retirer `merge:auto`

| Acteur | Peut poser ? | Effet sur la fusion |
|---|---|---|
| Propriétaire (`ReC82`, web) | oui | **seule** autorisation valable (`LQ_MERGE_TRUSTED`) |
| Session / superviseur (application) | techniquement oui (*Issues : write*) | **refusé** : acteur `…[bot]` (critère `autorisation-merge-auto`), même si le bot était ajouté à `LQ_MERGE_TRUSTED` |
| Action faite « au nom de » `ReC82` via une application (`performed_via_github_app`) | — | **refusée** |
| Retrait d'un veto (`merge:manual`, `agent:blocked`, `agent:human`, `do-not-merge`) par l'application | techniquement oui | **le veto reste valable** (critère `veto-leve-par-compte-autorise`) |

Ce qui reste invérifiable avec un compte partagé : **quel humain** se cache derrière `ReC82`. Le
modèle distingue « le propriétaire » de « l'automate », pas deux personnes.

## 3. Protections indépendantes

1. **Ruleset `lodyorch-main-guard`** (`deploy/lodyorch/ruleset-main.json`) sur la branche par
   défaut : *Restrict updates*, *Restrict deletions*, *Block force pushes* ; contournement
   **uniquement** pour le rôle *Repository admin* (`ReC82` : le propriétaire et le jeton du
   broker). L'application ne peut ni fusionner une PR, ni pousser sur `main`, quel que soit le
   code exécuté sur l'hôte. Indépendant de la protection progressive « `CI result` requis »
   ([lody-fusion-auto-mise-en-service.md §3](lody-fusion-auto-mise-en-service.md)), qui reste à
   poser plus tard (voie A).
2. **Utilisateur Unix `lodyorch-merge`** : seul lecteur de `/etc/lodyorch` (secrets) et de son
   journal. `lodyorch.service` rend en plus `/etc/lodyorch`, le journal du broker et les sockets
   qui donnent root sans `sudo` (Docker, containerd, **LXD** — `ubuntu` est membre du groupe `lxd`)
   **invisibles** pour le superviseur et ses sessions ; `NoNewPrivileges` rend `sudo` inopérant.
3. **Le broker ne fait confiance à aucune demande** : il recollecte tout avec son jeton, applique
   la politique installée par root (`/opt/lodyorch/merge_policy.py`) avec **sa** configuration
   (`/etc/lodyorch/merge-broker.env`, root), son **plafond root** (`LQ_AUTO_MERGE`), un **armement
   non rétroactif** (date de modification de ce fichier : une demande ne peut que la retarder),
   le **SHA attendu** et un **journal idempotent** (jamais deux `PUT …/merge` pour un SHA).
   Une session qui dépose une fausse demande n'obtient que ce que le propriétaire a déjà autorisé.
4. **Environnement des sessions nettoyé** (`session_env`) : aucune variable `GH_TOKEN`,
   `GITHUB_TOKEN`, `GH_CONFIG_DIR`, `SSH_AUTH_SOCK`, ni aucune variable dont le nom évoque un secret
   (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*API_KEY*`…) n'est transmise à une session.
5. **Crochet de garde** (défense en profondeur seulement) : refuse en plus toute mention des
   secrets du broker, la surcharge de l'identité GitHub (`GH_TOKEN=`, `GH_CONFIG_DIR=`…) et les
   appels `gh api` aux rulesets, à la protection de branche, aux collaborateurs, clés et webhooks.

### 3.1 Limites assumées

- Le jeton d'agent est lisible par les sessions : c'est **leur** identité, sans droit sur `main`.
  Une session pourrait l'utiliser hors de `gh` ; elle n'obtiendrait rien de plus.
- Une session peut toujours **supprimer ou renommer** la définition d'un label de veto (*Issues :
  write*). Le veto principal reste l'absence de `merge:auto` humain, et le kill switch root.
- Une PR déjà autorisée par `merge:auto` peut recevoir des commits d'une session de correction CI
  avant fusion (comportement #200 : l'autorisation porte sur le ticket). Amélioration possible :
  lier l'autorisation au SHA relu (ticket séparé).
- La console tmux interactive du propriétaire tourne aussi sous `ubuntu` **hors** du service : après
  migration, son `gh` n'a plus le jeton administrateur (les actions d'administration se font depuis
  le navigateur, ou `sudo` hors session Claude).

## 4. Procédure administrateur (une intervention root, ~20 min)

Pré-requis : PR #207 fusionnée, aucune session à interrompre (`lodyorch status` : idle, ou
attendre la fin de la session courante — rien ci-dessous ne la coupe avant l'étape 7).

1. **Sauvegarde** : `sudo cp -a /opt/lodyorch ~/backups/lodyorch/opt-$(date +%F)` et copie de
   `~/.local/state/lodyorch/state.db`.
2. **Ruleset** (avec le `gh` administrateur actuel, AVANT l'étape 6) :
   `gh api -X POST repos/ReC82/lody-video-factory/rulesets --input deploy/lodyorch/ruleset-main.json`
   puis vérifier dans *Settings → Rules → Rulesets* : cible = branche par défaut, contournement =
   *Repository admin* seul.
3. **Application GitHub** (*Settings → Developer settings → GitHub Apps → New*) : nom
   `lody-agent-rec82` (sinon reporter le slug dans `LQ_MERGE_PR_AUTHORS`, §5), webhook désactivé,
   permissions du §2.1, « Only on this account » ; générer une **clé privée** (.pem) ; installer
   l'app sur le seul dépôt `lody-video-factory`. Relever *App ID* et *Installation ID* (URL de la
   page d'installation).
4. **Jeton du broker** (*Settings → Developer settings → Fine-grained tokens*) : permissions du §2.1.
5. **Installation** depuis un clone à jour de `main` :
   `sudo deploy/lodyorch/install.sh --isolation`, puis déposer les secrets **sans les afficher** :
   `sudo install -o root -g lodyorch-merge -m 0640 <fichier.pem> /etc/lodyorch/app-key.pem`,
   `sudo install -o root -g lodyorch-merge -m 0640 /dev/null /etc/lodyorch/merge-token` puis
   `sudo tee /etc/lodyorch/merge-token >/dev/null` (coller le jeton, Entrée, Ctrl-D ; les droits
   0640 sont conservés), et renseigner `LQ_APP_ID` / `LQ_APP_INSTALLATION_ID` dans
   `sudo nano /etc/lodyorch/merge-broker.env` (`LQ_AUTO_MERGE=dry-run`). Supprimer le `.pem`
   téléchargé. Puis `sudo systemctl enable --now lodyorch-merge.timer` ;
   `sudo -u lodyorch-merge /usr/bin/python3 /opt/lodyorch/merge_broker.py status` → jeton
   privilégié lisible : oui, application configurée, jeton d'agent âge < 60 s.
6. **Retirer le jeton administrateur du compte `ubuntu`** : `gh auth logout --hostname github.com`
   (en tant qu'`ubuntu`), puis révoquer ce jeton côté GitHub (*Settings → Applications → GitHub
   CLI*, ou le PAT correspondant). Vérifier : `/usr/bin/gh auth status` → non connecté ;
   `/opt/lodyorch/bin/gh api repos/ReC82/lody-video-factory --jq .full_name` → fonctionne.
7. **Bascule du superviseur entre deux sessions** : `lodyorch stop` (attend la fin de la session en
   cours), puis `sudo systemctl restart lodyorch.service` (nouvelle unité : PATH, chemins masqués),
   `lodyorch resume`. `lodyorch check` : section « Isolation des permissions » tout en ✓.

**Retour arrière** (à tout moment) : `sudo systemctl disable --now lodyorch-merge.timer` ;
`gh auth login` en tant qu'`ubuntu` ; réinstaller la sauvegarde de `/opt/lodyorch` (ou
l'installation précédente) et `sudo systemctl daemon-reload && sudo systemctl restart lodyorch` entre
deux sessions ; dans le clone dédié, `git config --local --unset-all credential.https://github.com.helper`.
Le ruleset peut rester (il ne gêne que les non-administrateurs) ; sinon le supprimer dans
*Settings → Rules*. Aucune donnée Lody n'est concernée.

**Kill switch** : logiciel `lodyorch automerge off` (immédiat, le superviseur ne demande plus
rien) ; matériel `sudo sed -i 's/^LQ_AUTO_MERGE=.*/LQ_AUTO_MERGE=off/' /etc/lodyorch/merge-broker.env`
(le broker refuse toute fusion, sans appel GitHub) ; total `sudo systemctl stop lodyorch-merge.timer`.

## 5. Configuration

| Fichier | Propriétaire | Contenu |
|---|---|---|
| `/opt/lodyorch/lodyorch.env` | root 0644 | `LQ_MERGE_BROKER=/var/lib/lodyorch-merge`, `LQ_MERGE_PR_AUTHORS=ReC82,lody-agent-rec82[bot]`, plafond `LQ_AUTO_MERGE` du superviseur |
| `/etc/lodyorch/merge-broker.env` | root:lodyorch-merge 0640 | plafond root, `LQ_MERGE_TRUSTED`, `LQ_MERGE_PR_AUTHORS`, identifiants numériques de l'app, chemins ; **jamais écrasé** par `install.sh` |
| `/etc/lodyorch/app-key.pem`, `/etc/lodyorch/merge-token` | root:lodyorch-merge 0640 | secrets (jamais copiés, affichés ni sauvegardés par les scripts) |
| `/var/lib/lodyorch-merge/requests` | lodyorch-merge:ubuntu 0770 | demandes du superviseur |
| `/var/lib/lodyorch-merge/results` | lodyorch-merge 0755 | réponses du broker |
| `/var/lib/lodyorch-merge/agent/token` | lodyorch-merge:ubuntu 0640 | jeton d'installation (1 h) |
| `/var/lib/lodyorch-merge/state` | lodyorch-merge 0700 | journal des fusions (invisible au superviseur) |

Mode `on`, une fois la recette passée : `LQ_AUTO_MERGE=on` dans **les deux** fichiers (le plafond
du superviseur par réinstallation de `lodyorch.env`, celui du broker par `sudo nano`, ce qui réarme
la non-rétroactivité), puis `lodyorch reload` et `lodyorch automerge on`.

## 6. Recette sur AWS (à reprendre dans #205)

1. `lodyorch check` : isolation tout en ✓ (aucun jeton dans l'environnement, NoNewPrivileges,
   sockets root inaccessibles, secrets du broker illisibles, jeton d'agent `ghs_` récent, aucun
   identifiant `gh` enregistré pour `ubuntu`).
2. Depuis une session (ou `sudo -u ubuntu` dans le service) : `cat /etc/lodyorch/merge-token` →
   refusé (crochet, puis Unix) ; `/usr/bin/gh auth status` → non connecté ;
   `gh api -X PUT repos/ReC82/lody-video-factory/pulls/<pr factice>/merge -f sha=<tête>` avec le
   jeton d'agent (hors crochet, par le propriétaire) → **refusé par GitHub** (ruleset).
3. Une PR de session est attribuée à `lody-agent-rec82[bot]`.
4. `dry-run` (étape 5 de [lody-fusion-auto-mise-en-service.md](lody-fusion-auto-mise-en-service.md)) :
   `merge:auto` posé par le propriétaire → « Simulation : serait fusionnée » ; le même label posé
   avec le jeton d'agent → refus `autorisation-merge-auto` (… `[bot]`).
5. `on` (PR factice documentaire) : fusion par le broker
   (`sudo -u lodyorch-merge … merge_broker.py status` montre `merged`), ticket gardé « à tester ».
   Kill switch matériel → nouvelle demande refusée `mode`.

## 7. Tests automatiques

`test/lodyorch/test_isolation.py` : politique (bot, application, veto, auteurs), broker (plafond,
kill switch, armement, SHA, idempotence, demandes invalides, jamais root, jeton d'agent 0640 aux
permissions réduites), superviseur (environnement des sessions, plafond sans broker, aller-retour
avec le broker), installation (`--isolation --dry-run` sans secret ni démarrage), unités systemd,
wrappers `gh` / Git, crochet de garde.
