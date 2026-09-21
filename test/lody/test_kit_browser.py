"""Kit de publication dans un vrai navigateur (Chromium) : boutons « Copier », onglets, 1440 / 820 / 390 px sans débordement.

Mode démonstration : moteur simulé, aucun fournisseur, aucun coût. Ignoré sans Playwright ni Chromium (ex. CI).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from test.lody.test_responsive import SIZES, SUBJECT, running_app, sync_api  # noqa: F401  (fixture réutilisée)

TABS = ["Fichiers", "YouTube", "TikTok / Shorts", "Miniature", "Sous-titres", "Contrôle"]
OVERFLOW = "() => { const m = document.querySelector('[data-testid=\"stMain\"]') || document.documentElement; return m.scrollWidth - m.clientWidth; }"


def _open_kit(page, base, data_dir):
    import sqlite3

    page.goto(base, wait_until="networkidle")
    page.get_by_text("Tes projets vidéo").wait_for(timeout=20000)
    project = sqlite3.connect(data_dir / "lody.sqlite3").execute("select id from projects where name='LodyCrypto'").fetchone()[0]
    page.goto(f"{base}/?projet={project}&vue=production", wait_until="networkidle")
    page.get_by_placeholder("Décris la vidéo que tu veux…").fill(SUBJECT)
    page.get_by_placeholder("Décris la vidéo que tu veux…").press("Control+Enter")
    page.get_by_text("Mode démonstration : simuler").first.click()
    page.get_by_role("button", name="Préparer la génération").click()
    page.get_by_text("Estimation et confirmation").wait_for(timeout=15000)
    page.get_by_role("button", name="Confirmer et générer la vidéo").click()
    page.get_by_text("Ta vidéo est prête").wait_for(timeout=60000)
    page.get_by_text("Publication manuelle").wait_for(timeout=20000)


@pytest.mark.parametrize("width", list(SIZES))
def test_kit_tabs_have_no_horizontal_overflow(running_app, width):  # noqa: F811
    base, chrome, data_dir = running_app
    shots = os.environ.get("LODY_SHOTS_DIR")
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": SIZES[width]})
        _open_kit(page, base, data_dir)
        for tab in TABS:
            page.get_by_role("tab", name=tab).click()
            page.wait_for_timeout(900)
            assert page.evaluate(OVERFLOW) <= 1, f"débordement horizontal (onglet {tab}, {width}px)"
            if shots and width in (1440, 390):
                Path(shots).mkdir(parents=True, exist_ok=True)
                page.locator(".st-key-kit").screenshot(path=str(Path(shots) / f"kit-{width}-{tab.split()[0].lower()}.png"))
        browser.close()


def test_copy_buttons_put_the_field_text_in_the_clipboard(running_app):  # noqa: F811
    base, chrome, data_dir = running_app
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1440, "height": 900}, permissions=["clipboard-read", "clipboard-write"],
                                      base_url=base)
        page = context.new_page()
        _open_kit(page, base, data_dir)
        page.get_by_role("tab", name="YouTube").click()
        page.wait_for_timeout(800)
        title = page.get_by_label("Titre principal", exact=True).input_value()
        assert title
        page.frame_locator("iframe").nth(0).get_by_role("button").first.click()
        page.wait_for_timeout(500)
        assert page.evaluate("() => navigator.clipboard.readText()") == title
        browser.close()
