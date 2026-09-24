# Catalogue recherchable des voix ElevenLabs (ticket #55)

Permet de choisir une voix ElevenLabs depuis l'interface (paramètres du projet, fiche d'un personnage)
sans avoir à retrouver et copier son `voice_id` à la main. Entièrement optionnel : la saisie manuelle du
`voice_id` reste toujours possible, et c'est le seul mode disponible tant que le catalogue n'a pas été
généré côté hôte (voir plus bas — c'est le cas juste après le déploiement de cette fonctionnalité, avant
la première exécution du script).

## API ElevenLabs utilisée (vérifiée, v2, janvier 2026)

- `GET https://api.elevenlabs.io/v2/voices`, authentification par l'en-tête `xi-api-key` (jamais un
  paramètre d'URL).
- Pagination par curseur : `page_size` (max 100) + `next_page_token` ; réponse `{voices, has_more,
  next_page_token, total_count}`.
- Chaque voix porte au moins `voice_id`/`name`, et selon les comptes `category`, `labels` (langue, accent,
  genre, âge, cas d'usage…), `preview_url` (URL **publique**, lisible sans la clé API).
- **401** : clé absente ou invalide. **403** : documenté pour l'IP allowlisting, mais peut aussi couvrir un
  compte sans droit sur certaines catégories de voix — même message générique dans les deux cas (pas de
  distinction fiable côté client). **429** : débit dépassé ; l'en-tête `Retry-After` est respecté quand
  présent, sinon message générique (aucune limite de débit précise n'est publiée pour cet endpoint). **5xx** :
  erreur serveur ElevenLabs, traitée comme temporairement indisponible.

## Architecture : le conteneur Lody ne voit JAMAIS la clé

`config.toml` **n'est pas monté** dans `lody-video-factory-ui`, et ne doit pas l'être pour cette
fonctionnalité — `docker-compose.lody.yml` n'a reçu aucune modification. `lody.generation.elevenlabs_voices`
n'a côté conteneur qu'un seul chemin, sans alternative : lire un **rapport assaini** déjà déposé dans
`./engine-report` (même dossier, déjà monté en lecture seule, qui porte `engine-capabilities.json` — aucun
changement de montage nécessaire). Rien dans le code de `resolve_catalog()` n'ouvre `config.toml` ni ne
référence une clé (vérifié par un test statique qui inspecte le code source de la fonction).

Toute la partie qui touche réellement la clé et appelle ElevenLabs tourne **côté hôte**, jamais dans un
conteneur :

```
lody.generation.elevenlabs_voices.build_report(config_path)
```

lit `config.toml` (lecture seule, jamais réécrit), appelle `GET /v2/voices` avec la clé (gardée en variable
locale le temps de cet appel, jamais journalisée), puis renvoie un dict ne contenant que des métadonnées
publiques (`voice_id`, `name`, `category`, `labels`, `preview_url` — validée HTTPS uniquement, toute autre
URL est vidée).

## Commande de génération / régénération du catalogue

```
./scripts/lody-elevenlabs-voices-report.sh
```

- **Fichier produit** : `engine-report/elevenlabs-voices.json` (remplaçable par `LODY_VOICE_REPORT`).
- **Permissions** : `0o644` — lecture pour tout le monde (dont le conteneur Lody, uid différent de celui de
  l'hôte, sans groupe partagé ici — même schéma que `engine-capabilities.json`), écriture réservée au
  propriétaire (l'hôte). Écriture atomique (fichier temporaire + `os.replace`) : jamais de lecture d'un
  fichier à moitié écrit.
- **Format** : `{"version": 1, "generated_at": "<ISO 8601>", "voices": [{"voice_id", "name", "category",
  "labels", "preview_url"}, …]}`.
- **Comportement selon la réponse ElevenLabs** :
  - 200 avec des voix → rapport écrit, catalogue disponible dans l'interface ;
  - 200 avec 0 voix → rapport écrit quand même (`voices: []`) ; l'interface affiche « aucune voix trouvée »,
    distinct d'un rapport absent ;
  - 401/403 → le script échoue (code de sortie 1), message clair sans détail de la réponse, **aucun fichier
    écrit** ; le catalogue précédent (s'il existe) reste en place jusqu'à la prochaine exécution réussie ;
  - 429/5xx/réseau → idem (échec propre, rien n'est écrasé).
- **Fait un appel réel à ElevenLabs** (aucune génération, aucun coût) : à exécuter quand c'est
  effectivement souhaité, pas en boucle.

À lancer une première fois **lors du déploiement de cette fonctionnalité, après avoir vérifié que la clé
ElevenLabs configurée est valide** (ex. depuis « Paramètres système »). Avant cette première exécution,
l'interface affiche clairement qu'aucun catalogue n'est disponible et propose la saisie manuelle — ce n'est
pas une régression, c'est le comportement attendu tant que le script n'a pas tourné.

## Actualisation automatique lors d'une rotation de clé

`apply_secrets.py` (déclenché par `lody-secrets-reload.path`/`.service`, voir `docs/lody-secrets.md`)
régénère déjà `engine-capabilities.json` après toute rotation de clé réussie. Il fait maintenant de même
pour le catalogue de voix, **uniquement quand `elevenlabs.api_key` fait partie du lot appliqué** (jamais à
chaque rotation d'une autre clé) :

- clé vérifiée valide → catalogue régénéré avec la nouvelle clé (best-effort : une panne ElevenLabs
  transitoire à ce moment précis n'empêche jamais l'application de la clé, qui reste `ok` — le catalogue sera
  simplement régénéré au prochain cycle) ;
- clé refusée/expirée → retour arrière automatique de `config.toml` (comportement déjà existant) ; le
  catalogue est régénéré à partir de la configuration restaurée (l'ancienne clé, qui fonctionnait déjà).

`scripts/lody-apply-secrets.sh` passe déjà `--voice-report` avec la même valeur par défaut que l'interface
(`settings.voice_catalog_report_path()`) : rien à changer opérationnellement, ce mécanisme existant est
réutilisé tel quel plutôt que dupliqué.

## Actualisation du catalogue affiché

Depuis #62, le sélecteur vit **à l'intérieur** de la section « Voix » des formulaires (projet et
personnage) — un `st.button()` n'étant pas autorisé dans un `st.form()` Streamlit, il n'y a plus de bouton
dédié « Actualiser ». Ce n'est pas une perte de fonctionnalité : `resolve_catalog()` n'utilise aucun cache
Python/Streamlit et relit déjà ce petit fichier JSON à chaque rendu de la page — toute interaction avec le
formulaire (recherche, filtre, sélection) montre donc déjà le contenu le plus récent du fichier. Comme
avant, le catalogue **ne contacte jamais ElevenLabs** au rendu : un vrai nouveau contenu exige de relancer
le script hôte, manuellement ou via une rotation de clé (voir ci-dessus).

## Cas gérés par l'interface

| État du rapport | Comportement |
|---|---|
| Absent (jamais généré) | Message clair, saisie manuelle disponible |
| Invalide (JSON cassé, version inconnue, `voices` pas une liste) | Message clair, saisie manuelle disponible |
| Valide, 0 voix | Message « aucune voix trouvée », distinct d'un rapport absent |
| Valide, périmé (> 30 jours) | Catalogue affiché quand même, avertissement + rappel de la commande |
| Valide, récent | Catalogue et recherche disponibles, date de génération affichée |

## Sécurité

- La clé n'est jamais envoyée au navigateur : `VoiceInfo`/`CatalogResult` ne peuvent structurellement pas la
  porter (mêmes principes que `EngineFacts`), et le code côté conteneur n'ouvre jamais `config.toml`.
- Aucun `logger`/`print` de la clé, des en-têtes de requête ou d'une réponse brute dans ce module.
- L'aperçu audio (`preview_url`) est une URL **publique** ElevenLabs, validée HTTPS uniquement : le
  navigateur la lit directement (`st.audio(preview_url)`), le serveur Lody ne la relaie jamais et la clé
  n'intervient à aucun moment.
- Le rapport écrit par le script hôte ne contient que des champs publics de voix, revalidés au moment même
  de l'écriture (`VoiceInfo.to_dict`), pas seulement à la lecture de la réponse ElevenLabs.
