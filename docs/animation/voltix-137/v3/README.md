# Épisode #137 — V3 « du HDMI familier au SDI » : script, storyboard, échantillons voix

> **Statut : V3 à valider par le propriétaire.** Elle suit la directive éditoriale V3 du 11/11/2026 et le cadre
> d'exécution du 13/11/2026 (#137). Elle remplace le script V1 (9 scènes, régie, BNC, 3G/12G) et le MP4 Piper du
> dossier parent, refusés.
>
> **Dépense réelle de cette session : 0 appel ElevenLabs, 0 crédit.** L'orchestrateur n'a accès ni au moteur
> qui détient la clé ElevenLabs ni à la fiche Voltix de production. Les **2 échantillons autorisés** se lancent
> donc en DEV par le propriétaire, avec le kit ci-dessous (§ 3). **Aucune voix Piper n'est utilisée.**
> L'animatique livrée est **muette** : c'est un contrôle visuel gratuit, pas un rendu.

## 1. Fichiers

| Fichier | Contenu |
|---|---|
| [`script-v3.md`](script-v3.md) | script chronométré, scène par scène (généré) |
| [`storyboard-v3.json`](storyboard-v3.json) | storyboard `lody.animation/1` **gelé**, 8 scènes, qui couvrent les 10 plans de la directive (64,7 s estimées à 15 caractères/s) |
| [`echantillons-v3.json`](echantillons-v3.json) | storyboard gelé des **2 échantillons** : scènes 1 et 2, mêmes textes que l'épisode |
| [`episode-v3.json`](episode-v3.json) | repères estimés, coût des échantillons et coût restant de l'épisode |
| [`animatique-v3-muette.mp4`](animatique-v3-muette.mp4) | animatique **muette**, sans voix ni musique, rendue par la vraie chaîne #120 / Remotion |
| `capture-<n>-<scène>.png` | une capture par scène, prise dans l'animatique |
| [`planche-v3.png`](planche-v3.png) / `.svg` | planche de contrôle des 13 nouveaux schémas |
| `resource/animation-assets/sdi-v3-catalogue.json` | catalogue des nouveaux schémas, à importer avec `voltix-catalogue.json` et `sdi-catalogue.json` |
| `scripts/animation/sdi_137_v3_assets.py` | générateur des schémas V3, du catalogue et de la planche |
| `scripts/animation/episode_voltix_137_v3.py` | script, storyboards, estimation, animatique (`--render`) |

## 2. Script V3 (texte dit = texte affiché = sous-titres)

Le moteur envoie à ElevenLabs le texte des sous-titres **tel quel** : le lexique de prononciation #163 n'est pas
fusionné. Le texte est donc écrit pour être dit :

- « soixante-quinze ohms » et « dix caméras » sont écrits en toutes lettres ;
- les sigles restent écrits HDMI, DisplayPort, VGA, SDI : ni « H-D-M-I » ni « esse-dé-i » forcés ;
- **l'échantillon 1 sert précisément à écouter comment la voix lit ces sigles.**

Les sous-titres sont découpés en repères de 60 caractères au plus. Ces repères, rejoints, redonnent la phrase
exacte.

| # | Plans | Texte | Visuel |
|---|---|---|---|
| 1 | 1–2 | « Vous connaissez sûrement le câble HDMI qui relie votre box à votre télé. » « Ou le DisplayPort de votre écran d'ordinateur. » « Et avant, il y avait le VGA, pour l'image analogique. » | Salon : box → câble HDMI → téléviseur, l'image apparaît. Bureau : PC → DisplayPort → moniteur. Fiche VGA bleue, « image analogique ». Fiches HDMI et DisplayPort de face. |
| 2 | 3 | « Mais dans les studios télé, on utilise souvent une autre connexion : le SDI. » | Plateau clair (projecteur, caméra, voyant ON AIR). « SDI » apparaît sur le mot. Ni régie ni matrice. |
| 3 | 4–5 | « Pourquoi ? » « D'abord, parce qu'on peut transporter l'image plus loin, avec un câble adapté. » | Caméra → câble SDI → moniteur, l'image arrive. Puis câble HDMI de maison court et câble SDI qui s'allonge, « … avec un câble adapté ». Aucun nombre de mètres. |
| 4 | 6 | « Et le SDI peut aussi transporter le son avec l'image, sans câble audio séparé. » | Icônes image et son qui rejoignent **le même** câble. « son intégré possible ». |
| 5 | 7 | « Physiquement, il utilise souvent un câble coaxial, comme ceux des antennes de télé. » « Mais attention : tous les câbles coaxiaux ne conviennent pas ! » | Antenne râteau et câble d'antenne blanc (fiche TV) au-dessus du câble SDI noir : « Même famille : le coaxial », puis « Pas n'importe lequel ! » et « pas les mêmes exigences ». |
| 6 | 8 | « Pour le SDI, on utilise une liaison à soixante-quinze ohms, avec du matériel adapté. » | Étiquette 75 Ω, « Liaison 75 Ω de bout en bout », câble et connecteurs, « câble · connecteurs · appareils adaptés ». **Aucune** comparaison d'impédance avec l'antenne. |
| 7 | 9 | « Voilà pourquoi on trouve tant de connexions SDI en vidéo professionnelle ! » | Sur le plateau, la caméra envoie son image au moniteur. Voltix rebondit. |
| 8 | 10 | « Mais maintenant, imaginez dix caméras et vingt écrans… » « Comment envoyer la bonne image au bon endroit ? » « Réponse dans la prochaine vidéo ! » | 10 caméras puis 20 écrans, gros « ? », « Prochain épisode ! ». **Aucune matrice** dessinée ni nommée. |

Chronologie estimée : voir [`script-v3.md`](script-v3.md). La durée totale est de 64,7 s à 15 caractères/s.

### Écarts avec le texte proposé par le propriétaire (pour tenir 55–65 s)

Le texte proposé fait 951 caractères, soit environ 69 s à un débit naturel. La V3 en fait 856, sans changer le
fond :

| Proposé | V3 |
|---|---|
| … votre box à votre **télévision**. | … votre box à votre **télé**. |
| Et avant, **on avait aussi** le VGA… | Et avant, **il y avait** le VGA… |
| Mais dans les studios **de télévision**… | Mais dans les studios **télé**… |
| … transporter l'image **sur de plus grandes distances**, avec un câble adapté. | … transporter l'image **plus loin**, avec un câble adapté. |
| Et le SDI peut **également** transporter le son… | Et le SDI peut **aussi** transporter le son… |
| … comme ceux des **anciennes** antennes de **télévision**. | … comme ceux des antennes de **télé**. |
| Mais attention, tous les câbles coaxiaux ne conviennent pas **forcément** ! | Mais attention : tous les câbles coaxiaux ne conviennent pas ! |
| Voilà pourquoi on **retrouve autant de** connexions SDI **dans la** vidéo professionnelle ! | Voilà pourquoi on **trouve tant de** connexions SDI **en** vidéo professionnelle ! |
| C'est ce qu'on découvrira dans la prochaine vidéo ! | Réponse dans la prochaine vidéo ! |

Pour revenir au texte exact, il suffit de modifier `LINES` dans `episode_voltix_137_v3.py`. La durée dépasse
alors 65 s, sauf si la voix mesurée est rapide.

### Exactitude (directive V3)

- Pas de régie, matrice, routage, 3G/12G, débits ni vidéo sur IP. Le teaser ne fait que poser la question.
- 75 Ω est présenté comme une caractéristique **de la liaison** (câble, connecteurs, appareils). Le texte ne dit
  **pas** que l'impédance distingue le SDI du câble d'antenne : les coaxiaux d'antenne sont souvent en 75 Ω eux
  aussi. Le message porte sur les exigences : « pas n'importe lequel ».
- Les distances restent qualitatives (« plus loin, avec un câble adapté ») : aucun nombre de mètres universel.
- Le son est « possible » (audio intégré), jamais présenté comme obligatoire.
- Le VGA est explicitement analogique ; HDMI et DisplayPort ne sont pas qualifiés.
- Connecteurs dessinés de face :
  - HDMI type A : trapèze, deux coins chanfreinés ;
  - DisplayPort : un seul coin chanfreiné ;
  - VGA : D-sub 15, 3 rangées de 5 broches, 2 vis ;
  - antenne : fiche TV « IEC ».

  La géométrie est vérifiée par les tests.
- Sources de la directive : SMPTE ST 2110, réservée au futur épisode IP (<https://www.smpte.org/standards/st2110>) ;
  coaxial Belden 75 Ω 1506A et famille 5901 (liens dans le ticket).

## 3. Les 2 échantillons ElevenLabs (cadre du 13/11) — à lancer par le propriétaire en DEV

| | Échantillon 1 — accroche « du connu » | Échantillon 2 — arrivée au SDI |
|---|---|---|
| Scène | 1 « maison » | 2 « studio » |
| Ce qu'on écoute | HDMI, DisplayPort, VGA : rythme et naturel des sigles, ton complice | « le SDI » en fin de phrase, ton curieux |
| Caractères | 173 | 76 |

- **Total : 2 appels, 249 caractères.**
- **Pire cas : 747 caractères.** Le moteur relance jusqu'à 3 fois un appel en échec (#187).
- **Plafond proposé : 750 caractères.**

**Avant l'appel**, identifier :

| Point | Valeur |
|---|---|
| Fournisseur | ElevenLabs, via l'endpoint `voice_preview` du moteur (la clé reste dans le moteur, #164) |
| Voix | celle enregistrée sur la fiche Voltix (« Eli », #153). Recopier son `voice_id` dans la fiche DEV, sans appel |
| Modèle et réglages | ceux de la fiche (`voice_direction`) ; à défaut, le modèle par défaut du moteur |
| Coût unitaire | **inconnu de l'orchestrateur.** Ordre de grandeur : 1 crédit par caractère avec `eleven_multilingual_v2`, soit ≈ 249 crédits. Le tarif dépend du modèle et de l'abonnement |
| Crédits restants | à relever sur le tableau de bord ElevenLabs avant et après l'appel ; seul ce relevé fait foi |

**Procédure** : celle de la recette #187, avec la surcharge réseau temporaire, non versionnée, qui relie le
pilote DEV au moteur ([`docs/lody-recette-dev-2d.md`](../../../lody-recette-dev-2d.md) § 4).

1. Construire l'image DEV depuis la branche de cette PR (ou depuis `main` après fusion) : les nouveaux schémas
   doivent être présents dans l'image.
2. Ouvrir le projet DEV (mode Animation 2D) et créer la fiche **Voltix** avec la voix ElevenLabs « Eli ».
3. Dans Atelier 2D, utiliser « Importer le catalogue » pour importer, dans cet ordre :
   - `resource/animation-assets/voltix-catalogue.json` ;
   - `resource/animation-assets/sdi-catalogue.json` ;
   - `resource/animation-assets/sdi-v3-catalogue.json`.
4. Importer `echantillons-v3.json` comme storyboard.
5. Cliquer sur « Préparer la génération ». La ligne **Voix** doit indiquer « 2 scène(s) parlée(s) : 0 déjà en
   cache, 249 caractère(s) à synthétiser ». Ensuite, donner l'accord et générer. On obtient un MP4 court,
   d'environ 25 s : voix, bouche, sous-titres et schémas des scènes 1 et 2.
6. Écouter et regarder. Relever :
   - la prononciation des sigles ;
   - le débit en caractères par seconde : durée de parole dans `voice-timing.json` du dossier de rendu, divisée
     par 173 et par 76 ;
   - la synchronisation de la bouche et des sous-titres ;
   - le solde de crédits.

Si un sigle sonne mal, **ne pas relancer en boucle.** Le correctif est le lexique de prononciation #163 (PR
ouverte) : la forme dite y est séparée de la forme affichée. Un nouvel essai coûte alors de nouveaux caractères
et demande un nouvel accord.

## 4. Après l'écoute : rendu complet (sur accord du propriétaire)

1. Régénérer l'épisode au débit mesuré, sans appel :
   ```bash
   uv run --no-sync python scripts/animation/episode_voltix_137_v3.py --out docs/animation/voltix-137/v3 --cps <débit mesuré>
   ```
   Les scènes sont recalées sur la voix réelle. Si la durée sort de 55–65 s, le script s'arrête : raccourcir le
   texte. Les fins de scène sont recalées, les textes ne changent pas.
2. Importer `storyboard-v3.json` dans le même projet DEV. Les scènes 1 et 2 ont **exactement** les textes des
   échantillons : le cache de voix (#164 : empreinte voix + réglages + texte) les reprend **sans nouvel appel**.
3. Coût prévisionnel du rendu complet :
   - **6 appels, 607 caractères** (≈ 607 crédits avec `eleven_multilingual_v2`) ;
   - pire cas avec les relances du moteur : 1 821 caractères.

   Total de l'épisode, échantillons compris : 856 caractères.
4. Musique : le niveau était jugé correct, mais la piste générée n'a pas convaincu (#160). Il faut choisir une
   piste instrumentale libre avant le rendu final. Cette V3 ne tranche pas.

## 5. Ce qui reste à valider par le propriétaire

1. Le texte V3 et ses écarts (§ 2), et le découpage en 8 scènes / 10 plans (captures et animatique).
2. La planche des nouveaux schémas : HDMI, DisplayPort, VGA, antenne, plateau.
3. Le lancement des 2 échantillons (§ 3), puis l'accord sur le rendu complet (≈ 607 caractères).

## 6. Limites connues de l'animatique

- Muette par construction : la bouche de Voltix ne suit aucune voix. Les repères et les sous-titres sont
  **estimés** à 15 caractères/s.
- Teaser (scène 8) : la fin de « La bonne image au bon endroit ? » passe derrière Voltix. À corriger lors du
  recalage au débit mesuré (§ 4).
- Le câble HDMI est posé en diagonale entre les prises de la box et du téléviseur. Il n'est pas animé
  « en branchement ».
