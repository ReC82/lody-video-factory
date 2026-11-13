# Voltix décrypte le broadcast — « Pourquoi une caméra a une lumière rouge ? Le Tally »

> Ticket #140. **Préproduction sans coût** : scénario, script TTS-safe, storyboard horodaté, fiche lieu,
> liste d'assets et prompts visuels, preset de production. **Aucune vidéo n'est livrée ici** : aucun
> appel TTS, image ou musique payant, rien n'est créé dans une instance Lody. La production réelle attend
> la validation du personnage Voltix (#136), le provider d'animation 2D (#120) et l'accord sur les coûts.

| Élément | Fichier |
|---|---|
| Scénario, script, storyboard, lieu, assets, prompts | ce document |
| Preset de production (template #110) | [`voltix-tally/template.json`](voltix-tally/template.json) |
| Storyboard animé `lody.animation/1` (#119) | [`voltix-tally/storyboard.json`](voltix-tally/storyboard.json) |
| Contrôles automatiques | `test/lody/test_episode_voltix_tally.py` |

## 1. Fiche épisode

| Champ | Valeur |
|---|---|
| Série | Voltix décrypte le broadcast |
| Personnage | Voltix, **fiche canonique #136** (silhouette, palette, rig inchangés ; aucune modification pour cet épisode) |
| Idée centrale | Le Tally est le signal lumineux, envoyé par la régie, qui indique quelle caméra est à l'antenne. |
| Hook | « Cette petite lumière rouge sur une caméra ? Elle peut vous éviter une énorme bourde en direct ! » |
| Public | Grand public curieux, débutants en régie vidéo |
| Format | 9:16, 1080 × 1920, 30 images/s, sous-titres français en phrases entières |
| Durée | Standard **63 s** (10 scènes de 6 à 7 s) ; version courte **~45 s** (section 4) |
| Débit | Rapide (« rapide » du brief Lody, repère 175 mots/min), mais articulé |

## 2. Vérification technique (critères de justesse)

Source de validation : [Roland — « What is the Tally Connector For? »](https://support.roland.com/hc/en-us/articles/206558536-Roland-Switchers-What-is-the-Tally-Connector-For)
— le connecteur Tally d'un mélangeur envoie un signal indiquant **quelle entrée est sélectionnée**, afin
d'allumer un voyant (« tally light ») sur la caméra correspondante.

Ce que le script **affirme** :

- Le Tally est un **signal envoyé par le mélangeur / la régie** vers les caméras ; le voyant n'est que son
  affichage. Il indique la caméra **à l'antenne** (sortie programme).
- À l'antenne, le voyant est **généralement** rouge (jamais « toujours »).
- Sur **certains** systèmes, une autre couleur (souvent verte) signale la caméra **en prévisualisation**,
  c'est-à-dire préparée pour le plan suivant. Formulé « selon équipement ».
- Le voyant est vu par le présentateur (voyant avant, sur la caméra) **et** par l'opérateur (voyant dans le
  viseur ou à l'arrière).

Ce que le script **n'affirme pas** (pièges évités) :

- « Rouge = enregistrement » : faux en général. Sur un caméscope autonome, un voyant rouge peut signaler
  l'enregistrement (REC) ; ce n'est **pas** le Tally. La scène 9 le dit explicitement.
- Confondre prévisualisation et antenne : la caméra en preview n'est **pas** diffusée.
- Un code couleur universel : les couleurs et fonctions exactes dépendent du matériel (scène 9).
- Le mode de transport du signal (contacts GPI, câble multibroche, réseau IP, voie de commande de caméra)
  n'est volontairement pas détaillé : hors de l'idée centrale, il varie selon les installations.

## 3. Script standard (~63 s), TTS-safe

Règles TTS appliquées : nombres écrits en toutes lettres (« caméra un »), aucun sigle (« PGM », « PVW »,
« REC » ne sont jamais prononcés, seulement affichés à l'écran), « Tally » écrit avec majuscule et prononcé
« ta-li » (vérifier à l'écoute ; à défaut, remplacer par « Talli » dans le texte envoyé au TTS uniquement),
apostrophes typographiques conservées dans les sous-titres, points de suspension pour les respirations.

<!-- script-standard:debut -->
| # | Temps | Narration (texte exact envoyé au TTS) |
|---|---|---|
| 1 | 0–6 s | Cette petite lumière rouge sur une caméra ? Elle peut vous éviter une énorme bourde en direct ! |
| 2 | 6–12 s | Dans une émission à plusieurs caméras, le réalisateur choisit l'image qui part à l'antenne. |
| 3 | 12–18 s | Toutes les caméras arrivent au mélangeur, et une seule image sort vers le programme. |
| 4 | 18–24 s | Mais comment le présentateur sait-il quelle caméra le filme en direct ? Grâce au Tally ! |
| 5 | 24–31 s | Le mélangeur renvoie un signal à la caméra choisie, et son voyant s'allume, généralement en rouge. |
| 6 | 31–38 s | Regardez : caméra un, rouge. On change de plan… caméra deux, rouge ! |
| 7 | 38–44 s | Le présentateur regarde la bonne caméra, et l'opérateur sait qu'il doit garder son cadrage. |
| 8 | 44–51 s | Sur certains systèmes, une autre couleur, souvent verte, signale la caméra prête en prévisualisation. |
| 9 | 51–57 s | Attention : ce n'est pas le voyant d'enregistrement, et les couleurs dépendent du matériel. |
| 10 | 57–63 s | Voilà, le Tally, c'est le petit signal lumineux qui évite de regarder du mauvais côté ! |
<!-- script-standard:fin -->

≈ 150 mots, soit ≈ 51 s de parole au repère « rapide » : il reste ~12 s pour les respirations, la chute
visuelle et l'entrée/sortie de la musique.

Écart assumé avec le script provisoire du ticket : la phrase « prévisualisation » est déplacée **après** la
démonstration (ordre du storyboard du ticket, plus clair : on comprend le rouge avant la nuance) ; la scène 3
explicite le trajet caméra → mélangeur → programme ; la scène 9 ajoute la distinction Tally / voyant
d'enregistrement demandée par les critères de justesse.

## 4. Version courte (~45 s)

Scènes conservées : 1, 4, 5, 6, 8 (raccourcie), 9 (raccourcie), 10 — scènes 2, 3 et 7 coupées, leur
visuel est repris en arrière-plan de la scène 4 (régie visible derrière Voltix).

| # | Temps | Narration |
|---|---|---|
| 1 | 0–6 s | Cette petite lumière rouge sur une caméra ? Elle peut vous éviter une énorme bourde en direct ! |
| 4 | 6–12 s | Avec plusieurs caméras, comment le présentateur sait-il laquelle le filme en direct ? Grâce au Tally ! |
| 5 | 12–19 s | Le mélangeur renvoie un signal à la caméra choisie, et son voyant s'allume, généralement en rouge. |
| 6 | 19–26 s | Regardez : caméra un, rouge. On change de plan… caméra deux, rouge ! |
| 8 | 26–32 s | Sur certains systèmes, une autre couleur signale la caméra prête en prévisualisation. |
| 9 | 32–38 s | Attention : ce n'est pas le voyant d'enregistrement, et les codes dépendent du matériel. |
| 10 | 38–45 s | Voilà, le Tally, c'est le petit signal lumineux qui évite de regarder du mauvais côté ! |

## 5. Chutes

- **Chute principale (scène 10)** — avant / après en écran partagé : en haut, le présentateur parle au
  mauvais objectif, panneau « Oups ! » ; en bas, il suit le voyant rouge et sourit à la bonne caméra.
  Voltix rebondit entre les deux et pointe le voyant.
- **Chute alternative** — Voltix termine son explication face à la caméra un… pendant que le voyant de la
  caméra trois s'allume. Il remarque le rouge, pivote sur ses ressorts vers la caméra trois et reprend,
  faussement nonchalant : « Je le savais, évidemment ! » (≈ 2 s de plus ; remplacer alors la scène 10 et
  retirer « Voilà, » pour tenir 63 s).

## 6. Storyboard horodaté (standard)

Scènes de 6 à 7 s, une action visuelle par scène. Chaque scène a un **repli** (image fixe) pour le rendu
hybride #120. Le détail technique (calques, images-clés, opacités) est dans
[`voltix-tally/storyboard.json`](voltix-tally/storyboard.json).

| # | Id | Temps | Visuel | Texte à l'écran | Voltix |
|---|---|---|---|---|---|
| 1 | `accroche` | 0–6 s | Plateau : le présentateur parle à la caméra un (voyant éteint) alors que la caméra deux a le voyant rouge. Voltix jaillit du bas avec le panneau « Oups ! ». | « Oups ! » | entrée en rebond, `surprise` puis `sourire`, geste `montrer` vers la caméra deux |
| 2 | `regie` | 6–12 s | Régie : réalisateur de dos au pupitre du mélangeur ; écran multivue avec caméras 1, 2, 3. | « Régie » | à côté du pupitre, `saluer` |
| 3 | `flux` | 12–18 s | Schéma : trois caméras → mélangeur → « Programme » ; flèches grises, une seule flèche sortante rouge. | « Caméras → Mélangeur → Programme » | `montrer` le schéma, `concentration` |
| 4 | `question` | 18–24 s | Le présentateur hésite entre deux objectifs ; point d'interrogation ; le mot « Tally » apparaît. | « Tally » | `confusion` puis `sourire` sur « Grâce au Tally ! » |
| 5 | `voyant-rouge` | 24–31 s | Gros plan caméra un : flèche rouge **retour** mélangeur → caméra (sens distinct du flux image), le voyant situé **sur la caméra** s'allume en rouge. | « À l'antenne » | `montrer` le voyant |
| 6 | `commutation` | 31–38 s | Plateau large : le rouge s'éteint sur la caméra un et s'allume sur la caméra deux, synchronisé sur « caméra deux, rouge ! ». | « Caméra 1 » puis « Caméra 2 » | `rebond` sur le changement |
| 7 | `bon-regard` | 38–44 s | Le présentateur tourne la tête vers la caméra deux ; l'opérateur derrière elle garde son cadre (voyant du viseur rouge). | — | petit pouce / `sourire` |
| 8 | `previsualisation` | 44–51 s | Caméra un rouge, caméra trois **verte** ; étiquette « Prévisualisation — selon équipement ». | « Selon équipement » | `concentration`, `montrer` le vert |
| 9 | `attention` | 51–57 s | Deux vignettes : caméscope avec voyant « REC » barré d'un « ≠ » vers la caméra de plateau et son Tally. | « Tally ≠ REC » | `concentration`, sourcil levé |
| 10 | `chute` | 57–63 s | Avant / après (section 5), panneau « Oups ! » puis pouce levé. | « Avant » / « Après » | `rebond`, `sourire` |

## 7. Fiche lieu

**Plateau et régie de démonstration Voltix** (lieu fictif, réutilisable pour #141 et #142) :

- **Plateau** : fond bleu nuit uni avec un bandeau de projecteurs stylisé ; bureau de présentation
  arrondi ; trois caméras de plateau sur pied à roulettes, numérotées 1, 2, 3 sur une plaque blanche ;
  sur chaque caméra, un **voyant Tally avant** au-dessus de l'objectif et un **voyant arrière** près du
  viseur (visible de l'opérateur).
- **Régie** : pupitre de mélangeur vu de trois quarts (une rangée de boutons « programme » et une rangée
  « prévisualisation », un levier de transition) ; au-dessus, un écran multivue avec trois vignettes
  caméra et deux grandes vignettes encadrées rouge (programme) et vert (prévisualisation).
- **Personnages secondaires** : un présentateur et un opérateur **fictifs, génériques et non
  nominatifs**, en aplats simples (aucun visage réaliste, aucun collègue — cf. #135).
- **Palette** : fond `#14213D`, éléments techniques gris `#3C3F44` / `#8C939B`, rouge Tally `#E53935`,
  vert prévisualisation `#43A047`, flèches neutres `#B4BAC1` ; Voltix garde sa palette #136.

## 8. Assets à dessiner (SVG éditable)

Les voyants sont des **calques séparés** (cercles « éteint », « rouge », « vert » superposés) que le
storyboard bascule par opacité : aucun état n'est figé dans le dessin de la caméra.

| Asset | Type | MVP | Enrichi |
|---|---|---|---|
| Voltix | character | **réutilisé tel quel** depuis #136 (`voltix-catalogue.json`) | — |
| `decor-plateau` | background | aplat bleu nuit + bureau | projecteurs, reflets au sol |
| `decor-regie` | background | pupitre en aplat, multivue en rectangles | boutons détaillés, levier, lumière d'ambiance |
| `camera-studio` | prop | silhouette de profil 3/4, objectif, plaque numérotée vierge | pied à roulettes, poignées, viseur, câble arrière |
| `voyant-eteint` / `voyant-rouge` / `voyant-vert` | overlay | cercle plein avec contour | halo radial, reflet |
| `presentateur` | character | buste en aplats, tête orientable (gauche/face/droite) | expressions `neutre`, `surprise`, `sourire`, geste `tourner-tete` |
| `operateur` | character | silhouette derrière la caméra | main sur la poignée, voyant de viseur visible |
| `schema-flux` | overlay | 3 boîtes caméra, 1 boîte mélangeur, 1 boîte programme, flèches | flèche retour Tally animée en pointillés |
| `panneau-oups` | prop | pancarte « Oups ! » | rebond et inclinaison |
| `camescope-rec` | prop | caméscope de poing, pastille « REC » | écran latéral ouvert |

## 9. Prompts visuels de référence (images de travail, facultatif et payant)

Uniquement si l'API d'images est disponible **et** le coût autorisé ; le dessin final reste le SVG ci-dessus.
Style commun : *flat vector illustration, clean shapes, thick dark-brown outlines (#2A1F14), no texture, no
gradients except one flat shadow, vertical 9:16, French labels only, no brand logos, no real people*.

1. `accroche` — TV studio set at night, presenter at a round desk speaking to the wrong camera, the camera
   next to it has a small red light above the lens, yellow round mascot with spring legs popping up holding
   a sign "Oups !".
2. `regie` — TV control room seen from behind a director, video switcher panel with two rows of buttons,
   multiview monitor with three camera thumbnails, one bordered red, one bordered green.
3. `flux` — simple technical diagram, three camera icons with grey arrows into a box labelled "Mélangeur",
   one single red arrow out to a box labelled "Programme".
4. `question` — presenter hesitating between two studio cameras, big question mark, word "Tally".
5. `voyant-rouge` — close-up of a studio camera numbered 1, red tally lamp lit on top front of the camera,
   red dashed arrow coming back from a switcher box to the camera.
6. `commutation` — three studio cameras in a row; red light off on camera 1 and lit on camera 2.
7. `bon-regard` — presenter turning to face camera 2 with a red light, camera operator behind it holding
   the handles, small red light near the viewfinder.
8. `previsualisation` — camera 1 with red light, camera 3 with green light, label "Prévisualisation —
   selon équipement".
9. `attention` — side-by-side: handheld camcorder with a "REC" dot, not-equal sign, studio camera with a
   tally light.
10. `chute` — split screen "Avant" / "Après": presenter looking at the wrong lens with a "Oups !" sign, then
    smiling at the camera with the red light, yellow mascot giving a thumbs up.

## 10. Voix, musique et sous-titres

- **Voix** : celle **liée à la fiche Voltix** (#136) une fois choisie ; ne jamais saisir d'identifiant de
  voix dans cet épisode ni en inventer un. Débit rapide. Avant tout appel ElevenLabs : budget et
  autorisation du propriétaire.
- **Musique** : instrumentale, discrète et dynamique, **droits vérifiés** (licence notée dans la
  production) ; volume de base ~20 % sous la voix, à ajuster au rendu, ducking pendant la parole, coupée
  à la fin de la narration (fondu court) ; aucun bip synthétique du prototype #118.
- **Sous-titres** : phrases entières, apostrophes typographiques, « Tally » avec majuscule ; à
  l'écran, « Caméra 1 » s'écrit en chiffres (le TTS, lui, lit « caméra un »).

## 11. Passage en production (quand #136 et #120 sont validés)

1. Importer le catalogue Voltix #136 et les assets de cet épisode dans le projet (`import_catalog`) ;
   récupérer la table « ancien id → nouvel id ».
2. Remplacer les identifiants provisoires du storyboard (`ast_1360…`, `ast_1400…`) avec `remap_assets`,
   puis figer (`freeze_document`) : les gestes et expressions sont alors vérifiés contre les manifestes.
3. Créer le template de production à partir de `template.json` (champs `name`, `description`,
   `payload`).
4. Estimer les coûts (TTS, éventuelles images de référence), les faire approuver, puis seulement lancer la
   production. Une vidéo n'est « livrée » qu'avec un MP4 vérifiable (voix, musique, sous-titres).
