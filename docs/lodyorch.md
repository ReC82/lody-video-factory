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
| Service | `lodyorch.service` (file, sans fenêtre) |
| Console | session tmux `lodyorch` du propriétaire ; crochets dans `/srv/moneyprinterturbo/.claude/settings.local.json` (exclu de Git) |
| État, verrou, journaux, rapports | `~/.local/state/lodyorch/` (0700 : `state.db`, `supervisor.lock`, `sessions/*.jsonl`, `reports/`) |
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
- **Budget** : 8 sessions/jour au démarrage, pause à 80 % de la fenêtre Claude de 5 h et à 70 % de
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
```

L'installation ajoute les crochets à la console sans rien retirer ni remplacer. Ils sont pris en
compte au prochain démarrage de Claude dans la console (ou après validation dans `/hooks`).

```sh
sudo systemctl enable --now lodyorch.service   # la file démarre, sans fenêtre
lodyorch console                               # rattache LA console ; la crée seulement si absente
```

## Exploitation

```
lodyorch status                 service, console, priorité humaine, session, ticket, phase, PR, erreurs
lodyorch console                rattacher la console tmux lodyorch (créée seulement si absente)
lodyorch pulse                  point d'activité en 3 lignes (fenêtre « activite »)
lodyorch check                  contrôles d'installation (lecture seule)
lodyorch queue | log [N] | sessions
lodyorch pause | resume         ne plus démarrer de session / reprendre (resume réarme le disjoncteur)
lodyorch stop [--now]           arrêter après la session en cours, ou tout de suite
lodyorch skip N [raison] | add N [--prio 10] | set N champ valeur
lodyorch config [set NOM VALEUR | unset NOM]
lodyorch reload                 appliquer un code mis à jour entre deux sessions
journalctl -u lodyorch -f
```

Mise à jour : `sudo deploy/lodyorch/install.sh` (sauvegarde dans `~/backups/lodyorch/`), puis
`lodyorch reload`. Retour arrière : réinstaller les fichiers de la sauvegarde dans `/opt/lodyorch/`,
`sudo systemctl daemon-reload`, `lodyorch reload`.

Désinstallation : `lodyorch stop`, `sudo systemctl disable --now lodyorch.service`, puis
supprimer `/etc/systemd/system/lodyorch.service`, `/opt/lodyorch`, `/usr/local/bin/lodyorch` et les
entrées `lodyorch` de `/srv/moneyprinterturbo/.claude/settings.local.json`. L'état (`~/.local/state/lodyorch`) et le clone peuvent être conservés.

## Tests

`test/lodyorch/` (lancés par la CI avec le reste de `test/`) : priorités, éligibilité, blocages,
dépendances, verrou visible, PR exclues, verrou d'exclusion, reprise après crash sans doublon,
temporisation et abandon, disjoncteur, refus sans authentification ni garde, masquage des
journaux, `status`/`pulse`/`phase`/`check`, crochet de garde, unité systemd, console unique
(crochets fusionnés sans écrasement, `lodyorch console` sans renommage, priorité humaine, rangement
puis reprise de la session de file) et installation à blanc. Faux `gh` et faux `claude` : aucun réseau, aucun appel au modèle.
