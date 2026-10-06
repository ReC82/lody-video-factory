"""Garde-fou : le namespace GitHub/GHCR historique de l'upstream ne doit plus apparaître NULLE PART.

Lody Video Factory est un fork : l'attribution à MoneyPrinterTurbo et la licence MIT restent dues, et
restent présentes (voir ``test_the_upstream_attribution_is_still_there``). Ce qui ne doit plus exister,
c'est une référence au COMPTE d'origine — ni technique, ni documentaire :

* publier une image dans son espace GHCR échoue (`denied: permission_denied`), le `GITHUB_TOKEN` de ce
  dépôt ne pouvant écrire que dans les packages de son propre owner ;
* interroger ses publications proposerait des versions sans rapport avec le code installé ;
* ses liens renvoient les utilisateurs de CE dépôt vers un autre projet.

Critère : ``git grep -ni <namespace>`` doit retourner **zéro** occurrence. Il n'y a donc PAS d'allowlist.

Le nom recherché est assemblé à l'exécution : écrit en clair, ce fichier serait lui-même une occurrence et
le critère ne pourrait jamais être atteint.

Aucun réseau, aucun Docker : lecture de fichiers uniquement.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Assemblé, jamais écrit en clair — voir le docstring du module.
LEGACY_NAMESPACE = "harry" + "0703"
CANONICAL_IMAGE = "ghcr.io/rec82/lody-video-factory"
CANONICAL_REPO = "ReC82/lody-video-factory"

_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".ruff_cache",
              "storage", "data", "secrets", "engine-report", ".mypy_cache"}


def _tracked_files() -> list[Path]:
    """Les fichiers SUIVIS PAR GIT : même périmètre que le ``git grep`` du critère d'acceptation."""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
        files = [ROOT / name for name in out.stdout.decode("utf-8").split("\0") if name]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):  # git indisponible : on retombe sur le disque
        pass
    return [path for path in ROOT.rglob("*")
            if path.is_file() and not any(part in _SKIP_DIRS for part in path.relative_to(ROOT).parts)]


def _occurrences() -> dict[str, int]:
    needle = LEGACY_NAMESPACE.lower()
    found: dict[str, int] = {}
    for path in _tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, FileNotFoundError):
            continue  # binaire ou fichier supprimé de l'index : rien de lisible à auditer
        count = text.lower().count(needle)
        if count:
            found[path.relative_to(ROOT).as_posix()] = count
    return found


def test_the_legacy_namespace_appears_nowhere():
    """Critère impératif : zéro occurrence, insensible à la casse, sur tous les fichiers suivis."""
    found = _occurrences()
    assert not found, (
        f"Le namespace historique réapparaît dans : {found}. Remplace-le par « {CANONICAL_REPO} » "
        f"(GitHub) ou « {CANONICAL_IMAGE} » (image GHCR). Pour une attribution, nomme MoneyPrinterTurbo "
        f"sans le compte d'origine — la notice de copyright vit dans LICENSE."
    )


def test_the_upstream_attribution_is_still_there():
    """Le nettoyage ne doit pas avoir supprimé l'attribution : la licence MIT l'exige. Elle doit juste
    être dissociée du namespace."""
    assert "MIT" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MoneyPrinterTurbo" in (ROOT / "README.md").read_text(encoding="utf-8")
    footer = (ROOT / "webui" / "lody" / "components.py").read_text(encoding="utf-8")
    assert "MoneyPrinterTurbo (licence MIT)" in footer


# -- publication Docker : la cible doit appartenir à ce dépôt ------------------------------------------
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "docker-ghcr.yml"


def _workflow() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))


def test_publish_workflow_targets_the_canonical_lowercase_image():
    """Cause exacte de l'échec corrigé : la cible appartenait à un autre namespace, donc
    « denied: permission_denied: The requested installation does not exist » à chaque push sur main."""
    image = _workflow()["env"]["IMAGE_NAME"]
    assert image == CANONICAL_IMAGE, image
    assert image == image.lower(), "GHCR refuse les majuscules dans un nom d'image"


def test_publish_workflow_keeps_the_permissions_needed_to_push_a_package():
    assert _workflow()["permissions"] == {"contents": "read", "packages": "write"}


def test_publish_workflow_builds_the_lody_image_not_the_engine_one():
    """Le nom publié doit correspondre à l'artefact publié : `Dockerfile.lody` (interface Lody), jamais
    `./Dockerfile` (image du moteur), sinon l'image serait mal étiquetée."""
    build = next(step for step in _workflow()["jobs"]["publish"]["steps"]
                 if str(step.get("uses", "")).startswith("docker/build-push-action"))
    assert build["with"]["file"] == "./Dockerfile.lody"


# -- le moteur reste un service distinct, construit localement -----------------------------------------
def test_the_engine_service_is_still_built_locally_and_never_renamed():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / "docker-compose.engine-local.yml").read_text(encoding="utf-8"))
    services = list(compose["services"].values())
    assert "moneyprinterturbo-api" in {service.get("container_name") for service in services}
    for service in services:
        image = str(service.get("image", ""))
        assert image.startswith("moneyprinterturbo:"), image  # construite localement, jamais tirée
        assert "lody" not in image.lower(), image


def test_no_compose_or_dockerfile_pulls_an_engine_image_from_an_external_registry():
    """Plus aucun fichier de déploiement ne doit dépendre d'une image publiée hors de ce dépôt : c'était
    le cas de docker-compose.release.yml (supprimé) et de la base de Dockerfile.claude (repointée vers
    l'image construite par docker-compose.engine-local.yml)."""
    assert not (ROOT / "docker-compose.release.yml").exists(), (
        "docker-compose.release.yml a été supprimé : il tirait l'image publique de l'upstream et était "
        "remplacé par docker-compose.engine-local.yml."
    )
    claude = (ROOT / "Dockerfile.claude").read_text(encoding="utf-8")
    assert "ARG BASE_IMAGE=moneyprinterturbo:local" in claude
    assert "FROM ${BASE_IMAGE}" in claude
    assert "ghcr.io" not in claude
