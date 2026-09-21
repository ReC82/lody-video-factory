"""État des fournisseurs : booléens uniquement, jamais de valeur de clé."""

from lody import provider_status


def test_readiness_reports_booleans_only(lody_env):
    table = provider_status.readiness()
    assert all(isinstance(value, bool) for value in table.values())
    assert table[("text", "openai")] is True       # clé présente dans la config factice
    assert table[("visual", "openai_image")] is False  # liste vide
    assert table[("voice", "elevenlabs")] is False     # chaîne vide
    assert "FAKE-CONFIG-VALUE-NOT-A-KEY" not in repr(table)


def test_environment_variable_counts_as_configured(lody_env, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "valeur-de-test")
    table = provider_status.readiness()
    assert table[("voice", "elevenlabs")] is True and table[("music", "elevenlabs")] is True
    assert "valeur-de-test" not in repr(table)


def test_missing_or_invalid_config_is_not_an_error(lody_env, monkeypatch, tmp_path):
    monkeypatch.setenv("LODY_CONFIG_PATH", str(tmp_path / "absent.toml"))
    assert provider_status.readiness()[("text", "openai")] is False
    broken = tmp_path / "broken.toml"
    broken.write_text("= pas du toml", encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(broken))
    assert provider_status.readiness()[("text", "openai")] is False


def test_providers_without_key_are_always_ready(lody_env):
    table = provider_status.readiness()
    assert provider_status.is_ready("voice", "edge", table) is True
    assert provider_status.is_ready("music", "library", table) is True
