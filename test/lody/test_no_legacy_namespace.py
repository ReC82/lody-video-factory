"""Garde-fou : le namespace historique de l'upstream (`harry0703`) ne doit plus revenir dans une
référence OPÉRATIONNELLE de ce dépôt.

Lody Video Factory est un fork : les mentions de MoneyPrinterTurbo restent légitimes pour l'attribution
MIT et pour documenter l'origine. Ce qui ne l'est plus, c'est de *dépendre* du namespace historique :
publier une image dans son espace GHCR (le `GITHUB_TOKEN` de ce dépôt n'y a aucun droit — `denied:
permission_denied`), interroger ses publications, ou renvoyer les utilisateurs de CE dépôt vers ses
documents.

Le test échoue donc dès qu'un fichier NON listé ci-dessous contient `harry0703`. Ajouter une entrée à
l'allowlist est un geste explicite, qui demande une justification écrite — jamais un effet de bord.

Aucun réseau, aucun Docker : lecture de fichiers uniquement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEGACY_NAMESPACE = "harry0703"
CANONICAL_IMAGE = "ghcr.io/rec82/lody-video-factory"
CANONICAL_REPO = "ReC82/lody-video-factory"

# Dossiers sans intérêt pour cet audit (artefacts, dépendances, données locales non versionnées).
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".ruff_cache",
              "storage", "data", "secrets", "engine-report", ".mypy_cache"}
_TEXT_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".toml", ".txt", ".html", ".json", ".ipynb", ".sh",
                  ".cfg", ".ini", ".j2", ".conf", ".env", ".example", ""}

# Mentions VOLONTAIREMENT conservées : attribution MIT, identité propre du moteur upstream, et traces
# historiques. La valeur est la justification — elle est lue par un humain en revue, pas par le test.
HERITAGE_ALLOWLIST: dict[str, str] = {
    # -- attribution MIT (obligatoire, et le lien doit pointer vers l'upstream : c'est tout l'objet) ------
    "webui/lody/components.py":
        "Pied de page de Lody : « construit sur MoneyPrinterTurbo (licence MIT) ». Attribution — le lien "
        "DOIT désigner le dépôt upstream.",
    # (LICENSE n'est pas listé : la licence MIT upstream ne nomme pas le compte, seulement « Harry ».)
    # -- identité propre du moteur upstream, qui reste un service distinct --------------------------------
    "webui/Main.py":
        "Ancienne WebUI MoneyPrinterTurbo (port 8501), conservée telle quelle : son branding et ses liens "
        "décrivent le projet upstream, pas Lody. Non exposée par le déploiement Lody.",
    "resource/public/index.html":
        "Page statique servie par le moteur upstream : sa propre identité.",
    "app/config/config.py":
        "`project_description` par défaut du moteur (API FastAPI) : le moteur EST MoneyPrinterTurbo.",
    "app/services/subtitle.py":
        "Message renvoyant à la FAQ du README upstream, où cette FAQ se trouve réellement.",
    "app/services/video.py":
        "Commentaire citant l'issue upstream à l'origine d'un contournement : traçabilité.",
    # -- artefact upstream utilisé comme dépendance séparée (jamais par le déploiement Lody) --------------
    "docker-compose.release.yml":
        "Compose de l'upstream : tire son image publique. Non utilisé par ce déploiement (le moteur tourne "
        "depuis docker-compose.engine-local.yml, image construite localement).",
    "Dockerfile.claude":
        "Surcouche basée sur l'image publique upstream : cette image n'existe que dans ce namespace.",
    # -- documentation d'origine et archives -------------------------------------------------------------
    "README.md": "Documentation upstream conservée (origine du projet).",
    "README-en.md": "Documentation upstream conservée (origine du projet).",
    "README-ja.md": "Documentation upstream conservée (origine du projet).",
    "docs/MoneyPrinterTurbo.ipynb": "Notebook Colab upstream, conservé tel quel.",
    "docs/skill/SKILL.md":
        "Skill upstream (auteur et champ `upstream` = attribution) ; installe le code upstream.",
    "docs/skill/mpt_agent.py":
        "Script du skill upstream : télécharge l'archive upstream, c'est sa raison d'être.",
    "docs/audits/moneyprinterturbo_initial_audit.md":
        "Audit historique daté : décrit l'état du dépôt À L'ÉPOQUE. Le réécrire le falsifierait.",
    # -- ce fichier -------------------------------------------------------------------------------------
    "test/lody/test_no_legacy_namespace.py":
        "Le garde-fou lui-même nomme forcément le namespace qu'il interdit.",
}


def _candidate_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() in _TEXT_SUFFIXES or path.name.startswith("Dockerfile"):
            files.append(path)
    return files


def _files_mentioning_legacy_namespace() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in _candidate_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        count = text.count(LEGACY_NAMESPACE)
        if count:
            found[path.relative_to(ROOT).as_posix()] = count
    return found


def test_no_new_operational_reference_to_the_legacy_namespace():
    """Toute occurrence hors allowlist est une régression : elle doit être corrigée, ou justifiée ici."""
    unexpected = {name: count for name, count in _files_mentioning_legacy_namespace().items()
                  if name not in HERITAGE_ALLOWLIST}
    assert not unexpected, (
        f"Référence(s) au namespace historique « {LEGACY_NAMESPACE} » hors allowlist : {unexpected}. "
        f"Remplace-la par « {CANONICAL_REPO} » / « {CANONICAL_IMAGE} », ou ajoute une entrée JUSTIFIÉE "
        f"à HERITAGE_ALLOWLIST si c'est une mention d'attribution ou d'origine."
    )


def test_the_allowlist_has_no_stale_entry():
    """Une entrée qui ne correspond plus à rien doit disparaître, sinon l'allowlist se périme en silence
    et finirait par autoriser un fichier réécrit entre-temps."""
    mentioning = _files_mentioning_legacy_namespace()
    stale = sorted(set(HERITAGE_ALLOWLIST) - set(mentioning))
    assert not stale, f"Entrées d'allowlist devenues inutiles (le fichier ne mentionne plus rien) : {stale}"


def test_every_allowlist_entry_carries_a_justification():
    for name, reason in HERITAGE_ALLOWLIST.items():
        assert len(reason) > 30, f"Justification trop vague pour {name}"


# -- publication Docker : la cible doit appartenir à ce dépôt ------------------------------------------
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "docker-ghcr.yml"


def test_no_workflow_mentions_the_legacy_namespace():
    """Un workflow est par nature opérationnel : aucune exception possible ici."""
    for workflow in WORKFLOWS:
        assert LEGACY_NAMESPACE not in workflow.read_text(encoding="utf-8"), workflow.name


def test_publish_workflow_targets_the_canonical_lowercase_image():
    """Cause exacte de l'échec corrigé : la cible appartenait à un autre namespace, donc
    « denied: permission_denied: The requested installation does not exist » à chaque push sur main."""
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    image = workflow["env"]["IMAGE_NAME"]
    assert image == CANONICAL_IMAGE, image
    assert image == image.lower(), "GHCR refuse les majuscules dans un nom d'image"
    assert image.startswith("ghcr.io/rec82/"), "la cible doit appartenir à l'owner de ce dépôt"


def test_publish_workflow_keeps_the_permissions_needed_to_push_a_package():
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read", "packages": "write"}


def test_publish_workflow_builds_the_lody_image_not_the_engine_one():
    """Le nom publié doit correspondre à l'artefact publié : `Dockerfile.lody` (interface Lody), jamais
    `./Dockerfile` (ancienne image du moteur), sinon l'image serait mal étiquetée."""
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(PUBLISH_WORKFLOW.read_text(encoding="utf-8"))
    build = next(step for step in workflow["jobs"]["publish"]["steps"]
                 if str(step.get("uses", "")).startswith("docker/build-push-action"))
    assert build["with"]["file"] == "./Dockerfile.lody"


def test_the_engine_service_is_still_built_locally_and_never_renamed():
    """Le moteur `moneyprinterturbo-api` est un service DISTINCT : ce nettoyage ne doit pas l'avoir
    renommé ni fait basculer sur une image Lody."""
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((ROOT / "docker-compose.engine-local.yml").read_text(encoding="utf-8"))
    services = list(compose["services"].values())
    assert "moneyprinterturbo-api" in {service.get("container_name") for service in services}
    for service in services:
        image = str(service.get("image", ""))
        assert image.startswith("moneyprinterturbo:"), image  # construite localement, jamais tirée de GHCR
        assert "lody" not in image.lower(), image
