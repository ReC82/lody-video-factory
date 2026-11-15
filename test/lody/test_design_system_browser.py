"""Design system #214 dans un vrai navigateur : en-tête minimal et premier écran utile sur téléphone.

Nécessite Playwright et un Chromium ; ignoré sinon (ex. CI), comme ``test_responsive.py``.
"""

from __future__ import annotations

import sqlite3

import pytest

from test.lody.test_responsive import running_app, sync_api  # noqa: F401  (fixture réutilisée)

OVERFLOW = ("() => { const m = document.querySelector('[data-testid=\"stMain\"]') || document.documentElement;"
            " return m.scrollWidth - m.clientWidth; }")
GEOMETRY = """() => {
  const top = (sel) => { const el = document.querySelector(sel); return el ? el.getBoundingClientRect() : null; };
  const brand = top('.brand'), h1 = top('h1'), back = top('.st-key-nav_home');
  return {brandTop: brand.top, brandBottom: brand.bottom, h1Top: h1 ? h1.top : null,
          backTop: back ? back.top : null, backBottom: back ? back.bottom : null};
}"""


@pytest.mark.parametrize("width,height", [(360, 740), (390, 844), (1440, 900)])
def test_header_is_one_thin_line_and_title_comes_first(running_app, width, height):  # noqa: F811
    base, chrome, data_dir = running_app
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(base, wait_until="networkidle")
        page.get_by_text("Tes projets vidéo").wait_for(timeout=20000)
        project = sqlite3.connect(data_dir / "lody.sqlite3").execute(
            "select id from projects where name='LodyCrypto'").fetchone()[0]

        page.wait_for_timeout(700)
        home = page.evaluate(GEOMETRY)
        assert home["brandTop"] <= 24, home  # plus de bandeau de 100-200 px au-dessus de la marque
        assert home["h1Top"] <= 110, home
        assert page.evaluate(OVERFLOW) <= 1

        page.goto(f"{base}/?projet={project}", wait_until="networkidle")
        page.get_by_role("button", name="Commencer une production").wait_for(timeout=20000)
        page.wait_for_timeout(700)
        geo = page.evaluate(GEOMETRY)
        # « Tous les projets » tient sur la même ligne que la marque, y compris à 360 px.
        assert geo["backTop"] < geo["brandBottom"] and geo["backBottom"] > geo["brandTop"], geo
        assert geo["h1Top"] <= 110, geo
        # L'action principale du projet est visible dès le premier écran.
        start = page.get_by_role("button", name="Commencer une production").bounding_box()
        assert start["y"] + start["height"] <= min(height, 400), start
        assert start["height"] >= 44
        assert page.evaluate(OVERFLOW) <= 1
        browser.close()
