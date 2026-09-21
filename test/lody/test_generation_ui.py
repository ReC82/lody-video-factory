"""Parcours d'interface (AppTest) : sujet → estimation → confirmation → suivi → résultat → V2. Faux connecteur."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from lody import settings
from lody.generation import runtime
from lody.generation.costing import PriceBook
from lody.generation.models import ErrorKind, ProviderError, RemoteState, TaskSnapshot
from lody.generation.service import ProductionService
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS
from test.lody.fakes import SCRIPT, ScriptedConnector, SyncExecutor

apptest = pytest.importorskip("streamlit.testing.v1")
streamlit = pytest.importorskip("streamlit")

WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
SUBJECT = "Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming."
PRICES = PriceBook("EUR", {("text", "openai"): Decimal("0.02"), ("visual", "openai_image"): Decimal("0.04"),
                           ("voice", "elevenlabs"): Decimal("0.30"), ("music", "elevenlabs"): Decimal("0.10")})


@pytest.fixture(autouse=True)
def _fresh_resources():
    streamlit.cache_resource.clear()
    yield
    streamlit.cache_resource.clear()


@pytest.fixture
def engine(lody_env, monkeypatch, tmp_path):
    """L'application avec un faux moteur enregistré à la place du connecteur réel."""
    connector = ScriptedConnector(tmp_path / "engine-storage")
    holder = {"book": PRICES}
    clock = {"now": 5000.0}

    def build():
        repo = ProductionRepository(settings.db_path())
        return ProductionService(repo, {runtime.DEFAULT_PROVIDER: connector, runtime.DEMO_PROVIDER: runtime.demo.DemoConnector(
            settings.data_dir(), clock=lambda: clock["now"], make_video=lambda target: target.write_bytes(b"demo-video"))},
            SyncExecutor(), price_book=lambda: holder["book"])

    monkeypatch.setattr("lody.app.build_service", build)
    connector.holder = holder
    connector.clock = clock
    return connector


def _project(name="LodyCrypto"):
    repo = ProjectRepository(settings.db_path())
    repo.seed_defaults(SEED_PROJECTS)
    return next(p for p in repo.list_projects() if p.name == name)


def _repo():
    return ProductionRepository(settings.db_path())


def _run(query=None):
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=30)
    for key, value in (query or {}).items():
        app.query_params[key] = value
    return app.run()


def _text(app):
    return " ".join(str(item.value) for item in app.markdown)


def _button(app, label):
    return next(button for button in app.button if button.label == label)


def _labels(app):
    return [button.label for button in app.button]


def _prepare(app, project, subject=SUBJECT):
    app.text_area(key=f"request_{project.id}").set_value(subject).run()
    return _button(app, "Préparer la génération").click().run()


def _launch(engine):
    engine.queue(TaskSnapshot(RemoteState.QUEUED, 0, "Dans la file du moteur"))  # premier suivi : le moteur n'a pas commencé
    project = _project()
    app = _run({"projet": project.id, "vue": "production"})
    app = _prepare(app, project)
    app = _button(app, "Confirmer et générer la vidéo").click().run()
    return project, app


# -- libellés et estimation -----------------------------------------------------------------------------
def test_button_labels_follow_the_flow_and_the_misleading_label_is_gone(engine):
    project = _project()
    app = _run({"projet": project.id, "vue": "production"})
    assert "Préparer la génération" in _labels(app) and "Générer la vidéo" not in _labels(app)
    app = _prepare(app, project)
    assert "Confirmer et générer la vidéo" in _labels(app) and "Préparer la génération" not in _labels(app)
    assert engine.calls == ["check_ready"]  # préparer = estimation locale + vérification sans coût, jamais d'appel payant


def test_estimate_summarises_subject_duration_scenes_providers_and_cost(engine):
    project = _project()
    app = _prepare(_run({"projet": project.id, "vue": "production"}), project)
    text = _text(app)
    for expected in ("Estimation et confirmation", SUBJECT, "45 à 60 secondes", "6 à 10 scènes", "OpenAI", "ElevenLabs",
                     "Montants indicatifs", "Une seule confirmation", "plusieurs appels payants", "≈ "):
        assert expected in text, expected
    assert "tarif non configuré" not in text and "Total partiel" not in text
    assert not _button(app, "Confirmer et générer la vidéo").disabled


def test_missing_rates_show_partial_total_and_need_an_explicit_acknowledgement(engine):
    engine.holder["book"] = PriceBook("EUR", {("visual", "openai_image"): Decimal("0.04")})
    project = _project()
    app = _prepare(_run({"projet": project.id, "vue": "production"}), project)
    text = _text(app)
    assert "tarif non configuré" in text and "Total partiel" in text and "(partiel)" in text
    assert _button(app, "Confirmer et générer la vidéo").disabled
    app.checkbox(key=f"accept_partial_{project.id}").check().run()
    assert not _button(app, "Confirmer et générer la vidéo").disabled


def test_editing_the_request_invalidates_the_estimate(engine):
    project = _project()
    app = _prepare(_run({"projet": project.id, "vue": "production"}), project)
    app.text_area(key=f"request_{project.id}").set_value(SUBJECT + " Version longue.").run()
    assert "Préparer la génération" in _labels(app) and "Estimation et confirmation" not in _text(app)


def test_blocking_readiness_disables_confirmation_with_a_clear_message(engine):
    from lody.generation.models import ReadinessIssue

    engine.issues = [ReadinessIssue("text_engine_mismatch", "Le moteur écrit ses scripts avec un autre fournisseur.")]
    project = _project()
    app = _prepare(_run({"projet": project.id, "vue": "production"}), project)
    assert "autre fournisseur" in _text(app) and _button(app, "Confirmer et générer la vidéo").disabled


# -- lancement, suivi, résultat ------------------------------------------------------------------------------
def test_single_confirmation_launches_and_opens_the_tracking_page(engine):
    project, app = _launch(engine)
    assert app.query_params["vue"] == ["suivi"] or app.query_params["vue"] == "suivi"
    text = _text(app)
    assert "Suivi de la production" in text and "Dans la file du moteur" in text and "En file d’attente" in text
    assert engine.calls.count("submit") == 1 and engine.calls.count("write_script") == 1
    productions = _repo().list_for_project(project.id)
    assert len(productions) == 1 and productions[0].external_task_id == "task-0001"


def test_tracking_shows_real_steps_without_inventing_a_percentage(engine):
    project, app = _launch(engine)
    assert "Avancement du moteur" not in _text(app)  # file d'attente : pas de pourcentage
    engine.queue(TaskSnapshot(RemoteState.RUNNING, 25, "Génération de la voix"))
    app = _button(app, "Actualiser").click().run()
    text = _text(app)
    assert "Génération de la voix" in text and "En cours" in text and "Avancement du moteur : 25 %" in text
    assert "Temps écoulé" in text and "Tu peux quitter cette page" in text


def test_tracking_survives_leaving_and_coming_back(engine):
    project, app = _launch(engine)
    production = _repo().list_for_project(project.id)[0]
    _button(app, "Retour au projet").click().run()
    assert "Tes vidéos" in _text(app) and "V1 ·" in _text(app)
    reopened = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    assert "Suivi de la production" in _text(reopened)
    assert engine.calls.count("submit") == 1


def test_success_shows_video_download_script_storyboard_parameters_and_cost(engine):
    project, app = _launch(engine)
    engine.queue(engine.done("task-0001", warnings=("elevenlabs_bgm_failed",)))
    app = _button(app, "Actualiser").click().run()
    text = _text(app)
    assert "Ta vidéo est prête" in text and "52 secondes" in text and "Coût estimé" in text
    assert "non mesurable" in text and "elevenlabs_bgm_failed" in text
    assert app.get("video"), "lecteur vidéo intégré"
    assert any(getattr(element, "label", "") == "Télécharger la vidéo" for element in app.get("download_button"))
    assert "Créer une V2" in _labels(app)
    labels = [expander.label for expander in app.expander]
    assert {"Script utilisé", "Storyboard et prompts visuels", "Paramètres principaux"} <= set(labels)
    assert "Prompt visuel" in text and SCRIPT.split(".")[0] in text and "Phrase entière" in text


def test_failure_is_readable_and_keeps_script_and_storyboard(engine):
    project, app = _launch(engine)
    error = ProviderError(ErrorKind.QUOTA, "Le fournisseur signale un quota ou un crédit insuffisant. (échec pendant les images).")
    engine.queue(TaskSnapshot(RemoteState.FAILED, 40, error=error))
    app = _button(app, "Actualiser").click().run()
    text = _text(app)
    assert "quota ou un crédit insuffisant" in text and "Échec" in text and "conservés" in text
    assert "Préparer à nouveau" in _labels(app) and "Créer une V2 avec ce script" in _labels(app)
    assert "Prompt visuel" in text


def test_engine_down_does_not_break_the_page_or_lose_the_production(engine):
    project, app = _launch(engine)
    engine.queue(ProviderError(ErrorKind.UNAVAILABLE, "Le moteur de génération est injoignable pour le moment."))
    app = _button(app, "Actualiser").click().run()
    assert not app.exception
    assert "Dernière vérification impossible" in _text(app) and "En file d’attente" in _text(app)


def test_missing_video_is_reported_without_crashing(engine):
    project, app = _launch(engine)
    engine.queue(engine.done("task-0001"))
    app = _button(app, "Actualiser").click().run()
    (engine.root / "tasks" / "task-0001" / "final-1.mp4").unlink()
    production = _repo().list_for_project(project.id)[0]
    reopened = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    assert not reopened.exception and "Vidéo introuvable" in _text(reopened)


def test_a_second_launch_is_blocked_while_one_is_running(engine):
    project, app = _launch(engine)
    again = _run({"projet": project.id, "vue": "production"})
    assert "Une génération est déjà en cours" in _text(again) and "Voir le suivi" in _labels(again)
    assert _button(again, "Préparer la génération").disabled
    assert engine.calls.count("submit") == 1


def test_unknown_production_or_foreign_project_falls_back_gracefully(engine):
    project = _project()
    app = _run({"projet": project.id, "vue": "suivi", "production": "prd_inexistant"})
    assert not app.exception and "Cette production est introuvable" in _text(app)
    other = _project("Audiovisuel")
    _, launched = _launch(engine)
    production = _repo().list_for_project(project.id)[0]
    foreign = _run({"projet": other.id, "vue": "suivi", "production": production.id})
    assert "Cette production est introuvable" in _text(foreign)


# -- V2 ---------------------------------------------------------------------------------------------------------------------
def test_v2_flow_edit_script_recompute_and_confirm_again(engine):
    project, app = _launch(engine)
    engine.queue(engine.done("task-0001"))
    app = _button(app, "Actualiser").click().run()
    v1 = _repo().list_for_project(project.id)[0]
    app = _button(app, "Créer une V2").click().run()
    text = _text(app)
    assert "Créer une V2" in text and "régénère tout" in text and "nouveaux appels payants" in text
    assert "aucun média de la version précédente n’est réutilisé" in text.replace("’", "’")
    v2 = next(p for p in _repo().list_for_project(project.id) if p.version == 2)
    assert v2.status.value == "BROUILLON" and v2.parent_production_id == v1.id and v2.script == SCRIPT
    field = app.text_area(key=f"v2_script_{v2.id}")
    assert field.value == SCRIPT and "Durée approximative" in _text(app)
    edited = SCRIPT + " Voici une phrase ajoutée pour la deuxième version."
    field.set_value(edited).run()
    assert "Confirmer et générer la V2" not in _labels(app)  # tant que rien n'est recalculé
    app = _button(app, "Recalculer la durée et le coût").click().run()
    assert "Confirmer et générer la V2" in _labels(app) and "Estimation et confirmation" in _text(app)
    assert "nouveaux appels payants" in _text(app)
    assert engine.calls.count("submit") == 1  # pas de lancement sans nouvelle confirmation
    app = _button(app, "Confirmer et générer la V2").click().run()
    assert "Suivi de la production" in _text(app) and engine.calls.count("submit") == 2
    assert engine.submitted[1].script == edited
    versions = _repo().chain(v1.id)
    assert [(p.version, p.status.value) for p in versions][0] == (1, "TERMINEE")
    assert versions[1].version == 2 and versions[1].status.value in ("EN_FILE", "EN_COURS")
    assert "V1" in _labels(app) and "V2" in _labels(app)


# -- mode démonstration -----------------------------------------------------------------------------------------------------------
def test_demo_mode_runs_end_to_end_with_no_provider_call(engine):
    clock = engine.clock
    project = _project()
    app = _run({"projet": project.id, "vue": "production"})
    app.toggle(key=f"demo_{project.id}").set_value(True).run()
    app = _prepare(app, project)
    assert "Mode démonstration" in _text(app) and not _button(app, "Confirmer et générer la vidéo").disabled
    app = _button(app, "Confirmer et générer la vidéo").click().run()
    assert "Démonstration" in _text(app)
    clock["now"] += 60
    app = _button(app, "Actualiser").click().run()
    assert "Ta vidéo est prête" in _text(app) and app.get("video")
    assert engine.calls == []  # le faux moteur « réel » n'a jamais été touché
    app = _button(app, "Créer une V2").click().run()
    assert "Créer une V2" in _text(app)


# -- rendu corrigé (réparation technique) ------------------------------------------------------------------------------
def test_repaired_render_is_shown_first_with_the_original_kept_and_no_cost(engine):
    project, app = _launch(engine)
    engine.queue(engine.done("task-0001"))
    _button(app, "Actualiser").click().run()
    production = _repo().list_for_project(project.id)[0]
    folder = engine.root / "tasks" / "task-0001" / "repair"
    folder.mkdir()
    (folder / "final-1-repaired.mp4").write_bytes(b"repaired")
    (folder / "final-1.original.mp4").write_bytes(b"fake-mp4")
    _repo().update(production.id, assets=[
        *production.assets,
        {"kind": "repaired_video", "ref": "tasks/task-0001/repair/final-1-repaired.mp4"},
        {"kind": "original_video", "ref": "tasks/task-0001/repair/final-1.original.mp4"}])
    submits = engine.calls.count("submit")
    app = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    text = _text(app)
    assert "Réparation technique" in text and "Rendu corrigé" in text and "Rendu original (conservé)" in text
    assert "aucune nouvelle génération, aucun fournisseur appelé, rien de facturé" in text
    assert len(app.get("video")) == 2                                               # corrigé + original, lisibles
    labels = [getattr(element, "label", "") for element in app.get("download_button")]
    assert "Télécharger le rendu corrigé" in labels and "Télécharger la vidéo" in labels
    assert engine.calls.count("submit") == submits and _repo().get(production.id).status.value == "TERMINEE"


def test_a_repaired_asset_pointing_outside_the_production_is_ignored(engine):
    project, app = _launch(engine)
    engine.queue(engine.done("task-0001"))
    _button(app, "Actualiser").click().run()
    production = _repo().list_for_project(project.id)[0]
    other = engine.root / "tasks" / "task-9999"
    other.mkdir(parents=True)
    (other / "final-1.mp4").write_bytes(b"other")
    _repo().update(production.id, assets=[*production.assets,
                                          {"kind": "repaired_video", "ref": "tasks/task-9999/final-1.mp4"}])
    app = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    assert not app.exception and "Rendu corrigé" not in _text(app) and len(app.get("video")) == 1
