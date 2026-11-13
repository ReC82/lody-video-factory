# Navigation et architecture d'information (ticket #213)

Ce document décrit les écrans de Lody, la portée réelle des données et le parcours à une main. Il
fixe aussi le découpage en lots. Le **lot 1** (navigation, sans migration) est livré par la PR du
ticket #213. Le **lot 2** (bibliothèque partagée) reste une proposition à valider.

## 1. Inventaire des écrans

| Écran | URL | Module |
|---|---|---|
| Accueil — Projets | `/` | `view_home` |
| Personnages (tous projets) — **nouveau** | `?vue=personnages` | `view_library.render_characters` |
| Ressources (tous projets) — **nouveau** | `?vue=ressources` | `view_library.render_resources` |
| Nouveau projet | `?vue=nouveau` | `view_form` |
| Page du projet | `?projet=<id>` | `view_project` |
| Paramètres du projet | `?projet=<id>&vue=parametres` (`modifier` accepté) | `view_settings` |
| Nouvelle production (brouillon #189) | `?projet=<id>&vue=production` | `view_production` |
| Suivi d'une production | `?projet=<id>&vue=suivi&production=<id>` | `view_tracking` |
| V2 d'une production | `?projet=<id>&vue=v2&production=<id>` | `view_v2` |
| Personnages du projet | `?projet=<id>&vue=personnages` | `view_characters` |
| Lieux du projet | `?projet=<id>&vue=lieux` | `view_locations` |
| Réglages système (verrouillé) | `?vue=systeme` | `view_system_settings` |

Aucun lien profond existant ne change de destination. Avec `projet`, `vue=personnages` ouvre
toujours la page du projet. Le sens transverse ne s'applique que **sans** `projet`.

## 2. Portée réelle des données (vérifiée dans `lody/db.py`)

| Donnée | Table | Portée |
|---|---|---|
| Personnages (voix, `voice_direction`, image de référence) | `characters` | **projet** (`project_id NOT NULL`) |
| Lieux (image de référence) | `locations` | **projet** |
| Assets d'animation 2D et leurs versions | `animation_assets`, `animation_asset_versions` | **projet** |
| Templates de production | `production_templates` | **projet** |
| Brouillon « Nouvelle production » | `production_compose_drafts` | **projet** (une ligne) |
| Productions, kits de publication | `productions`, `publication_kits` | **projet** |
| Images de référence (fichiers) | `<données>/…/<projet>/…` (`reference_images`) | **projet** |
| Extraits de voix (cache audio) | `<données>/voice-samples/` | cache technique global, indexé par empreinte |

**Il n'existe aucune bibliothèque globale.** La seule réutilisation entre projets passe par l'export /
import (projet entier #44, personnages et lieux #57). C'est une copie explicite, avec contrôle des
noms en double.

## 3. Arborescence cible

```
Lody
├── Projets            (accueil : Reprendre, Nouveau projet, recherche, liste compacte #195)
│   └── Projet         (← Retour au projet sur chaque sous-page #212 ; « Tous les projets » dans l'en-tête)
│       ├── Nouvelle production / Suivi / V2
│       ├── Personnages du projet
│       ├── Lieux du projet
│       └── Paramètres
├── Personnages        (tous les projets actifs, en lecture ; ouverture dans le projet)
└── Ressources         (Lieux | Éléments animés, en lecture ; ouverture dans le projet)
```

- La barre *Projets / Personnages / Ressources* n'apparaît que sur ces trois pages de premier niveau.
  Elle remplace alors le lien « Tous les projets », qui serait redondant.
- Sur une page de projet, l'en-tête garde « Tous les projets ». #212 ajoute « Retour au projet ».
- Chaque ligne transverse affiche « Dans le projet « X » » et ne s'ouvre que dans ce projet. Les
  vues transverses ne modifient rien et ne copient rien. Aucun élément d'un projet n'apparaît dans
  un autre projet.
- Les projets archivés sont exclus des vues transverses : leurs pages demandent d'abord une
  restauration.

## 4. Parcours à une main (nombre d'appuis)

| Besoin | Avant | Après |
|---|---|---|
| Reprendre le dernier projet | rechercher ou déplier la ligne, puis « Ouvrir » (2 appuis ou plus) | « Reprendre : X » (1) |
| Retrouver Voltix sans savoir dans quel projet il est | ouvrir chaque projet, puis Personnages | Personnages → déplier Voltix (2) → ouvrir dans le projet (3) |
| Voir tous les lieux | idem, projet par projet | Ressources (1) |
| Revenir à la liste depuis un projet | « Tous les projets » (1) | inchangé (1) ; recherche et ligne ouverte conservées |

« Reprendre » propose le dernier projet ouvert dans la session s'il est encore actif. Sinon, il
propose le projet actif modifié le plus récemment. Aucun projet archivé n'est proposé. Le brouillon
de production (#189) est enregistré en base par projet : naviguer entre les domaines ne le perd pas.

## 5. Voix : modèle existant, sans nouvelle hiérarchie

Ce modèle est vérifié dans `generation/narrative_context.resolve_voice` et `animation/audio.voice_plan`.

- **Personnage qui parle** : la voix de sa fiche (`voice_provider` + `external_voice_id`, et
  `voice_direction` pour la 2D) est prioritaire pour ses répliques.
- **Vidéo classique** : la voix du personnage principal sélectionné (ou du seul personnage
  sélectionné) remplace la voix du projet. Sinon, c'est la voix de narration du projet.
- **Projet sans personnage** : voix de narration du projet (Paramètres).
- **Personnage sans voix complète** :
  - en vidéo classique : repli consigné sur la voix du projet ;
  - en 2D : voix provisoire signalée.
- **Figurant muet** : un personnage sans réplique ne parle pas. Il n'existe **pas** de case « muet »
  sur la fiche. L'ajouter serait un changement de modèle, à traiter dans un ticket séparé si besoin.

Cette aide est affichée, en version courte, dans *Personnages → Portée et voix : comment ça marche ?*.

## 6. Lot 2 proposé : bibliothèque partagée (migration additive, à valider)

Le lot 2 ne démarre qu'après validation du propriétaire et fusion du lot 1.

1. Nouvelle table `library_items` : `id`, `kind` (`character`, `location`, `asset`), `source_project_id`,
   `source_id`, `shared_at`, `is_active`. Aucune colonne existante n'est modifiée ni supprimée.
2. Nouvelle table `project_library_links` : `project_id`, `library_item_id`, `linked_at`. Un projet
   n'utilise une ressource partagée que s'il l'a **liée explicitement**.
3. Le partage est une action volontaire (« Rendre réutilisable »). Sans ce geste, rien n'est visible
   ailleurs.
4. La référence est stable (id de la ligne d'origine). Il n'y a pas de duplication silencieuse : une
   « copie dans ce projet » reste l'import existant (#57), une action distincte et nommée.
5. Dans l'interface, deux étiquettes séparent « Dans ce projet » et « Réutilisable (partagé depuis
   X) ». La sélection des personnages d'une production (#34) reste par projet tant que le lien
   n'existe pas.

Questions à trancher avant le lot 2 :

- Un personnage partagé modifié dans son projet d'origine doit-il changer partout ?
- Ou faut-il le figer par version, comme les assets d'animation ?

## Captures (lot 1, données de démonstration)

Les captures ont été prises avec Chromium sans interface, sur une base de test locale.

- 390 px : [accueil](captures/213/mobile-accueil.png), [personnages](captures/213/mobile-personnages.png),
  [ressources](captures/213/mobile-ressources.png).
- 360 px : [personnages](captures/213/mobile360-personnages.png).
- 1280 px : [accueil](captures/213/desktop-accueil.png), [personnages](captures/213/desktop-personnages.png).

Le titre de l'accueil y apparaît pâle : la capture tombe pendant son animation d'entrée, qui existait
déjà avant ce ticket.
