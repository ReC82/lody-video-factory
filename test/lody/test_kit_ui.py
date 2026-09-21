"""Kit de publication dans l'interface (AppTest) : sections, édition persistante, miniature, téléchargements, fond payant. Faux moteur."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from lody import settings
from lody.generation import media, runtime
from lody.generation.costing import PriceBook
from lody.generation.kit_service import KitService
from lody.generation.kit_store import KitRepository
from lody.generation.service import ProductionService
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS
from test.lody.fakes import ScriptedConnector, SyncExecutor, _png

apptest = pytest.importorskip("streamlit.testing.v1")
streamlit = pytest.importorskip("streamlit")

WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
SUBJECT = "Crée le Short #0 : « Pourquoi je lance cette série ». Je ne suis pas expert financier."
PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.30"), ("music", "elevenlabs"): Decimal("0.10")})
SRT = "1\n00:00:00,000 --> 00:00:04,000\nc’ est l’  idée\n\n2\n00:00:04,000 --> 00:00:08,000\nqu’ il n’ est pas simple\n"
TABS = ["Fichiers", "YouTube", "TikTok / Shorts", "Miniature", "Sous-titres", "Contrôle"]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(media, "probe_duration_ms", lambda path, timeout=20.0: 52_400)
    for cache in (streamlit.cache_resource, streamlit.cache_data):
        cache.clear()
    yield
    for cache in (streamlit.cache_resource, streamlit.cache_data):
        cache.clear()


class World:
    def __init__(self, tmp_path, monkeypatch):
        self.connector = ScriptedConnector(tmp_path / "engine-storage")
        self.connector.subtitles_text = SRT
        self.productions = ProductionService(ProductionRepository(settings.db_path()), {runtime.DEFAULT_PROVIDER: self.connector},
                                             SyncExecutor(), price_book=lambda: PRICES)
        self.kits = KitService(KitRepository(settings.db_path()), self.productions, ProjectRepository(settings.db_path()), SyncExecutor(),
                               price_book=lambda: PRICES)
        monkeypatch.setattr("lody.app.build_service", lambda: self.productions)
        monkeypatch.setattr("lody.app.build_kit_service", lambda service: self.kits)
        repo = ProjectRepository(settings.db_path())
        repo.seed_defaults(SEED_PROJECTS)
        self.projects = {p.name: p for p in repo.list_projects()}

    def finish(self, name="LodyCrypto", subject=SUBJECT):
        project = self.projects[name]
        draft = self.productions.prepare(project, subject, provider_id=runtime.DEFAULT_PROVIDER)
        running = self.productions.confirm(draft.id, accept_partial=True)
        task = running.external_task_id
        folder = self.connector.root / "tasks" / task
        folder.mkdir(parents=True, exist_ok=True)
        self.connector.scene_images = []
        for i in range(2):
            image = f"openai-image-{i:04x}aa.png"
            (folder / image).write_bytes(_png(f"{task}-{i}", (512, 768)))
            self.connector.scene_images.append({"ref": f"tasks/{task}/{image}", "name": image})
        self.connector.queue(self.connector.done(task))
        return project, self.productions.refresh(running.id)

    def open(self, project, production):
        app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=40)
        app.query_params["projet"] = project.id
        app.query_params["vue"] = "suivi"
        app.query_params["production"] = production.id
        return app.run()


@pytest.fixture
def world(lody_env, tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def _text(app):
    return " ".join(str(item.value) for item in app.markdown)


def _input(app, suffix):
    return next(widget for widget in [*app.text_input, *app.text_area] if str(widget.key).endswith(suffix))


def _button(app, label):
    return next(button for button in app.button if button.label == label)


def test_finished_production_shows_the_kit_with_all_sections_and_costs_nothing(world):
    project, production = world.finish()
    before = list(world.connector.calls)
    app = world.open(project, production)
    assert not app.exception
    assert [tab.label for tab in app.tabs if tab.label in TABS] == TABS
    text = _text(app)
    assert "Publication manuelle" in text and "Rien n’est publié automatiquement" in text and "Gratuit" in text
    assert _input(app, "_title").value == "Pourquoi je lance cette série"
    assert "#LodyCrypto" in _input(app, "_hashtags").value and "#" not in _input(app, "_tags").value
    assert world.connector.calls.count("thumbnail_background") == 0
    assert [c for c in world.connector.calls if c not in before] and all(c in ("list_scene_images", "read_subtitles", "poll") for c in world.connector.calls[len(before):])


def test_downloads_are_offered_for_video_thumbnail_subtitles_and_kit(world):
    project, production = world.finish()
    app = world.open(project, production)
    labels = {getattr(element, "label", "") for element in app.get("download_button")}
    assert {"Télécharger le kit (ZIP)", "Télécharger en PNG", "Télécharger en JPEG", "Télécharger le SRT", "Télécharger le VTT"} <= labels


def test_subtitle_tab_explains_burned_in_subtitles_and_shows_french_apostrophes(world):
    project, production = world.finish()
    app = world.open(project, production)
    code = " ".join(str(block.value) for block in app.code)
    assert "déjà incrustés dans le MP4" in _text(app) and "c’est l’idée" in code and "qu’il n’est" in code and "l’  idée" not in code


def test_editing_saving_and_restoring_metadata_persist_across_reloads(world):
    project, production = world.finish()
    app = world.open(project, production)
    _input(app, "_title").set_value("Mon titre corrigé").run()
    assert "Modifications non enregistrées" in _text(app)
    _button(app, "Enregistrer les modifications").click().run()
    kit = world.kits.kits.find_by_production(production.id)
    assert kit.metadata["title"] == "Mon titre corrigé" and kit.initial_metadata["title"] == "Pourquoi je lance cette série"
    app = world.open(project, production)                                    # nouvelle session : tout est relu depuis la base
    assert _input(app, "_title").value == "Mon titre corrigé"
    _button(app, "Restaurer les propositions initiales").click().run()
    assert world.kits.kits.find_by_production(production.id).metadata["title"] == "Pourquoi je lance cette série"
    assert _input(app, "_title").value == "Pourquoi je lance cette série"


def test_a_secret_pasted_in_a_field_is_refused_and_a_financial_promise_is_flagged(world):
    project, production = world.finish()
    app = world.open(project, production)
    _input(app, "_title").set_value("Ma clé sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD").run()
    _button(app, "Enregistrer les modifications").click().run()
    assert world.kits.kits.find_by_production(production.id).metadata["title"] == "Pourquoi je lance cette série"
    assert "refusée" in _text(app)
    _input(app, "_title").set_value("Achetez maintenant ce token, gains garantis !").run()
    _button(app, "Enregistrer les modifications").click().run()
    assert "à vérifier" in _text(app)                        # enregistré (c'est ton texte) mais signalé dans le contrôle


def test_regenerating_local_proposals_is_free_and_leaves_the_video_alone(world):
    project, production = world.finish()
    app = world.open(project, production)
    calls = list(world.connector.calls)
    _button(app, "Nouvelles propositions (gratuit)").click().run()
    assert "gratuit" in _text(app) and "thumbnail_background" not in world.connector.calls[len(calls):] and "submit" not in world.connector.calls[len(calls):]
    assert world.productions.repo.get(production.id).video_ref == production.video_ref


def test_thumbnail_tab_shows_three_variants_and_a_phone_preview_and_persists_the_choice(world):
    project, production = world.finish()
    app = world.open(project, production)
    assert len(app.get("imgs")) + len(app.get("image")) >= 3
    assert "Aperçu téléphone" in _text(app)
    app.radio(key=f"kit_{world.kits.kits.find_by_production(production.id).id}_thumb_variant").set_value("lateral").run()
    assert world.kits.kits.find_by_production(production.id).thumbnail["variant"] == "lateral"
    _input(app, "_thumb_text").set_value("Le vrai départ").run()
    assert world.kits.kits.find_by_production(production.id).thumbnail["text"] == "Le vrai départ"
    assert world.connector.calls.count("thumbnail_background") == 0


def test_unsupported_engine_explains_that_the_background_comes_from_a_scene_image(world):
    project, production = world.finish()
    text = _text(world.open(project, production))
    assert "ne sait pas générer une image de fond seule" in text and "gratuit" in text
    assert "Estimer un nouveau fond" not in [b.label for b in world.open(project, production).button]


def test_paid_background_needs_an_estimate_then_a_confirmation(world):
    project, production = world.finish()
    world.connector.supports_thumbnail_background = True
    app = world.open(project, production)
    _button(app, "Estimer un nouveau fond").click().run()
    assert world.connector.calls.count("thumbnail_background") == 0           # estimation seule : aucun appel
    text = _text(app)
    assert "Estimation" in text and "Rien n’est lancé tant que tu n’as pas confirmé" in text
    _button(app, "Confirmer et générer le fond").click().run()
    assert world.connector.calls.count("thumbnail_background") == 1
    kit = world.kits.kits.find_by_production(production.id)
    assert kit.thumbnail["background"]["kind"] == "generated"


def test_cancelling_the_estimate_calls_nothing(world):
    project, production = world.finish()
    world.connector.supports_thumbnail_background = True
    app = world.open(project, production)
    _button(app, "Estimer un nouveau fond").click().run()
    _button(app, "Annuler").click().run()
    assert world.connector.calls.count("thumbnail_background") == 0
    assert "Estimer un nouveau fond" in [b.label for b in app.button]


def test_control_tab_lists_the_prepublication_checks(world):
    project, production = world.finish()
    text = _text(world.open(project, production))
    for title in ("Durée", "Format vertical", "Miniature choisie", "Sous-titres présents et synchronisés", "Absence de secret ou de donnée interne"):
        assert title in text


def test_kit_is_not_offered_for_an_unfinished_production(world):
    project = world.projects["LodyCrypto"]
    draft = world.productions.prepare(project, SUBJECT, provider_id=runtime.DEFAULT_PROVIDER)
    text = _text(world.open(project, draft))
    assert "Publication manuelle" not in text


def test_projects_keep_their_own_kits(world):
    crypto, prod_c = world.finish("LodyCrypto")
    audio, prod_a = world.finish("Audiovisuel", "Explique simplement la différence entre Fill et Key en télévision.")
    app_a = world.open(audio, prod_a)
    assert "#LodyCrypto" not in _input(app_a, "_hashtags").value
    app_c = world.open(crypto, prod_c)
    assert "#LodyCrypto" in _input(app_c, "_hashtags").value
    assert world.kits.kits.find_by_production(prod_a.id).id != world.kits.kits.find_by_production(prod_c.id).id


def test_no_secret_or_server_path_is_rendered(world):
    project, production = world.finish()
    app = world.open(project, production)
    rendered = _text(app) + json.dumps([str(w.value) for w in [*app.text_input, *app.text_area]], ensure_ascii=False)
    assert "FAKE-CONFIG-VALUE" not in rendered and str(world.connector.root) not in rendered and "/srv/" not in rendered


def test_copy_button_serialises_text_safely():
    from lody.view_kit import copy_button_html

    html = copy_button_html('</script><script>alert(1)</script> « l’idée » "x"', "Copier")
    assert html.count("</script>") == 1 and "<\\/script>" in html and "clipboard.writeText" in html
