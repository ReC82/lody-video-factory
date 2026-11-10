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

## Voix

- [Catalogue des voix ElevenLabs](lody-elevenlabs-voices.md) — choix d'une voix ElevenLabs
  depuis l'interface sans recopier son `voice_id`, à partir d'un catalogue généré côté hôte.

## Sécurité

- [Paramètres système : clés d'API du moteur](lody-secrets.md) — saisie et remplacement des clés
  du moteur depuis Lody, jamais réaffichées, validées en trois étapes avant d'être annoncées.
- [Authentification administrateur](lody-auth.md) — authentification qui protège la page
  « Paramètres système » et le remplacement des clés d'API depuis l'interface.

## Orchestrateur

- [LODYORCH](lodyorch.md) — orchestrateur autonome qui traite un ticket GitHub à la fois et
  livre une pull request, sans jamais fusionner ni déployer.

## Audits

- [Audit du backlog — novembre 2026](audits/2026-11-backlog-audit.md) — classement des anciens
  tickets après le choix du rendu animé 2D : clôtures proposées avec preuves, dédoublonnage
  #54/#71/#114, mode Classique et ordre de travail recommandé.
