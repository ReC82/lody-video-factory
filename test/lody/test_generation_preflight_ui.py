"""Preflight dans l'interface (vrai connecteur + transport factice) et nouvelle tentative après échec."""

from __future__ import annotations

import pytest

from lody import settings
from lody.generation import mpt_connector as mpt
from lody.generation.demo import DemoConnector
from lody.generation.models import ErrorKind, ProviderError, RemoteState, TaskSnapshot
from lody.generation.service import ProductionService
from lody.generation.store import ProductionRepository
from test.lody.test_generation_connector import CONFIG
from test.lody.test_generation_ui import (  # noqa: F401  (fixtures réutilisées)
    SUBJECT,
    _button,
    _fresh_resources,
    _labels,
    _project,
    _repo,
    _run,
    _text,
    engine,
)

SECRETS = ("FAKE-CONFIG-VALUE-NOT-A-KEY", "FAKE-IMAGE-VALUE-NOT-A-KEY", "FAKE-ELEVEN-VALUE-NOT-A-KEY")
MOONSHOT = CONFIG.replace('llm_provider = "openai"', 'llm_provider = "moonshot"').replace(
    "[app]\n", '[app]\nmoonshot_api_key = ""\n', 1)


@pytest.fixture
def real_engine(lody_env, monkeypatch, tmp_path):
    """L'application avec le VRAI connecteur ; seul le transport HTTP est factice (et interdit tout appel payant)."""
    calls: list[tuple[str, str]] = []

    def transport(method, url, body, headers, timeout):
        calls.append((method, url))
        if url.endswith("/ping"):
            return 200, b'"pong"'
        raise AssertionError(f"appel non autorisé pendant un test : {method} {url}")

    def use(config: str):
        settings.config_path().write_text(config, encoding="utf-8")
        storage = tmp_path / "engine-storage"
        storage.mkdir(exist_ok=True)

        def build():
            connector = mpt.MoneyPrinterTurboConnector("http://engine:8080", storage, settings.config_path(),
                                                       report_path=settings.engine_report_path(),
                                                       transport=transport)
            demo = DemoConnector(settings.data_dir(), make_video=lambda target: target.write_bytes(b"x"))
            return ProductionService(ProductionRepository(settings.db_path()),
                                     {mpt.PROVIDER_ID: connector, "demo": demo}, _Inline())

        monkeypatch.setattr("lody.app.build_service", build)
        return calls

    return use


class _Inline:
    def submit(self, fn, *args):
        return fn(*args)


def _prepare(app, project):
    app.text_area(key=f"request_{project.id}").set_value(SUBJECT).run()
    return _button(app, "Préparer la génération").click().run()


def test_wrong_engine_provider_blocks_the_page_before_any_confirmation(real_engine, caplog):
    caplog.set_level("DEBUG")
    calls = real_engine(MOONSHOT)
    project = _project()
    app = _prepare(_run({"projet": project.id, "vue": "production"}), project)
    text = _text(app)
    assert "Configuration de production" in text and "La génération est bloquée" in text
    assert "écrit ses scripts avec « Moonshot »" in text and "« OpenAI »" in text
    assert "Ouvrir les paramètres du projet" not in _labels(app)          # réglage serveur, pas projet
    assert "Diagnostic administrateur" in [e.label for e in app.expander]
    assert _button(app, "Confirmer et générer la vidéo").disabled
    # même un clic forcé ne confirme rien
    _button(app, "Confirmer et générer la vidéo").click().run()
    production = _repo().list_for_project(project.id)[0]
    assert production.status.value == "EN_ATTENTE_CONFIRMATION" and production.confirmed_at is None
    assert all(url.endswith("/ping") for _, url in calls)
    for secret in SECRETS:
        assert secret not in text and secret not in caplog.text


def test_user_facing_rows_use_plain_words_and_admin_details_are_in_the_expander(real_engine):
    real_engine(MOONSHOT)
    project = _project()
    app = _prepare(_run({"projet": project.id, "vue": "production"}), project)
    user = " ".join(str(m.value) for m in app.markdown if "cfg-row" in str(m.value))
    assert "Texte" in user and "À configurer" in user and "Voix" in user and "Prête" in user and "Moteur" in user and "Disponible" in user
    for technical in ("llm_provider", "api_key", "config.toml", "openai_image"):
        assert technical not in user
    admin = " ".join(str(m.value) for m in app.markdown if "llm_provider=moonshot" in str(m.value))
    assert "clé openai=renseignée" in admin and "clé moonshot=vide/absente" in admin


def test_complete_preflight_enables_confirmation(real_engine):
    real_engine(CONFIG)
    project = _project()
    app = _prepare(_run({"projet": project.id, "vue": "production"}), project)
    text = _text(app)
    assert "La génération est bloquée" not in text and "Prêt" in text and "Prête" in text
    app.checkbox(key=f"accept_partial_{project.id}").check().run()  # aucun tarif configuré : total partiel
    assert not _button(app, "Confirmer et générer la vidéo").disabled


def test_supplied_script_lifts_the_text_block(real_engine):
    real_engine(MOONSHOT)
    project = _project()
    app = _run({"projet": project.id, "vue": "production"})
    app.text_area(key=f"script_{project.id}").set_value("Voici mon script complet, prêt à être lu à voix haute. " * 3)
    app = _prepare(app, project)
    assert "Script fourni" in _text(app) and "La génération est bloquée" not in _text(app)


def test_engine_down_is_shown_as_unavailable(real_engine, monkeypatch):
    def down(method, url, body, headers, timeout):
        raise ConnectionRefusedError

    calls = real_engine(CONFIG)
    original = mpt.MoneyPrinterTurboConnector.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = down
        original(self, *args, **kwargs)

    monkeypatch.setattr(mpt.MoneyPrinterTurboConnector, "__init__", patched)
    project = _project()
    app = _prepare(_run({"projet": project.id, "vue": "production"}), project)
    text = _text(app)
    assert "injoignable" in text and "Indisponible" in text and _button(app, "Confirmer et générer la vidéo").disabled
    assert calls == []


def test_project_page_and_settings_show_real_provider_states_without_choosing_for_the_user(real_engine):
    real_engine(MOONSHOT)
    project = _project()
    page = _text(_run({"projet": project.id}))
    assert "Production réelle impossible" in page and "À configurer" in page and "Prêt" in page
    app = _run({"projet": project.id, "vue": "parametres"})
    labels = [option for box in app.selectbox for option in box.options]
    assert "OpenAI — à configurer" in labels and "Je fournis mon script" in labels
    assert "Aucun autre fournisseur n’est choisi à ta place" in _text(app)
    assert _repo().__class__  # base intacte : le projet garde son choix explicite
    from lody.projects import ProjectRepository

    assert next(p for p in ProjectRepository(settings.db_path()).list_projects() if p.id == project.id).text_provider == "openai"


def test_new_project_defaults_to_a_provider_that_is_really_usable(real_engine):
    real_engine(MOONSHOT)
    app = _run({"vue": "nouveau"})
    assert app.selectbox(key="form_new_text_provider").value == "manual"
    real_engine(CONFIG)
    streamlit_cache_clear()
    app = _run({"vue": "nouveau"})
    assert app.selectbox(key="form_new_text_provider").value == "openai"


def streamlit_cache_clear():
    import streamlit

    streamlit.cache_resource.clear()


# -- nouvelle tentative (faux connecteur sans coût) ------------------------------------------------------------------
def _failed_production(engine):  # noqa: F811
    engine.queue(TaskSnapshot(RemoteState.QUEUED, 0, "Dans la file du moteur"))
    project = _project()
    app = _run({"projet": project.id, "vue": "production"})
    app.text_area(key=f"request_{project.id}").set_value(SUBJECT).run()
    app = _button(app, "Préparer la génération").click().run()
    app = _button(app, "Confirmer et générer la vidéo").click().run()
    error = ProviderError(ErrorKind.KEY_MISSING, "Une clé d’API nécessaire à cette génération n’est pas configurée côté serveur. (échec pendant l’écriture du script).")
    engine.queue(TaskSnapshot(RemoteState.FAILED, 5, error=error))
    return project, _button(app, "Actualiser").click().run()


def test_failed_production_shows_its_cause_and_a_retry_creates_a_new_linked_attempt(engine):  # noqa: F811
    project, app = _failed_production(engine)
    assert "n’est pas configurée côté serveur" in _text(app) and "Échec" in _text(app)
    failed = _repo().list_for_project(project.id)[0]
    app = _button(app, "Préparer à nouveau").click().run()
    text = _text(app)
    assert "Nouvelle tentative de V1" in text and "reste dans l’historique" in text
    assert "nouvelle confirmation sont nécessaires" in text
    assert app.text_area(key=f"request_{project.id}").value == SUBJECT
    assert "Préparer la génération" in _labels(app)                    # pas de « Confirmer » repris
    app = _button(app, "Préparer la génération").click().run()
    assert "Estimation et confirmation" in _text(app)
    rows = {p.id: p for p in _repo().list_for_project(project.id)}
    retry = next(p for p in rows.values() if p.id != failed.id)
    assert rows[failed.id].status.value == "ECHEC" and rows[failed.id].error_code == "key_missing"
    assert retry.status.value == "EN_ATTENTE_CONFIRMATION" and retry.parent_production_id == failed.id
    assert retry.confirmed_at is None and retry.version == 2
    app = _button(app, "Confirmer et générer la vidéo").click().run()   # nouvelle confirmation, explicite
    assert "nouvelle tentative de V1" in _text(app)
    assert _repo().get(failed.id).status.value == "ECHEC"
