# CI ciblée par PR (#198)

La CI GitHub (`.github/workflows/ci.yml`) ne relance plus systématiquement les ~3 150 tests en deux
versions de Python. Elle choisit les tests d'après les fichiers de la PR, teste la version Python de
production (3.12) et garde la suite complète là où le risque la justifie. Constat de départ : audit #192
(`docs/audits/lody-infra-boucle-audit.md`), CI de PR médiane de 14,3 min, dont 97 % de pytest.

## Quel changement déclenche quoi

Le classement est fait par `scripts/ci/select_tests.py` (testé par `test/lody/test_ci_workflow.py`).
**Règle de sûreté : tout fichier qui n'est pas explicitement reconnu comme isolé déclenche la suite
complète.** Un seul fichier « complet » dans la PR suffit.

| Fichiers modifiés dans la PR | Mode | Tests lancés (Python 3.12) | Smoke Windows |
|---|---|---|---|
| Uniquement `*.md` à la racine ou `docs/**/*.md` | `docs` | tests qui lisent la documentation (index, liens, contrats documentés) | non |
| `webui/lody/**`, `webui/Lody.py`, `test/lody/**`, `lody_auth/**`, `test/lody_auth/**`, `renderers/remotion/**`, `scripts/animation/**`, `resource/animation-assets/**` | `targeted` | `test/lody` en entier (voix, projets, caches, 2D compris) | non |
| `deploy/lodyorch/**`, `test/lodyorch/**` | `targeted` | `test/lodyorch` | non |
| Combinaison des deux lignes précédentes (+ docs) | `targeted` | union des dossiers (+ tests de documentation) | non |
| `app/**`, `test/services/**`, `webui/Main.py`, `webui/i18n`, `webui/static`, `webui/styles.css`, `cli.py`, `main.py` | `full` | suite complète `test` + couverture | oui |
| `pyproject.toml`, `uv.lock`, `requirements*.txt`, `Dockerfile*`, `docker-compose*`, `.github/**`, `scripts/` (hors animation), `deploy/` (hors orchestrateur), `config.example.toml`, `resource/` (hors assets 2D) | `full` | suite complète + couverture | oui |
| `test/__init__.py`, `test/conftest.py`, tout fichier inconnu ou nouveau dossier | `full` | suite complète + couverture | oui |
| Diff illisible ou vide | `full` | suite complète + couverture | oui |
| **Push sur `main`**, programmation hebdomadaire (lundi 03:17 UTC), lancement manuel | `full` | suite complète en **3.11, 3.12 et 3.13** + couverture | oui |

Pourquoi c'est sûr pour les zones ciblées : ni `app/`, ni l'ancienne WebUI, ni `test/services` n'importent
`webui/lody`, `lody_auth` ou le moteur Remotion ; l'orchestrateur et ses tests n'importent rien du produit.
Un test (`test_isolated_areas_are_not_imported_by_the_engine_or_the_legacy_webui`) échoue si cela change.
Le sens inverse (`webui/lody` qui appelle `app/` pour la réparation de rendu) est couvert : toute
modification de `app/` lance la suite complète. Le rendu 2D réel reste vérifié par `worker-2d.yml`,
inchangé, déclenché sur ses propres chemins. La compilation (`compileall`) et le lint (`ruff`) portent
toujours sur tout le dépôt, quel que soit le mode.

Pour connaître la sélection d'une branche **avant de pousser** (même code que la CI) :

```bash
python3 scripts/ci/select_tests.py --base origin/main     # commits de la branche + copie de travail
python3 scripts/ci/select_tests.py --files webui/lody/ui.py app/x.py
```

La sélection et la raison de chaque fichier sont aussi affichées dans le résumé du run GitHub
(« Sélection des tests (#198) »).

## Check obligatoire : « CI result »

Le job `CI result` tourne toujours (`if: always()`) et n'est vert que si la classification a réussi, que
le job de tests a réussi, et que le smoke Windows a réussi en mode `full` (ou a été sauté en mode
`docs`/`targeted`). Un job échoué, annulé ou sauté à tort le rend rouge : un check sauté ne peut pas
passer pour un succès. **C'est le seul check à exiger** dans la protection de branche et pour la fusion
automatique (#193) ; les noms des autres jobs (« Python 3.12 tests »…) peuvent varier avec la matrice.

## Python 3.12 et compatibilité

- PR : un seul job, **Python 3.12**, version de `Dockerfile.lody` (`python:3.12-slim`). Un test vérifie que
  la CI suit cette version.
- `main`, programmation, lancement manuel : matrice **3.11 / 3.12 / 3.13** (`requires-python >= 3.11`,
  `audioop-lts` en 3.13). Une incompatibilité 3.11/3.13 est vue au plus tard sur `main` ou le lundi
  suivant ; elle ne bloque pas une PR.
- Smoke Windows : passé de 3.11 à 3.12, seulement en mode `full`.

## Parallélisme (pytest-xdist)

`pytest-xdist==3.8.0` est une dépendance de développement verrouillée dans `uv.lock`. Il est utilisé
**seulement pour les modes `docs` et `targeted`**, borné à 4 processus (`-n auto --maxprocesses 4`, les
runners standard ont 4 vCPU). Vérifié sans conflit (mêmes résultats qu'en série) sur `test/lody` et
`test/lodyorch`. La suite complète reste **en série** : elle porte la couverture et son seuil
(`fail_under = 70`), et `test/services` partage une base Redis de test non vérifiée en parallèle.

Tests lents laissés tels quels, en connaissance de cause : `test/lodyorch/test_console.py` (35 à 43 s par
test) et `test_consultation.py` (8,5 s) mesurent de vrais délais de calme d'une console et de vrais
processus concurrents ; les raccourcir les rendrait instables sur un runner chargé. Sous xdist ils
s'exécutent en parallèle des autres. `test_webui_loomloom_regressions.py` ne dort pas : il lance
l'ancienne WebUI Streamlit (AppTest) — il n'est exécuté qu'en mode `full`.

## Mesures (#198)

### Avant : CI GitHub (`scripts/ci/ci_durations.py`, 20 derniers runs réussis au 12/11/2026)

| Événement | Classe | Runs | Durée totale médiane | Étendue | Job de tests médian |
|---|---|---|---|---|---|
| pull_request | avant #198 | 12 | 14,8 min | 1,2 – 16,9 min (1,2 = PR docs #127) | 14,5 min |
| push `main` | avant #198 | 8 | 14,5 min | 11,4 – 16,2 min | 14,1 min |

### Mesures locales de la sélection (hôte AWS partagé, 2 vCPU, Python 3.12, 12/11/2026)

| Sélection | Tests | En série | xdist (`-n 4` sur 2 vCPU) |
|---|---|---|---|
| `test/lody` (PR interface) | 1 670 | 475 s | 266 s |
| `test/lodyorch` (PR orchestrateur) | 194 | 194 s | 108 s |
| Tests de documentation (PR docs) | 330 | 55 s | 54 s |

Ces chiffres viennent d'un hôte plus lent et partagé (l'audit #192 estime `test/lody` à ~175 s en série
sur un runner GitHub). **Les durées de CI après #198 ne sont pas encore mesurées** : la PR #198 elle-même
modifie `ci.yml`, donc tourne en mode `full`. Objectif à valider : 3 à 5 min pour une PR interface.

Ce qui change mécaniquement, sans hypothèse de durée :

- PR interface ou orchestrateur : `test/services` (~49 % du temps pytest mesuré par #192) et l'autre
  dossier ciblé ne tournent plus ; plus de second job Python ; plus de smoke Windows.
- PR `full` : un job Linux au lieu de deux (minutes de runner divisées par deux, durée murale ≈ inchangée).
- Nouveau push sur une PR : le run précédent, devenu inutile, est annulé (`concurrency`).

### Refaire la mesure après fusion

```bash
python3 scripts/ci/ci_durations.py --repo ReC82/lody-video-factory --runs 60
```

Le script (lecture seule, `gh api`) classe chaque run réussi d'après l'étape réellement exécutée
(`docs`, `targeted`, `full`, ou `avant-198`) et affiche médianes et étendues par événement et par classe.
Limite : il ne compte que les runs réussis ; les échecs se lisent dans l'onglet Actions.

## Pratique des sessions (Claude / lodyorch)

Avant de pousser : `compileall` + `ruff` sur tout le dépôt, puis les tests de la sélection
(`select_tests.py --base origin/main`) et les régressions spécifiques au changement, en parallèle borné
sur l'hôte (`-n 2` : 2 vCPU partagés avec la production). La suite complète locale n'est utile que pour un
changement `full` difficile à diagnostiquer : la CI la lance de toute façon, et la session n'attend pas la
CI (superviseur, #127). L'invite de session (`deploy/lodyorch/session_prompt.md`) n'est pas modifiée par
ce ticket ; l'aligner sur `select_tests.py` est une décision du propriétaire.

## Recette

1. PR ne touchant que `webui/lody/` : le résumé du run affiche `mode=targeted`, `tests=test/lody` ; un seul
   job « Python 3.12 tests » ; « Windows smoke tests » est sauté ; « CI result » est vert.
2. PR ne touchant que `docs/*.md` : `mode=docs`, quelques fichiers de test ; « CI result » vert.
3. PR touchant `app/` (ou cette PR, qui touche `ci.yml`) : `mode=full`, suite complète + couverture en
   3.12, smoke Windows ; « CI result » vert seulement si tout est vert.
4. Faire échouer volontairement un test sur une branche d'essai : « CI result » doit être rouge.
5. Après fusion : un push sur `main` lance 3.11, 3.12 et 3.13 ; Actions → CI → « Run workflow » aussi.
6. Protection de branche (propriétaire) : exiger le seul check **« CI result »**.

## Retour arrière

Tout est dans `.github/workflows/ci.yml` et `scripts/ci/` : restaurer l'ancien workflow
(`git checkout <commit avant #198> -- .github/workflows/ci.yml`) suffit ; `pytest-xdist` peut rester
installé sans effet. Si la protection de branche exige « CI result », l'adapter en même temps.
