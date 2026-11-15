"""Recette produit #211 : la marionnette Voltix doit parler avec la voix Eli de sa fiche, quel que soit l'ordre.

Premier blocage reproductible du parcours réel : démo Voltix #150 installée AVANT la fiche « Voltix » (voix Eli)
→ marionnette jamais reliée → vidéo muette, et aucun écran pour réparer (réinstaller disait « déjà présents »).
Aucun appel payant ici : seul le PLAN de voix est calculé (caractères à synthétiser), jamais la synthèse.
"""

from __future__ import annotations

import pytest

from lody import settings
from lody.animation import pilot, voiceover
from lody.animation.assets import AnimationAssetRepository
from lody.characters import CharacterRepository
from lody.projects import ProjectRepository

ELI = {"voice_provider": "elevenlabs", "voice_name": "Eli", "external_voice_id": "fakeEliVoice0001"}
PUPPET = "Voltix — marionnette"


@pytest.fixture
def world(tmp_path):
    db = tmp_path / "lody.sqlite3"
    project = ProjectRepository(db).create(name="Voltix")
    return project, AnimationAssetRepository(db), CharacterRepository(db), tmp_path / "data"


def _puppet(assets, project_id):
    return next(a for a in assets.list_for_project(project_id) if a.name == PUPPET)


def _plan(assets, characters, project_id, storyboard):
    return voiceover.plan_voiceover(storyboard, voiceover.speaker_resolver(assets, characters, project_id))


def test_demo_installed_before_the_character_is_linked_when_reinstalled(world):
    project, assets, characters, data = world
    storyboard, notes = pilot.install_voltix_demo(assets, project.id, data)
    assert _puppet(assets, project.id).character_id is None
    assert any("n'est reliée à aucune fiche" in note and "muette" in note for note in notes)
    assert not _plan(assets, characters, project.id, storyboard).scenes      # l'ancien piège : tout est muet

    eli = characters.create(project.id, name=" voltix ", **ELI)             # casse et espaces ignorés
    _, again = pilot.install_voltix_demo(assets, project.id, data)
    assert "déjà présents" in again[0]
    assert _puppet(assets, project.id).character_id == eli.id
    assert any("reliée à la fiche" in note and "« Eli »" in note for note in again)
    plan = _plan(assets, characters, project.id, storyboard)
    assert len(plan.scenes) == 3 and not plan.missing and not plan.unlinked
    assert {scene.spec.voice_id for scene in plan.scenes} == {ELI["external_voice_id"]}  # jamais une autre voix


def test_link_never_overrides_a_user_choice_and_says_when_the_voice_is_missing(world):
    project, assets, characters, data = world
    pilot.install_voltix_demo(assets, project.id, data)
    other = characters.create(project.id, name="Mascotte", **ELI)
    assets.update(project.id, _puppet(assets, project.id).id, character_id=other.id)
    characters.create(project.id, name="Voltix")                             # fiche sans voix ElevenLabs
    catalog = pilot.json.loads(pilot.VOLTIX_CATALOG.read_text(encoding="utf-8"))
    assert pilot.link_catalog_characters(assets, project.id, catalog) == []
    assert _puppet(assets, project.id).character_id == other.id

    assets.update(project.id, _puppet(assets, project.id).id, character_id=None)
    notes = pilot.link_catalog_characters(assets, project.id, catalog)
    assert len(notes) == 1 and "aucune voix ElevenLabs choisie" in notes[0]


# -- à l'écran : Atelier 2D → « Voix des marionnettes » ----------------------------------------------------------
streamlit_testing = pytest.importorskip("streamlit.testing.v1")


def test_atelier_links_a_puppet_to_the_eli_character_without_any_call(lody_env):
    import streamlit

    from lody.animation import modes
    from test.lody.test_generation_ui import _run

    streamlit.cache_resource.clear()
    projects = ProjectRepository(settings.db_path())
    project = projects.create(name="Voltix")
    project = projects.update(project.id, settings={**project.settings, modes.SETTINGS_KEY: modes.ANIMATION})
    assets = AnimationAssetRepository(settings.db_path())
    pilot.install_voltix_demo(assets, project.id, settings.data_dir())
    eli = CharacterRepository(settings.db_path()).create(project.id, name="Voltix", **ELI)
    puppet = _puppet(assets, project.id)

    app = _run({"projet": project.id, "vue": "production"})
    assert not app.exception
    box = app.selectbox(key=f"pilot_{project.id}_puppet_{puppet.id}")
    assert box.value == ""                                                   # muette tant que rien n'est relié
    app = box.set_value(eli.id).run()
    assert not app.exception
    assert _puppet(assets, project.id).character_id == eli.id
    assert any("reliée à « Voltix »" in str(item.value) and "Eli" in str(item.value) for item in app.success)

    fresh = _run({"projet": project.id, "vue": "production"})               # rechargement : lien persistant
    assert fresh.selectbox(key=f"pilot_{project.id}_puppet_{puppet.id}").value == eli.id
    streamlit.cache_resource.clear()
