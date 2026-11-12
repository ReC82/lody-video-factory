# Réédition ciblée et contrôle qualité du rendu 2D (#121)

> Suite de #119 (contrat), #120 (fournisseur et worker) et #150 (moteur Remotion, premier MP4 Voltix).
> Objectif : retoucher une scène, un geste, une expression, un plan, un texte ou une réplique **sans refaire
> le reste de la vidéo**, voir le résultat vite, et repérer automatiquement les défauts visibles.

Cette étape livre les **fondations moteur** (worker, fournisseur, outil DEV). L'écran « Nouvelle version » qui
les expose dans l'interface est livré par #159 (section 6).

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

## 6. Écran « Nouvelle version » (#159) — mode d'emploi

Pour une vidéo **animée** terminée (fournisseur Animation 2D) :

1. Page de suivi de la vidéo → **« Créer une nouvelle version »**. Un brouillon V2 est créé ; la V1 n'est jamais
   modifiée (storyboard figé, MP4 et dossier de rendu intacts) et reste accessible par la barre « Versions ».
2. La page liste les **scènes** : miniature (tirée du rendu de la V1), début → fin, objectif, personnage avec ses
   gestes et expressions, répliques (« texte dit », avec leurs temps), textes affichés, schémas et décors (version).
3. **« Modifier cette scène »** ouvre un formulaire simple, avec info-bulles :
   - *Expression* et *Geste* de chaque personnage : listes limitées à ce que la version de l'asset déclare
     (aucun nom inventé) ; le moment du geste ne change pas ;
   - *Texte dit* (60 caractères, c'est aussi le sous-titre) et *Texte affiché* (titres, étiquettes) ;
   - *Version* d'un personnage, schéma ou décor quand le catalogue en a plusieurs ;
   - *Cadrage* : plan large / moyen / serré (zoom de la caméra ; un mouvement de caméra existant garde ses
     proportions) ;
   - *Durée de la scène* : toute l'animation de la scène est étirée ou resserrée.
   « Enregistrer la scène » regèle le storyboard du brouillon (mêmes vérifications que #119 : gestes et
   expressions déclarés, versions existantes) ; « Annuler » ne change rien.
4. **Plan de réédition avant dépense** : scènes réutilisées / à recalculer et pourquoi, temps de rendu estimé,
   temps évité (mesuré sur la V1), coût. Le rendu 2D est local : coût 0 €. Une réplique modifiée met à jour les
   **sous-titres seulement** : tant que la voix réelle du worker (#169) n'est pas fusionnée, l'écran l'annonce
   explicitement comme **non re-voisée** (aucune synthèse, aucun coût, aucune case d'accord). Le drapeau
   `reedit_v2.VOICE_REGENERATION_AVAILABLE` ne passera à vrai qu'avec #169 ; l'accord de coût sera alors exigé.
5. **« Aperçu rapide »** : rendu ½ résolution du brouillon, sans créer de version ni rien confirmer (tâche
   déterministe : relancé sans changement, il ne refait rien). Puis **« Générer la V2 »**. L'aperçu et la
   génération respectent la même garde de licence Remotion que le worker Docker #156 : sans
   `LODY_REMOTION_LICENSE` ni `LODY_REMOTION_EVALUATION=1` (pilote DEV), ni l'un ni l'autre ne part (#184).
6. Dans le suivi de la V2 : bloc **« Réédition et contrôle qualité »** — scènes réutilisées / recalculées, temps
   de rendu évité, contrôles qualité (`qa.json`), lecture **V1 et V2 côte à côte**, bouton « Revenir à la V1 ».

Code : `webui/lody/animation/scene_edit.py` (résumé et modifications d'une scène, module pur),
`webui/lody/generation/reedit_v2.py` (plan, aperçu, miniatures, comparaison), `ProductionService.prepare_animation_v2`,
`webui/lody/view_reedit.py` (écran). Les miniatures sont rangées dans `animation/<projet>/.thumbs/` (jamais dans le
dossier de rendu d'une version ; supprimables sans risque).

Vérification réelle (DEV, moteur Remotion, voix provisoire Piper locale, aucun appel payant) :

```bash
cd renderers/remotion && npm ci && cd -
uv run --no-sync python scripts/animation/demo_reedit_159.py --out /tmp/voltix-159 \
    --piper-model /chemin/fr_FR-upmc-medium.onnx --preview
```

Le script refait la démo Voltix #150 (V1), modifie **uniquement la scène 2** avec les fonctions de l'écran (geste et
expression), affiche le plan, lance l'aperçu ½ puis la V2, et vérifie `scenes_reused == 2`, `render_ms_saved > 0`,
scène rendue = `[2]`, V1 inchangée (empreinte du MP4 et storyboard).

## Outil DEV

```bash
# Ce qu'une V2 refait / réutilise (storyboards ou job.json de tâche) :
uv run --no-sync python scripts/animation/reedit.py plan --before v1.json --after v2.json \
    [--manifest <tâche V1>/manifest.json] [--voice] [--json]
# Contrôle qualité d'une tâche déjà rendue (écrit qa.json) :
uv run --no-sync python scripts/animation/reedit.py qa <dossier de la tâche>
```

## Limites (suites proposées)

- **Voix par scène** : la TTS n'est pas branchée dans le fournisseur ; quand elle le sera, synthétiser
  **par scène** et ne régénérer que les répliques modifiées (le plan les liste déjà), avec accord de coût.
- Les contrôles de texte sont des estimations ; la caméra (zoom) n'est pas prise en compte.
- Le contrôle `blackdetect` décode toute la vidéo (≈ quelques dizaines de secondes de CPU pour 3 min en
  1080×1920, à priorité basse).
