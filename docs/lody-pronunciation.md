# Prononciation des sigles et règle éditoriale (#162)

Les voix de synthèse épellent souvent les sigles lettre par lettre, de façon heurtée (« S-D-I », « H-D-M-I »).
Lody permet de dire **comment la voix doit lire** une expression sans jamais changer **ce qui s'affiche** :
les sous-titres gardent `SDI`, `HDMI`, `3G-SDI` exactement.

## Où ?

Projet → **Paramètres** → section **Prononciation** (sous le formulaire).

## Le lexique

Chaque expression du projet porte :

| Champ | Rôle |
| --- | --- |
| Forme écrite | Le sigle tel qu'il apparaît dans le script (`SDI`, `3G-SDI`). Sensible à la casse. |
| Lecture demandée | La graphie parlée envoyée à la voix (ex. une épellation liée par traits d'union). |
| Forme dans les sous-titres | Vide = la forme écrite, exacte (cas normal). |
| Transcription phonétique | Conservée pour mémoire, **jamais envoyée** : les modèles ElevenLabs utilisés (`eleven_multilingual_v2`, `eleven_v3`) n'ont pas de support confirmé des balises de phonèmes. |
| Langue | Vide = toutes ; `fr` couvre `fr-FR` ; `fr-FR` seulement `fr-FR`. |
| Personnage | Facultatif. L'entrée d'un personnage l'emporte sur l'entrée générale. |

Lody ne fige **aucune** prononciation par défaut. Les « pistes à écouter » affichées sous le champ sont des
suggestions, jamais appliquées d'office.

## Écouter, puis valider

1. Choisir la **voix de l'écoute** : la voix du projet ou celle d'un personnage, avec le modèle et les réglages
   réellement utilisés en production (direction vocale du personnage comprise).
2. **Écouter** la lecture actuelle (sans lexique) et la lecture demandée, dans une phrase courte. Chaque écoute
   est un appel ElevenLabs payant, déclenché uniquement par le bouton — jamais une vidéo.
3. Juger le **rythme, les liaisons et les pauses**, pas seulement l'absence d'erreur : une épellation claire mais
   robotique (lettres disjointes) est un échec. Essayer plusieurs graphies si besoin.
4. **Valider cette prononciation après écoute** (bouton actif seulement une fois la lecture demandée écoutée).
   La voix entendue est enregistrée avec la validation.

Toute modification de la forme écrite, de la lecture, de la langue ou du personnage **annule la validation** :
il faut réécouter.

## Dans les productions

- À la préparation, les entrées applicables (langue du projet, personnage de référence) sont **figées** dans
  l'instantané de la production : une modification ultérieure du lexique ne change pas une production déjà
  préparée. Une V2 reprend le lexique **actuel** du projet.
- **Aucune vidéo ne part** tant qu'une expression du script a une entrée **non validée** (refus à la confirmation,
  et de nouveau sur le script final s'il est écrit par le moteur).
- Le moteur reçoit `video_script` (sous-titres, forme exacte) et, seulement si le lexique change quelque chose,
  `video_script_spoken` (texte lu par la voix). Les sous-titres sont alors calés sur la durée réelle de l'audio,
  phrase par phrase, comme pour toute voix ElevenLabs. Un moteur antérieur à #162 ignore ce champ.

## Règle éditoriale pour les futurs scénarios

- **Un concept par Short.** Un épisode explique une seule idée.
- **Aucun jargon inutile.** Chaque terme nécessaire est expliqué **dans la même phrase** ou par une illustration.
- **Pas de surenchère d'acronymes** : au-delà de **3 sigles différents** dans un script, le contrôle éditorial
  avertit. Préférer « le câble de la caméra » à une suite de normes et de débits.
- Avant génération, coller le script dans **Contrôle éditorial d'un texte** (gratuit, sans appel réseau) : il
  liste les sigles sans prononciation enregistrée, ceux encore à écouter (bloquants) et montre côte à côte le
  texte envoyé à la voix et celui des sous-titres.

## Limites connues

- Le rendu 2D (Remotion) reçoit une voix déjà produite : le lexique s'applique au pipeline de génération
  classique et à l'aperçu ; la voix du rendu 2D devra appeler `lody.pronunciation.render` au moment où elle sera
  synthétisée par Lody.
- Les tests automatiques vérifient la mécanique (substitution, portée, blocage), **jamais** la qualité d'une
  lecture : seule l'écoute humaine valide une prononciation.
