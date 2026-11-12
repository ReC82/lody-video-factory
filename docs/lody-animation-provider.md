# Fournisseur « Animation 2D » et rendu hybride (#120)

> Suite de #118 (prototype Remotion) et #119 (contrat `lody.animation/1` + catalogue d'assets).
> Le moteur historique MoneyPrinterTurbo **n'est pas remplacé** : il reste le fournisseur du mode Classique,
> qui est le mode par défaut de tout projet.

## Modes de rendu d'un projet

Paramètres du projet → onglet **Général** → « Mode de rendu des vidéos » (`settings["render_mode"]`).

| Mode | Fournisseur | Comportement |
|---|---|---|
| **Classique** (défaut) | `moneyprinterturbo` | Pipeline historique strictement inchangé : même demande, même instantané, même empreinte qu'avant #120. |
| **Animation 2D** | `animation_2d` | Le storyboard animé figé est rendu scène par scène. Si une scène échoue, la production échoue : une scène non animée n'est jamais présentée comme animée. |
| **Hybride** | `animation_2d` | Même rendu, mais une scène en échec est remplacée par son **image de repli** (`render.fallback`), avec un avertissement par scène dans le suivi. |

En mode Animation 2D ou Hybride, l'écran de production propose d'importer le **storyboard animé**
(JSON `lody.animation/1`). Il est figé à la préparation : les versions explicites des assets du
catalogue du projet sont enregistrées dans `productions.animation`. Un storyboard invalide est refusé
avec le chemin exact de l'erreur.

## Architecture

```
view_production ──prepare(animation=…)──► ProductionService ──freeze_document──► productions.animation (figé)
                                             │ request_of(): GenerationRequest.animation = {project_id, storyboard}
                                             ▼
                               AnimationConnector (webui/lody/generation/animation_connector.py)
                                 preflight · plan_units · submit · poll · recover · resolve_asset · read_subtitles
                                             │ dossier de tâche + processus détaché
                                             ▼
                               python -m lody.animation.worker <dossier>   (webui/lody/animation/worker.py)
                                 un rendu à la fois · nice 10 · délai par scène · reprise · repli hybride
                                             │ une commande par scène, environnement minimal
                                             ▼
                               moteur de scène  (LODY_ANIMATION_RENDERER, ex. adaptateur Remotion)
```

- `GenerationRequest.render_mode` et `GenerationRequest.animation` sont **absents** de `to_dict()` à leur
  valeur par défaut, et la liaison au storyboard n'est jamais recopiée dans `params` : le storyboard a
  sa propre colonne, qui est immuable après la confirmation.
- Le coût fournisseur est **nul** : aucun appel texte (la narration vient du storyboard), aucune image
  générée, aucun TTS. Le préflight annonce une durée de rendu estimée à environ 7 fois la durée de la
  vidéo, d'après le benchmark #118.

## Dossier de tâche (`<données>/animation/<projet>/<tâche>/`)

| Fichier | Rôle |
|---|---|
| `job.json` | Demande figée : mode, storyboard figé, fichiers d'assets copiés (chemin, SHA-256) |
| `assets/<asset>/<version>/<rôle>.<ext>` | Copies **vérifiées** des fichiers du catalogue utilisés : le worker ne lit jamais ailleurs |
| `scenes/NN-input.json` | Entrée du moteur de scène (`lody.animation-scene/1`) |
| `scenes/NN.mp4`, `scenes/NN.json` | Rendu et trace de chaque scène : statut (`animated` / `fallback`), durée attendue, **durée réelle** mesurée, durée de rendu, SHA-256 |
| `state.json` | `queued`, `running`, `interrupted`, `done`, `failed` ou `cancelled`, avec l'avancement en images et le battement |
| `qa.json` | Contrôle qualité automatique du rendu (#121) |
| `out.mp4`, `manifest.json` | Vidéo finale (H.264 yuv420p) et son manifeste : durée réelle, empreinte, taille, scènes, replis, avertissements |
| `subtitles.srt` | Sous-titres tirés des repères de parole (`render.speech.cues`), décalés par scène |
| `worker.log` | Journal **nettoyé** (`safety.sanitize`) |
| `cancel` | Drapeau d'arrêt : le worker s'arrête entre deux scènes (`AnimationConnector.cancel`) |

Isolation : un dossier par projet, puis un dossier par tâche. `resolve_asset` refuse tout fichier qui
n'est pas dans le dossier de sa propre tâche, et le gel refuse tout asset d'un autre projet.

## Sûreté d'exécution

- **Un seul rendu à la fois** sur la machine (verrou `animation/.render.lock`). Les autres tâches
  restent `queued` et répondent quand même aux demandes d'arrêt.
- **Ressources bornées** : priorité basse (`nice 10`), délai maximal par scène
  (`LODY_ANIMATION_SCENE_TIMEOUT`, 900 s par défaut), 10 minutes de vidéo au plus par tâche, limites du
  contrat (30 scènes, 120 s par scène).
- **Aucun secret transmis** : le worker et le moteur de scène reçoivent un environnement minimal
  (`PATH`, `HOME`, `LANG`, la commande du moteur) et jamais les variables de l'application.
- **Arrêt et reprise sûrs** : SIGTERM ou SIGINT arrêtent proprement le moteur en cours (état
  `interrupted`). Un worker disparu (redémarrage du conteneur) est détecté par son verrou de tâche
  libéré et relancé au plus 3 fois par le suivi. Une scène déjà rendue, dont l'empreinte est vérifiée,
  n'est jamais refaite. Ces relances sont permises parce que le rendu est local et gratuit ; le
  pipeline historique, lui, ne relance jamais rien d'office.

## Moteur de scène (`LODY_ANIMATION_RENDERER`)

La commande est découpée comme dans un shell et appelée ainsi :

```
<commande> --input scenes/NN-input.json --output scenes/NN.render.mp4          # rendu de la scène
<commande> --input scenes/NN-input.json --still <image> --output NN-still.png   # repli « still » (hybride)
```

`NN-input.json` = `{"schema": "lody.animation-scene/1", "canvas", "index", "scene", "assets"}`.
`assets` associe chaque `"<id>@<version>"` aux chemins absolus de ses fichiers copiés. Le moteur traduit
ce JSON **déclaratif** vers des composants écrits et relus à l'avance ; aucun code n'est généré depuis
le storyboard. Sans cette variable, le préflight bloque avec « moteur de rendu 2D non installé »,
renvoie à l'administrateur et nomme la variable dans le détail technique.

Repli hybride, dans l'ordre : le décor du catalogue (PNG/WebP) d'un repli `asset` ; l'image demandée au
moteur pour un repli `still` ; à défaut, un carton neutre. La source utilisée est tracée.

## Limites connues (suites proposées)

- **Moteur de scène** : livré par #150 (`renderers/remotion`, voir [lody-animation-remotion.md](lody-animation-remotion.md)).
  Restent l'image Docker du worker (Node 22, Chrome headless, polices figées) et la licence Remotion (#118).
- **Voix off et musique** : le worker mixe `voice.wav` + `music.wav` (#150), mais la synthèse vocale n'est pas
  encore appelée par le fournisseur : sans fichier déposé, la vidéo reste muette et le préflight l'annonce.
- **Import des fichiers d'assets** : le catalogue (#119) référence des chemins du dossier de données, mais
  aucun écran ne téléverse encore ces fichiers.
- Réédition ciblée par scène et contrôle qualité : voir [lody-animation-reedit.md](lody-animation-reedit.md) (#121).
