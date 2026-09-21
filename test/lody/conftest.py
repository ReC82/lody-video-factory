"""Fixtures communes : chaque test utilise sa propre base, sans secret ni réseau."""

import sys
from pathlib import Path

import pytest

WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))


@pytest.fixture
def lody_env(tmp_path, monkeypatch):
    """Isole données et configuration ; les exemples initiaux sont activés par défaut."""
    data_dir = tmp_path / "data"
    config = tmp_path / "config.toml"
    # Configuration factice : valeurs volontairement identifiables pour prouver l'absence de fuite.
    config.write_text(
        '[app]\nopenai_api_key = "FAKE-CONFIG-VALUE-NOT-A-KEY"\nopenai_image_api_keys = []\n'
        '[elevenlabs]\napi_key = ""\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LODY_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LODY_CONFIG_PATH", str(config))
    monkeypatch.setenv("LODY_SEED_DEFAULTS", "1")
    # Aucun test ne doit joindre un vrai moteur : port local fermé (refus immédiat) + stockage isolé.
    monkeypatch.setenv("LODY_ENGINE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("LODY_ENGINE_STORAGE", str(tmp_path / "engine-storage"))
    monkeypatch.delenv("LODY_ENGINE_API_KEY", raising=False)
    monkeypatch.setenv("LODY_PRICING_PATH", str(tmp_path / "pricing.toml"))
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return data_dir
