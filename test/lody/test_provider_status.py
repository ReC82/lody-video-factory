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


def test_unreadable_config_is_unverified_not_missing(lody_env, monkeypatch, tmp_path):
    """Config absente, illisible (droits) ou invalide : jamais une erreur, jamais « clé manquante »."""
    monkeypatch.setenv("LODY_CONFIG_PATH", str(tmp_path / "absent.toml"))
    assert provider_status.readiness()[("text", "openai")] is None
    broken = tmp_path / "broken.toml"
    broken.write_text("= pas du toml", encoding="utf-8")
    monkeypatch.setenv("LODY_CONFIG_PATH", str(broken))
    table = provider_status.readiness()
    assert table[("text", "openai")] is None
    assert provider_status.is_unverified("text", "openai", table) and provider_status.is_ready("text", "openai", table)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "valeur-de-test")  # l'environnement reste une source fiable
    assert provider_status.readiness()[("voice", "elevenlabs")] is True


def test_providers_without_key_are_always_ready(lody_env):
    table = provider_status.readiness()
    assert provider_status.is_ready("voice", "edge", table) is True
    assert provider_status.is_ready("music", "library", table) is True
