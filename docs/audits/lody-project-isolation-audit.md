# Audit — contamination des prompts entre projets (LodyCrypto ← Audiovisuel)

*Aucun secret dans ce document : uniquement des textes de prompt, des identifiants de projet/production et des noms de champs.*

## Constat
Des visuels LodyCrypto montrent tables de mixage, régie, éléments Fill/Key.

## Cause exacte
`[app].openai_image_prompt_template` de la configuration du **moteur** est un gabarit Audiovisuel / Fill & Key appliqué à **chaque
image de tous les projets** (`material._openai_image_prompt` : `template.replace("{term}", terme)`), *après* réception du payload de
Lody. Le payload de Lody était propre ; le moteur n'enregistre pas le prompt final, ce qui rendait la contamination invisible.

| Hypothèse | Verdict | Preuve |
|---|---|---|
| A. données persistantes de LodyCrypto | **non** | SQLite : 0 terme broadcast dans le projet et ses 9 productions |
| B. valeur globale partagée | **oui** | gabarit global du moteur (config.toml), commun à tous les projets |
| C. prompt codé en dur dans Lody | non | vocabulaire broadcast uniquement dans le preset du projet Audiovisuel |
| D. copie superficielle d'un objet mutable | latent, non causal | `DEFAULTS` partagés ; durci (copies profondes) |
| E. moteur historique | **oui** | le moteur ajoute le gabarit ; `[ui].video_script_prompt` / `custom_system_prompt` ne servent qu'à l'ancienne WebUI |
| F. payload de la production | non (propre) ; le prompt **final** ne l'est pas | 9 termes propres dans `script.json` |
| G. interprétation spontanée du modèle | **écartée** | le prompt envoyé au modèle contenait « broadcast television… vision mixer… control room » |

Chronologie (UTC) : `config.toml` modifié 12:32:45, moteur redémarré 12:33:13 ; les 3 productions réelles (12:40, 14:29, 15:19) ont
toutes utilisé ce gabarit.

## Prompts réellement envoyés (production `prd_e535418834b3`, scène 4)
- **Envoyé par Lody (`video_terms`)** : « Style visuel : Univers sombre et moderne, accents cyan et bleu électrique, crypto et gaming, sans texte,
  sans logo, sans marque. Illustration cinématographique d’une scène qui accompagne ce passage : « Concrètement, dans certains jeux, un objet
  peut être lié à un NFT… ». Composition verticale, un sujet clair, lumière soignée. Aucun texte, … »
- **Prompt final reçu par le modèle d'images (1 008 car.)** :
  `professional broadcast television visual explaining <prompt de Lody>, realistic studio or control room environment, clear educational
  composition, directly related to Fill and Key video compositing, use broadcast monitors, vision mixer, lower thirds, alpha matte,
  graphic overlay or presenter only when relevant, no unrelated people, no fashion portraits, …, no abstract art, no landscapes, …, photorealistic`
  (le gabarit interdit aussi « abstract art » et « landscapes » : il s'oppose à l'esthétique crypto/gaming).

## Comparatif Audiovisuel / LodyCrypto (avant correction)
| Élément | Audiovisuel (`prj_67cb2f3b51c4`) | LodyCrypto (`prj_5b4fc7e066aa`) | Origine |
|---|---|---|---|
| Brief | pas de bloc `brief` (ancien : durée « 45 – 65 s », voix) | bloc complet (45–60 s, débutants, Web3 gaming, structure en 6 étapes, 7 consignes) | projet |
| Style visuel | « Réaliste broadcast : régie, plateau, écrans — sans texte ni logo » | « Univers sombre et moderne, accents cyan et bleu électrique, crypto et gaming, sans texte, sans logo, sans marque » | projet |
| Sujets autorisés | régie, studio, chaîne de diffusion | crypto, Web3 gaming, pédagogie sans promesse financière | projet |
| Sujets à éviter | aucun défini | aucun défini (**avant correction**) ; 8 éléments propres au projet (**après**) | projet |
| Ton | « Clair et précis » | « Simple, honnête, dynamique, sans posture d'expert » | projet |
| Fournisseur texte | openai (demandé) | openai (demandé) ; le moteur utilisait `moonshot` jusqu'à sa correction | projet / moteur |
| Fournisseur visuel · modèle | openai_image · `gpt-image-2` | openai_image · `gpt-image-2` | projet · moteur (config) |
| Voix · musique | ElevenLabs Kev · bibliothèque | ElevenLabs Kev · ElevenLabs Music | projet |
| `video_terms` | — (aucune production) | 9 prompts Lody, sans vocabulaire broadcast | projet + script (Lody) |
| Prompt système | par défaut du moteur (neutre) | par défaut du moteur (neutre) | moteur (défaut) |
| Prompt visuel global | **gabarit Fill & Key du moteur** | **le même gabarit** | **moteur (config.toml, global)** |
| Identifiant de production | — | `prd_e535418834b3` (dernière), `prd_aa48cb4766f6`, `prd_ea6e63ae142a` | Lody |

## Correction
Voir `docs/lody-generation.md` (« Isolation par projet ») : preflight bloquant tant que le moteur applique un gabarit d'images global ;
instantané immuable et prompts finaux tracés par production ; profil visuel autonome de LodyCrypto avec liste négative propre au projet ;
défauts de plateforme neutres ; outil `scripts/lody-neutralize-image-template.py`.

Les productions existantes ne sont ni régénérées ni modifiées : leurs données restent telles qu'utilisées.
