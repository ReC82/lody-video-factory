"""« Paramètres système » (AppTest) : verrou de mise en ligne, jamais de valeur réaffichée, aucun lien nulle part,
pastilles Configurée/Absente/Invalide, aucun bouton d'affichage/export/copie."""

from __future__ import annotations

from pathlib import Path

import pytest

apptest = pytest.importorskip("streamlit.testing.v1")

WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
SECRET = "sk-marker-should-never-render-XYZ99"


def _run(monkeypatch, lody_env, *, enabled: bool, query: dict | None = None):
    monkeypatch.setenv("LODY_ENABLE_SYSTEM_SETTINGS", "1" if enabled else "0")
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=30)
    for key, value in (query or {"vue": "systeme"}).items():
        app.query_params[key] = value
    return app.run()


def _text(app):
    return " ".join(str(item.value) for item in app.markdown)


def test_disabled_by_default_route_falls_back_to_home(lody_env, monkeypatch):
    monkeypatch.delenv("LODY_ENABLE_SYSTEM_SETTINGS", raising=False)
    app = _run(monkeypatch, lody_env, enabled=False)
    assert not app.exception
    assert "<h2 class=\"brief-title\">Paramètres système</h2>" not in _text(app)
    assert "Tes projets vidéo" in _text(app) or "projet" in _text(app).lower()


def test_no_page_anywhere_links_to_the_system_settings_route(lody_env, monkeypatch):
    monkeypatch.setenv("LODY_ENABLE_SYSTEM_SETTINGS", "1")
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=30).run()
    for button in app.button:
        assert "système" not in (button.label or "").lower() and "secret" not in (button.label or "").lower()
    for md in app.markdown:
        assert "vue=systeme" not in str(md.value)


def test_enabled_shows_the_four_fields_with_provider_labels(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, enabled=True)
    assert not app.exception
    text = _text(app)
    assert "Paramètres système" in text
    for label, provider in [("Clé de script", "OpenAI"), ("Clé d’images", "OpenAI"),
                            ("Fournisseur de script actif", "Moteur"), ("Clé voix et musique", "ElevenLabs")]:
        assert label in text and provider in text


def test_no_reveal_export_or_copy_control_exists_on_the_page(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, enabled=True)
    labels = [str(getattr(b, "label", "") or "").lower() for b in app.button]
    for banned in ("afficher", "révéler", "reveal", "copier", "exporter", "télécharger"):
        assert not any(banned in label for label in labels)
    assert not list(app.get("download_button"))


def test_password_fields_are_never_pre_filled(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, enabled=True)
    for widget in app.text_input:
        assert widget.value in (None, "")


def test_saving_a_valid_key_never_renders_it_back_anywhere(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, enabled=True)
    field = next(w for w in app.text_input if "app.openai_api_key" in str(w.key) and "_choice" not in str(w.key))
    field.set_value(SECRET).run()
    save = next(b for b in app.button if str(b.key).startswith("sys_app.openai_api_key_save"))
    app = save.click().run()
    rendered = _text(app) + "".join(str(w.value or "") for w in app.text_input)
    assert SECRET not in rendered
    assert field.key not in app.session_state or app.session_state[field.key] == ""


def test_absent_key_shows_the_absente_pill(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, enabled=True)
    assert "Absente" in _text(app)


def test_configured_key_shows_the_configuree_pill_with_only_last_four_chars(lody_env, monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[app]\nopenai_api_key = "sk-abcdefghij1234"\nopenai_image_api_keys = []\nllm_provider = "openai"\n'
                      '[elevenlabs]\napi_key = ""\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    app = _run(monkeypatch, lody_env, enabled=True)
    text = _text(app)
    assert "Configurée" in text
    assert "1234" in text  # les 4 derniers caractères, jamais la clé entière
    assert "sk-abcdefghij1234" not in text


def test_active_llm_provider_is_shown(lody_env, monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[app]\nopenai_api_key = ""\nopenai_image_api_keys = []\nllm_provider = "moonshot"\n'
                      '[elevenlabs]\napi_key = ""\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    app = _run(monkeypatch, lody_env, enabled=True)
    assert "moonshot" in _text(app)


def test_llm_provider_choices_are_a_strict_allowlist_not_free_text(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, enabled=True)
    choice = next(w for w in app.selectbox if "app.llm_provider" in str(w.key))
    assert list(choice.options) == ["openai"]


def test_invalid_shape_shows_an_error_and_does_not_queue_anything(lody_env, monkeypatch, tmp_path):
    secrets_dir = tmp_path / "secrets"
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env, enabled=True)
    field = next(w for w in app.text_input if "app.openai_api_key" in str(w.key) and "_choice" not in str(w.key))
    field.set_value("has a space").run()
    save = next(b for b in app.button if str(b.key).startswith("sys_app.openai_api_key_save"))
    app = save.click().run()
    assert "banner-error" in "".join(str(m.value) for m in app.markdown)
    assert not (secrets_dir / "secrets.toml").exists()


def test_in_progress_state_shows_a_transient_label_not_a_static_pill(lody_env, monkeypatch, tmp_path):
    import json

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secrets-status.json").write_text(
        json.dumps({"state": "restarting", "processing": ["app.openai_api_key"], "fields": {}}), encoding="utf-8")
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env, enabled=True)
    assert "Redémarrage du moteur" in _text(app)


def test_critical_state_is_shown_without_revealing_any_secret(lody_env, monkeypatch, tmp_path):
    import json

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secrets-status.json").write_text(json.dumps({
        "state": "critical", "message": "Le moteur ne répond plus après une tentative de restauration automatique.",
        "fields": {"app.openai_api_key": {"state": "critical", "message": "Le moteur ne répond plus."}},
    }), encoding="utf-8")
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env, enabled=True)
    text = _text(app)
    assert "critique" in text.lower() or "Erreur critique" in text
    assert SECRET not in text


def test_rolled_back_state_explains_the_old_configuration_was_restored(lody_env, monkeypatch, tmp_path):
    import json

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secrets-status.json").write_text(json.dumps({
        "state": "rolled_back", "fields": {"app.openai_api_key": {
            "state": "rolled_back", "message": "La nouvelle valeur a été refusée par le moteur : l’ancienne configuration a été restaurée automatiquement."}},
    }), encoding="utf-8")
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env, enabled=True)
    assert "restaurée" in _text(app)
