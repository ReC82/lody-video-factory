# Mise en service de la fusion automatique sur AWS (#205)

> **Statut au 13/11/2026 : non activée.** Décision du propriétaire du même jour : identité GitHub
> unique (`ReC82`), installation et recette en `dry-run` d'abord, mode réel en attente d'une
> correction technique de la séparation des pouvoirs (§2), protection de `main` progressive (§3).
> Constat fait en lecture seule par une session lodyorch,
> qui n'a ni root, ni Docker, ni systemd, ni le droit de piloter le superviseur, de poser un label ou
> de modifier la protection de `main` (crochet de garde). Toutes les étapes ci-dessous sont donc à
> lancer **par le propriétaire**, dans l'ordre. Rien n'a été installé, fusionné ni déployé.
>
> Politique et fonctionnement : [lody-fusion-auto.md](lody-fusion-auto.md) (#193, #200).

## 1. Constat (13/11/2026, lecture seule)

| Élément | Constat | Conséquence |
|---|---|---|
| `main` | `031c345` (#202/#204), après `8eddf4f` (#200/#203) et `b9f71ae` (#198/#201) | le code de la fusion sécurisée est sur `main` |
| Superviseur installé (`/opt/lodyorch/lodyorch.py`) | empreinte identique à `lodyorch.py` de `b9f71ae` / `c14732b`, **différente** de `8eddf4f` / `031c345` | la version installée **ne contient pas #200** : aucun `watch_merges`, aucun mode `automerge` ; à réinstaller |
| Labels `merge:auto` / `merge:manual` | **absents** du dépôt | `lodyorch labels --apply` à lancer après réinstallation |
| Protection de `main` | aucune (404), aucun ruleset | rien côté GitHub n'impose la CI |
| Check `CI result` | présent et `success` sur `031c345` (avec `Detect code changes`, 3.11/3.12/3.13, Windows) | candidat stable disponible pour `branch_protection.py` |
| PR ouvertes (14 : #138, #145, #147–#149, #158, #161, #163, #177–#182) | **aucune** n'a `CI result` sur son SHA de tête (CI antérieure à #198) ; aucune n'a de label | exiger `CI result` maintenant **bloquerait toutes les PR ouvertes** (voir §3) ; aucune n'est fusionnable par le superviseur (aucun `merge:auto`) |
| Compte GitHub du jeton `gh` de l'hôte | `ReC82`, **administrateur** du dépôt — le même que le propriétaire | l'origine d'un label `merge:auto` n'est pas prouvable (voir §2) |
| Réglages dépôt | `allow_auto_merge: false`, merge commit et squash autorisés | sans effet : le superviseur fusionne par `PUT pulls/<n>/merge` avec SHA attendu |

Production (`/srv/moneyprinterturbo`, conteneurs, volume `lody-video-factory-data`) : invisible
pour la session, **non inspectée** ; SHA déployé et rendus en cours restent à relever par le
propriétaire (§4, étape 1).

## 2. Identité GitHub unique : décision du propriétaire et risque résiduel

**Décision du 13/11/2026** ([commentaire du propriétaire sur #205](https://github.com/ReC82/lody-video-factory/issues/205#issuecomment-6848306621)) :
**pas de second compte GitHub**. Le superviseur et les sessions gardent l'identité `ReC82`. La
mise en service n'est plus conditionnée à un compte machine ; la séparation des pouvoirs se fait
au niveau des processus et de l'hôte, et les garde-fous existants restent en place.

### 2.1 Ce que la garde actuelle garantit — et ce qu'elle ne garantit pas

`merge_policy.py` accepte `merge:auto` si le dernier ajout du label sur le ticket vient d'un compte
de `LQ_MERGE_TRUSTED` (par défaut `ReC82`). Avec un compte partagé, le journal GitHub montre
`ReC82` quel que soit le processus qui a posé le label : **l'auteur humain du label n'est pas
authentifiable**. C'est un risque résiduel assumé, pas une garantie.

| Barrière | Nature | Limite |
|---|---|---|
| Le superviseur ne pose jamais `merge:auto` | code relu (`lodyorch.py`) | — |
| Les tickets `agent:auto` ne donnent aucun droit de fusion | `merge_policy.py` (`autorisation-merge-auto`) | — |
| Crochet de garde des sessions (`guard.py`) | refus par **motifs de commande** (`gh pr merge`, `pulls/<n>/merge`, `/labels`, `lodyorch …`) | son en-tête le dit : « un garde-fou contre les erreurs, pas un bac à sable ». Le processus de la session exécute `gh` avec le jeton `ReC82` (admin) ; une formulation que les motifs ne couvrent pas resterait techniquement possible. **Non testé volontairement** (contourner le garde est interdit aux sessions). |
| CI sur le SHA courant, veto `merge:manual`, chemins sensibles, non-rétroactivité, audit, kill switch | `merge_policy.py` + superviseur | protègent contre une fusion **non conforme**, pas contre un label posé par le mauvais processus |

**Conclusion (point 4 du plan révisé)** : les sessions Claude peuvent techniquement atteindre
l'API GitHub avec le même jeton administrateur ; seule la garde logicielle les en empêche. Le mode
réel (`on`) reste donc **en attente** ; `dry-run` peut être installé et testé tout de suite.

### 2.2 Correction technique proposée, sans second compte (ticket séparé à créer)

Objectif : qu'une session ne détienne plus aucun jeton capable de fusionner ou de poser un label
indiscernable de celui du propriétaire. Pistes compatibles avec une identité unique, à arbitrer
dans un ticket dédié (toutes demandent une configuration root ponctuelle de l'hôte) :

1. **Utilisateur Unix séparé pour le superviseur** (ex. `lodyorch`) : seul lui lit le jeton
   `ReC82` (répertoire de configuration de `gh` en `0700`) ; les sessions tournent sous un autre
   utilisateur, sans accès à ce répertoire. Indispensable à toutes les variantes ci-dessous.
2. **Jeton de session restreint** fourni par le superviseur à chaque lancement (`GH_TOKEN`) :
   - **jeton fine-grained de `ReC82`** limité au dépôt avec *Contents : read* (le push des branches
     passe par une **clé de déploiement** en écriture) ; sans *Contents : write*, `PUT …/merge` est
     refusé par GitHub. Reste possible : poser un label (*Issues : write* est nécessaire aux
     commentaires) — label toujours attribué à `ReC82` ;
   - **ou application GitHub** appartenant à `ReC82` (ce n'est pas un compte) : jeton d'installation
     d'une heure, actions attribuées à `<app>[bot]` ; un `merge:auto` posé par une session devient
     discernable et rejeté par `LQ_MERGE_TRUSTED=ReC82`. Combiné à un ruleset « Restrict updates »
     sur `main` avec le seul rôle administrateur en contournement, l'app ne peut pas fusionner.
3. Le superviseur garde son propre jeton (`ReC82`) pour les seules opérations prévues : labels de
   suivi, fusion en mode `on` après tous les contrôles.

Recommandation de la session : **application GitHub + utilisateur Unix séparé** (seule variante qui
rend l'origine du label discernable). Tant que ce ticket n'est pas livré, rester en `dry-run`.

## 3. Protection de `main` : approche progressive (voie A, décidée)

`python3 deploy/lodyorch/branch_protection.py` (à blanc) proposerait d'exiger `CI result` avec
`enforce_admins: true`. Les 14 PR ouvertes n'ont pas ce check sur leur SHA de tête : GitHub les
déclarerait non fusionnables, **y compris pour le propriétaire**, jusqu'à un nouveau push
(une relance de l'ancien run réutilise l'ancien `ci.yml`, sans `CI result`).

**Voie A retenue par le propriétaire (13/11/2026)** : la protection est appliquée **après** mise à
jour des PR historiques à conserver (`git merge origin/main` → nouveau run avec `CI result`) ou
leur fermeture. Elle ne dépend plus d'un compte machine. Tant qu'elle n'est pas posée,
`merge_policy.py` vérifie lui-même `CI result` sur le SHA courant avant toute fusion.

## 4. Procédure pour le propriétaire (dans l'ordre)

Chaque étape a son retour arrière. Ne pas passer à la suivante si une vérification échoue.

1. **État initial** (aucun changement) : relever le SHA PROD (`/api/version`), `docker ps`, les tâches
   vidéo actives ; sauvegarder la base SQLite Lody et `/opt/lodyorch` (copie datée), ainsi que
   `state.db` du superviseur.
2. **Réinstaller le superviseur** depuis `main` (`031c345` ou plus récent) avec
   `deploy/lodyorch/install.sh`, en gardant `LQ_AUTO_MERGE=dry-run` dans l'env installé, puis
   `lodyorch reload`. Vérifier : `lodyorch status` (service actif), `lodyorch automerge` → mode
   `dry-run`. Retour arrière : réinstaller la sauvegarde de `/opt/lodyorch`, `lodyorch reload`.
3. `lodyorch labels --apply` → les labels `merge:auto` et `merge:manual` existent
   (`gh label list | grep merge`). Retour arrière : supprimer les deux labels.
4. **Vérifier que les sessions ne peuvent pas fusionner** : depuis une session, `gh pr merge` et
   `gh api -X PUT …/pulls/<n>/merge` doivent être refusés par le crochet (déjà constaté pour
   `lodyorch …` et les labels dans cette session).
5. **Recette à blanc** (mode `dry-run`) :
   - (a) PR factice sûre : **la PR de ce ticket** (documentation seule, `docs/*.md`, aucune zone
     exclue) ; poser `merge:auto` sur #205 depuis le compte du propriétaire → commentaire
     « Simulation : serait fusionnée » attendu, **aucune** fusion ;
   - (b) veto : poser `merge:manual` sur #205 → verdict « refusée » (`aucun-veto`) ; puis retirer.
     Une PR touchant `deploy/` (ex. #138) avec `merge:auto` sur son ticket → refus `infra-ci-cd` ;
   - (c) non-rétroactivité : vérifiée à l'étape 6 (label posé avant `automerge on` → refus
     `autorisation-apres-activation`) ;
   - (d) arrêt : `lodyorch automerge off` → plus aucun verdict de fusion ; puis `dry-run`.
6. **Mode réel** — **en attente** de la correction technique du §2.2 (le refus d'un second compte
   n'autorise pas à retirer les protections). Une fois livrée : `LQ_AUTO_MERGE=on` dans l'env
   installé, réinstaller, `lodyorch reload`, puis `lodyorch automerge on`. Le `merge:auto` posé à
   l'étape 5 est antérieur : verdict attendu « refusée — autorisation antérieure »
   (non-rétroactivité). Retirer puis reposer `merge:auto` sur #205 → fusion réelle de la PR factice,
   ticket rouvert et gardé `status:needs-test`, aucun déploiement. Kill switch :
   `lodyorch automerge off`, vérifier `lodyorch automerge`, puis `on`.
7. **Protection de `main`** (voie A du §3, indépendante de l'étape 6) : après actualisation ou
   fermeture des PR historiques, `branch_protection.py --apply`. Retour arrière :
   `gh api -X DELETE repos/ReC82/lody-video-factory/branches/main/protection`.
8. Rapport : coller dans #205 les sorties de `lodyorch automerge`, le verdict, le SHA de fusion et
   le mode final.

## 5. Livraison applicative séparée (#189, #191, #202)

Fusionnés sur `main`, déploiement PROD non vérifié par la session. À livrer **séparément** de
l'activation de la fusion automatique, en une seule fois :

| Ticket | Commit `main` | Fichiers applicatifs | Données |
|---|---|---|---|
| #189 brouillons persistants | `2089f4c` | `compose_drafts.py`, `view_production.py`, `view_animation_pilot.py`, `db.py` | **migration additive** : nouvelle table `production_compose_drafts` (création « si absente ») |
| #191 voix enregistrées | `f33f98b` | `voice_picker.py`, `view_characters.py`, `view_settings.py` | aucune |
| #202 lieux détaillés | `031c345` | `locations.py`, `view_locations.py`, `project_transfer.py`, `generation/narrative_context.py` | aucune migration (limite applicative relevée à 8 000 caractères) |

Proposition : déployer l'image de `031c345` (ou du dernier `main` vert) **hors rendu en cours**,
après sauvegarde du volume `lody-video-factory-data` ; vérifier `/api/version` = SHA attendu,
page d'accueil 200, ouverture d'une production existante, d'un personnage avec voix, d'un lieu ;
retour arrière par l'image précédente + restauration de la sauvegarde si la table ajoutée posait
problème (elle est ignorée par l'ancienne version).
