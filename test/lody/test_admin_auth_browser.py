"""Authentification admin dans un vrai navigateur : page de connexion, page « Paramètres système » une fois
authentifiée (cookie posé directement par le test, comme un vrai navigateur après un vrai /login), verrouillage
sans session, et absence de débordement horizontal à 1440/820/390 px.

Deux vrais processus (aucun mock) : ``lody_auth`` (service dédié) et Lody (Streamlit), reliés exactement comme
en production (Lody appelle ``/verify`` en HTTP). Ignoré sans Playwright ni Chromium (ex. CI).
"""

from __future__ import annotations

import glob
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
pytest.importorskip("streamlit")

ROOT = Path(__file__).resolve().parents[2]
SIZES = {1440: 900, 820: 1100, 390: 844}
USERNAME, PASSWORD = "admin", "browser-test-password-123456"


def _chromium() -> str | None:
    explicit = os.environ.get("LODY_TEST_CHROMIUM")
    if explicit:
        return explicit
    found = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
    return found[0] if found else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_http(url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except OSError:
            time.sleep(0.4)
    raise TimeoutError(url)


@pytest.fixture
def stack(lody_env, tmp_path):
    chrome = _chromium()
    if not chrome:
        pytest.skip("Chromium introuvable")

    from lody_auth.store import set_admin_password

    admin_path = tmp_path / "admin-auth.json"
    set_admin_password(admin_path, USERNAME, PASSWORD)

    auth_port = _free_port()
    auth_env = {**os.environ, "PYTHONPATH": str(ROOT), "LODY_AUTH_ADMIN_FILE": str(admin_path),
               "LODY_AUTH_PORT": str(auth_port), "LODY_AUTH_HOST": "127.0.0.1", "LODY_AUTH_COOKIE_SECURE": "0"}
    auth_process = subprocess.Popen([sys.executable, "-m", "lody_auth"], env=auth_env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ui_port = _free_port()
    ui_env = {**os.environ, "LODY_DATA_DIR": str(lody_env), "HOME": str(lody_env.parent),
             "PYTHONPATH": str(ROOT / "webui"), "LODY_ENABLE_SYSTEM_SETTINGS": "1",
             "LODY_AUTH_VERIFY_URL": f"http://127.0.0.1:{auth_port}/verify", "LODY_AUTH_PUBLIC_URL": "/lody-auth"}
    ui_process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "webui" / "Lody.py"), "--server.headless=true",
         f"--server.port={ui_port}", "--server.address=127.0.0.1", "--browser.gatherUsageStats=false"],
        env=ui_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_http(f"http://127.0.0.1:{auth_port}/healthz")
        _wait_http(f"http://127.0.0.1:{ui_port}/_stcore/health")
        yield {"auth_base": f"http://127.0.0.1:{auth_port}", "ui_base": f"http://127.0.0.1:{ui_port}",
               "ui_port": ui_port, "chrome": chrome}
    finally:
        auth_process.terminate()
        ui_process.terminate()
        auth_process.wait(timeout=10)
        ui_process.wait(timeout=10)


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response  # laisse le 303 tel quel : on ne veut que le cookie, pas suivre vers /

    https_response = http_response


def _real_login_token(auth_base: str) -> str:
    """Un VRAI aller-retour HTTP /login (comme le ferait un navigateur), côté test — pour ensuite poser le
    cookie obtenu dans le contexte Playwright avant de naviguer vers Lody (voir le module, en tête)."""
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _NoRedirect())
    page = opener.open(f"{auth_base}/login", timeout=5).read().decode()
    marker = 'name="csrf" value="'
    csrf = page[page.index(marker) + len(marker):]
    csrf = csrf[: csrf.index('"')]
    body = urlencode({"username": USERNAME, "password": PASSWORD, "csrf": csrf, "next": "/"}).encode()
    request = urllib.request.Request(f"{auth_base}/login", data=body, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    response = opener.open(request, timeout=5)
    assert response.status == 303, f"connexion réelle échouée : statut {response.status}"
    for cookie in jar:
        if cookie.name == "lody_admin_session":
            return cookie.value
    raise AssertionError("connexion réelle échouée : pas de cookie de session")


def test_login_page_renders_and_has_no_horizontal_overflow(stack):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=stack["chrome"], args=["--no-sandbox"])
        for width, height in SIZES.items():
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(f"{stack['auth_base']}/login", wait_until="networkidle")
            page.get_by_label("Identifiant").wait_for(timeout=10000)
            overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 1, f"débordement horizontal ({width}px)"
            page.close()
        browser.close()


def test_a_real_login_round_trip_then_access_to_system_settings(stack):
    """Connexion RÉELLE (formulaire, CSRF, redirection) côté test, cookie posé dans le navigateur, page
    authentifiée vérifiée à l'écran — bout en bout, comme un vrai administrateur."""
    token = _real_login_token(stack["auth_base"])
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=stack["chrome"], args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_cookies([{"name": "lody_admin_session", "value": token, "url": stack["ui_base"]}])
        page = context.new_page()
        page.goto(f"{stack['ui_base']}/?vue=systeme", wait_until="networkidle")
        page.get_by_text("Paramètres système").wait_for(timeout=15000)
        text = page.inner_text("body")
        assert "Connecté en tant que" in text and USERNAME in text
        assert "Clé de script" in text and "Absente" in text
        assert "Se déconnecter" in text
        browser.close()


def test_system_settings_is_locked_without_a_session_cookie(stack):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=stack["chrome"], args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(f"{stack['ui_base']}/?vue=systeme", wait_until="networkidle")
        page.get_by_text("Authentification requise").wait_for(timeout=15000)
        text = page.inner_text("body")
        assert "Absente" not in text and "Configurée" not in text  # aucune donnée avant authentification
        browser.close()


@pytest.mark.parametrize("width", list(SIZES))
def test_authenticated_system_settings_has_no_horizontal_overflow(stack, width):
    token = _real_login_token(stack["auth_base"])
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=stack["chrome"], args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": width, "height": SIZES[width]})
        context.add_cookies([{"name": "lody_admin_session", "value": token, "url": stack["ui_base"]}])
        page = context.new_page()
        page.goto(f"{stack['ui_base']}/?vue=systeme", wait_until="networkidle")
        page.get_by_text("Paramètres système").wait_for(timeout=15000)
        page.wait_for_timeout(700)
        overflow = page.evaluate("() => { const m = document.querySelector('[data-testid=\"stMain\"]') || document.documentElement; return m.scrollWidth - m.clientWidth; }")
        assert overflow <= 1, f"débordement horizontal ({width}px)"
        shots = os.environ.get("LODY_SHOTS_DIR")
        if shots and width in (1440, 390):
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(shots) / f"admin-auth-{width}.png"), full_page=True)
        browser.close()


def test_logout_revokes_the_session_in_a_real_browser(stack):
    token = _real_login_token(stack["auth_base"])
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=stack["chrome"], args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        context.add_cookies([{"name": "lody_admin_session", "value": token, "url": stack["auth_base"]}])
        page = context.new_page()
        response = page.request.post(f"{stack['auth_base']}/logout")
        assert response.status in (200, 303)
        verify = page.request.get(f"{stack['auth_base']}/verify", headers={"X-Lody-Session-Token": token})
        assert verify.status == 401
        browser.close()
