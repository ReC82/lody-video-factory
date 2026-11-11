# Voltix décrypte le broadcast — « À quoi sert un mélangeur vidéo en direct ? »

> Ticket #142. **Préproduction sans coût** : scénario, script TTS-safe, storyboard horodaté, fiche lieu,
> liste d'assets et prompts visuels, preset de production. **Aucune vidéo n'est livrée ici** : aucun
> appel TTS, image ou musique payant, rien n'est créé dans une instance Lody. La production réelle attend
> la validation du personnage Voltix (#136), le provider d'animation 2D (#120) et l'accord sur les coûts.

| Élément | Fichier |
|---|---|
| Scénario, script, storyboard, lieu, assets, prompts | ce document |
| Preset de production (template #110) | [`voltix-melangeur/template.json`](voltix-melangeur/template.json) |
| Storyboard animé `lody.animation/1` (#119) | [`voltix-melangeur/storyboard.json`](voltix-melangeur/storyboard.json) |
| Contrôles automatiques | `test/lody/test_episode_voltix_melangeur.py` |

## 1. Fiche épisode

| Champ | Valeur |
|---|---|
| Série | Voltix décrypte le broadcast |
| Personnage | Voltix, **fiche canonique #136** (silhouette, palette, rig inchangés ; aucune modification pour cet épisode) |
| Idée centrale | Le mélangeur vidéo sélectionne et combine, en direct, les sources qu'on lui envoie : c'est lui qui fait passer l'émission d'une caméra à l'autre. |
| Hook | « Comment une émission passe-t-elle d'une caméra à l'autre… sans que personne ne débranche les câbles ? » |
| Public | Grand public curieux, débutants en régie vidéo |
| Format | 9:16, 1080 × 1920, 30 images/s, sous-titres français en phrases entières |
| Durée | Standard **60 s** (10 scènes de 5 à 7 s) ; version courte **~45 s** (section 4) |
| Débit | Rapide (repère Lody 175 mots/min), mais articulé |

## 2. Vérification technique (critères de justesse)

Source de validation indiquée par le ticket : manuel de la gamme Blackmagic ATEM Mini
(`https://documents.blackmagicdesign.com/UserManuals/ATEMMiniManual.pdf`). **Le lien renvoyait une
erreur 404 lors de la préparation (2026-11-11)** : les affirmations ci-dessous sont volontairement
génériques et prudentes ; elles sont à relire contre le manuel en vigueur (page Support de Blackmagic
Design) avant production.

Ce que le script **affirme** :

- Plusieurs caméras envoient chacune leur signal, par **un câble par source**, vers les entrées du
  mélangeur vidéo ; changer de plan se fait au mélangeur, pas en débranchant.
- La **multivue** est un écran de contrôle qui montre toutes les sources (et, sur les modèles qui
  l'offrent, les fenêtres prévisualisation et programme). Elle sert à **choisir** ; ce n'est **pas** l'image
  diffusée.
- **Prévisualisation** (preview) = la source préparée pour le plan suivant ; **programme** (program) =
  l'image réellement envoyée en sortie. Formulé « sur beaucoup de mélangeurs » : certains modèles ou
  modes commutent directement la source au programme sans étape de prévisualisation (sur l'ATEM Mini,
  mode dit « cut bus » par opposition au mode « programme / prévisualisation », selon le modèle et le
  réglage).
- Une **transition** fait passer la prévisualisation au programme : coupe franche (CUT) ou transition
  automatique (AUTO, par exemple un fondu). Après la transition, l'ancienne source programme se retrouve
  en prévisualisation (scène 6, échange des deux fenêtres).
- La **sortie programme** va vers des moniteurs de contrôle puis vers la diffusion **ou** l'enregistrement
  / le streaming : le script ne dit pas qu'elle part « directement à l'antenne ».
- Fondus, titres, incrustations : « **selon le modèle** » ; les transitions disponibles diffèrent d'une
  machine à l'autre.
- Le mélangeur **ne fabrique pas** les images des caméras : il sélectionne et combine les sources
  qu'on lui donne (les titres et générateurs internes de certains modèles restent un détail hors sujet).

Ce que le script **n'affirme pas** (pièges évités) : la multivue n'est jamais appelée « sortie » ; aucune
marque ni modèle n'est cité à l'écran ; aucun type d'effet précis (volet, DVE…) n'est présenté comme
universel ; le transport du signal (SDI, HDMI, IP) n'est pas détaillé — c'est le sujet de l'épisode
câble SDI (#137).

## 3. Script standard (~60 s), TTS-safe

Règles TTS appliquées : nombres écrits en toutes lettres (« caméra deux »), aucun sigle ni mot anglais
prononcé (« CUT », « AUTO », « PREVIEW », « PROGRAM » sont seulement affichés à l'écran), apostrophes
typographiques conservées dans les sous-titres, points de suspension pour les respirations.

<!-- script-standard:debut -->
| # | Temps | Narration (texte exact envoyé au TTS) |
|---|---|---|
| 1 | 0–6 s | Comment une émission passe-t-elle d'une caméra à l'autre… sans que personne ne débranche les câbles ? |
| 2 | 6–12 s | Dans une régie, plusieurs caméras envoient leur image vers un appareil : le mélangeur vidéo. |
| 3 | 12–18 s | Sur un écran multivue, le réalisateur voit toutes les sources, et choisit laquelle passe à l'antenne. |
| 4 | 18–25 s | Sur beaucoup de mélangeurs, il y a la prévisualisation, ce qu'on prépare… et le programme, l'image réellement diffusée. |
| 5 | 25–31 s | Regardez : Voltix prépare la caméra deux, puis appuie sur le bouton de transition. |
| 6 | 31–36 s | Et hop ! La caméra deux passe au programme. |
| 7 | 36–42 s | La sortie programme part ensuite vers les moniteurs de contrôle, puis vers la diffusion ou l'enregistrement. |
| 8 | 42–48 s | Selon le modèle, le mélangeur peut aussi faire des fondus, ajouter des titres ou incruster une image. |
| 9 | 48–54 s | Attention : il ne fabrique pas les images. Il sélectionne et combine les sources qu'on lui donne. |
| 10 | 54–60 s | Voilà pourquoi le mélangeur, c'est un peu le chef d'orchestre de la vidéo en direct ! |
<!-- script-standard:fin -->

≈ 154 mots, soit ≈ 53 s de parole au repère « rapide » : il reste ~7 s pour les respirations, la chute
visuelle et l'entrée/sortie de la musique.

Écarts assumés avec le script provisoire du ticket : question d'accroche mise en forme interrogative
correcte (« passe-t-elle ») ; la multivue est nommée en scène 3 (critère : ne pas la confondre avec le
programme) ; prévisualisation et programme réunis en une scène (« sur beaucoup de mélangeurs » : le mode
dépend de l'équipement) ; ajout de la scène 7 « sortie programme » demandée par le storyboard du ticket ;
« selon ses fonctions » devient « selon le modèle ».

## 4. Version courte (~45 s)

Scènes conservées : 1, 2, 4, 5, 6, 9, 10 — scènes 3, 7 et 8 coupées (la multivue reste visible en
arrière-plan de la scène 2).

| # | Temps | Narration |
|---|---|---|
| 1 | 0–6 s | Comment une émission passe-t-elle d'une caméra à l'autre… sans que personne ne débranche les câbles ? |
| 2 | 6–12 s | Dans une régie, toutes les caméras arrivent dans un appareil : le mélangeur vidéo. |
| 4 | 12–19 s | Souvent, il y a la prévisualisation, ce qu'on prépare… et le programme, l'image réellement diffusée. |
| 5 | 19–25 s | Regardez : Voltix prépare la caméra deux, puis appuie sur le bouton de transition. |
| 6 | 25–30 s | Et hop ! La caméra deux passe au programme. |
| 9 | 30–38 s | Attention : il ne fabrique pas les images. Il sélectionne et combine les sources qu'on lui donne. |
| 10 | 38–45 s | Voilà pourquoi le mélangeur, c'est un peu le chef d'orchestre de la vidéo en direct ! |

## 5. Chutes

- **Chute principale (scène 10)** — Voltix, baguette de chef d'orchestre à la main, rebondit devant les
  trois caméras ; à chaque mouvement de baguette, une caméra « répond » ; titre « Le chef d'orchestre du
  direct ».
- **Chute alternative** — Voltix appuie fièrement sur le bouton de transition… et c'est sa propre tête,
  filmée par la caméra trois en très gros plan, qui passe au programme. Il se recoiffe les ressorts :
  « Bon… on va dire que c'était prévu ! » (≈ 2 s de plus ; remplacer alors la scène 10 et raccourcir la
  scène 8 à « Selon le modèle, il peut aussi faire des fondus ou ajouter des titres. »).

## 6. Storyboard horodaté (standard)

Une action visuelle par scène, 5 à 7 s. Chaque scène a un **repli** (image fixe) pour le rendu hybride
#120. Le détail technique (calques, images-clés, opacités) est dans
[`voltix-melangeur/storyboard.json`](voltix-melangeur/storyboard.json).

| # | Id | Temps | Visuel | Texte à l'écran | Voltix |
|---|---|---|---|---|---|
| 1 | `accroche` | 0–6 s | Régie : Voltix tire sur trois câbles caméra ; panneau rond d'interdiction rouge qui jaillit. | « Interdit ! » | entrée en rebond, `tenir-cable`, `concentration` → `surprise` → `sourire` |
| 2 | `cablage` | 6–12 s | Schéma : trois caméras en haut, **un câble distinct par caméra** qui apparaît l'un après l'autre vers le mélangeur. | « Mélangeur vidéo », « Caméra 1/2/3 » | `montrer` le mélangeur |
| 3 | `multivue` | 12–18 s | Écran multivue : trois petites cases 1, 2, 3 et deux grandes fenêtres étiquetées PREVIEW (vert) et PROGRAM (rouge). | « Multivue : toutes les sources » | `concentration`, `montrer` |
| 4 | `preview-program` | 18–25 s | Deux grandes fenêtres l'une au-dessus de l'autre : PREVIEW — prévisualisation (Caméra 2, cadre vert), PROGRAM — à l'antenne (Caméra 1, cadre rouge). | étiquettes bilingues | `montrer` chaque fenêtre |
| 5 | `preparation` | 25–31 s | Pupitre en gros plan : Voltix sélectionne la caméra deux (rangée prévisualisation), puis vise les boutons CUT / AUTO. | « PREVIEW : Caméra 2 », « PROGRAM : Caméra 1 » | `montrer` puis main sur le bouton |
| 6 | `bascule` | 31–36 s | Changement d'état : les deux fenêtres échangent leur contenu (Caméra 2 au programme, Caméra 1 en prévisualisation). | PREVIEW / PROGRAM | `surprise` puis `rebond` |
| 7 | `sortie` | 36–42 s | Schéma vertical : mélangeur (sortie programme) → moniteur de contrôle → « Diffusion ou enregistrement ». | légendes du schéma | `montrer` |
| 8 | `effets` | 42–48 s | Dans la fenêtre programme : fondu Caméra 1 → Caméra 2, puis incrustation d'un bandeau titre simple. | « Fondu », « Incrustation d'un titre », « Selon le modèle » | `sourire`, `montrer` |
| 9 | `attention` | 48–54 s | Les trois caméras (« Sources fournies ») au-dessus du mélangeur ; légende « Il sélectionne et combine ». | « Il sélectionne et combine » | `cligner`, `concentration` |
| 10 | `chute` | 54–60 s | Voltix chef d'orchestre avec sa baguette devant les trois caméras (section 5). | « Le chef d'orchestre du direct » | `rebond`, `sourire` |

## 7. Fiche lieu

**Régie de démonstration Voltix** — même lieu fictif que les épisodes #140 et #141 (réutiliser leurs
décors si déjà dessinés) :

- **Régie** : pupitre de mélangeur vu de trois quarts, avec une rangée de boutons **programme**, une
  rangée **prévisualisation**, un bouton CUT, un bouton AUTO (et en version enrichie un levier de
  transition) ; au-dessus, un écran multivue.
- **Multivue** : trois petites vignettes numérotées 1, 2, 3 en bas, deux grandes fenêtres en haut,
  PREVIEW à gauche (cadre vert) et PROGRAM à droite (cadre rouge), étiquettes toujours lisibles.
- **Caméras** : trois caméras de plateau stylisées, numérotées 1, 2, 3, chacune avec **son** câble.
- **Personnages secondaires** : aucun indispensable ; le réalisateur éventuel est **fictif, générique et
  de dos** (aucun collègue, cf. #135).
- **Palette** : fond `#14213D`, matériel gris `#3C3F44` / `#8C939B`, programme rouge `#E53935`,
  prévisualisation vert `#43A047`, câbles et flèches `#B4BAC1` ; Voltix garde sa palette #136.

## 8. Assets à dessiner (SVG éditable)

Identifiants provisoires du storyboard : `ast_136000000001` (Voltix, #136) et `ast_142000000001` à
`ast_14200000000c` (épisode), tous en `latest`, à remapper puis figer à l'import (section 11).

| Asset (id provisoire) | Type | MVP | Enrichi |
|---|---|---|---|
| Voltix (`…136…01`) | character | **réutilisé tel quel** depuis #136 | — |
| `decor-regie` (`…01`) | background | aplats : mur, pupitre, rectangle d'écran | lumière d'ambiance, reflets, réalisateur de dos |
| `camera-studio` (`…02`) | prop | silhouette 3/4, objectif, plaque vierge | pied, viseur, câble arrière |
| `melangeur` (`…03`) | prop | boîtier avec deux rangées de 3 boutons | levier de transition, voyants de boutons rouge/vert |
| `multivue` (`…04`) | overlay | grille : 3 petites cases + 2 grandes, cadres vert/rouge | bordures animées, horloge de régie générique |
| `moniteur-controle` (`…05`) | prop | rectangle d'écran sur pied | reflets, étiquette « Contrôle » |
| `interdit` (`…06`) | overlay | cercle rouge barré | rebond, léger tremblement |
| `cable` (`…07`) | prop | trait épais avec connecteur | courbe animée (tracé progressif) |
| `fenetre` (`…08`) | overlay | cadre vide (l'image et la couleur du cadre viennent des calques) | coins arrondis, ombre plate |
| `baguette` (`…09`) | prop | baguette de chef d'orchestre | traînée de mouvement |
| `bandeau-titre` (`…0a`) | overlay | bandeau bas d'écran générique | animation d'entrée glissée |
| `diffusion` (`…0b`) | prop | pictogrammes « émetteur / enregistreur » côte à côte | — |
| `bouton` (`…0c`) | prop | bouton carré éteint | états allumé rouge / vert en calques séparés |

Les états (cadre vert/rouge, bouton allumé, contenu des fenêtres) sont des **calques séparés** basculés
par opacité : aucun état figé dans le dessin, compatible avec le contrat actuel.

## 9. Prompts visuels de référence (images de travail, facultatif et payant)

Uniquement si l'API d'images est disponible **et** le coût autorisé ; le dessin final reste le SVG ci-dessus.
Style commun : *flat vector illustration, clean shapes, thick dark-brown outlines (#2A1F14), no texture, no
gradients except one flat shadow, vertical 9:16, French labels only (PREVIEW / PROGRAM allowed on the
device), no brand logos, no real people*.

1. `accroche` — TV control room, yellow round mascot with spring legs pulling on three camera cables,
   a big red prohibition sign popping above, humorous.
2. `cablage` — technical diagram, three studio cameras at the top, three separate cables each going into a
   box labelled "Mélangeur vidéo".
3. `multivue` — multiview monitor: three small numbered tiles 1 2 3 at the bottom, two large windows on top
   labelled PREVIEW (green frame) and PROGRAM (red frame).
4. `preview-program` — two stacked windows, top green-framed "PREVIEW — prévisualisation" showing camera 2,
   bottom red-framed "PROGRAM — à l'antenne" showing camera 1.
5. `preparation` — close-up of a video switcher panel, a yellow hand pressing the camera 2 button on the
   preview row, CUT and AUTO buttons next to it.
6. `bascule` — same two windows after the transition: camera 2 now in PROGRAM, camera 1 in PREVIEW, motion
   arrows showing the swap.
7. `sortie` — vertical diagram: switcher "sortie programme" → control monitor → "Diffusion ou
   enregistrement" icons; no antenna directly connected to the switcher.
8. `effets` — program window showing a crossfade between camera 1 and camera 2, then a simple lower-third
   title bar; caption "Selon le modèle".
9. `attention` — three cameras labelled "Sources fournies" feeding a switcher, caption "Il sélectionne et
   combine".
10. `chute` — yellow mascot conducting with a baton in front of three studio cameras, title "Le chef
    d'orchestre du direct".

## 10. Voix, musique et sous-titres

- **Voix** : celle **liée à la fiche Voltix** (#136) une fois choisie ; ne jamais saisir d'identifiant de
  voix dans cet épisode ni en inventer un. Débit rapide. Avant tout appel ElevenLabs : budget et
  autorisation du propriétaire.
- **Musique** : instrumentale, discrète et dynamique, **droits vérifiés** (licence notée dans la
  production) ; volume de base ~20 % sous la voix, à ajuster au rendu, ducking pendant la parole, coupée
  à la fin de la narration (fondu court) ; aucun bip synthétique du prototype #118. Un léger « tchac »
  sur la bascule de la scène 6 est possible s'il fait partie de la musique ou d'un bruitage libre de droits.
- **Sous-titres** : phrases entières, apostrophes typographiques ; à l'écran « Caméra 2 » s'écrit en
  chiffres (le TTS lit « caméra deux ») ; les étiquettes du matériel (PREVIEW, PROGRAM, CUT, AUTO) restent
  en anglais comme sur les vrais pupitres, toujours accompagnées du mot français.

## 11. Passage en production (quand #136 et #120 sont validés)

1. Importer le catalogue Voltix #136 et les assets de cet épisode dans le projet (`import_catalog`) ;
   récupérer la table « ancien id → nouvel id ».
2. Remplacer les identifiants provisoires du storyboard (`ast_1360…`, `ast_1420…`) avec `remap_assets`,
   puis figer (`freeze_document`) : les gestes et expressions sont alors vérifiés contre les manifestes.
3. Créer le template de production à partir de `template.json` (champs `name`, `description`,
   `payload`).
4. Relire la section 2 contre le manuel du fabricant en vigueur (le lien du ticket était en 404).
5. Estimer les coûts (TTS, éventuelles images de référence), les faire approuver, puis seulement lancer la
   production. Une vidéo n'est « livrée » qu'avec un MP4 vérifiable (voix, musique, sous-titres).
