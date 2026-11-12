# Cache de scène et voix ElevenLabs : contrat commun (#185)

Note de conception qui prépare la réconciliation de la PR #169 (voix ElevenLabs dans le rendu 2D, #164) avec le
cache de scène de #157 (#121), en vue de la fusion des PR dans l'ordre **#157 → #176 → #169**. Elle décrit ce
qui est déjà livré sur `main` par #185 et ce qui reste à faire dans la branche de #169 une fois #176 fusionnée.

## Règle

Une scène réutilisée (cache du projet ou reprise d'une tâche) n'affiche **jamais** une bouche calée sur
l'ancienne voix. Toute donnée qui pilote la bouche (visèmes) ou les sous-titres d'une scène fait donc partie
de la clé de cache de CETTE scène, et d'aucune autre.

| Changement                                         | Entrée de `scene_dependencies` concernée      | Effet                       |
|----------------------------------------------------|-----------------------------------------------|-----------------------------|
| Rien (même voix, mêmes repères)                    | aucune                                        | cache utilisé               |
| Texte d'une réplique (voix re-synthétisée)         | `scene` (repère du storyboard) + `voice` (tranche PCM) + `speech_timing` | seule cette scène refaite |
| Mêmes textes, repères recalés différents / visèmes | `speech_timing`                               | seule cette scène refaite   |
| Nouvelle voix, mêmes repères                       | `voice` (tranche PCM de la fenêtre de la scène) | scènes où elle s'entend     |
| Modification visuelle seule                        | `scene` ; `voice` et `speech_timing` inchangés | image refaite, voix gardée  |
| Locuteur changé sur une scène                      | `scene` (`render.speech.speaker`)             | seule cette scène refaite   |
| Rendu V1 sans voix ElevenLabs                      | `speech_timing` **absent**                    | clés d'avant #185 identiques |

## Livré sur `main` par #185 (indépendant de #169 et #176)

- `scene_cache.scene_dependencies(..., *, speech_cues=None)` : les repères recalés de la scène (tels que
  transmis au moteur) entrent dans la clé sous `speech_timing`. Sans repères (V1), l'entrée est absente : aucune
  clé déjà en cache n'est invalidée.
- `scene_cache.normalized_speech_cues` : refuse des repères malformés (`ValueError`) plutôt que de les ignorer.
- `test/lody/test_animation_cache_voice.py` : les six scénarios du ticket sur les fonctions pures, plus un
  test d'intégration du worker qui **s'active tout seul** dès que `lody.animation.voiceover` (#169) est
  présent (`pytest.importorskip`) et sert de critère d'acceptation de la réconciliation.

## Reste à faire dans la branche de #169 (après fusion de #176)

`git merge origin/main` dans `agent/164-elevenlabs-worker-remotion` (pas de rebase, pas de force), puis :

1. **`webui/lody/animation/worker.py`** (conflit sémantique) :
   - supprimer `_voice_digest` (#169) : la voix est déjà dans la clé via `voice_slice_digest` (#157) ;
   - dans `_render_scene`, calculer une seule fois
     `timed = voiceover.timed_cues(voiceover.read_timing(ctx.directory), index)` et appeler
     `scene_cache.scene_dependencies(ctx.job, index, scene, voice…, ctx.identity, ctx.scale, speech_cues=timed)` ;
   - garder dans `scene_input` la scène recalée (`voiceover.scene_with_timing`) et l'entrée `render.scale`
     de la prévisualisation (#157) ;
   - `write_subtitles` : garder la version #169 (repères recalés sinon storyboard), inchangée par #157.
2. **`webui/lody/generation/animation_connector.py`** : garder le préflight Docker (#156), la réédition V2
   (#176) et la synthèse ElevenLabs au `submit` (#169) ; aucune synthèse dans le worker.
3. **`docs/README.md`** : garder les quatre entrées (worker Docker, réédition, voix du rendu 2D, voix d'un
   personnage).
4. Lancer `test/lody/test_animation_cache_voice.py` : le test d'intégration ne doit plus être `skipped`.

## Divergences inventoriées (à traiter ou à reporter dans un ticket ciblé)

- **Empreinte de voix en double** : `voiceover.fingerprint` (#169) recopie `voice_casting.fingerprint` (#155,
  sur `main`). Recommandation : importer celle de `voice_casting` (même formule, même version) pour qu'un
  aperçu de casting et une réplique du rendu partagent toujours le même cache d'échantillons.
- **Lexique #163** : `lody.pronunciation` n'est pas sur `main` ; `voiceover.render_text` retombe sur le texte
  brut. Le gel du lexique par version est à vérifier quand #163 sera fusionnée (le texte parlé entre dans
  l'empreinte, donc dans la voix, donc dans `voice`).
- **Plan de réédition** (`reedit.plan`) : il annonce `voice: keep/regenerate` d'après le texte du storyboard
  seulement ; il ne voit pas un recalage seul. Sans danger (le cache, lui, le voit), mais le plan peut annoncer
  « réutilisée » une scène que le worker refera.
- **Déplacement d'une scène** : l'en-tête de `scene_cache` affirme qu'un déplacement n'invalide pas la scène,
  mais `voice_slice_digest` inclut la fenêtre (`[décalage, durée]`) dans l'empreinte ; une scène déplacée avec
  sa voix est donc refaite. Prudent (jamais de bouche fausse), mais la doc et le code divergent.

## Test DEV proposé (après fusion de #169, avec accord de dépense)

1. Projet de deux scènes, Voltix dans les deux, voix ElevenLabs choisie (#153). Rendu V1.
2. V2 : modifier seulement le texte de la réplique de la scène 2. Attendu : synthèse ElevenLabs de la seule
   scène 2 (estimation = ses caractères), scène 1 « réutilisée depuis le cache », bouche de la scène 2 calée
   sur la nouvelle phrase, sous-titres recalés.
3. V3 : déplacer seulement le personnage de la scène 1. Attendu : aucun appel ElevenLabs (coût 0), scène 1
   re-rendue, scène 2 réutilisée.
