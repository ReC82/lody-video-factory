"""Navigation depuis la page de suivi dans un VRAI navigateur.

Les boutons du suivi sont dans un ``st.fragment`` (actualisation automatique) : un ``on_click`` n'y relance que le
fragment, ce qu'AppTest ne voit pas. Ce test cliquerait dans Chromium ; ignoré sans Playwright/Chromium (ex. CI).
"""

from __future__ import annotations

from lody import settings
from lody.generation.models import ProductionStatus as S
from lody.generation.service import build_request
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS
from test.lody.test_responsive import _chromium, running_app, sync_api  # noqa: F401

SUBJECT = "Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming."


def _seed():
    projects = ProjectRepository(settings.db_path())
    projects.seed_defaults(SEED_PROJECTS)
    by_name = {p.name: p for p in projects.list_projects()}
    repo = ProductionRepository(settings.db_path())
    params = {"request": build_request(by_name["LodyCrypto"], SUBJECT).to_dict(), "engine": {}}
    failed = repo.create(params=params, project_id=by_name["LodyCrypto"].id, subject=SUBJECT, provider="moneyprinterturbo",
                         status=S.ECHEC, script="Un script déjà écrit. " * 5, error_code="key_missing",
                         error_message="Une clé d’API nécessaire à cette génération n’est pas configurée côté serveur.",
                         current_step="Échec")
    active = repo.create(project_id=by_name["Audiovisuel"].id, subject=SUBJECT, provider="moneyprinterturbo",
                         status=S.EN_FILE, external_task_id="task-navigation-0001", progress=0,
                         params={"request": build_request(by_name["Audiovisuel"], SUBJECT).to_dict(), "engine": {}})
    return by_name, failed, active


def test_tracking_buttons_change_page_inside_the_fragment(running_app):  # noqa: F811
    base, chrome, _ = running_app
    projects, failed, active = _seed()
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1200, "height": 1600})
        # échec : « Préparer à nouveau » ouvre la page de production avec le sujet conservé
        page.goto(f"{base}/?projet={projects['LodyCrypto'].id}&vue=suivi&production={failed.id}", wait_until="networkidle")
        page.get_by_text("Suivi de la production").wait_for(timeout=20000)
        page.get_by_role("button", name="Préparer à nouveau").click()
        page.get_by_text("Nouvelle tentative de V1").wait_for(timeout=20000)
        sync_api.expect(page.get_by_placeholder("Décris la vidéo que tu veux…")).to_have_value(SUBJECT, timeout=15000)
        # échec : « Créer une V2 avec ce script » ouvre la page V2
        page.goto(f"{base}/?projet={projects['LodyCrypto'].id}&vue=suivi&production={failed.id}", wait_until="networkidle")
        page.get_by_role("button", name="Créer une V2 avec ce script").click()
        page.get_by_text("Une V2 régénère tout").wait_for(timeout=20000)
        # production active (moteur injoignable dans ce test) : « Retour au projet » quitte le suivi
        page.goto(f"{base}/?projet={projects['Audiovisuel'].id}&vue=suivi&production={active.id}", wait_until="networkidle")
        page.get_by_text("Suivi de la production").wait_for(timeout=20000)
        page.get_by_role("button", name="Retour au projet").click()
        page.get_by_text("Tes vidéos").wait_for(timeout=20000)
        browser.close()
    # la tentative échouée est restée telle quelle
    assert ProductionRepository(settings.db_path()).get(failed.id).status is S.ECHEC
