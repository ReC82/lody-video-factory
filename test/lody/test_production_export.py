"""Deux téléchargements directs, sans ZIP : logs complets et configuration complète d'une production (#91).

Fonctions pures (``lody.generation.export``) + présence et contenu des deux boutons sur la page de suivi
(AppTest, faux connecteur, aucun appel réseau).
"""

from __future__ import annotations

import json

import pytest

from lody.generation import export
from lody.generation.models import ErrorKind, ProviderError, RemoteState, TaskSnapshot
from test.lody.test_generation_ui import _button, _fresh_resources, _launch, _repo, engine  # noqa: F401

apptest = pytest.importorskip("streamlit.testing.v1")


def test_config_export_is_self_contained_json_with_no_secret(engine):  # noqa: F811
    project, app = _launch(engine)
    production = _repo().list_for_project(project.id)[0]
    text = export.config_export_text(production)
    config = json.loads(text)  # doit être du JSON valide, exploitable tel quel
    assert config["identifiants"]["production_id"] == production.id
    assert config["statut"]["status"] == production.status.value
    assert config["demande_initiale"]["sujet"] == production.subject
    for forbidden in ("api_key", "Bearer ", "-----BEGIN", "AKIA"):
        assert forbidden not in text


def test_config_export_marks_an_in_progress_production_as_partial(engine):  # noqa: F811
    project, app = _launch(engine)
    production = _repo().list_for_project(project.id)[0]
    config = json.loads(export.config_export_text(production))
    assert config["instantane_partiel"] is True


def test_logs_export_is_chronological_and_mentions_real_events(engine):  # noqa: F811
    project, app = _launch(engine)
    engine.queue(TaskSnapshot(RemoteState.RUNNING, 40, "Génération des images"))
    app = _button(app, "Actualiser").click().run()
    production = _repo().list_for_project(project.id)[0]
    text = export.logs_export_text(production)
    assert "production créée" in text and "confirmée par l'utilisateur" in text
    assert "étape changée" in text and "Génération des images" in text
    # Chronologique : "créée" doit apparaître avant "confirmée" dans le texte.
    assert text.index("production créée") < text.index("confirmée par l'utilisateur")


def test_logs_export_records_a_failure_with_its_step(engine):  # noqa: F811
    project, app = _launch(engine)
    error = ProviderError(ErrorKind.QUOTA, "Le fournisseur signale un quota insuffisant.")
    engine.queue(TaskSnapshot(RemoteState.FAILED, 40, error=error))
    _button(app, "Actualiser").click().run()
    production = _repo().list_for_project(project.id)[0]
    text = export.logs_export_text(production)
    assert "échec final" in text and "quota" in text.lower()


def test_old_production_without_events_reports_it_honestly_instead_of_inventing_data():
    from lody.generation.store import Production
    from lody.generation.models import ProductionStatus as S

    base = dict(
        id="prd_old", project_id="prj_x", root_production_id="prd_old", parent_production_id=None, version=1,
        subject="sujet", brief={}, script="script", script_source="generated", storyboard=[], visual_prompts=[],
        params={}, cost_currency="EUR", cost_low=None, cost_high=None, cost_partial=False, cost_detail={},
        confirmed_at="2020-01-01T00:00:00+00:00", provider="scripted", external_task_id="task-1",
        idempotency_key="k", status=S.TERMINEE, progress=100, current_step="Terminée", error_code="",
        error_message="", created_at="2020-01-01T00:00:00+00:00", started_at="2020-01-01T00:00:00+00:00",
        finished_at="2020-01-01T00:10:00+00:00", updated_at="2020-01-01T00:10:00+00:00", last_polled_at=None,
        video_ref="tasks/task-1/final-1.mp4", video_duration=42.0, trace={},  # pas de trace["events"] (#91)
    )
    production = Production(**base)
    text = export.logs_export_text(production)
    assert "journal détaillé indisponible" in text
    config = json.loads(export.config_export_text(production))
    assert config["mode_script"] == export.UNAVAILABLE
    assert config["cout"]["estimation"] == export.UNAVAILABLE


def test_download_buttons_are_present_on_the_tracking_page(engine):  # noqa: F811
    project, app = _launch(engine)
    labels = [getattr(button, "label", "") for button in app.get("download_button")]
    assert "Télécharger les logs complets" in labels
    assert "Télécharger la configuration complète" in labels
