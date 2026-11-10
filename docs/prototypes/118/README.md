# #118 — Étude du moteur Remotion et prototype MP4 autonome (animation 2D)

> **Statut : prototype à valider visuellement par le propriétaire.** Rien n'est branché sur le
> moteur Python ni sur la production. Recommandation ci-dessous : **GO conditionnel** pour la suite
> (#119/#120). C'est une recommandation technique, pas une validation visuelle.

## Livrables de ce dossier

| Fichier | Contenu |
|---|---|
| [`prototype-118.mp4`](prototype-118.mp4) | Rendu Remotion complet : 15 s, 1080x1920 (9:16), 30 fps = 450 images, H.264 + AAC |
| [`capture-01-salut.png`](capture-01-salut.png) | Image 60 (2,0 s) : salut du bras, bouche ouverte sur « suis », sous-titre karaoké |
| [`capture-02-graphique.png`](capture-02-graphique.png) | Image 214 (7,1 s) : clignement (paupières fermées), bras qui désigne le graphique animé, tête inclinée |
| [`capture-03-au-revoir.png`](capture-03-au-revoir.png) | Image 410 (13,7 s) : salut d'au revoir, ciel passé au bleu, soleil levé, graphique complet |

Le code source est dans [`prototypes/remotion-118/`](../../../prototypes/remotion-118/) (projet Node isolé,
aucune dépendance au moteur Python ni à `webui/lody`).

## Ce qui est réellement animé (aucune image fixe zoomée)

Toute la scène est du **SVG vectoriel recalculé à chaque image** à partir de `useCurrentFrame()` ;
aucune image matricielle n'est utilisée, aucun effet Ken Burns. Personnage fictif « Mimo » (créature
ronde à oreilles) — **ce n'est pas Eli** et ne cherche pas à le représenter.

| Calque | Mouvement | Source (`src/timeline.mjs`) |
|---|---|---|
| Yeux | 8 clignements (dont un double), paupière qui descend/remonte sur 6 images ; regard interpolé (spectateur → main qui salue → graphique → oiseau → spectateur) + micro-saccades | `blink`, `gaze` |
| Bouche | Ouverture pilotée par l'enveloppe RMS **mesurée dans le WAV** (lissée, seuil de fermeture), langue et dents | `mouthOpen` + `src/envelope.json` |
| Bras droit | Salut (épaule levée + coude qui oscille), désigne le graphique, salut d'au revoir | `armRight` |
| Bras gauche, corps, tête | Balancement de repos, respiration, hochement pendant la parole, oreilles qui frémissent, inclinaison vers le graphique | `armLeft`, `breathe`, `headBob` |
| Décor | Ciel qui passe de l'aube au bleu, soleil qui monte et rayons qui tournent, 3 nuages en parallaxe, 2 oiseaux qui battent des ailes, collines qui ondulent, 44 brins d'herbe dans le vent | `sun`, `cloudX`, `wingFlap`, `grassSway` |
| Texte | Titre lettre par lettre avec ressort (`spring`), sous-titre en fondu, sous-titres karaoké synchronisés mot à mot | `titleLetters`, `currentWordIndex` |
| Graphique | Carte qui glisse, 4 barres qui poussent l'une après l'autre, anneau de progression 0 → 100 | `barHeight`, `progress` |

**Audio factice** : babillage voisé synthétisé en Node (`scripts/make-audio.mjs` : fondamentale +
harmoniques filtrées par deux formants, 25 syllabes, fond de « vent »), déterministe (PRNG à
graine fixe). Aucun appel payant, aucune image générée, aucune donnée de production.

## Vérifications (mesurées dans ce worktree)

### ffprobe

```
video  h264  1080x1920  yuv420p  30/1 fps  450 images  start 0.000  durée 15.000 s
audio  aac   48000 Hz   2 canaux            start 0.000  durée 15.019 s
conteneur : 15.019 s, 2 528 838 octets (2,5 Mo), 1,35 Mbit/s
volumedetect : moyenne -27,3 dB, crête -11,4 dB (piste audio non muette)
```

### Preuve du mouvement — `scripts/motion-proof.sh out/prototype-118.mp4`

Différence absolue moyenne (luminance 0-255) entre **images successives**, par zone de calque ;
« paires » = nombre de paires d'images successives où la zone change (YAVG > 0,05).

```
zone           paires    moyenne        max
yeux          449/449       4.244      25.43
bouche        447/449       3.104      16.63
bras_droit    449/449       1.391      11.46
ciel_nuages   449/449       0.602       4.51
herbe         449/449       0.402       0.84
graphique     448/449       0.590       8.80
titre         445/449       2.823      27.40
sous_titres    73/449       1.200      20.87
```

Les sous-titres ne changent qu'aux transitions de mots (comportement attendu) ; toutes les autres
zones bougent sur quasiment chaque paire d'images.

### Synchronisation labiale — `node scripts/lipsync-proof.mjs out/prototype-118.mp4`

Aire sombre de la bouche mesurée **dans la vidéo rendue**, corrélée à l'enveloppe audio :

```
corrélation aire de bouche ↔ enveloppe audio : 0.983
  décalage -2 image(s) : 0.355
  décalage -1 image(s) : 0.737
  décalage +1 image(s) : 0.902
  décalage +2 image(s) : 0.571
aire moyenne (px) pendant la parole : 2735 — en silence : 853
```

Le maximum est à décalage 0 : la bouche est calée sur l'audio à l'image près.

### Tests (`npm test`, Node 22, `node --test`)

- 11 tests de composition temporelle et d'audio : format (450 images), interpolation par
  images-clés, syllabes sans chevauchement, clignements, regard, gestes, décor, graphique/texte,
  bouche ouverte sur chaque syllabe et fermée dans les silences, WAV déterministe.
- Test d'export reproductible (`RENDER_TEST=1 npm test`) : 3 images (45, 200, 410) rendues deux fois
  par Remotion → empreintes SHA-256 identiques octet pour octet. **Passé** localement.
- Reproductibilité de la vidéo entière : voir « Reproductibilité du MP4 » ci-dessous.

## Benchmark (hôte partagé 2 vCPU, cgroup 1,8 Go / 1,5 CPU, `--concurrency=1`)

| Mesure | Valeur |
|---|---|
| Durée totale `remotion render` (empaquetage webpack + 450 images + encodage) | **1 min 43 s** (≈ 6,9× le temps réel) ; premier essai à froid : 2 min 01 s |
| Dont empaquetage webpack | ≈ 20 s (réutilisable si on pré-construit le bundle) |
| Temps CPU | 75 s utilisateur + 2,2 s système (74 % d'un cœur en moyenne) |
| Pic CPU instantané (arbre de processus) | 144 % d'un cœur |
| Pic mémoire, arbre complet (somme des PSS, échantillonnée chaque seconde) | **1,22 Go** pendant l'empaquetage ; **≈ 0,75 Go** stable pendant le rendu |
| Plus gros processus (RSS max, `/usr/bin/time`) | 613 Mo |
| Taille du MP4 | **2,5 Mo** (CRF 26) |
| Disque : `node_modules` + Chrome headless shell | 278 Mo + 243 Mo |
| Installation `npm install` / téléchargement de Chrome | 26 s / 7 s |

Lecture : un rendu de 15 s tient dans le budget de l'hôte sans gêner la production, mais le pic de
l'empaquetage (1,2 Go) est proche de la limite ; en production il faudra pré-construire le bundle
une fois (`@remotion/bundler`) et ne lancer que `renderMedia`. Une vidéo de 60 s coûterait de
l'ordre de 5-6 min de rendu à concurrence 1 sur cet hôte.

## Reproductibilité du MP4

Deux rendus complets successifs (`npm run render`, même machine, même Chrome headless, mêmes
polices) produisent un MP4 **identique octet pour octet** :

```
sha256  6b98c6eaf943b9818ad07abd0ae66bb378f5911a553030a5eeb97c86934707ff  out/prototype-118.mp4
sha256  6b98c6eaf943b9818ad07abd0ae66bb378f5911a553030a5eeb97c86934707ff  out/prototype-118-b.mp4
framemd5 (images décodées) : 662853e78a380dd5c08a0d3dd510d1fd pour les deux rendus
```

Le fichier commité ici est ce rendu. Sur une autre machine (autres polices, autre version de
Chrome ou de l'encodeur), les octets peuvent différer : la reproductibilité garantie est celle de
la composition (images identiques à environnement figé), d'où l'intérêt d'une image Docker figée.

## Contraintes Docker

- Remotion pilote un **Chrome headless shell** (téléchargé par `npx remotion browser ensure`,
  ≈ 100 Mo compressé / 243 Mo sur disque) : l'image du worker doit embarquer les bibliothèques
  système de Chrome (`libnss3`, `libatk-bridge2.0-0`, `libgbm1`, `libxkbcommon0`, `libasound2`,
  polices…) — la doc Remotion fournit un Dockerfile Debian de référence. Ffmpeg est fourni par
  Remotion (binaire embarqué dans `@remotion/compositor-*`).
- Les **polices** sont celles du système (ici DejaVu Sans) : pour un rendu identique entre machines,
  il faut figer les polices dans l'image (ou les charger via `@remotion/google-fonts`/fichiers locaux).
- Recommandation : **conteneur worker séparé** (Node 22 + Chrome), sans accès réseau sortant
  nécessaire au rendu, qui lit un dossier d'entrée et écrit un MP4 ; jamais dans l'image Python du
  moteur ni de l'UI. Limites conseillées : 1 CPU, 1,5 Go, concurrence 1, file d'attente d'un rendu à
  la fois.
- `/dev/shm` : Chrome en conteneur a besoin d'un `/dev/shm` suffisant (`--shm-size=512m`) ou de
  `--disable-dev-shm-usage`.

## Licence

- Remotion n'est **pas** sous licence open source classique : *Remotion License* (vérifiée dans
  `node_modules/remotion/LICENSE.md`, v4.0.542). Gratuite pour un particulier, une entreprise à
  but lucratif de **3 salariés au plus**, une association, ou pour l'**évaluation** ; au-delà, une
  **Company License** payante est obligatoire (remotion.pro). La licence change légèrement en
  Remotion 5.0.
- Interdit : revendre/relicencier un dérivé de Remotion lui-même. Utiliser Remotion pour produire
  des vidéos, y compris commerciales, est permis dans le cadre ci-dessus.
- Compatibilité avec le dépôt (MIT, MoneyPrinterTurbo) : Remotion reste une **dépendance npm** du
  prototype, non redistribuée dans le dépôt (`node_modules` ignoré). **Décision propriétaire à
  prendre** avant tout usage en production : confirmer l'éligibilité à la licence gratuite (taille
  de l'entité exploitante) ou budgéter la Company License.
- Alternatives sans cette contrainte si besoin : Motion Canvas (MIT), ou rendu SVG → images via
  Playwright + ffmpeg maison (plus de travail pour l'audio, les ressorts, le préchargement).

## Interface d'entrée/sortie proposée pour un futur `VideoGenerationProvider`

L'interface Python existante (`webui/lody/generation/provider.py`) reste inchangée ; MoneyPrinterTurbo
demeure le fournisseur par défaut. Un futur `RemotionAnimationProvider` (#120) serait un nouveau
fournisseur à côté de `mpt_connector`, qui délègue le rendu à un **worker isolé** :

```
Entrée (dossier de travail du worker, par rendu)
  props.json      {
                    "schema": "lody.animation/1",
                    "fps": 30, "width": 1080, "height": 1920, "durationInFrames": 450,
                    "audio": "voice.wav",                  // fichier fourni (TTS existant ou factice)
                    "envelope": [0.0, …],                  // RMS par image, calculé côté worker si absent
                    "words": [{"text","start","end"}],    // repères de sous-titres (images)
                    "scenes": [ … ]                        // storyboard animé — contrat de #119
                  }
  voice.wav, assets/…   (catalogue d'assets réutilisables — #119)

Sortie
  out.mp4         H.264 yuv420p + AAC
  manifest.json   {"frames":450,"durationSec":15.0,"sha256":"…","renderMs":…,"peakMemMb":…,
                   "remotionVersion":"4.0.542","stills":[…]}
  progress.json   {"rendered": n, "total": 450}   // pour poll()
```

Correspondance avec le provider : `submit()` crée le dossier et lance le worker (idempotence via
`idempotency_key` = nom du dossier) ; `poll()` lit `progress.json` ; `recover()` lit
`manifest.json` ; `resolve_asset()` renvoie `out.mp4` ; `supports_cancel` possible (arrêt du
worker). Les fonctions pures de `timeline.mjs` (images-clés, clignements, bouche ← enveloppe)
préfigurent la bibliothèque d'animations réutilisables.

## Décision go/no-go (recommandation technique)

**GO conditionnel** pour poursuivre avec #119 (contrat de storyboard + catalogue d'assets) puis
#120 (provider et rendu hybride) :

- ✅ Animation réellement image par image, fluide, déterministe et reproductible ; synchro labiale
  mesurée à l'image près ; tests unitaires simples des fonctions temporelles.
- ✅ Coût de rendu acceptable sur l'hôte actuel (≈ 7× temps réel, < 0,8 Go en rendu, MP4 léger).
- ⚠️ Conditions : (1) décision du propriétaire sur la **licence Remotion** ; (2) worker dans un
  conteneur dédié avec bundle pré-construit (évite le pic de 1,2 Go) ; (3) le style visuel du
  personnage et le rendu final restent **à valider personnellement par le propriétaire** —
  ce prototype ne préjuge pas du design des personnages Lody (ni d'Eli).

## Reproduire localement

```bash
cd prototypes/remotion-118
npm ci                       # 26 s, 278 Mo
npx remotion browser ensure  # Chrome headless shell, 243 Mo
npm test                     # 11 tests + 1 ignoré (RENDER_TEST=1 pour le test d'export)
npm run render               # génère l'audio factice puis out/prototype-118.mp4 (--concurrency=1)
scripts/motion-proof.sh out/prototype-118.mp4
node scripts/lipsync-proof.mjs out/prototype-118.mp4
npx remotion studio src/index.ts   # aperçu interactif (optionnel)
```
