"""Pipeline hôte : valider, sauvegarder, patcher, redémarrer, VÉRIFIER auprès du fournisseur, régénérer le
rapport — et retour arrière automatique complet si l'une de ces vérifications échoue. Aucun vrai Docker, aucun
vrai systemd, aucun vrai appel réseau : redémarrage, santé et vérification fournisseur sont de simples fonctions
injectées (sauf les tests dédiés à ``_http_check``, qui utilisent un vrai serveur HTTP local)."""

from __future__ import annotations

import json
import stat

import pytest

from lody.generation import apply_secrets, secrets_store
from lody.generation.apply_secrets import ProviderCheck
from lody.generation.engine_facts import build_report

_MISSING = object()  # sentinelle : distingue « voice_report_path non précisé » de « explicitement None »

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
        "voice_report": tmp_path / "elevenlabs-voices.json",
    }


def _set(env, field, value):
    secrets_store.set_pending(field, value, secrets_path=env["secrets"], reload_path=env["secrets"].with_suffix(".reload"))


def _always_valid(field_path, value, config):
    return ProviderCheck("valid")


def _run(env, *, restart=None, healthy=True, restart_calls=None, verify_key=_always_valid, wait_healthy=None,
        voice_report_path=_MISSING):
    calls = restart_calls if restart_calls is not None else []

    def default_restart():
        calls.append("restart")

    def default_health():
        return healthy

    return apply_secrets.apply(
        config_path=env["config"], secrets_path=env["secrets"], status_path=env["status"], backup_path=env["backup"],
        report_path=env["report"], restart_engine=restart or default_restart,
        wait_healthy=wait_healthy or default_health, verify_key=verify_key,
        voice_report_path=env["voice_report"] if voice_report_path is _MISSING else voice_report_path), calls


def test_nothing_pending_is_a_safe_no_op(env):
    result, calls = _run(env)
    assert result == {"state": "noop"}
    assert calls == [] and not env["status"].exists() and not env["backup"].exists()
    assert env["config"].read_text() == CONFIG  # rien touché


def test_valid_field_is_patched_restarted_verified_and_reported_ready(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    result, calls = _run(env)
    assert result["state"] == "ready" and result["fields"]["app.openai_api_key"]["state"] == "ok"
    assert calls == ["restart"]
    assert 'openai_api_key = "sk-new-9999"' in env["config"].read_text()
    assert 'llm_provider = "moonshot"' in env["config"].read_text()  # rien d'autre touché
    assert env["report"].exists()
    assert json.loads(env["report"].read_text())["facts"]["llm_key"]["openai"] is True


def test_status_file_is_group_readable_not_world_readable(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    _run(env)
    mode = stat.S_IMODE(env["status"].stat().st_mode)
    assert mode == 0o640 and mode & 0o007 == 0


def test_secrets_toml_is_cleared_after_a_successful_apply(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    _run(env)
    assert secrets_store.read_pending(env["secrets"]) == {}


def test_status_progresses_through_applying_restarting_verifying_ready(env):
    _set(env, "elevenlabs.api_key", "voice-secret")
    seen = []
    real_write = apply_secrets._write_status

    def spy(status_path, data):
        seen.append(data["state"])
        real_write(status_path, data)

    import unittest.mock as mock

    with mock.patch.object(apply_secrets, "_write_status", side_effect=spy):
        _run(env)
    assert seen == ["applying", "restarting", "verifying", "ready"]


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


# -- retour arrière automatique : moteur pas sain -----------------------------------------------------------------
def test_engine_unhealthy_after_write_triggers_automatic_rollback(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    calls = []
    healths = iter([False, True])  # la nouvelle config ne redémarre pas sainement ; l'ancienne, oui
    result, _ = _run(env, restart=lambda: calls.append("restart"), restart_calls=calls,
                     wait_healthy=lambda: next(healths))
    assert result["state"] == "rolled_back"
    assert calls == ["restart", "restart"]  # une fois avec la nouvelle config, une fois après restauration
    assert env["config"].read_text() == CONFIG  # entièrement restauré, byte pour byte
    assert result["fields"]["app.openai_api_key"]["state"] == "rolled_back"


def test_rollback_health_check_uses_a_second_independent_attempt(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    healths = iter([False, True])  # 1er passage : pas sain ; après restauration : sain
    calls = []
    result, _ = _run(env, restart=lambda: calls.append("restart"), restart_calls=calls,
                     wait_healthy=lambda: next(healths))
    assert result["state"] == "rolled_back"
    assert calls == ["restart", "restart"]


def test_engine_still_unhealthy_after_rollback_attempt_is_a_critical_state_not_a_loop(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    calls = []
    result, _ = _run(env, restart=lambda: calls.append("restart"), healthy=False, restart_calls=calls)
    assert result["state"] == "critical"
    assert calls == ["restart", "restart"]  # jamais une troisième tentative
    assert "intervention manuelle" in result["message"].lower()


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


# -- clé expirée (distincte d'une clé simplement invalide) ---------------------------------------------------------
def test_expired_key_is_reported_distinctly_and_still_rolls_back(env):
    _set(env, "app.openai_api_key", "sk-expired-9999")
    result, calls = _run(env, verify_key=lambda f, v, c: ProviderCheck("expired", "clé expirée (401)"))
    assert result["state"] == "expired"
    assert calls == ["restart", "restart"]
    assert env["config"].read_text() == CONFIG
    field = result["fields"]["app.openai_api_key"]
    assert field["state"] == "expired" and "expiré" in field["message"].lower()


def test_expired_and_rejected_produce_different_messages(env):
    _set(env, "app.openai_api_key", "sk-a")
    expired, _ = _run(env, verify_key=lambda f, v, c: ProviderCheck("expired", "x"))
    _set(env, "app.openai_api_key", "sk-b")
    rejected, _ = _run(env, verify_key=lambda f, v, c: ProviderCheck("rejected", "x"))
    assert expired["fields"]["app.openai_api_key"]["message"] != rejected["fields"]["app.openai_api_key"]["message"]


def test_http_check_recognises_the_expired_secret_key_error_code(local_server):
    body = json.dumps({"error": {"code": "expired_secret_key",
                                 "message": "Incorrect API key provided: sk-proj-XXXXFAKE"}}).encode()
    url, _ = local_server(status=401, body=body)
    result = apply_secrets._http_check(url, {"Authorization": "Bearer x"}, 3)
    assert result.outcome == "expired"
    assert "sk-proj-XXXXFAKE" not in result.detail


def test_http_check_never_reads_the_error_message_field_only_the_code(local_server):
    """Même avec un fragment de clé dans error.message, seul error.code (jamais error.message) est lu."""
    body = json.dumps({"error": {"code": "invalid_api_key", "message": "sk-proj-should-never-appear-anywhere"}}).encode()
    url, _ = local_server(status=401, body=body)
    result = apply_secrets._http_check(url, {"Authorization": "Bearer x"}, 3)
    assert result.outcome == "rejected"
    assert "sk-proj-should-never-appear-anywhere" not in result.detail


def test_http_check_treats_a_plain_401_without_a_recognised_code_as_rejected_not_expired(local_server):
    url, _ = local_server(status=401)
    result = apply_secrets._http_check(url, {"Authorization": "Bearer x"}, 3)
    assert result.outcome == "rejected"


# -- historique persistant : modified_at / checked_at --------------------------------------------------------------
def test_a_successful_change_stamps_both_modified_at_and_checked_at(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    result, _ = _run(env)
    field = result["fields"]["app.openai_api_key"]
    assert field["modified_at"] and field["checked_at"] and field["modified_at"] == field["checked_at"]


def test_an_invalid_shape_stamps_checked_at_but_never_modified_at(env):
    env["secrets"].write_text('[app]\nopenai_api_key = "has space"\n', encoding="utf-8")
    result, _ = _run(env)
    field = result["fields"]["app.openai_api_key"]
    assert field["checked_at"] and field["modified_at"] is None


def test_a_rolled_back_change_does_not_update_modified_at(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    result, _ = _run(env, healthy=False)
    field = result["fields"]["app.openai_api_key"]
    assert field["checked_at"] and field["modified_at"] is None  # jamais devenue la valeur active


def test_an_unverified_change_still_updates_modified_at_the_value_is_live(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    result, _ = _run(env, verify_key=lambda f, v, c: ProviderCheck("timeout"))
    field = result["fields"]["app.openai_api_key"]
    assert field["modified_at"] is not None  # la clé EST appliquée, seule sa vérification est incertaine


def test_history_of_untouched_fields_is_preserved_across_runs(env):
    _set(env, "app.openai_api_key", "sk-first")
    first, _ = _run(env)
    first_modified = first["fields"]["app.openai_api_key"]["modified_at"]

    _set(env, "elevenlabs.api_key", "voice-secret")
    second, _ = _run(env)
    assert "app.openai_api_key" in second["fields"]  # toujours là, pas effacé
    assert second["fields"]["app.openai_api_key"]["modified_at"] == first_modified  # inchangé : pas touché cette fois
    assert second["fields"]["elevenlabs.api_key"]["modified_at"] is not None


def test_checked_at_advances_on_a_second_run_of_the_same_field(env):
    _set(env, "app.openai_api_key", "sk-first")
    first, _ = _run(env)
    _set(env, "app.openai_api_key", "sk-second")
    second, _ = _run(env)
    assert second["fields"]["app.openai_api_key"]["checked_at"] >= first["fields"]["app.openai_api_key"]["checked_at"]
    assert second["fields"]["app.openai_api_key"]["modified_at"] >= first["fields"]["app.openai_api_key"]["modified_at"]


# -- vérification réelle auprès du fournisseur (forme OK + moteur sain ne suffit pas) -------------------------------
def test_engine_healthy_but_key_rejected_still_triggers_automatic_rollback(env):
    """Une clé expirée ou inventée ne fait PAS planter le démarrage du moteur : la validation fournisseur est ce
    qui doit la détecter, pas le redémarrage."""
    _set(env, "app.openai_api_key", "sk-expired-9999")
    calls = []
    result, _ = _run(env, restart=lambda: calls.append("restart"), restart_calls=calls,
                     verify_key=lambda f, v, c: ProviderCheck("rejected", "authentification refusée (401)"))
    assert result["state"] == "rejected"
    assert calls == ["restart", "restart"]  # moteur redémarré avec la nouvelle clé, PUIS avec l'ancienne restaurée
    assert env["config"].read_text() == CONFIG  # entièrement restauré
    assert result["fields"]["app.openai_api_key"]["state"] == "rejected"


def test_key_rejected_with_403_also_triggers_rollback(env):
    _set(env, "elevenlabs.api_key", "voice-forbidden")
    result, _ = _run(env, verify_key=lambda f, v, c: ProviderCheck("rejected", "authentification refusée (403)"))
    assert result["state"] == "rejected"
    assert result["fields"]["elevenlabs.api_key"]["state"] == "rejected"


def test_provider_timeout_does_not_roll_back_key_stays_applied_but_unverified(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    calls = []
    result, _ = _run(env, restart=lambda: calls.append("restart"), restart_calls=calls,
                     verify_key=lambda f, v, c: ProviderCheck("timeout", "délai dépassé"))
    assert result["state"] == "ready_unverified"
    assert calls == ["restart"]  # une seule tentative : pas de retour arrière sur un simple délai dépassé
    assert 'openai_api_key = "sk-new-9999"' in env["config"].read_text()  # la nouvelle clé RESTE appliquée
    field = result["fields"]["app.openai_api_key"]
    assert field["state"] == "unverified" and "délai" in field["message"].lower()


def test_provider_unavailable_does_not_roll_back_and_is_distinct_from_a_timeout_message(env):
    _set(env, "app.openai_api_key", "sk-new-9999")
    result, calls = _run(env, verify_key=lambda f, v, c: ProviderCheck("unavailable", "fournisseur injoignable"))
    assert result["state"] == "ready_unverified"
    assert calls == ["restart"]
    field = result["fields"]["app.openai_api_key"]
    assert field["state"] == "unverified"
    _set(env, "app.openai_api_key", "sk-newer-8888")  # un second enregistrement, indépendant du premier
    timeout_result, _ = _run(env, verify_key=lambda f, v, c: ProviderCheck("timeout", "délai dépassé"))
    assert timeout_result["fields"]["app.openai_api_key"]["message"] != field["message"]  # jamais confondus


def test_network_error_during_verification_never_rolls_back_and_never_raises(env):
    def boom(field_path, value, config):
        raise ConnectionError("dns failure")

    _set(env, "app.openai_api_key", "sk-new-9999")
    result, calls = _run(env, verify_key=boom)
    assert result["state"] == "ready_unverified"
    assert calls == ["restart"]
    assert result["fields"]["app.openai_api_key"]["state"] == "unverified"


def test_provider_check_is_only_called_after_the_engine_is_healthy_not_before(env):
    order = []

    def restart():
        order.append("restart")

    def verify(field_path, value, config):
        order.append("verify")
        return ProviderCheck("valid")

    _set(env, "app.openai_api_key", "sk-new-9999")
    _run(env, restart=restart, verify_key=verify)
    assert order == ["restart", "verify"]


def test_provider_check_is_never_called_when_the_engine_itself_is_unhealthy(env):
    calls = []

    def verify(field_path, value, config):
        calls.append(field_path)
        return ProviderCheck("valid")

    _set(env, "app.openai_api_key", "sk-new-9999")
    _run(env, healthy=False, verify_key=verify)
    assert calls == []  # jamais atteint : le moteur ne redevient pas sain


def test_verify_key_is_called_with_the_value_and_the_freshly_patched_config(env):
    seen = []

    def verify(field_path, value, config):
        seen.append((field_path, value, config.get("app", {}).get("llm_provider")))
        return ProviderCheck("valid")

    _set(env, "app.openai_api_key", "sk-new-9999")
    _run(env, verify_key=verify)
    assert seen == [("app.openai_api_key", "sk-new-9999", "moonshot")]


def test_changing_llm_provider_also_verifies_the_currently_active_openai_key(env):
    """app.llm_provider n'a pas de clé propre : passer à « openai » doit vérifier la clé déjà en place."""
    seen = []

    def verify(field_path, value, config):
        seen.append((field_path, value))
        return ProviderCheck("valid")

    _set(env, "app.llm_provider", "openai")
    result, _ = _run(env, verify_key=verify)
    assert seen == [("app.openai_api_key", "sk-old-1234")]  # la clé DÉJÀ dans config.toml, pas une nouvelle
    assert result["fields"]["app.llm_provider"]["state"] == "ok"


def test_changing_llm_provider_with_no_existing_key_at_all_skips_the_companion_check(env, tmp_path):
    config = tmp_path / "config-no-key.toml"
    config.write_text('[app]\nopenai_api_key = ""\nopenai_image_api_keys = []\nllm_provider = "moonshot"\n[elevenlabs]\napi_key = ""\n',
                      encoding="utf-8")
    env = {**env, "config": config}
    calls = []

    def verify(field_path, value, config):
        calls.append(field_path)
        return ProviderCheck("valid")

    _set(env, "app.llm_provider", "openai")
    result, _ = _run(env, verify_key=verify)
    assert calls == []  # rien à vérifier : aucune clé n'existe pour l'instant
    assert result["fields"]["app.llm_provider"]["state"] == "ok"


def test_changing_llm_provider_rolls_back_together_when_the_companion_key_is_rejected(env):
    # app.llm_provider lui-même a une forme parfaitement valide : c'est la clé openai déjà en place (vérifiée
    # comme « compagnon », voir _fields_to_verify) qui est refusée. Tout le lot revient en arrière ensemble.
    _set(env, "app.llm_provider", "openai")
    result, calls = _run(env, verify_key=lambda f, v, c: ProviderCheck("rejected", "401"))
    assert result["state"] == "rolled_back"  # pas "rejected" au niveau global : app.llm_provider n'a rien de rejetable
    assert calls == ["restart", "restart"]
    assert 'llm_provider = "moonshot"' in env["config"].read_text()  # restauré
    assert result["fields"]["app.llm_provider"]["state"] == "rolled_back"  # pas "rejected" : ce n'est pas LUI qui l'a été


def test_changing_llm_provider_together_with_its_key_verifies_the_new_value_not_the_old_one(env):
    seen = []

    def verify(field_path, value, config):
        seen.append((field_path, value))
        return ProviderCheck("valid")

    env["secrets"].write_text('[app]\nopenai_api_key = "sk-brand-new"\nllm_provider = "openai"\n', encoding="utf-8")
    _run(env, verify_key=verify)
    assert seen == [("app.openai_api_key", "sk-brand-new")]  # une seule vérification, pas de doublon


def test_a_key_that_was_not_touched_is_never_sent_to_the_provider(env):
    calls = []

    def verify(field_path, value, config):
        calls.append(field_path)
        return ProviderCheck("valid")

    _set(env, "elevenlabs.api_key", "voice-secret")  # openai_api_key n'est pas touchée, llm_provider non plus
    _run(env, verify_key=verify)
    assert calls == ["elevenlabs.api_key"]


def test_provider_check_exception_never_leaks_into_the_status_or_result(env):
    def boom(field_path, value, config):
        raise RuntimeError(f"leak attempt with the value {value}")

    _set(env, "app.openai_api_key", "sk-should-never-leak-9999")
    result, _ = _run(env, verify_key=boom)
    assert "sk-should-never-leak-9999" not in json.dumps(result)
    assert "sk-should-never-leak-9999" not in env["status"].read_text()
    assert "leak attempt" not in json.dumps(result) and "leak attempt" not in env["status"].read_text()


# -- _http_check : vrai serveur HTTP local, aucun réseau externe -----------------------------------------------------
class _Handler:
    """Fabrique un gestionnaire http.server.BaseHTTPRequestHandler paramétrable (statut, délai, en-têtes vus)."""

    def __init__(self, status=200, delay=0.0, redirect_to=None, body=None):
        self.status = status
        self.delay = delay
        self.redirect_to = redirect_to
        self.body = body
        self.seen_headers = []

    def build(self):
        import time as time_module
        from http.server import BaseHTTPRequestHandler

        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 — imposé par BaseHTTPRequestHandler
                outer.seen_headers.append(dict(self.headers))
                if outer.delay:
                    time_module.sleep(outer.delay)
                if outer.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", outer.redirect_to)
                    self.end_headers()
                    return
                self.send_response(outer.status)
                if outer.body is not None:
                    self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                if outer.body is not None:
                    self.wfile.write(outer.body)

            def log_message(self, *a):  # tait les journaux du serveur de test
                pass

        return Handler


@pytest.fixture
def local_server():
    import threading
    from http.server import HTTPServer

    servers = []

    def start(status=200, delay=0.0, redirect_to=None, body=None):
        handler_factory = _Handler(status=status, delay=delay, redirect_to=redirect_to, body=body)
        server = HTTPServer(("127.0.0.1", 0), handler_factory.build())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}", handler_factory

    yield start
    for server in servers:
        server.shutdown()


def test_http_check_2xx_is_valid(local_server):
    url, _ = local_server(status=200)
    assert apply_secrets._http_check(url, {"Authorization": "Bearer x"}, 3).outcome == "valid"


def test_http_check_401_is_rejected(local_server):
    url, _ = local_server(status=401)
    result = apply_secrets._http_check(url, {"Authorization": "Bearer x"}, 3)
    assert result.outcome == "rejected" and "x" not in result.detail and "Bearer" not in result.detail


def test_http_check_403_is_rejected(local_server):
    url, _ = local_server(status=403)
    assert apply_secrets._http_check(url, {"Authorization": "Bearer x"}, 3).outcome == "rejected"


def test_http_check_500_is_unavailable_not_rejected(local_server):
    url, _ = local_server(status=500)
    assert apply_secrets._http_check(url, {"Authorization": "Bearer x"}, 3).outcome == "unavailable"


def test_http_check_timeout_is_a_distinct_outcome(local_server):
    url, _ = local_server(delay=1.0)
    assert apply_secrets._http_check(url, {"Authorization": "Bearer x"}, 0.2).outcome == "timeout"


def test_http_check_connection_error_is_unavailable():
    result = apply_secrets._http_check("http://127.0.0.1:1", {"Authorization": "Bearer x"}, 2)
    assert result.outcome == "unavailable"


def test_http_check_sends_the_key_only_in_the_header_never_in_the_url(local_server):
    url, handler = local_server(status=200)
    apply_secrets._http_check(url, {"Authorization": "Bearer sk-header-only-marker"}, 3)
    assert "sk-header-only-marker" not in url
    assert handler.seen_headers[0].get("Authorization") == "Bearer sk-header-only-marker"


def test_http_check_never_follows_a_redirect(local_server):
    other_url, other_handler = local_server(status=200)
    redirecting_url, _ = local_server(redirect_to=other_url)
    result = apply_secrets._http_check(redirecting_url, {"Authorization": "Bearer sk-should-not-travel"}, 3)
    assert result.outcome == "unavailable"  # 302 traité comme un échec, jamais suivi
    assert other_handler.seen_headers == []  # l'en-tête n'a JAMAIS atteint l'autre hôte


def test_real_verify_key_routes_each_field_to_the_right_check(monkeypatch):
    seen = []
    monkeypatch.setattr(apply_secrets, "_http_check",
                        lambda url, headers, timeout: seen.append((url, list(headers))) or ProviderCheck("valid"))
    verify = apply_secrets._real_verify_key(5.0)
    verify("app.openai_api_key", "sk-x", {"app": {}})
    verify("app.openai_image_api_keys", "sk-y", {"app": {}})
    verify("elevenlabs.api_key", "voice-z", {})
    assert seen[0] == ("https://api.openai.com/v1/models", ["Authorization"])
    assert seen[1] == ("https://api.openai.com/v1/models", ["Authorization"])
    assert seen[2] == ("https://api.elevenlabs.io/v1/user", ["xi-api-key"])


def test_real_verify_key_respects_a_custom_openai_base_url(monkeypatch):
    seen = []
    monkeypatch.setattr(apply_secrets, "_http_check",
                        lambda url, headers, timeout: seen.append(url) or ProviderCheck("valid"))
    verify = apply_secrets._real_verify_key(5.0)
    verify("app.openai_api_key", "sk-x", {"app": {"openai_base_url": "https://proxy.example/v1/"}})
    assert seen == ["https://proxy.example/v1/models"]


# -- déclenchement automatique du catalogue de voix ElevenLabs (#55) ----------------------------------------------
def test_elevenlabs_key_change_regenerates_the_voice_report(env, monkeypatch):
    calls = []
    monkeypatch.setattr(apply_secrets.elevenlabs_voices, "write_report",
                        lambda config_path, out_path: calls.append((config_path, out_path)))
    _set(env, "elevenlabs.api_key", "voice-secret")
    result, _ = _run(env)
    assert result["state"] == "ready"
    assert calls == [(env["config"], env["voice_report"])]


def test_unrelated_key_change_never_calls_elevenlabs(env, monkeypatch):
    """Changer la clé OpenAI ne doit jamais déclencher un appel à ElevenLabs."""
    calls = []
    monkeypatch.setattr(apply_secrets.elevenlabs_voices, "write_report",
                        lambda config_path, out_path: calls.append((config_path, out_path)))
    _set(env, "app.openai_api_key", "sk-new-9999")
    result, _ = _run(env)
    assert result["state"] == "ready"
    assert calls == []


def test_voice_report_path_none_disables_the_feature_without_error(env, monkeypatch):
    calls = []
    monkeypatch.setattr(apply_secrets.elevenlabs_voices, "write_report",
                        lambda config_path, out_path: calls.append((config_path, out_path)))
    _set(env, "elevenlabs.api_key", "voice-secret")
    result, _ = _run(env, voice_report_path=None)
    assert result["state"] == "ready" and calls == []


def test_voice_report_failure_is_best_effort_and_never_breaks_a_valid_key_apply(env, monkeypatch):
    """Une panne ElevenLabs transitoire lors de la régénération du catalogue ne doit jamais faire échouer
    l'application d'une clé par ailleurs valide et vérifiée."""
    def boom(config_path, out_path):
        raise RuntimeError("ElevenLabs indisponible")

    monkeypatch.setattr(apply_secrets.elevenlabs_voices, "write_report", boom)
    _set(env, "elevenlabs.api_key", "voice-secret")
    result, _ = _run(env)
    assert result["state"] == "ready"
    assert result["fields"]["elevenlabs.api_key"]["state"] == "ok"


def test_voice_report_is_also_regenerated_after_a_rollback_when_elevenlabs_key_was_in_the_batch(env, monkeypatch):
    """Après un retour arrière, la clé ACTIVE redevient l'ancienne : le catalogue doit refléter celle-ci."""
    calls = []
    monkeypatch.setattr(apply_secrets.elevenlabs_voices, "write_report",
                        lambda config_path, out_path: calls.append((config_path, out_path)))

    def rejecting(field_path, value, config):
        return ProviderCheck("rejected") if field_path == "elevenlabs.api_key" else ProviderCheck("valid")

    _set(env, "elevenlabs.api_key", "voice-forbidden")
    result, _ = _run(env, verify_key=rejecting)
    assert result["state"] == "rejected"
    assert calls == [(env["config"], env["voice_report"])]


def test_cli_voice_report_argument_defaults_to_settings_path(monkeypatch, tmp_path):
    """La CLI (scripts/lody-apply-secrets.sh) doit pouvoir omettre --voice-report et retomber sur le même
    emplacement que le catalogue lu par l'interface (settings.voice_catalog_report_path())."""
    import lody.settings as settings_mod

    captured = {}

    def fake_apply(**kwargs):
        captured.update(kwargs)
        return {"state": "noop"}

    monkeypatch.setattr(apply_secrets, "apply", fake_apply)
    monkeypatch.setattr(settings_mod, "voice_catalog_report_path", lambda: tmp_path / "default-voices.json")
    monkeypatch.setattr(settings_mod, "config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(settings_mod, "secrets_dir", lambda: tmp_path)
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    apply_secrets.main(["--container", "moneyprinterturbo-api"])
    assert captured["voice_report_path"] == tmp_path / "default-voices.json"
