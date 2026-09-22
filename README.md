# Lody Video Factory

**Lody Video Factory** est une plateforme de génération de vidéos courtes assistée par IA, pensée autour d’un fonctionnement **par projets**.

L’objectif est de pouvoir créer plusieurs séries ou chaînes avec leurs propres paramètres éditoriaux, voix, visuels, sous-titres, musique et règles de génération, puis lancer une nouvelle vidéo depuis une interface volontairement simple.

> Projet en développement actif. Le dépôt est issu de MoneyPrinterTurbo et évolue progressivement vers une application distincte, avec sa propre UX, sa propre gestion de projets et ses propres workflows.

## Objectif du projet

Lody Video Factory vise à transformer un processus de génération vidéo complexe en un workflow simple :

1. choisir un projet ;
2. saisir un sujet ou un prompt ;
3. générer le script et les paramètres nécessaires ;
4. produire la vidéo avec les réglages du projet ;
5. consulter et récupérer le résultat.

Chaque projet possède sa propre configuration afin d’éviter de reconfigurer manuellement le moteur à chaque génération.

Exemples de projets actuellement visés :

- vidéos pédagogiques audiovisuelles ;
- contenus crypto / Web3 gaming ;
- séries courtes avec identité visuelle et personnages récurrents ;
- futurs formats destinés à YouTube Shorts, TikTok et autres plateformes verticales.

## Principes

- **Interface simple** : la page principale doit rester centrée sur le prompt et le bouton de génération.
- **Configuration par projet** : les paramètres avancés sont stockés dans le projet.
- **Génération reproductible** : voix, format, style visuel, sous-titres et autres choix doivent rester cohérents entre les épisodes.
- **Contrôle humain** : une génération automatique n’est jamais considérée comme correcte par défaut.
- **Séparation des secrets** : les clés API et credentials ne doivent pas être stockés dans les exports projet ni dans le dépôt.
- **Évolution progressive** : les fonctions héritées de MoneyPrinterTurbo sont conservées quand elles sont utiles puis progressivement encapsulées ou remplacées.

## Architecture actuelle

Le dépôt contient encore le moteur et plusieurs composants hérités de MoneyPrinterTurbo, mais Lody Video Factory dispose déjà de sa propre couche applicative.

### Interface Lody

L’interface dédiée est lancée avec Streamlit sur le port local :

```text
127.0.0.1:8601
```

Elle est isolée de l’ancienne WebUI MoneyPrinterTurbo.

### Moteur de génération

Lody Video Factory communique avec le moteur de génération via son API interne.

Configuration actuelle du déploiement :

```text
Lody UI        -> 127.0.0.1:8601
Lody Auth      -> 127.0.0.1:8602
MPT API        -> 127.0.0.1:8080
Ancienne WebUI -> 127.0.0.1:8501
```

Les services Lody et le moteur communiquent via le réseau Docker `moneyprinterturbo_default`.

### Données

Les projets et productions Lody utilisent une base persistante dédiée.

Le volume Docker principal est :

```text
lody-video-factory-data
```

Le stockage du moteur reste séparé.

## Déploiement Docker

Le fichier principal pour l’interface Lody est :

```text
docker-compose.lody.yml
```

Démarrage :

```bash
docker compose -f docker-compose.lody.yml up -d --build
```

Logs :

```bash
docker compose -f docker-compose.lody.yml logs -f
```

Arrêt :

```bash
docker compose -f docker-compose.lody.yml down
```

> Éviter `docker compose down -v` sur l’environnement de production si le volume de données doit être conservé.

La documentation détaillée de l’interface est disponible dans [docs/lody-ui.md](docs/lody-ui.md).

## Authentification et secrets

Lody Video Factory utilise un service d’authentification dédié pour les paramètres sensibles.

Les secrets ne doivent jamais être commités dans Git.

Les fichiers et dossiers locaux tels que :

```text
config.toml
storage/
secrets/
engine-report/
```

sont destinés au runtime et doivent rester hors du dépôt lorsque leur contenu contient des données locales ou sensibles.

Documentation :

- [Authentification](docs/lody-auth.md)
- [Gestion des secrets](docs/lody-secrets.md)
- [Contrat avec le moteur](docs/lody-engine-contract.md)
- [Pipeline de génération](docs/lody-generation.md)

## Gestion par projets

Le modèle cible repose sur des projets indépendants.

Un projet peut définir notamment :

- nom et identité ;
- langue ;
- ton éditorial ;
- paramètres de script ;
- modèle IA ;
- voix et TTS ;
- format vidéo ;
- style visuel ;
- personnages et références visuelles ;
- sous-titres ;
- musique ;
- règles de montage ;
- paramètres propres à une série.

L’objectif est qu’un utilisateur puisse passer d’un projet à l’autre sans modifier manuellement toute la configuration du moteur.

## Workflow de génération

Le workflow visé est :

```text
Projet
  -> Prompt
  -> Script
  -> Découpage en scènes
  -> Génération / sélection des visuels
  -> Voix
  -> Sous-titres
  -> Musique
  -> Montage
  -> Contrôles
  -> Vidéo finale
```

Les contrôles après génération doivent notamment porter sur :

- cohérence script / visuels ;
- continuité des personnages ;
- prononciation TTS ;
- sous-titres ;
- musique ;
- durée ;
- coût de génération.

## Fonctionnalités en développement

Le projet évolue activement via les issues GitHub.

Parmi les axes actuellement travaillés ou planifiés :

- nouvelle UX orientée projets ;
- paramètres séparés par projet ;
- personnages récurrents et cohérence visuelle ;
- amélioration du pipeline image / vidéo ;
- export et import complet des configurations de projet ;
- notifications de fin de génération ;
- automatisation plus poussée du workflow ;
- amélioration de la supervision des générations ;
- gestion plus fine des coûts et des fournisseurs IA.

Les fonctionnalités listées ici ne sont pas toutes considérées comme terminées.

Voir les [issues du dépôt](https://github.com/ReC82/lody-video-factory/issues) pour l’état réel du développement.

## Providers et services IA

Lody Video Factory est conçu pour pouvoir exploiter plusieurs fournisseurs selon les besoins du projet.

Le moteur hérité prend notamment en charge différents services pour :

- génération de texte ;
- synthèse vocale ;
- génération d’images ;
- génération vidéo ;
- musique.

Dans l’environnement actuel, le projet utilise ou expérimente notamment :

- modèles compatibles OpenAI ;
- ElevenLabs pour le TTS ;
- génération d’images compatible OpenAI ;
- OFox AI pour certains tests vidéo ;
- MoneyPrinterTurbo comme moteur de génération historique.

La disponibilité réelle dépend de la configuration du serveur et des clés présentes localement.

## Sécurité

Quelques règles importantes :

- ne jamais commiter de clé API ;
- ne jamais inclure de secret dans un export de projet ;
- ne pas exposer directement les services internes sur Internet ;
- conserver les services sensibles derrière le reverse proxy et l’authentification ;
- traiter les fichiers de configuration runtime comme des secrets potentiels.

## Structure utile du dépôt

```text
app/                     Backend / API historique et services
webui/                   Interfaces Web, dont l’interface Lody
lody_auth/               Service d’authentification Lody
deploy/                  Éléments de déploiement
docs/                    Documentation technique
scripts/                 Scripts d’exploitation et de maintenance
docker-compose.lody.yml  Stack Lody Video Factory
config.example.toml      Exemple de configuration moteur
```

## État du projet

Lody Video Factory est actuellement un **projet personnel en développement actif**.

L’interface, le modèle de données et les workflows peuvent encore évoluer rapidement. Le dépôt ne doit pas être considéré comme un produit stable ou prêt pour un déploiement générique sans adaptation.

## Origine et crédits

Lody Video Factory est basé à l’origine sur le projet open source **MoneyPrinterTurbo** de Harry Zhang / harry0703 :

https://github.com/harry0703/MoneyPrinterTurbo

Une partie importante du moteur de génération, des intégrations et de la structure historique du dépôt provient de ce projet.

Lody Video Factory ajoute progressivement une couche produit distincte centrée sur :

- la gestion multi-projets ;
- une UX simplifiée ;
- des configurations éditoriales persistantes ;
- des workflows de génération adaptés à plusieurs chaînes et séries ;
- l’automatisation et la supervision du pipeline.

Le crédit au projet upstream doit être conservé conformément à sa licence.

## Licence

Ce dépôt conserve la licence héritée du projet upstream. Voir [LICENSE](LICENSE) pour les conditions applicables.
