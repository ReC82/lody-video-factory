# Documentation Lody Video Factory

Index des pages propres à Lody (`docs/lody*.md`). Les `README*.md` à la racine du dépôt
sont ceux du projet amont MoneyPrinterTurbo et ne décrivent pas Lody.

Chaque page `docs/lody*.md` doit figurer ici : `test/lody/test_docs_index.py` le vérifie,
ainsi que l'existence des fichiers liés.

## Application

- [Interface et déploiement](lody-ui.md) — interface Streamlit indépendante (`webui/Lody.py`)
  organisée par projets, avec ses commandes de build, de déploiement et de retour arrière.
- [Export / import de configuration de projet](lody-project-transfer.md) — format JSON versionné
  pour exporter ou importer un projet entier (paramètres, personnages, lieux), en création ou en
  mise à jour explicite.

## Génération

- [Génération vidéo](lody-generation.md) — parcours sujet → estimation → confirmation → suivi →
  vidéo, architecture du module de génération, coûts, stockage et limitations connues.
- [Contrat du moteur historique](lody-engine-contract.md) — contrat de l'API MoneyPrinterTurbo
  utilisée comme connecteur hérité, relevé dans le code et connu du seul `mpt_connector.py`.
- [Contrat de storyboard animé 2D](lody-animation-contract.md) — format `lody.animation/1` d'une
  scène animée, catalogue d'assets réutilisables versionnés et gel des versions par production.
- [Fournisseur Animation 2D et rendu hybride](lody-animation-provider.md) — modes de rendu d'un
  projet (Classique, Animation 2D, Hybride), fournisseur `animation_2d`, worker isolé et reprise.
- [Voltix — fiche personnage et rig SVG](lody-voltix.md) — mascotte jaune aux jambes à ressort :
  palette, proportions, rig SVG en calques, expressions, visèmes, gestes, accessoires BNC et catalogue importable.
- [Moteur de scène Remotion](lody-animation-remotion.md) — adaptateur `lody.animation-scene/1` → MP4,
  rig `lody.rig/1`, synchro labiale, mixage voix + musique ; démo [Voltix #150](animation/voltix-150/README.md).
- [Worker 2D Remotion en Docker — pilote DEV](lody-animation-worker-docker.md) — image reproductible,
  compose DEV isolé, préflight (licence Remotion comprise), atelier 2D, mesures CPU/RAM.
- [Réédition ciblée et contrôle qualité du rendu 2D](lody-animation-reedit.md) — cache de scène par
  empreinte des dépendances, plan d'une V2, prévisualisation basse résolution, contrôles automatiques du MP4 ; écran « Nouvelle version » scène par scène (#159).
- [Voix ElevenLabs du rendu 2D](lody-animation-voix.md) — voix de la fiche personnage, lexique de
  prononciation, cache, piste calée et repères recalés pour la bouche et les sous-titres (#164).
- [Recette contrôlée du rendu 2D parlé](lody-recette-dev-2d.md) — prérequis DEV, jeu de test 2 scènes
  Voltix V1/V2/V3, coût ElevenLabs estimé et plafonné, autorisation écrite, critères PASS/FAIL (#187).

## Voix

- [Catalogue des voix ElevenLabs](lody-elevenlabs-voices.md) — choix d'une voix ElevenLabs
  depuis l'interface sans recopier son `voice_id`, à partir d'un catalogue généré côté hôte.
- [Choisir la voix d'un personnage](lody-voix-personnage.md) — comparer 2 à 3 voix ElevenLabs
  sur le même dialogue, budget visible avant tout appel, voix mémorisée pour tous les épisodes.

## Sécurité

- [Paramètres système : clés d'API du moteur](lody-secrets.md) — saisie et remplacement des clés
  du moteur depuis Lody, jamais réaffichées, validées en trois étapes avant d'être annoncées.
- [Authentification administrateur](lody-auth.md) — authentification qui protège la page
  « Paramètres système » et le remplacement des clés d'API depuis l'interface.

## Orchestrateur

- [LODYORCH](lodyorch.md) — orchestrateur autonome qui traite un ticket GitHub à la fois et
  livre une pull request, sans jamais fusionner ni déployer.
- [Fusion automatique des PR — politique](lody-fusion-auto.md) — label `merge:auto`, critères
  cumulatifs, exclusions ; fusion par le superviseur après CI verte (#200), à blanc par défaut.
