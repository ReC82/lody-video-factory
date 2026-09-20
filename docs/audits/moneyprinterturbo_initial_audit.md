# Audit technique MoneyPrinterTurbo → plateforme multichaîne LodyLands

- Date de l'audit : 2026-09-20 (serveur AWS, `/srv/moneyprinterturbo`)
- Mode : **lecture seule** (aucun fichier du dépôt modifié, aucun commit, aucun build, aucun redémarrage, aucune génération, aucun appel fournisseur payant, aucune tâche créée).
- Convention de preuve : `chemin:lignes` — *commande de diagnostic* — résultat résumé.
- Aucune valeur de clé/token/mot de passe n'est reproduite. Pour `config.toml` : noms de sections/paramètres, et `présent` / `vide` / `absent` pour les valeurs sensibles.
- Limites de l'audit (à garder en tête) :
  - L'utilisateur `ubuntu` **n'a pas accès au socket Docker** (`permission denied`). Je n'ai pas utilisé `sudo`. Le digest d'image, les variables d'environnement, l'état de santé et la politique de redémarrage réels **n'ont pas pu être relevés** ; j'ai reconstitué ce qui est observable via `ps`, `ss`, `/proc/<pid>/mountinfo` et l'API locale.
  - `script.json` et les BGM de `storage/tasks/*` sont en `0600 root` : illisibles depuis l'hôte. Leur structure est déduite du code.
  - Aucune vérification externe (web) n'a été faite : tout ce qui concerne ElevenLabs (paramètres API), Suno, OFox est marqué **« à vérifier »** quand le code local ne suffit pas.
  - La suite de tests n'a pas été exécutée (elle écrirait des caches dans le dépôt ; l'environnement hôte n'a pas les dépendances). Inventaire statique seulement.

---

## 1. Résumé exécutif

**Verdict.** MoneyPrinterTurbo (v1.3.7 + 14 commits, ≈ 58 700 lignes dont 7 700 pour `webui/Main.py`) est une base **exploitable** : pipeline complet (script → scènes → TTS → sous-titres → matériaux → assemblage → publication), API FastAPI, CLI, 1 092 tests. Mais c'est un **monolithe à configuration globale unique** : secrets, préférences UI, style de chaîne et réglages d'épisode vivent dans le même `config.toml` et dans un même état Streamlit. La couche multichaîne doit donc s'ajouter **à côté** du moteur (nouveau package + nouvelles routes), avec quelques correctifs ciblés *dans* le moteur.

**Constats prioritaires (par gravité)**

| # | Constat | Gravité | Preuve (résumé) |
|---|---|---|---|
| 1 | **La WebUI est publique sans authentification** (nginx → 8501 sans `auth_basic`, Streamlit sans login) et elle propose **« Export Keys »** : un fichier JSON contenant toutes les clés en clair, plus l'édition de la config. | **Critique** | `curl https://video.lodylands.com/` → 200 sans identifiant ; `/etc/nginx/sites-enabled/video.lodylands.com` (aucun `auth_basic`, aucun allow/deny) ; `webui/Main.py:2966-3030` (`_render_key_backup_settings`). À confirmer qu'aucune couche externe (Cloudflare Access, etc.) ne protège devant nginx : rien de tel n'est visible localement. |
| 2 | **Bug de durée musique confirmé sur un artefact réel** : MP4 de 83,04 s pour 62,28 s de narration (flux vidéo 62,30 s, flux audio 83,04 s). | Élevé | `ffprobe` sur `storage/tasks/16c4e4da…/final-1.mp4` ; cause : `video.py:1485-1506` (voir §10). |
| 3 | **Le prompt visuel global, le modèle et la taille d'image ne sont pas paramétrables par tâche** (config globale uniquement) ; `video_terms` en chaîne est coupé sur les virgules ; l'ordre des visuels n'est garanti que si `sequential`/`match_materials_to_script`, alors que le défaut API est `random`. | Élevé | `material.py:1144-1165`, `task.py:327`, `schema.py:109-118`, `video.py:203-262`. |
| 4 | **ElevenLabs** : `voice_rate` et `voice_volume` sont acceptés puis **ignorés** ; `voice_settings` codés en dur ; pas de timestamps → sous-titres répartis **proportionnellement aux caractères** ; pas de script d'affichage distinct du script TTS ; aucune réutilisation de narration via l'API. | Élevé | `voice.py:2023-2112`, `voice.py:1061-1132`, `task.py:363-407`. |
| 5 | **Le clone local n'est pas ce qui tourne** : conteneurs sur l'image upstream (code copié *dans* l'image, seuls `config.toml` et `storage` sont montés). Le clone est propre, identique à `origin/main`, **sans aucune adaptation LodyLands**. Modifier le clone n'a aucun effet sans reconstruire/redéployer. | Élevé (process) | `git status` propre, `git rev-list --left-right --count HEAD...origin/main` = `0 0`, `mountinfo` du conteneur. |
| 6 | **Configuration globale contaminée** : `app.openai_image_prompt_template`, `ui.custom_system_prompt`, `ui.video_script_prompt` contiennent une direction « broadcast / Fill & Key » (autre chaîne). | Moyen | `config.toml` (noms de clés relevés, contenu tronqué non sensible). |
| 7 | **L'API garde un instantané périmé de la config** : les défauts du schéma API sont figés à l'import ; l'API live annonce `subtitle_animation` = `none` alors que `ui.subtitle_animation` = `pop_spring` dans `config.toml`. L'état des tâches API est **en mémoire** (perdu au redémarrage, Redis désactivé). | Moyen | `/openapi.json` live vs `config.toml` ; `state.py:44-96` ; `enable_redis=False`. |
| 8 | Aucun **contrôle de coût**, aucune **régénération de scène**, aucune **métadonnée par image** exploitable (fichiers `openai-image-<hex12>.png` sans index de scène). | Moyen | `material.py:1365-1400` ; grep « cost/estimate » : rien hors LoomLoom. |
| 9 | API locale **sans clé** (`app.api_key` vide) — acceptable car liée à `127.0.0.1`, mais **inutilisable pour un agent externe** sans exposition ; `config.toml` est en `0664` (lisible par tous les utilisateurs de l'hôte). | Moyen | `ss -ltn` ; `stat config.toml`. |
| 10 | Suno : **aucune trace** dans le dépôt, les dépendances, `uv.lock`. Aucune intégration à réutiliser. | Info | `grep -rIil suno` → 0 résultat. |

**Recommandation d'architecture (détaillée §14-16).**
1. Dépôt **privé dérivé** avec remote `upstream`, branche `lodylands/main`, **image construite depuis ce dépôt et étiquetée** (rollback = retagger).
2. Nouveau package `app/lodylands/` (profils, manifeste, compilateur manifeste→`VideoParams`, providers de musique, contrôle qualité) + nouvelles routes `/api/v2/…` ajoutées par **une ligne** dans `app/router.py`.
3. Petits correctifs dans le moteur, chacun avec test : trim/fade musique, ElevenLabs (speed/modèle/prononciation), prompt d'image par tâche, scènes structurées, régénération d'une scène.
4. Séparer 4 niveaux : **secrets** (env/fichier 0600, jamais affichés) · **config système** · **profils de chaîne** (JSON versionné dans Git privé) · **manifeste d'épisode** (JSON versionné par épisode).
5. **Avant toute évolution** : protéger la WebUI (basic-auth/SSO nginx), désactiver l'export de clés, `chmod 600 config.toml`, définir `app.api_key`.

---

## 2. État réel du dépôt

| Élément | Valeur | Preuve |
|---|---|---|
| Branche courante | `main` (seule branche locale ; `origin/main` suivie) | `git branch -a` |
| Commit courant | `92bebeefb55046cbb28846a43915c7d389794c31` — « Merge pull request #1363 … docs(config): document the ElevenLabs music base URL » (2026-09-14 20:55 +0800) | `git log -1` |
| Remotes | `origin` = `https://github.com/harry0703/MoneyPrinterTurbo.git` (fetch+push). **Pas de remote `upstream`, pas de fork privé.** | `git remote -v` |
| Tags | `v1.3.7` … `v1.2.8` ; `git describe` = `v1.3.7-14-g92bebee` (14 commits après v1.3.7) | `git tag`, `git describe --tags` |
| Fichiers modifiés | 0 | `git status --porcelain` (vide) |
| Fichiers non suivis | 0 ; ignorés : `config.toml`, `storage/` | `git status --porcelain --ignored` |
| Écart avec l'upstream | `0 0` **par rapport à `origin/main` tel que déjà présent localement**. Je n'ai **pas** fait de `git fetch` (écrirait dans `.git`) : l'écart avec le vrai upstream d'aujourd'hui est **inconnu**. | `git rev-list --left-right --count HEAD...origin/main` |
| Adaptations LodyLands | **Aucune.** Aucun commit de l'utilisateur `ReC82`, aucune occurrence « lodyland » dans les fichiers suivis. | `git log --author=ReC82`, `grep -rIl -i lodyland` |
| Version affichée WebUI | `v{config.project_version}` (badge dans l'en-tête) = `__version__` = **1.3.7** (aussi visible dans l'API : `/openapi.json` → `info.version` = `1.3.7`) | `webui/Main.py:1656`, `app/config/config.py:580`, `pyproject.toml:10`, API live |
| Auteurs | 4 principaux contributeurs upstream ; tous les commits sont upstream | `git log --format=%an` |

**Risque d'écrasement lors d'une mise à jour.**
- Le clone ne contient rien de local à perdre aujourd'hui ; les risques sont **ailleurs** :
  1. `config.toml` est **ignoré par Git** et **réécrit intégralement par la WebUI** (`config.py:476-548` : `toml.dumps` de tout, commentaires perdus — 8 Ko contre 28 Ko pour `config.example.toml`). Une modif manuelle pendant que la WebUI tourne peut être écrasée.
  2. `docker compose pull` sur `:latest` (`docker-compose.release.yml:7,15`) change le code **sans** que le clone ne bouge : aucune reproductibilité, aucun rollback identifiable.
  3. `docker-compose.yml` (variante *build*) monte **tout** le dépôt (`./:/MoneyPrinterTurbo`) : un `git pull` change alors le code en direct au prochain redémarrage.
  4. `storage/` appartient à `root` (conteneur en root) : un `git clean -fdx` par `ubuntu` échouerait partiellement ; un `rm` avec sudo détruirait les artefacts.

---

## 3. Déploiement réel

### 3.1 Ce qui est **observé** (sans Docker)

| Constat | Preuve |
|---|---|
| 2 conteneurs actifs depuis le 2026-09-14 18:46 : WebUI (`streamlit run ./webui/Main.py --server.address=0.0.0.0 --server.port=8501 --browser.serverAddress=127.0.0.1 --server.enableCORS=True --browser.gatherUsageStats=False --client.toolbarMode=minimal --logger.hideWelcomeMessage=True --server.showEmailPrompt=False`) et API (`python3 main.py`), **exécutés en root** dans les conteneurs | `ps -o pid,user,lstart,args -p 1844357,1844358` |
| Ports : `127.0.0.1:8501` (WebUI) et `127.0.0.1:8080` (API) via `docker-proxy` ; **rien d'autre n'est publié** ; nginx écoute sur 80/443 | `ss -ltnp`, `ps` (`docker-proxy -host-ip 127.0.0.1 -host-port 8080/8501`) |
| Montages du conteneur WebUI : **uniquement** `/srv/moneyprinterturbo/config.toml → /MoneyPrinterTurbo/config.toml` et `/srv/moneyprinterturbo/storage → /MoneyPrinterTurbo/storage` (le code est donc **dans l'image**) | `/proc/1844357/mountinfo` |
| Version servie par l'API = 1.3.7, avec les routes `/api/v1/social-metadata`, `/terms`, `/scripts`… identiques au clone | `GET http://127.0.0.1:8080/openapi.json` |
| Reverse proxy : `video.lodylands.com` → `proxy_pass http://127.0.0.1:8501` (WebSocket OK, `client_max_body_size 250M`), TLS Certbot, HTTP 80 → 404/redirection ; **aucune authentification, aucun rate-limit, aucune restriction IP** ; `/api/` n'est **pas** proxifié (le chemin public `/api/v1/tasks` renvoie la page HTML Streamlit) | `/etc/nginx/sites-enabled/video.lodylands.com` ; `curl https://video.lodylands.com/api/v1/tasks` → HTML Streamlit |
| Hôte : `ubuntu` n'est pas dans le groupe `docker` ; `dockerd` tourne en root | `id`, `docker ps` → permission denied |
| Ports hôte 8100/8143/8144 = autres services (jury-central, etc.) ; pas de Redis local sur 6379 | `ss -ltnp`, `ps` |

### 3.2 Ce qui **n'a pas pu être relevé** (à récupérer par un utilisateur avec accès Docker)
`docker ps -a`, `docker inspect` (digest `RepoDigests`, `Config.Env` — **attention : contient potentiellement des secrets, ne pas coller dans un ticket**, `HostConfig.RestartPolicy`, `Health`), `docker images --digests`, `docker compose config`. Commande sûre pour le digest seul : `docker inspect --format '{{.Image}} {{index .RepoDigests 0}}' moneyprinterturbo-api`.

Déduction : le déploiement correspond à **`docker-compose.release.yml`** (ou un `docker run` équivalent) : image `ghcr.io/harry0703/moneyprinterturbo:latest`, deux services, deux volumes. Le fichier compose réellement lancé et son dossier de travail **restent à confirmer**.

### 3.3 Inventaire des fichiers Docker du dépôt

| Fichier | Rôle | Points notables |
|---|---|---|
| `Dockerfile` (l.1-111) | Image CPU `python:3.11-slim-bullseye` + `git ffmpeg` + `pip install -r requirements.txt` + `COPY . .` | **`ARG DOCKER_BUILD_MIRROR=china` et `PIP_USE_OFFICIAL=0` par défaut** : un build local sur AWS tente d'abord les miroirs Aliyun/Tsinghua (apt + PyPI) ; pour AWS passer `--build-arg DOCKER_BUILD_MIRROR=default --build-arg PIP_USE_OFFICIAL=1`. `chmod 777 /MoneyPrinterTurbo`. Pas de `USER` (root). Pas de `HEALTHCHECK`. Base Debian **bullseye** (ancienne). `CMD` = Streamlit. |
| `Dockerfile.gpu` (l.1-55) | Base `nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04`, Python 3.11 via deadsnakes | Sans intérêt ici (pas de GPU sur l'hôte ; `whisper.device=cpu`). Miroirs chinois en tête pour pip. |
| `Dockerfile.claude` (l.1-24) | `FROM ghcr.io/harry0703/moneyprinterturbo:latest` + Node + `@anthropic-ai/claude-code` (version 2.1.260 figée) pour le provider LLM `claude_code` | Hors périmètre. Montre le patron « surcouche sur l'image officielle ». |
| `docker-compose.yml` | Services `webui`+`api`, **`build:` local**, volume **`./:/MoneyPrinterTurbo`** (tout le dépôt), ports `127.0.0.1`, `restart: always` | Le code du clone est exécuté en direct ; `config.toml`/`storage` inclus par ce bind. |
| `docker-compose.release.yml` | Mêmes services, **`image: ghcr.io/harry0703/moneyprinterturbo:latest`**, volumes `./config.toml` et `./storage` uniquement | ← **Correspond au déploiement observé.** `:latest` non épinglé. |
| `docker-compose.gpu.yml` | Override GPU (`deploy.resources.reservations.devices`) | Non utilisé. |
| `docker-compose.claude.yml` | Variante Claude CLI (token OAuth via env) | Non utilisée. |
| `.dockerignore` | exclut `storage/`, `config.toml`, `.git/`, `.env*` | Bon : secrets non copiés dans l'image lors d'un build. |
| `.github/workflows/docker-ghcr.yml` | Publie l'image multi-arch (amd64+arm64) sur GHCR (build-args « default »/PIP officiel) | C'est ainsi qu'est produit `:latest`. |

Aucun `healthcheck`, aucune variable d'environnement, aucun GPU dans les compose (sauf l'override GPU). `restart: always` partout. Redis : non déclaré (donc `enable_redis` ne peut fonctionner que si un Redis externe est ajouté).

### 3.4 Image upstream vs construction locale
- **Image upstream** : code figé au moment du build GHCR ; `docker compose pull` = mise à jour non contrôlée ; le clone Git n'a aucun lien avec le code exécuté.
- **Construction locale** : `docker compose -f docker-compose.yml build` (ou `docker build`) produit une image à partir du clone ; avec le compose « build » le code est **en plus** monté depuis l'hôte (la modification prend effet au redémarrage, sans rebuild, mais les dépendances Python ne changent pas).
- **Conséquence pour LodyLands** : toute modif du clone est **invisible** tant que l'on utilise `docker-compose.release.yml`. Il faut un compose dédié (`build:` + `image: lodylands/mpt:<tag>`), voir §14 et ticket T0.3.
- Comment construire une image personnalisée sans se battre : `docker build -t lodylands/mpt:$(git rev-parse --short HEAD) --build-arg DOCKER_BUILD_MIRROR=default --build-arg PIP_USE_OFFICIAL=1 .` (à ne lancer qu'après validation — non exécuté ici).

---

## 4. Architecture actuelle

Deux processus indépendants partagent le **même code** et le **même `config.toml`** mais **pas la même mémoire** :
- **API** (`main.py` → `uvicorn app.asgi:app`, port 8080) : `InMemoryTaskManager`/`MemoryState` propres.
- **WebUI** (`streamlit run webui/Main.py`, port 8501) : exécute le pipeline **dans son propre processus** via `webui_task.submit_generation` (thread) avec son propre gestionnaire et son propre état. **Elle n'appelle pas l'API HTTP.** Sans Redis, les tâches lancées par l'une sont invisibles à l'autre (la WebUI reconstitue son historique en scannant `storage/tasks/*/script.json`, `webui/Main.py:921-1046`).

| Composant | Fichier / symbole | Responsabilité | Dépendances | Réutilisable ? | Couplage problématique |
|---|---|---|---|---|---|
| Entrée API | `main.py`, `app/asgi.py` (`get_application`, l.1-220) | Uvicorn, CORS par liste blanche `CORS_ALLOWED_ORIGINS`, garde d'origine navigateur, protection de `/tasks/*` par `verify_token`, montage statique `/tasks` et `/` | FastAPI, `config` | Oui, tel quel | Défauts de schéma calculés à l'import depuis `config.ui` (`schema.py:140-149,173-178`) |
| Routeur | `app/router.py` | Inclut `ping`, `video`, `llm` | — | **Point d'extension idéal** (ajouter `lodylands.router`) | — |
| Auth API | `app/controllers/base.py:verify_token` (l.34-100) | Header `x-api-key` unique, comparaison constant-time, **libre si `app.api_key` vide** ; rejette les en-têtes dupliqués | `config` | Oui | Clé unique, pas de rôles |
| Contrôleur vidéo | `app/controllers/v1/video.py` | `POST /videos|subtitle|audio`, `GET/DELETE /tasks`, BGM/matériaux (upload/liste), `stream`/`download` | task manager, `state`, `task` | Oui | `create_task` instancie le gestionnaire au niveau module (`l.43-69`) ; renvoie les `params` dans la réponse |
| Contrôleur LLM | `app/controllers/v1/llm.py` | `POST /scripts`, `/terms`, `/social-metadata` (synchrones, **facturent l'appel LLM**) | `llm` | Oui | — |
| Modèles | `app/models/schema.py` : `VideoParams` (l.91-161), `SubtitleRequest`, `AudioRequest`, `MaterialInfo`, enums | Contrat des requêtes | pydantic | Oui, **à étendre par champs optionnels** | Pas d'`extra="forbid"` → champs inconnus **ignorés silencieusement** ; défauts dépendants de `config.ui` |
| Gestionnaire de tâches | `controllers/manager/base_manager.py`, `memory_manager.py`, `redis_manager.py` | Threads + file bornée (`max_concurrent_tasks`=5, `max_queued_tasks`=100) | threading, redis | Oui | File en mémoire perdue au redémarrage |
| États / progression | `services/state.py` (`MemoryState`, `RedisState`), `models/const.py` (états `-1` échec, `1` terminé, `4` en cours) ; progression 5/10/20/30/40/50→100 | Suivi | — | Oui | Un seul état à plat, pas de sous-étapes ni d'ID de scène |
| Pipeline | `services/task.py` : `_run_pipeline` (l.1353-1656), `start` (l.1658) | Enchaîne script → terms → audio → subtitle → materials → final → cross-post ; `stop_at ∈ {script, terms, audio, subtitle, materials, video}` | tous les services | **Cœur à conserver** | **Aucune reprise** : chaque tâche refait tout ; l'audio est régénéré (payant) à chaque exécution |
| Script LLM | `services/llm.py:generate_script` (l.730), `build_script_prompt` (l.~690-728) | Script depuis sujet ; `custom_system_prompt` et `video_script_prompt` | 26 providers (registre `models/llm_provider.py`) | Oui | Pas de script « TTS » vs « affichage » |
| Mots-clés / scènes | `llm.py:generate_terms` (l.826) → `task.py:generate_terms` (l.312-352) | 5 (ou 8 en mode ordre) **mots-clés de 1-3 mots pour banques de vidéos**, pas des prompts de scène | LLM | **À remplacer par des scènes structurées** | Chaîne coupée sur `[,，]` (`task.py:327`) |
| TTS | `services/voice.py` (3 043 lignes) : `tts` (l.974), `_single_tts` (l.590), `elevenlabs_tts` (l.2023) + 10 autres fournisseurs | Synthèse + `SubMaker` | requests, edge_tts, SDK optionnels | Partiellement | Dispatch par **préfixe de chaîne** `voice_name` ; paramètres non transmis (voir §9) |
| Sous-titres | `services/subtitle.py` (Whisper), `voice.create_subtitle` (l.2885) ; rendu dans `video.generate_video` | SRT depuis `SubMaker` (Edge) ou Whisper | faster-whisper (import paresseux) | Oui | Style lu dans `VideoParams` — bon |
| Matériaux | `services/material.py` (2 352 lignes) : Pexels/Pixabay/Coverr, WaveSpeed, Seedance, OFox, Metaso, **`openai_image`** (l.1071-1560) | Recherche/génération et rendu image→clip | requests, PIL | `openai_image` oui | Modèle, taille, **template lus dans `config.app`** (l.1109-1165) |
| Images → clip | `video.render_image_zoom_video` (l.1526) | Ken-Burns 3 %/s pour `clip_duration` | MoviePy | Oui | Durée **uniforme** |
| Assemblage | `video.combine_videos` (l.742-980), `concat_video_clips_with_ffmpeg` (l.480), `generate_video` (l.1207-1523) | Découpe, transitions, concat FFmpeg, sous-titres MoviePy, mixage audio | MoviePy 2.2.1, FFmpeg | Oui | Mixage BGM cause du bug de durée (§10) |
| Musique | `services/bgm.py`, `elevenlabs_music.py`, `sonilo.py`, `task._VIDEO_MUSIC_PROVIDERS` (l.74-89) | BGM locale/upload/vidéo→musique | requests, ffmpeg | **Table de providers déjà proche d'une interface `MusicProvider`** | Durée décidée par le provider |
| Stockage | `utils.task_dir/storage_dir` ; `task_artifacts.write_script_data` (`script.json` atomique) | `storage/tasks/<uuid>/` : `audio.mp3`, `subtitle.srt`, `openai-image-*.png(.mp4)`, `combined-N.mp4`, `final-N.mp4`, `<provider>-bgm-N.mp3`, `script.json` | — | Oui | Nom d'image sans index de scène |
| Récupération | `GET /tasks/{id}` (URLs `tasks/<id>/final-1.mp4`), `/stream/*`, `/download/*`, statique `/tasks/*` | — | `file_security.resolve_path_within_directory` | Oui | URLs relatives si `app.endpoint` vide |
| Presets | `webui/Main.py:239-296,2840-2965` | Export/import JSON des `VideoParams` | — | Partiellement (§7) | Vit dans le fichier UI de 7 700 lignes |
| Publication | `services/upload_post.py` + `task._run_cross_post*` (l.1125-1350) | Cross-post via service tiers **Upload-Post** (désactivé : `upload_post_enabled=False`) | requests | Optionnel | Configuration **globale**, pas par chaîne ; publie automatiquement si activé |
| Erreurs | `task._mark_task_failed` (l.242) : `{state:-1, failed_stage, error, …ids distants}` ; `HttpException` | — | — | Oui | — |
| Logs | Loguru **stdout uniquement** (sink fichier commenté, `config/__init__.py:8-21`), niveau `DEBUG` ; WebUI capture par thread (`webui_task.py`) | — | — | — | Pas de fichier persistant → `docker logs` seulement |
| CLI | `cli.py` (1 735 lignes) | Exécute le pipeline **en processus**, `--batch-file` JSON/JSONL, `--stop-at`, confirmations de coût pour Seedance/OFox/Metaso | `task.start` | **Utile pour l'automatisation locale** | Non exposée dans l'image via une commande dédiée |
| Skill agent | `docs/skill/SKILL.md` + `mpt_agent.py` | Skill d'installation/génération autonome pour agents | — | Inspiration seulement | Orienté installation `uv`, pas API |
| Tests | `test/` : 1 092 fonctions `def test_` sur 65 fichiers | — | — | — | Voir §12/§18 |

---

## 5. API actuelle

Base : `http://127.0.0.1:8080` (**non** publiée par nginx). Doc live : `/docs`, `/openapi.json` (interrogés en GET uniquement).

### 5.1 Routes (relevées sur `/openapi.json`)

| Méthode | Chemin | Corps / paramètres | Réponse | Effet / coût |
|---|---|---|---|---|
| GET | `/ping` | — | `"pong"` | — |
| POST | `/api/v1/videos` | `TaskVideoRequest` (= `VideoParams`) | `TaskResponse` `{status,message,data:{task_id,request_id,params}}` | **Crée une tâche payante** ; `stop_at="video"` |
| POST | `/api/v1/subtitle` | `SubtitleRequest` | `TaskResponse` | Tâche `stop_at="subtitle"` (TTS **payant** puis sous-titres) |
| POST | `/api/v1/audio` | `AudioRequest` | `TaskResponse` | Tâche `stop_at="audio"` (TTS **payant**) |
| GET | `/api/v1/tasks?page&page_size` | — | `TaskListResponse` | Liste depuis l'état **en mémoire** |
| GET | `/api/v1/tasks/{task_id}` | — | `TaskQueryResponse` (`state`, `progress`, `videos[]`, `combined_videos[]`, `failed_stage`, `error`, `warnings`, `audio_duration`, `script`, `terms`…, champs libres autorisés) | 404 si inconnue de la mémoire |
| DELETE | `/api/v1/tasks/{task_id}` | — | `TaskDeletionResponse` | Supprime dossier + état ; **409** si en cours |
| GET/POST | `/api/v1/musics` | POST : multipart `file` (MP3/M4A/AAC/WAV/FLAC/OGG/OPUS/WMA, ≤ 30 Mo, validé FFmpeg, renommé UUID) | fichiers / nom stocké | — |
| GET/POST | `/api/v1/video_materials` | POST : multipart (vidéo ≤ 200 Mo, image ≤ 20 Mo) → `storage/local_videos/` nom UUID | idem | — |
| GET | `/api/v1/stream/{file_path}` | En-tête `Range` | 206 `video/mp4` | Chemin résolu **dans** `storage/tasks` |
| GET | `/api/v1/download/{file_path}` | — | `FileResponse` | idem |
| POST | `/api/v1/scripts` | `VideoScriptRequest` (`video_subject, video_language, paragraph_number 1-10, video_script_prompt ≤2000, custom_system_prompt ≤8000`) | `{video_script}` | **Appel LLM payant**, synchrone |
| POST | `/api/v1/terms` | `VideoTermsRequest` (`video_subject, video_script, amount, match_materials_to_script`) | `{video_terms[]}` | Appel LLM payant |
| POST | `/api/v1/social-metadata` | `VideoSocialMetadataRequest` (`video_subject, video_script, language, platform`) | `{title, caption, hashtags[]}` | Appel LLM payant |
| GET | `/tasks/**` (statique) | — | fichiers | protégé par `verify_token` (middleware `asgi.py:106-127`) |
| GET | `/` (statique) | — | `resource/public/index.html` | — |

Enveloppe : `{"status": <int>, "message": "success"|…, "data": …}` (`utils.get_response`). Erreurs métier → `HttpException` avec le même enveloppe et le vrai code HTTP (400/401/403/404/409/416/429/500) ; erreurs de validation Pydantic → **400** (handler `asgi.py`), alors que l'OpenAPI déclare 422.

### 5.2 Comportements demandés

| Sujet | Constat | Preuve |
|---|---|---|
| Authentification | Header `x-api-key`, **désactivée si `app.api_key` vide** (cas actuel : `vide`). S'applique à `/api/v1/*` et `/tasks/*`, **pas** à `/ping` ni `/`. | `controllers/base.py:34-100`, `asgi.py:106-127`, `config.toml` |
| Lancer | `POST /api/v1/videos` renvoie **immédiatement** `task_id` ; exécution dans un thread | `video.py:181-237` |
| Suivi | `GET /api/v1/tasks/{id}` : `state` 4 → 1 (ou -1), `progress` 0-100 (paliers 5/10/20/30/40/50, puis 50→100 par vidéo) | `task.py:1353-1656` |
| Liste | `GET /api/v1/tasks` paginée, ordre d'insertion | `state.py:49-55` |
| Téléchargement | via URL retournée `/tasks/<id>/final-1.mp4` (relative si `app.endpoint` vide → **vide** aujourd'hui) ou `/api/v1/download/<id>/final-1.mp4` | `video.py:107-129` |
| Erreurs | `failed_stage ∈ {preflight, script, terms, audio, materials, video, pipeline, scheduling, webui_worker}` + `error` texte ; IDs distants conservés pour Seedance/OFox/Metaso/LoomLoom | `task.py:242-290` |
| Suppression | Oui (`DELETE`), refus si busy | `video.py:290-322` |
| Uploads | BGM (`/musics`) et matériaux (`/video_materials`) avec noms UUID immuables | `video.py:325-417`, `bgm.py`, `material_upload.py:17-18` |
| Audio seul / sous-titres seuls | Oui (`/audio`, `/subtitle`) — mais l'audio produit reste dans le dossier d'une tâche **dont l'API ne permet pas de le réinjecter** (voir §9) | `task.py:363-407` |
| Concurrence | `app.max_concurrent_tasks`=5, `max_queued_tasks`=100 → **429** si file pleine ; threads dans le processus API | `base_manager.py`, `video.py:228-234` |
| Persistance au redémarrage | **Non** avec `enable_redis=False` (actuel) : état et file en mémoire perdus, `GET /tasks/{id}` → 404 alors que les fichiers restent sur disque. Avec Redis : état + file persistés, récupération des cross-posts interrompus. | `state.py`, `asgi.py:33-46` |
| Webhooks / callbacks | **Aucun** (ni paramètre `callback_url`, ni émission). Le seul flux sortant est Upload-Post. | grep `callback|webhook` → rien |
| Coût | **Aucun endpoint d'estimation ni de pré-validation** | grep |

### 5.3 Exemple `POST /api/v1/videos` (valide, **sans clé**, non exécuté)
Voir Annexe A3 (corps JSON) — la route est `/api/v1/videos` et non `/videos`.

---

## 6. Tableau complet des paramètres

Champs de `VideoParams` (`schema.py:91-161`, 41 champs) : voir Annexe A2 pour le schéma JSON complet (extrait de l'OpenAPI live, donc **ce que le déploiement accepte réellement**).

Légende — *WebUI* : contrôle présent ; *API* : champ de `VideoParams` ; *config* : lu dans `config.toml` (section) ; *preset* : inclus dans l'export JSON (`_build_settings_preset_payload`, exclusions : `video_materials`, `custom_audio_file`, `bgm_file` sauf musique intégrée) ; *secret* : oui/non.

| Réglage | WebUI | API | config.toml | preset | secret | Portée actuelle | Portée recommandée |
|---|---|---|---|---|---|---|---|
| Sujet `video_subject` | oui | oui (**requis**) | non | oui | non | tâche | manifeste |
| Script `video_script` | oui | oui | non | oui | non | tâche | manifeste (`display_script`) |
| Script TTS distinct | **non** | **non** | non | non | non | inexistant (le script sert à la fois d'affichage, TTS, sous-titres) | manifeste (`tts_script`) |
| Langue `video_language` | oui | oui | `ui.video_language` | oui | non | UI globale + tâche | profil |
| Nb de paragraphes `paragraph_number` (1-10) | oui | oui | `ui.paragraph_number` | oui | non | UI globale + tâche | profil |
| Prompt de script `video_script_prompt` (≤2000) | oui | oui | `ui.video_script_prompt` (**contaminé**) | oui | non | UI globale + tâche | profil |
| Prompt système `custom_system_prompt` (≤8000) | oui | oui | `ui.custom_system_prompt` (**contaminé**) | oui | non | UI globale + tâche | profil |
| Scènes / `video_terms` (`str \| list`) | oui (zone de texte, **jointure « , »**) | oui | non | oui | non | tâche | manifeste (`scenes[]`) |
| Ordre des scènes = script | via `match_materials_to_script` | idem (défaut `false`) | `app.match_materials_to_script=true` (défaut UI seulement) | oui | non | UI globale + tâche | profil (forcé) |
| Source `video_source` | oui | oui (défaut `pexels`) | `app.video_source` (défaut UI seulement) | oui | non | UI globale + tâche | profil |
| Matériaux locaux `video_materials` | oui (upload) | oui (`MaterialInfo[]`, noms dans `storage/local_videos`) | `app.material_directory` (dossier de sortie images) | **non** | non | tâche | manifeste (artefacts) |
| Modèle d'image | oui (dialogue Réglages) | **non** | `app.openai_image_model` (`gpt-image-2`) | **non** | non | **global** | profil |
| URL de l'API image | oui | **non** | `app.openai_image_base_url` | non | non (mais sensible à la config) | global | config système |
| Clés image | oui | non | `app.openai_image_api_keys` (présent) | non | **oui** | global | secrets serveur |
| Taille d'image | oui | **non** | `app.openai_image_size` (vide → 1024x1536 auto par format) | non | non | global | profil |
| Modèle global de prompt d'image | oui (`app.openai_image_prompt_template`) | **non** | `app.openai_image_prompt_template` (**contaminé**) | **non** | non | **global** | profil (+ prompt par scène) |
| Concaténation `video_concat_mode` (random/sequential) | oui | oui (défaut `random`) | `ui.video_concat_mode`=`random` | oui | non | UI + tâche | profil (forcé `sequential` pour images) |
| Durée des scènes `video_clip_duration` (int ≥ 1) | oui | oui (défaut 5) | `ui.video_clip_duration`=7 | oui | non | UI + tâche | manifeste (par scène) |
| Vitesse des clips `video_clip_speed` | oui | oui | `ui.video_clip_speed` | oui | non | UI + tâche | profil |
| Format `video_aspect` (16:9 / 9:16 / 1:1) | oui (mémorisé **par source**) | oui (défaut 9:16) | `ui.video_aspect_pexels/local/openai_image` | oui | non | UI + tâche | profil |
| Résolution | **codée en dur** (1920×1080, 1080×1920, 1080×1080) | non | non | non | non | codée en dur | profil (avec validation) |
| Recadrage `video_fit_mode` (cover/contain) | oui | oui | `ui.video_fit_mode` | oui | non | UI + tâche | profil |
| Transitions `video_transition_mode` | oui | oui | `ui.video_transition_mode`=`None` | oui | non | UI + tâche | profil |
| Nombre de vidéos `video_count` (≥1) | oui | oui | `ui.video_count` | oui | non | UI + tâche | tâche (fixé à 1 en multichaîne) |
| Threads `n_threads` | **non exposé** | oui (défaut 2) | non | oui (valeur du modèle) | non | API seulement | config système |
| Codec vidéo | oui (option) | non | `app.video_codec` | non | non | global | config système |
| Serveur TTS (`tts_server`) | oui | **non** (déduit du préfixe de `voice_name`) | `ui.tts_server`=`elevenlabs` | non | non | UI globale | profil |
| Voix `voice_name` (`elevenlabs:<id>:<nom>`) | oui | oui | `ui.voice_name` | oui | non | UI + tâche | profil |
| Modèle TTS | oui (réglage ElevenLabs) | **non** | `elevenlabs.model_id`=`eleven_multilingual_v2` | non | non | **global** | profil |
| Réglages de voix (stabilité, similarité, style…) | **non** | non | non | non | non | **codés en dur** (`voice.py:2050-2058`) | profil |
| Vitesse `voice_rate` | oui | oui | `ui.voice_rate`=1.1 | oui | non | UI + tâche | profil — **ignoré par ElevenLabs** (`voice.py:2023`) |
| Volume voix `voice_volume` | oui | oui | `ui.voice_volume` | oui | non | UI + tâche | profil (appliqué au mixage `video.py:1433`, pas au TTS) |
| Audio personnalisé `custom_audio_file` | oui (upload) | oui, **mais** doit être dans `storage/tasks/<task_id>` (inaccessible à un client API) | non | **non** | non | tâche | manifeste (artefact d'épisode) |
| Musique `bgm_type` (`""`, `random`, `preset`, `custom`, `sonilo`, `elevenlabs`) | oui | oui (défaut `random`) | `ui.bgm_type`=`elevenlabs` | oui | non | UI + tâche | profil |
| `bgm_file` | oui | oui | `ui.preset_song`, `ui.custom_bgm_file` | seulement musique intégrée | non | UI + tâche | manifeste/profil |
| Volume musique `bgm_volume` | oui | oui (défaut 0,2) | `ui.bgm_volume`=0,2 | oui | non | UI + tâche | profil |
| Prompt musique `video_music_prompt` (≤2000) ; ancien `sonilo_bgm_prompt` | oui | oui | `ui.elevenlabs_music_prompt`, `ui.sonilo_bgm_prompt` | oui (`video_music_prompt`) | non | UI + tâche | profil |
| Modèle musique ElevenLabs | non | non | `elevenlabs.music_model_id`=`music_v2` | non | non | global | profil |
| Sous-titres activés `subtitle_enabled` | oui | oui | `ui.subtitle_enabled` | oui | non | UI + tâche | profil |
| Fournisseur de sous-titres | non | non | `app.subtitle_provider`=`edge` (autre : `whisper`) | non | non | **global** | config système / profil |
| Position `subtitle_position` (+ `custom_position` 0-100) | oui | oui | `ui.subtitle_position`, `ui.custom_position` | oui | non | UI + tâche | profil |
| Mode d'affichage (`sentence` / `word_by_word`) | oui | oui (défaut lu dans config à l'import) | `ui.subtitle_display_mode` | oui | non | UI + tâche | profil |
| Animation (`none` / `pop_spring`) | oui | oui (défaut lu dans config à l'import) | `ui.subtitle_animation` | oui | non | UI + tâche | profil |
| Police `font_name` | oui | oui | `ui.font_name` | oui | non | UI + tâche | profil (polices dans `resource/fonts`) |
| Taille `font_size` | oui | oui | `ui.font_size` | oui | non | UI + tâche | profil |
| Couleur texte `text_fore_color` | oui | oui | `ui.text_fore_color` | oui | non | UI + tâche | profil |
| Contour `stroke_color` / `stroke_width` | oui | oui | `ui.stroke_*` | oui | non | UI + tâche | profil |
| Arrière-plan `text_background_color` (bool ou couleur) | oui (case + couleur → champ unique) | oui | `ui.subtitle_background_enabled/_color` | oui | non | UI + tâche | profil |
| Fond arrondi `rounded_subtitle_background` | oui | oui (défaut `false`) | `ui.rounded_subtitle_background` | oui | non | UI + tâche | profil |
| Publication (Upload-Post) | oui (Réglages) | **non** (pas de champ par tâche) | `app.upload_post_*` | non | clé : **oui** | **global**, auto-publication si activée | profil (plateformes) + manifeste (métadonnées) + secret serveur |
| Métadonnées sociales | non intégré au pipeline | route `/social-metadata` | non | non | non | appel LLM séparé | manifeste (`publication`) |
| Concurrence / file | non | non | `app.max_concurrent_tasks`, `max_queued_tasks` | non | non | global | config système |
| Redis | non | non | `app.enable_redis` + `redis_*` (mot de passe `vide`) | non | mot de passe : oui | global | config système |
| Whisper | oui | non | `[whisper]` (`large-v3`, `cpu`, `int8`) | non | non | global | config système |
| Clé API de l'API | non | — | `app.api_key` (**vide**) | non | **oui** | global | secrets serveur |
| Coût max / budget | **non** | non | non | non | non | inexistant | profil + manifeste |

Champs **inutilisés/obsolètes/en double** : `sonilo_bgm_prompt` (hérité, `task.py:92-102`), `video_language` « auto » (chaîne vide), `subtitle_enabled` dans `SubtitleRequest` est une **chaîne** `"true"` (incohérent avec `VideoParams` booléen), `hide_config` (historique, `Main.py:3048-3050`).

---

## 7. Presets et configuration

### 7.1 Mécanisme de preset (WebUI)
- **Fichier** : `webui/Main.py` — constantes l.239-256, `_build_settings_preset_payload` (l.2840-2863), `_parse_settings_preset` (l.2866-2895), `_render_settings_transfer` (l.2924-2964), application `_apply_restored_params` (l.1436-1570).
- **Schéma exact** :
  ```json
  {"schema": "moneyprinterturbo.settings-preset", "version": 1, "app_version": "1.3.7", "params": { …VideoParams… }}
  ```
  Nom de fichier `moneyprinterturbo-settings.json`.
- **Versionnement** : un entier `version` = 1, **comparaison d'égalité stricte** (`_load_transfer_payload`, l.2787-2802) → toute évolution future casse l'import des anciens fichiers, sauf à écrire une migration. Pas de mécanisme de compatibilité ascendante.
- **Champs inclus** : tous ceux de `VideoParams` (y compris `video_subject`, `video_script`, `video_terms`, prompts de script, réglages de sous-titres/voix/musique).
- **Exclus volontairement** : `video_materials`, `custom_audio_file`, `bgm_file` (chemins locaux), sauf musique intégrée (`bgm_type=="preset"` → nom de fichier de `resource/songs`).
- **Validation** : `VideoParams.model_validate` (bornes Pydantic) ; `video_subject` défaut `""`.
- **Champs inconnus** : Pydantic par défaut = **ignorés sans erreur** ; les champs `openai_image_*`, `elevenlabs.model_id`, `subtitle_provider`, `tts_server`, `upload_post_*`, `match_materials_to_script` (celui-ci *est* dans `VideoParams`) — **tout ce qui vit dans `config.toml` n'est pas dans le preset**. Un épisode « exporté » ne se rejoue donc **pas** à l'identique sur une autre configuration.
- **Fuite de secrets** : `VideoParams` ne contient **aucun champ credential** → un preset n'expose pas de clé (le contrôle porte sur la structure, pas sur un filtrage). **En revanche**, un second export existe : **`moneyprinterturbo-keys.json`** (`schema: moneyprinterturbo.key-backup`), qui exporte **toutes** les clés (`_collect_key_backup`, l.2754-2769 : suffixes `api_key`, `api_keys`, `api_token`, `access_key`, `secret_key`, `speech_key` + champs compagnons) et en **restaure** (écrit dans `config.toml`). Combiné au constat n°1 (WebUI publique) c'est le point de sécurité le plus grave.
- **Différence preset / requête API** : le preset = `VideoParams` + enveloppe ; la requête API = `VideoParams` seul, mais **le preset ne contient ni le modèle d'image, ni la taille, ni le template d'image, ni le modèle TTS**, ni la politique de sous-titres système. Aucun preset n'est chargeable via l'API.

### 7.2 Exemple de preset exporté représentatif (sans secret)
Voir Annexe A5.

### 7.3 Le preset peut-il devenir le format canonique d'un épisode ?
**Non — créer un manifeste versionné distinct** (recommandation ferme) :
1. Le preset est **plat** (une liste `video_terms`, un seul `video_clip_duration`), sans scènes, sans prompts par scène, sans artefacts, sans provenance.
2. Il **omet** exactement les paramètres qui posent problème (modèle/taille/template d'image, modèle TTS).
3. Sa version est en égalité stricte, sans migration.
4. Il vit dans `Main.py` (7 700 lignes) et dépend de Streamlit.
Le manifeste v1.0 (Annexe A6) **compile** vers `VideoParams` (§14) et un convertisseur **preset → manifeste** (à sens unique) assure la reprise de l'existant. Le preset reste utile comme **export de compatibilité**.

### 7.4 Configuration globale — 4 niveaux mélangés
`config.toml` mélange aujourd'hui : **A) secrets** (`app.*_api_key(s)`, `elevenlabs.api_key`, `redis_password`…), **B) config système** (ports, Redis, concurrence, whisper, ffmpeg, codec), **C) préférences UI** (`[ui]`, 35 clés écrites automatiquement par chaque interaction), **D) style de chaîne** (`openai_image_prompt_template`, `ui.custom_system_prompt`, `ui.video_script_prompt`, voix/sous-titres/musique dans `[ui]`). La WebUI **réécrit tout le fichier** à chaque changement (`config.py:476-548`) ; ajouter un profil dans ce fichier est donc à proscrire.

Sections/paramètres relevés (valeurs sensibles masquées) : `[app]` (146 clés : `api_key`=vide, `hide_config`, `video_source`=`openai_image`, `openai_image_*` (`api_keys`=**présent**, `base_url` → api.openai.com, `model`=`gpt-image-2`, `size`=vide, `prompt_template`=**contaminé**), `llm_provider`=`moonshot` **alors que seul `openai_api_key` est présent** (`moonshot_api_key` vide → toute génération de script LLM échouerait), une trentaine de blocs `<provider>_api_key/_base_url/_model_name` **vides**, `subtitle_provider`=`edge`, `enable_redis`=False, `max_concurrent_tasks`=5, `upload_post_*` désactivé), `[whisper]`, `[proxy]` (vide), `[azure]`, `[siliconflow]`, `[minimax_tts]`, `[elevenlabs]` (`api_key`=présent, `model_id`, `music_base_url`, `music_model_id`, `music_timeout`), `[chatterbox]`, `[kokoro]`, `[fish_audio]`, `[voxcpm]`, `[ui]`.

---

## 8. Pipeline visuel

Chaîne réelle pour `video_source="openai_image"` (fichiers : `task.py:312-352, 638-793`, `material.py:1071-1560`, `video.py:742-980, 1526`).

1. **Script** : fourni (`video_script`) ou LLM (`task.generate_script`).
2. **`video_terms`** (`task.generate_terms`, l.312-352) :
   - fourni **str** → `re.split(r"[,，]", …)` (**l.327**) → toute virgule dans un prompt le coupe en plusieurs « scènes » ;
   - fourni **list** → conservée telle quelle (`strip` seulement) — **la liste JSON évite déjà le problème via l'API** ;
   - absent → LLM : **5 mots-clés (8 si `match_materials_to_script`) de 1 à 3 mots, en anglais**, conçus pour des banques de vidéos (`llm.py:826-905`) — ce ne sont **pas** des descriptions de scène.
   - WebUI : la zone de texte stocke les termes **joints par `", "`** (`Main.py:1446,4446,4941,4485`) puis relus par le split → **un prompt riche contenant une virgule est cassé dans la WebUI**, et la restauration d'une tâche (`Main.py:1379,1446`) rejoint la liste par virgules (corruption aller-retour).
   - `twelvelabs.rerank_terms_by_subject` peut réordonner (désactivé : clé vide).
3. **Prompt final** : `material._openai_image_prompt(search_term)` (**l.1144-1165**) lit **`config.app["openai_image_prompt_template"]`** ; si le gabarit contient `{term}` → `template.replace("{term}", search_term)`, sinon retourne le terme brut. Utilisé dans `generate_images_openai` (l.1406-1466) qui envoie `{"model", "prompt", "n":1, "size"}` à `POST {base_url}/images/generations`. **Aucun override par tâche** : modèle (`l.1109-1127`), taille (`l.1130-1141`) et gabarit sont globaux.
4. **Génération à la demande** (`_download_videos_openai_image_on_demand`, l.1485-1562) : itère les termes **dans l'ordre**, génère 1 image par terme, la rend en clip `render_image_zoom_video(image, max_clip_duration)` (durée **uniforme** = `video_clip_duration`), **s'arrête dès que la durée cumulée ≥ durée audio** (économie), ignore l'échec d'un terme (image sautée, pas de reprise). Consignes de coût : pas de retry sur *read timeout* (risque de double facturation), 3 tentatives sur 429/5xx, rotation de clés sur 401/403.
5. **Artefacts** : `storage/tasks/<id>/openai-image-<uuid4[:12]>.png` + `<…>.png.mp4`. **Le nom ne contient ni index de scène ni hash de prompt.** Les métadonnées (`search_term`, dimensions réelles) sont écrites par `_persist_material_sources` dans `script.json` (0600 root, donc illisible côté hôte). Le **prompt final** (après gabarit) n'est journalisé qu'en `logger.info(term=…)` — pas conservé.
6. **Nombre de scènes / durée** : nombre d'images = ⌈durée audio / `video_clip_duration`⌉ **au plus**, limité par le nombre de termes ; si les termes s'épuisent avant la durée audio → `combine_videos` **boucle les clips** (`video.py:933-947`) ⇒ **répétition des visuels**. Il n'y a pas de lien entre une phrase du script et sa scène (aucun alignement temporel).
7. **Ordre** : `generate_final_videos` (l.860+) force `sequential` **seulement si** `match_materials_to_script` ; sinon `video_concat_mode` (défaut API/UI : **`random`**) → `_prioritize_unique_source_clips` fait `random.shuffle` (`video.py:256-257`) ⇒ **par défaut les visuels sont mélangés**. Avec `sequential`, `combine_videos` ne garde que le premier segment de chaque source (`video.py:786-824` (`break` après le premier segment)).
8. **Régénération d'une scène isolée** : **impossible** aujourd'hui (pas d'ID de scène, pas d'API, pas de reprise, images non indexées). Contournement existant : uploader les PNG validés via `POST /video_materials` puis `video_source="local"` + `video_materials` + `sequential` (`task.py:645-660`, `video.py:1560-1642`) — durée **uniforme**, pas d'audio réutilisable, pas de métadonnées.

### Changements minimaux (précis)

| Besoin | Changement minimal | Fichiers |
|---|---|---|
| Prompts avec virgules | Traiter `video_terms` de type `list` comme atomique (déjà vrai) ; côté WebUI/CLI/preset remplacer la jointure `", "` par un séparateur de ligne (`\n`) et splitter sur `\n` uniquement ; *ou* ne plus passer par cette voie (manifeste) | `task.py:327`, `Main.py` (4 points) |
| Liste structurée de scènes | Champ optionnel `scenes: list[Scene]` dans `VideoParams` (`id, narration, visual_prompt, duration_target, seed?, negative?`) ; si présent, `generate_terms` court-circuité, `search_terms = [s.visual_prompt]` | `schema.py`, `task.py:312+` |
| Prompt global par profil | Nouveau champ `image_prompt_template: str = ""` (+ `image_model`, `image_size`) dans `VideoParams` ; `_openai_image_prompt(term, template=None)` et `_openai_image_endpoint/size` acceptent l'override et retombent sur `config.app` | `material.py:1109-1165, 1406`, chaîne d'appel `task.get_video_materials` → `material.download_videos` → `_download_videos_openai_image_on_demand` |
| Prompt particulier par scène | Le `visual_prompt` de scène remplace `{term}`, ou est envoyé tel quel si `raw=True` | idem |
| Graine | Non supportée par `/images/generations` OpenAI ; champ `seed` **conservé dans le manifeste** et transmis uniquement aux providers compatibles (**à vérifier** selon le fournisseur) | `material.py:1417` (payload) |
| Conserver image + prompt | Nommer `scene-01.png`, écrire `scene-01.json` (prompt final, modèle, taille, hash, coût estimé, statut) à côté ; `save_dir` est déjà paramétrable (`generate_images_openai(save_dir=…)`) | `material.py:1365-1466` |
| Régénération d'une scène | Fonction `regenerate_scene(episode, scene_id)` = `generate_images_openai` + `render_image_zoom_video` sur **cette** scène, puis nouvelle assemblée | nouveau `app/lodylands/scenes.py` |
| Assemblage depuis artefacts validés | Nouvelle étape `assemble` : `combine_videos` + `generate_video` sur clips/audio/SRT **déjà présents** (pas de TTS, pas d'image) ; durée par scène via `MaterialInfo.duration` ou liste `clip_durations` | `task.py:1353+` (nouvelle fonction, réutilise `generate_final_videos`) |

---

## 9. Pipeline audio et TTS

### 9.1 Intégration ElevenLabs (état réel)

| Sujet | Constat | Preuve |
|---|---|---|
| Appel TTS | `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}` — **URL codée en dur** (la base URL configurable n'existe que pour la *musique*, `elevenlabs.music_base_url`) ; `Content-Type: application/json`, en-tête `xi-api-key` ; corps `{text, model_id, voice_settings}` | `voice.py:2023-2112` |
| Emplacement de la clé | `config.elevenlabs.api_key` (présent) **ou** variable d'environnement `ELEVENLABS_API_KEY` ; saisie WebUI en champ `password` qui **écrit dans `config.toml`** | `voice.py:411-420`, `Main.py:5967-6040` |
| Voix « favorites » | **Notion inexistante.** `get_elevenlabs_voices` liste toutes les voix du compte (`GET /v2/voices`), formatées `elevenlabs:{voice_id}:{nom}`. La voix retenue (« Kev - Young, Dynamic and Bright ») est mémorisée dans `ui.voice_name` (**global**) | `voice.py:183-205`, `config.toml` `[ui]` |
| Modèle | `elevenlabs.model_id` (config globale, `eleven_multilingual_v2`). `elevenlabs_tts(..., model_id="")` sait recevoir un modèle mais **`_single_tts` ne le transmet jamais** → impossible de le choisir par tâche/profil | `voice.py:2023-2046`, `voice.py:660-667` |
| Vitesse | `voice_rate` est **dans la signature mais absent du corps de requête** → sans effet sur ElevenLabs (le réglage `ui.voice_rate = 1.1` est donc inopérant pour cette voix). L'API ElevenLabs propose un paramètre de vitesse dans `voice_settings` : **à vérifier** dans la documentation officielle avant implémentation | `voice.py:2023-2057` |
| Volume | `voice_volume` non transmis au TTS ; appliqué au **mixage** (`afx.MultiplyVolume`) | `video.py:1431-1435` |
| Réglages de voix | `stability=0.5, similarity_boost=0.75, style=0.0, use_speaker_boost=True` **codés en dur**, non exposés | `voice.py:2050-2058` |
| Prévisualisation | WebUI seulement : extrait (`sample`) ou script complet (`full`) ; empreinte SHA-256 de `{contenu, serveur, voix, rate, volume, signature du fournisseur}` ; l'aperçu **complet** est réutilisé par la tâche **uniquement si** script/voix/rate identiques et `voice_volume==1.0` | `Main.py:5432-5826`, `task.py:421-478` |
| Timestamps | **Aucun** : `SubMaker` reconstruit par `populate_legacy_submaker_with_full_text` = découpage par ponctuation + **durée répartie proportionnellement au nombre de caractères** ; dernière phrase absorbe le reste | `voice.py:1061-1132` |
| Acronymes / prononciation | **Aucun dictionnaire.** Seuls existent les tags de pause `[pause: 1s]` (`utils.py:324-430`), **supprimés** pour ElevenLabs (`tts()`, `voice.py:974-1010` : seul Azure v1/Edge découpe en segments) | idem |
| Script affiché ≠ script TTS | **Non.** `voice.tts(text=video_script)` (`task.py` `generate_audio`) et `voice.create_subtitle(text=video_script, …)` utilisent la même chaîne. Comme les phrases sont appariées par ponctuation, un texte TTS différent casserait l'appariement (`len(sub_items) != len(script_lines)` → aucun SRT, `voice.py:2917-2921`) | `task.py:481-568`, `voice.py:2885-2927` |
| Reprise d'audio | `custom_audio_file` : accepté par l'API mais **doit se trouver dans `storage/tasks/<task_id>`** (`resolve_custom_audio_file`, `task.py:363-407`) — le `task_id` est généré par le serveur → **un client API ne peut pas réinjecter une narration**. Autorisé : upload WebUI, CLI (`allow_server_file_input`). Sans `sub_maker` (audio custom), les sous-titres exigent Whisper. | `task.py:363-407, 570-636` |
| Cache | Aucun cache d'audio TTS (chaque tâche → nouvel appel **facturé**). Seul l'aperçu WebUI est mis en cache par empreinte (session Streamlit) | — |
| Coût | Non suivi. Ordre du pipeline : **TTS avant images** (échec des images = TTS déjà payé, `task.py:1503-1518`) | — |
| Erreurs / timeouts | 3 tentatives ; 401/403/422 et statuts `voice_disabled/voice_access_denied/unauthorized` **non rejoués** ; autres → `continue` **sans temporisation** ; `timeout=60` s ; durée arrondie **au-dessus** (`math.ceil`) | `voice.py:2060-2111`, `task.py:548-556` |
| Sortie | `audio.mp3` écrit tel quel (pas de normalisation, pas de trim des silences) | `voice.py:2094-2103` |

### 9.2 Ajouts minimaux proposés

| Besoin | Proposition minimale |
|---|---|
| `display_script` / `tts_script` | Champs optionnels `tts_script: str = ""` (et `display_script` = alias de `video_script`) dans `VideoParams`. `generate_audio` utilise `params.tts_script or video_script`. Les sous-titres se construisent **sur le texte d'affichage** en répartissant la durée réelle de l'audio (comme aujourd'hui pour ElevenLabs), ce qui supprime la contrainte d'appariement. Amélioration ultérieure (**à vérifier**) : endpoint d'alignement caractère-par-caractère d'ElevenLabs pour des sous-titres précis. |
| Dictionnaire de prononciation par profil | `profile.tts.pronunciations: {"BTC":"Bitcoin","NFT":"N F T","Web3":"Web trois"}` appliqué par un pré-processeur déterministe (regex à frontières de mot, sensible à la casse, journal des substitutions) qui produit `tts_script` **avant** l'appel. Testable sans réseau. Les dictionnaires de prononciation natifs ElevenLabs : **à vérifier**. |
| Prévisualisation avant génération complète | `POST /api/v2/episodes/{id}/tts-preview` : 1 à 2 phrases (≤ 300 caractères), stocke `narration/preview-<hash>.mp3`, retourne durée et coût estimé. Réutilise l'empreinte existante. |
| Conservation / réutilisation de la narration | `narration/tts-<sha256(tts_script|voice|model|settings)>.mp3` + `narration.json` (durée mesurée, alignement) ; l'étape `assemble` réutilise ce fichier ; si le hash change → invalidation explicite. Nécessite de sortir la contrainte « fichier dans le dossier de la tâche » pour les fichiers appartenant à l'épisode. |
| Paramètres de voix, modèle, vitesse | Transmettre `model_id`, `voice_settings` (+ vitesse si confirmée) depuis le profil jusqu'à `elevenlabs_tts` (signature déjà prête pour `model_id`). |

---

## 10. Pipeline musique

### 10.1 Modes disponibles

| Mode `bgm_type` | Source | Durée finale de la musique | Coût | Remarques / preuve |
|---|---|---|---|---|
| `""` | aucune | — | 0 | `bgm.should_use_bgm` (`bgm.py:48`) : type vide ou volume ≤ 0 → désactivée |
| `random` | tirage dans `storage/bgm` + `resource/songs` (29 fichiers livrés dans `resource/songs`) | **bouclée et coupée** à la durée de la vidéo (`AudioLoop(duration=video_clip.duration)`) | 0 | `video.py:654-680, 1493` |
| `preset` | morceau intégré nommé | idem | 0 | `bgm.resolve_builtin_bgm_file` |
| `custom` | upload utilisateur (`POST /api/v1/musics`, ≤ 30 Mo, validé FFmpeg, nom UUID) | idem | 0 | `bgm.py:169-272` |
| `elevenlabs` | **vidéo → musique** : envoie un proxy du `combined-N.mp4` à ElevenLabs Music (`music_v1`/`music_v2`), prompt `video_music_prompt` ≤ 1000 car. ; plan payant requis | **décidée par ElevenLabs**, **non tronquée localement** | payant (tarif non suivi) | `elevenlabs_music.py:292-403` ; `task._VIDEO_MUSIC_PROVIDERS` (l.74-89) |
| `sonilo` | idem via `api.sonilo.com` (clé `vide`) | idem (mécanisme identique ; **à vérifier** par test) | payant | `sonilo.py:271-357` |
| Musique « synchronisée » à la narration | **Inexistante** en tant que telle (seuls les deux fournisseurs vidéo→musique ci-dessus s'adaptent à l'image, pas à la voix) | — | — | — |
| Découpage / fondu | fondu de sortie fixe **3 s** appliqué à la **fin du fichier de musique**, volume `bgm_volume` (0,2 par défaut) | — | — | `video.py:1485-1488` |

### 10.2 Pourquoi une musique plus longue rallonge le MP4 (cause démontrée)
1. `generate_final_videos` génère la musique avec `video_duration=audio_duration` (`task.py:959-964`) — mais pour ElevenLabs, ce paramètre ne sert qu'à **valider** `≤ 600 s` (`elevenlabs_music.py:363-403`) : **il n'est pas envoyé à l'API**. La durée réelle est celle que renvoie ElevenLabs.
2. `generate_video` reçoit `bgm_file_override` (fichier fournisseur). Dans ce cas **aucun `AudioLoop`/trim n'est appliqué** (`video.py:1489-1493` : « le fournisseur a déjà adapté la durée »).
3. L'audio final est `CompositeAudioClip([voix, musique])` (`l.1496`) : sa durée est celle du **plus long** composant. `video_clip.with_audio(audio_clip)` (`l.1506`) ne recoupe ni la piste ni le clip ; FFmpeg écrit alors un flux audio plus long que le flux vidéo, et **la durée du conteneur MP4 devient celle de l'audio**.
4. `AudioFadeOut(3)` s'applique à la **fin de la musique fournisseur** (au-delà de la fin de la vidéo) : aucun fondu audible à la fin de la narration, musique qui se prolonge sur image figée/noir.
5. **Preuve** — `storage/tasks/16c4e4da-e075-4720-a587-c1e1ece98dde` (commande : `ffprobe -show_entries stream=duration / format=duration`) : flux vidéo **62,30 s**, flux audio **83,04 s**, conteneur **83,04 s**, `audio.mp3` de narration **62,28 s** ; le fichier `elevenlabs-bgm-1.mp3` fait 1 328 737 octets = **83,04 s à 128 kb/s** (calcul) — exactement l'excédent. Les 8 autres tâches ont un flux audio à ±0,1 s du flux vidéo.
6. Le chemin `random/preset/custom` n'est **pas** touché (trim par `AudioLoop`).

### 10.3 Meilleur point d'intervention
`app/services/video.py:generate_video`, bloc l.1482-1506 (un seul endroit, testable avec un MP3 factice de 90 s) :
```
target = min(video_clip.duration, narration_duration + profile.music.tail_margin)   # marge configurable, ex. 0.3 s
bgm   = bgm_source.subclipped(0, min(bgm_source.duration, target))                  # trim
if bgm.duration < target: loop                                                       # optionnel selon profil
bgm   = bgm.with_effects([MultiplyVolume(v), AudioFadeOut(profile.music.fade_out)]) # fondu APRÈS le trim
audio = CompositeAudioClip([voice, bgm]).with_duration(target)
final = video_clip.with_audio(audio).with_duration(target)
```
Puis tests : durée finale = narration + marge ± 1 image ; fondu positionné à `target`. Le trim est aussi un filet de sécurité contre tout futur fournisseur.

| Exigence | Où / comment |
|---|---|
| Durée finale = narration + marge configurable | `target` ci-dessus ; marge dans le profil (`music.tail_margin_s`) |
| Trim automatique | `subclipped(0, target)` sur la musique **fournisseur** (chemin `override`) |
| Fade-out | après trim ; durée dans le profil (`music.fade_out_s`, défaut 3) |
| Réutilisation d'une musique | cache `music/<sha256(provider|model|prompt|version)>.mp3` + `music.json` ; `MusicProvider.get_or_create` ; import manuel = même cache |
| Désactivation par profil | `music.mode = "none"` → `bgm_type=""` dans la compilation |
| Coût estimé avant génération | `MusicProvider.estimate_cost(context)` lu dans une table de tarifs **de la config système** (à renseigner et **vérifier** par l'utilisateur ; je n'invente aucun tarif) ; entré dans la prévalidation et le budget |

### 10.4 Suno — ce qu'on peut réellement affirmer
- **Code local** : aucune occurrence de « suno » dans les sources, `requirements.txt`, `pyproject.toml`, `uv.lock`, docs (`grep -rIil suno` → aucun résultat). Aucune intégration existante, aucune dépendance à réutiliser.
- **API officielle** : **information à vérifier ultérieurement** (je n'ai pas consulté de source externe et ne l'affirme pas).
- Classement des options :

| Option | Statut | Risque | Recommandation |
|---|---|---|---|
| **Import manuel** d'un morceau généré sur Suno (MP3/WAV) via `POST /api/v1/musics` puis `bgm_type="custom"` ou via l'upload d'un futur `MusicProvider("manual_import")` | Fonctionne **aujourd'hui**, sans dépendance | Droits/licence du morceau : **à vérifier** selon l'abonnement (usage commercial) | ✅ **Solution par défaut** |
| API officielle Suno | **À vérifier** (existence, conditions, tarifs) | — | À évaluer plus tard ; ne pas planifier tant que non confirmée |
| API tierce « compatible Suno » / passerelles revendeuses | API tierce, non officielle | Stabilité, conformité aux CGU, continuité de service, coût | Seulement derrière l'interface `MusicProvider`, désactivée par défaut |
| Automatisation navigateur / cookies de session | **Fragile** | Casse à chaque évolution du site, risque de violation des CGU, secrets de session à stocker | ❌ Non recommandée |

- **Interface abstraite proposée** (`app/lodylands/music.py`) :
```python
class MusicProvider(Protocol):
    id: str                                   # "local_library" | "uploaded" | "elevenlabs" | "sonilo" | "manual_import" | "suno_*"
    official_api: bool                        # documente le statut (voir tableau ci-dessus)
    def validate_access(self) -> None: ...    # non facturé
    def estimate_cost(self, ctx: MusicContext) -> CostEstimate: ...
    def get_or_create(self, ctx: MusicContext) -> MusicResult: ...   # (path, duration_s, provider, cost, cache_key)
@dataclass class MusicContext: profile_id, episode_id, target_duration_s, prompt, video_path|None, seed|None
```
Adaptateurs immédiats : `local_library`, `uploaded`, `manual_import` (fichier posé dans l'épisode), `elevenlabs` et `sonilo` (enveloppes des modules existants). Un adaptateur Suno futur n'est qu'une classe de plus. La logique de trim/fondu (§10.3) reste **hors** des providers.

---

## 11. Fournisseurs présents

Légende de classement : **V** = conserver visible · **M** = conserver mais masquer par défaut · **O** = rendre optionnel (dépendance/flag) · **D** = désactiver dans notre build · **S** = suppression éventuelle *plus tard* (jamais d'emblée).

### 11.1 Inventaire

| Catégorie | Fournisseur (identifiant) | Statut config actuel | API | Classement |
|---|---|---|---|---|
| **LLM** (26 entrées dans `models/llm_provider.py`) | `openai` (+ base_url : compatible OpenAI) | clé **présente** | officielle | **V** |
| | `openrouter`, `litellm`, `oneapi`, `ollama` (local) | vides | compatibles OpenAI / locale | **V** (compatible) / **M** |
| | `anthropic`, `gemini`, `deepseek`, `groq`, `azure`, `grok`, `claude_code` | vides | officielles | **M** |
| | `moonshot` (**valeur actuelle de `llm_provider`**, clé vide), `qwen`, `volcengine`, `minimax`, `mimo`, `shengsuanyun`, `modelscope`, `apimart`, `aihubmix`, `evolink`, `api_route`, `cloudflare`, `pollinations`, `aimlapi` | vides | régionales / passerelles | **D** (build LodyLands) ; **S** éventuel plus tard |
| **TTS** (valeurs `tts_server` de la WebUI) | `elevenlabs` | clé **présente** | officielle | **V** |
| | `azure-tts-v1` (Edge TTS, gratuit, sans clé, fournit des limites de mots) | — | non officielle mais standard du projet | **M** (secours/gratuit) |
| | `kokoro`, `chatterbox` (auto-hébergés locaux) | `127.0.0.1:8880/4123` | locale | **O** |
| | `azure-tts-v2`, `gemini-tts`, `fish_audio` | vides | officielles | **M** |
| | `siliconflow`, `mimo-tts`, `minimax-tts`, `voxcpm` | vides | régionales | **D** |
| **Sous-titres** | `edge` (à partir du TTS) / `whisper` (`faster-whisper`, `large-v3`, CPU) | `edge` | locale | **V** (edge) ; **O** (whisper : dépendance lourde, modèle de plusieurs Go téléchargé à l'usage) |
| **Banques vidéo** | `pexels`, `pixabay`, `coverr` | clés vides | officielles | **M** (utiles en repli) |
| **Images** | `openai_image` (`/images/generations`, compatible) | clés **présentes** ; modèle `gpt-image-2` | officielle / compatible | **V** |
| **Vidéos IA** | `ofox` (`api.ofox.ai`, clé vide), `wavespeed`, | vides | tiers (**à vérifier**) | **M**/**O** (OFox, envisagé) |
| | `volcengine_seedance`, `metaso_minimax`, `loomloom` (Shengsuanyun) | vides | régionales | **D** |
| **Matériaux locaux** | `local` (images/vidéos uploadées) | — | — | **V** |
| **Rerank sémantique** | `twelvelabs` | clé vide | tiers | **D** |
| **Musique** | `random`/`preset`/`custom` | — | locale | **V** |
| | `elevenlabs` (Music) | clé partagée **présente** | officielle | **V** |
| | `sonilo` | vide | tiers (**à vérifier**) | **M** |
| | Suno | **absent** | **à vérifier** | Import manuel seulement (§10.4) |
| **Publication** | `upload_post` (service tiers Upload-Post, YouTube/TikTok…) | désactivé | tiers | **M** (couche `publication` du profil à concevoir, pas de publication automatique par défaut) |
| **Infra** | Redis | désactivé | — | **O** (à activer pour l'état persistant) |
| **Assemblage** | FFmpeg / MoviePy 2.2.1 | — | locale | **V** |

### 11.2 Stratégie (sans supprimer de code upstream)
1. **Registre + `enabled_providers`** : une petite table (`app/lodylands/providers.py`) déclare pour chaque fournisseur `kind`, `official_api`, `secret_keys`, `enabled_by_default`. Un fichier de configuration système (`lodylands.toml`, hors `config.toml`) fournit `enabled_providers = ["openai", "elevenlabs", "openai_image", "local", …]`.
2. **UI filtrée** : les `selectbox` de la WebUI (`VIDEO_SOURCE_GROUPS`, l.116-130 ; liste TTS ; `bgm_type`, `Main.py:6042+`) lisent ce registre (patch de quelques lignes) — ou, mieux, on ne modifie pas `Main.py` et on fournit une **WebUI LodyLands séparée** (page Streamlit dédiée, §14).
3. **API v2** : rejet explicite (HTTP 400) d'un fournisseur non activé.
4. **Dépendances optionnelles** : `dashscope`, `litellm`, `azure-cognitiveservices-speech`, `faster_whisper` sont **importés paresseusement** (`llm.py:334,426`, `voice.py:1496`, `subtitle.py:7`) → on peut les retirer d'un `requirements.lodylands.txt` sans casser l'import (à valider par un test d'import).

### 11.3 Impacts
| Axe | Impact d'un masquage/flag | Impact d'une suppression de code |
|---|---|---|
| Maintenance | Faible (patchs minimes, conflits rares) | **Élevé** : chaque merge upstream touche `voice.py`/`material.py`/`Main.py` |
| Sécurité | Réduit la surface exposée (clés/endpoints non saisis dans l'UI) ; dépendances retirées = moins de CVE à suivre | Idem, mais divergence durable |
| Taille d'image | Retirer `litellm`, `dashscope`, `azure-…speech`, `faster-whisper` (+ dépendances CUDA/ctranslate2) réduit nettement l'image *(mesure non faite : pas d'accès Docker)* | idem |
| Temps de build | Moins de paquets → plus court ; les couches `pip` sont déjà en cache tant que `requirements.txt` ne change pas | idem |
| Compatibilité upstream | Bonne (feature flags additifs) | Mauvaise |
| Tests | Suite existante inchangée ; ajouter des tests « provider désactivé » | Il faudrait retirer/adapter des dizaines de tests (`test_loomloom`, `test_ofox`, `test_volcengine_seedance`, `test_metaso_minimax`, `test_voxcpm`, `test_webui_*` … ≈ 5 000 lignes) |

---

## 12. Sécurité et secrets

| Sujet | Constat | Gravité | Preuve |
|---|---|---|---|
| **WebUI publique sans authentification** | nginx proxifie tout `/` vers Streamlit ; ni `auth_basic`, ni IP allow-list, ni SSO ; Streamlit n'a pas de login. N'importe quel visiteur peut **lancer des générations payantes**, lire/modifier les réglages, **exporter les clés** (`moneyprinterturbo-keys.json`) et importer des clés | **Critique** | `curl https://video.lodylands.com/` 200 ; vhost nginx ; `Main.py:2966-3030` |
| Stockage des secrets | `config.toml` en clair, montage direct, **mode `0664`** (lisible par tout compte de l'hôte), propriétaire `ubuntu` ; contient clés OpenAI (image + LLM) et ElevenLabs (**présentes**), toutes les autres **vides** | Élevé | `stat config.toml`, lecture des noms de clés |
| Affichage des clés dans la WebUI | Champs `type="password"` (masqués à l'écran) mais **valeurs préremplies depuis la config** ; l'export de clés les donne en clair | Élevé | `Main.py:3117, 3342, 5885, 6037…` |
| Écriture dans `config.toml` | Réécriture complète à chaque changement (`toml.dumps`), écrasement en place si bind-mount monofichier (`EBUSY`), commentaires perdus ; verrou intra-processus seulement (l'API et la WebUI sont deux processus) | Moyen | `config.py:476-548` |
| Presets | Ne contiennent **pas** de secret (structure `VideoParams`) ; **mais** leur import ne bloque pas les champs inconnus (ignorés) | Faible | `Main.py:2840-2895` |
| Logs | Loguru **stdout**, niveau `DEBUG`, pas de fichier. Redaction des clés dans les erreurs HTTP (`material._redact_secret`, `_redact_request_error`), la clé de l'API n'est jamais loguée (`asgi.py:28-33`). Les **params de la tâche** (script, prompts, `custom_system_prompt`) sont loggés en entier à la création (`controllers/v1/video.py:226` `Task created: {to_json(task)}`) : pas de secret, mais du contenu éditorial | Faible | greps `logger.` |
| États de tâche | `VideoParams` sans credential ; réponse `POST /videos` renvoie `params` complets ; `script.json` en `0600 root` | Faible | `video.py:210-227` |
| Authentification API | `x-api-key` unique, constant-time, désactivée si vide (**cas actuel**) ; l'écoute conteneur est `0.0.0.0:8080` mais l'hôte ne publie que `127.0.0.1:8080` | Moyen (contenu par l'exposition réseau) | `base.py`, `ss -ltn` |
| CORS | Désactivé par défaut (`CORS_ALLOWED_ORIGINS` vide) ; garde d'`Origin` côté serveur (403) ; `*` déclenche un avertissement si pas de clé | Bon | `asgi.py:60-146` |
| Exposition du port 8080 | Non exposé (127.0.0.1) et **non proxifié** ; l'outil ne peut donc pas être appelé depuis ChatGPT/agent externe sans nouvelle route | Info | vhost, `ss` |
| Reverse proxy | TLS Certbot OK ; `client_max_body_size 250M` ; **pas de rate-limit, pas d'auth, pas d'en-têtes de sécurité** | Élevé | vhost |
| Permissions de fichiers | Conteneurs en **root**, `chmod 777 /MoneyPrinterTurbo` dans l'image, artefacts `root:root` (dossiers `755`, `script.json`/BGM `0600`) → l'hôte `ubuntu` ne peut pas lire `script.json` ni les BGM ; suppression/sauvegarde exigent sudo | Moyen | `ls -la storage/tasks/*`, `Dockerfile:8` |
| Téléchargement des résultats | `resolve_path_within_directory` (realpath + commonpath) sur `/stream`, `/download`, `_task_file_to_uri` ; `/tasks/*` protégé par `verify_token` | Bon | `file_security.py`, `video.py:85-129` |
| Traversée de chemins | Contrôles présents et testés (`test_controller_video.py`, `test_api_authentication.py`, `test_asgi_static_files.py`) ; `custom_audio_file` limité au dossier de la tâche ; `bgm_file` limité aux répertoires BGM ; matériaux limités à `storage/local_videos` | Bon | `task.py:363-407`, `video.py:654-680`, `video.py:1560-1600` |
| Uploads | Extensions/tailles bornées (BGM 30 Mo, vidéo 200 Mo, image 20 Mo), validation FFmpeg, noms UUID ; `client_max_body_size` nginx 250 Mo | Bon | `bgm.py`, `material_upload.py:17-18` |
| Cross-post automatique | Si activé, **publie tout** sans validation humaine (`upload_post_auto_upload`) ; désactivé aujourd'hui | Info | `task.py:1600-1640` |
| Image de base | `python:3.11-slim-bullseye` (Debian 11, en fin de support standard) ; build local par défaut via miroirs chinois | Moyen | `Dockerfile:2,14-15` |

### 12.1 Séparation cible des niveaux de secret/configuration

| Niveau | Contenu | Où | Qui écrit | Dans Git ? |
|---|---|---|---|---|
| **A. Secrets serveur** | clés OpenAI/ElevenLabs/…, `app.api_key`, `redis_password` | variables d'environnement (`.env` 0600, `env_file` compose) ou `/run/secrets`, **jamais** dans l'UI ni les profils ; `config.toml` garde des champs vides | l'administrateur | **Non** |
| **B. Configuration système** | ports, Redis, concurrence, codec, whisper, `enabled_providers`, tarifs estimés | `lodylands.toml` (nouveau, non secret) | l'administrateur | Oui (privé) |
| **C. Profils de chaîne/série** | voix, modèle, style visuel, sous-titres, musique, règles éditoriales, budget | `profiles/<profile_id>.json` (schéma versionné, **validateur qui refuse tout champ ressemblant à un secret**, réutilisant `CREDENTIAL_KEY_SUFFIXES`) | éditeurs | Oui (privé) |
| **D. Manifestes d'épisodes** | sujet, scripts, scènes, prompts, réglages | `storage/episodes/<episode_id>/manifest.json` | API/WebUI/agent | Non (données) ; export possible |
| **E. Artefacts de tâche** | audio, images, clips, SRT, MP4, rapport QC | `storage/episodes/<episode_id>/…` (+ `storage/tasks/<task_id>` upstream conservé) | pipeline | Non |

Correctifs immédiats sans code : `chmod 600 config.toml`, authentification nginx devant `video.lodylands.com` (basic-auth ou SSO), `app.api_key` non vide, rotation des clés si l'exposition a duré (à décider — je ne peux pas déterminer si l'export a déjà été utilisé).

---

## 13. Limites techniques actuelles

1. **Une seule configuration globale** partagée par deux processus non synchronisés ; l'API garde un instantané des défauts (`schema.py:140-178`, preuve : défaut live `subtitle_animation=none` vs config `pop_spring`) ; changer de chaîne = éditer des réglages globaux ou l'UI.
2. **Pas de notion de profil, de série, d'épisode, de scène, de manifeste.**
3. **Pas de reprise ni de régénération partielle** : `_run_pipeline` refait TTS + images à chaque tâche (`task.py:1353-1656`) ; un échec d'image après TTS = TTS perdu/facturé.
4. **Scènes = mots-clés** ; durée **uniforme** (`video_clip_duration`), sans lien avec la narration ; boucle de clips si peu de termes ; ordre aléatoire par défaut.
5. **Prompt d'image global** (template, modèle, taille) ; virgules coupant les prompts (str) ; jointures WebUI par virgules ; pas de graine ; nom d'image sans index.
6. **ElevenLabs** : vitesse ignorée, modèle non transmis, réglages en dur, pas de timestamps → **sous-titres approximatifs**, pas de dictionnaire de prononciation, pas de script TTS distinct, pas de réutilisation de narration côté API.
7. **Musique fournisseur non bornée** → MP4 plus long que la narration (bug confirmé) ; fondu au mauvais endroit.
8. **API** : état en mémoire, pas de webhook, pas d'estimation de coût, pas de pré-validation, pas de liste d'artefacts, URLs relatives (`endpoint` vide), enveloppe non typée (`data: Any` pour les erreurs), 400 vs 422 incohérent, `n_threads` seulement côté API.
9. **Deux exécuteurs** (API et WebUI) avec des états séparés ; tâches WebUI invisibles à l'API.
10. **Monolithe UI** : `webui/Main.py` = 7 708 lignes ; toute évolution UI en conflit potentiel avec l'upstream.
11. **Permissions/propriété root** dans `storage` ; `script.json` illisible côté hôte ; pas de logs persistants.
12. **Sécurité opérationnelle** : WebUI publique sans auth ; clés exportables ; `config.toml` 0664.
13. **Aucun suivi du coût** ; aucun budget par vidéo ; retries TTS sans backoff.
14. **Déploiement non reproductible** : `:latest` non épinglé, healthchecks absents, image Debian 11, miroirs chinois par défaut au build.
15. **Configuration LLM actuelle incohérente** : `llm_provider=moonshot` avec clé vide alors que seul OpenAI est configuré (les routes `/scripts`, `/terms`, `/social-metadata` échoueraient ; un script fourni contourne).

### 13.1 Tests existants, lacunes et tests prioritaires

**Existant** (`test/`, 65 fichiers, 1 092 fonctions ; CI `.github/workflows/ci.yml` : Python 3.11 et 3.13, `compileall`, `ruff`, pytest + couverture, service Redis) — *inventaire statique, suite non exécutée ici* :
- API/sécurité : `test_controller_video.py` (608 l.), `test_controller_base.py`, `test_controller_llm.py`, `test_api_authentication.py`, `test_asgi_cors.py`, `test_asgi_static_files.py`.
- Pipeline/état : `test_task.py` (2 265 l.), `test_task_manager.py` (in-memory + Redis), `test_task_artifacts.py`, `test_state.py`.
- Média : `test_video.py` (1 404 l.), `test_material.py`, `test_material_openai_image.py` (dont `…applies_prompt_template`, `…redacts_api_key_in_failure_detail`), `test_subtitle*.py`, `test_video_effects.py`, `test_clip_speed.py`.
- Voix/musique : `test_voice.py` (2 115 l., 53 occurrences ElevenLabs), `test_bgm.py`, `test_elevenlabs_music.py`, `test_sonilo.py`, `test_webui_bgm.py`.
- Config/WebUI : `test_config.py`, `test_webui_settings_transfer.py` (preset + sauvegarde de clés), nombreux `test_webui_*`.
- CLI/agent : `test_cli.py` (2 154 l.), `test_mpt_agent_skill.py`.

**Lacunes** (constatées par `grep` de noms de tests) :

| Domaine | Lacune | Priorité |
|---|---|---|
| Durée finale / musique | `test_video.py:354` verrouille que le BGM fournisseur **n'est pas** bouclé, mais **aucun test** ne vérifie durée finale = narration avec une musique plus longue (le bug §10.2 passe la suite) | **P0** |
| Prompts visuels | Pas de test : override par tâche (modèle/taille/gabarit), prompt contenant des virgules, aller-retour WebUI (jointure « , »), **ordre** avec `random` vs `sequential` sur `openai_image` | **P0** |
| Séparation des scènes | Un test d'ordre (`test_generate_terms_uses_script_order_mode_when_enabled`) mais rien sur la structure de scène, les durées par scène, la répétition de clips | P1 |
| TTS ElevenLabs | Pas de test affirmant ce qui est **envoyé** (modèle, `voice_settings`, vitesse) ni le comportement de `voice_rate` ; pas de test de prononciation ni d'alignement | P1 |
| Secrets | Redaction testée par fournisseur, mais **aucun test global de non-fuite** (logs, preset, `TaskStatusData`, `script.json`, réponse `POST /videos`) ; rien sur l'export de clés exposé publiquement | **P0** |
| Presets | Pas de test de compatibilité ascendante/descendante (version ≠ 1, champs inconnus ignorés) | P1 |
| API | Pas de test de **dérive des défauts** (schéma à l'import vs config), ni de persistance après redémarrage, ni de contrat OpenAPI figé | P1 |
| Profils multichaînes | Inexistant (à créer) : isolation entre profils, refus des secrets | P0 (avec T2.1) |
| Reprise de tâche / régénération d'une scène | Inexistant (fonctionnalités absentes) | P1 (avec T5.x) |
| Compatibilité upstream | Aucun garde-fou : recommandé un job CI « merge à blanc de `upstream/main` + suite complète » et des tests de contrat sur les points d'extension que nous patchons | P1 |

**Tests prioritaires avant toute modification** : (1) tests de caractérisation « état actuel » (durée MP4 avec BGM long — doit échouer aujourd'hui —, ordre avec `random`, virgules dans `video_terms`) marqués `xfail` puis inversés par les correctifs ; (2) test de non-fuite de secrets sur toutes les sorties ; (3) test d'isolation de configuration entre deux tâches concurrentes (garantit qu'aucun override ne mute `config.app`) ; (4) contrat `VideoParams` (snapshot du schéma OpenAPI) pour détecter une dérive upstream.

---

## 14. Architecture cible recommandée

Principe : **ne pas toucher au moteur plus que nécessaire**, ajouter une couche **au-dessus** qui *compile* un manifeste + un profil vers le `VideoParams` existant, et n'ouvrir dans le moteur que les points d'entrée manquants (overrides par tâche, scènes, réutilisation d'artefacts, trim musique).

```
        ChatGPT / agent / WebUI Studio / CLI
                      │  (x-api-key)
             ┌────────▼─────────┐
             │  /api/v2 (new)   │  profiles · episodes · validate · estimate · render · scenes · qc
             └───┬───────┬──────┘
   D Profils ────┘       └──── E Manifeste v1.0 ──► compilateur ──► VideoParams (+ overrides)
   (JSON, sans secret)         (par épisode)                              │
                                                                          ▼
 A Secrets (env 0600)  B Config système (lodylands.toml)      moteur MPT existant (task._run_pipeline,
 C Registre providers ───────────────────────────────────►    voice, material, video)   ← F Tâches
                                                                          │
                                        G Artefacts (storage/episodes/<id>/…) ──► H Contrôle qualité ──► I Publication (opt-in)
```

| Bloc | Rôle | Réalisation minimale |
|---|---|---|
| **A. Secrets serveur** | Clés, `api_key` | Variables d'environnement lues avant `config.toml` (`resolve_secret(name)`), jamais affichées, jamais dans un profil/manifeste ; export de clés de la WebUI désactivé par flag |
| **B. Configuration système** | Ports, concurrence, Redis, codec, tarifs estimés, `enabled_providers`, chemins | `lodylands.toml` (nouveau, non secret) |
| **C. Registre des fournisseurs** | Métadonnées + activation (kind, `official_api`, clés requises, coût) | `app/lodylands/providers.py` ; pas de suppression de code upstream |
| **D. Profils** | Style d'une chaîne/série | `profiles/*.json`, schéma Pydantic versionné, refus des champs « secret » |
| **E. Manifeste d'épisode** | Contrat unique d'un épisode (script, scènes, audio, sous-titres, musique, sortie) | `storage/episodes/<id>/manifest.json`, `schema_version` |
| **F. Tâches de génération** | Exécution, statut, reprise | `task.start` existant + nouvelle étape `assemble` ; état persistant (Redis ou `task.json`) |
| **G. Artefacts intermédiaires** | Narration, images par scène, clips, SRT, musique, MP4 | dossier d'épisode, noms stables (`scene-01.png`, `narration/tts-<hash>.mp3`) + `meta.json` (prompt final, modèle, taille, hash, coût, statut) |
| **H. Contrôle qualité** | Rapport durée/voix/visuels/sous-titres/musique/coût | `qc/report.json` produit par `ffprobe` + règles ; bloquant/avertissement |
| **I. Publication** | Métadonnées + éventuellement Upload-Post | Opt-in par profil, validation humaine par défaut |

### 14.1 Conversion manifeste → `VideoParams` (le moins de modifications possible)
Fonction pure `compile(manifest, profile) -> (VideoParams, RenderOptions)` :

| Manifeste / profil | `VideoParams` | Statut |
|---|---|---|
| `subject` | `video_subject` | existant |
| `display_script` (provisoirement `tts_script` si présent) | `video_script` | existant ; **`tts_script`** = nouveau champ optionnel (T5.6) |
| `scenes[].visual_prompt` (ordre du tableau) | `video_terms` = **liste** (atomique, virgules OK) | existant (l'API accepte déjà une liste) → **scènes structurées** = nouveau `scenes` optionnel (T5.1) |
| `profile.visual.provider = openai_image` | `video_source="openai_image"` | existant |
| forcé `sequential` + `match_materials_to_script=true` | `video_concat_mode`, `match_materials_to_script` | existant (empêche le mélange) |
| médiane de `scenes[].duration_target` | `video_clip_duration` (int) | existant, **approximation** tant que les durées par scène n'existent pas (T5.5) |
| `profile.format` | `video_aspect` | existant (`9:16`) |
| `profile.tts.voice_id/name` | `voice_name = "elevenlabs:<id>:<nom>"` | existant |
| `profile.tts.model`, `voice_settings`, `speed` | — | **nouveau** (`tts_model_id`, `tts_voice_settings`) |
| `profile.visual.model/size/prompt_template` | — | **nouveau** (`image_model`, `image_size`, `image_prompt_template`) |
| `profile.subtitles.*` | `subtitle_enabled, subtitle_position, custom_position, subtitle_display_mode, subtitle_animation, font_name, font_size, text_fore_color, stroke_color, stroke_width, text_background_color, rounded_subtitle_background` | existant (mapping 1-pour-1) |
| `profile.music.*` | `bgm_type, bgm_volume, bgm_file, video_music_prompt` | existant ; politique de durée/fondu = **nouveau** (T6.1) |
| `manifest.audio.file`, `scenes[].image` (artefacts validés) | `custom_audio_file`, `video_source="local"` + `video_materials` | existant mais **limité** (dossier de la tâche, durée uniforme) → T5.3 |
| `video_count`, `n_threads` | `1`, valeur système | existant |

Règle d'or : le compilateur écrit **tous** les champs explicitement (jamais de dépendance aux défauts calculés à l'import depuis `config.ui`, source du décalage API/WebUI).

### 14.2 Où placer le code
- Nouveau package `app/lodylands/` : `profiles.py`, `manifest.py`, `compiler.py`, `providers.py`, `music.py`, `pronunciation.py`, `estimate.py`, `qc.py`, `scenes.py`, `api.py` (routeur v2).
- Un seul point d'accrochage dans l'existant : `app/router.py` (`root_api_router.include_router(lodylands_router)`).
- WebUI : **page Streamlit séparée** (`webui/Studio.py`, lancée par un troisième service ou par `streamlit` multipage) plutôt que de modifier `Main.py` (7 708 lignes).
- Tests : `test/lodylands/`.

### 14.3 Stratégie Git et upstream (comparaison)

| Approche | Conserver nos changements | Récupérer l'upstream | Éviter les secrets dans Git | Image Docker personnalisée | Retour arrière | Verdict |
|---|---|---|---|---|---|---|
| Modifier seulement le clone local (état actuel) | Fragile : rien n'est versionné hors du disque de l'hôte ; `git pull` peut entrer en conflit | Manuel, risqué | Bon (`config.toml` ignoré) | Non reproductible (le clone n'est pas ce qui tourne) | Aucun | ❌ |
| Branche permanente dans le clone (remote = upstream) | Oui, mais **pas de sauvegarde distante** (pas de droits de push sur l'upstream) | Facile (`git merge upstream/main`) | Bon | Possible | Bon localement | ⚠️ insuffisant seul |
| Fork GitHub **public** | Oui | Facile | **Risque** : prompts/profils/historique publics ; erreurs de commit visibles de tous | Possible | Bon | ❌ pour un usage commercial |
| **Dépôt privé dérivé** avec remotes `origin` (privé) + `upstream` | Oui, sauvegardé | Facile (`fetch`/`merge`/`rebase` de `upstream/main`) | Bon si `.gitignore` + scan de secrets + `env_file` hors dépôt | Oui (build depuis le dépôt, tag image = tag Git) | Excellent (tags Git + tags d'image) | ✅ **Recommandé** |
| Quelques patchs au-dessus de l'image officielle (`FROM ghcr.io/…` + `COPY` de fichiers patchés) | Oui si les patchs sont versionnés ailleurs | Automatique *mais* non contrôlé (`latest`) ou à épingler par digest | Bon | Rapide (pas de réinstallation des dépendances) | Bon si digest épinglé | ✅ Complément acceptable pour les tout premiers patchs, **à condition d'épingler le digest** |

**Recommandation** : dépôt **privé dérivé** (`origin` privé, `upstream` = harry0703/MoneyPrinterTurbo), branche `lodylands/main` créée depuis `v1.3.7-14-g92bebee`, nos ajouts **majoritairement dans de nouveaux fichiers** (`app/lodylands/`, `profiles/`, `test/lodylands/`) + patchs moteur courts et isolés (M1-M11) pour limiter les conflits ; intégration de l'upstream par `merge` régulier (jamais par `docker pull :latest`) avec le job CI de merge à blanc. L'image est construite depuis ce dépôt : `lodylands/mpt:<date>-<sha>`, le tag précédent restant disponible pour le rollback. Les secrets vivent dans un `env_file` hors dépôt (`0600`) ; `profiles/` ne contient que du non-secret (validateur). Les correctifs génériques (M1 surtout) peuvent être proposés à l'upstream pour réduire la divergence.

---

## 15. Changements minimaux nécessaires

Dans le **moteur** (chaque ligne = un patch petit, avec test, candidat à contribution upstream) :

| # | Fichier : symbole | Changement | Taille estimée |
|---|---|---|---|
| M1 | `video.py:generate_video` (l.1482-1506) | Trim + fade + durée finale = narration + marge (§10.3) | ~25 lignes + 3 tests |
| M2 | `schema.py:VideoParams` | Champs optionnels : `image_model`, `image_size`, `image_prompt_template`, `tts_model_id`, `tts_voice_settings`, `tts_script`, `scenes`, `music_tail_margin`, `music_fade_out`, `budget_max` (défauts « comme aujourd'hui ») | ~30 lignes |
| M3 | `material.py:_openai_image_endpoint/_openai_image_size/_openai_image_prompt/generate_images_openai/_download_videos_openai_image_on_demand` + relais dans `download_videos` et `task.get_video_materials` | Accepter les overrides (repli sur `config.app`) ; nom `scene-XX.png` + `scene-XX.json` quand `scenes` est fourni | ~60 lignes |
| M4 | `task.py:generate_terms` (l.312) | Si `params.scenes` : `video_terms = [s.visual_prompt …]`, pas d'appel LLM | ~15 lignes |
| M5 | `voice.py:_single_tts` + `elevenlabs_tts` | Transmettre `model_id`, `voice_settings`, vitesse (si confirmée) ; retries avec backoff | ~30 lignes |
| M6 | `task.py:generate_audio/generate_subtitle` | Utiliser `tts_script or video_script` pour la voix ; sous-titres sur le texte d'affichage | ~25 lignes |
| M7 | `task.py` (nouvelle fonction `assemble`) | Assembler depuis audio + clips **déjà présents** (réutilise `generate_final_videos`) | ~80 lignes |
| M8 | `task.py:resolve_custom_audio_file` | Autoriser un fichier situé dans le dossier d'**épisode** appartenant à l'appelant | ~15 lignes |
| M9 | `state.py` / `task.py` | Écrire `task.json` (état) sur disque à chaque `update_task` pour survivre au redémarrage | ~40 lignes |
| M10 | `controllers/v1/video.py:create_task` | Journaliser sans `params` complets (option) ; renvoyer aussi `episode_id` | ~5 lignes |
| M11 | `webui/Main.py` (optionnel) | Séparateur `\n` pour `video_terms` ; masquer l'export de clés derrière un flag d'environnement | ~15 lignes |

Nouveau code (couche LodyLands) : profils, manifeste, compilateur, prévalidation, estimation, registre providers, `MusicProvider`, dictionnaire de prononciation, QC, routes v2. **Aucune migration de données** n'est requise pour l'existant ; les presets actuels restent importables (converter à sens unique vers le manifeste).

---

## 16. Plan de tickets progressif

Format : **Objectif** · **Fichiers** · **Critères d'acceptation** · **Tests** · **Risques** · **Dépendances** · **Migration** · **Rebuild Docker**.
« Rebuild » = reconstruction de l'image nécessaire ; « non » = opération d'hôte/config uniquement.

### Phase 0 — Sauvegarde et reproductibilité

**T0.1 — Sauvegarder l'état actuel**
- Objectif : point de retour avant toute modification. 
- Fichiers : `config.toml` (copie chmod 600 **hors dépôt**), `storage/` (tar), sortie `docker inspect` (digest **uniquement**, sans `Config.Env`).
- Acceptation : archives listées avec taille+empreinte ; digest d'image noté dans `docs/DEPLOY.md` (privé) ; procédure de restauration écrite.
- Tests : restauration à blanc sur un dossier temporaire.
- Risques : copie des clés (permissions) ; `storage` en root (sudo requis).
- Dépendances : accès Docker/sudo. · Migration : non · Rebuild : non.

**T0.2 — Fermer l'accès public non authentifié** (urgent, indépendant du code)
- Objectif : supprimer le risque n°1. 
- Fichiers : `/etc/nginx/sites-available/video.lodylands.com` (basic-auth ou SSO + rate-limit), `config.toml` (`app.api_key` non vide, `chmod 600`).
- Acceptation : `curl https://video.lodylands.com/` → 401 sans identifiant ; WebSocket Streamlit OK avec identifiants ; `config.toml` en 0600 lisible par le conteneur ; décision documentée sur la **rotation des clés** (exposition passée).
- Tests : `curl` avec/sans identifiants ; génération d'un aperçu gratuit (Edge) après connexion.
- Risques : rupture du WebSocket (en-têtes `Upgrade` à conserver) ; la clé `api_key` s'applique à l'API **au prochain redémarrage** du conteneur API (redémarrage à planifier, non fait ici).
- Dépendances : aucune. · Migration : non · Rebuild : non.

**T0.3 — Dépôt privé dérivé avec remote `upstream`**
- Objectif : versionner nos changements et recevoir l'upstream.
- Fichiers : `.git/config` ; `.gitignore` (+ `profiles/*.local.json`, `.env*`, `storage/`).
- Acceptation : `origin`=privé, `upstream`=harry0703/MoneyPrinterTurbo ; branche `lodylands/main` depuis `v1.3.7-14-g92bebee` ; tag `base-upstream-2026-09-14` ; aucun secret dans l'historique (scan).
- Tests : `git fetch upstream && git merge --no-commit upstream/main` (à blanc) ; scan de secrets.
- Risques : mauvaise configuration de visibilité du dépôt. · Dépendances : compte GitHub privé (**non créé ici**). · Migration : non · Rebuild : non.

**T0.4 — Image reproductible et rollback**
- Objectif : exécuter *notre* code, identifiable et réversible.
- Fichiers : `Dockerfile.lodylands` (ou `Dockerfile` avec build-args), `docker-compose.lodylands.yml` (`build:` + `image: lodylands/mpt:<date>-<sha>`, volumes `config.toml`, `storage`, `profiles`, `env_file`, `healthcheck`, `user:`), doc de déploiement.
- Acceptation : `docker build --build-arg DOCKER_BUILD_MIRROR=default --build-arg PIP_USE_OFFICIAL=1` ; healthchecks `/ping` (API) et `/_stcore/health` (WebUI) ; rollback = relancer le tag précédent ; `:latest` supprimé.
- Tests : démarrage dans un projet Compose parallèle (ports 18080/18501), `GET /ping`, aucune génération.
- Risques : conteneur en root/permissions de `storage` ; espace disque de build ; bullseye. · Dépendances : T0.3. · Migration : non · **Rebuild : oui**.

**T0.5 — Baseline de tests**
- Objectif : suite verte avant patchs ; rendre l'exécution reproductible (`uv sync --frozen`).
- Fichiers : aucun (procédure) ; éventuellement `Makefile`.
- Acceptation : `pytest -q test` vert (Python 3.11) ; tests Redis exécutés ou explicitement ignorés ; rapport de couverture enregistré.
- Tests : la suite elle-même. · Risques : tests dépendant de variables `MPT_TEST_REDIS_*`. · Dépendances : T0.3. · Migration : non · Rebuild : non.

### Phase 1 — Séparation des configurations

**T1.1 — Nettoyer la configuration contaminée**
- Objectif : retirer la direction « broadcast » du global. 
- Fichiers : `config.toml` (`app.openai_image_prompt_template`, `ui.custom_system_prompt`, `ui.video_script_prompt` → vides).
- Acceptation : plus aucune mention de Fill/Key/broadcast dans la config globale ; profils Crypto/Broadcast créés en T2.2.
- Tests : diff de config ; génération d'aperçu **gratuit** (script fourni, pas d'image).
- Risques : écrasement par la WebUI en cours d'exécution (arrêter l'écriture concurrente) ; l'API garde l'ancien état jusqu'au redémarrage. 
- Dépendances : T0.1. · Migration : non · Rebuild : non.

**T1.2 — Secrets par environnement + neutralisation de l'export de clés**
- Objectif : niveau A isolé. 
- Fichiers : `app/lodylands/secrets.py`, `app/config/config.py` (lecture env prioritaire), `webui/Main.py` (flag `MPT_DISABLE_KEY_EXPORT`), `docker-compose.lodylands.yml` (`env_file`).
- Acceptation : clés chargées depuis l'environnement sans être écrites dans `config.toml` ; export/import de clés absent quand le flag est actif ; aucune clé dans `docker inspect` **hors** `env_file` documenté.
- Tests : unitaires (priorité env > toml), test WebUI (bouton absent), test de non-fuite (`grep` des valeurs dans logs/config générée).
- Risques : la WebUI réécrit `config.toml` avec les valeurs en mémoire (vérifier qu'elle n'y matérialise pas les secrets d'env). 
- Dépendances : T0.4. · Migration : oui (déplacer les clés) · **Rebuild : oui**.

**T1.3 — Configuration système `lodylands.toml` + `enabled_providers`**
- Fichiers : `app/lodylands/system_config.py`, `lodylands.toml` (exemple versionné, sans secret).
- Acceptation : lecture validée (Pydantic) ; valeurs par défaut sûres ; erreur claire si fournisseur inconnu.
- Tests : unitaires. · Risques : deux fichiers de config → documenter la frontière. · Dépendances : T0.4. · Migration : non · Rebuild : oui.

**T1.4 — Overrides par tâche des réglages d'image et de TTS**
- Objectif : rendre `image_model/size/prompt_template` et `tts_model_id/voice_settings` **portés par la requête** (M2, M3, M5). 
- Fichiers : `schema.py`, `material.py`, `voice.py`, `task.py`.
- Acceptation : sans champ → comportement **strictement identique** (tests existants verts) ; avec champ → l'appel sortant (mocké) porte la valeur ; `config.app` **jamais muté**.
- Tests : `test_material_openai_image.py` (étendre), `test_voice.py`, `test_schema.py`, test de concurrence de deux tâches à gabarits différents.
- Risques : oubli d'un point d'appel ; compatibilité des presets (champs optionnels donc OK). 
- Dépendances : T0.5. · Migration : non · **Rebuild : oui**.

### Phase 2 — Profils multichaînes

**T2.1 — Schéma `ChannelProfile` (v1) et validateur sans secret**
- Fichiers : `app/lodylands/profiles.py`, `docs/profile.schema.json`.
- Acceptation : champs de la mission (id, nom, langue, format, résolution, durée cible, style, ton, vitesse, TTS, voix, modèle, provider/modèle visuel, template de prompt, palette/DA, règles « pas de texte/logo », durée des scènes, transitions, sous-titres, musique, sécurité éditoriale, mentions, plateformes, budget) ; `extra="forbid"` ; rejet de toute clé se terminant par `api_key|api_keys|api_token|access_key|secret_key|speech_key` ou valeurs ressemblant à un jeton.
- Tests : unitaires (profil valide/invalide, secret refusé). · Risques : sur-spécification → garder v1 étroite. · Dépendances : T1.3. · Migration : non · Rebuild : oui.

**T2.2 — Chargeur, registre et trois profils initiaux**
- Fichiers : `profiles/lodylands_crypto_web3.json`, `profiles/broadcast_av.json`, `profiles/lodyquests_minecraft.json`, loader.
- Acceptation : le profil Crypto reprend les besoins listés (9:16, 45-65 s, Kev + `eleven_multilingual_v2`, cyan/bleu sombre, sans texte/logo, scène 5-7 s, sous-titres par phrase fond arrondi `#101010`, texte blanc contour noir, aucune promesse financière) ; les profils sont **isolés** (test : modifier l'un ne change pas l'autre).
- Tests : chargement, isolation, snapshot JSON. · Risques : contenu éditorial à valider par vous. · Dépendances : T2.1. · Migration : non · Rebuild : oui.

**T2.3 — Compilateur profil → défauts de `VideoParams`**
- Acceptation : tous les champs écrits **explicitement** ; test « ne dépend pas de `config.ui` » (config modifiée → sortie identique).
- Tests : unitaires + golden files. · Dépendances : T1.4, T2.2. · Migration : non · Rebuild : oui.

**T2.4 — Routes `GET /api/v2/profiles` et `/profiles/{id}`** (lecture seule)
- Fichiers : `app/lodylands/api.py`, `app/router.py` (1 ligne). 
- Acceptation : authentifié par `verify_token`, aucune valeur secrète dans la réponse. Tests : `TestClient`. · Dépendances : T2.2. · Rebuild : oui.

**T2.5 — WebUI Studio minimale (page séparée)**
- Fichiers : `webui/Studio.py` (choix de profil, sujet, bouton « Prévalider »). 
- Acceptation : ne modifie ni `Main.py` ni `config.toml` ; le changement de chaîne ne touche **aucun** réglage global.
- Tests : `AppTest` Streamlit. · Risques : deuxième port/service à exposer derrière l'auth. · Dépendances : T2.4, T0.2. · Rebuild : oui.

### Phase 3 — Manifeste versionné d'épisode

**T3.1 — Schéma `EpisodeManifest` v1.0**
- Fichiers : `app/lodylands/manifest.py`, `docs/manifest.schema.json`.
- Acceptation : champs de l'Annexe A6 ; `schema_version` avec chaîne de migrations ; `extra="forbid"` ; statuts de scène `planned|generated|validated|failed|stale`.
- Tests : unitaires + migration factice 1.0→1.1. · Dépendances : T2.1. · Migration : non · Rebuild : oui.

**T3.2 — Compilateur manifeste → `VideoParams`** (§14.1)
- Acceptation : `video_terms` **liste** (prompts avec virgules intacts) ; `sequential` + `match_materials_to_script` forcés ; aucune mutation globale.
- Tests : golden ; test « prompt avec virgules ». · Dépendances : T2.3, T3.1. · Rebuild : oui.

**T3.3 — Conversion preset ↔ manifeste**
- Acceptation : import d'un preset WebUI v1 → manifeste (champs manquants explicités dans un rapport) ; export manifeste → preset (perte documentée).
- Tests : preset réel de l'Annexe A5. · Dépendances : T3.1. · Migration : oui (existant → nouveau format). · Rebuild : oui.

**T3.4 — Prévalidation hors ligne (aucun appel payant)**
- Contrôles : durée estimée du script (mots ≈ vitesse) dans la cible du profil ; nombre de scènes vs durée ; longueur des prompts ; **règles interdisant texte/marques/logos** ; acronymes non couverts par le dictionnaire ; fournisseurs activés ; clés présentes (booléen) ; format/résolution ; police disponible ; budget.
- Acceptation : rapport `{errors[], warnings[], estimate}` ; bloque `render` si erreurs.
- Tests : cas nominaux/erreurs. · Dépendances : T3.2. · Rebuild : oui.

**T3.5 — Estimation de coût**
- Acceptation : `estimate.py` lit une table de tarifs **de `lodylands.toml`** (aucun tarif codé en dur ; valeurs à fournir/vérifier par vous) ; total par poste (LLM, TTS, N images, musique) ; comparaison avec le budget du profil.
- Tests : calculs déterministes. · Risques : tarifs obsolètes → date de la table affichée. · Dépendances : T3.4. · Rebuild : oui.

### Phase 4 — API d'automatisation

**T4.1 — `POST/GET /api/v2/episodes`, `POST /episodes/{id}/validate`, `GET /episodes/{id}/estimate`**
- Acceptation : création idempotente (`Idempotency-Key`), stockage `storage/episodes/<id>/manifest.json`, validation OpenAPI stricte. Tests : `TestClient`. · Dépendances : T3.4. · Rebuild : oui.

**T4.2 — `POST /episodes/{id}/render` + statut enrichi**
- Acceptation : crée une tâche via `task_manager`/`tm.start` ; refuse si validation KO ou budget dépassé ; `GET /episodes/{id}` renvoie étapes (script/audio/scènes/assemblage), progression, coûts cumulés, URLs absolues (`endpoint`).
- Tests : pipeline mocké. · Risques : file/concurrence partagée avec la WebUI. · Dépendances : T4.1. · Rebuild : oui.

**T4.3 — État persistant** (M9)
- Acceptation : redémarrage de l'API → `GET /tasks/{id}` et `/episodes/{id}` répondent ; tâches « processing » orphelines marquées `failed(interrupted)` ; option Redis documentée.
- Tests : redémarrage simulé. · Migration : non · Rebuild : oui.

**T4.4 — Webhooks signés (optionnel)**
- Acceptation : `callback_url` sur liste blanche, HMAC (secret serveur), retries bornés, événements `episode.completed|failed`.
- Risques : SSRF → liste blanche stricte, pas de redirections. · Dépendances : T4.2. · Rebuild : oui.

**T4.5 — Exposition sécurisée pour agents**
- Fichiers : nginx (`location /mpt/` → 127.0.0.1:8080, TLS, `x-api-key` obligatoire, rate-limit, éventuelle liste d'IP), `app.api_key` fort.
- Acceptation : sans clé → 401 ; avec clé → OK ; `/tasks/*` protégé ; limites de taille. · Dépendances : T0.2. · Rebuild : non.

**T4.6 — Contrat pour ChatGPT/agent**
- Fichiers : `docs/openapi-agent.json` (sous-ensemble v2), instructions d'usage. 
- Acceptation : un agent peut « créer → valider → estimer → rendre → suivre → récupérer » sans connaître les clés. · Dépendances : T4.5. · Rebuild : non.

### Phase 5 — Artefacts et régénération par scène

**T5.1 — Scènes structurées dans le pipeline** (M2, M4)
- Acceptation : avec `scenes`, aucun appel LLM `generate_terms` ; le prompt final = gabarit profil + `visual_prompt` (ou brut) ; l'ordre est celui du tableau. Tests : `test_task.py` étendu.
- Dépendances : T1.4, T3.2. · Rebuild : oui.

**T5.2 — Artefacts nommés par scène + `meta.json`** (M3)
- Acceptation : `scenes/scene-01/{image.png, clip.mp4, meta.json}` avec prompt final, modèle, taille, hash, horodatage, coût estimé, statut ; manifeste mis à jour.
- Tests : arborescence, hash stable. · Dépendances : T5.1. · Rebuild : oui.

**T5.3 — Conservation de la narration et étape `assemble`** (M7, M8)
- Acceptation : `narration/tts-<hash>.mp3` conservé ; `assemble` refait uniquement clips→final à partir des artefacts **sans** TTS ni image ; invalidation si hash change.
- Tests : coût d'appel = 0 sur `assemble` (mocks stricts). · Dépendances : T5.2, T5.6. · Rebuild : oui.

**T5.4 — Régénérer une seule scène**
- Acceptation : `POST /episodes/{id}/scenes/{sid}/regenerate {visual_prompt?, seed?}` → un seul appel image, statut `stale` pour l'assemblage, puis `assemble` ; les autres scènes intactes (hash inchangés).
- Tests : un appel image exactement ; échec image → scène `failed`, autres intactes. · Dépendances : T5.3. · Rebuild : oui.

**T5.5 — Durées par scène alignées sur la narration**
- Acceptation : chaque scène reçoit la durée de sa (ses) phrase(s) (alignement char-proportionnel puis timestamps si disponibles) ; somme = narration ± tolérance ; plus de boucle de clips.
- Tests : durées synthétiques. · Risques : approximations sans timestamps réels. · Dépendances : T5.1. · Rebuild : oui.

**T5.6 — `tts_script`, prononciations, paramètres ElevenLabs** (M5, M6)
- Acceptation : dictionnaire par profil appliqué avec journal ; `model_id`/`voice_settings` transmis ; vitesse selon la doc **vérifiée** ; aperçu `tts-preview` (≤ 300 car.) ; retries avec backoff.
- Tests : corps de requête (mock) ; substitutions. · Dépendances : T1.4. · Rebuild : oui.

### Phase 6 — Musique et durée finale

**T6.1 — Trim, fade et durée finale** (M1) — *correctif prioritaire, indépendant de la couche profils*
- Acceptation : MP4 = narration + marge configurable (défaut 0,3 s) ; `abs(durée_conteneur − (narration + marge)) ≤ 1 image` avec une musique de 90 s ; fondu positionné à la fin. 
- Tests : fixtures audio/vidéo synthétiques (FFmpeg) ; régression sur le cas 83 s vs 62 s. · Risques : effets MoviePy 2.x (vérifier `subclipped`, `with_duration`). · Dépendances : T0.5. · Rebuild : oui.

**T6.2 — `MusicProvider` + adaptateurs + cache**
- Acceptation : interface §10.4 ; adaptateurs `local_library`, `uploaded`, `manual_import`, `elevenlabs`, `sonilo` ; clé de cache ; réutilisation d'une musique déjà générée.
- Tests : fakes, cache hit/miss. · Dépendances : T6.1. · Rebuild : oui.

**T6.3 — Politique musique par profil + import manuel (Suno)**
- Acceptation : `music.mode ∈ {none, library, uploaded, elevenlabs, manual_import}` ; import d'un MP3/WAV validé FFmpeg ; **aucune** dépendance à une API Suno.
- Tests : upload + assemblage. · Risques : droits du morceau (à vérifier par vous). · Dépendances : T6.2. · Rebuild : oui.

**T6.4 — Coût musique dans l'estimation** (table de tarifs, T3.5). · Dépendances : T6.2, T3.5. · Rebuild : oui.

### Phase 7 — Simplification des fournisseurs

**T7.1 — Registre + application de `enabled_providers`** (API v2 rejette ; Studio filtre). Acceptation : fournisseur désactivé → 400 explicite. Tests : matrice. Dépendances : T1.3. Rebuild : oui.

**T7.2 — Dépendances optionnelles** : `requirements.lodylands.txt` sans `dashscope`, `litellm`, `azure-cognitiveservices-speech` (et `faster-whisper` si Whisper non utilisé). Acceptation : l'application démarre et la suite (hors tests de ces fournisseurs) passe ; taille d'image mesurée avant/après. Risques : imports paresseux à valider. Dépendances : T0.4. Rebuild : oui.

**T7.3 — Masquage dans la WebUI upstream** (patch minimal `VIDEO_SOURCE_GROUPS`, liste TTS, `bgm_type`) *ou* décision d'abandonner `Main.py` au profit de Studio. Acceptation : seuls les fournisseurs activés sont visibles. Risques : conflits de merge. Dépendances : T7.1. Rebuild : oui.

**T7.4 — Base d'image** : évaluer le passage de `bullseye` à une base supportée (bookworm) + exécution non-root. Acceptation : rendu vidéo identique (ffprobe), permissions de `storage` maîtrisées. Risques : polices/FFmpeg différents. Rebuild : oui.

### Phase 8 — Contrôle qualité et récupération automatique

**T8.1 — Rapport QC** (`qc/report.json`) : durée conteneur vs narration, durée flux audio/vidéo, nombre de scènes vs manifeste, couverture des sous-titres (début/fin, chevauchements), présence de la piste musique et fondu, résolution/format, poids, coût réel vs estimé. Tests : fixtures. Dépendances : T5.3, T6.1. Rebuild : oui.

**T8.2 — Règles bloquantes/avertissements par profil** (durée hors cible, image dupliquée, texte détecté — à vérifier selon la faisabilité —, sous-titres manquants). Dépendances : T8.1. Rebuild : oui.

**T8.3 — Récupération automatique** : reprise depuis le dernier artefact valide ; retries bornés par étape, jamais de retry aveugle sur un appel payant *non confirmé* (règle déjà appliquée aux images, `material.py:1276+`). Tests : injection d'échecs. Dépendances : T5.3. Rebuild : oui.

**T8.4 — Métadonnées et publication contrôlée** : `social-metadata` par profil (titre/description/hashtags, mentions obligatoires), validation humaine, Upload-Post **opt-in** par profil. Risques : publication involontaire → défaut désactivé. Dépendances : T8.1. Rebuild : oui.

**T8.5 — Observabilité** : logs persistants (rotation), coûts cumulés par profil/épisode, alerte de budget. Rebuild : oui.

---

## 17. Questions restant à trancher

1. **Rotation des clés** : la WebUI est publique sans auth (dates d'exposition inconnues) — faut-il révoquer/renouveler les clés OpenAI et ElevenLabs maintenant ?
2. **Protection d'accès** souhaitée pour `video.lodylands.com` : basic-auth nginx, SSO (Cloudflare Access/Google), liste d'IP ?
3. **Exposition de l'API à ChatGPT/agents** : sous-domaine dédié (`api.…`) ou chemin ? clés par agent ? limites de débit ?
4. **Dépôt privé** : quelle organisation GitHub ? Fork public déconseillé (contient l'historique de config/prompts) — confirmer.
5. **Conserver la WebUI upstream** pour l'usage courant ou basculer sur une WebUI **Studio** dédiée (et masquer `Main.py`) ?
6. **Paramètres ElevenLabs** : vitesse, endpoint d'alignement (timestamps), dictionnaires de prononciation → **à vérifier dans la documentation officielle** avant T5.6.
7. **Tarifs** à saisir pour l'estimation (LLM, TTS, `gpt-image-2` par image/taille, ElevenLabs Music) ; budget max par vidéo par profil ?
8. **Modèle d'image** : `gpt-image-2` via `api.openai.com` confirmé ? (support de `seed`, taille, qualité **à vérifier** selon le modèle) ; besoin d'un second fournisseur d'image ?
9. **Durée des scènes** : 5-7 s fixes ou pilotée par les phrases (T5.5) ? tolérance d'écart total ?
10. **Musique** : ElevenLabs Music (vidéo→musique, durée non maîtrisée) ou bibliothèque/import (Suno manuel) comme défaut ? Réutilisation d'un « thème de série » ? Droits d'usage commercial des morceaux Suno **à vérifier**.
11. **Suno** : accepter uniquement l'import manuel, ou évaluer plus tard une API officielle (**à vérifier**) ? (Pas d'API tierce ni d'automatisation navigateur recommandées.)
12. **Publication** : Upload-Post envisagé ? plateformes cibles, validation humaine obligatoire ?
13. **OFox pour la vidéo** : usage prévu (quels modèles/durées), conformité et coûts **à vérifier** ; à traiter après la phase 5.
14. **LLM par défaut** : passer `llm_provider` sur `openai` (la valeur actuelle `moonshot` a une clé vide) et quel modèle ?
15. **Mentions obligatoires / règles de sécurité éditoriale** de chaque chaîne (texte exact à intégrer dans les profils).
16. **Persistance des tâches** : activer Redis (service supplémentaire) ou état fichier (`task.json`) ?
17. **Accès Docker** pour l'utilisateur d'exploitation (groupe `docker`) afin de compléter les relevés manquants (digest, env, health) — acceptable ?

---

## 18. Annexes techniques

### A1. Liste des routes API (relevée sur `GET /openapi.json`, API live v1.3.7)

| Méthode | Chemin | Résumé | Corps | Paramètres |
|---|---|---|---|---|
| GET | `/ping` | Ping | — |  |
| POST | `/api/v1/videos` | Generate a short video | TaskVideoRequest | x-api-key |
| POST | `/api/v1/subtitle` | Generate subtitle only | SubtitleRequest | x-api-key |
| POST | `/api/v1/audio` | Generate audio only | AudioRequest | x-api-key |
| GET | `/api/v1/tasks` | Get all tasks | — | page, page_size, x-api-key |
| GET | `/api/v1/tasks/{task_id}` | Query task status | — | task_id*, x-api-key |
| DELETE | `/api/v1/tasks/{task_id}` | Delete a generated short video task | — | task_id*, x-api-key |
| GET | `/api/v1/musics` | Retrieve local BGM files | — | x-api-key |
| POST | `/api/v1/musics` | Upload a background music file | Body_upload_bgm_file_api_v1_musics_post | x-api-key |
| GET | `/api/v1/video_materials` | Retrieve local video materials | — | x-api-key |
| POST | `/api/v1/video_materials` | Upload the video material file to the local videos directory | Body_upload_video_material_file_api_v1_video_materials_post | x-api-key |
| GET | `/api/v1/stream/{file_path}` | Stream Video | — | file_path*, x-api-key |
| GET | `/api/v1/download/{file_path}` | Download Video | — | file_path*, x-api-key |
| POST | `/api/v1/scripts` | Create a script for the video | VideoScriptRequest | x-api-key |
| POST | `/api/v1/terms` | Generate video terms based on the video script | VideoTermsRequest | x-api-key |
| POST | `/api/v1/social-metadata` | Generate social publishing metadata | VideoSocialMetadataRequest | x-api-key |

Hors OpenAPI : `GET /tasks/**` (fichiers de tâche, protégé par `x-api-key` si configuré) et `GET /` (page statique `resource/public/index.html`). `*` = requis. `x-api-key` est optionnel tant que `app.api_key` est vide.

### A2. Schéma JSON complet de `VideoParams` (= `TaskVideoRequest`, extrait de l'OpenAPI **du déploiement en cours**)

Contrainte importante : Pydantic est en mode par défaut → **les champs inconnus sont ignorés sans erreur** (pas de `additionalProperties:false`). Le schéma `TaskVideoRequest` :

```json
{
  "properties": {
    "video_subject": {
      "type": "string",
      "title": "Video Subject"
    },
    "video_script": {
      "type": "string",
      "title": "Video Script",
      "default": ""
    },
    "video_terms": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "items": {},
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Video Terms"
    },
    "video_aspect": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/VideoAspect"
        },
        {
          "type": "null"
        }
      ],
      "default": "9:16"
    },
    "video_fit_mode": {
      "$ref": "#/components/schemas/VideoFitMode",
      "default": "cover"
    },
    "video_concat_mode": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/VideoConcatMode"
        },
        {
          "type": "null"
        }
      ],
      "default": "random"
    },
    "video_transition_mode": {
      "anyOf": [
        {
          "$ref": "#/components/schemas/VideoTransitionMode"
        },
        {
          "type": "null"
        }
      ]
    },
    "video_clip_duration": {
      "type": "integer",
      "minimum": 1.0,
      "title": "Video Clip Duration",
      "default": 5
    },
    "video_clip_speed": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Video Clip Speed",
      "default": 1.0
    },
    "match_materials_to_script": {
      "type": "boolean",
      "title": "Match Materials To Script",
      "default": false
    },
    "video_count": {
      "type": "integer",
      "minimum": 1.0,
      "title": "Video Count",
      "default": 1
    },
    "video_source": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Video Source",
      "default": "pexels"
    },
    "video_materials": {
      "anyOf": [
        {
          "items": {
            "$ref": "#/components/schemas/MaterialInfo"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Video Materials"
    },
    "custom_audio_file": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Custom Audio File"
    },
    "video_language": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Video Language",
      "default": ""
    },
    "voice_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Voice Name",
      "default": ""
    },
    "voice_volume": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Voice Volume",
      "default": 1.0
    },
    "voice_rate": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Voice Rate",
      "default": 1.0
    },
    "bgm_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Bgm Type",
      "default": "random"
    },
    "bgm_file": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Bgm File",
      "default": ""
    },
    "bgm_volume": {
      "anyOf": [
        {
          "type": "number"
        },
        {
          "type": "null"
        }
      ],
      "title": "Bgm Volume",
      "default": 0.2
    },
    "video_music_prompt": {
      "type": "string",
      "maxLength": 2000,
      "title": "Video Music Prompt",
      "default": ""
    },
    "sonilo_bgm_prompt": {
      "type": "string",
      "maxLength": 2000,
      "title": "Sonilo Bgm Prompt",
      "default": ""
    },
    "subtitle_enabled": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "null"
        }
      ],
      "title": "Subtitle Enabled",
      "default": true
    },
    "subtitle_position": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Subtitle Position",
      "default": "bottom"
    },
    "subtitle_display_mode": {
      "type": "string",
      "enum": [
        "sentence",
        "word_by_word"
      ],
      "title": "Subtitle Display Mode",
      "default": "sentence"
    },
    "subtitle_animation": {
      "type": "string",
      "enum": [
        "none",
        "pop_spring"
      ],
      "title": "Subtitle Animation",
      "default": "none"
    },
    "custom_position": {
      "type": "number",
      "title": "Custom Position",
      "default": 70.0
    },
    "font_name": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Font Name",
      "default": "STHeitiMedium.ttc"
    },
    "text_fore_color": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Text Fore Color",
      "default": "#FFFFFF"
    },
    "text_background_color": {
      "anyOf": [
        {
          "type": "boolean"
        },
        {
          "type": "string"
        }
      ],
      "title": "Text Background Color",
      "default": false
    },
    "rounded_subtitle_background": {
      "type": "boolean",
      "title": "Rounded Subtitle Background",
      "default": false
    },
    "font_size": {
      "type": "integer",
      "title": "Font Size",
      "default": 60
    },
    "stroke_color": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Stroke Color",
      "default": "#000000"
    },
    "stroke_width": {
      "type": "number",
      "title": "Stroke Width",
      "default": 1.5
    },
    "n_threads": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "title": "N Threads",
      "default": 2
    },
    "paragraph_number": {
      "type": "integer",
      "maximum": 10.0,
      "minimum": 1.0,
      "title": "Paragraph Number",
      "default": 1
    },
    "video_script_prompt": {
      "type": "string",
      "maxLength": 2000,
      "title": "Video Script Prompt",
      "default": ""
    },
    "custom_system_prompt": {
      "type": "string",
      "maxLength": 8000,
      "title": "Custom System Prompt",
      "default": ""
    }
  },
  "type": "object",
  "required": [
    "video_subject"
  ],
  "title": "TaskVideoRequest"
}
```

Schémas référencés :

```json
{
  "VideoAspect": {
    "type": "string",
    "enum": [
      "16:9",
      "9:16",
      "1:1"
    ],
    "title": "VideoAspect"
  },
  "VideoFitMode": {
    "type": "string",
    "enum": [
      "cover",
      "contain"
    ],
    "title": "VideoFitMode",
    "description": "How source clips with a different aspect ratio fill the output canvas."
  },
  "VideoConcatMode": {
    "type": "string",
    "enum": [
      "random",
      "sequential"
    ],
    "title": "VideoConcatMode"
  },
  "VideoTransitionMode": {
    "type": "string",
    "enum": [
      "None",
      "Shuffle",
      "FadeIn",
      "FadeOut",
      "SlideIn",
      "SlideOut",
      "ZoomIn",
      "ZoomOut"
    ],
    "title": "VideoTransitionMode"
  },
  "MaterialInfo": {
    "properties": {
      "provider": {
        "type": "string",
        "title": "Provider",
        "default": "pexels"
      },
      "url": {
        "type": "string",
        "title": "Url",
        "default": ""
      },
      "duration": {
        "type": "integer",
        "title": "Duration",
        "default": 0
      },
      "source_info": {
        "anyOf": [
          {
            "additionalProperties": true,
            "type": "object"
          },
          {
            "type": "null"
          }
        ],
        "title": "Source Info"
      }
    },
    "type": "object",
    "title": "MaterialInfo"
  }
}
```

Notes : `video_transition_mode` accepte `null`/omis pour « aucune transition » (l'énumération publie la chaîne `"None"` mais la valeur nulle est la valeur réelle). Les défauts `subtitle_position`, `subtitle_display_mode`, `subtitle_animation`, `custom_position` sont **calculés à l'import depuis `config.ui`** du processus API (d'où la dérive constatée). Champs **absents** du schéma (donc non pilotables par requête) : modèle/taille/gabarit d'image, modèle TTS, réglages de voix, fournisseur de sous-titres, publication, `script TTS`, scènes structurées.

### A3. Exemple complet `POST /api/v1/videos` (valide, **sans clé**, **non exécuté**)

Validé localement contre le schéma de l'annexe A2 (`jsonschema`, aucun appel réseau). Le script est un texte **illustratif**. Trois points d'attention :
1. La requête ne peut pas porter le modèle, la taille ni le **gabarit de prompt d'image** : ils viennent de `config.app` (aujourd'hui **contaminé**, cf. §1 n°6). Envoyer cette requête en l'état génèrerait des images avec le gabarit « broadcast » global.
2. `video_terms` est une **liste** : chaque prompt (virgules incluses) reste **atomique**. En chaîne de caractères, il serait découpé sur les virgules.
3. `voice_rate` est sans effet avec ElevenLabs ; `video_clip_duration` est un entier uniforme (6 s ≈ 8 images pour ~50 s ; ici seulement 6 prompts → la vidéo **boucle** ses clips si la narration dépasse 36 s, cf. §8). Musique désactivée (`bgm_type:""`) ; pour ElevenLabs Music : `"bgm_type":"elevenlabs"` + `video_music_prompt` (≤ 1000 car.), **avec le risque de dépassement de durée** tant que T6.1 n'est pas fait.

```bash
# NE PAS EXÉCUTER dans l'audit — modèle d'appel (API locale, clé éventuelle via en-tête)
curl -sS -X POST http://127.0.0.1:8080/api/v1/videos \
  -H 'Content-Type: application/json' \
  -H "x-api-key: $MPT_API_KEY" \
  --data @episode-crypto-000.json
```

```json
{
  "video_subject": "Pourquoi je lance cette série",
  "video_script": "Le Web3, ce n'est pas magique : c'est une technologie qui permet de posséder des objets numériques dans un jeu. Voyons simplement comment ça marche. D'abord, une blockchain est un registre partagé, difficile à modifier. Ensuite, dans certains jeux, un objet peut être inscrit sur ce registre. Cela change les usages, mais aussi les risques : sécurité, arnaques, volatilité. Ici, on distingue toujours la technique, les usages, les risques et la spéculation. Aucune promesse de gain : seulement de quoi comprendre avant de décider.",
  "video_terms": [
    "Dark modern digital workspace, glowing cyan and blue light, abstract shared ledger of connected blocks, no text, no logo",
    "A gamer at a desk in a dim room, cyan and blue ambient light, holographic game item floating above the keyboard, no text, no brand",
    "Chain of glowing translucent cubes linked together on a dark background, cyan highlights, clean minimal composition, no lettering",
    "A game character holding a glowing digital item inside a stylized fantasy world, dark blue palette, cyan accents, no text, no logo",
    "Split scene: a secure padlock on one side and a warning triangle on the other, dark background, cyan and blue tones, no words",
    "Calm neutral scene of a person taking notes before a decision, dark modern style, soft cyan light, no text, no brand"
  ],
  "video_aspect": "9:16",
  "video_fit_mode": "cover",
  "video_concat_mode": "sequential",
  "video_transition_mode": null,
  "video_clip_duration": 6,
  "video_clip_speed": 1.0,
  "match_materials_to_script": true,
  "video_count": 1,
  "video_source": "openai_image",
  "video_materials": null,
  "custom_audio_file": null,
  "video_language": "fr-FR",
  "voice_name": "elevenlabs:jGpnMdbhtKgQbVrYezOx:Kev - Young, Dynamic and Bright",
  "voice_volume": 1.0,
  "voice_rate": 1.0,
  "bgm_type": "",
  "bgm_file": "",
  "bgm_volume": 0.2,
  "video_music_prompt": "",
  "subtitle_enabled": true,
  "subtitle_position": "bottom",
  "subtitle_display_mode": "sentence",
  "subtitle_animation": "none",
  "custom_position": 70.0,
  "font_name": "MicrosoftYaHeiBold.ttc",
  "text_fore_color": "#FFFFFF",
  "text_background_color": "#101010",
  "rounded_subtitle_background": true,
  "font_size": 58,
  "stroke_color": "#000000",
  "stroke_width": 1.8,
  "n_threads": 2,
  "paragraph_number": 1,
  "video_script_prompt": "",
  "custom_system_prompt": ""
}
```

Réponse attendue : `{"status":200,"message":"success","data":{"task_id":"<uuid>","request_id":"<uuid>","params":{…}}}` puis `GET /api/v1/tasks/<task_id>` jusqu'à `state==1` (terminé) ou `-1` (échec, voir `failed_stage`/`error`).

### A4. Exemple de profil multichaîne (`profiles/lodylands_crypto_web3.json`) — **aucun secret**

Les valeurs reprennent celles déjà présentes dans `config.toml` (voix, modèle TTS, sous-titres) et les besoins de la mission. `budget.max_per_video` est laissé à `null` : **à fixer par vous**. Les identifiants de voix ne sont pas des secrets. Le validateur (T2.1) refuse toute clé se terminant par `api_key`, `api_keys`, `api_token`, `access_key`, `secret_key`, `speech_key`.

```json
{
  "schema_version": "1.0",
  "profile_id": "lodylands_crypto_web3",
  "display_name": "LodyLands — Crypto / Web3 Gaming",
  "language": "fr-FR",
  "format": {
    "aspect": "9:16",
    "resolution": "1080x1920",
    "target_duration_s": [
      45,
      65
    ]
  },
  "editorial": {
    "style": "pédagogique, naturel, dynamique",
    "tone": "accessible au débutant total, orienté Web3 gaming rapidement",
    "audience_assumption": "débutant complet",
    "safety_rules": [
      "aucune promesse financière ni conseil d'investissement",
      "distinguer technique, usages, risques et spéculation",
      "ne pas présenter un jeton comme un gain assuré"
    ],
    "mandatory_mentions": [
      "Ceci n'est pas un conseil financier."
    ],
    "platforms": [
      "youtube_shorts",
      "tiktok",
      "instagram_reels"
    ]
  },
  "script": {
    "llm_provider": "openai",
    "system_prompt_ref": "prompts/crypto_web3_script.md",
    "paragraphs": 1
  },
  "tts": {
    "provider": "elevenlabs",
    "voice_id": "jGpnMdbhtKgQbVrYezOx",
    "voice_name": "Kev - Young, Dynamic and Bright",
    "model_id": "eleven_multilingual_v2",
    "speed": 1.0,
    "voice_settings": {
      "stability": 0.5,
      "similarity_boost": 0.75,
      "style": 0.0,
      "use_speaker_boost": true
    },
    "pronunciations": {
      "Web3": "Web trois",
      "NFT": "N F T",
      "BTC": "Bitcoin",
      "DeFi": "dé-faï"
    }
  },
  "visual": {
    "provider": "openai_image",
    "model": "gpt-image-2",
    "size": "auto_by_aspect",
    "prompt_template": "{term}. Dark modern aesthetic, deep navy and black background, cyan and electric blue glow, clean cinematic composition. No text, no letters, no logos, no brands, no watermarks.",
    "art_direction": {
      "palette": [
        "#05080F",
        "#0B1B33",
        "#00E5FF",
        "#1E90FF"
      ],
      "mood": "sombre, moderne, technologique"
    },
    "forbid": [
      "text",
      "logo",
      "brand",
      "watermark"
    ],
    "scene": {
      "target_seconds": [
        5,
        7
      ],
      "order": "follows_script",
      "transition": null
    }
  },
  "subtitles": {
    "enabled": true,
    "display_mode": "sentence",
    "animation": "none",
    "position": "bottom",
    "font_name": "MicrosoftYaHeiBold.ttc",
    "font_size": 58,
    "text_color": "#FFFFFF",
    "stroke_color": "#000000",
    "stroke_width": 1.8,
    "background": {
      "enabled": true,
      "color": "#101010",
      "rounded": true
    }
  },
  "music": {
    "mode": "elevenlabs",
    "volume": 0.2,
    "prompt": "Subtle modern background music for a short educational technology video. No vocals.",
    "tail_margin_s": 0.3,
    "fade_out_s": 2.0,
    "allow_disable": true
  },
  "budget": {
    "max_per_video": null,
    "currency": "EUR"
  },
  "publication": {
    "enabled": false,
    "require_human_approval": true
  }
}
```

### A5. Exemple de preset exporté représentatif (sans secret)

Construit à partir de la logique de `_build_settings_preset_payload` (`webui/Main.py:2840-2863`) et du modèle `VideoParams` — **ce n'est pas un export réel** (je n'ai pas exécuté la WebUI). Noter : `video_materials`, `custom_audio_file`, `bgm_file` sont exclus ; **aucun** réglage `openai_image_*`, `elevenlabs.model_id`, `subtitle_provider`, `tts_server` n'est présent ; et `video_terms` y apparaît comme **une seule chaîne jointe par « , »**, ce qui recasse les prompts contenant des virgules à l'import (chaque virgule interne du prompt devient une frontière de scène : ici 6 prompts deviennent 28 « scènes », voir le calcul ci-dessous).

```json
{
  "schema": "moneyprinterturbo.settings-preset",
  "version": 1,
  "app_version": "1.3.7",
  "params": {
    "video_subject": "Pourquoi je lance cette série",
    "video_script": "Le Web3, ce n'est pas magique : c'est une technologie qui permet de posséder des objets numériques dans un jeu. Voyons simplement comment ça marche. D'abord, une blockchain est un registre partagé, difficile à modifier. Ensuite, dans certains jeux, un objet peut être inscrit sur ce registre. Cela change les usages, mais aussi les risques : sécurité, arnaques, volatilité. Ici, on distingue toujours la technique, les usages, les risques et la spéculation. Aucune promesse de gain : seulement de quoi comprendre avant de décider.",
    "video_terms": "Dark modern digital workspace, glowing cyan and blue light, abstract shared ledger of connected blocks, no text, no logo, A gamer at a desk in a dim room, cyan and blue ambient light, holographic game item floating above the keyboard, no text, no brand, Chain of glowing translucent cubes linked together on a dark background, cyan highlights, clean minimal composition, no lettering, A game character holding a glowing digital item inside a stylized fantasy world, dark blue palette, cyan accents, no text, no logo, Split scene: a secure padlock on one side and a warning triangle on the other, dark background, cyan and blue tones, no words, Calm neutral scene of a person taking notes before a decision, dark modern style, soft cyan light, no text, no brand",
    "video_aspect": "9:16",
    "video_fit_mode": "cover",
    "video_concat_mode": "sequential",
    "video_transition_mode": null,
    "video_clip_duration": 6,
    "video_clip_speed": 1.0,
    "match_materials_to_script": true,
    "video_count": 1,
    "video_source": "openai_image",
    "video_language": "fr-FR",
    "voice_name": "elevenlabs:jGpnMdbhtKgQbVrYezOx:Kev - Young, Dynamic and Bright",
    "voice_volume": 1.0,
    "voice_rate": 1.0,
    "bgm_type": "",
    "bgm_volume": 0.2,
    "video_music_prompt": "",
    "subtitle_enabled": true,
    "subtitle_position": "bottom",
    "subtitle_display_mode": "sentence",
    "subtitle_animation": "none",
    "custom_position": 70.0,
    "font_name": "MicrosoftYaHeiBold.ttc",
    "text_fore_color": "#FFFFFF",
    "text_background_color": "#101010",
    "rounded_subtitle_background": true,
    "font_size": 58,
    "stroke_color": "#000000",
    "stroke_width": 1.8,
    "n_threads": 2,
    "paragraph_number": 1,
    "video_script_prompt": "",
    "custom_system_prompt": "",
    "sonilo_bgm_prompt": ""
  }
}
```

Vérification par calcul : `re.split(r"[,，]", video_terms)` (règle de `task.py:327`) sur ce preset donne **28** termes au lieu de 6.

### A6. Exemple de manifeste d'épisode v1.0

Extension de la structure proposée dans la mission (un seul objet `scenes[]` montré pour la lisibilité). Statuts de scène : `planned → generated → validated → failed → stale`. Tout hash différent d'un artefact ⇒ `stale`.

```json
{
  "schema_version": "1.0",
  "profile_id": "lodylands_crypto_web3",
  "profile_version": "1.0",
  "episode_id": "crypto-000",
  "created_at": "2026-09-20T22:00:00+02:00",
  "subject": "Pourquoi je lance cette série",
  "display_script": "Le Web3, ce n'est pas magique… (texte affiché, ponctuation éditoriale)",
  "tts_script": "Le Web trois, ce n'est pas magique… (texte lu, acronymes développés)",
  "script_hash": "sha256:<calculé>",
  "scenes": [
    {
      "id": "scene-01",
      "narration": "Le Web3, ce n'est pas magique.",
      "visual_prompt": "Dark modern digital workspace, glowing cyan and blue light, abstract shared ledger of connected blocks",
      "prompt_mode": "template",
      "duration_target": 6.0,
      "provider": "openai_image",
      "model": "gpt-image-2",
      "size": "1024x1536",
      "seed": null,
      "status": "planned",
      "artifacts": {
        "image": null,
        "clip": null,
        "meta": null
      },
      "cost_estimate": {
        "currency": "EUR",
        "amount": null
      }
    }
  ],
  "audio": {
    "provider": "elevenlabs",
    "voice_id": "jGpnMdbhtKgQbVrYezOx",
    "model_id": "eleven_multilingual_v2",
    "file": null,
    "duration_s": null,
    "tts_hash": null,
    "status": "planned"
  },
  "subtitles": {
    "source": "display_script",
    "file": null,
    "style_ref": "profile",
    "status": "planned"
  },
  "music": {
    "mode": "elevenlabs",
    "prompt": "Subtle modern background music…",
    "file": null,
    "cache_key": null,
    "tail_margin_s": 0.3,
    "fade_out_s": 2.0,
    "status": "planned"
  },
  "budget": {
    "max": null,
    "estimated": null,
    "spent": 0.0,
    "currency": "EUR"
  },
  "publication": {
    "title": null,
    "caption": null,
    "hashtags": [],
    "mandatory_mentions": [
      "Ceci n'est pas un conseil financier."
    ],
    "platforms": [
      "youtube_shorts"
    ],
    "status": "draft"
  },
  "qc": {
    "report": null,
    "status": "pending"
  },
  "output": {
    "task_id": null,
    "final_video": null,
    "duration_s": null,
    "status": "planned"
  }
}
```

### A7. Inventaire des fournisseurs
Voir le tableau détaillé et le classement V/M/O/D/S au **§11.1**. Compléments (noms de configuration relevés, sans valeur) :
- LLM (26) : `moonshot, openai, anthropic, gemini, deepseek, qwen, azure, volcengine, grok, minimax, mimo, shengsuanyun, apimart, cloudflare, modelscope, aihubmix, aimlapi, evolink, openrouter, api_route, ollama, claude_code, oneapi, litellm, groq, pollinations` (`app/models/llm_provider.py`, `LLM_PROVIDER_REGISTRY`, lignes 198-449). Chaque entrée : `<id>_api_key`, `<id>_base_url`, `<id>_model_name` dans `[app]`.
- TTS : `azure-tts-v1` (Edge), `azure-tts-v2`, `siliconflow`, `gemini-tts`, `mimo-tts`, `minimax-tts`, `elevenlabs`, `chatterbox`, `kokoro`, `fish_audio`, `voxcpm` (`webui/Main.py:5482-5535`, `voice.py:_single_tts`).
- Sources visuelles (`VIDEO_SOURCE_GROUPS`, `Main.py:116-130`) : stock `pexels, pixabay, coverr` ; IA vidéo `metaso_minimax, ofox, loomloom, volcengine_seedance, wavespeed` ; IA image `openai_image` ; `local`.
- Musique : `random, preset, custom, sonilo, elevenlabs` (`Main.py:6042+`, `task._VIDEO_MUSIC_PROVIDERS`). **Suno : absent.**
- Autres : `twelvelabs` (rerank), `upload_post` (publication), Whisper (`faster-whisper`), Redis.
- Statut des clés dans `config.toml` (constat) : **présentes** = `app.openai_api_key`, `app.openai_image_api_keys`, `elevenlabs.api_key` ; **toutes les autres vides**.

### A8. Cartographie des volumes Docker

| Hôte | Conteneur | Mode | Propriétaire/droits observés | Remarque |
|---|---|---|---|---|
| `/srv/moneyprinterturbo/config.toml` | `/MoneyPrinterTurbo/config.toml` | rw, **bind monofichier** (remplacement atomique impossible → écriture en place, `EBUSY`) | `ubuntu:ubuntu` `0664` | Contient les secrets ; réécrit en entier par la WebUI ; **non monté en lecture seule** |
| `/srv/moneyprinterturbo/storage` | `/MoneyPrinterTurbo/storage` | rw | `root:root` `0755` ; 502 Mo ; sous-dossiers `tasks/` (9 tâches), `local_videos/`, `temp/` | `script.json` et BGM en `0600 root` |
| — (dans l'image) | `/MoneyPrinterTurbo/{app,webui,resource,…}` | — | — | `resource/songs` (29 fichiers) et `resource/fonts` livrés **dans l'image** ; le clone n'est **pas** monté (compose « release ») |
| — | `/MoneyPrinterTurbo/storage/bgm` (créé à la volée) | dans `storage` | — | BGM uploadées |

Volumes à **ajouter** (T0.4/T1.x) : `./profiles:/MoneyPrinterTurbo/profiles:ro`, `./lodylands.toml:/MoneyPrinterTurbo/lodylands.toml:ro`, `env_file: ./.env.lodylands` (0600, hors dépôt), `storage/episodes` (dans le volume `storage` existant). Comparaison : `docker-compose.yml` (build) monte `./:/MoneyPrinterTurbo` (tout le dépôt, y compris `config.toml` et `storage`).

### A9. Structure de dossiers proposée

```
/srv/lodylands-mpt/                      # dépôt privé dérivé (origin privé, upstream = harry0703)
├── app/                                  # upstream (patchs M1-M11 isolés)
│   └── lodylands/                        # NOUVEAU — couche multichaîne
│       ├── profiles.py  manifest.py  compiler.py  providers.py
│       ├── music.py     pronunciation.py  estimate.py  qc.py  scenes.py
│       ├── secrets.py   system_config.py
│       └── api.py                        # routeur /api/v2
├── webui/
│   ├── Main.py                           # upstream (inchangé ou patch minimal)
│   └── Studio.py                         # NOUVEAU — UI simple multichaîne
├── profiles/                             # niveau D — versionné, SANS secret
│   ├── lodylands_crypto_web3.json
│   ├── broadcast_av.json
│   └── lodyquests_minecraft.json
├── prompts/                              # gabarits de script/visuels référencés par les profils
├── schemas/                              # profile.schema.json, manifest.schema.json
├── lodylands.toml                        # niveau B — config système (non secret)
├── test/lodylands/                       # tests de la couche
├── docker-compose.lodylands.yml  Dockerfile.lodylands
└── .gitignore                            # .env*, storage/, config.toml, profiles/*.local.json

# Hors dépôt (hôte)
/srv/moneyprinterturbo-data/
├── .env.lodylands                        # niveau A — secrets (0600)
├── config.toml                           # hérité (clés vidées progressivement)
└── storage/
    ├── tasks/<task_id>/                  # upstream conservé
    └── episodes/<episode_id>/            # NOUVEAU — niveaux D/E
        ├── manifest.json
        ├── narration/    tts-<hash>.mp3  narration.json  preview-<hash>.mp3
        ├── scenes/scene-01/  image.png  clip.mp4  meta.json
        ├── subtitles/    subtitle.srt
        ├── music/        <cache_key>.mp3  music.json
        ├── output/       final.mp4  combined.mp4
        └── qc/           report.json
```

### A10. Fichiers qu'il faudra probablement modifier (moteur upstream)

| Fichier | Raison | Ticket |
|---|---|---|
| `app/services/video.py` (`generate_video` l.1482-1506) | Trim/fade/durée finale de la musique | T6.1 |
| `app/models/schema.py` (`VideoParams`) | Champs optionnels (overrides image/TTS, `tts_script`, `scenes`, politique musique) | T1.4, T5.1, T5.6 |
| `app/services/material.py` (l.1109-1165, 1365-1562) | Overrides image, noms d'artefacts par scène | T1.4, T5.2 |
| `app/services/task.py` (`generate_terms` l.312, `generate_audio` l.481, `_run_pipeline` l.1353, nouvelle `assemble`) | Scènes, TTS script, assemblage depuis artefacts, chemin d'audio d'épisode (`resolve_custom_audio_file` l.363) | T5.1, T5.3, T5.6 |
| `app/services/voice.py` (`_single_tts` l.660, `elevenlabs_tts` l.2023) | Modèle, réglages de voix, vitesse, backoff | T1.4, T5.6 |
| `app/services/state.py` | Persistance `task.json` | T4.3 |
| `app/router.py` | Inclusion du routeur v2 (1 ligne) | T2.4 |
| `app/config/config.py` | Lecture des secrets depuis l'environnement | T1.2 |
| `webui/Main.py` (optionnel) | Flag de désactivation de l'export de clés ; séparateur `\n` des termes ; masquage de fournisseurs | T1.2, T7.3 |
| `Dockerfile` / nouveau `Dockerfile.lodylands`, `docker-compose.lodylands.yml`, `requirements.lodylands.txt` | Build reproductible, dépendances optionnelles | T0.4, T7.2 |
| `.gitignore` | `.env*`, `profiles/*.local.json` | T0.3 |
| `test/services/test_video.py`, `test_material_openai_image.py`, `test_voice.py`, `test_task.py`, `test_schema.py` | Tests de caractérisation puis de non-régression | T0.5 et phases 1-6 |

Configuration hôte (hors dépôt) : `/etc/nginx/sites-available/video.lodylands.com` (authentification, T0.2), `config.toml` (nettoyage T1.1, `chmod 600`, `api_key`).

### A11. Commandes de diagnostic utilisées (toutes en lecture seule)

| Domaine | Commandes |
|---|---|
| Git | `git branch -a`, `git log -1`, `git remote -v`, `git tag --sort=-creatordate`, `git status --porcelain [--ignored]`, `git rev-parse origin/main`, `git rev-list --left-right --count HEAD...origin/main`, `git describe --tags`, `git log --author=ReC82`, `git ls-files` |
| Recherche | `grep -rIil suno …`, `grep -rIl -i lodyland …`, `grep -n …` sur `app/`, `webui/`, `cli.py`, `test/` |
| Docker (sans socket) | `docker ps` (→ *permission denied*), `ps -o pid,user,lstart,args -p …`, `ss -ltnp`, `cat /proc/<pid>/mountinfo` (colonnes de montage uniquement), `cat /proc/<pid>/cgroup` |
| Reverse proxy | lecture de `/etc/nginx/sites-enabled/video.lodylands.com` (chemins de certificats masqués) ; `curl -s https://video.lodylands.com/`, `/_stcore/health`, `/api/v1/tasks` (GET, aucune donnée sensible) |
| API locale (GET uniquement) | `curl http://127.0.0.1:8080/ping`, `/openapi.json`, `http://127.0.0.1:8501/_stcore/health` — **aucun POST, aucune tâche créée** |
| Configuration | lecture de `config.toml` via un script `tomllib` qui n'affiche que les noms de clés et `présent`/`vide` pour les valeurs sensibles (URL réduites au nom d'hôte) |
| Artefacts | `ls -la storage/…` ; `ffprobe -show_entries stream=duration / format=duration` sur les `final-1.mp4` et `audio.mp3` lisibles |
| Validation d'exemple | `jsonschema.validate` de l'exemple A3 contre le schéma OpenAPI (hors réseau) |
Non exécutés : `docker inspect/images/compose`, `pytest`, tout build, tout redémarrage, tout appel de génération ou fournisseur.
