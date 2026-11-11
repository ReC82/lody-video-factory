# Contrôle qualité automatique des MP4 Remotion (#166)

> Objectif : qu'une vidéo 2D n'arrive pas en relecture avec une étiquette rognée, un schéma caché par le
> personnage, un objet cité absent de l'image, des sous-titres sur les pieds, un silence ou une musique qui
> traîne après la parole. **Contrôle technique et estimé** : il signale ce qu'il faut regarder ; il ne
> garantit pas la justesse pédagogique ni la qualité du dessin.

Module : `webui/lody/animation/video_qa.py` (schéma `lody.video-qa/1`). Local, déterministe, gratuit :
ffprobe/ffmpeg + lecture des fichiers déjà produits par la chaîne 2D. Aucun appel réseau, aucune génération.

## Où trouver le rapport

- **Automatiquement après chaque rendu 2D** : le worker (#120) écrit `qa/index.html` et `qa/qa.json` dans le
  dossier de la tâche, et un résumé dans `manifest.json["qa"]`. Le contrôle ne fait **jamais** échouer un rendu
  (en cas d'erreur interne : `"status": "unavailable"`, tracé dans `worker.log`).
- **Dans Lody** : page de suivi de la production → bouton **« Rapport de contrôle qualité (HTML) »** sous la
  vidéo ; s'il y a des erreurs ou des points à vérifier, un avertissement « Contrôle qualité automatique : … »
  s'affiche avec les autres avertissements.
- **À la demande** (dossier de tâche, dossier versionné ou fichier MP4) :

  ```bash
  uv run --no-sync python scripts/animation/qa_video.py docs/animation/voltix-150 --out /tmp/qa-voltix
  # puis ouvrir /tmp/qa-voltix/index.html dans un navigateur
  ```

  Code de sortie 1 s'il y a au moins une erreur, 2 si le contrôle est impossible.

Le rapport HTML est **autonome** : les captures (début / milieu / fin de chaque scène, hors transitions) y sont
intégrées ; il contient aussi le lien et le lecteur du MP4 (chemin relatif : il fonctionne tant que le rapport
reste à côté de la vidéo), les répliques avec leurs minutages et les constats par scène.

## Entrées lues

| Fichier | Rôle | Dossier de tâche | Dossier versionné |
|---|---|---|---|
| MP4 | vidéo contrôlée | `out.mp4` | l'unique `*.mp4` |
| storyboard figé | scènes, objets, répliques | `job.json["storyboard"]` | `storyboard.json` |
| dimensions des assets | boîtes des objets | `job.json["asset_info"]` | `asset-info.json` |
| `manifest.json` | empreinte, replis, musique déclarée | oui | oui |
| `subtitles.srt` | synchronisation SRT | oui | oui |
| `voice.wav` / `music.wav` | parole réelle, voix coupée | si présents | — |

## Contrôles

| Famille | Constat (`id`) | Niveau |
|---|---|---|
| Technique | format non 9:16 (`aspect`), aucune piste vidéo/audio (`no_video`, `no_audio`), durée fausse (`duration`) | erreur |
| | résolution ou cadence ≠ storyboard, nombre d'images (écart > 1), pixels non yuv420p, piste audio plus courte/longue que la vidéo, MP4 modifié après le manifeste, scène de repli | à vérifier |
| Image | écran noir ≥ 0,5 s (`black`) | erreur |
| | bandes noires (`borders`), capture presque noire (`dark_frame`) ou uniforme (`empty_frame`) | à vérifier |
| Mise en page (estimée) | texte rogné par un bord (`text_clipped`), schéma/objet coupé (`asset_clipped`, > 5 % ; personnage > 20 %) | à vérifier |
| | élément couvert à plus de 30 % par un personnage (`hidden_by_character`) | à vérifier |
| | sous-titre hors zone de sécurité (`subtitle_unsafe`), sur les pieds du personnage (`subtitle_on_character`), sur un schéma (`subtitle_covers`) | à vérifier |
| | objet cité dans une réplique mais absent de l'image à ce moment (`cited_object_missing`), locuteur hors champ (`speaker_offscreen`) | à vérifier |
| | texte hors zone de sécurité mais dans l'image (`text_unsafe`), dimensions d'asset inconnues (`geometry_unknown`) | info |
| Synchronisation | voix plus longue que la vidéo (`voice_cut`) | erreur |
| | SRT ≠ répliques du storyboard (`srt_mismatch`, > 50 ms), silence complet ≥ 1 s (`audio_gap`), sous-titre sans parole (`speech_missing`), voix sans sous-titre ≥ 0,8 s (`speech_unsubtitled`), musique seule > 3 s après la dernière réplique (`music_tail`) | à vérifier |
| | musique seule > 4 s entre deux répliques (`music_gap`), pas de SRT (`srt_missing`) | info |

Détails et limites :

- **Zone de sécurité 9:16** : marges de 8 % en haut, 10 % en bas, 4 % sur les côtés (interface des plateformes
  verticales). Constantes `SAFE_*` en tête du module.
- **Boîtes estimées** avec les mêmes interpolations que le moteur (`renderers/remotion/lib/anim.mjs`) et ses
  styles de texte (largeur moyenne d'un caractère gras ≈ 0,6 × la taille). La boîte d'un personnage englobe tout
  son dessin : elle est réduite (15 % sur les côtés, 10 % en haut) et ses « pieds » sont le quart bas. La rotation
  de caméra est ignorée. Un objet **en mouvement** (entrée, sortie) n'est pas déclaré rogné.
- **Objet cité** : mots des noms d'assets du storyboard (`frozen.assets[].name`, nom court du fichier) retrouvés
  dans une réplique ; l'objet est « montré » si un asset portant ce mot ou un texte le contenant est à l'image
  au milieu de la réplique. Nombres et mots outils ignorés.
- **Parole** : avec `voice.wav`, fenêtres de 100 ms au-dessus de −40 dBFS ; sans elle (dossier versionné),
  estimation sur le mixage : la parole doit dépasser d'au moins 6 dB le niveau médian hors répliques.

## Coordination avec #121 / PR #157

#121 apporte des contrôles **du storyboard avant rendu** (`animation/qa.py` : sous-titres trop longs ou trop
rapides, continuité) et le cache par scène. Ce module ne touche ni au cache ni à la réédition : il juge la
**sortie finale**. Les deux pourront être réunis dans un même rapport une fois #157 fusionnée.

## Pilote Voltix #150 (dossier versionné)

`docs/animation/voltix-150/asset-info.json` donne les dimensions des 4 assets (catalogue #136 + coupe coaxiale
de la démo). Résultat actuel : 0 erreur ; **1 point à vérifier** — scène 1, le sous-titre « Aujourd'hui : le
câble SDI. » recouvre les chaussures de Voltix (confirmé sur la capture du milieu) ; 1 info — l'étiquette « Âme en
cuivre » déborde de la marge droite de sécurité.
