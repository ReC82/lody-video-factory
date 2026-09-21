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

## Lecture de `config.toml` par Lody

Lody n'y lit que des booléens (« cette clé est-elle renseignée ? ») et le `llm_provider` du moteur, pour
prévenir *avant* de lancer. Le conteneur tourne en uid 10001 : si `config.toml` n'est pas lisible par cet
utilisateur (ex. `0600` d'un autre propriétaire), Lody affiche **« Non vérifiée »** et un avertissement
non bloquant — il n'affirme jamais qu'une clé manque. Le moteur reste juge : ses vérifications préalables
échouent avant tout appel payant. Pour activer les vérifications, donner à cet uid un accès en lecture
seule (à décider par l'administrateur, le fichier contient des secrets) :
`sudo setfacl -m u:10001:r config.toml` (annulable : `sudo setfacl -x u:10001 config.toml`).

## Limitations connues

- Une V2 **régénère tous les médias** (voix, images, musique) : aucune réutilisation.
- Pas d'annulation d'une génération en cours (le moteur n'en offre pas).
- Le fournisseur de texte est celui du moteur (`llm_provider` de son `config.toml`). Si le projet
  demande OpenAI et que le moteur est réglé sur un autre fournisseur, Lody bloque avec un message
  clair : régler `llm_provider = "openai"` côté moteur (et le redémarrer) ou fournir son script.
- Visuels « fichiers locaux » non pris en charge par ce connecteur.
- Le moteur ne renvoie pas le coût facturé.
