# Voltix décrypte le broadcast — « Pourquoi synchroniser les caméras ? Le Genlock »

> Ticket #141. **Préproduction sans coût** : scénario, script TTS-safe, storyboard horodaté, fiche lieu,
> liste d'assets et prompts visuels, preset de production. **Aucune vidéo n'est livrée ici** : aucun
> appel TTS, image ou musique payant, rien n'est créé dans une instance Lody. La production réelle attend
> la validation du personnage Voltix (#136), le provider d'animation 2D (#120) et l'accord sur les coûts.

| Élément | Fichier |
|---|---|
| Scénario, script, storyboard, lieu, assets, prompts | ce document |
| Preset de production (template #110) | [`voltix-genlock/template.json`](voltix-genlock/template.json) |
| Storyboard animé `lody.animation/1` (#119) | [`voltix-genlock/storyboard.json`](voltix-genlock/storyboard.json) |
| Contrôles automatiques | `test/lody/test_episode_voltix_genlock.py` |

## 1. Fiche épisode

| Champ | Valeur |
|---|---|
| Série | Voltix décrypte le broadcast |
| Personnage | Voltix, **fiche canonique #136** (silhouette, palette, rig inchangés ; aucune modification pour cet épisode) |
| Idée centrale | Même fréquence ne veut pas dire même instant : le Genlock aligne le timing des caméras sur une référence commune. |
| Hook | « Deux caméras filment à la même cadence. Alors pourquoi leurs images ne sont-elles pas forcément synchronisées ? » |
| Public | Grand public curieux, débutants en régie vidéo |
| Format | 9:16, 1080 × 1920, 30 images/s, sous-titres français en phrases entières |
| Durée | Standard **63 s** (10 scènes de 6 à 7 s) ; version courte **~45 s** (section 4) |
| Débit | Rapide (« rapide » du brief Lody, repère 175 mots/min), mais articulé |

## 2. Vérification technique (critères de justesse)

Source de validation : [PTZOptics — Rise 4K, « Broadcast advanced connections »](https://docs.ptzoptics.com/docs/cameras/rise-4k/broadcast-advanced-connections/)
— « Genlock (Generator Lock) synchronizes the timing of multiple video devices » ; les appareils se
verrouillent sur un signal de référence partagé, ce qui facilite des commutations plus propres ; l'entrée
Genlock est un **BNC, en entrée seulement** ; « Genlock carries only a timing reference. It does not carry
video, audio, or control signals » ; « Unlike Genlock, Timecode does not synchronize video timing ».

Ce que le script **affirme** :

- Deux caméras réglées sur la même cadence ont chacune **leur propre horloge** : leurs images ne
  commencent pas forcément au même instant (décalage de phase, et légère dérive possible avec le temps).
- Le Genlock consiste à envoyer à chaque caméra une **référence commune** issue d'un générateur ; les
  caméras alignent leur timing dessus.
- Cela **facilite** la synchronisation des sources et les changements de plan, surtout dans les
  installations exigeantes (formulation prudente, pas « indispensable »).
- **Certains** mélangeurs savent resynchroniser des sources non verrouillées (synchroniseur d'image) : le
  Genlock n'est **pas obligatoire pour tous les directs**. Visuel : « peut ajouter un léger retard »,
  **sans aucun chiffre**.
- Le Genlock **n'est pas un timecode** (le timecode numérote les images, il ne règle pas leur timing).
- La référence **ne transporte pas l'image** : elle ne sert qu'au timing.

Ce que le script **n'affirme pas** (pièges évités) :

- « Sans Genlock, impossible de commuter » : faux, cf. synchroniseurs (scène 7).
- « Le câble Genlock transporte la vidéo » : faux (scène 9, comparaison avec le câble SDI).
- Confusion Genlock / timecode (scène 8).
- Le type exact de référence (salve de noir, synchronisation tri-niveaux…) et les réglages fins (phase
  horizontale, compensation des longueurs de câble) : hors idée centrale, variables selon les
  équipements ; on ne les nomme pas.
- Une durée chiffrée de retard d'un synchroniseur.

## 3. Script standard (~63 s), TTS-safe

Règles TTS appliquées : aucun chiffre, aucun sigle prononcé (« SDI », « BNC », « GENLOCK » en
capitales ne sont qu'affichés à l'écran), « Genlock » écrit avec une majuscule et prononcé
« djèn-lok » (vérifier à l'écoute ; à défaut, écrire « Djènlok » dans le texte envoyé au TTS
uniquement), « timecode » et « broadcast » à vérifier de même (à défaut « taïmcode », « brodcast »),
points de suspension pour les respirations.

<!-- script-standard:debut -->
| # | Temps | Narration (texte exact envoyé au TTS) |
|---|---|---|
| 1 | 0–6 s | Deux caméras filment à la même cadence. Alors pourquoi leurs images ne sont-elles pas forcément synchronisées ? |
| 2 | 6–12 s | Imaginez deux musiciens qui jouent à la même vitesse… mais pas sur le même tempo ! |
| 3 | 12–19 s | C'est pareil avec deux caméras : chacune suit sa propre horloge, donc leurs images ne démarrent pas ensemble. |
| 4 | 19–25 s | En broadcast, on peut leur envoyer une référence commune. Ça s'appelle le Genlock ! |
| 5 | 25–31 s | Les caméras alignent alors leur timing sur ce signal de référence. |
| 6 | 31–38 s | Résultat : on facilite la synchronisation des sources et les changements de plan, surtout dans les installations exigeantes. |
| 7 | 38–45 s | Mais attention : certains mélangeurs savent resynchroniser des sources qui ne sont pas verrouillées. |
| 8 | 45–51 s | Le Genlock n'est donc pas obligatoire pour tous les directs. Et ce n'est pas un timecode ! |
| 9 | 51–57 s | Et non : cette référence ne transporte pas l'image. Elle sert seulement à synchroniser le temps. |
| 10 | 57–63 s | Moralité : même fréquence ne veut pas forcément dire même instant ! |
<!-- script-standard:fin -->

≈ 145 mots, soit ≈ 50 s de parole au repère « rapide » : il reste ~13 s pour les respirations, la chute
visuelle et l'entrée/sortie de la musique. Chaque réplique tient dans sa scène au débit rapide (contrôlé
par le test).

Écarts assumés avec le script provisoire du ticket :

- scène 3 : ajout de « donc leurs images ne démarrent pas ensemble » pour relier l'horloge au décalage
  visible des trames ;
- scène 8 : ajout de « Et ce n'est pas un timecode ! » (critère de justesse Genlock ≠ timecode, qui
  n'était pas dit dans le script provisoire) ;
- scène 9 : « l'image de la caméra » raccourci en « l'image » et « seulement » ajouté, pour tenir 6 s ;
- le storyboard du ticket (8 plans, certains de 8–9 s) est redécoupé en 10 scènes de 6–7 s : le plan
  « 40–48 » devient les scènes 7 et 8, le plan « 48–57 » la scène 9, la morale a sa propre scène 10.

## 4. Version courte (~45 s)

Scènes conservées : 1, 3, 4, 5, 7, 9, 10 (2, 6 et 8 coupées ; la mention « pas obligatoire » est
fusionnée dans la 7). Les deux musiciens restent en clin d'œil visuel en arrière-plan de la scène 3.

| # | Temps | Narration |
|---|---|---|
| 1 | 0–6 s | Deux caméras filment à la même cadence. Alors pourquoi leurs images ne sont-elles pas forcément synchronisées ? |
| 3 | 6–12 s | Chacune suit sa propre horloge, donc leurs images ne démarrent pas ensemble. |
| 4 | 12–18 s | En broadcast, on peut leur envoyer une référence commune. Ça s'appelle le Genlock ! |
| 5 | 18–24 s | Les caméras alignent alors leur timing sur ce signal : les changements de plan sont plus propres. |
| 7 | 24–32 s | Mais certains mélangeurs resynchronisent des sources non verrouillées : le Genlock n'est pas toujours obligatoire. |
| 9 | 32–39 s | Et cette référence ne transporte pas l'image, ni un timecode. Elle synchronise seulement le temps. |
| 10 | 39–45 s | Moralité : même fréquence ne veut pas forcément dire même instant ! |

## 5. Chutes

- **Chute principale (scène 10)** — les deux chronomètres de l'accroche reviennent ; sur « Moralité »,
  le chronomètre B rattrape A et leurs aiguilles se superposent ; titre « Même fréquence ≠ même
  instant ». Voltix rebondit, salue et fait un clin d'œil.
- **Chute alternative (humoristique)** — Voltix se dédouble en deux Voltix qui rebondissent sur leurs
  ressorts à la même vitesse mais décalés (« boing… boing » à contretemps). Un petit générateur
  « Référence » apparaît et clignote : les deux rebondissent enfin ensemble. Le second Voltix, fier :
  « Ah ! Moi, je suis verrouillé ! » — et le premier : « Sur le goûter, surtout. » (≈ 3 s de plus ;
  remplacer alors la scène 10 et raccourcir la scène 6 à « Résultat : des changements de plan plus
  propres. » pour tenir ~63 s ; la duplication de Voltix réutilise le même rig #136, sans le modifier).

## 6. Storyboard horodaté (standard)

Scènes de 6 à 7 s, une action visuelle par scène. Chaque scène a un **repli** (image fixe) pour le rendu
hybride #120. Le détail (calques, images-clés, opacités) est dans
[`voltix-genlock/storyboard.json`](voltix-genlock/storyboard.json).

| # | Id | Temps | Visuel | Texte à l'écran | Voltix |
|---|---|---|---|---|---|
| 1 | `accroche` | 0–6 s | Deux petites caméras A et B ; sous chacune un chronomètre ; les aiguilles tournent **à la même vitesse** mais B a un angle d'avance constant. | « Décalage ! » | entrée en rebond, `surprise` puis `confusion`, `montrer` les chronomètres |
| 2 | `musiciens` | 6–12 s | Deux métronomes (version enrichie : deux musiciens) qui battent à la même période mais à contretemps ; notes qui s'envolent décalées. | « Même vitesse, temps décalés » | `sourire`, `montrer`, `cligner` |
| 3 | `horloges` | 12–19 s | Caméra A et caméra B à gauche, chacune suivie d'une **bande de trames** (rectangles identiques, même cadence) ; la bande B est décalée d'une fraction de trame ; ligne pointillée « Début d'image ». | « Caméra A », « Caméra B » | `concentration`, `montrer` |
| 4 | `reference` | 19–25 s | Régie : un **générateur de référence** unique apparaît, deux branches fines descendent vers l'entrée « GENLOCK » de chaque caméra ; le mot « Genlock » s'affiche. | « Générateur de référence », « Entrée GENLOCK », « Genlock » | `montrer` puis `rebond` |
| 5 | `alignement` | 25–31 s | Des impulsions partent de la référence vers les deux bandes ; la bande B glisse jusqu'à être **exactement alignée** sur A. | « Référence », « Aligné ! » | `concentration` puis `sourire`, `rebond` |
| 6 | `commutation` | 31–38 s | Moniteur « Programme » au-dessus du mélangeur : coupes franches A → B → A, sans saut. | « Programme », « Caméra A / B » | petits `rebond` à chaque coupe |
| 7 | `synchroniseur` | 38–45 s | Deux caméras « non verrouillées » vers un mélangeur ; à l'intérieur s'allume un bloc « Synchroniseur d'image » ; mention « Peut ajouter un léger retard » (aucun chiffre). | « Non verrouillées », « Synchroniseur d'image » | `montrer` le bloc |
| 8 | `pas-obligatoire` | 45–51 s | Carte « Pas obligatoire partout », puis « Genlock = timing » ≠ « Timecode = numéro d'image ». | idem | `montrer`, `surprise`, `cligner` |
| 9 | `pas-image` | 51–57 s | Deux câbles horizontaux : **SDI** épais où circule une vignette d'image ; **référence** fin où ne passent que des impulsions. | « SDI : l'image », « Référence : le timing seulement » | `montrer`, `tenir-cable` |
| 10 | `chute` | 57–63 s | Les chronomètres de la scène 1 : B rattrape A, aiguilles superposées. | « Même fréquence ≠ même instant » | `rebond`, `saluer`, `cligner` |

## 7. Fiche lieu

**Plateau et régie de démonstration Voltix** — même lieu fictif que l'épisode Tally (#140), à
réutiliser tel quel s'il est validé :

- **Plateau** : fond bleu nuit uni, bandeau de projecteurs stylisé ; deux caméras de plateau sur pied
  marquées « A » et « B » ; à l'arrière de chaque caméra, un connecteur rond étiqueté « GENLOCK »
  (BNC, entrée).
- **Régie** : baie technique avec un **générateur de référence** (boîtier 1U, voyant « verrouillé »,
  plusieurs sorties BNC) ; mélangeur vu de trois quarts ; moniteur « Programme ».
- **Schémas** : fond uni sombre, bandes de trames horizontales (rectangles arrondis gris clair),
  impulsions de référence en petits losanges cyan.
- **Personnages secondaires** : aucun (épisode purement technique).
- **Palette** : fond `#14213D`, gris techniques `#3C3F44` / `#8C939B`, trames `#B4BAC1`, référence
  cyan `#26C6DA`, image SDI orange `#FB8C00`, alerte décalage `#E53935`, aligné `#43A047` ; Voltix garde
  sa palette #136.

## 8. Assets à dessiner (SVG éditable)

Identifiants provisoires du storyboard : `ast_1410000000xx` (épisode) et `ast_136000000001` (Voltix).

| Asset | Id provisoire | Type | MVP | Enrichi |
|---|---|---|---|---|
| Voltix | `ast_136000000001` | character | **réutilisé tel quel** depuis #136 | — |
| `decor-plateau` | `…01` | background | aplat bleu nuit (repris de #140 si validé) | projecteurs, reflets |
| `decor-regie` | `…02` | background | baie en rectangles | voyants, câblage arrière |
| `camera-studio` | `…03` | prop | silhouette 3/4, plaque A/B, prise BNC « GENLOCK » à l'arrière | pied, viseur, câble SDI sortant |
| `chronometre` | `…04` | prop | cadran + **aiguille en calque séparé** (pivot au centre, animée par rotation) | graduations, bouton, reflet |
| `metronome` | `…05` | prop | pyramide + **balancier en calque séparé** (pivot en bas) | deux musiciens génériques (batteur, guitariste) en aplats |
| `bande-trames` | `…06` | overlay | 8 rectangles identiques espacés régulièrement (une image = un rectangle) | dégradé de lecture, numéro d'ordre discret (pas de timecode) |
| `generateur-reference` | `…07` | prop | boîtier 1U « Référence », 4 sorties BNC | voyant « verrouillé » qui pulse |
| `cable-reference` | `…08` | overlay | trait fin cyan avec embouts BNC | branche en « Y » / distribution |
| `melangeur` | `…09` | prop | pupitre simplifié + compartiment « Synchroniseur d'image » | rangées de boutons, levier de transition |
| `cable-sdi` | `…0a` | overlay | trait épais orange avec embouts BNC | gaine annelée, étiquette |
| `impulsion-sync` | `…0b` | overlay | petit losange cyan | traînée lumineuse |
| `note-musique` | `…0c` | prop | croche simple | double-croche, rebond |
| `moniteur` | `…0d` | prop | écran 9:16 bordé, vignette de caméra | bordure rouge « Programme », reflet |

Exactitude des schémas : même nombre de rectangles et même espacement sur les deux bandes (même
cadence) ; seul l'**origine** diffère ; l'alignement de la scène 5 est exact (bords confondus) ; le câble
de référence ne montre **jamais** de vignette d'image.

## 9. Prompts visuels de référence (images de travail, facultatif et payant)

Uniquement si l'API d'images est disponible **et** le coût autorisé ; le dessin final reste le SVG ci-dessus.
Style commun : *flat vector illustration, clean shapes, thick dark-brown outlines (#2A1F14), no texture, no
gradients except one flat shadow, vertical 9:16, French labels only, no brand logos, no real people*.

1. `accroche` — two small studio cameras labelled A and B, under each a stopwatch, both hands at
   different angles, yellow round mascot with spring legs popping up, label "Décalage !".
2. `musiciens` — two metronomes side by side swinging at the same speed but out of phase, musical notes
   rising at different moments, label "Même vitesse, temps décalés".
3. `horloges` — technical diagram: camera A and camera B on the left, each followed by a horizontal strip
   of identical rectangles (video frames), strip B shifted by half a rectangle, dotted vertical line.
4. `reference` — control room rack with a single box labelled "Générateur de référence", two thin cyan
   cables branching to the rear BNC input labelled "GENLOCK" of two studio cameras.
5. `alignement` — the same two frame strips now perfectly aligned edge to edge, small cyan pulses coming
   from a "Référence" label, green "Aligné !".
6. `commutation` — video switcher under a "Programme" monitor showing camera A then camera B, clean cut.
7. `synchroniseur` — two cameras labelled "Non verrouillées" feeding a switcher with an inner glowing
   block "Synchroniseur d'image", small note "Peut ajouter un léger retard", no numbers.
8. `pas-obligatoire` — two cards "Genlock = timing" not-equal "Timecode = numéro d'image".
9. `pas-image` — two horizontal cables: thick orange "SDI : l'image" carrying a small picture thumbnail;
   thin cyan "Référence : le timing seulement" carrying only small pulses.
10. `chute` — two stopwatches with perfectly superimposed hands, title "Même fréquence ≠ même instant",
    yellow mascot waving.

## 10. Voix, musique et sous-titres

- **Voix** : celle **liée à la fiche Voltix** (#136) une fois choisie ; ne jamais saisir d'identifiant de
  voix dans cet épisode ni en inventer un. Débit rapide. Avant tout appel ElevenLabs : budget et
  autorisation du propriétaire.
- **Musique** : instrumentale, discrète et dynamique, **droits vérifiés** (licence notée dans la
  production) ; volume de base ~20 % sous la voix, à ajuster au rendu, ducking pendant la parole, coupée
  à la fin de la narration (fondu court) ; aucun bip synthétique du prototype #118. Idée : un léger
  motif rythmique qui « boite » pendant les scènes 1 à 3 et retombe en mesure à la scène 5.
- **Sous-titres** : phrases entières, apostrophes typographiques, « Genlock » avec majuscule,
  « timecode » en minuscules ; les sigles (SDI, BNC) n'apparaissent qu'en légendes de schéma.

## 11. Passage en production (quand #136 et #120 sont validés)

1. Importer le catalogue Voltix #136 et les assets de cet épisode dans le projet (`import_catalog`) ;
   récupérer la table « ancien id → nouvel id ».
2. Remplacer les identifiants provisoires du storyboard (`ast_1360…`, `ast_1410…`) avec `remap_assets`,
   puis figer (`freeze_document`) : les gestes et expressions sont alors vérifiés contre les manifestes.
3. Créer le template de production à partir de `template.json` (champs `name`, `description`,
   `payload`).
4. Estimer les coûts (TTS, éventuelles images de référence), les faire approuver, puis seulement lancer la
   production. Une vidéo n'est « livrée » qu'avec un MP4 vérifiable (voix, musique, sous-titres).
