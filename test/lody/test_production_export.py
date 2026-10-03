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


# -- correspondance scène → image, modèle d'images, modèle de voix (#92) ----------------------------------
def _production_with_storyboard(**overrides):
    from lody.generation.store import Production
    from lody.generation.models import ProductionStatus as S

    base = dict(
        id="prd_x", project_id="prj_x", root_production_id="prd_x", parent_production_id=None, version=1,
        subject="sujet", brief={}, script="script", script_source="generated",
        storyboard=[{"index": 1, "narration": "Une.", "prompt": "p1", "seconds": 3.0, "action": "assis"},
                   {"index": 2, "narration": "Deux.", "prompt": "p2", "seconds": 3.0, "action": ""},
                   {"index": 3, "narration": "Trois.", "prompt": "p3", "seconds": 3.0, "action": "bras levés"}],
        visual_prompts=[], params={}, cost_currency="EUR", cost_low=None, cost_high=None, cost_partial=False,
        cost_detail={}, confirmed_at="2020-01-01T00:00:00+00:00", provider="scripted", external_task_id="task-1",
        idempotency_key="k", status=S.TERMINEE, progress=100, current_step="Terminée", error_code="",
        error_message="", created_at="2020-01-01T00:00:00+00:00", started_at="2020-01-01T00:00:00+00:00",
        finished_at="2020-01-01T00:10:00+00:00", updated_at="2020-01-01T00:10:00+00:00", last_polled_at=None,
        video_ref="tasks/task-1/final-1.mp4", video_duration=9.0, trace={},
    )
    base.update(overrides)
    return Production(**base)


def test_scene_image_correspondence_maps_by_position_and_flags_missing_scenes():
    """3 scènes, seulement 2 images reçues (le moteur peut s'arrêter avant la fin — cause établie #92) :
    la 3e scène doit le dire explicitement, jamais une image inventée ou mal associée."""
    production = _production_with_storyboard()
    images = [{"name": "img-a.png", "captured_at": "2020-01-01T00:05:00+00:00"},
             {"name": "img-b.png", "captured_at": "2020-01-01T00:06:00+00:00"}]
    config = export.build_config_export(production, images)
    rows = config["correspondance_scene_image"]
    assert rows[0] == {"scene": 1, "image_recue": "img-a.png", "recue_le": "2020-01-01T00:05:00+00:00", "statut": "reçue"}
    assert rows[1]["image_recue"] == "img-b.png"
    assert rows[2]["image_recue"] is None
    assert "aucune image dédiée reçue" in rows[2]["statut"]


def test_scene_image_correspondence_is_explicit_when_images_were_not_fetched():
    production = _production_with_storyboard()
    config = export.build_config_export(production)  # images=None : jamais fetché, jamais prétendre le contraire
    assert "indisponible" in config["correspondance_scene_image"]


def test_storyboard_export_includes_the_action_field():
    production = _production_with_storyboard()
    config = export.build_config_export(production)
    assert config["storyboard"][0]["action"] == "assis"
    assert config["storyboard"][1]["action"] == ""


def test_image_model_and_voice_model_are_exported_when_known():
    production = _production_with_storyboard(trace={
        "image_model": "gpt-image-2",
        "voice": {"provider": "elevenlabs", "name": "V", "voice_id": "abc", "model": "eleven_multilingual_v2",
                  "source": "project", "fallback": False, "reason": "projet"},
    })
    config = export.build_config_export(production)
    assert config["modele_images"] == "gpt-image-2"
    assert config["voix"]["transmise_trace"]["model"] == "eleven_multilingual_v2"


def test_image_model_is_explicitly_unavailable_when_unknown():
    production = _production_with_storyboard(trace={})
    config = export.build_config_export(production)
    assert config["modele_images"] == export.UNAVAILABLE


def test_character_reference_image_usage_is_exported_when_recorded():
    """#92 : la trace déjà posée par ``ProductionService._trace`` (``used``/``character_name``/
    ``reference_file``) doit apparaître dans l'export — jamais seulement calculée, jamais perdue."""
    production = _production_with_storyboard(trace={
        "reference_image": {"used": True, "character_name": "Eli", "reference_file": "references/p/characters/c/abc.png"},
    })
    config = export.build_config_export(production)
    assert config["reference_visuelle_personnage"] == {
        "used": True, "character_name": "Eli", "reference_file": "references/p/characters/c/abc.png",
    }


def test_character_reference_image_usage_is_explicitly_unavailable_before_the_ticket():
    production = _production_with_storyboard(trace={})
    config = export.build_config_export(production)
    assert config["reference_visuelle_personnage"] == export.UNAVAILABLE
