"""Interface : traçabilité par production, profil visuel dans les réglages, isolation des sessions entre projets."""

from __future__ import annotations

from lody import settings
from lody.generation.models import ProductionStatus as S
from lody.generation.store import ProductionRepository
from test.lody.test_generation_ui import (  # noqa: F401  (fixtures et outils réutilisés)
    SUBJECT,
    _button,
    _fresh_resources,
    _labels,
    _launch,
    _project,
    _repo,
    _run,
    _text,
    engine,
)

DIAG = "Diagnostic administrateur — traçabilité de la production"


def _finished(engine):  # noqa: F811
    project, app = _launch(engine)
    engine.queue(engine.done("task-0001"))
    _button(app, "Actualiser").click().run()
    return project, _repo().list_for_project(project.id)[0]


def test_diagnostic_shows_project_snapshot_brief_prompts_params_and_origins_without_secrets(engine):  # noqa: F811
    project, production = _finished(engine)
    assert production.snapshot["version"] and production.trace["origins"]
    app = _run({"projet": project.id, "vue": "suivi", "production": production.id})
    assert DIAG in [expander.label for expander in app.expander]
    text = _text(app) + " " + " ".join(str(code.value) for code in app.code)
    assert f"LodyCrypto ({project.id})" in text and production.snapshot["version"] in text and production.id in text
    assert "Version des paramètres" in text and "Instantané créé le" in text and "Brief utilisé" in text
    assert "Paramètres transmis au moteur" in text and "Origine de chaque valeur" in text
    assert "Prompt système du script" in text and "moteur — prompt par défaut" in text
    assert "voix" in text.lower() and "Débutants complets en crypto" in text
    # prompt exact de chaque scène du storyboard
    for scene in production.storyboard:
        assert scene["prompt"] in text
    assert "Consignes visuelles" in text and "Ne montre jamais :" in text and "aucun studio de télévision" in text
    for secret in ("FAKE-CONFIG-VALUE-NOT-A-KEY", "sk-", "api_key"):
        assert secret not in text


def test_diagnostic_of_an_older_production_says_what_was_not_recorded(engine):  # noqa: F811
    project = _project()
    repo = ProductionRepository(settings.db_path())
    old = repo.create(project_id=project.id, subject=SUBJECT, provider="moneyprinterturbo", status=S.TERMINEE,
                      script="Un script déjà écrit avant la traçabilité.", external_task_id="task-old-0001",
                      storyboard=[{"index": 1, "narration": "Un script.", "prompt": "Prompt de la scène 1", "seconds": 3.0}],
                      params={"request": {"subject": SUBJECT}, "engine": {}})
    app = _run({"projet": project.id, "vue": "suivi", "production": old.id})
    text = _text(app) + " " + " ".join(str(code.value) for code in app.code)
    assert not app.exception and "Production antérieure à la traçabilité" in text
    assert "aucun instantané ni prompt final n’a été enregistré" in text and "Prompt de la scène 1" in text
    assert "gabarit d’images" in text          # avertit que le moteur a pu ajouter son gabarit


def test_settings_page_saves_the_projects_own_visual_profile(engine):  # noqa: F811
    project = _project()
    app = _run({"projet": project.id, "vue": "parametres"})
    assert app.text_area(key=f"set_{project.id}_visual_rules").value.startswith("Univers crypto et Web3 gaming")
    assert "aucune régie de télévision" in app.text_area(key=f"set_{project.id}_visual_avoid").value
    app.text_area(key=f"set_{project.id}_visual_rules").set_value("Règles à moi.")
    app.text_area(key=f"set_{project.id}_visual_avoid").set_value("un truc\n\n  un autre truc  ")
    app.run()
    _button(app, "Enregistrer les paramètres").click().run()
    from lody.projects import ProjectRepository

    brief = ProjectRepository(settings.db_path()).get(project.id).settings["brief"]
    assert brief["visual_rules"] == "Règles à moi." and brief["visual_avoid"] == ["un truc", "un autre truc"]
    page = _text(_run({"projet": project.id, "vue": "production"}))
    assert "Règles à moi." in page and "un truc ; un autre truc" in page


def test_visual_profile_validation_messages(engine):  # noqa: F811
    project = _project()
    app = _run({"projet": project.id, "vue": "parametres"})
    app.text_area(key=f"set_{project.id}_visual_avoid").set_value("\n".join(f"élément {i}" for i in range(30)))
    app.run()
    _button(app, "Enregistrer les paramètres").click().run()
    assert "16 éléments maximum" in _text(app)


def test_typing_in_one_project_never_appears_in_another_project(engine):  # noqa: F811
    crypto, audio = _project("LodyCrypto"), _project("Audiovisuel")
    app = _run({"projet": crypto.id, "vue": "production"})
    app.text_area(key=f"request_{crypto.id}").set_value("Sujet secret du projet crypto, unique.").run()
    _button(app, "Préparer la génération").click().run()
    assert "Estimation et confirmation" in _text(app)
    other = _run({"projet": audio.id, "vue": "production"})
    assert other.text_area(key=f"request_{audio.id}").value == ""            # rien n'est repris du « dernier projet »
    text = _text(other)
    assert "Estimation et confirmation" not in text and "Sujet secret" not in text and "crypto" not in text.lower().replace("audiovisuel", "")
    # les brouillons enregistrés restent rattachés à leur projet
    assert [p.project_id for p in _repo().list_for_project(audio.id)] == []
    assert all(p.project_id == crypto.id for p in _repo().list_for_project(crypto.id))
