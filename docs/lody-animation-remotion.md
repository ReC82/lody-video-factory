# Moteur de scène Remotion (adaptateur `lody.animation-scene/1`)

> Ticket #150. Suite de #118 (prototype), #119 (contrat), #120 (fournisseur et worker), #136 (rig Voltix).

`renderers/remotion/` est le moteur de scène appelé par le worker du fournisseur Animation 2D. Il interprète
l'entrée de scène `lody.animation-scene/1` écrite par le worker et le rig `lody.rig/1` des personnages.

```
lody-remotion --input scenes/NN-input.json --output scenes/NN.render.mp4
lody-remotion --input scenes/NN-input.json --still <image> --output scenes/NN-still.png
```

Code de sortie 0 = fichier produit ; 2 = arguments invalides ; 1 = échec (entrée refusée ou rendu).

## Installation (DEV)

```bash
cd renderers/remotion && npm ci          # Node 22 ; Chrome headless téléchargé au premier rendu (~100 Mo)
export LODY_ANIMATION_RENDERER="node /chemin/renderers/remotion/bin/lody-remotion.mjs"
```

Sans `LODY_ANIMATION_RENDERER`, le préflight du mode Animation 2D bloque (« moteur de rendu 2D non installé »).
Le bundle webpack est construit une fois puis réutilisé (`renderers/remotion/.bundle/<empreinte>`, ignoré par git).

## Ce que le moteur interprète

- **Calques et objets** : `asset` (SVG, PNG/WebP, personnage riggé) et `text` (styles `title`, `subtitle`,
  `caption`, `label`), avec `start`/`end`, images-clés de position, échelle, rotation, opacité.
- **Easing** : `linear`, `ease_in`, `ease_out`, `ease_in_out`, `spring`.
- **Caméra** : images-clés `x`, `y`, `zoom`, `rotation` ; **transitions** d'entrée/sortie (`cut`, `fade`, `slide_left|right|up`, `zoom`).
- **Rig `lody.rig/1`** : rotations aux pivots, variantes yeux / sourcils / bouche, expressions par images-clés,
  gestes interpolés (`saluer`, `montrer`, `tenir-cable`, `rebond`, `cligner`), clignements automatiques,
  respiration, ressorts.
- **Synchro labiale** : si l'objet a `lipsync: true` et que la scène porte `speech.cues`, la bouche suit les
  visèmes du texte, modulés par l'enveloppe de la voix (`voice.wav` transmis par le worker avec son décalage).
- **Sous-titres** incrustés depuis `speech.cues` ; le worker produit en plus `subtitles.srt`.

Sécurité : les fichiers sont lus côté Node aux chemins fournis par le worker (copies vérifiées par
empreinte) ; la page de rendu ne reçoit que leur contenu et n'accède pas au disque. Taille max 8 Mo par fichier.

## Audio (worker)

`webui/lody/animation/audio.py` mixe `voice.wav` et `music.wav` du dossier de tâche : musique ≈ 20 % du
niveau de la voix (−14 dB), ducking en chaîne latérale, fondus, durée exacte. La provenance (`audio.json`,
« provisoire », licence) est recopiée dans le manifeste. `voice_plan(fiche)` choisit la voix ElevenLabs
enregistrée dans la fiche personnage (accord de coût requis) ou signale une voix provisoire.

## Ressources

Une page Chrome à la fois (`concurrency: 1`), H.264 yuv420p CRF 20 sans audio par scène. Démo #150
(586 images 1080×1920) : ≈ 40 s par scène de 5 s, ≈ 110 % CPU, pic ≈ 1,5 Go avec le premier empaquetage.

## Tests

```bash
cd renderers/remotion && node --test test/*.test.mjs
uv run --no-sync python -m pytest -q test/lody/test_animation_soundtrack.py test/lody/test_animation_provider.py
```

## Limites

- **Licence Remotion** à trancher par le propriétaire avant tout usage en production (voir #118).
- Image Docker du worker et pilote DEV : [lody-animation-worker-docker.md](lody-animation-worker-docker.md) (#154) ; non déployée.
- Voix et musique de la démo strictement provisoires ; rig Voltix v1 non validé visuellement.
- La TTS n'est pas encore branchée dans le fournisseur : la démo dépose `voice.wav` elle-même.

Démonstration : [docs/animation/voltix-150](animation/voltix-150/README.md).
