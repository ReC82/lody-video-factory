# Fusion automatique des PR — politique et évaluateur à blanc (ticket #193)

> **Statut : phase 1, rien n'est activé.** Ce document propose une politique ; l'outil livré
> (`deploy/lodyorch/merge_policy.py`) **évalue** une PR et **mesure** l'attente, mais ne fusionne
> rien, ne pose aucun label et ne déploie rien. L'activation (phase 2) attend une décision du
> propriétaire sur les points de la section « Décisions à prendre ».

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

## 7. Décisions à prendre avant la phase 2

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
