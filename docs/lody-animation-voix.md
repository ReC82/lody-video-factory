# Voix ElevenLabs du rendu 2D (#164)

Le rendu 2D (fournisseur `animation_2d`, modes Animation 2D / Hybride) fait parler les personnages avec la
**voix ElevenLabs choisie sur leur fiche**. Aucune voix de remplacement (Piper ou autre) n'est utilisée, et
aucune vidéo n'est rendue muette sans prévenir.

## Chaîne

| Étape | Où | Détail |
|---|---|---|
| Voix du personnage | fournisseur (`animation_connector`) | `speech.speaker` → objet asset → `character_id` de l'asset → fiche (`voice_provider = elevenlabs`, `external_voice_id`, `voice_direction`). Même contrat que le casting de voix (#155). |
| Texte oral | `lody.animation.voiceover.render_text` | Lexique de prononciation figé de la production (#162/#163) : la forme parlée part à la voix, la forme écrite reste à l'écran. Sans effet tant que #163 n'est pas fusionné. |
| Synthèse | fournisseur, au lancement (`submit`) | Un appel par scène parlée (texte des repères joints) via l'endpoint moteur `/api/v1/voice_preview` : **la clé ElevenLabs reste dans le moteur** ; le worker de rendu ne l'appelle jamais. |
| Cache | `<données>/voice-samples/` | Empreinte voix + modèle + réglages + texte (formule et dossier du casting #155). Une reprise, un second rendu ou la réédition d'une autre scène ne repaie rien. L'audio est vérifié (décodable, non vide) avant d'entrer dans le cache. |
| Piste et timing | `voiceover.build_track` | Chaque scène est décodée (48 kHz mono), mesurée, ses silences de tête et de queue détectés ; la voix est posée au début du premier repère prévu (avancée si besoin). `voice.wav` a la durée exacte de la vidéo. |
| Repères recalés | `voice-timing.json` | Repères répartis sur la parole réelle selon la longueur du texte parlé : `text` (éditorial) et `spoken` (prononcé). |
| Bouche | worker → moteur Remotion | L'entrée de scène reçoit les repères recalés ; les visèmes suivent `spoken`, l'enveloppe de `voice.wav` ouvre et ferme la bouche. |
| Sous-titres | `subtitles.srt` et incrustation | Texte éditorial, minutage de la parole réelle. |
| Mixage | `animation.audio` (inchangé) | Musique à ≈ 20 % du niveau de la voix, ducking pendant la parole, fondus. |

## Préflight et coût

- **Voix : prête** — toutes les scènes parlées ont une voix ElevenLabs sur la fiche de leur locuteur ;
  le message indique combien de scènes sont déjà en cache et combien de caractères seront synthétisés.
- **Voix : à configurer** (bloquant) — un locuteur est relié à une fiche **sans** voix ElevenLabs : choisir
  la voix dans Personnages (casting #155).
- **Voix : désactivée** — aucun locuteur n'est relié à une fiche (anciens storyboards, prototypes) :
  comportement historique, vidéo muette annoncée.

L'estimation de coût compte les caractères **absents du cache**, au tarif ElevenLabs (`pricing.toml`),
quel que soit le fournisseur de voix du projet ; l'accord de dépense habituel du service s'applique.

## Erreurs

Toute erreur fait échouer le lancement avec un message clair, **sans** autre voix ni vidéo muette :
refus ou quota ElevenLabs (le moteur relaie le message), audio illisible (jamais mis en cache), voix plus
longue que sa scène (« la voix de la scène 2 dure 7,30 s pour une scène de 6,00 s » : allonger la scène ou
raccourcir la réplique ; rien n'est tronqué ni accéléré). La synthèse s'arrête à la première erreur ; les
scènes déjà payées restent en cache pour le nouvel essai.

## Limites connues

- Le minutage des repères à l'intérieur d'une scène est **proportionnel** à la longueur du texte parlé, sur
  la plage de parole réellement détectée. Un alignement mot à mot exact demanderait l'endpoint ElevenLabs
  `with-timestamps`, donc une évolution de l'API du moteur (suite possible).
- Une scène = un locuteur (`speech.speaker`) : les dialogues à plusieurs voix dans une même scène restent à
  découper en scènes.
- Seuls les repères de parole sont dits ; `intent.narration` reste une intention, pas un texte prononcé.
