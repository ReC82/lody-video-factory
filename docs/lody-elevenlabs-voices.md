# Catalogue recherchable des voix ElevenLabs (ticket #55)

Permet de choisir une voix ElevenLabs depuis l'interface (paramètres du projet, fiche d'un personnage)
sans avoir à retrouver et copier son `voice_id` à la main. Entièrement optionnel : la saisie manuelle du
`voice_id` reste toujours possible et est le seul mode disponible tant que le catalogue n'est pas activé
côté hôte (voir ci-dessous).

## API ElevenLabs utilisée (vérifiée, v2, janvier 2026)

- `GET https://api.elevenlabs.io/v2/voices`, authentification par l'en-tête `xi-api-key` (jamais un
  paramètre d'URL).
- Pagination par curseur : `page_size` (max 100) + `next_page_token` ; réponse `{voices, has_more,
  next_page_token, total_count}`.
- Chaque voix porte au moins `voice_id`/`name`, et selon les comptes `category`, `labels` (langue, accent,
  genre, âge, cas d'usage…), `preview_url` (URL **publique**, lisible sans la clé API).
- Erreurs : 401/403 (clé absente, invalide, ou compte/IP sans droit), 429 (débit), 5xx. Aucune limite de
  débit précise n'est publiée pour cet endpoint ; l'en-tête `Retry-After` est respecté s'il est présent.

## Pourquoi un mécanisme en deux chemins

Le conteneur Lody ne peut aujourd'hui pas lire `config.toml` (0600, propriétaire différent — voir
`docs/lody-secrets.md`), et ce ticket ne touche pas `moneyprinterturbo-api` (qui n'expose pas d'endpoint de
liste des voix). `lody.generation.elevenlabs_voices.resolve_catalog()` gère donc, exactement comme
`engine_facts.resolve()` pour le rapport de capacités :

1. **Lecture directe** de `config.toml`, si le conteneur en a le droit (ex. après un `setfacl` documenté
   dans `docs/lody-generation.md`) : la clé n'est alors utilisée que le temps de l'appel HTTP sortant, dans
   une variable locale, jamais journalisée ni renvoyée à l'interface.
2. **Rapport assaini**, sinon (cas actuel du déploiement) : `./engine-report/elevenlabs-voices.json`
   (voice_id/nom/catégorie/labels/preview_url — **jamais** la clé), produit côté hôte par :

   ```
   ./scripts/lody-elevenlabs-voices-report.sh
   ```

   (même dossier déjà monté en lecture seule que `engine-capabilities.json` : aucun changement de
   `docker-compose.lody.yml` nécessaire). Ce script fait un **appel réel** à ElevenLabs (aucune génération,
   aucun coût) : à lancer côté hôte quand on veut rafraîchir le catalogue proposé dans l'interface, comme
   `lody-engine-report.sh` après un changement de `config.toml`.

Si ni l'un ni l'autre n'est disponible (clé absente, script jamais lancé), l'interface affiche un message
clair et retombe entièrement sur la saisie manuelle — rien n'est bloqué.

## Cache et actualisation

Le catalogue récupéré en direct (chemin 1) est mis en cache en mémoire 15 minutes
(`elevenlabs_voices.CACHE_TTL_SECONDS`) ; le bouton **Actualiser les voix** de l'interface force un nouvel
appel. En mode rapport (chemin 2, cas actuel), « Actualiser » relit simplement le fichier depuis le disque —
un rafraîchissement réel nécessite de relancer le script côté hôte.

## Sécurité

- La clé n'est jamais envoyée au navigateur : `VoiceInfo`/`CatalogResult` ne peuvent structurellement pas la
  porter (mêmes principes que `EngineFacts`).
- Aucun `logger`/`print` de la clé, des en-têtes de requête ou d'une réponse brute dans ce module.
- L'aperçu audio (`preview_url`) est une URL **publique** ElevenLabs : le navigateur la lit directement
  (`st.audio(preview_url)`), le serveur Lody ne la relaie jamais et la clé n'intervient à aucun moment.
- Le rapport écrit par le script hôte ne contient que des champs publics de voix (voir `VoiceInfo.to_dict`).
