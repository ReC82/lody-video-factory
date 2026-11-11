# Garde-fou éditorial avant génération (#167)

> **En une phrase :** avant de fabriquer une vidéo (voix, images, rendu), on fait relire l'épisode par un
> petit contrôle automatique qui dit, scène par scène, ce qui risque de perdre un débutant, ce qui ne se voit
> pas à l'écran, et ce que la voix risque de mal prononcer.

Le contrôle **ne coûte rien** (il ne contacte aucun service payant, ni Internet), **ne corrige rien tout seul**
et ne remplace pas une relecture humaine ni l'écoute de la voix. Il pose des questions ; c'est vous qui
décidez.

## Pourquoi

Le premier épisode SDI (#137, puis sa V2 #160) a montré trois dérives :

- **trop de mots nouveaux d'un coup** : SDI, broadcast, coaxial, BNC, 75 ohms, 3G-SDI, 12G-SDI, grille de
  routage… Un débutant décroche. La règle retenue : **une seule notion nouvelle par épisode**, on avance
  pas à pas, du HDMI de la maison vers le matériel des pros ;
- **la phrase ne correspond pas à l'image** : la voix dit « Tu vois ce câble ? » alors qu'aucun câble n'est à
  l'écran ;
- **la voix lit mal les sigles** : « SDI » épelé à la française, pauses bizarres entre « 3G » et « SDI ».

## Ce que l'on décrit pour un épisode

Un fichier « de préproduction » (format `lody.preprod/1`) en deux parties.

**1. Le brief : l'intention**

| Champ | Ce qu'on y met | Exemple |
|---|---|---|
| `objective` | ce que le spectateur doit retenir, en une phrase | « Le SDI, c'est la prise vidéo des pros, comme le HDMI à la maison. » |
| `audience` | `novice` (débutant, par défaut) ou `initie` | `novice` |
| `assumed_knowledge` | les mots que le spectateur connaît déjà | télé, box, câble, HDMI |
| `new_terms` | **le** mot nouveau, avec son explication simple (et ses autres écritures dans `aliases`) | SDI : « la prise et le câble vidéo des pros » |
| `examples` | les exemples concrets utilisés | « la box reliée à la télé » |
| `cta` | la dernière phrase, qui donne envie de voir l'épisode suivant | « Et toi, sais-tu comment le présentateur devine quelle caméra le filme ? » |
| `next_episode` | note libre sur l'épisode suivant | « Le voyant rouge des caméras » |
| `pronunciations` | comment la voix doit dire chaque sigle (écrit → dit) | `"SDI": "èss di aï"` |
| `words_per_second` | vitesse de la voix (2,6 par défaut ; mettre la valeur mesurée) | 2,6 |
| `scene_seconds` | durée visée d'une scène | `[5, 7]` (par défaut) |
| `target_seconds` | durée totale visée de la parole | `[25, 45]` |
| `extra_jargon` | mots techniques propres au projet à surveiller en plus | `["multiplexeur"]` |

**2. Les scènes : quatre textes à ne jamais mélanger**

| Champ | C'est quoi |
|---|---|
| `spoken` | le texte **envoyé à la voix** (avec, si besoin, l'écriture « phonétique » des sigles) |
| `captions` | les **sous-titres**, écrits normalement (« SDI », pas « èss di aï ») |
| `displayed` | les textes **posés à l'écran** : titres, étiquettes |
| `visual` | ce que l'on **voit**, décrit concrètement : quel objet, dans quel état, ce qui bouge |
| `duration_s` | durée prévue de la scène (facultatif) |
| `audio_s` | durée réelle de la voix si elle a déjà été mesurée (facultatif, remplace l'estimation) |

Un exemple complet qui passe sans alerte :
[`editorial/hdmi-vers-sdi-novice.preprod.json`](editorial/hdmi-vers-sdi-novice.preprod.json).

## Lancer le contrôle

```bash
uv run python scripts/editorial/verifier_preproduction.py docs/editorial/hdmi-vers-sdi-novice.preprod.json
```

- Résultat lisible : un **verdict**, un **tableau scène par scène** (durée, voix, ce qu'on entend, ce qu'on voit,
  ce qui est écrit à l'écran, alertes), puis la **liste des alertes** avec la scène et le champ exacts à corriger.
- `--sortie rapport.md` pour l'enregistrer ; `--json` pour un résultat exploitable par un programme.
- Un storyboard animé existant (format `lody.animation/1`) se contrôle aussi, avec son brief à part :
  `… storyboard.json --brief brief.json`. Les alertes renvoient alors aux champs du storyboard
  (`scenes[2].intent.narration`, `scenes[2].render.speech.cues[1]`…). Ce que l'on « voit » est alors déduit du
  but de la scène, de ses notes et des noms des objets affichés : plus ils sont parlants, meilleur est le contrôle.
- Code de retour : `0` prêt ou à relire, `1` à corriger, `2` fichier illisible.

## Les verdicts

| Verdict | Signification |
|---|---|
| ✅ **prêt** | aucune alerte à traiter |
| ⚠️ **à relire** | des points à vérifier : corriger, ou garder en connaissance de cause |
| ⛔ **à corriger** | au moins un problème bloquant : ne pas lancer de génération |

## Les alertes, en clair

**Niveau débutant**

| Alerte | Ce qu'elle veut dire |
|---|---|
| `objectif-absent` ⛔ | on ne sait pas ce que l'épisode doit apprendre |
| `plusieurs-notions` | plus d'une notion nouvelle : garder les autres pour les épisodes suivants |
| `hors-niveau` | un mot technique apparaît sans être prévu par le brief (ex. « 3G-SDI », « grille de routage ») |
| `premiere-occurrence` | le mot nouveau est dit la première fois sans être expliqué dans la phrase ou la suivante |
| `affiche-avant-dit` | le mot nouveau est écrit à l'écran avant d'être dit et expliqué |
| `notion-jamais-dite` | le mot nouveau du brief n'est jamais prononcé |
| `exemple-absent`, `connaissances-absentes`, `notion-absente`, `explication-absente` | le brief est incomplet |
| `cta-absent`, `cta-non-enseigne`, `cta-hors-scene` | dernière phrase manquante, qui nomme une technologie pas encore expliquée, ou absente de la dernière scène |

**Phrase ↔ image**

| Alerte | Ce qu'elle veut dire |
|---|---|
| `visuel-absent` ⛔ | la scène ne dit pas ce que l'on voit |
| `visuel-vague` | description trop courte pour être dessinée |
| `montre-sans-visuel` | la voix montre quelque chose (« tu vois ce câble », « regarde cette fiche ») qui n'est pas dans l'image |
| `voix-deborde` ⛔ | la voix est plus longue que la scène |
| `scene-courte`, `scene-longue` | scène hors de 5 à 7 secondes (une idée visuelle nouvelle toutes les 5 à 7 s) |
| `ecran-sans-voix` | plus de 3 s d'image sans parole : la durée doit suivre la voix, pas un nombre d'écrans |
| `duree-totale` | la parole totale sort de la durée visée |

**Voix et sous-titres**

| Alerte | Ce qu'elle veut dire |
|---|---|
| `consigne-dans-voix` ⛔ | le texte parlé contient une consigne de fabrication (« Scène 2 », « 9:16 », « 45 à 60 secondes »…) |
| `phonetique-affichee` ⛔ | l'écriture phonétique (« èss di aï ») s'affiche à l'écran : l'écran doit montrer « SDI » |
| `tts-sigle` | un sigle est envoyé à la voix sans prononciation déclarée |
| `prononciation-declaree` (info) | une prononciation est prévue : **l'écouter** avant le rendu (voir le lexique #162) |
| `tts-symbole`, `tts-unite` | symbole (Ω, %, /…) ou unité abrégée (Gbit/s) dans le texte parlé |
| `tts-pause` | virgule entre « HD/3G/12G » et « SDI » : la voix ferait une pause artificielle |
| `voix-sous-titres` | la voix et les sous-titres ne disent pas la même chose |
| `sous-titre-long` | sous-titre de plus de deux lignes |

## Les limites, honnêtement

- Ce sont des **règles simples**, pas une compréhension du texte. Exemples : un deux-points suffit à considérer
  qu'un mot est « expliqué » ; « ce câble » n'est vérifié que si la phrase montre quelque chose (« vois »,
  « regarde », « voici », « ici »…). Une alerte peut être fausse : on la lit, on décide.
- La liste des mots techniques (`JARGON` dans `webui/lody/editorial/checks.py`) est **à enrichir** ; un projet
  ajoute les siens par `extra_jargon`. Les sigles (au moins deux majuscules, ou chiffre + lettre comme 4K) sont
  repérés automatiquement.
- La durée de parole est **estimée** à partir du nombre de mots, tant qu'aucune voix n'a été mesurée
  (`audio_s`).
- **Rien n'est corrigé automatiquement** : un mot technique ne se remplace pas sans vérifier que le sens reste
  juste. Les « pistes » sont des propositions.
- Le contrôle n'est pas encore branché dans l'interface ni dans le lancement d'une production : il se lance à la
  main (voir « Suites possibles »).

## Tests de non-régression

`test/lody/test_editorial_guard.py` rejoue le contrôle sur :

| Fichier | Ce que le contrôle doit trouver |
|---|---|
| [`regression-sdi-v1`](editorial/regression-sdi-v1.preprod.json) (#137) | « Tu vois ce câble ? » sans câble à l'écran, 3G-SDI / 12G-SDI / 4K, pauses entre « 3G » et « SDI », scènes trop longues |
| [`regression-sdi-v2`](editorial/regression-sdi-v2.preprod.json) (#160) | broadcast, grille de routage, coaxial, BNC, impédance caractéristique, 3G-SDI… : trop de mots pour un débutant |
| [`regression-tally`](editorial/regression-tally.preprod.json) (#140) | « Tally » dit avant d'être expliqué, « prévisualisation », pas de phrase finale vers l'épisode suivant |
| [`regression-genlock`](editorial/regression-genlock.preprod.json) (#141) | « cadence », « broadcast », « signal de référence », « resynchroniser », « mélangeur » |
| [`regression-melangeur`](editorial/regression-melangeur.preprod.json) (#142) | « régie », « prévisualisation », une scène où la voix est trop longue |
| [`hdmi-vers-sdi-novice`](editorial/hdmi-vers-sdi-novice.preprod.json) | rien : c'est le modèle d'un épisode débutant, du HDMI vers le SDI |

Les scripts Tally, Genlock et mélangeur sont ceux des tickets ; les scripts SDI sont ceux des PR #158 et #161,
avec les durées mesurées dans leurs rendus. Ces fichiers servent d'exemples : ils ne modifient aucun épisode.

## Suites possibles (hors de ce ticket)

- Afficher ce tableau dans l'interface de réédition par scène (#159) et le lancer avant toute génération
  animée.
- Brancher les prononciations sur le lexique par projet de #162 une fois fusionné.
