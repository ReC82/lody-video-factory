# Audit du backlog — novembre 2026 (#124)

Audit des anciens tickets ouverts au 11 novembre 2026, après la décision de faire du **rendu animé 2D**
(personnages articulés, calques, synchronisation voix/lèvres, réédition par scène) la voie principale,
tout en conservant le moteur historique en **mode Classique** puis un **mode Hybride**.

Ce document est une **proposition** : aucun ticket n'a été fermé, réétiqueté ni modifié par l'agent.
Chaque clôture proposée doit être validée par le propriétaire, ticket par ticket, au vu des preuves citées.

## Méthode et limites

- Sources : corps et commentaires de chaque ticket, liste complète des PR (`gh pr list --state all`),
  commits de `main` et lecture du code de `main` (`fbcc633`).
- Une PR **fusionnée** est une preuve de livraison dans `main`, pas une preuve de **déploiement** :
  la production (`/srv/moneyprinterturbo`) est volontairement inaccessible à l'agent. Le déploiement
  reste à confirmer par le propriétaire pour chaque clôture proposée.
- L'état de la CI des anciennes PR n'a pas été rejoué ; seules les PR fusionnées sont citées comme preuves.
- Décision éditoriale rappelée : aucune ancienne simulation pan/zoom (Ken Burns) ne doit être présentée
  comme « animation 2D réussie ». Les tickets visuels du pipeline d'images indépendantes relèvent du
  mode Classique.

## Synthèse

| Décision proposée | Tickets |
|---|---|
| **Clôture proposée** (livré dans `main`, preuves ci-dessous) | #91, #93, #109, #2, #3, #4, #8 |
| **Clôture proposée sous condition** (validation visuelle ou constat du propriétaire) | #78, #79, #1, #6 |
| **Garder ouvert — prioritaire / transversal** | #5, #7, #29, #39, #40, #80, #87, #92, #94, #110 |
| **Dédupliquer** en un seul chantier | #54, #71, #114 |
| **Mode Classique — en attente** après #118/#119 | #73 (et le reliquat de #78/#79) |
| **Garder ouvert — reporté** (vision long terme) | #45, #47, #48 |

Les tickets récents de l'orchestrateur et de l'animation (#116 à #142) ne font pas partie de l'audit :
#116, #117, #118, #119, #122, #123, #129 sont déjà fermés ; #120 (PR #144) et #127/#139 (PR #143) sont
en cours de revue.

## A. Clôtures proposées — livré dans `main`

| Ticket | Preuve | Reste à vérifier par le propriétaire |
|---|---|---|
| #91 Téléchargements directs logs / configuration | PR #97 fusionnée le 03/10 (`238e62b`) ; boutons « Télécharger les logs complets » et « Télécharger la configuration complète » dans `webui/lody/view_tracking.py` ; enrichi par #99 (correspondance scène → image). | Présence des deux boutons sur une production en production réelle. |
| #93 Suivi par étapes réelles | PR #95 fusionnée le 02/10 (`3cbc0e5`), module `webui/lody/generation/progress.py`. | Le suivi n'affiche plus de pourcentage inventé en production. |
| #109 Import et identifiant de voix ElevenLabs | PR #111 fusionnée le 06/10 (`f8d24bc`), « Corrige #109 » : trois causes identifiées (court-circuit de `brief_settings`, modèle sans emplacement de voix, identifiant mal placé avalé) et tests de round-trip. | Cocher les critères du ticket après un import réel (le ticket a encore ses cases vides). |
| #2 Modèle de données projets + instantané | PR #9 (`bdf98e7`), #10 (`5db521a`), #22 (`066de57`, instantané immuable par production) ; dépôt SQLite `webui/lody/projects.py`. | — |
| #3 Paramètres du projet organisés | PR #10 (`5db521a`) ; `webui/lody/view_settings.py`, `view_form.py`. | — |
| #4 Accueil simplifié | PR #9, #12 (`aed0c6c`) ; `webui/lody/view_home.py`. | — |
| #8 Prototype 1 Docker Compose | PR #9 fusionnée le 21/09 (`bdf98e7`) ; `docker-compose.lody.yml`, `docs/lody-ui.md`. Prototype dépassé par la génération réelle (#12). | — |

## B. Clôtures proposées sous condition

- **#78 — identité canonique du personnage à chaque scène.** Livré en substance par PR #99 (`4933c55`,
  description complète répétée à chaque scène, bloc en tête du prompt), #98 (`44e9b05`, borne portée à
  3000 caractères) et #96 (`91a82f7`, personnage parlant présent dans chaque scène). Essai réel
  `prd_3acea928d942` rapporté sur #92 : même personnage reconnaissable sur 3 images. Non couvert
  explicitement : contraintes négatives dédiées. **Proposition** : clore après validation visuelle du
  propriétaire ; le reliquat éventuel rejoint le mode Classique.
- **#79 — lieu canonique à chaque scène.** Même PR #99 (la description du lieu est aussi répétée
  intégralement). **Proposition** identique à #78.
- **#1 — Epic interface simplifiée.** Tous les critères de fin sont couverts par #2/#3/#4/#8 sauf la
  partie sécurité (#5, #7) et la duplication de projet (#6). **Proposition** : clore l'epic quand #5 et
  #7 sont tranchés, ou dès maintenant en laissant #5/#7 ouverts seuls.
- **#6 — gestion des projets.** Livré : liste, sélection, création, renommage, archivage/restauration
  (`ProjectRepository.archive/restore`, `view_project.py`). **Absent** : « Dupliquer un projet » (seuls
  les templates de production ont `duplicate_to_project`). Contournement existant : export puis import
  complet (#44, PR #56). **Proposition** : clore si ce contournement suffit, sinon réduire le ticket à
  la seule duplication.

## C. Garder ouvert — prioritaire ou transversal

### Sécurité (#5, #7) — à conserver absolument

- `docs/lody-auth.md` (PR #41) : l'authentification protège **uniquement** la page « Paramètres
  système » et le remplacement des clés. Le code de `webui/lody/app.py` ne demande aucune session pour
  les autres vues (projets, productions — qui déclenchent des appels payants).
- L'ancienne WebUI (`webui/Main.py`, ~l. 2986) contient toujours le bouton « Export Keys » ;
  `docs/lody-auth.md` indique qu'elle est arrêtée en production, ce que l'agent ne peut pas vérifier.
- #7 : livré en grande partie (PR #26, #41 ; secrets jamais réaffichés, `secrets_guard.py`), mais les
  critères « aucun secret dans les journaux » et « clés lues depuis l'environnement » restent à
  confirmer.
- **Proposition** : garder #5 et #7 ouverts ; le propriétaire confirme (1) si une protection nginx
  couvre déjà tout le site, (2) que l'ancienne WebUI n'est plus exposée. S'ils sont vrais, #5 peut être
  clos avec ce constat et #7 réduit à la vérification des journaux.

### Narration, voix et synchronisation (utiles aux deux moteurs)

- **#92 — retour test Eli.** Continuité visuelle traitée (PR #96, #98–#103), jeu vocal traité (PR #104,
  #105, #106, #107). Restent : fidélité du scénario, synchronisation images/sous-titres sur la parole
  réelle, ouverture titrée. Ces défauts sont **précisément** ceux que vise le rendu 2D (timecodes de
  parole du contrat `lody.animation/1`). **Proposition** : garder ouvert comme référence qualité ;
  ne plus y ajouter de correctif d'images fixes, et le clore lorsque le POC 2D (#120/#121) aura été
  validé sur un vrai MP4 ou en le découpant.
- **#94 — storyboard validable avant production.** Partiellement livré : direction vocale par réplique
  (PR #105, #106) et indications scéniques `{…}` (PR #99). Reste : template TXT/JSON importable et
  relecture du storyboard avant appel payant. **Recouvrement** avec #119 (contrat `lody.animation/1`)
  et #110 (templates de production). **Proposition** : garder ; reformuler pour viser le contrat
  animé comme format commun, pour éviter un second schéma de storyboard.
- **#80 — storyboard ancré dans le script.** Non livré. Utile aux deux moteurs (une scène animée doit
  aussi être justifiée par le script). Garder.
- **#87 — segmentation TTS multi-locuteurs.** Prérequis #86 livré (PR #88, #89, #90) ; orchestration
  Lody non livrée (aucun `speaker_type` dans `webui/lody`). Indispensable au 2D (dialogues à plusieurs
  personnages, timecodes par réplique). Garder, priorité haute après #120.
- **#39 — voix multiples.** Dépend de #87. Garder.
- **#40 — plusieurs lieux par scène.** En 2D, chaque scène choisit déjà son décor par calque d'asset
  (`lody.animation/1`) ; le ticket reste utile pour le mode Classique et pour la sélection multi-lieux
  à la création d'une production. Garder, priorité moyenne.
- **#29 — epic personnages et lieux.** Sa liste de contrôle est **obsolète** : #75, #76, #77, #81 sont
  fermés (PR #82, #83, #85, #84). Garder l'epic, mettre à jour les cases ; les fiches
  personnages/lieux sont la base du catalogue d'assets 2D (#119, #136).

### Plateforme

- **#110 — templates de production.** PR A fusionnée (`b21bebe`, modèle + CRUD, `webui/lody/templates.py`) ;
  le module n'est encore utilisé par **aucune vue** : la PR B (application, interface, import/export
  de production) reste à faire. Garder.

## D. Dédupliquer #54 / #71 / #114 en un seul chantier

Les trois tickets décrivent la suppression avec des exigences complémentaires :

| Exigence | #71 | #114 | #54 |
|---|---|---|---|
| Suppression définitive d'un projet, aperçu exact, saisie du nom, transaction SQLite unique | ✔ (le plus exigeant) | ✔ | — |
| Refus si une production est confirmée / en file / en cours ; rejeu Streamlit sans effet | ✔ | — | — |
| Suppression d'une **production** individuelle | hors périmètre | ✔ | ✔ (version) |
| Fichiers exclusifs seulement, jamais hors répertoire autorisé, erreurs de nettoyage signalées | ✔ | ✔ | ✔ |
| Historique de versions, version retenue, suppression d'assets individuels, corbeille, orphelins, rétention | — | hors périmètre | ✔ |

**Plan unique proposé** (sans perte d'exigence) :

1. **Lot 1 — projet** : #71 sert de spécification de référence ; #114 y ajoute « projet archivé
   supprimable ». Fermer #114 en le déclarant couvert par #71 + lot 2 (sur décision du propriétaire).
2. **Lot 2 — production** : partie « production » de #114, avec les garde-fous de #71 (aperçu, refus si
   active, transaction, fichiers exclusifs).
3. **Lot 3 — versions et assets** : #54 reste ouvert seul pour l'historique de versions, la corbeille et
   le nettoyage des orphelins. À relier à la réédition par scène (#121) : un plan réédité crée une
   nouvelle version d'asset (catalogue versionné de #119), ce qui change le modèle de suppression.

La pression disque est réelle (#122, ENOSPC) : le lot 1+2 mérite une priorité P2 juste après le POC.

## E. Mode Classique — en attente

- **#73 — références OpenAI transmises.** Partiellement livré par PR #100 (`4d4b22c`) : une référence
  de **personnage** est envoyée à `POST /v1/images/edits` à chaque scène (mono-personnage). Restent :
  références de **lieu**, plusieurs personnages, feature flag. Conformément au constat de #124, ne pas
  investir avant l'évaluation du POC 2D ; à réévaluer ensuite pour le mode Classique. Le label
  `agent:blocked` actuel est cohérent.
- **#78 / #79** : voir B ; tout reliquat est rattaché au mode Classique.

## F. Reporté — vision long terme

- **#45 — e-mail de fin de génération vidéo.** Distinct de #123 (rapport de travail de l'agent, livré
  par PR #131) : le SMTP de lodyorch n'envoie rien à la fin d'une production Lody. Garder ; l'envoi
  SMTP de #123 peut servir de modèle technique.
- **#47 — API publique.** Garder. Recouvre en partie #128 (création de personnages/lieux/vidéos via
  tickets) : #128 en est un premier usage piloté par GitHub, sans API publique.
- **#48 — commandes vocales.** Dépend de #47. Garder, priorité basse.

## Ordre de travail recommandé

1. P0/P1 en cours : #127/#139 (PR #143), #120 (PR #144), puis #121 — POC 2D évalué sur un vrai MP4.
2. #87 puis #39 (dialogues multi-voix, nécessaires au 2D).
3. #80 et la reformulation de #94 autour de `lody.animation/1`.
4. Sécurité #5/#7 (constat du propriétaire, puis correctif si l'application entière est publique).
5. Suppression : lots 1 et 2 (#71 + #114), puis #54.
6. #110 PR B, #40, puis le mode Classique (#73, reliquats #78/#79).
7. Long terme : #45, #47, #48.

## Actions demandées au propriétaire

1. Valider ou refuser chaque clôture de la section A (7 tickets), après constat du déploiement.
2. Valider visuellement #78/#79 (production `prd_3acea928d942` citée sur #92) avant clôture.
3. Dire si la duplication de projet (#6) reste nécessaire.
4. Confirmer l'exposition réelle du site (#5) : protection globale ou non, ancienne WebUI arrêtée.
5. Accepter le plan de dédoublonnage #54/#71/#114 et la fermeture de #114 une fois couvert.
6. Mettre à jour la liste de contrôle de l'epic #29.
