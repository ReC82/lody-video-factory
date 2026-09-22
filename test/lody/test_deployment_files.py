"""Fichiers de déploiement : Dockerfile, Compose, healthcheck (sans réseau externe).

La plupart des tests lisent seulement les fichiers (aucun Docker requis). Un test vérifie en plus, avec un
vrai `docker compose config`, que LODY_SECRETS_GID est obligatoire — ignoré si Docker ou le sudo sans mot de
passe attendu par ce dépôt (voir docs/lody-secrets.md) ne sont pas disponibles."""

import os
import re
import shutil
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
    # Verrou de mise en ligne (ticket #28) : activé en production après la finalisation opérationnelle
    # complète (nginx protège /lody-auth/*, authentification admin en place — voir docs/lody-auth.md).
    # Avant cette validation, le drapeau devait rester absent ou commenté ; ce n'est plus l'état voulu.
    assert service["environment"].get("LODY_ENABLE_SYSTEM_SETTINGS") == "1"
    # Groupe partagé host<->conteneur (voir lody-setup-secrets-group.sh) : requis, pas de GID par défaut codé
    # en dur (chaque hôte a le sien). Compose doit refuser de démarrer si LODY_SECRETS_GID est absent.
    assert service["group_add"] == ["${LODY_SECRETS_GID:?exécute ./scripts/lody-setup-secrets-group.sh puis mets LODY_SECRETS_GID dans .env}"]


def test_compose_refuses_to_start_without_lody_secrets_gid_set():
    """Vérification réelle avec Docker Compose (pas seulement une lecture YAML) : sans LODY_SECRETS_GID, la
    résolution de group_add doit échouer avec un message clair plutôt que démarrer sans le groupe."""
    if not shutil.which("docker") or not shutil.which("sudo"):
        pytest.skip("docker ou sudo introuvable")
    if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=10).returncode != 0:
        pytest.skip("sudo sans mot de passe indisponible dans cet environnement")
    result = subprocess.run(
        ["sudo", "-n", "env", "-u", "LODY_SECRETS_GID", "docker", "compose", "--env-file", "/dev/null",
         "-f", str(ROOT / "docker-compose.lody.yml"), "config", "--quiet"],
        capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "LODY_SECRETS_GID" in result.stderr


def test_systemd_units_exist_watch_the_signal_file_and_run_the_apply_script():
    path_unit = (ROOT / "deploy/systemd/lody-secrets-reload.path").read_text(encoding="utf-8")
    service_unit = (ROOT / "deploy/systemd/lody-secrets-reload.service").read_text(encoding="utf-8")
    assert "PathModified=" in path_unit and "secrets.reload" in path_unit
    assert "PathModified=/srv/moneyprinterturbo/secrets/secrets.reload" in path_unit
    assert "Type=oneshot" in service_unit
    assert "lody-apply-secrets.sh" in service_unit
    assert "User=ubuntu" in service_unit and "User=root" not in service_unit  # jamais en root


def test_lody_auth_image_exists_and_never_copies_secrets():
    dockerfile = (ROOT / "Dockerfile.lody-auth").read_text(encoding="utf-8")
    dockerignore = (ROOT / "Dockerfile.lody-auth.dockerignore").read_text(encoding="utf-8")
    assert not re.search(r"COPY[^\n]*secrets", dockerfile)
    assert not re.search(r"COPY[^\n]*config\.toml", dockerfile)
    assert re.search(r"^USER lody-auth\s*$", dockerfile, re.MULTILINE)  # jamais root
    for ignored in ("secrets", "config.toml", ".env", ".git"):
        assert re.search(rf"^{re.escape(ignored)}$", dockerignore, re.MULTILINE), ignored


def test_compose_wires_lody_auth_read_only_and_separately_from_the_engine_network():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / "docker-compose.lody.yml").read_text(encoding="utf-8"))
    auth = compose["services"]["lody-auth"]
    assert "./secrets:/secrets:ro" in auth["volumes"]  # jamais rw : ce service ne modifie jamais admin-auth.json
    assert "engine" not in auth.get("networks", [])  # aucune raison de joindre le réseau du moteur
    assert auth["ports"] == ["127.0.0.1:8602:8602"]  # local uniquement, comme lody-ui
    assert auth["cap_drop"] == ["ALL"]
    lody_ui = compose["services"]["lody-ui"]
    assert lody_ui["environment"]["LODY_AUTH_VERIFY_URL"] == "http://lody-auth:8602/verify"
    assert lody_ui["depends_on"]["lody-auth"]["condition"] == "service_healthy"


def test_nginx_snippet_blocks_verify_publicly_and_rate_limits_login():
    conf = (ROOT / "deploy/nginx/lody-auth.conf").read_text(encoding="utf-8")
    assert "location = /lody-auth/verify" in conf and "return 404" in conf
    assert "limit_req" in conf
    assert "NE PAS appliquer avant validation" in conf


def test_setup_and_verification_scripts_exist_and_are_valid_shell():
    for name in ("lody-setup-secrets-group.sh", "lody-verify-secrets-permissions.sh", "lody-apply-secrets.sh",
                "lody-auth-set-password.sh"):
        script = ROOT / "scripts" / name
        assert script.is_file(), name
        result = subprocess.run(["sh", "-n", str(script)], capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_setup_script_creates_a_dedicated_group_with_setgid_and_no_world_access():
    content = (ROOT / "scripts" / "lody-setup-secrets-group.sh").read_text(encoding="utf-8")
    assert "groupadd" in content and "lody-secrets" in content
    assert "chmod 2770 secrets" in content  # setgid + rwxrws--- : aucun accès pour « autre »
    assert "usermod -aG" in content


def test_verify_script_reproduces_the_real_container_uid_and_a_third_party_user():
    content = (ROOT / "scripts" / "lody-verify-secrets-permissions.sh").read_text(encoding="utf-8")
    assert "-o 10001" in content  # le vrai uid du conteneur lody-ui (voir Dockerfile.lody)
    assert "nobody" in content  # utilisateur tiers, hors du groupe : doit échouer à lire


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
