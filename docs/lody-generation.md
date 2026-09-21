# Génération vidéo dans Lody Video Factory

Parcours : **sujet → estimation → une confirmation → suivi → vidéo → V2 éventuelle.**
Le moteur utilisé aujourd'hui est un *connecteur hérité* (voir `docs/lody-engine-contract.md`) ;
les vues ne parlent qu'à l'interface `VideoGenerationProvider`.

## Architecture

```
webui/lody/generation/
  models.py         modèles typés : demande, tâche externe, progression, résultat, statuts, erreurs
  provider.py       interface VideoGenerationProvider (moteur remplaçable) + quantités estimées
  mpt_connector.py  1re implémentation (API historique) — seul module qui connaît ce contrat
  demo.py           connecteur de démonstration : simule une génération, aucun fournisseur
  costing.py        estimation du coût, tarifs configurables
  storyboard.py     découpe du script en scènes + un prompt visuel explicite par scène
  service.py        orchestration : préparer, confirmer (atomique), exécuter, suivre, reprendre, V2
  store.py          dépôt SQLite des productions
  safety.py         nettoyage des secrets, classement des erreurs, validation des chemins
  runtime.py        assemblage (connecteurs + service) pour l'application
```

Ajouter un autre moteur = écrire une classe `VideoGenerationProvider` et l'enregistrer dans
`runtime.build_service()`.

### Déroulé après la confirmation (automatique, sans autre validation)

1. **Script** : écrit par le moteur (un appel texte), ou repris tel quel s'il est fourni / en V2.
2. **Storyboard** : le script est découpé en scènes (nombre selon la durée réelle et le rythme
   visuel du projet) ; chaque scène reçoit un prompt visuel explicite (style du projet + passage
   illustré + « aucun texte, aucun logo »). *Les prompts sont dérivés du script, pas rédigés par un
   modèle* ; ils sont conservés et visibles. Le script et le storyboard sont enregistrés **avant**
   tout appel d'images.
3. **Envoi** d'une seule tâche vidéo au moteur (script + prompts, ordre des scènes respecté).
4. **Suivi** : l'interface interroge le moteur (rafraîchissement automatique toutes les 4 s tant
   que la production est active).

## Statuts internes

`BROUILLON` → `EN_ATTENTE_CONFIRMATION` → `CONFIRMEE` → `EN_COURS` (script, scènes) →
`EN_FILE` (envoyée, le moteur n'a pas démarré) → `EN_COURS` → `TERMINEE` | `ECHEC`.
`ANNULEE` existe dans le schéma mais **n'est jamais utilisée** : le moteur n'a pas d'annulation.

La progression affichée est celle du moteur (jalons 5, 10, 20, 30, 40, 50…100). Aucun pourcentage
n'est inventé : sans valeur du moteur, seule l'étape est montrée.

### Sécurité des lancements payants

- La confirmation est une transition SQL atomique : double clic, deux onglets ou deux sessions
  ne lancent qu'**une** génération.
- Une seule génération active par projet.
- Un lancement n'est **jamais rejoué automatiquement**. Si Lody redémarre avant l'envoi au moteur,
  la production passe en `ECHEC` (« interrompue ») ; si elle était déjà envoyée, Lody redemande son
  état au moteur au démarrage, sans rien relancer.
- Moteur injoignable pendant le suivi : la production reste telle quelle (avertissement), elle
  n'est pas déclarée perdue. Si le moteur ne connaît plus la tâche (il garde ses tâches en
  mémoire), Lody cherche la vidéo sur disque avant de conclure à un échec.

## Coût

Les tarifs ne sont **pas** dans le code. Fichier `pricing.toml`, cherché dans cet ordre :
`$LODY_PRICING_PATH`, sinon `/data/pricing.toml` (volume persistant, hors image). Format :

```toml
currency = "EUR"
[text]   openai = <montant>          # par script écrit
[visual] openai_image = <montant>    # par image
[voice]  elevenlabs = <montant>      # par tranche de 1 000 caractères
[music]  elevenlabs = <montant>      # par morceau
```

Un tarif absent s'affiche « tarif non configuré » : le total est alors **partiel** et la
confirmation exige de cocher un avertissement explicite. Les montants sont indicatifs ; le coût
réel n'est pas mesurable (le moteur ne le fournit pas). Quantités estimées : images = nombre de
scènes (durée × rythme visuel du projet), voix ≈ 6 caractères par mot, 1 script, 1 morceau.

Créer le fichier sur le serveur :
`sudo docker exec -i lody-video-factory-ui sh -c 'cat > /data/pricing.toml' < pricing.toml`

## Mode démonstration

Interrupteur « Mode démonstration » sur la page de production. Simule tout le parcours (script,
storyboard, suivi ~20 s, vidéo de test générée localement par ffmpeg, V2) **sans aucun appel
fournisseur** ; la production est marquée « Démonstration ».

## Stockage

- Base : `/data/lody.sqlite3` (tables `projects`, `productions` ; migrations `lody/db.py`,
  `user_version = 2`). Aucune clé API n'y est écrite.
- Vidéos et médias du moteur : `./storage/tasks/<tâche>/` (monté en lecture seule dans Lody).
  Seuls les fichiers de la tâche de la production sont lisibles (chemin validé : pas de `..`,
  pas de lien symbolique sortant, extensions vidéo uniquement).
- Vidéos de démonstration : `/data/demo/`.

## Configuration de production (preflight) et résolution de configuration

Avant « Confirmer et générer la vidéo », Lody effectue un **preflight complet, sans aucun coût** et affiche
« Configuration de production » : Texte, Visuels, Voix, Musique, Moteur (Stockage et Paramètres apparaissent
s'ils bloquent). Chaque ligne dit **ce que le projet demande**, **ce qui est réellement configuré** et si c'est
prêt. Si une capacité obligatoire manque : bouton de confirmation désactivé, rien n'est confirmé ni lancé
(`confirm()` refuse aussi côté service), le message nomme le fournisseur et où corriger (paramètres du projet ou
serveur). Les noms de champs restent dans « Diagnostic administrateur » (jamais de valeur de clé).

Le moteur choisit son fournisseur de texte via `llm_provider` (global, non surchargeable par requête). Un projet
« OpenAI » sur un moteur réglé sur un autre fournisseur est donc **bloqué**, jamais basculé en silence.

La couche `engine_facts` (indépendante des vues) fournit les « faits » de configuration : fournisseur de texte
réel, clés *renseignées ou non*, noms de modèles, points d'accès d'images, préférences de sous-titres. Deux sources :

1. `config.toml` lu directement, si le conteneur peut le lire ;
2. sinon le **rapport de capacités** `engine-report/engine-capabilities.json`, généré par le propriétaire de
   `config.toml` (`./scripts/lody-engine-report.sh`, à relancer après chaque changement de `config.toml`). Il ne
   contient que des booléens et des noms de modèles. Il n'est cru que si `config.toml` (taille + date, lues par
   `stat`) n'a pas changé depuis : sinon « rapport périmé » et la production réelle reste bloquée.

Ainsi Lody n'a jamais accès aux clés (le conteneur ne peut pas lire `config.toml`, fichier `0600`), tout en
sachant exactement ce qui est configuré. Sans aucune des deux sources, chaque fournisseur est « non vérifié » et
la production réelle est bloquée (le mode démonstration reste possible).

## Isolation par projet

Incident : des visuels LodyCrypto montraient tables de mixage et régie. Cause : le moteur applique un **gabarit d'images global**
(`[app].openai_image_prompt_template` de sa configuration) à chaque image de chaque projet ; celui-ci décrivait un univers
Audiovisuel / Fill & Key (audit : `docs/audits/lody-project-isolation-audit.md`). Garanties mises en place :

- **Gabarit global bloquant.** Le rapport de capacités porte ce gabarit ; s'il n'est pas neutre (vide, `{term}`, ou sans `{term}`), le
  preflight bloque la production réelle (« le moteur ajoute un gabarit d'images global à tous les projets »). Pour le neutraliser :
  `python3 scripts/lody-neutralize-image-template.py --dry-run`, puis sans `--dry-run` (sauvegarde datée, toute autre valeur vérifiée
  identique), redémarrer l'API du moteur, puis `./scripts/lody-engine-report.sh`.
- **Instantané immuable.** À la préparation, la production enregistre une copie profonde des paramètres du projet (`snapshot` : projet,
  version des paramètres, date, demande, origine de chaque valeur). Il n'est jamais modifié ensuite (seul le brouillon peut être re-préparé),
  y compris si le projet change. La confirmation et le worker refusent une production dont l'instantané n'est pas celui de son projet ou
  dont les paramètres à envoyer diffèrent ; une V2 hérite de l'instantané de sa version précédente. Aucune lecture du « dernier projet ».
- **Prompts finaux tracés** (`trace`, enregistrée *avant* l'envoi) : requête d'écriture du script, prompt exact de chaque scène, ce que le
  moteur y ajoute, paramètres transmis, origine de chaque élément (projet, moteur, plateforme). Visible dans « Diagnostic administrateur »
  de la page de la production ; tout est passé par un masquage de secrets.
- **Profil visuel propre au projet** (`brief.visual_rules`, `brief.visual_avoid`), ajouté à chaque prompt de scène de CE projet uniquement.
  Les défauts de plateforme sont neutres (vides). LodyCrypto porte sa propre liste négative (régie TV, table de mixage, pupitre broadcast,
  multiview, SDI, schéma FILL/KEY, caméra de plateau/PTZ, studio TV sauf demande explicite) ; migration additive au démarrage pour le
  projet existant (ne remplit que les clés absentes, n'écrase jamais une saisie).

## Sous-titres français : apostrophes et police

Incident V7 : « mais l’  idée de base est simple » à l'écran, alors que le script, le texte envoyé au moteur, le
texte du TTS et le SRT du moteur sont tous corrects (`l’idée`, U+2019, aucune espace). **Cause :** l'incrustation
(MoviePy/PIL) utilisait `MicrosoftYaHeiBold.ttc`, police CJK reprise de `[ui].font_name` : son `’` est en **pleine
largeur** (58 px à corps 58, 3,4 espaces). Les polices `STHeiti*` et `MicrosoftYaHei*` ont le même défaut ; l'apostrophe
droite `'` est normale.

- **Correction à la source** (`typography.pick_subtitle_font`, appelé par le connecteur) : pour une langue latine, une
  police dont l'apostrophe typographique est pleine largeur (mesurée avec Pillow) est remplacée par
  `BeVietnamPro-Bold.ttf` (fournie, couvre le français) ; les langues chinoise, japonaise et coréenne gardent leur
  police. La police réellement envoyée est visible dans les paramètres effectifs.
- **Protection supplémentaire** (`typography.normalize_french_text`) : retire les espaces réellement présentes après
  une élision (`l’ idée` → `l’idée`, `qu’ il`, `aujourd’ hui`…), sans toucher aux URL, chemins, courriels ni code,
  aux autres langues, ni au type d'apostrophe. Appliquée au script (généré, fourni ou modifié en V2) pour que
  narration, SRT et affichage ne divergent jamais, ainsi qu'aux SRT/ASS de la réparation.

## Réparer le rendu (sans rien régénérer)

**Limite du moteur :** son API ne sait pas reprendre seulement le montage (`POST /api/v1/videos` relance tout, donc
voix, images et musique payantes). Sa fonction interne `generate_video` ne travaille toutefois que sur des fichiers
locaux ; `scripts/lody-repair-render.sh` l'appelle sur les assets existants d'une production terminée :

```
DOCKER="sudo -n docker" ./scripts/lody-repair-render.sh prd_xxxxxxxxxxxx
```

Réutilise `combined-1.mp4`, `audio.mp3`, `subtitle.srt` (normalisé) et la musique déjà générée ; **réseau bloqué**
pendant le montage ; le rendu original n'est jamais modifié (copie vérifiée par empreinte dans `repair/`) ; le rendu
corrigé (`repair/final-1-repaired.mp4`, avec `repair/repair.json`) est ajouté à la même production comme
« réparation technique » — aucune nouvelle production, aucun coût. La page de la production affiche le rendu corrigé
en premier et conserve l'original.

## Kit de publication manuelle

Chaque production **terminée** a un kit (bloc « Kit de publication » sous le résultat). Rien n'est publié et aucun compte
YouTube/TikTok n'est connecté : le kit prépare ce qu'on copie-colle et téléverse soi-même.

**Architecture.** `generation/kit_service.py` (orchestration) s'appuie sur `publication.py` (métadonnées, locales et
déterministes, règles anti-conseil financier), `thumbnail.py` (composition Pillow), `subtitles.py` (SRT/VTT),
`kit_store.py` (SQLite), `kit_files.py` (fonds générés) et `media.py` (durée réelle via ffprobe). L'interface est
`view_kit.py` ; le profil de publication (playlist, hashtags, mention, public…) appartient à **chaque projet**
(Paramètres → Contenu → Publication) : aucune donnée d'un projet n'entre dans le kit d'un autre.

**Migration SQLite v4** (`publication_kits`, une ligne par production, `UNIQUE(production_id)`) : `metadata`,
`initial_metadata` (pour « Restaurer »), `thumbnail` (texte, variante, fond), `history` (40 entrées max, avec l'ancienne
valeur), `background_job` (état du fond payant). Additive ; sauvegarde de la base avant migration au déploiement. Une V2 a
son propre kit ; celui de la V1 n'est jamais modifié.

**Miniature.** Le fond ne contient jamais de texte : le texte est dessiné localement (police fournie, contraste ≥ 4,5:1,
corps ≥ 20 px dans l'aperçu téléphone de 360 px). Trois compositions (centré, en haut, latéral) sortent du **même fond**,
sans appel fournisseur ; PNG et JPEG téléchargeables. Fond gratuit : image de scène déjà produite. Fond payant dédié :
uniquement si le moteur sait générer une image seule (`supports_thumbnail_background`) ; il est alors compris dans
l'estimation initiale, et un nouveau fond ne part qu'après estimation puis confirmation explicite (idempotente).

**Sous-titres.** SRT (UTF-8) et VTT (`lang fr`) reconstruits depuis le SRT du moteur déjà produit : apostrophes
françaises corrigées, minutages bornés à la durée réelle, rien après la dernière image. Les sous-titres restent incrustés
dans le MP4.

**Archive ZIP** : `video.mp4`, `thumbnail.png`, `subtitles-fr.srt`, `subtitles-fr.vtt`, `publication-youtube.txt`,
`publication-tiktok.txt`, `metadata.json`. Chaque texte passe un contrôle (secrets, chemins serveur, identifiants internes)
avant d'entrer dans l'archive.

**Coûts.** Gratuit : création du kit, propositions, modifications, recomposition de la miniature, SRT/VTT, ZIP,
régénération des métadonnées. Payant : uniquement un nouveau fond d'image, après estimation et confirmation.

**Productions existantes.** Le kit se crée à l'ouverture, gratuitement, depuis le script et les sous-titres déjà produits.
Les images de scène d'anciennes productions ont pu recevoir le gabarit d'images global du moteur : le kit l'indique.

**Retour arrière.** Redéployer l'image précédente ; la table `publication_kits` peut rester (ignorée). Aucune donnée de
production n'est modifiée par le kit.

**Limites avant une publication directe.** Le moteur historique ne génère pas d'image seule (fonds = images de scène) ;
pas de programmation, d'envoi ni de comptes ; le contrôle avant publication est indicatif ; playlist, catégorie et
audience sont à saisir sur la plateforme.

## Nouvelle tentative après un échec

Une production échouée reste dans l'historique, avec sa cause. « Préparer à nouveau » ouvre la page de production
avec le sujet (et le script déjà écrit, le cas échéant) conservés, et crée une **nouvelle tentative liée** (V2 du
même sujet, « nouvelle tentative de V1 ») : nouvelle estimation, nouveau preflight, **nouvelle confirmation**.
Rien de la confirmation ni du coût précédents n'est repris.

## Limitations connues

- Une V2 **régénère tous les médias** (voix, images, musique) : aucune réutilisation.
- Pas d'annulation d'une génération en cours (le moteur n'en offre pas).
- Le fournisseur de texte est celui du moteur (`llm_provider`) : si le projet en demande un autre, Lody bloque
  (voir preflight) ; régler `llm_provider` côté moteur (et le redémarrer) ou fournir son script.
- Visuels « fichiers locaux » non pris en charge par ce connecteur.
- Le moteur ne renvoie pas le coût facturé.
