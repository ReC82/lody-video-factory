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
    for ignored in ("config.toml", "storage", "data", ".env", ".git"):
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


def test_requirements_and_gitignore_protect_local_data():
    assert "streamlit" in (ROOT / "requirements.lody.txt").read_text(encoding="utf-8")
    assert re.search(r"^/data/$", (ROOT / ".gitignore").read_text(encoding="utf-8"), re.MULTILINE)


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
