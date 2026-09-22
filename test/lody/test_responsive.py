"""Responsive 1440 / 820 / 390 px : aucun débordement horizontal sur tout le parcours (mode démonstration).

Nécessite Playwright et un Chromium ; ignoré sinon (ex. CI). Lance l'application en local, sans moteur ni fournisseur.
"""

from __future__ import annotations

import glob
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
pytest.importorskip("streamlit")

ROOT = Path(__file__).resolve().parents[2]
SIZES = {1440: 900, 820: 1100, 390: 844}
SUBJECT = "Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming."


def _chromium() -> str | None:
    explicit = os.environ.get("LODY_TEST_CHROMIUM")
    if explicit:
        return explicit
    found = glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"))
    return found[0] if found else None


@pytest.fixture
def running_app(lody_env):
    chrome = _chromium()
    if not chrome:
        pytest.skip("Chromium introuvable")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "LODY_DATA_DIR": str(lody_env), "HOME": str(lody_env.parent),
           "PYTHONPATH": str(ROOT / "webui")}
    process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "webui" / "Lody.py"), "--server.headless=true",
         f"--server.port={port}", "--server.address=127.0.0.1", "--browser.gatherUsageStats=false"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        yield f"http://127.0.0.1:{port}", chrome, lody_env
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("width", list(SIZES))
def test_demo_journey_has_no_horizontal_overflow(running_app, width):
    base, chrome, data_dir = running_app
    overflow = "() => { const m = document.querySelector('[data-testid=\"stMain\"]') || document.documentElement; return m.scrollWidth - m.clientWidth; }"
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": SIZES[width]})
        page.goto(base, wait_until="networkidle")
        page.get_by_text("Tes projets vidéo").wait_for(timeout=20000)
        project = sqlite3.connect(data_dir / "lody.sqlite3").execute(
            "select id from projects where name='LodyCrypto'").fetchone()[0]

        def check(label):
            page.wait_for_timeout(700)
            assert page.evaluate(overflow) <= 1, f"débordement horizontal ({label}, {width}px)"

        check("accueil")
        page.goto(f"{base}/?projet={project}", wait_until="networkidle")
        check("projet")
        page.goto(f"{base}/?projet={project}&vue=production", wait_until="networkidle")
        page.get_by_placeholder("Décris la vidéo que tu veux…").fill(SUBJECT)
        page.get_by_placeholder("Décris la vidéo que tu veux…").press("Control+Enter")
        page.get_by_text("Mode démonstration : simuler").first.click()
        page.get_by_role("button", name="Préparer la génération").click()
        page.get_by_text("Estimation et confirmation").wait_for(timeout=15000)
        check("estimation")
        page.get_by_role("button", name="Confirmer et générer la vidéo").click()
        page.get_by_text("Suivi de la production").wait_for(timeout=15000)
        check("suivi")
        page.get_by_text("Ta vidéo est prête").wait_for(timeout=60000)
        check("résultat")
        page.get_by_role("button", name="Créer une V2").click()
        page.get_by_text("Une V2 régénère tout").wait_for(timeout=15000)
        check("V2")
        browser.close()


@pytest.mark.parametrize("width", list(SIZES))
def test_characters_and_locations_pages_have_no_horizontal_overflow(running_app, width):
    """#32/#33 : état vide, formulaire ouvert et liste peuplée, aux trois largeurs."""
    base, chrome, data_dir = running_app
    overflow = "() => { const m = document.querySelector('[data-testid=\"stMain\"]') || document.documentElement; return m.scrollWidth - m.clientWidth; }"
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": SIZES[width]})
        project = sqlite3.connect(data_dir / "lody.sqlite3").execute(
            "select id from projects where name='LodyCrypto'").fetchone()[0]

        def check(label):
            page.wait_for_timeout(700)
            assert page.evaluate(overflow) <= 1, f"débordement horizontal ({label}, {width}px)"

        for view, empty_text, add_label, name_placeholder in (
            ("personnages", "Aucun personnage pour l’instant", "Ajouter un personnage", "Ex. Gaston"),
            ("lieux", "Aucun lieu pour l’instant", "Ajouter un lieu", "Ex. Place du marché"),
        ):
            page.goto(f"{base}/?projet={project}&vue={view}", wait_until="networkidle")
            page.get_by_text(empty_text).wait_for(timeout=15000)
            check(f"{view} (vide)")
            page.get_by_role("button", name=add_label).click()
            page.get_by_placeholder(name_placeholder).wait_for(timeout=15000)
            check(f"{view} (formulaire)")
            page.get_by_placeholder(name_placeholder).fill("Test responsive")
            page.get_by_role("button", name="Enregistrer").click()
            page.get_by_text("Test responsive").wait_for(timeout=15000)
            check(f"{view} (liste peuplée)")
        browser.close()
