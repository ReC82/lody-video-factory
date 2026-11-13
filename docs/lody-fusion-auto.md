# Fusion automatique des PR — politique (#193) et fusion sécurisée par le superviseur (#200)

> **Statut : phase 2 livrée en mode à blanc (`dry-run`).** Le superviseur évalue chaque PR à CI
> verte et journalise son verdict ; il ne fusionne **réellement** qu'après deux gestes explicites du
> propriétaire (plafond `LQ_AUTO_MERGE=on` puis `lodyorch automerge on`, section 8). Aucun déploiement.
> Les sections 1 à 7 décrivent la politique de la phase 1 (#193), complétée par la section 8.
>
> **Mise en service (#205)** : constat et procédure dans
> [lody-fusion-auto-mise-en-service.md](lody-fusion-auto-mise-en-service.md) — non activée à ce jour.

**Fusion GitHub ≠ déploiement.** Fusionner une PR dans `main` et mettre une version en service sur
AWS sont deux niveaux distincts, avec des garde-fous distincts. Aucune fusion n'entraîne de
déploiement PROD implicite.

## 1. Inventaire constaté (12/11/2026, lecture seule)

| Élément | Constat |
|---|---|
| Protection de `main` | **aucune** (`branches/main/protection` → 404, aucun ruleset) : rien côté GitHub n'impose la CI avant fusion |
| Auto-merge GitHub | désactivé dans le dépôt (`allow_auto_merge: false`) ; fusion « merge commit » et « squash » autorisées |
| Suppression de branche après fusion | désactivée |
| Workflows sur PR | `ci.yml` : `Detect code changes`, puis soit `Documentation checks` (PR `.md` seule), soit `Python 3.11 tests`, `Python 3.13 tests`, `Windows smoke tests` ; `worker-2d.yml` : `Build worker 2D image and render` si fichiers Docker/2D ; `docker-ghcr.yml` sur push `main` seulement |
| Permissions des workflows | `contents: read` : aucun workflow ne peut fusionner |
| Compte de lodyorch | même compte GitHub que le propriétaire (jeton `gh` de l'hôte) : GitHub ne distingue donc pas « fusion par lodyorch » et « fusion par le propriétaire » |
| lodyorch aujourd'hui | suit la CI (`watch_ci`, check-runs du SHA de tête, base empilée validée), pose `status:needs-test` quand elle est verte ; **ne fusionne jamais** ; le crochet de garde refuse toute fusion aux sessions Claude |
| Attente mesurée | `merge_policy.py metrics --limit 15` : **médiane ≈ 24 min, cumul ≈ 46 h** entre la fin de la CI et la fusion sur les 15 dernières PR (pointes à 9–10 h la nuit) |

Conséquence : sans protection de branche, la seule garantie « CI verte avant fusion » est celle que
le superviseur vérifiera lui-même. La politique ci-dessous ne suppose aucun accès supplémentaire.

## 2. Autorisation

- **`merge:auto`** posé **sur le ticket** par un compte de confiance (`LQ_MERGE_TRUSTED`, par défaut
  le propriétaire du dépôt). L'auteur du **dernier ajout** est lu dans le journal des événements
  GitHub du ticket ; un retrait annule l'autorisation, un ajout par un autre compte ne vaut rien.
- Aucune autorisation par catégorie n'existe tant qu'elle n'est pas **explicitement** définie ici.
- **Veto** (prévalent toujours, sur le ticket OU la PR) : `merge:manual`, `agent:blocked`,
  `agent:human`, `do-not-merge`, et toute revue « changements demandés » non levée.
- Journal d'audit : les événements de label GitHub (qui, quand) ; en phase 2, chaque décision
  (matrice complète + SHA) serait journalisée par le superviseur et citée dans le ticket.

Les labels `merge:auto` / `merge:manual` ne sont **pas** créés par cette PR (référentiel des labels
géré par le propriétaire et le superviseur).

## 3. Critères cumulatifs (matrice de décision)

Tous doivent être satisfaits ; l'évaluateur les calcule **tous** (pas d'arrêt au premier refus).

| Critère (`code`) | Règle |
|---|---|
| `pr-ouverte`, `pr-non-draft` | PR ouverte, non fusionnée, pas en brouillon |
| `base-main`, `pas-de-fork` | base `main` ; branche de tête dans ce dépôt (PR de fork refusées) |
| `auteur-de-confiance` | PR ouverte par un compte de confiance (lodyorch) |
| `sans-conflit`, `base-a-jour` | `mergeable` vrai et `mergeable_state == clean` (`behind`, `dirty`, `unknown`, `blocked` → refus) |
| `ticket-lie` | branche `agent/<N>-…` **et** `Closes #<N>` dans le corps, même numéro |
| `ticket-ouvert`, `ticket-agent-auto` | ticket ouvert, dans la file `agent:auto` |
| `autorisation-merge-auto`, `aucun-veto`, `aucune-demande-de-modification` | section 2 |
| `ci` | sur le **SHA de tête** : aucun job en cours ni en échec ; jobs requis **tous** `success` (`Detect code changes` + les 3 jobs de tests, ou `Documentation checks` pour une PR `.md` seule) ; un job non requis peut être `skipped`/`neutral`, jamais `failure`/`cancelled` ; une relance remplace l'échec précédent du même job |
| `tests-modifies` | du code modifié implique au moins un fichier `test/` modifié |
| `aucune-zone-exclue` | section 4 (chemins) |
| `aucune-suppression-de-fichier` | aucun fichier supprimé |
| `diff-lisible`, `perimetre-borne` | diff fourni pour chaque fichier ; ≤ 40 fichiers et ≤ 1 500 lignes ; liste de fichiers complète |
| `aucune-migration-destructive`, `aucune-migration-additive` | SQL ajouté : `DROP`, `DELETE FROM`, `TRUNCATE`, `RENAME` → refus ; `CREATE TABLE`, `ADD COLUMN`, `CREATE INDEX` → refus tant que la politique « additive » n'est pas validée |
| `aucun-appel-payant-ou-aws`, `aucun-secret` | lignes ajoutées sans hôte OpenAI/ElevenLabs/Anthropic/AWS ni motif de secret |
| `pas-de-boucle-de-conflits` | ≤ 3 intégrations de `main` dans la branche |
| `sha-de-tete-coherent` | le dernier commit listé est la tête évaluée |

**Course de SHA.** Une fusion future se ferait par `PUT repos/…/pulls/<n>/merge` avec
`sha=<SHA évalué>` : GitHub la refuse (409) si la tête a bougé. Juste avant, `revalidate()` refait
**toute** l'évaluation et refuse si le SHA a changé. Une seule fusion par cycle du superviseur, PR
déjà fusionnée → refus (`pr-ouverte`) : pas de fusion multiple accidentelle.

## 4. Exclusions par défaut (validation explicite, même CI verte)

| Catégorie | Chemins (hors `.md` de la racine et de `docs/`) |
|---|---|
| `infra-ci-cd` | `.github/`, `Dockerfile*`, `docker-compose*`, `deploy/` (dont lodyorch lui-même), `scripts/`, `renderers/`, `*.sh`, `*.service` |
| `dependances` | `pyproject.toml`, `uv.lock`, `requirements*.txt`, `package*.json` |
| `auth-secrets` | `lody_auth/`, `app/config/`, `config.example.toml`, tout chemin contenant `secret`, `auth`, `password`, `credential`, `token` |
| `donnees-migrations` | `webui/lody/db.py`, `*migration*`, `*.sql`, `app/services/state.py` |
| `api-payantes-video` | fournisseurs `app/services/` (LLM, voix, ElevenLabs, matériaux, vidéo…), `webui/lody/generation/`, `webui/lody/animation/`, `webui/lody/voice_*` |

Plus les contenus de la section 3 (SQL, hôtes payants/AWS, secrets) et les suppressions de
fichiers. Les modifications métier « à fort impact » ne sont pas détectables automatiquement : le
propriétaire ne pose simplement pas `merge:auto` sur ces tickets.

## 5. Niveau DEV, distinct de la fusion (proposition, à coordonner avec #192)

1. Fusion automatique → **notification unique** dans le ticket et le rapport : « PR fusionnée —
   DEV disponible après déploiement — test humain requis ».
2. Déploiement **DEV uniquement**, par l'hôte (jamais par une session Claude, qui n'a ni Docker ni
   systemd) : image construite depuis le SHA fusionné, préflight, vérification de santé
   (`/api/version` = SHA attendu, page d'accueil 200), sauvegarde du volume DEV avant toute
   migration, **rollback** automatique vers l'image précédente si la santé échoue.
3. **PROD : jamais automatique.** Toujours une action explicite du propriétaire.
4. Le ticket reste `status:needs-test` après fusion : la CI verte et la fusion ne prouvent pas une
   UX correcte ; la fermeture suit la vérification réelle.

## 6. Outil livré (phase 1)

```bash
python3 deploy/lodyorch/merge_policy.py evaluate 190          # matrice (Markdown) d'une PR
python3 deploy/lodyorch/merge_policy.py evaluate 190 --json   # décision + requête de fusion qui SERAIT envoyée
python3 deploy/lodyorch/merge_policy.py metrics --limit 20    # attente « CI verte → fusion »
```

Lecture seule (`gh api -X GET`). Essai à blanc sur #190 (déjà fusionnée) : refusée — `pr-ouverte`,
`ticket-ouvert`, `merge:auto` absent, `webui/lody/db.py` (zone données) et SQL ajouté (`CREATE TABLE`,
`DELETE FROM`, `DROP TABLE`) : sous cette politique, #190 aurait demandé une validation explicite.
Tests : `test/lodyorch/test_merge_policy.py` (PR factices, chaque critère et chaque exclusion).

## 7. Décisions prises pour la phase 2 (voir section 8)

1. Créer les labels `merge:auto` / `merge:manual` et confirmer qui peut poser `merge:auto`
   (recommandation : le propriétaire seul).
2. Activer une protection de `main` exigeant les vérifications CI (recommandé : filet côté GitHub
   indépendant de lodyorch ; attention, les jobs « skipped » de la PR documentaire comptent comme
   réussis).
3. Politique des migrations additives (recommandation : exclues tant que la sauvegarde DEV
   automatique avant migration n'existe pas).
4. Autoriser le superviseur (pas les sessions Claude) à fusionner avec SHA attendu : c'est une
   modification de `lodyorch.py` et du crochet de garde, donc une PR dédiée relue par le propriétaire.
5. Niveau DEV automatique : périmètre et commande de déploiement à fixer avec l'audit infra #192.

## 8. Phase 2 (#200) — fusion par le superviseur

Décisions du propriétaire du 12/11/2026 (commentaire du #193) appliquées : labels `merge:auto` /
`merge:manual`, autorisation par compte de confiance seulement, migrations même additives exclues,
dry-run journalisé d'abord, fusion avec revalidation + SHA attendu, `status:needs-test` conservé,
**aucun déploiement**, aucune fusion depuis une session Claude.

### Fonctionnement

- `watch_ci` constate la CI verte (sans session Claude), puis `watch_merges` évalue la PR avec
  `merge_policy.py` ; les PR « prêtes pour revue » sont réévaluées toutes les `LQ_CI_POLL` s (un
  `merge:auto` posé après la CI est donc pris en compte).
- **Journal d'audit** (`merge_audit` dans `state.db`) : une ligne et un événement par changement de
  verdict (PR, SHA, mode, critères refusés) — rien n'est réécrit tant que rien ne change. Sans
  `merge:auto` sur le ticket, un seul verdict complet par SHA (pas de collecte répétée) et aucun
  commentaire ; avec `merge:auto`, le verdict (simulation, refus motivé, ou fusion) est commenté
  **une fois** dans le ticket, matrice complète repliée.
- **Critères ajoutés (politique v2)** :
  - `ci` : si l'agrégateur `CI result` de #198 est présent sur le SHA de tête, c'est lui (avec
    `Detect code changes`) qui fait foi ; sinon, les jobs historiques. Rien n'est changé dans `ci.yml`.
  - `base-sans-chevauchement` : base en retard tolérée seulement si `main` n'a touché **aucun**
    fichier de la PR depuis la divergence, et au plus 30 commits de retard.
  - `autorisation-apres-activation` (mode `on`) : le dernier ajout de `merge:auto` doit être
    **postérieur** à `lodyorch automerge on` → aucune PR historique n'est fusionnée rétroactivement ;
    pour autoriser une PR déjà labellisée, retirer puis reposer le label.
- **Fusion (mode `on` seulement)** : nouvelle collecte complète et `revalidate()` (même SHA, mode
  toujours `on`), intention écrite dans `merge_ops` **avant** la requête, puis
  `PUT pulls/<n>/merge` avec `sha=<SHA évalué>`. 409 (tête modifiée) / 405 (non fusionnable) /
  erreur API : consignés, **jamais retentés pour ce SHA**, la PR reste en revue manuelle. Au plus
  une fusion par tour. Après un arrêt brutal, l'intention est résolue en **lisant** l'état réel de
  la PR, jamais en rejouant la requête.
- **Après fusion** : ticket gardé `status:needs-test` (rouvert si `Closes #N` l'a fermé),
  commentaire « fusionnée, non déployée », rapport de tentative finalisé, notification groupée
  « fusionnée — test à faire ». Aucun déploiement DEV/PROD.

### Activer, vérifier, arrêter

```bash
lodyorch labels --apply             # crée merge:auto / merge:manual (référentiel labels.json)
lodyorch automerge                  # mode effectif + derniers verdicts et fusions
lodyorch automerge dry-run          # défaut : verdicts journalisés, rien n'est fusionné
# Recette à blanc : PR factice sûre (ex. .md + test), poser merge:auto sur son ticket → commentaire
# « Simulation : serait fusionnée » ; une PR touchant deploy/ ou une migration → « refusée » + motifs.
# Activation réelle : LQ_AUTO_MERGE=on dans lodyorch.env (installé root, hors de portée des
# sessions), réinstaller + lodyorch reload, PUIS :
lodyorch automerge on               # horodate l'activation : seules les autorisations postérieures comptent
lodyorch automerge off              # KILL SWITCH immédiat (pris en compte avant toute requête de fusion)
```

Rollback : `lodyorch automerge off` (immédiat), ou `LQ_AUTO_MERGE=off` dans l'env installé. Une
fusion déjà faite se défait par un `git revert` du commit de fusion via une PR normale.

Les sessions Claude ne peuvent ni piloter ce mode (`lodyorch automerge` refusé par le crochet de
garde) ni fusionner (`pulls/<n>/merge`, `gh pr merge` refusés).

### Protection de `main`

`deploy/lodyorch/branch_protection.py` (à blanc par défaut, `--apply` par le propriétaire) exige
uniquement un check **toujours présent** : l'agrégateur `CI result` de #198. Tant qu'il n'est pas
sur `main`, le script refuse d'appliquer — exiger `Python 3.11 tests` ou `Documentation checks`
bloquerait les PR où ces jobs n'existent plus. `enforce_admins` est activé car lodyorch utilise le
compte administrateur du propriétaire ; `strict` est désactivé (la base en retard est contrôlée par
la politique, sans boucle de mises à jour).

### Limite connue

lodyorch et le propriétaire partagent le même compte GitHub : le journal d'événements ne distingue
pas un label posé par le propriétaire d'un label posé par l'outillage. Les sessions Claude ne
peuvent pas poser de label (crochet de garde) et le superviseur ne pose jamais `merge:auto` ; un
compte machine distinct lèverait cette ambiguïté.
