# LODYORCH — orchestrateur autonome de Lody Video Factory (ticket #116)

LODYORCH traite **un ticket GitHub à la fois**, sans surveillance : il choisit le ticket, lance une
session Claude Code non interactive qui développe, teste, pousse une branche, ouvre une pull request
et rend compte ; puis il passe au suivant. **Il ne fusionne et ne déploie jamais rien** : chaque PR
attend la revue du propriétaire.

## Une seule console

Comme pour Dig et RPG Quest, **la console est la session tmux `lodyorch` dans laquelle le
propriétaire parle à Claude** (Claude interactif dans `/srv/moneyprinterturbo`). Aucune deuxième
console, aucun renommage. La file de tickets tourne **sans fenêtre** dans `lodyorch.service` et ne
lance jamais deux sessions Claude de code en même temps.

Coordination (ce que Dig et RPG Quest n'ont pas : chez eux, les deux peuvent travailler en même
temps) :

- chaque message du propriétaire déclenche le crochet `UserPromptSubmit` → `lodyorch human busy` ;
  chaque réponse terminée de Claude, le crochet `Stop` → `lodyorch human idle` ;
- tant que le propriétaire a la main, **aucune session de file ne démarre** ; elle reprend après
  15 min de calme (`human_quiet_seconds`) ;
- si une session de file travaille quand le propriétaire écrit, elle **range son travail** : le
  crochet de garde ne lui permet plus que commit, push et rapport ; après 5 min
  (`yield_grace_seconds`) elle est arrêtée. Le ticket reste en cours, sans pénalité, et il est
  **repris en premier** ensuite. Claude, dans la console, en est informé par une ligne de contexte ;
- la ligne d'état de la console (`lodyorch statusline`) affiche la file : ticket, phase, PR en revue.

Compromis assumé : la console et la file sont deux processus Claude distincts (l'un interactif,
l'autre non). Une même session Claude ne peut pas à la fois recevoir les messages du propriétaire
et être pilotée par un superviseur sans synchronisation ; ici, la synchronisation est explicite
(crochets + verrou), et le propriétaire n'a qu'une console à utiliser.

C'est une instance dédiée du superviseur déjà éprouvé sur l'hôte (LQORCH → JCORCH → DIGORCH,
`/opt/dig-orch/lqorch.py`), avec ses propres service, session tmux, état, verrou et worktrees.
Il ne partage rien en écriture avec `dig`, `digorch`, `jury-central` ou `rpgquest`.

## Éléments

| Élément | Emplacement |
|---|---|
| Sources versionnées | `deploy/lodyorch/` (ce dépôt) |
| Code installé, prompt, configuration | `/opt/lodyorch/` (root, lecture seule pour les sessions) |
| Commande | `/usr/local/bin/lodyorch` |
| Service | `lodyorch.service` (file, sans fenêtre) ; `lodyorch-notify.timer` (emails des rapports) |
| Console | session tmux `lodyorch` du propriétaire ; crochets dans `/srv/moneyprinterturbo/.claude/settings.local.json` (exclu de Git) |
| État, verrou, journaux, rapports | `~/.local/state/lodyorch/` (0700 : `state.db`, `supervisor.lock`, `sessions/*.jsonl`, `reports/`, `reports/runs/`) |
| Clone dédié | `~/lody-orch/repo` (jamais `/srv/moneyprinterturbo`) |
| Worktrees | `~/lody-orch/worktrees/ticket-<n>`, environnement Python partagé `~/lody-orch/venv` |

## Sélection des tickets

- Seuls les tickets **ouverts** portant `agent:auto` entrent dans la file ; les PR n'y entrent jamais.
- Ordre strict `priority:P0` → `P1` → `P2` → `P3`, puis par numéro. Sans label de priorité valide :
  P2. Deux labels de priorité : la plus haute l'emporte.
- Exclus : `agent:blocked`, `agent:human`, `wontfix`, `duplicate`, `invalid`, et un ticket qui perd
  `agent:auto`. Un `agent:in-progress` posé par quelqu'un d'autre que LODYORCH est un verrou : le
  ticket est mis de côté tant que le label reste.
- Dépendances : `Depends on #N` (ou « Dépend de #N », « Blocked by #N »…) dans le corps du ticket.
  Une dépendance n'est levée que lorsque `#N` est **fermé** (PR fusionnée) : une PR en attente de
  revue ne débloque rien. Une dépendance elle-même éligible est traitée d'abord.
- Une session interrompue (crash, redémarrage) est **reprise en premier**, sur la même branche et la
  même PR : jamais deux tickets entamés en parallèle, jamais de deuxième PR.

## Labels tenus par le superviseur

| Situation | Label |
|---|---|
| Session en cours | `agent:in-progress` |
| Bloqué (question, CI rouge après 2 corrections, 3 échecs) | `agent:blocked` |
| PR ouverte, en attente de revue | `status:needs-test` |

Le propriétaire reprend un ticket bloqué en **répondant dans le ticket** ou en **retirant
`agent:blocked`**.

## Protocole de traitement (ticket #117)

Référentiel des labels : `deploy/lodyorch/labels.json` (nom, couleur, description).
`lodyorch labels` compare le dépôt au référentiel (lecture seule) ; `lodyorch labels --apply` crée
les labels manquants et aligne couleur et description (jamais de suppression).

| Label | Posé par | Effet |
|---|---|---|
| `agent:auto` | propriétaire | éligible à la file automatique |
| `agent:human` | propriétaire | action humaine : hors file |
| `agent:blocked` | superviseur ou propriétaire | suspendu ; le retirer remet en file |
| `agent:in-progress` | superviseur | verrou visible ; posé par un tiers, il exclut le ticket |
| `priority:P0` … `P3` | propriétaire | ordre strict ; P2 si absent ou invalide |
| `status:needs-test` | superviseur | PR livrée : revue et validation fonctionnelle attendues |

Règles :

1. **Une seule issue active.** Le superviseur ne lance qu'une session (verrou `flock`) ; une issue
   interrompue est reprise avant toute autre.
2. **Un seul label de priorité.** Si plusieurs : la plus haute est retenue. Label `priority:*`
   invalide : P2. `agent:auto` avec `agent:human` : exclu. Label `agent:*` inconnu : signalé. Ces
   anomalies sont affichées par `lodyorch status` et `lodyorch queue`, et journalisées une fois.
   Rien n'est corrigé sur GitHub à la place du propriétaire.
3. **Les PR ne sont pas des tickets** : elles n'entrent jamais dans la file.
4. **`Depends on #N`** : le ticket attend que `#N` soit fermé (PR fusionnée).
5. **Prise en charge** : le superviseur publie UN commentaire de prise en charge par ticket
   (marqueur `<!-- lodyorch:prise-en-charge -->`, vérifié sur GitHub avant publication, donc pas de
   doublon après un crash). La session publie ensuite le plan et la branche, puis le résultat et la
   PR. Tout commentaire de l'orchestrateur porte `<!-- lodyorch -->`.
6. **Ne jamais écraser** une demande ou une tâche déjà démarrée : ticket interrompu repris en
   premier ; branche ou PR d'un tiers jamais réutilisée (la session rend `BLOCKED` avec la question) ;
   `agent:in-progress` d'un tiers respecté ; `skip`/`add` du propriétaire non remis en cause par la
   synchronisation.
7. **CI suivie par le superviseur, jamais attendue par Claude** (voir ci-dessous) : au plus 2
   corrections sur la même branche, puis `agent:blocked` avec le diagnostic.
8. **Idempotence** : une PR par ticket (recherchée avant création) ; une livraison déclarée sans PR
   n'est pas une livraison (reprise, puis blocage après 3 sessions) ; reprise sur la branche
   existante, jamais de rebase d'une branche poussée.

## Suivi de la CI sans Claude

Une session pousse sa branche, ouvre la PR et rend la main tout de suite (`WAITING_OWNER`). Le
superviseur suit ensuite la CI **sans session Claude** : une ou deux requêtes GitHub par PR toutes
les 5 min (`LQ_CI_POLL`). Pendant ce temps, la file traite un autre ticket **indépendant** (jamais
un ticket qui dépend de cette PR : une dépendance n'est levée qu'à la fusion), et les nouvelles
branches partent toujours de `main`.

| État (`lodyorch queue`) | Signification | Label |
|---|---|---|
| `work_in_progress` | session de code en cours ou à reprendre | `agent:in-progress` |
| `ci_pending` | PR ouverte, CI en cours (ou base d'une PR empilée pas encore verte) | `agent:in-progress` |
| `ci_failed` | CI rouge : correction relancée (1/2, 2/2), avant tout nouveau ticket | `agent:in-progress` |
| `ready_for_review` | CI verte constatée par le superviseur (et base validée) | `status:needs-test` |
| `blocked` | CI rouge après 2 corrections, PR fermée, CI sans conclusion après 3 h, question | `agent:blocked` |
| `needs_human` | `agent:human`, retrait d'`agent:auto`, verrou d'un tiers | — |

Une PR n'est jamais annoncée prête tant que sa CI n'a pas été constatée verte ; une PR empilée ne
l'est pas tant que la PR de sa base n'est pas verte elle aussi. Fusion ou fermeture de la PR : fin
du suivi. Les sessions font lint et tests ciblés avant de pousser ; la suite complète tourne en CI.

## Rapport permanent par tentative (ticket #123)

Après chaque tentative (livraison, blocage, échec, crash, interruption récupérée au redémarrage du
superviseur), **le superviseur** rédige un rapport standard. Ce n'est pas Claude qui le rédige : il existe donc même quand la
session a planté. Une session arrêtée pour laisser la main à la console ou par une limite du fournisseur
n'est pas une tentative et ne produit pas de rapport.

- **Contenu** : projet, date UTC, ticket et lien, session et run ; résumé en français ; reste à faire ;
  ce qui est déployé (jamais rien par lodyorch) ; branche, commits et PR (lus sur GitHub), état de la
  CI, tests déclarés ; fichiers modifiés ; risques, coût estimé, questions ; test humain numéroté
  (`human_tests` du JSON de fin) ; état final (terminé / à tester / bloqué / échec + cause, blocage,
  dépendances, prochaine action) ; identifiant du run. Le rapport détaillé écrit par la session est
  joint en annexe, seulement s'il se trouve sous `reports/` (aucun autre fichier du compte ne peut
  être joint). Les motifs de secrets sont masqués.
- **Livraison avec PR** : le rapport attend le verdict de la CI (suivie par le superviseur) pour
  l'inclure ; CI rouge → rapport « échec — correction relancée », puis un nouveau rapport pour la
  tentative de correction.
- **Archive** : `~/.local/state/lodyorch/reports/runs/YYYY-MM-DD_HHMM_lody_issue-N_run-ID.md`, écrit
  une fois et jamais réécrit. Un nouveau run du même ticket produit un nouveau fichier.
- **Lien permanent** : le rapport complet est publié en **commentaire du ticket**, marqué
  `<!-- lodyorch:rapport run=ID -->`. Le marqueur est cherché avant publication : jamais de doublon,
  même après un crash. Si GitHub est indisponible, le rapport reste archivé et la publication est
  retentée (délai croissant). Pour « lis le rapport #N » : dernier commentaire « Rapport lodyorch » du ticket.
- **Email** : sujet `[Lody Video Factory] #N — état — intitulé`, résumé et lien du commentaire dans
  le corps, Markdown en pièce jointe, une fois par run. Transport et destinataire : le fichier SMTP
  existant de PlugAdmin (comme digorch et jury-central). **Le superviseur et ses sessions ne le voient
  pas** (systemd) : les emails sont préparés dans la base d'état puis envoyés par
  `lodyorch-notify.service`. Cette unité, déclenchée par un timer toutes les 2 min, lit le fichier sans jamais le copier ni l'afficher. Elle n'a
  accès ni à Claude, ni à GitHub, ni au dépôt. En cas d'échec SMTP, de nouveaux essais sont faits, sans refaire le travail
  (30 au plus, délai croissant). Le message d'erreur ne contient jamais d'identifiant.

```
lodyorch reports                 derniers rapports : lien du commentaire, email envoyé ou en attente
lodyorch report N                rapport de la dernière session de N (rédigé s'il manque, publié s'il attend)
lodyorch notifications [--retry] emails envoyés / en attente ; --retry : nouvel essai immédiat
```

## Garde-fous

- **Compte de service `ubuntu`, jamais root.** L'authentification Claude Code existante est
  réutilisée telle quelle (aucune copie, aucun affichage) ; elle est vérifiée avant chaque session
  (`claude auth status`) : absente → aucune session, cause affichée par `lodyorch status`.
- **Isolation imposée par systemd** (héritée par les sessions) : `NoNewPrivileges` (sudo, et donc
  Docker et systemctl, inopérants), `/srv/moneyprinterturbo` (config.toml et sauvegardes, secrets,
  storage, data) **invisible**, autres projets et superviseurs en **lecture seule**, `/tmp` privé.
- **Crochet de garde** (`guard.py`, PreToolUse, actif même en `--dangerously-skip-permissions`) :
  refuse fusion/fermeture/labels, push forcé ou sur `main`, Docker, tmux, sudo, lecture
  d'identifiants, pilotage du superviseur, écriture hors worktree/rapports, motifs de secrets ;
  aucun connecteur MCP (`--strict-mcp-config`).
- **Journaux** : motifs de secrets (jetons Anthropic, OpenAI, GitHub, AWS, Slack, clés privées,
  `api_key=…`, `Bearer …`) masqués avant écriture des journaux de session et des événements.
- **Ressources** : l'hôte est partagé avec la production. Les sessions sont freinées au-delà de 1,4 Go de RAM
  et ne dépassent jamais 1,8 Go ni 1,5 CPU, avec des poids CPU et disque réduits (`lodyorch.service`).
- **Budget** : 50 sessions/jour (relevé par le propriétaire), pause à 80 % de la fenêtre Claude de 5 h et à 70 % de
  celle de 7 jours (quota **partagé** avec les autres superviseurs, dont les bases sont lues en
  lecture seule), session ≤ 2 h, coupure après 1 h sans sortie, 3 tentatives par ticket,
  disjoncteur après 4 sessions consécutives sans rapport. Réglages : `lodyorch config`.

Limite connue : le crochet est un filet contre les erreurs, pas un bac à sable ; l'isolation forte
vient de systemd. Une isolation complète (compte dédié sans accès au reste de `/home/ubuntu`)
demanderait une nouvelle connexion Claude et GitHub pour ce compte : décision du propriétaire.

## Installation (propriétaire de l'hôte)

```sh
cd /srv/moneyprinterturbo && git fetch origin && git checkout <branche ou main après fusion>
sudo deploy/lodyorch/install.sh --dry-run   # affiche les actions
sudo deploy/lodyorch/install.sh             # installe ; ne démarre RIEN
lodyorch check                              # tout doit être ✓
lodyorch labels                             # puis lodyorch labels --apply pour créer les labels manquants
```

L'installation ajoute les crochets à la console sans rien retirer ni remplacer. Ils sont pris en
compte au prochain démarrage de Claude dans la console (ou après validation dans `/hooks`).

```sh
sudo systemctl enable --now lodyorch.service   # la file démarre, sans fenêtre
sudo systemctl enable --now lodyorch-notify.timer  # emails des rapports
lodyorch console                               # rattache LA console ; la crée seulement si absente
```

## Exploitation

```
lodyorch status                 service, console, priorité humaine, session, ticket, phase, PR, erreurs
lodyorch console                rattacher la console tmux lodyorch (créée seulement si absente)
lodyorch pulse                  point d'activité en 3 lignes (fenêtre « activite »)
lodyorch check                  contrôles d'installation (lecture seule)
lodyorch labels [--apply]       labels du dépôt comparés au protocole (#117)
lodyorch reports | report N     rapports de tentative (#123)
lodyorch queue | log [N] | sessions
lodyorch pause | resume         ne plus démarrer de session / reprendre (resume réarme le disjoncteur)
lodyorch stop [--now]           arrêter après la session en cours, ou tout de suite
lodyorch skip N [raison] | add N [--prio 10] | set N champ valeur
lodyorch config [set NOM VALEUR | unset NOM]
lodyorch reload                 appliquer le code et lodyorch.env mis à jour, entre deux sessions
journalctl -u lodyorch -f
```

Mise à jour : `sudo deploy/lodyorch/install.sh` (sauvegarde dans `~/backups/lodyorch/`), puis
`lodyorch reload` (relit aussi `lodyorch.env` ; une variable retirée garde sa valeur jusqu'au
prochain redémarrage). Une unité systemd modifiée demande `sudo systemctl daemon-reload` puis
`sudo systemctl restart lodyorch`, entre deux sessions. Retour arrière : réinstaller les fichiers de la sauvegarde dans `/opt/lodyorch/`,
`sudo systemctl daemon-reload`, `lodyorch reload`.

Désinstallation : `lodyorch stop`, `sudo systemctl disable --now lodyorch.service lodyorch-notify.timer`, puis
supprimer `/etc/systemd/system/lodyorch*.service`, `/etc/systemd/system/lodyorch-notify.timer`, `/opt/lodyorch`, `/usr/local/bin/lodyorch` et les
entrées `lodyorch` de `/srv/moneyprinterturbo/.claude/settings.local.json`. L'état (`~/.local/state/lodyorch`) et le clone peuvent être conservés.

## Tests

`test/lodyorch/` (lancés par la CI avec le reste de `test/`) : protocole de labels (référentiel,
labels invalides, prise en charge unique, livraison sans PR), priorités, éligibilité, blocages,
dépendances, verrou visible, PR exclues, verrou d'exclusion, reprise après crash sans doublon,
temporisation et abandon, disjoncteur, refus sans authentification ni garde, masquage des
journaux, `status`/`pulse`/`phase`/`check`, crochet de garde, unité systemd, console unique
(crochets fusionnés sans écrasement, `lodyorch console` sans renommage, priorité humaine, rangement
puis reprise de la session de file), rapports de tentative (faux SMTP, panne SMTP, panne GitHub,
idempotence, masquage) et installation à blanc. Faux `gh` et faux `claude` : aucun réseau, aucun appel au modèle.
