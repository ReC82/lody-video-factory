"""``admin_auth`` (côté Lody) : vérification de session auprès d'un vrai serveur /verify local, jamais de secret."""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from lody.generation import admin_auth


class _FakeCookies(dict):
    pass


class _FakeContext:
    def __init__(self, cookies: dict[str, str] | None = None):
        self.cookies = _FakeCookies(cookies or {})


@pytest.fixture
def verify_server():
    """Vrai petit serveur /verify : accepte un jeton fixe, refuse tout le reste."""
    good_token = "good-session-token"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            token = self.headers.get("X-Lody-Session-Token", "")
            body = json.dumps({"valid": True, "username": "admin"} if token == good_token else {"valid": False}).encode()
            self.send_response(200 if token == good_token else 401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{server.server_port}", "good_token": good_token}
    finally:
        server.shutdown()


def test_no_cookie_at_all_is_not_authenticated(monkeypatch, verify_server):
    monkeypatch.setattr(admin_auth, "st", type("S", (), {"context": _FakeContext({})})())
    monkeypatch.setenv("LODY_AUTH_VERIFY_URL", verify_server["url"])
    assert admin_auth.current_session() is None


def test_valid_session_token_is_authenticated(monkeypatch, verify_server):
    monkeypatch.setattr(admin_auth, "st", type("S", (), {
        "context": _FakeContext({admin_auth.SESSION_COOKIE: verify_server["good_token"]})})())
    monkeypatch.setenv("LODY_AUTH_VERIFY_URL", verify_server["url"])
    session = admin_auth.current_session()
    assert session is not None and session.username == "admin"


def test_wrong_or_expired_token_is_not_authenticated(monkeypatch, verify_server):
    monkeypatch.setattr(admin_auth, "st", type("S", (), {
        "context": _FakeContext({admin_auth.SESSION_COOKIE: "not-the-right-token"})})())
    monkeypatch.setenv("LODY_AUTH_VERIFY_URL", verify_server["url"])
    assert admin_auth.current_session() is None


def test_auth_service_unreachable_never_grants_access(monkeypatch):
    monkeypatch.setattr(admin_auth, "st", type("S", (), {
        "context": _FakeContext({admin_auth.SESSION_COOKIE: "whatever"})})())
    monkeypatch.setenv("LODY_AUTH_VERIFY_URL", "http://127.0.0.1:1/verify")  # rien n'écoute : connexion refusée
    assert admin_auth.current_session() is None  # jamais d'exception, jamais d'accès accordé par défaut


def test_login_url_points_at_the_public_auth_prefix_and_carries_next():
    assert admin_auth.login_url("/?vue=systeme") == "/lody-auth/login?next=%2F%3Fvue%3Dsysteme"


def test_logout_url_points_at_the_public_auth_prefix():
    assert admin_auth.logout_url() == "/lody-auth/logout"


def test_public_urls_respect_a_custom_base(monkeypatch):
    monkeypatch.setenv("LODY_AUTH_PUBLIC_URL", "https://video.lodylands.com/lody-auth")
    assert admin_auth.login_url("/x").startswith("https://video.lodylands.com/lody-auth/login")
