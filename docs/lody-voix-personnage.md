# Choisir la voix d'un personnage (ticket #153)

Écran **Personnages → Modifier → « Choisir la voix du personnage »**. Il sert à comparer 2 ou 3 voix
ElevenLabs réelles sur le même court dialogue, puis à en garder **une** comme voix par défaut du
personnage (par exemple Voltix). Une fois choisie, cette voix est reprise dans chaque épisode sans qu'il
faille la renseigner à nouveau.

## Pas à pas

1. **Voix à comparer** : la liste propose d'abord la voix déjà enregistrée, puis les voix **françaises**,
   puis les voix **multilingues** du catalogue ElevenLabs du compte (voir
   [lody-elevenlabs-voices.md](lody-elevenlabs-voices.md)). Aucune voix n'est inventée : si le catalogue
   n'a pas été généré côté hôte, l'écran l'indique et ne propose rien. Trois voix au maximum.
2. **Extrait public (gratuit)** : quand ElevenLabs en fournit un, l'extrait du catalogue est lisible
   tout de suite. Il donne une idée du timbre, sur un texte choisi par ElevenLabs.
3. **Modèle** : `eleven_multilingual_v2` (stable, réglages fins) ou `eleven_v3` (plus expressif). Pour
   `eleven_v3`, la stabilité est ramenée au niveau accepté le plus proche (0 / 0,5 / 1).
4. **Variantes de rythme et d'énergie** (mêmes mots pour toutes les voix) :

   | Variante | stabilité | style | vitesse | Usage |
   |---|---|---|---|---|
   | Naturel | 0,5 | 0,2 | 1,0× | référence pour juger le timbre |
   | Vif *(par défaut)* | 0,4 | 0,4 | 1,08× | énergique et articulé, cible Voltix |
   | Très vif | 0,3 | 0,55 | 1,15× | limite haute avant de perdre en clarté |

5. **Dialogue de comparaison** : un court texte SDI/BNC, modifiable (600 caractères au maximum).
6. **Budget** : avant tout appel, l'écran affiche le nombre d'extraits à générer, ceux **déjà en cache**
   (gratuits), le nombre de caractères facturés et le montant estimé, d'après le tarif
   `[voice] elevenlabs` de `pricing.toml` (par tranche de 1 000 caractères). Sans tarif configuré, le
   montant est affiché comme « non configuré », jamais comme gratuit.
7. **Accord** : le bouton « Générer N extrait(s) manquant(s) » reste désactivé tant que la case
   « J'autorise cette dépense ElevenLabs » n'est pas cochée. L'accord vaut pour **ce** plan et pour un
   seul lancement : changer de voix, de variante, de modèle ou de texte le remet à zéro.
8. **Écouter puis valider** : sous chaque extrait, « Utiliser *voix* (*variante*) par défaut »
   enregistre la voix (`voice_provider = elevenlabs`, `voice_name`, `external_voice_id`) **et** ses
   réglages (`voice_direction` : modèle, stabilité, style, vitesse) sur la fiche de **ce** personnage
   uniquement. Rien n'est enregistré sans ce clic.

## Cache audio

Chaque extrait est rangé dans `<données Lody>/voice-samples/` sous une **empreinte** calculée à partir de
la voix, du modèle, des réglages et du texte. Rouvrir l'écran, ou relancer la même comparaison, relit le
fichier sans nouvel appel ElevenLabs. Changer un seul de ces éléments donne une autre empreinte : un audio
périmé n'est jamais réutilisé. Le cache n'est jamais vidé automatiquement.

## Où la voix est reprise

- **Production classique** : `narrative_context.resolve_voice` (#70) prend déjà la voix du personnage de
  référence sélectionné dans la production.
- **Rendu animé 2D** : `animation.audio.voice_plan` (PR #151) lit la même fiche. Si elle porte une voix
  ElevenLabs, c'est celle-là, avec accord de coût ; sinon, une voix provisoire signalée comme telle.
- **Contrat de re-voisement** (`lody.voice_casting`) prévu pour le worker 2D après la fusion de #151 :
  - `character_voice_fingerprint(personnage)` donne l'empreinte de la voix ;
  - `revoice_plan(empreinte_précédente, personnage)` indique ce qu'il faut recalculer quand elle change :
    **voix, visèmes (bouche), sous-titres, mixage**. Le storyboard, les illustrations, le rig et la
    musique ne sont jamais régénérés ;
  - `plan_lines(personnage, répliques, cache)` liste les répliques déjà synthétisées (réutilisées telles
    quelles) et estime le coût des autres avant tout appel.

## Limites connues

- Les réglages enregistrés (`voice_direction`) sont stockés sur la fiche. La production classique
  transmet la voix (`voice_id`) mais pas encore ces réglages fins. Le branchement est prévu côté 2D avec
  #151.
- Le repérage « français / multilingue » repose sur les libellés (`labels`) fournis par ElevenLabs. Une
  voix mal étiquetée reste sélectionnable à la main dans la liste.
- Le montant est une **estimation indicative** fondée sur votre tarif ; il ne remplace pas la facture
  ElevenLabs.
