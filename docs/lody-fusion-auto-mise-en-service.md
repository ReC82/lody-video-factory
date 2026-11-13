# Mise en service de la fusion automatique sur AWS (#205)

> **Statut au 13/11/2026 : non activée.** Constat fait en lecture seule par une session lodyorch,
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

## 2. Blocage : origine du consentement `merge:auto`

`merge_policy.py` accepte `merge:auto` si le dernier ajout du label sur le ticket a été fait par un
compte de `LQ_MERGE_TRUSTED` (par défaut `ReC82`). Or le superviseur **et** les sessions Claude
utilisent le jeton `gh` de `ReC82`, administrateur. Le journal d'événements GitHub montrera donc
`ReC82` que le label vienne du propriétaire, du superviseur ou d'une session. Les protections
actuelles sont seulement logicielles :

- le crochet de garde refuse aux sessions les commandes de labels et de fusion (motifs de commande) ;
- le superviseur ne pose jamais `merge:auto` (code relu).

Le jeton lui-même permettrait tout (labels, fusion, protection de branche). Le ticket demande de
**ne pas activer le mode réel** dans ce cas sans mesure compensatoire fiable.

**Mesure recommandée : compte machine distinct pour lodyorch.**

1. Créer un compte GitHub dédié (ex. `lody-bot`), l'inviter sur le dépôt avec le rôle **Write**
   (pas Admin, pas Maintain).
2. Sur l'hôte, authentifier `gh` de l'utilisateur `ubuntu` avec un jeton de ce compte (fine-grained,
   limité au dépôt : Contents RW, Pull requests RW, Issues RW, Metadata R, Checks R, Actions R).
3. Garder `LQ_MERGE_TRUSTED=ReC82` (valeur par défaut) : un `merge:auto` posé par `lody-bot` ne
   vaut plus rien ; seul le propriétaire, depuis son propre compte, autorise une fusion.
4. Effets à prévoir : les PR seront ouvertes par `lody-bot` → ajouter ce compte au critère
   `auteur-de-confiance` (variable dédiée ou adaptation de `merge_policy.py`, **PR séparée** relue
   par le propriétaire) ; les commentaires `<!-- lodyorch -->` viendront de `lody-bot`, ce qui
   simplifie la distinction propriétaire / outillage.
5. Protection de `main` avec `enforce_admins` : `lody-bot` (Write) ne peut fusionner que si
   `CI result` est vert ; le propriétaire garde la main.

Alternative plus faible (non recommandée) : garder le compte partagé et accepter que la seule
barrière soit le crochet de garde. À décider explicitement par le propriétaire.

## 3. Protection de `main` : pas maintenant

`python3 deploy/lodyorch/branch_protection.py` (à blanc) proposerait d'exiger `CI result` avec
`enforce_admins: true`. Les 14 PR ouvertes n'ont pas ce check sur leur SHA de tête : GitHub les
déclarerait non fusionnables, **y compris pour le propriétaire**, jusqu'à un nouveau push
(une relance de l'ancien run réutilise l'ancien `ci.yml`, sans `CI result`). Conformément au
ticket, la protection n'est **pas** appliquée tant qu'elle bloque les PR courantes.

Deux voies :

- **A (recommandée)** : appliquer la protection après le compte machine (§2), une fois les PR
  ouvertes à conserver mises à jour par `git merge origin/main` (nouveau run avec `CI result`) ou
  fermées.
- **B** : appliquer tout de suite et accepter que chaque PR ancienne doive être actualisée avant
  fusion.

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
6. **Mode réel** — seulement après la décision du §2 (compte machine en place, ou risque accepté
   par écrit) : `LQ_AUTO_MERGE=on` dans l'env installé, réinstaller, `lodyorch reload`, puis
   `lodyorch automerge on`. Le `merge:auto` posé à l'étape 5 est antérieur : verdict attendu
   « refusée — autorisation antérieure » (non-rétroactivité). Retirer puis reposer `merge:auto`
   sur #205 → fusion réelle de la PR factice, ticket rouvert et gardé `status:needs-test`, aucun
   déploiement. Kill switch : `lodyorch automerge off`, vérifier `lodyorch automerge`, puis `on`.
7. **Protection de `main`** : voie A ou B du §3, `branch_protection.py --apply`. Retour arrière :
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
