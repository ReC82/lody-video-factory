# Contrat du moteur de génération historique (connecteur hérité)

> **Statut : connecteur hérité.** Lody Video Factory s'appuie, pour sa première génération de
> bout en bout, sur l'API du socle historique **MoneyPrinterTurbo** (MIT, crédité dans
> l'interface). Ce contrat n'est *ni* l'identité *ni* l'architecture définitive de la
> plateforme : il n'est connu que du module `webui/lody/generation/mpt_connector.py`. Les vues
> et le service d'orchestration ne parlent qu'à l'interface `VideoGenerationProvider`.
>
> Tout ce qui suit a été relevé **dans le code** (`app/controllers/v1/video.py`,
> `app/models/schema.py`, `app/services/task.py`, `state.py`, `material.py`, `llm.py`), pas supposé.

## Déploiement observé

| | |
|---|---|
| Service | conteneur `moneyprinterturbo-api` (`python3 main.py`), publié sur `127.0.0.1:8080` |
| Réseau | `moneyprinterturbo_default` ; l'UI Lody le rejoint pour l'appeler par nom (`http://moneyprinterturbo-api:8080`) |
| Configuration | `config.toml` de l'hôte monté **dans le moteur** ; c'est le moteur qui lit ses clés. Lody ne les lit ni ne les transmet jamais |
| Stockage | `./storage` de l'hôte ; sorties dans `storage/tasks/<task_id>/` (Lody le monte en lecture seule) |
| Authentification | `x-api-key` uniquement si `app.api_key` est non vide (vide aujourd'hui). Lody sait l'envoyer via `LODY_ENGINE_API_KEY` |

## Endpoints (préfixe `/api/v1`, sauf `/ping`)

| Usage | Requête | Réponse |
|---|---|---|
| Santé | `GET /ping` (sans préfixe) | `"pong"` |
| Créer une vidéo complète | `POST /api/v1/videos` (JSON `TaskVideoRequest`) | `200 {"status":200,"data":{"task_id","request_id","params"}}` — la tâche est mise en file, la réponse est immédiate |
| Script seul | `POST /api/v1/scripts` | `{"status":200,"data":{"video_script": "..."}}` (appel texte synchrone) |
| Mots-clés seuls | `POST /api/v1/terms` | `{"data":{"video_terms":[...]}}` |
| Consulter une tâche | `GET /api/v1/tasks/{task_id}` | `{"status":200,"data":{…}}` ; `404 {"status":404,"message":"…: task not found"}` si inconnue |
| Lister les tâches | `GET /api/v1/tasks?page=&page_size=` | `{"data":{"tasks":[…],"total","page","page_size"}}` |
| Supprimer | `DELETE /api/v1/tasks/{task_id}` | `409` si la tâche tourne encore |
| Fichiers finaux | `GET /tasks/<task_id>/final-1.mp4` (statique), `/api/v1/stream/…`, `/api/v1/download/…` | vidéo, chemins confinés au dossier des tâches |

Enveloppe : `{"status": <int>, "message"?: str, "data"?: …}` ; `data` est **omis** quand il est vide.
Erreurs HTTP : `400` (validation, `message: "field required"`), `401` (clé), `404`, `409`,
`429` (file pleine, `max_queued_tasks`), `403/404` pour un chemin refusé.

### Aucune annulation

Il n'existe **aucun endpoint d'annulation** : `DELETE` refuse les tâches occupées (`409`). Le statut
interne `ANNULEE` est donc réservé au schéma mais **non exposé** (`supports_cancel = False`).

## Statuts et progression

`state` (dans `app/models/const.py`) : `4` en cours (`TASK_STATE_PROCESSING`), `1` terminée
(`TASK_STATE_COMPLETE`), `-1` en échec (`TASK_STATE_FAILED`). Il n'y a **pas** d'état « en file » :
une tâche créée est `state=4, progress=0` jusqu'à ce qu'un worker la prenne (`progress=5`).

`progress` est un entier réel mais grossier, jalons du pipeline (`_run_pipeline`) :

| progress | Étape franchie |
|---|---|
| 0 | créée, en attente d'un worker |
| 5 | vérifications préalables (clés, ffmpeg) |
| 10 | script prêt |
| 20 | mots-clés / prompts prêts |
| 30 | voix générée |
| 40 | sous-titres prêts |
| 50 → 100 | images/séquences puis montage (progression par clip) |
| 100 | terminée |

Échec : `state=-1`, `error` (texte), `failed_stage` ∈ `preflight | script | terms | audio | materials | video | pipeline`,
`progress` conservé.

## Réponse d'une tâche terminée

`videos` et `combined_videos` : liste d'URI `"/tasks/<task_id>/final-1.mp4"` (préfixées par
`app.endpoint` s'il est défini). Autres champs utiles : `script`, `terms`, `audio_duration` (secondes),
`audio_file`, `subtitle_path`, `materials` (liste de chemins de clips), `warnings`
(ex. `elevenlabs_bgm_failed` : la musique a échoué mais la vidéo existe).
Fichiers écrits dans `storage/tasks/<task_id>/` : `final-N.mp4`, `combined-N.mp4`, `audio.mp3`,
`subtitle.srt`, `script.json` (script, mots-clés, paramètres), images et clips.

**L'état des tâches est en mémoire** (`MemoryState`) sauf si Redis est activé (`app.enable_redis`,
désactivé ici) : un redémarrage du moteur fait répondre `404` pour des tâches pourtant présentes sur
disque. Lody s'appuie donc sur son propre enregistrement et, dans ce cas, cherche la vidéo sur disque
avant de conclure.

## Paramètres de la requête (`TaskVideoRequest`)

| Domaine | Champs |
|---|---|
| Sujet / texte | `video_subject` (requis), `video_script` (si non vide : **aucun appel texte pour le script**), `video_language`, `paragraph_number` (1–10), `video_script_prompt` (≤ 2000), `custom_system_prompt` (≤ 8000) |
| Prompts visuels | `video_terms` (liste ou chaîne séparée par virgules). **Si fournis, le moteur ne les régénère pas** ; sinon il les demande au LLM : *1 à 3 mots, en anglais*, 5 (ou 8 avec `match_materials_to_script`) |
| Images / vidéo | `video_source` (`pexels`, `pixabay`, `coverr`, `openai_image`, `local`, …), `video_aspect` (`9:16`, `16:9`, `1:1`), `video_clip_duration` (durée max d'un clip, s), `video_concat_mode` (`random`\|`sequential`), `match_materials_to_script`, `video_transition_mode`, `video_count` |
| Voix | `voice_name` (`elevenlabs:<voice_id>:<nom>`), `voice_rate`, `voice_volume` ; le modèle ElevenLabs vient de `[elevenlabs].model_id` (config) |
| Musique | `bgm_type` (`""` = aucune, `random` = bibliothèque, `elevenlabs`, `sonilo`), `bgm_volume`, `video_music_prompt` |
| Sous-titres | `subtitle_enabled`, `subtitle_display_mode` (`sentence`\|`word_by_word`), `subtitle_animation`, `subtitle_position`, `font_name`, `font_size`, couleurs, contour, fond |

### `video_terms` et ordre des scènes

Avec `openai_image`, le moteur génère **une image par terme, dans l'ordre de la liste**, et s'arrête dès
que la durée cumulée des clips couvre la durée de la voix ; chaque image devient un clip de
`video_clip_duration` secondes au plus. Le montage suit l'ordre des clips si
`video_concat_mode = sequential` (ou `match_materials_to_script = true`), sinon il est mélangé.
Le nombre de scènes est donc **le nombre de termes** (au plus), et chaque terme est envoyé tel quel
(après le gabarit `openai_image_prompt_template` du serveur, s'il existe) au modèle d'image.

## Erreurs propres au moteur (dans `error` d'une tâche en échec)

Clé absente : `"<provider>: api_key is not set, please set it in the config.toml file."` (texte) ou
`"… requires an … API key"` (`preflight`). Quota/erreur fournisseur : message du fournisseur (déjà
expurgé de la clé côté moteur pour les images). Lody n'affiche **jamais** ce texte brut : il est
classé (`clé absente`, `quota`, `fournisseur`, `délai`, …) puis nettoyé de tout motif de secret.

## Configuration : ce que Lody lit et ne lit pas

- Le **moteur** lit `config.toml` (clés, `llm_provider`, gabarit d'images, etc.).
- **Lody** lit `config.toml` en lecture seule uniquement pour savoir si une clé est *renseignée*
  (booléens) et pour connaître le `llm_provider` du moteur ; aucune valeur de clé n'est conservée,
  journalisée, affichée ou enregistrée en base.
- Le fournisseur de texte réellement utilisé par le moteur est `app.llm_provider` (global, non
  surchargeable par requête) : Lody vérifie sa clé avant tout lancement.
