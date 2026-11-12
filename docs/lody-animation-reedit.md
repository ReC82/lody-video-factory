# Réédition ciblée et contrôle qualité du rendu 2D (#121)

> Suite de #119 (contrat), #120 (fournisseur et worker) et #150 (moteur Remotion, premier MP4 Voltix).
> Objectif : retoucher une scène, un geste, une expression, un plan, un texte ou une réplique **sans refaire
> le reste de la vidéo**, voir le résultat vite, et repérer automatiquement les défauts visibles.

Cette étape livre les **fondations moteur** (worker, fournisseur, outil DEV). L'écran « actions simples » qui
les expose dans l'interface est une suite proposée (voir « Limites »).

## 1. Cache de scène par projet

Chaque scène réellement animée est rangée dans `<données>/animation/<projet>/.scene-cache/<clé>.mp4` (+ `.json`).
La **clé** est le SHA-256 de ses seules dépendances (`webui/lody/animation/scene_cache.py`) :

| Dépendance | Effet d'une modification |
|---|---|
| La scène telle que le moteur la lit (calques, objets, gestes, expressions, mouvements, caméra, répliques, transitions, repli, durée) | cette scène est refaite |
| `id` et `intent` (intention narrative, jamais interprétée par le moteur) | **rien n'est refait** |
| Canevas et échelle de rendu (aperçu ≠ rendu final) | toutes les scènes / cache séparé |
| Empreinte des fichiers d'assets utilisés **par cette scène** et leur placement | seules les scènes qui les utilisent |
| **Tranche de voix** jouée pendant la scène (échantillons PCM de sa fenêtre) | seule la scène où la voix change ; une scène déplacée garde sa voix si la tranche est identique |
| Identité du moteur de scène : commande, `LODY_ANIMATION_RENDERER_REVISION`, `<moteur> --version` | tout le cache (nouvelle version du moteur) |

Une V2 dont le storyboard ne change qu'une scène ne rend donc **qu'une scène** ; les autres sont reprises
octet pour octet (lien physique, sinon copie). Garanties :

- un **repli hybride** (image fixe) n'entre jamais au cache : il ne sera jamais resservi comme une animation ;
- chaque entrée porte l'empreinte de son MP4, revérifiée avant réutilisation (fichier altéré = ignoré et supprimé) ;
- un cache par projet (même isolation que les dossiers de tâche) ;
- taille plafonnée à 2 Gio par projet (`LODY_ANIMATION_CACHE_MAX_MB`), les entrées les moins récemment utilisées
  partent en premier ; supprimer `.scene-cache/` est toujours sans risque (les scènes seront simplement refaites) ;
- `run_job(..., use_cache=False)` force un rendu complet.

Le moteur Remotion annonce l'empreinte de ses sources (`lody-remotion --version`) : modifier le moteur invalide
le cache sans intervention. Pour un autre moteur, fixer `LODY_ANIMATION_RENDERER_REVISION` (ex. tag de l'image).

## 2. Plan de réédition (gratuit, sans rendu)

`lody.animation.reedit.plan(v1, v2, previous_manifest=…, has_voice=…)` compare deux storyboards scène par scène
(appariées par `id`) et renvoie `lody.animation-reedit-plan/1` :

- pour chaque scène : `reuse` ou `render`, et **pourquoi** (geste, expression, mouvement, plan (caméra), texte
  affiché, réplique, timecodes, durée, transitions, version d'asset, repli, nouvelle scène…) ;
- scènes retirées, scènes déplacées (réutilisées) ;
- voix : `keep` (conservée), `regenerate` (texte de réplique modifié — **synthèse payante, jamais lancée sans
  accord sur le coût**), `new`, `removed` ;
- `voice_shift` : avec une voix enregistrée, si une scène précédente change de durée, la fenêtre de voix se
  décale et la scène sera refaite (synchro labiale) ;
- temps de rendu à dépenser (≈ 7× la durée, benchmark #118) et temps **évité** — mesuré dans le manifeste de
  la V1 quand il est fourni, sinon estimé (le plan dit lequel) ;
- les contrôles qualité du nouveau storyboard (section 4), avant tout rendu.

La décision `reuse` du plan est celle du cache du worker (test `test_plan_and_cache_agree_on_what_is_reusable`).

## 3. Prévisualisation basse résolution

`GenerationRequest.animation["preview_scale"]` (¼, ½ ou ¾ ; toute autre valeur est ramenée au palier le plus
proche, hors bornes = rendu final) → `job.json` `preview.scale` → entrée de scène `"render": {"scale": 0.5}` →
Remotion rend à cette échelle (`scale` de `renderMedia`/`renderStill`) ; l'assemblage sort à la résolution
réduite (dimensions paires). Le manifeste porte `preview: true`. Les scènes d'aperçu ont leur propre clé de
cache : un aperçu n'est jamais resservi comme rendu final, mais deux aperçus successifs se réutilisent.

## 4. Contrôle qualité automatique (`qa.json`)

`webui/lody/animation/qa.py`, lancé par le worker après l'assemblage (local, gratuit, n'échoue jamais un rendu) :

| Contrôle | Niveau | Comment |
|---|---|---|
| `black_screen` | erreur | `ffmpeg blackdetect`, ≥ 0,5 s, pixel < 10 % de luminance (le carton de repli gris n'en est pas) ; scène indiquée |
| `duration` / `scene_duration` | erreur / avertissement | durée réelle vs prévue (tolérance 0,12 s) |
| `voice_cut` | erreur | `voice.wav` plus long que la vidéo (> 0,25 s) : fin de voix perdue |
| `audio_duration` / `audio_missing` | avertissement / erreur | piste audio décodée vs vidéo ; voix ou musique fournie mais vidéo muette |
| `subtitle_cut` | avertissement | réplique trop longue pour la zone de sous-titres du moteur (lignes estimées) |
| `subtitle_fast` / `subtitle_short` | avertissement | > 20 caractères/s, ou < 0,5 s à l'écran |
| `subtitle_in_transition` | info | réplique qui se termine pendant la transition de sortie |
| `text_overflow` | avertissement | texte affiché qui dépasse du cadre (centre dans le cadre ; une entrée hors champ est voulue) |
| `continuity_version` | avertissement | même asset en plusieurs versions d'une scène à l'autre |
| `continuity_jump` | info | personnage qui saute de > 25 % de la largeur à une coupe franche |
| `sync_no_voice` | info | synchro labiale sans voix enregistrée |

Les largeurs de texte sont **estimées** (0,6 × la taille de police, styles du moteur Remotion). Les
avertissements et erreurs sont recopiés dans `manifest.warnings` (préfixe « Contrôle qualité : ») et donc
affichés dans le suivi de la production ; `manifest.qa` en donne le statut et les comptes.

## 5. Audit des ressources par étape

`manifest.audit` : scènes rendues et réutilisées, temps de rendu dépensé, temps **évité** (durée de rendu mesurée
à l'origine des scènes réutilisées), temps de chaque étape (`scenes_ms`, `soundtrack_ms`, `assembly_ms`, `qa_ms`),
appels externes (0) et coût externe (0). Chaque scène du manifeste porte `cache` (`hit`/`miss`) et, si réutilisée,
`reused_from` (tâche et scène d'origine) et `render_ms_saved`.

## Outil DEV

```bash
# Ce qu'une V2 refait / réutilise (storyboards ou job.json de tâche) :
uv run --no-sync python scripts/animation/reedit.py plan --before v1.json --after v2.json \
    [--manifest <tâche V1>/manifest.json] [--voice] [--json]
# Contrôle qualité d'une tâche déjà rendue (écrit qa.json) :
uv run --no-sync python scripts/animation/reedit.py qa <dossier de la tâche>
```

## Limites (suites proposées)

- **Interface** : pas encore d'écran « modifier une scène » dans la V2 (aujourd'hui `prepare_v2` ne change que
  le script, et la V2 hérite du storyboard figé). Suite : action « Importer le storyboard modifié » sur un
  brouillon de V2 → plan affiché → aperçu ½ → rendu final ; puis actions simples (changer un geste, une
  expression, le texte d'une réplique) sans éditer le JSON.
- **Voix par scène** : la TTS n'est pas branchée dans le fournisseur ; quand elle le sera, synthétiser
  **par scène** et ne régénérer que les répliques modifiées (le plan les liste déjà), avec accord de coût.
- Les contrôles de texte sont des estimations ; la caméra (zoom) n'est pas prise en compte.
- Le contrôle `blackdetect` décode toute la vidéo (≈ quelques dizaines de secondes de CPU pour 3 min en
  1080×1920, à priorité basse).
