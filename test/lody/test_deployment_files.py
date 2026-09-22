"""Fichiers de déploiement : Dockerfile, Compose, healthcheck (sans Docker ni réseau externe)."""

import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "Dockerfile.lody").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / "Dockerfile.lody.dockerignore").read_text(encoding="utf-8")


def test_dockerfile_uses_debian_13_slim_and_no_alpine_or_busybox():
    assert re.search(r"^FROM python:3\.12-slim-trixie\s*$", DOCKERFILE, re.MULTILINE)
    from_lines = [line for line in DOCKERFILE.splitlines() if line.startswith("FROM ")]
    assert from_lines == ["FROM python:3.12-slim-trixie"]
    assert not any(word in line.lower() for line in from_lines for word in ("alpine", "busybox"))


def test_dockerfile_keeps_media_tools_fonts_nonroot_user_and_healthcheck():
    assert re.search(r"apt-get install[^\n]*ffmpeg", DOCKERFILE)  # ffmpeg fournit ffprobe
    assert "fonts-dejavu-core" in DOCKERFILE
    assert re.search(r"^USER lody\s*$", DOCKERFILE, re.MULTILINE)
    assert "HEALTHCHECK" in DOCKERFILE and "/_stcore/health" in DOCKERFILE


def test_dockerfile_never_copies_configuration_or_secrets():
    assert not re.search(r"COPY[^\n]*config\.toml", DOCKERFILE)
    for ignored in ("config.toml", "storage", "engine-report", "data", ".env", ".git"):
        assert re.search(rf"^{re.escape(ignored)}$", DOCKERIGNORE, re.MULTILINE), ignored


def test_compose_file_is_isolated_and_keeps_existing_mounts():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / "docker-compose.lody.yml").read_text(encoding="utf-8"))
    assert compose["name"] == "lody-video-factory"
    service = compose["services"]["lody-ui"]
    assert service["build"]["dockerfile"] == "Dockerfile.lody"
    assert service["image"].startswith("lody-video-factory:")
    assert "ghcr.io" not in str(compose) and ":latest" not in str(compose)
    assert service["pull_policy"] == "never"
    assert service["container_name"] == "lody-video-factory-ui"
    assert service["ports"] == ["127.0.0.1:8601:8601"]
    volumes = service["volumes"]
    assert "./config.toml:/MoneyPrinterTurbo/config.toml:ro" in volumes
    assert "./storage:/MoneyPrinterTurbo/storage:ro" in volumes
    assert "lody_data:/data" in volumes
    assert compose["volumes"]["lody_data"]["name"] == "lody-video-factory-data"
    assert service["restart"] == "unless-stopped"
    assert service["healthcheck"]["test"][-1].count("/_stcore/health") == 1
    assert "8501" not in str(service["ports"]) and "8080" not in str(service["ports"])


def test_compose_reaches_the_engine_without_exposing_more_or_holding_secrets():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / "docker-compose.lody.yml").read_text(encoding="utf-8"))
    service = compose["services"]["lody-ui"]
    assert service["environment"]["LODY_ENGINE_URL"] == "http://moneyprinterturbo-api:8080"
    assert service["environment"]["LODY_ENGINE_STORAGE"] == "/MoneyPrinterTurbo/storage"
    assert set(service["networks"]) == {"default", "engine"}
    assert compose["networks"]["engine"] == {"name": "moneyprinterturbo_default", "external": True}
    assert "./engine-report:/engine-report:ro" in service["volumes"]  # rapport assaini, lecture seule
    assert service["environment"]["LODY_ENGINE_REPORT"] == "/engine-report/engine-capabilities.json"
    assert service["ports"] == ["127.0.0.1:8601:8601"]  # l'API n'est pas republiée
    raw = (ROOT / "docker-compose.lody.yml").read_text(encoding="utf-8")
    assert not re.search(r"(?i)(api_key|secret|token|password)\s*[:=]\s*\S", raw.replace("Aucune clé ici", ""))
    assert "LODY_ENGINE_API_KEY" not in raw and "./config.toml:/MoneyPrinterTurbo/config.toml:ro" in raw


def test_requirements_and_gitignore_protect_local_data():
    assert "streamlit" in (ROOT / "requirements.lody.txt").read_text(encoding="utf-8")
    assert re.search(r"^/data/$", (ROOT / ".gitignore").read_text(encoding="utf-8"), re.MULTILINE)


def test_secrets_directory_is_never_versioned_or_baked_into_the_image():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^/secrets/$", gitignore, re.MULTILINE)
    assert re.search(r"^/config\.toml\.rollback$", gitignore, re.MULTILINE)
    assert re.search(r"^secrets$", DOCKERIGNORE, re.MULTILINE)
    assert re.search(r"^config\.toml\.rollback$", DOCKERIGNORE, re.MULTILINE)
    assert not re.search(r"COPY[^\n]*secrets", DOCKERFILE)


def test_compose_mounts_only_a_small_secrets_directory_read_write_config_toml_stays_read_only():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / "docker-compose.lody.yml").read_text(encoding="utf-8"))
    service = compose["services"]["lody-ui"]
    volumes = service["volumes"]
    assert "./secrets:/secrets:rw" in volumes
    assert "./config.toml:/MoneyPrinterTurbo/config.toml:ro" in volumes  # jamais rw : voir docs/lody-secrets.md
    assert not any(v.startswith("./config.toml:") and v.endswith(":rw") for v in volumes)
    assert service["environment"]["LODY_SECRETS_DIR"] == "/secrets"
    # Verrou de mise en ligne : désactivé (absent ou commenté), jamais actif par défaut.
    assert "LODY_ENABLE_SYSTEM_SETTINGS" not in service.get("environment", {})


def test_systemd_units_exist_watch_the_signal_file_and_run_the_apply_script():
    path_unit = (ROOT / "deploy/systemd/lody-secrets-reload.path").read_text(encoding="utf-8")
    service_unit = (ROOT / "deploy/systemd/lody-secrets-reload.service").read_text(encoding="utf-8")
    assert "PathModified=" in path_unit and "secrets.reload" in path_unit
    assert "PathModified=/srv/moneyprinterturbo/secrets/secrets.reload" in path_unit
    assert "Type=oneshot" in service_unit
    assert "lody-apply-secrets.sh" in service_unit


def test_lody_container_never_gets_docker_access():
    """Le redémarrage du moteur est fait par le service systemd, hors conteneur — jamais par Lody lui-même."""
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / "docker-compose.lody.yml").read_text(encoding="utf-8"))
    service = compose["services"]["lody-ui"]
    assert not any("docker.sock" in str(v) for v in service["volumes"])
    assert "cap_drop" in service and "ALL" in service["cap_drop"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_application_answers_the_healthcheck_endpoint(lody_env):
    pytest.importorskip("streamlit")
    port = _free_port()
    env = {**os.environ, "LODY_DATA_DIR": str(lody_env), "HOME": str(lody_env.parent)}
    process = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "webui" / "Lody.py"),
         "--server.headless=true", f"--server.port={port}", "--server.address=127.0.0.1",
         "--browser.gatherUsageStats=false"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 45
        status = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as response:
                    status = response.status
                    break
            except OSError:
                time.sleep(0.5)
        assert status == 200
    finally:
        process.terminate()
        process.wait(timeout=10)
