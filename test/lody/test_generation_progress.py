"""Suivi de génération par étapes réelles (#93) — remplace le pourcentage global trompeur.

Deux niveaux de test :
  1. Fonctions pures de ``lody.generation.progress`` (aucun Streamlit, aucun réseau) : tous les états
     possibles (en attente / en cours / terminée / en erreur) pour chaque étape, le script manuel, le
     compteur d'images, et les horodatages honnêtes (jamais un temps restant inventé).
  2. Événements simulés représentatifs (``ScriptedConnector`` + AppTest), sans aucun appel payant :
     progression des images, erreur, nouvelle tentative automatique après une panne transitoire du
     moteur, et reprise du suivi après actualisation de la page.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lody.generation import progress as prog
from lody.generation.models import ErrorKind, ProductionStatus as S, ProviderError, RemoteState, TaskSnapshot
from lody.generation.store import Production
from test.lody.test_generation_ui import _button, _fresh_resources, _launch, _repo, _text, engine  # noqa: F401

apptest = pytest.importorskip("streamlit.testing.v1")


def _production(**overrides) -> Production:
    base = dict(
        id="prd_x", project_id="prj_x", root_production_id="prd_x", parent_production_id=None, version=1,
        subject="sujet", brief={}, script="", script_source="generated", storyboard=[], visual_prompts=[],
        params={}, cost_currency="EUR", cost_low=None, cost_high=None, cost_partial=True, cost_detail={},
        confirmed_at=None, provider="scripted", external_task_id="task-1", idempotency_key="k",
        status=S.EN_COURS, progress=None, current_step="Génération de la voix", error_code="",
        error_message="", created_at="x", started_at=None, finished_at=None, updated_at="x",
        last_polled_at=None, video_ref="", video_duration=None, trace={},
    )
    base.update(overrides)
    return Production(**base)


# ======================================================================================================
# -- compute_steps() : fonction centrale ------------------------------------------------------------------
# ======================================================================================================
def test_compute_steps_marks_everything_before_the_active_step_as_done_and_after_as_pending():
    production = _production(current_step="Génération de la voix")
    steps = {s.label: s.state for s in prog.compute_steps(production)}
    assert steps[prog.PREPARATION] == prog.DONE
    assert steps[prog.SCRIPT] == prog.DONE
    assert steps[prog.SCENES] == prog.DONE
    assert steps[prog.VOICE] == prog.IN_PROGRESS
    assert steps[prog.SUBTITLES] == prog.PENDING
    assert steps[prog.IMAGES] == prog.PENDING
    assert steps[prog.ASSEMBLY] == prog.PENDING
    assert steps[prog.READY] == prog.PENDING


def test_compute_steps_respects_the_real_pipeline_order_subtitles_before_images():
    """Vérifié ligne à ligne dans app/services/task.py:_run_pipeline — jamais supposé depuis l'exemple
    de l'énoncé, qui n'est qu'un affichage, pas un ordre d'exécution."""
    assert prog.STEP_ORDER.index(prog.SUBTITLES) < prog.STEP_ORDER.index(prog.IMAGES)
    production = _production(current_step="Génération des images")
    steps = {s.label: s.state for s in prog.compute_steps(production)}
    assert steps[prog.SUBTITLES] == prog.DONE
    assert steps[prog.IMAGES] == prog.IN_PROGRESS


def test_compute_steps_on_failure_marks_the_failed_step_in_error_and_leaves_the_rest_pending():
    production = _production(status=S.ECHEC, current_step="Échec", trace={"failed_at_step": "Génération des images"})
    steps = {s.label: s.state for s in prog.compute_steps(production)}
    assert steps[prog.VOICE] == prog.DONE and steps[prog.SUBTITLES] == prog.DONE
    assert steps[prog.IMAGES] == prog.ERROR
    assert steps[prog.ASSEMBLY] == prog.PENDING and steps[prog.READY] == prog.PENDING


@pytest.mark.parametrize("raw_step", [
    "Écriture du script", "Préparation des scènes", "Envoi au moteur", "Dans la file du moteur",
    "En attente dans la file du moteur", "En attente dans la file", "Préparation", "Génération de la voix",
    "Création des sous-titres", "Génération des images", "Montage de la vidéo", "Terminée",
])
def test_compute_steps_never_crashes_on_any_known_raw_step(raw_step):
    production = _production(current_step=raw_step)
    steps = prog.compute_steps(production)
    assert sum(1 for s in steps if s.state == prog.IN_PROGRESS) <= 1  # jamais deux étapes "en cours" à la fois


def test_compute_steps_on_an_unrecognised_raw_step_falls_back_to_scenes_never_further():
    """Production ancienne ou connecteur futur avec un libellé inconnu : jamais une inférence en avance
    sur ce qui est honnêtement su (voir le docstring du module)."""
    production = _production(current_step="Un libellé jamais vu")
    steps = {s.label: s.state for s in prog.compute_steps(production)}
    assert steps[prog.SCENES] == prog.IN_PROGRESS
    assert steps[prog.VOICE] == prog.PENDING


def test_compute_steps_manual_script_is_instantly_done_never_shown_in_progress():
    production = _production(script_source="manual", current_step="Préparation des scènes")
    steps = {s.label: s for s in prog.compute_steps(production)}
    assert steps[prog.SCRIPT].state == prog.DONE
    assert "manuellement" in steps[prog.SCRIPT].detail


def test_compute_steps_ready_step_is_done_only_once_the_production_is_terminee():
    in_progress = _production(status=S.EN_COURS, current_step="Montage de la vidéo")
    assert {s.label: s.state for s in prog.compute_steps(in_progress)}[prog.READY] == prog.PENDING
    finished = _production(status=S.TERMINEE, current_step="Terminée")
    assert {s.label: s.state for s in prog.compute_steps(finished)}[prog.READY] == prog.DONE


# -- compteur d'images : « N images terminées sur M » -----------------------------------------------------
def test_compute_steps_images_counter_reflects_only_what_was_actually_passed_in():
    production = _production(current_step="Génération des images", visual_prompts=["p"] * 10)
    detail = next(s for s in prog.compute_steps(production, images_done=3) if s.label == prog.IMAGES).detail
    assert "3 image(s) reçue(s) sur 10 prévue(s)" in detail


def test_compute_steps_images_counter_without_a_known_total_still_shows_the_count():
    production = _production(current_step="Génération des images")  # aucun storyboard/visual_prompts connu
    detail = next(s for s in prog.compute_steps(production, images_done=2) if s.label == prog.IMAGES).detail
    assert detail == "2 image(s) reçue(s)."


def test_compute_steps_distinguishes_a_new_image_from_a_retry_by_only_counting_files_actually_present():
    """Une tentative automatique qui échoue puis réussit ne produit jamais de fichier intermédiaire (voir
    le docstring du module) : le compteur ne voit donc jamais « deux images » pour une seule scène, et une
    image supplémentaire (fichier en plus) fait avancer le compteur d'exactement un."""
    production = _production(current_step="Génération des images", visual_prompts=["p"] * 3)
    before = next(s for s in prog.compute_steps(production, images_done=2) if s.label == prog.IMAGES).detail
    after_retry_then_success_on_same_scene = next(
        s for s in prog.compute_steps(production, images_done=2) if s.label == prog.IMAGES).detail
    after_one_more_image = next(
        s for s in prog.compute_steps(production, images_done=3) if s.label == prog.IMAGES).detail
    assert before == after_retry_then_success_on_same_scene  # une tentative ne change jamais le compteur
    assert after_one_more_image != before  # une image en plus, elle, le change


# -- horodatages honnêtes : jamais un temps restant inventé -------------------------------------------------
def test_step_elapsed_seconds_is_none_without_the_new_trace_field():
    """Production antérieure à #93 : le champ n'existe pas encore, jamais une valeur inventée."""
    assert prog.step_elapsed_seconds(_production(trace={})) is None


def test_step_elapsed_seconds_counts_from_the_last_real_step_change():
    now = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)
    started = (now - timedelta(seconds=42)).isoformat()
    production = _production(trace={"current_step_started_at": started})
    assert prog.step_elapsed_seconds(production, now) == 42


def test_last_image_activity_seconds_is_none_when_no_image_was_ever_received():
    assert prog.last_image_activity_seconds([]) is None


def test_last_image_activity_seconds_uses_the_most_recent_capture():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    images = [
        {"captured_at": (now - timedelta(seconds=90)).isoformat()},
        {"captured_at": (now - timedelta(seconds=10)).isoformat()},
    ]
    assert prog.last_image_activity_seconds(images, now) == 10


def test_waiting_text_is_honest_when_nothing_is_known_yet():
    assert prog.waiting_text(None) == "En attente de la réponse du fournisseur."


def test_waiting_text_never_mentions_a_remaining_time():
    text = prog.waiting_text(37)
    assert "restant" not in text and "reste" not in text and "estimé" not in text


def test_elapsed_label_formats_minutes_only_when_needed():
    assert prog.elapsed_label(45) == "45 s"
    assert prog.elapsed_label(125) == "2 min 05 s"
    assert prog.elapsed_label(None) == "—"


# ======================================================================================================
# -- événements simulés représentatifs (AppTest + ScriptedConnector), sans aucun appel payant --------------
# ======================================================================================================
def test_simulated_image_progression_shows_the_real_count_and_grows_across_refreshes(engine):  # noqa: F811
    """« 3 images terminées sur 10 » : respecte les générations parallèles (le connecteur peut renvoyer
    plusieurs nouveaux fichiers d'un coup entre deux actualisations)."""
    project, app = _launch(engine)
    engine.queue(TaskSnapshot(RemoteState.RUNNING, 40, "Génération des images"))
    engine.scene_images = [{"ref": f"img-{i}", "name": f"img-{i}.png", "captured_at": "2026-01-01T00:00:00+00:00"}
                            for i in range(3)]
    app = _button(app, "Actualiser").click().run()
    text = _text(app)
    assert "3 image(s) reçue(s)" in text and "step-en_cours" in text

    # Deux images de plus arrivent EN MÊME TEMPS (génération parallèle) : le compteur suit sans confondre
    # ça avec une nouvelle tentative sur une image déjà comptée.
    engine.queue(TaskSnapshot(RemoteState.RUNNING, 40, "Génération des images"))
    engine.scene_images = [{"ref": f"img-{i}", "name": f"img-{i}.png", "captured_at": "2026-01-01T00:00:00+00:00"}
                            for i in range(5)]
    app = _button(app, "Actualiser").click().run()
    assert "5 image(s) reçue(s)" in _text(app)


def test_simulated_error_shows_which_step_failed_and_keeps_the_earlier_steps_done(engine):  # noqa: F811
    project, app = _launch(engine)
    engine.queue(TaskSnapshot(RemoteState.RUNNING, 40, "Génération des images"))  # on entre d'abord l'étape...
    app = _button(app, "Actualiser").click().run()
    error = ProviderError(ErrorKind.QUOTA, "Le fournisseur signale un quota insuffisant (échec pendant les images).")
    engine.queue(TaskSnapshot(RemoteState.FAILED, 40, error=error))  # ...puis elle échoue
    app = _button(app, "Actualiser").click().run()
    text = _text(app)
    assert 'step-erreur"><span class="run-step-dot" aria-hidden="true"></span><div><p class="run-step-label">Génération des images' in text
    assert "run-step-tag\">Terminée" in text  # les étapes précédentes (voix, sous-titres) restent acquises
    assert "quota insuffisant" in text


def test_simulated_transient_provider_error_is_an_automatic_retry_not_a_failure(engine):  # noqa: F811
    """Panne transitoire du moteur pendant un sondage (réseau, 5xx) : la production reste ACTIVE, et
    l'interface l'annonce comme une nouvelle tentative automatique — jamais comme un échec définitif."""
    project, app = _launch(engine)
    engine.queue(ProviderError(ErrorKind.TIMEOUT, "Le moteur ne répond pas (délai dépassé)."))
    app = _button(app, "Actualiser").click().run()
    text = _text(app)
    assert "Nouvelle tentative automatique" in text
    assert "délai dépassé" in text
    production = _repo().list_for_project(project.id)[0]
    assert production.status in (S.EN_FILE, S.EN_COURS, S.CONFIRMEE)  # toujours active, pas en échec


def test_simulated_resume_after_reload_keeps_the_step_and_image_count(engine):  # noqa: F811
    """Reprise du suivi après actualisation ou réouverture de la page (#93) : rien n'est recalculé depuis
    zéro, tout est relu depuis la production et une nouvelle interrogation du moteur."""
    project, app = _launch(engine)
    engine.queue(TaskSnapshot(RemoteState.RUNNING, 40, "Génération des images"))
    engine.scene_images = [{"ref": "img-0", "name": "img-0.png", "captured_at": "2026-01-01T00:00:00+00:00"}]
    app = _button(app, "Actualiser").click().run()
    assert "1 image(s) reçue(s)" in _text(app)

    production = _repo().list_for_project(project.id)[0]
    from test.lody.test_generation_ui import _run

    # Le moteur, interrogé à nouveau au moment de la réouverture, annonce toujours la même étape en
    # cours (son état réel n'a pas changé) : jamais recalculé depuis une session, toujours relu.
    engine.queue(TaskSnapshot(RemoteState.RUNNING, 40, "Génération des images"))
    reopened = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    text = _text(reopened)
    assert "Suivi de la production" in text
    assert "1 image(s) reçue(s)" in text  # relu depuis le disque (list_scene_images), pas depuis une session
    assert "step-en_cours" in text
