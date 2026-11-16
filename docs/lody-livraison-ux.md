# Livraison UX automatique : de la PR verte à la page réellement mise à jour (#221)

> **Règle** : « terminé » ne veut plus rien dire seul. Un changement est annoncé **« EN LIGNE — prêt à
> tester »** seulement quand la version est servie et vérifiée (release validée, révision du conteneur,
> santé HTTP du site). Une PR ouverte, une CI verte ou une fusion ne sont **pas** une mise en ligne.
>
> **Statut au 13/11/2026** : outils livrés et testés en simulation (CI, sans Docker ni réseau), **rien
> d'installé ni d'activé sur l'hôte**. La session qui les a écrits n'a ni Docker, ni root, ni accès au
> clone de production : la mise en ligne de #195 demande **une intervention du propriétaire** (§1).

## 1. Cas immédiat : #195 (PR #216, `2dba0396fc`) n'est pas en ligne

### Constat (13/11/2026, depuis la session, sans privilège)

| Élément | Constat | Conséquence |
|---|---|---|
| `main` | `2dba039` = #195 (liste compacte), après `73227ee` (#209) | le code est fusionné |
| Site `video.lodylands.com` | `/_stcore/health` → `200 ok`, page `/` → `200` | le site répond |
| Révision servie | **non observable de l'extérieur** : aucune route de version publique (toute URL renvoie la page Streamlit) ; la révision n'est lisible que sur l'hôte (`docker container inspect`, label `org.opencontainers.image.revision`) | impossible de prouver depuis la session ce qui est servi |
| Déploiement | aucune automatisation : `main` ne se déploie **pas** seul ; `scripts/lody-deploy.sh` (#209) livré mais **jamais exécuté** sur l'hôte (aucun journal de release connu) | #195 n'est en ligne que si le propriétaire l'a livré à la main |
| Droits de la session | ni `sudo`, ni Docker, ni `systemctl`, ni lecture de `/srv/moneyprinterturbo` (crochet de garde) | **blocage** : la session ne peut ni déployer, ni vérifier la révision servie |

### Intervention unique demandée au propriétaire (sur l'hôte AWS)

La livraison de `main` emporte **tout** ce qui n'a pas encore été livré depuis la version servie, pas
seulement #195 : potentiellement #189 (migration **additive** : table `production_compose_drafts`), #191,
#202 et #209. C'est pourquoi cette première livraison est **manuelle** (le livreur automatique la refuse :
plage non `ux-safe`, et aucune release #209 validée sur l'hôte).

```bash
cd /srv/moneyprinterturbo
git status --short                       # doit être vide (fichiers suivis) ; sinon s'arrêter
docker container inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }} {{.Config.Image}}' lody-video-factory-ui
git fetch origin && git log --oneline HEAD..origin/main   # ce qui va partir en ligne
git merge --ff-only origin/main          # clone sur 2dba039 (ou plus récent)
# 2D : vérifier d'abord l'équivalence de l'override (docs/lody-retention.md, « Migration depuis l'override racine »)
sudo -n -E ./scripts/lody-deploy.sh --2d --dry-run      # simulation : rien ne change
sudo -n -E ./scripts/lody-deploy.sh --2d                # verrou, aucun rendu actif, sauvegarde SQLite, build tagué SHA, santé
./scripts/lody-deploy.sh --list                          # « * <sha12> validée » attendu
docker container inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' lody-video-factory-ui   # = SHA de main
```

- **Refus « activité en cours »** (code 3) : un rendu tourne ; rien n'a changé. Relancer plus tard — ne
  jamais interrompre un rendu.
- **Livraison non saine** (code 1) : le script affiche la commande de retour arrière. Sans release
  validée antérieure (première livraison #209), le retour arrière se fait avec l'image notée à la première
  commande `inspect` : `LODY_IMAGE_TAG=<ancien tag> docker compose -f docker-compose.lody.yml -f
  docker-compose.lody-2d.override.yml up -d --no-build`, puis restauration de la sauvegarde
  `.lody-ops/backups/` si la migration de #189 posait problème (elle est ignorée par l'ancienne version).

**Vérification UX (mobile 390 px)**, après connexion : liste de projets compacte (une ligne par projet,
plus de grosses tuiles), recherche par nom, tri (récent / nom), dépliage d'un projet, ouverture puis retour
à la liste. Ensuite seulement : « EN LIGNE — prêt à tester ». Cette première livraison crée aussi la
**release validée** dont le livreur automatique (§3) a besoin.

## 2. Classification `ux-safe` — par le code, jamais par un label

`deploy/lodyorch/ux_safe.py` (module pur, testé) classe un ensemble de fichiers modifiés (diff réel) :

- **Liste blanche de fichiers** : `styles.css`, `theme.py`, `nav.py`, `components.py`, `project_list.py`,
  les vues `view_home`, `view_project`, `view_characters`, `view_locations`, `view_form`, `view_kit`,
  `view_tracking`, leurs tests `test/lody/test_*.py` et `docs/*.md`. Exclues même pour du CSS : vues de
  production, rendu 2D, réédition, V2, estimation de coût, réglages, réglages système, `app.py`.
- **Contenu ajouté** des fichiers Python d'interface : imports limités à Streamlit, une poignée de modules
  standard et aux modules d'interface/lecture de Lody (`lody.db`, `lody.generation`, `lody.animation`,
  `voice_*`… refusés) ; refus de tout motif SQL/base, réseau, sous-processus, variables d'environnement,
  écriture de fichier, suppression (`delete_…(`, `remove_…(`), code dynamique, secrets/auth, traitement
  payant (`generation`, `animation`, `voice_…`, `elevenlabs`, `openai`, `launch_…`, `enqueue…`), HTML actif
  (`<script>`, `onclick=`, `javascript:`, composants personnalisés).
- **Aucune suppression ni renommage, aucun binaire**, au moins un fichier d'interface, ≤ 15 fichiers,
  ≤ 800 lignes modifiées.

Faux positifs assumés : ils renvoient en revue humaine. Vérifié sur l'historique : #195 (`2dba039`) est
`ux-safe` ; #191 (voix), #189 (migration), #202 (lieux/génération), #209 (infra) ne le sont pas.

```bash
python3 deploy/lodyorch/ux_safe.py diff 2dba039~1 2dba039          # ux-safe : 5 fichier(s), 466 ligne(s)
python3 deploy/lodyorch/ux_safe.py diff <servi> origin/main --json  # matrice complète (lecture git seule)
```

## 3. Chaîne automatique (une fois activée)

| Étape | Qui | Garde-fous | Mail |
|---|---|---|---|
| PR ouverte | session Claude | aucune fusion, aucun déploiement | « Travail terminé — PR prête » (non fusionnée, pas en ligne) |
| Fusion | superviseur (mode `on`, puis broker #207) | politique #200 complète : CI verte sur le SHA exact, SHA attendu, vetos `merge:manual`/`agent:blocked`/`do-not-merge`, revue « changements demandés », base à jour… ; **autorisation** = `merge:auto` du propriétaire **ou** classement `ux-safe` par le code (`LQ_UX_SAFE_AUTO=1`) pour une PR ouverte **après** l'activation | « CI verte — fusionné : … (pas encore en ligne) » |
| Mise en ligne | **livreur de l'hôte** `scripts/lody_ux_deliver.py` (timer `lody-ux-deliver`, 5 min) — indépendant du superviseur et des sessions | interrupteur d'arrêt ; verrou ; release #209 validée existante ; `origin/main` descend de la version servie ; clone propre ; **plage « servie → main » entièrement `ux-safe`** (une seule PR non sûre fusionnée entre-temps → tout part en livraison manuelle) ; CI verte sur le SHA exact de `main` ; aucun rendu actif (sinon **report**, jamais d'interruption) | « Déploiement en cours » |
| Vérification | livreur | release validée = SHA cible, révision du conteneur = SHA cible, `/_stcore/health` = `ok` et `/` = 200 (10 essais) | « EN LIGNE — prêt à tester » + commentaire dans chaque ticket de la plage, avec preuve |
| Échec | livreur | `lody-deploy.sh --rollback <release précédente>` automatique, santé revérifiée | « Déploiement échoué / retour arrière » (ou intervention manuelle si le retour arrière échoue) |
| Refus | livreur | plage non `ux-safe`, CI rouge, clone modifié | « Mise en ligne manuelle requise » |

Le livreur ne retente **jamais** une cible déjà traitée (en ligne, refusée, échouée, revenue en arrière) :
`--retry` explicite. Il ne fusionne rien, ne lit aucun secret, n'appelle aucune API d'écriture (CI lue par
l'API publique de GitHub). Les sessions Claude ne peuvent ni l'exécuter ni l'installer (pas de Docker, pas
de root). Journal en ajout seul : `.lody-ops/ux-deliveries.jsonl` (`--status` pour le lire).

Plusieurs PR UX (#212, #213, #214) fusionnées entre deux passages partent **ensemble** si toutes sont
`ux-safe` (une seule livraison, un mail citant tous les tickets) ; une seule fusion par tour côté
superviseur, une seule livraison à la fois (verrou du livreur + verrou de `lody-deploy.sh`).

## 4. Mise en service (propriétaire, dans l'ordre)

Prérequis : §1 fait (une release #209 validée existe et `main` est servi).

1. **Simulation** sur l'hôte : `cd /srv/moneyprinterturbo && sudo -n -E python3 scripts/lody_ux_deliver.py`
   → `up-to-date` (rien à livrer) ou `plan` avec le verdict. Rien n'est écrit.
2. **Livreur** : `sudo install -m 644 deploy/systemd/lody-ux-deliver.service deploy/systemd/lody-ux-deliver.timer
   /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now lody-ux-deliver.timer`.
   Vérifier : `systemctl list-timers lody-ux-deliver.timer`, `python3 scripts/lody_ux_deliver.py --status`.
   Pour un premier temps en simulation, retirer `--apply` de l'unité.
3. **Mails d'état** : dans l'env installé du superviseur (`/opt/lodyorch/lodyorch.env`),
   `LQ_UX_DELIVERY_JOURNAL=/srv/moneyprinterturbo/.lody-ops/ux-deliveries.jsonl` (le livreur le rend lisible,
   `0644` ; le répertoire doit être traversable par le compte du superviseur, sinon pointer vers une copie),
   réinstaller (`deploy/lodyorch/install.sh`) et `lodyorch reload`.
4. **Fusion sans `merge:auto` des PR `ux-safe`** : `LQ_UX_SAFE_AUTO=1` dans le même env. En `dry-run`, le
   superviseur commente « Simulation : serait fusionnée » sur les tickets UX — à observer sur une ou deux PR.
   La fusion réelle suit le mode `automerge on`, lui-même en attente de l'isolation des permissions #207
   ([lody-fusion-auto-mise-en-service.md](lody-fusion-auto-mise-en-service.md) §2) : **ne pas** contourner
   cette attente avec un jeton supplémentaire. En attendant, une fusion manuelle par le propriétaire d'une
   PR `ux-safe` suffit : le livreur la met en ligne seul.

**Arrêt immédiat** : `sudo touch /srv/moneyprinterturbo/.lody-ops/ux-deliver.off` (livreur) ;
`LQ_UX_SAFE_AUTO=0` + `lodyorch reload` ou `lodyorch automerge off` (fusion). **Retour arrière** d'une
mise en ligne : `./scripts/lody-deploy.sh --list` puis `sudo -n -E ./scripts/lody-deploy.sh --rollback <tag>`.

## 5. Hors périmètre (validation manuelle maintenue)

Voix et production réelle, traitements payants, migrations de base (même additives), nouvelles clés,
infrastructure, Docker, CI, sécurité/authentification, suppressions, et toute évolution non réversible :
jamais `ux-safe`, quelle que soit la taille du changement.

## 6. Limites connues

- La santé HTTP prouve que le site répond, pas que l'écran attendu est correct : la vérification visuelle
  (mobile 390 px) reste humaine, d'où « prêt à **tester** ».
- Le site n'expose pas sa révision publiquement ; la preuve de version vient de l'hôte (release + label de
  l'image du conteneur), consignée dans le journal et le mail.
- Après un retour arrière, le clone de production reste sur la cible refusée : la livraison suivante est
  refusée (« clone ≠ version servie ») jusqu'à intervention — volontaire, un échec mérite un regard humain.
- Identité GitHub unique (#205/#207) : tant que l'isolation n'est pas en place, la fusion réelle reste en
  `dry-run` ; la mise en ligne, elle, ne dépend pas de GitHub en écriture.

## 7. Tests

`test/lodyorch/test_ux_safe.py` (classification, cas éligibles/inéligibles, plage git réelle, voie
d'autorisation de la politique, non-rétroactivité, vetos, CI rouge), `test/lodyorch/test_ux_delivery.py`
(fusion `ux-safe` en `dry-run`/`on`, relais du journal en mails et commentaires, idempotence, libellés),
`test/ops/test_ux_deliver.py` (vrai dépôt git temporaire, faux `lody-deploy.sh`/Docker/site/GitHub :
simulation, en ligne avec preuve, refus non `ux-safe`, CI rouge/en cours, rendu actif, site KO → retour
arrière, retour arrière KO, absence de release, interrupteur, verrou, clone modifié). Aucun réseau, aucun
Docker, aucun fournisseur payant.
