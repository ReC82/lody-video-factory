"""Écran « Choisir la voix du personnage » (#153, AppTest) : comparaison de voix réelles du catalogue, budget
affiché avant tout appel, accord obligatoire, cache réutilisé, attribution explicite et durable. Faux moteur
uniquement : aucun appel ElevenLabs."""

from __future__ import annotations

import json

import pytest

from lody import settings
from lody import voice_casting as vc
from lody.characters import CharacterRepository
from lody.generation import elevenlabs_voices as ev
from test.lody.test_generation_ui import _button, _fresh_resources, _labels, _project, _run, _text, engine  # noqa: F401

pytest.importorskip("streamlit.testing.v1")

LEA = ev.VoiceInfo("lea-fr-001", "Léa", category="generated", labels={"language": "fr"},
                   preview_url="https://cdn.elevenlabs.io/lea.mp3")
HUGO = ev.VoiceInfo("hugo-fr-002", "Hugo", category="professional", labels={"accent": "french"})
ADAM = ev.VoiceInfo("adam-en-003", "Adam", category="professional", labels={"language": "en"})


@pytest.fixture(autouse=True)
def _catalog(tmp_path, monkeypatch):
    report = tmp_path / "elevenlabs-voices.json"
    monkeypatch.setenv("LODY_VOICE_CATALOG_REPORT", str(report))
    report.write_text(json.dumps({"version": 1, "generated_at": "2026-11-01T00:00:00+00:00",
                                  "voices": [v.to_dict() for v in (LEA, HUGO, ADAM)]}), encoding="utf-8")
    return report


def _all_text(app):
    return _text(app) + " " + " ".join(str(item.value) for item in app.caption)


def _open_voltix(engine):  # noqa: F811
    project = _project()
    character = CharacterRepository(settings.db_path()).create(project.id, name="Voltix")
    app = _run({"projet": project.id, "vue": "personnages"})
    app = _button(app, "Modifier").click().run()
    return project, character, app


def _p(project, character):
    return f"char_{project.id}_{character.id}"


def test_casting_suggests_real_french_voices_and_shows_the_budget_before_any_call(engine):  # noqa: F811
    engine.supports_voice_preview = True
    project, character, app = _open_voltix(engine)
    assert not app.exception
    text = _all_text(app)
    assert "Choisir la voix du personnage" in text and "voix provisoire" in text
    selected = app.multiselect(key=f"{_p(project, character)}_cast_voices").value
    assert selected == ["lea-fr-001", "hugo-fr-002"]  # jamais la voix anglaise par défaut, jamais une voix inventée
    assert "2 extrait(s) à générer" in text and "non configuré" in text
    assert engine.calls.count("generate_voice_preview") == 0
    generate = _button(app, "Générer 2 extrait(s) manquant(s)")
    assert generate.disabled  # accord non donné


def test_generation_requires_the_approval_checkbox_then_reuses_the_cache(engine):  # noqa: F811
    engine.supports_voice_preview = True
    project, character, app = _open_voltix(engine)
    p = _p(project, character)
    token = vc.approval_token(vc.comparison_plan([LEA, HUGO], [vc.DEFAULT_PROFILE]))
    app = app.checkbox(key=f"{p}_cast_ok_{token}").check().run()
    app = _button(app, "Générer 2 extrait(s) manquant(s)").click().run()

    assert not app.exception
    assert engine.calls.count("generate_voice_preview") == 2
    sent = {voice_id for _text_, voice_id, _settings in engine.voice_previews_received}
    assert sent == {"lea-fr-001", "hugo-fr-002"}
    assert all(s["model_id"] == "eleven_multilingual_v2" for _t, _v, s in engine.voice_previews_received)
    assert "aucun appel ElevenLabs, coût 0" in _all_text(app)
    assert "Utiliser Léa (Vif) par défaut" in _labels(app)

    # Rouvrir la page : extraits relus depuis le cache, aucun nouvel appel payant.
    app = _run({"projet": project.id, "vue": "personnages"})
    app = _button(app, "Modifier").click().run()
    assert engine.calls.count("generate_voice_preview") == 2
    assert "Utiliser Hugo (Vif) par défaut" in _labels(app)


def test_choosing_a_voice_persists_it_on_the_character_for_every_episode(engine):  # noqa: F811
    engine.supports_voice_preview = True
    project, character, app = _open_voltix(engine)
    p = _p(project, character)
    token = vc.approval_token(vc.comparison_plan([LEA, HUGO], [vc.DEFAULT_PROFILE]))
    app = app.checkbox(key=f"{p}_cast_ok_{token}").check().run()
    app = _button(app, "Générer 2 extrait(s) manquant(s)").click().run()
    app = _button(app, "Utiliser Hugo (Vif) par défaut").click().run()

    assert not app.exception
    saved = CharacterRepository(settings.db_path()).get(project.id, character.id)
    assert (saved.voice_provider, saved.voice_name, saved.external_voice_id) == ("elevenlabs", "Hugo", "hugo-fr-002")
    assert saved.voice_direction == vc.profile("vif").settings("eleven_multilingual_v2")
    assert app.session_state[f"{p}_external_voice_id"] == "hugo-fr-002"  # formulaire aligné, pas d'écrasement


def test_nothing_is_generated_when_the_engine_cannot_preview(engine):  # noqa: F811
    project, character, app = _open_voltix(engine)  # supports_voice_preview = False par défaut
    assert not app.exception
    assert "ne sait pas encore générer" in _all_text(app)
    assert not any(label.startswith("Générer ") and "manquant" in label for label in _labels(app))


def test_missing_catalogue_shows_a_clear_message_without_crashing(engine, _catalog):  # noqa: F811
    _catalog.unlink()
    project, character, app = _open_voltix(engine)
    assert not app.exception
    assert "Aucun catalogue de voix ElevenLabs" in _all_text(app)
