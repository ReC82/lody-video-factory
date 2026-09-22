"""« Paramètres système » (AppTest) : verrou de mise en ligne, authentification admin réellement vérifiée à
chaque affichage, jamais de valeur réaffichée, aucun lien nulle part, pastilles d'état, confirmation de saisie,
purge du formulaire, aucun bouton d'affichage/export/copie."""

from __future__ import annotations

from pathlib import Path

import pytest

apptest = pytest.importorskip("streamlit.testing.v1")

from lody.generation import admin_auth  # noqa: E402 — après importorskip, comme le reste de ce fichier

WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
SECRET = "sk-marker-should-never-render-XYZ99"
SESSION = admin_auth.AdminSession(username="admin")


def _run(monkeypatch, lody_env, *, enabled: bool = True, authenticated: bool = True, query: dict | None = None):
    monkeypatch.setenv("LODY_ENABLE_SYSTEM_SETTINGS", "1" if enabled else "0")
    monkeypatch.setattr(admin_auth, "current_session", lambda **k: SESSION if authenticated else None)
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=30)
    for key, value in (query or {"vue": "systeme"}).items():
        app.query_params[key] = value
    return app.run()


def _text(app):
    return " ".join(str(item.value) for item in app.markdown)


def _field(app, marker):
    return next(w for w in app.text_input if marker in str(w.key) and "_confirm" not in str(w.key))


def _confirm_field(app, marker):
    return next(w for w in app.text_input if marker in str(w.key) and str(w.key).endswith("_confirm"))


def _save_button(app, marker):
    return next(b for b in app.button if str(b.key) == f"sys_{marker}_save")


# -- verrou de mise en ligne (flag) -------------------------------------------------------------------------------
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


# -- authentification : la vraie protection, pas seulement le flag -------------------------------------------------
def test_without_a_session_the_page_shows_only_a_login_prompt(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, authenticated=False)
    assert not app.exception
    text = _text(app)
    assert "Authentification requise" in text
    assert "Se connecter" in text
    assert '<h2 class="brief-title">Paramètres système</h2>' not in text  # le titre de la vraie page n'apparaît jamais


def test_without_a_session_no_key_data_is_computed_at_all(lody_env, monkeypatch, tmp_path):
    """Même avec une vraie clé configurée dans config.toml, rien n'en transparaît avant authentification."""
    config = tmp_path / "config.toml"
    config.write_text('[app]\nopenai_api_key = "sk-should-never-appear-unauthenticated"\nopenai_image_api_keys = []\n'
                      'llm_provider = "openai"\n[elevenlabs]\napi_key = ""\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    app = _run(monkeypatch, lody_env, authenticated=False)
    text = _text(app)
    assert "Configurée" not in text and "Absente" not in text  # aucune pastille du tout


def test_without_a_session_no_form_field_or_save_button_exists(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, authenticated=False)
    assert list(app.text_input) == [] and not any("save" in str(b.key) for b in app.button)


def test_login_link_points_at_the_auth_service_with_a_return_path(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, authenticated=False)
    text = _text(app)
    assert "/lody-auth/login?next=" in text


def test_authenticated_session_shows_the_real_page_and_the_username(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, authenticated=True)
    assert not app.exception
    text = _text(app)
    assert "Paramètres système" in text and "admin" in text


def test_authenticated_session_offers_a_logout_form(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env, authenticated=True)
    text = _text(app)
    assert 'action="/lody-auth/logout"' in text and "Se déconnecter" in text


def test_session_is_reverified_on_every_render_not_cached(lody_env, monkeypatch):
    calls = []
    monkeypatch.setenv("LODY_ENABLE_SYSTEM_SETTINGS", "1")

    def spy(**k):
        calls.append(1)
        return SESSION

    monkeypatch.setattr(admin_auth, "current_session", spy)
    app = apptest.AppTest.from_file(str(WEBUI_DIR / "Lody.py"), default_timeout=30)
    app.query_params["vue"] = "systeme"
    app.run()
    assert len(calls) >= 1
    before = len(calls)
    app.run()
    assert len(calls) > before  # revérifié à CHAQUE affichage, jamais un seul coup mis en cache


# -- contenu de la page authentifiée ---------------------------------------------------------------------------------
def test_enabled_shows_the_four_fields_with_provider_labels_and_capabilities(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env)
    assert not app.exception
    text = _text(app)
    for label, provider, capability in [("Clé de script", "OpenAI", "Texte"), ("Clé d’images", "OpenAI", "Image"),
                                        ("Fournisseur de script actif", "Moteur", None),
                                        ("Clé voix et musique", "ElevenLabs", "Voix, musique")]:
        assert label in text and provider in text
        if capability:
            assert capability in text


def test_no_reveal_export_or_copy_control_exists_on_the_page(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env)
    labels = [str(getattr(b, "label", "") or "").lower() for b in app.button]
    for banned in ("afficher", "révéler", "reveal", "copier", "exporter", "télécharger"):
        assert not any(banned in label for label in labels)
    assert not list(app.get("download_button"))


def test_password_fields_are_never_pre_filled(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env)
    for widget in app.text_input:
        assert widget.value in (None, "")


def test_each_secret_field_has_a_confirmation_field(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env)
    value_keys = {str(w.key) for w in app.text_input if not str(w.key).endswith("_confirm")}
    confirm_keys = {str(w.key) for w in app.text_input if str(w.key).endswith("_confirm")}
    assert confirm_keys == {f"{k}_confirm" for k in value_keys}
    assert len(confirm_keys) == 3  # les 3 champs secrets (pas app.llm_provider, qui est une liste déroulante)


def test_absent_key_shows_the_absente_pill(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env)
    assert "Absente" in _text(app)


def test_configured_key_shows_the_configuree_pill_with_only_last_four_chars(lody_env, monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[app]\nopenai_api_key = "sk-abcdefghij1234"\nopenai_image_api_keys = []\nllm_provider = "openai"\n'
                      '[elevenlabs]\napi_key = ""\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    app = _run(monkeypatch, lody_env)
    text = _text(app)
    assert "Configurée" in text
    assert "1234" in text  # les 4 derniers caractères, jamais la clé entière
    assert "sk-abcdefghij1234" not in text


def test_active_llm_provider_is_shown(lody_env, monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[app]\nopenai_api_key = ""\nopenai_image_api_keys = []\nllm_provider = "moonshot"\n'
                      '[elevenlabs]\napi_key = ""\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    app = _run(monkeypatch, lody_env)
    assert "moonshot" in _text(app)


def test_llm_provider_choices_are_a_strict_allowlist_not_free_text(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env)
    choice = next(w for w in app.selectbox if "app.llm_provider" in str(w.key))
    assert list(choice.options) == ["openai"]


def test_other_engine_providers_are_listed_read_only(lody_env, monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[app]\nopenai_api_key = ""\nopenai_image_api_keys = []\nllm_provider = "openai"\n'
                      'moonshot_api_key = "fake-moonshot-value"\n[elevenlabs]\napi_key = ""\n', encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    app = _run(monkeypatch, lody_env)
    text = _text(app)
    assert "moonshot" in text and "fake-moonshot-value" not in text
    assert not any("moonshot" in str(w.key) for w in app.text_input)  # lecture seule : aucun champ de saisie


# -- enregistrement : confirmation, purge du formulaire --------------------------------------------------------------
def test_mismatched_confirmation_is_refused_and_nothing_is_queued(lody_env, monkeypatch, tmp_path):
    secrets_dir = tmp_path / "secrets"
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env)
    _field(app, "app.openai_api_key").set_value(SECRET).run()
    _confirm_field(app, "app.openai_api_key").set_value("not-the-same-value").run()
    app = _save_button(app, "app.openai_api_key").click().run()
    assert "ne correspondent pas" in _text(app)
    assert not (secrets_dir / "secrets.toml").exists()


def test_saving_a_valid_key_never_renders_it_back_anywhere(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env)
    _field(app, "app.openai_api_key").set_value(SECRET).run()
    _confirm_field(app, "app.openai_api_key").set_value(SECRET).run()
    app = _save_button(app, "app.openai_api_key").click().run()
    rendered = _text(app) + "".join(str(w.value or "") for w in app.text_input)
    assert SECRET not in rendered


def test_form_fields_are_cleared_immediately_after_a_successful_submission(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env)
    _field(app, "app.openai_api_key").set_value(SECRET).run()
    _confirm_field(app, "app.openai_api_key").set_value(SECRET).run()
    app = _save_button(app, "app.openai_api_key").click().run()
    assert _field(app, "app.openai_api_key").value in (None, "")
    assert _confirm_field(app, "app.openai_api_key").value in (None, "")


def test_form_fields_are_cleared_even_after_a_mismatched_confirmation(lody_env, monkeypatch):
    app = _run(monkeypatch, lody_env)
    _field(app, "app.openai_api_key").set_value(SECRET).run()
    _confirm_field(app, "app.openai_api_key").set_value("different").run()
    app = _save_button(app, "app.openai_api_key").click().run()
    assert _field(app, "app.openai_api_key").value in (None, "")
    assert _confirm_field(app, "app.openai_api_key").value in (None, "")


def test_invalid_shape_shows_an_error_and_does_not_queue_anything(lody_env, monkeypatch, tmp_path):
    secrets_dir = tmp_path / "secrets"
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env)
    _field(app, "app.openai_api_key").set_value("has a space").run()
    _confirm_field(app, "app.openai_api_key").set_value("has a space").run()
    app = _save_button(app, "app.openai_api_key").click().run()
    assert "banner-error" in "".join(str(m.value) for m in app.markdown)
    assert not (secrets_dir / "secrets.toml").exists()


# -- états transitoires et terminaux -------------------------------------------------------------------------------
def test_in_progress_state_shows_a_transient_label_not_a_static_pill(lody_env, monkeypatch, tmp_path):
    import json

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secrets-status.json").write_text(
        json.dumps({"state": "restarting", "processing": ["app.openai_api_key"], "fields": {}}), encoding="utf-8")
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env)
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
    app = _run(monkeypatch, lody_env)
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
    app = _run(monkeypatch, lody_env)
    assert "restaurée" in _text(app)


def test_expired_state_is_shown_distinctly(lody_env, monkeypatch, tmp_path):
    import json

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secrets-status.json").write_text(json.dumps({
        "state": "expired", "fields": {"app.openai_api_key": {
            "state": "expired", "message": "Cette clé a expiré chez le fournisseur."}},
    }), encoding="utf-8")
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env)
    assert "Expirée" in _text(app)


def test_rejected_state_shows_the_provider_refused_the_key_distinctly_from_a_generic_rollback(lody_env, monkeypatch, tmp_path):
    import json

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secrets-status.json").write_text(json.dumps({
        "state": "rejected", "fields": {"app.openai_api_key": {
            "state": "rejected", "message": "Le fournisseur a refusé cette clé (authentification refusée) : l’ancienne configuration a été restaurée automatiquement."}},
    }), encoding="utf-8")
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env)
    text = _text(app)
    assert "Invalide" in text and "restaurée" in text


def test_unverified_state_is_distinct_from_configured_and_from_invalid(lody_env, monkeypatch, tmp_path):
    import json

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secrets-status.json").write_text(json.dumps({
        "state": "ready_unverified", "fields": {"app.openai_api_key": {
            "state": "unverified", "message": "Le moteur a redémarré normalement, mais la vérification auprès du fournisseur a dépassé le délai : la clé est appliquée mais non confirmée."}},
    }), encoding="utf-8")
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env)
    text = _text(app)
    assert "non vérifiée" in text.lower()


def test_verifying_in_progress_state_shows_a_transient_label(lody_env, monkeypatch, tmp_path):
    import json

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secrets-status.json").write_text(
        json.dumps({"state": "verifying", "processing": ["elevenlabs.api_key"], "fields": {}}), encoding="utf-8")
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env)
    assert "Vérification auprès du fournisseur" in _text(app)


def test_modified_and_checked_dates_are_shown(lody_env, monkeypatch, tmp_path):
    import json

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "secrets-status.json").write_text(json.dumps({
        "state": "ready", "fields": {"app.openai_api_key": {
            "state": "ok", "message": "", "modified_at": "2026-01-15T10:00:00+00:00",
            "checked_at": "2026-01-15T10:00:00+00:00"}},
    }), encoding="utf-8")
    monkeypatch.setenv("LODY_SECRETS_DIR", str(secrets_dir))
    app = _run(monkeypatch, lody_env)
    text = _text(app)
    assert "modifiée le" in text and "dernier contrôle le" in text
