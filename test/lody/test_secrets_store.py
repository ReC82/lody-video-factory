"""``secrets_store`` : écriture atomique de ``secrets.toml`` (0640, groupe lody-secrets), jamais de lecture de valeur, statut sûr."""

from __future__ import annotations

import stat

import pytest

from lody.generation import secrets_store
from lody.generation.secrets_fields import ShapeError


@pytest.fixture
def paths(tmp_path):
    return {"secrets": tmp_path / "secrets.toml", "reload": tmp_path / "secrets.reload",
            "status": tmp_path / "secrets-status.json"}


def test_no_function_returns_a_secret_value_to_a_caller_outside_this_module():
    """Garantie structurelle, pas seulement testée en pratique : ces fonctions n'existent pas.

    ``read_pending`` existe (elle relit les valeurs) mais n'est appelée que par ce module lui-même (fusion à
    l'écriture) et par ``apply_secrets`` côté hôte — jamais par l'interface. Ce test fixe les noms que l'interface
    est censée utiliser : aucun ne peut renvoyer une valeur en clair à l'utilisateur.
    """
    ui_facing = {"set_pending", "status", "field_status", "clear_pending"}
    for name in ui_facing:
        assert hasattr(secrets_store, name)
    for name in ("get_secret", "get_value", "reveal", "export"):
        assert not hasattr(secrets_store, name)


def test_set_pending_writes_the_file_mode_0640_group_readable_not_world_readable(paths):
    secrets_store.set_pending("app.openai_api_key", "sk-abcdefghij", secrets_path=paths["secrets"], reload_path=paths["reload"])
    mode = stat.S_IMODE(paths["secrets"].stat().st_mode)
    assert mode == 0o640
    assert mode & 0o007 == 0, "aucun bit « autre » : voir docs/lody-secrets.md (groupe lody-secrets)"
    assert paths["reload"].exists()
    reload_mode = stat.S_IMODE(paths["reload"].stat().st_mode)
    assert reload_mode == 0o640 and reload_mode & 0o007 == 0


def test_round_trips_through_read_pending(paths):
    secrets_store.set_pending("app.openai_api_key", "sk-abcdefghij", secrets_path=paths["secrets"], reload_path=paths["reload"])
    assert secrets_store.read_pending(paths["secrets"]) == {"app.openai_api_key": "sk-abcdefghij"}


def test_merges_with_an_existing_pending_field_instead_of_clobbering_it(paths):
    secrets_store.set_pending("app.openai_api_key", "sk-one", secrets_path=paths["secrets"], reload_path=paths["reload"])
    secrets_store.set_pending("elevenlabs.api_key", "eleven-one", secrets_path=paths["secrets"], reload_path=paths["reload"])
    assert secrets_store.read_pending(paths["secrets"]) == {"app.openai_api_key": "sk-one", "elevenlabs.api_key": "eleven-one"}


def test_overwrites_the_same_field(paths):
    secrets_store.set_pending("app.openai_api_key", "sk-one", secrets_path=paths["secrets"], reload_path=paths["reload"])
    secrets_store.set_pending("app.openai_api_key", "sk-two", secrets_path=paths["secrets"], reload_path=paths["reload"])
    assert secrets_store.read_pending(paths["secrets"]) == {"app.openai_api_key": "sk-two"}


def test_list1_field_round_trips(paths):
    secrets_store.set_pending("app.openai_image_api_keys", "img-secret", secrets_path=paths["secrets"], reload_path=paths["reload"])
    assert secrets_store.read_pending(paths["secrets"]) == {"app.openai_image_api_keys": "img-secret"}


def test_llm_provider_choice_field_round_trips(paths):
    secrets_store.set_pending("app.llm_provider", "openai", secrets_path=paths["secrets"], reload_path=paths["reload"])
    assert secrets_store.read_pending(paths["secrets"]) == {"app.llm_provider": "openai"}


@pytest.mark.parametrize("value", ["", "  ", "has space", "line\nbreak", "x" * 600])
def test_invalid_shape_is_rejected_and_nothing_is_written(paths, value):
    with pytest.raises(ShapeError):
        secrets_store.set_pending("app.openai_api_key", value, secrets_path=paths["secrets"], reload_path=paths["reload"])
    assert not paths["secrets"].exists()
    assert not paths["reload"].exists()


def test_llm_provider_rejects_anything_outside_the_allowlist(paths):
    with pytest.raises(ShapeError):
        secrets_store.set_pending("app.llm_provider", "moonshot", secrets_path=paths["secrets"], reload_path=paths["reload"])
    assert not paths["secrets"].exists()


def test_unknown_field_is_rejected(paths):
    with pytest.raises(ShapeError):
        secrets_store.set_pending("app.anthropic_api_key", "sk-x", secrets_path=paths["secrets"], reload_path=paths["reload"])


def test_a_write_failure_leaves_the_previous_content_untouched(paths, monkeypatch):
    secrets_store.set_pending("app.openai_api_key", "sk-one", secrets_path=paths["secrets"], reload_path=paths["reload"])

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(secrets_store.os, "replace", boom)
    with pytest.raises(OSError):
        secrets_store.set_pending("app.openai_api_key", "sk-two", secrets_path=paths["secrets"], reload_path=paths["reload"])
    monkeypatch.undo()
    assert secrets_store.read_pending(paths["secrets"]) == {"app.openai_api_key": "sk-one"}


def test_clear_pending_removes_only_the_named_fields(paths):
    secrets_store.set_pending("app.openai_api_key", "sk-one", secrets_path=paths["secrets"], reload_path=paths["reload"])
    secrets_store.set_pending("elevenlabs.api_key", "eleven-one", secrets_path=paths["secrets"], reload_path=paths["reload"])
    secrets_store.clear_pending(["app.openai_api_key"], secrets_path=paths["secrets"])
    assert secrets_store.read_pending(paths["secrets"]) == {"elevenlabs.api_key": "eleven-one"}


def test_clear_pending_removes_the_file_entirely_once_empty(paths):
    secrets_store.set_pending("app.openai_api_key", "sk-one", secrets_path=paths["secrets"], reload_path=paths["reload"])
    secrets_store.clear_pending(["app.openai_api_key"], secrets_path=paths["secrets"])
    assert not paths["secrets"].exists()


def test_status_defaults_to_idle_when_missing(paths):
    assert secrets_store.status(status_path=paths["status"]) == secrets_store.DEFAULT_STATUS


def test_status_tolerates_a_corrupt_file(paths):
    paths["status"].write_text("not json{{", encoding="utf-8")
    assert secrets_store.status(status_path=paths["status"]) == secrets_store.DEFAULT_STATUS


def test_field_status_reads_the_per_field_entry(paths):
    import json

    paths["status"].write_text(json.dumps({"state": "ready", "fields": {
        "app.openai_api_key": {"state": "ok", "message": ""},
        "elevenlabs.api_key": {"state": "invalid", "message": "Clé voix et musique : la valeur ne peut pas être vide."},
    }}), encoding="utf-8")
    assert secrets_store.field_status("app.openai_api_key", status_path=paths["status"])["state"] == "ok"
    bad = secrets_store.field_status("elevenlabs.api_key", status_path=paths["status"])
    assert bad["state"] == "invalid" and "vide" in bad["message"]


def test_field_status_defaults_to_idle_for_an_unknown_field(paths):
    assert secrets_store.field_status("app.openai_api_key", status_path=paths["status"])["state"] == "idle"
