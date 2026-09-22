"""Pipeline hôte : valider, sauvegarder, patcher, redémarrer, régénérer le rapport — et retour arrière automatique
complet si le moteur ne redevient pas sain. Aucun vrai Docker, aucun vrai systemd : redémarrage et santé sont de
simples fonctions injectées."""

from __future__ import annotations

import json

import pytest

from lody.generation import apply_secrets, secrets_store
from lody.generation.engine_facts import build_report

CONFIG = (
    "[app]\n"
    "openai_api_key = \"sk-old-1234\"\n"
    "openai_image_api_keys = []\n"
    "llm_provider = \"moonshot\"\n"
    "\n"
    "[elevenlabs]\n"
    "api_key = \"\"\n"
)


@pytest.fixture
def env(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(CONFIG, encoding="utf-8")
    return {
        "config": config, "secrets": tmp_path / "secrets.toml", "status": tmp_path / "secrets-status.json",
        "backup": tmp_path / "config.toml.rollback", "report": tmp_path / "engine-capabilities.json",
    }


def _set(env, field, value):
    secrets_store.set_pending(field, value, secrets_path=env["secrets"], reload_path=env["secrets"].with_suffix(".reload"))


def _run(env, *, restart=None, healthy=True, restart_calls=None):
    calls = restart_calls if restart_calls is not None else []

    def default_restart():
        calls.append("restart")

    def default_health():
        return healthy

    return apply_secrets.apply(
        config_path=env["config"], secrets_path=env["secrets"], status_path=env["status"], backup_path=env["backup"],
        report_path=env["report"], restart_engine=restart or default_restart, wait_healthy=default_health), calls


def test_nothing_pending_is_a_safe_no_op(env):
    result, calls = _run(env)
    assert result == {"state": "noop"}
    assert calls == [] and not env["status"].exists() and not env["backup"].exists()
    assert env["config"].read_text() == CONFIG  # rien touché


def test_valid_field_is_patched_restarted_and_reported_ready(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    result, calls = _run(env)
    assert result["state"] == "ready" and result["fields"]["app.openai_api_key"]["state"] == "ok"
    assert calls == ["restart"]
    assert 'openai_api_key = "sk-new-9999"' in env["config"].read_text()
    assert 'llm_provider = "moonshot"' in env["config"].read_text()  # rien d'autre touché
    assert env["report"].exists()
    assert json.loads(env["report"].read_text())["facts"]["llm_key"]["openai"] is True


def test_secrets_toml_is_cleared_after_a_successful_apply(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    _run(env)
    assert secrets_store.read_pending(env["secrets"]) == {}


def test_status_progresses_through_applying_then_restarting_then_ready(env):
    _set(env, "elevenlabs.api_key", "voice-secret")
    seen = []
    real_write = apply_secrets._write_status

    def spy(status_path, data):
        seen.append(data["state"])
        real_write(status_path, data)

    import unittest.mock as mock

    with mock.patch.object(apply_secrets, "_write_status", side_effect=spy):
        _run(env)
    assert seen == ["applying", "restarting", "ready"]


def test_the_secret_value_never_appears_in_the_status_file(env):
    _set(env, "app.openai_api_key", "sk-super-secret-marker-XYZ")
    _run(env)
    assert "sk-super-secret-marker-XYZ" not in env["status"].read_text()


def test_the_secret_value_never_appears_in_the_returned_result(env):
    _set(env, "app.openai_api_key", "sk-super-secret-marker-XYZ")
    result, _ = _run(env)
    assert "sk-super-secret-marker-XYZ" not in json.dumps(result)


def test_invalid_field_is_rejected_before_touching_config_or_restarting(env):
    _set(env, "app.openai_api_key", "sk-fine")  # écrit directement dans secrets.toml, contourne la validation d'écriture
    # force une valeur invalide malgré tout (simulateur d'un fichier corrompu/mal formé)
    env["secrets"].write_text('[app]\nopenai_api_key = "has space"\n', encoding="utf-8")
    result, calls = _run(env)
    assert result["state"] == "error" and result["fields"]["app.openai_api_key"]["state"] == "invalid"
    assert calls == []
    assert env["config"].read_text() == CONFIG
    assert "has space" not in json.dumps(result)


def test_llm_provider_choice_is_patched_like_any_other_field(env):
    _set(env, "app.llm_provider", "openai")
    result, _ = _run(env)
    assert result["state"] == "ready"
    assert 'llm_provider = "openai"' in env["config"].read_text()


def test_mixed_batch_applies_the_valid_field_and_reports_the_invalid_one(env):
    env["secrets"].write_text('[app]\nopenai_api_key = "sk-good-1"\nllm_provider = "bogus"\n', encoding="utf-8")
    result, calls = _run(env)
    assert result["fields"]["app.openai_api_key"]["state"] == "ok"
    assert result["fields"]["app.llm_provider"]["state"] == "invalid"
    assert calls == ["restart"]
    assert 'openai_api_key = "sk-good-1"' in env["config"].read_text()
    assert 'llm_provider = "moonshot"' in env["config"].read_text()  # champ invalide jamais écrit


# -- retour arrière automatique ------------------------------------------------------------------------------------
def test_engine_unhealthy_after_write_triggers_automatic_rollback(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    calls = []
    healths = iter([False, True])  # la nouvelle config ne redémarre pas sainement ; l'ancienne, oui
    result = apply_secrets.apply(
        config_path=env["config"], secrets_path=env["secrets"], status_path=env["status"], backup_path=env["backup"],
        report_path=env["report"], restart_engine=lambda: calls.append("restart"), wait_healthy=lambda: next(healths))
    assert result["state"] == "rolled_back"
    assert calls == ["restart", "restart"]  # une fois avec la nouvelle config, une fois après restauration
    assert env["config"].read_text() == CONFIG  # entièrement restauré, byte pour byte
    assert result["fields"]["app.openai_api_key"]["state"] == "rolled_back"


def test_rollback_health_check_uses_a_second_independent_attempt(env, monkeypatch):
    _set(env, "app.openai_api_key", "sk-new-9999")
    healths = iter([False, True])  # 1er passage : pas sain ; après restauration : sain
    calls = []

    def health():
        return next(healths)

    result = apply_secrets.apply(
        config_path=env["config"], secrets_path=env["secrets"], status_path=env["status"], backup_path=env["backup"],
        report_path=env["report"], restart_engine=lambda: calls.append("restart"), wait_healthy=health)
    assert result["state"] == "rolled_back"
    assert calls == ["restart", "restart"]


def test_engine_still_unhealthy_after_rollback_attempt_is_a_critical_state_not_a_loop(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    calls = []
    result, _ = _run(env, restart=lambda: calls.append("restart"), healthy=False, restart_calls=calls)
    assert result["state"] == "critical"
    assert calls == ["restart", "restart"]  # jamais une troisième tentative
    assert "intervention manuelle" in result["message"].lower() or "Intervention manuelle" in result["message"]


def test_critical_state_never_contains_the_secret(env):
    _set(env, "app.openai_api_key", "sk-critical-marker-ABC")
    result, _ = _run(env, healthy=False)
    assert "sk-critical-marker-ABC" not in json.dumps(result)
    assert "sk-critical-marker-ABC" not in env["status"].read_text()


def test_restore_verifies_the_backup_hash_before_trusting_it(env):
    _set(env, "app.openai_api_key", "sk-new-9999")

    def tampering_restart():
        # Un tiers modifie la sauvegarde entre la copie et la restauration : elle ne doit plus être approuvée.
        env["backup"].write_text("corrupted", encoding="utf-8")

    result, _ = _run(env, restart=tampering_restart, healthy=False)
    assert result["state"] == "critical"
    assert "sauvegarde" in result["message"].lower()


def test_restart_raising_an_exception_is_treated_like_an_unhealthy_engine(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    calls = []

    def flaky_restart():
        calls.append("attempt")
        if len(calls) == 1:
            raise RuntimeError("docker daemon unreachable")
        # deuxième tentative (après restauration) : réussit

    result, _ = _run(env, restart=flaky_restart, healthy=True)
    # Le 1er appel lève -> traité comme un échec de santé -> restauration -> 2e appel réussit et le moteur est sain.
    assert result["state"] == "rolled_back"
    assert len(calls) == 2


def test_a_missing_backup_before_any_write_is_a_critical_state_and_touches_nothing(env, monkeypatch):
    _set(env, "app.openai_api_key", "sk-new-9999")
    monkeypatch.setattr(apply_secrets, "_backup", lambda *a, **k: None)
    result, calls = _run(env)
    assert result["state"] == "critical" and calls == []
    assert env["config"].read_text() == CONFIG


def test_patch_error_on_an_unexpected_config_shape_is_reported_without_writing_or_restarting(env):
    env["config"].write_text("[app]\n# openai_api_key is not set here at all\n", encoding="utf-8")
    _set(env, "app.openai_api_key", "sk-new-9999")
    result, calls = _run(env)
    assert result["state"] == "error" and calls == []
    assert env["config"].read_text() == "[app]\n# openai_api_key is not set here at all\n"


def test_build_report_reflects_the_restored_configuration_after_rollback(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    _run(env, healthy=False)
    facts = build_report(env["config"])["facts"]
    assert facts["llm_key"]["openai"] is True  # l'ancienne clé (sk-old-1234) est bien revenue
