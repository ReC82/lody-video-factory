# Authentification admin + remplacement de clé API depuis l'interface (ticket #28)

Complète #25/#26 : la page « Paramètres système » (voir [`docs/lody-secrets.md`](lody-secrets.md)) devient
utilisable, protégée par une vraie authentification administrateur — pas seulement un drapeau désactivé.

## Mécanisme retenu, et pourquoi

**Constat vérifié avant de choisir** : aucune authentification n'existe nulle part dans ce dépôt pour Lody (ni
nginx, ni code applicatif) — confirmé par recherche, et par `docs/audits/moneyprinterturbo_initial_audit.md` qui
fait le même constat pour l'ancienne WebUI (déjà arrêtée).

**Un `auth_request` nginx classique ne peut pas protéger une seule vue** de Lody : la page « Paramètres système »
n'est pas une route HTTP distincte, c'est une vue Streamlit (`?vue=systeme`) à l'intérieur du **même** WebSocket
que tout le reste du site. nginx ne voit qu'une connexion WebSocket partagée, pas les vues qu'elle sert.

**Solution retenue** : un petit service dédié, `lody-auth` (conteneur séparé, bibliothèque standard uniquement,
aucune dépendance neuve), qui gère la connexion (page HTML séparée, hors Streamlit) et pose un cookie de
session. Streamlit lit ce cookie (`st.context.cookies`, disponible en lecture seule depuis la version 1.59) et
revérifie sa validité auprès de `lody-auth` — **à chaque affichage de la page ET avant chaque écriture** — via
le réseau Docker interne, jamais via nginx.

```
Navigateur ──HTTPS──▶ nginx ──▶ /lody-auth/login, /logout (public, limité en débit)
                          │
                          └──▶ / (Streamlit, port 8601, inchangé)
                                  │
                                  │ st.context.cookies → jeton de session
                                  ▼
                          lody-auth:8602/verify   (réseau Docker interne UNIQUEMENT,
                                                    jamais proxifié publiquement par nginx)
```

## Ce que chaque brique peut et ne peut jamais faire

- **nginx** : termine le TLS (déjà en place), limite le débit sur `/lody-auth/login` (anti-force-brute, défense
  en profondeur), bloque explicitement `/lody-auth/verify` (`return 404`). Ne connaît jamais un mot de passe ni
  une clé de fournisseur.
- **`lody-auth`** (conteneur séparé, uid 10002, `cap_drop: ALL`) : lit `secrets/admin-auth.json` (0640, groupe
  `lody-secrets`, **jamais** dans Git ni dans SQLite) en lecture seule ; ne modifie jamais ce fichier lui-même
  (voir `scripts/lody-auth-set-password.sh`, lancé à la main sur l'hôte). Sessions en mémoire du processus
  uniquement — **aucune persistance** : un redémarrage du service déconnecte tout le monde, accepté pour un
  déploiement mono-administrateur.
- **Lody** (conteneur `lody-ui`) : ne voit jamais le mot de passe ni le fichier `admin-auth.json` ; ne connaît
  que le jeton de session (opaque) reçu via cookie, vérifié par un appel interne à `lody-auth:8602/verify`.

## Mot de passe : hash robuste, jamais en clair

`hashlib.scrypt` (bibliothèque standard, aucune dépendance neuve), paramètres recommandés par l'OWASP quand
bcrypt/argon2 ne sont pas disponibles (N=16384, r=8, p=1). Comparaison en temps constant
(`hmac.compare_digest`). Un identifiant inconnu et un mot de passe incorrect renvoient exactement le même
message (`test_unknown_username_gives_the_same_generic_message_as_wrong_password`) : aucune énumération de
compte possible.

## Session : cookie, expiration, CSRF

- Cookie `lody_admin_session` : `Secure` (HTTPS uniquement), `HttpOnly` (invisible à tout JavaScript, y compris
  en cas de faille XSS ailleurs sur le site), `SameSite=Strict` — cette dernière propriété est la protection
  **principale** contre le CSRF sur les écritures : un site tiers ne peut jamais faire attacher ce cookie à une
  requête, y compris à la connexion WebSocket de Streamlit.
- Expiration par inactivité (30 min) ET plafond absolu (12 h), même en cas d'activité continue.
- `/login` (la requête qui, elle, précède l'obtention du cookie) est protégée par un jeton CSRF distinct
  (cookie + champ caché, comparés en temps constant) : défense en profondeur au-delà de `SameSite=Strict`.
- `/logout` ne nécessite pas de jeton CSRF séparé : `SameSite=Strict` empêche déjà un site tiers de déclencher
  cette requête avec le cookie de l'utilisateur (voir la docstring de `lody_auth/server.py`).
- Anti-force-brute : verrouillage après 5 échecs en 15 min, recul exponentiel (30 s, 60 s, 120 s…), par IP, en
  mémoire — renforcé par `limit_req` côté nginx (5 requêtes/minute sur `/lody-auth/login`).
- Aucune valeur de mot de passe ni de jeton de session ne transite jamais par l'URL, un journal applicatif, un
  journal du proxy, une trace d'exception ou de la télémétrie (`log_message` ne journalise que méthode + chemin
  + code, jamais le corps de la requête).

## Installation (une fois, sur l'hôte)

Suppose `scripts/lody-setup-secrets-group.sh` déjà exécuté (voir `docs/lody-secrets.md`) :

```sh
cd /srv/moneyprinterturbo
./scripts/lody-auth-set-password.sh admin     # saisie interactive, jamais en argument
sudo -n docker compose -f docker-compose.lody.yml up -d --build lody-auth
```

Puis, **seulement une fois le déploiement validé** :

1. Copier `deploy/nginx/lody-auth.conf` dans le vhost `video.lodylands.com` (voir les instructions en tête de
   ce fichier — zone `limit_req_zone` dans `nginx.conf`, bloc `location` dans le vhost).
2. `sudo nginx -t && sudo systemctl reload nginx`
3. Décommenter `LODY_ENABLE_SYSTEM_SETTINGS: "1"` dans `docker-compose.lody.yml`, puis redéployer `lody-ui`.

**Jamais dans cet ordre inverse** : activer `LODY_ENABLE_SYSTEM_SETTINGS` avant que nginx protège `/lody-auth/`
exposerait le formulaire de connexion sans limitation de débit (l'anti-force-brute applicatif resterait actif,
mais la défense en profondeur nginx manquerait).

## Procédure utilisateur : remplacer la clé OpenAI invalide/expirée actuelle

1. Se connecter : `https://video.lodylands.com/lody-auth/login`.
2. Une fois redirigé vers « Paramètres système », ouvrir la carte **Clé de script — OpenAI**.
3. Saisir la nouvelle clé (créée par l'utilisateur sur son propre compte OpenAI — Lody n'en crée jamais) dans
   les deux champs (nouvelle valeur + confirmation), cliquer **Remplacer la clé**.
4. Suivre la progression affichée : Enregistrement → Redémarrage du moteur → Vérification auprès du
   fournisseur → Prêt (ou un message d'erreur clair si la clé est refusée).
5. Si la même clé sert aussi aux images (`app.openai_image_api_keys`), la remplacer de la même façon dans sa
   propre carte — ce sont deux champs indépendants dans `config.toml`, vérifiés séparément.
6. Revenir sur un projet et ouvrir « Nouvelle production » : le préflight Texte (et Images le cas échéant)
   reflète immédiatement la nouvelle clé, sans action supplémentaire (aucun cache à vider : le préflight relit
   `secrets-status.json` et le rapport de capacités à chaque affichage).
7. Aucune génération n'est lancée automatiquement à aucune étape.

Les productions déjà en échec (dont le Short #4) restent dans l'historique, inchangées. Pour relancer : ouvrir
la production échouée, cliquer **Préparer à nouveau** (sujet et script conservés), puis confirmer une nouvelle
estimation — une tentative distincte, jamais une reprise silencieuse de l'ancienne confirmation.

## Rollback

```sh
# Revenir en arrière sur l'AUTHENTIFICATION seule (garde la gestion des clés désactivée) :
sudo systemctl stop lody-video-factory-auth 2>/dev/null || true   # si un jour un service systemd la wrappe
sudo -n docker compose -f docker-compose.lody.yml stop lody-auth

# Revenir en arrière complètement sur ce chantier (redéploie l'image précédente de lody-ui) :
LODY_IMAGE_TAG=<tag précédent> sudo -n -E docker compose -f docker-compose.lody.yml up -d --no-build lody-ui
```

`admin-auth.json` n'est jamais supprimé par un retour arrière (rien ne le lit si `lody-auth` est arrêté).
`config.toml`, SQLite et `storage/` ne sont jamais touchés par ce chantier au-delà de ce que #25/#26 faisaient
déjà (patch chirurgical d'un champ, retour arrière automatique déjà couvert par ces tickets).

## Limites

- Sessions en mémoire uniquement : un redémarrage de `lody-auth` déconnecte tout le monde (acceptable pour un
  admin unique).
- Un seul compte administrateur pour ce MVP (pas de gestion de rôles/plusieurs comptes).
- La vérification fournisseur porte sur l'authentification, pas sur le quota (voir la limite déjà documentée
  dans `docs/lody-secrets.md`).
