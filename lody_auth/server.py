"""Serveur HTTP du service d'authentification admin : ``/login``, ``/logout``, ``/verify``, ``/healthz``.

Bibliothèque standard uniquement (``http.server``). ``/verify`` n'est JAMAIS exposé publiquement par nginx
(voir ``docs/lody-auth.md`` et ``deploy/nginx/lody-auth.conf``) : Lody l'appelle par le réseau Docker interne.

Rien de ce module n'écrit ni ne journalise un mot de passe ou un jeton de session en clair : ``log_message`` est
volontairement réduit à méthode + chemin + code (jamais le corps de la requête, qui peut contenir un mot de passe).
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from lody_auth import store

logger = logging.getLogger("lody_auth")

SESSION_COOKIE = "lody_admin_session"
CSRF_COOKIE = "lody_csrf"
_SAFE_NEXT_DEFAULT = "/?vue=systeme"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_next(value: str | None) -> str:
    """N'accepte qu'un chemin relatif interne : jamais une redirection ouverte vers un autre site."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value or "\n" in value:
        return _SAFE_NEXT_DEFAULT
    return value


def _cookie(name: str, value: str, *, max_age: int | None = None, secure: bool = True, http_only: bool = True,
           same_site: str = "Strict") -> str:
    parts = [f"{name}={value}", "Path=/"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    if secure:
        parts.append("Secure")
    if http_only:
        parts.append("HttpOnly")
    parts.append(f"SameSite={same_site}")
    return "; ".join(parts)


def _expire_cookie(name: str, *, secure: bool = True) -> str:
    return _cookie(name, "", max_age=0, secure=secure)


def _parse_cookies(header: str) -> dict[str, str]:
    jar: dict[str, str] = {}
    for part in header.split(";"):
        if "=" in part:
            key, _, value = part.strip().partition("=")
            if key:
                jar[key] = value
    return jar


_LOGIN_PAGE = """<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connexion administrateur — Lody Video Factory</title>
<style>
:root{{color-scheme:dark}}
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#0a0e1a;color:#e8ecf7;font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}}
form{{background:#111729;border:1px solid rgba(148,163,209,.16);border-radius:18px;padding:2rem;width:100%;max-width:22rem}}
h1{{font-size:1.25rem;margin:0 0 1.25rem}}
label{{display:block;font-size:.85rem;color:#9ba6c4;margin:0 0 .3rem}}
input{{width:100%;box-sizing:border-box;padding:.6rem .7rem;margin:0 0 1rem;border-radius:10px;
  border:1px solid rgba(148,163,209,.3);background:#161d33;color:#e8ecf7;font-size:1rem}}
button{{width:100%;padding:.7rem;border:0;border-radius:12px;font-weight:600;font-size:1rem;cursor:pointer;
  background:linear-gradient(135deg,#22D3EE,#60A5FA);color:#06101c}}
.error{{background:rgba(253,164,175,.1);border:1px solid rgba(253,164,175,.3);color:#fda4af;
  border-radius:10px;padding:.6rem .8rem;margin:0 0 1rem;font-size:.9rem}}
.locked{{background:rgba(252,211,77,.1);border:1px solid rgba(252,211,77,.3);color:#fcd34d;
  border-radius:10px;padding:.6rem .8rem;margin:0 0 1rem;font-size:.9rem}}
</style></head><body>
<form method="post" action="/login" autocomplete="off">
<h1>Paramètres système — connexion</h1>
{message}
<label for="username">Identifiant</label>
<input id="username" name="username" autocomplete="username" required>
<label for="password">Mot de passe</label>
<input id="password" name="password" type="password" autocomplete="current-password" required>
<input type="hidden" name="csrf" value="{csrf}">
<input type="hidden" name="next" value="{next}">
<button type="submit">Se connecter</button>
</form></body></html>"""


def _render_login(*, csrf: str, next_url: str, error: str = "", locked_seconds: float = 0.0) -> str:
    if locked_seconds > 0:
        message = f'<div class="locked">Trop de tentatives : réessaie dans {int(locked_seconds) + 1} s.</div>'
    elif error:
        message = f'<div class="error">{html.escape(error)}</div>'
    else:
        message = ""
    return _LOGIN_PAGE.format(message=message, csrf=html.escape(csrf), next=html.escape(next_url))


def make_handler(*, admin_path, sessions: store.SessionStore, throttle: store.LoginThrottle,
                 cookie_secure: bool = True, clock: Callable[[], float] | None = None) -> type[BaseHTTPRequestHandler]:
    """Fabrique la classe du gestionnaire, avec l'état (fichier d'identifiant, sessions, anti-force-brute)
    capturé par fermeture — même schéma que les autres serveurs de test de ce dépôt."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "lody-auth/1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature imposée
            logger.info("%s - %s", self.address_string(), format % args)

        def _security_headers(self) -> None:
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")

        def _send_html(self, status: int, body: str, *, cookies: list[str] | None = None,
                       location: str | None = None) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self._security_headers()
            for one in cookies or []:
                self.send_header("Set-Cookie", one)
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(data)

        def _send_json(self, status: int, payload: dict[str, Any], *, cookies: list[str] | None = None) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self._security_headers()
            for one in cookies or []:
                self.send_header("Set-Cookie", one)
            self.end_headers()
            self.wfile.write(data)

        def _client_key(self) -> str:
            # Derrière nginx en production : l'en-tête est fourni par le proxy, jamais fait confiance venant
            # d'ailleurs qu'un appelant interne de confiance (le service n'est pas exposé directement).
            return self.headers.get("X-Real-IP") or self.client_address[0]

        def _read_form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            length = min(length, 8192)  # un formulaire de connexion ne dépasse jamais ça
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            parsed = parse_qs(raw, keep_blank_values=True)
            return {k: v[0] for k, v in parsed.items()}

        # -- routes ---------------------------------------------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802 - imposé par BaseHTTPRequestHandler
            path = urlsplit(self.path).path
            query = parse_qs(urlsplit(self.path).query)
            if path == "/login":
                return self._get_login(query)
            if path == "/verify":
                return self._get_verify()
            if path == "/healthz":
                return self._send_json(200, {"ok": True})
            self._send_html(404, "Introuvable")

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/login":
                return self._post_login()
            if path == "/logout":
                return self._post_logout()
            self._send_html(404, "Introuvable")

        # -- /login -----------------------------------------------------------------------------------------------
        def _get_login(self, query: dict[str, list[str]]) -> None:
            csrf = store.new_csrf_token()
            next_url = _safe_next((query.get("next") or [""])[0])
            body = _render_login(csrf=csrf, next_url=next_url)
            self._send_html(200, body, cookies=[_cookie(CSRF_COOKIE, csrf, max_age=600, secure=cookie_secure)])

        def _post_login(self) -> None:
            form = self._read_form()
            cookies_in = _parse_cookies(self.headers.get("Cookie", ""))
            key = self._client_key()
            next_url = _safe_next(form.get("next"))
            locked = throttle.locked_for(key)
            if locked > 0:
                body = _render_login(csrf=store.new_csrf_token(), next_url=next_url, locked_seconds=locked)
                return self._send_html(429, body)
            if not store.csrf_valid(cookies_in.get(CSRF_COOKIE), form.get("csrf")):
                body = _render_login(csrf=store.new_csrf_token(), next_url=next_url,
                                     error="Session de connexion expirée : réessaie.")
                return self._send_html(400, body)
            username, password = form.get("username", ""), form.get("password", "")
            ok = store.check_credentials(admin_path, username, password)
            # `password`/`form` ne sont jamais journalisés ni réutilisés au-delà de cette vérification.
            if not ok:
                throttle.record_failure(key)
                body = _render_login(csrf=store.new_csrf_token(), next_url=next_url,
                                     error="Identifiant ou mot de passe incorrect.")
                return self._send_html(401, body)
            throttle.record_success(key)
            token = sessions.create(username)
            self._send_html(303, "", cookies=[_cookie(SESSION_COOKIE, token, max_age=store.SESSION_ABSOLUTE_SECONDS,
                                                       secure=cookie_secure),
                                              _expire_cookie(CSRF_COOKIE, secure=cookie_secure)],
                            location=next_url)

        # -- /logout ----------------------------------------------------------------------------------------------
        def _post_logout(self) -> None:
            # SameSite=Strict interdit déjà à un site tiers de déclencher cette requête avec le cookie de
            # session : pas de jeton CSRF distinct nécessaire pour une simple déconnexion (voir docs/lody-auth.md).
            cookies_in = _parse_cookies(self.headers.get("Cookie", ""))
            token = cookies_in.get(SESSION_COOKIE)
            if token:
                sessions.revoke(token)
            self._send_html(303, "", cookies=[_expire_cookie(SESSION_COOKIE, secure=cookie_secure)], location="/login")

        # -- /verify (interne uniquement) --------------------------------------------------------------------------
        def _get_verify(self) -> None:
            token = self.headers.get("X-Lody-Session-Token", "")
            session = sessions.touch(token) if token else None
            if session is None:
                return self._send_json(401, {"valid": False})
            self._send_json(200, {"valid": True, "username": session.username, "checked_at": _now_iso()})

    return Handler


def serve(*, host: str, port: int, admin_path, cookie_secure: bool = True) -> ThreadingHTTPServer:
    sessions = store.SessionStore()
    throttle = store.LoginThrottle()
    handler = make_handler(admin_path=admin_path, sessions=sessions, throttle=throttle, cookie_secure=cookie_secure)
    return ThreadingHTTPServer((host, port), handler)
