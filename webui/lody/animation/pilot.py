"""Atelier du pilote 2D (#154) : importer un catalogue avec ses fichiers, adopter un storyboard figé ailleurs,
installer la démonstration Voltix #150 dans un projet de DEV.

- ``import_catalog_with_files`` : import ``lody.animation-catalog`` (#119) ; les fichiers des manifestes absents
  du dossier de données sont recopiés depuis les ressources livrées avec l'application
  (``resource/animation-assets``), **uniquement si leur empreinte correspond** au manifeste.
- ``adopt_storyboard`` : un storyboard figé dans un autre projet (ex. la démo #150) désigne des identifiants
  d'assets étrangers ; on les remplace par les assets de CE projet qui ont exactement le même contenu
  (empreinte de version identique). Rien n'est deviné d'après un nom.
- ``install_voltix_demo`` : catalogue Voltix (#136) + schéma coaxial (#150) + storyboard de la démo #150,
  prêt à être préparé dans le projet. Aucun appel payant ; la démo est muette tant que la voix n'est pas
  branchée dans le fournisseur.
"""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lody.animation import worker as animation_worker
from lody.animation.assets import AnimationAssetRepository, manifest_sha256, validate_manifest
from lody.animation.contract import AnimationValidationError, iter_asset_refs
from lody.generation.safety import resolve_within

REPO_ROOT = Path(__file__).resolve().parents[3]
BUNDLED_ROOT = REPO_ROOT / "resource"
VOLTIX_CATALOG = BUNDLED_ROOT / "animation-assets" / "voltix-catalogue.json"
DEMO_STORYBOARD = REPO_ROOT / "docs" / "animation" / "voltix-150" / "storyboard.json"
COAX_NAME = "Câble coaxial 75 Ω — coupe"
COAX_PATH = "animation-assets/coax-coupe/v1/coax-coupe.svg"


@dataclass
class ImportResult:
    id_map: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    copied: int = 0
    present: int = 0
    missing: list[str] = field(default_factory=list)


def _manifest_files(catalog: Any) -> list[dict[str, Any]]:
    files = []
    for item in (catalog or {}).get("assets") or []:
        for version in (item or {}).get("versions") or []:
            for entry in ((version or {}).get("manifest") or {}).get("files") or []:
                if isinstance(entry, dict):
                    files.append(entry)
    return files


def _safe_target(root: Path, relative: str) -> Path | None:
    """Chemin de ``relative`` sous ``root`` (sans ``..``, sans chemin absolu), même si le fichier n'existe pas."""
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        return None
    parts = relative.split("/")
    if relative.startswith("/") or any(part in ("", ".", "..") for part in parts):
        return None
    target = (root / relative).resolve()
    return target if target.is_relative_to(root.resolve()) else None


def seed_files(catalog: Any, data_root: Path, bundled_root: Path = BUNDLED_ROOT) -> ImportResult:
    """Recopie dans ``data_root`` les fichiers du catalogue fournis avec l'application (empreinte vérifiée)."""
    result = ImportResult()
    for entry in _manifest_files(catalog):
        relative, digest = entry.get("path"), entry.get("sha256")
        target = _safe_target(data_root, relative)
        if target is None:
            result.missing.append(str(relative))
            continue
        if target.is_file() and animation_worker.sha256_file(target) == digest:
            result.present += 1
            continue
        try:
            source = resolve_within(bundled_root, relative)
        except ValueError:
            source = None
        if source is None or animation_worker.sha256_file(source) != digest:
            result.missing.append(relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(target.name + ".tmp")
        shutil.copyfile(source, staging)
        staging.replace(target)
        result.copied += 1
    return result


def import_catalog_with_files(repo: AnimationAssetRepository, project_id: str, catalog: Any, data_root: Path,
                              bundled_root: Path = BUNDLED_ROOT) -> ImportResult:
    """Import du catalogue dans le projet, après mise en place des fichiers. Lève ``AssetValidationError``."""
    result = seed_files(catalog, data_root, bundled_root)
    id_map, warnings = repo.import_catalog(project_id, catalog)
    result.id_map, result.warnings = id_map, warnings
    if result.missing:
        result.warnings.append(f"{len(result.missing)} fichier(s) introuvable(s) ou d'empreinte différente : "
                               + ", ".join(result.missing[:5]) + " — à déposer dans le dossier de données.")
    return result


def _versions_by_digest(repo: AnimationAssetRepository, project_id: str) -> dict[str, tuple[str, int]]:
    found: dict[str, tuple[str, int]] = {}
    for asset in repo.list_for_project(project_id):
        if not asset.is_active:
            continue
        for version in repo.list_versions(project_id, asset.id):
            found.setdefault(version.sha256, (asset.id, version.version))
    return found


def adopt_storyboard(repo: AnimationAssetRepository, project_id: str, document: Any) -> Any:
    """Storyboard figé ailleurs → mêmes assets (par empreinte) dans ce projet. Inchangé si rien à adapter.

    Lève ``AnimationValidationError`` si un asset figé n'a aucun équivalent exact dans le catalogue du projet."""
    if not isinstance(document, dict) or not isinstance(document.get("frozen"), dict):
        return document
    pinned = document["frozen"].get("assets") or []
    local = _versions_by_digest(repo, project_id)
    mapping: dict[tuple[str, int], tuple[str, int]] = {}
    errors: dict[str, str] = {}
    for entry in pinned:
        if not isinstance(entry, dict):
            continue
        key = (entry.get("id"), entry.get("version"))
        if entry.get("sha256") in local:
            mapping[key] = local[entry["sha256"]]
        else:
            errors[f"frozen.assets[{entry.get('id')}]"] = (
                f"« {entry.get('name')} » v{entry.get('version')} absent du catalogue de ce projet "
                "(importe d'abord son catalogue).")
    if errors:
        raise AnimationValidationError(errors)
    if all(source == target for source, target in mapping.items()):
        return document
    adopted = copy.deepcopy(document)
    adopted.pop("frozen", None)
    for path, ref in iter_asset_refs(adopted):
        target = mapping.get((ref.get("id"), ref.get("version")))
        if target is None:
            raise AnimationValidationError({path: "asset absent du bloc « frozen » du storyboard."})
        ref["id"], ref["version"] = target
    return adopted


def coax_manifest(data_root: Path) -> dict[str, Any]:
    """Manifeste du schéma coaxial de la démo #150 (même contenu, donc même empreinte de version)."""
    return validate_manifest({"format": "svg", "width": 900, "height": 300, "anchor": {"x": 0.5, "y": 0.5},
                              "files": [{"role": "base", "path": COAX_PATH,
                                         "sha256": animation_worker.sha256_file(data_root / COAX_PATH)}]})


def install_voltix_demo(repo: AnimationAssetRepository, project_id: str, data_root: Path,
                        bundled_root: Path = BUNDLED_ROOT, storyboard_path: Path = DEMO_STORYBOARD
                        ) -> tuple[dict[str, Any], list[str]]:
    """Installe (une seule fois) les assets de la démo #150 dans le projet ; renvoie (storyboard adopté, notes)."""
    if not storyboard_path.is_file():
        raise FileNotFoundError(f"storyboard de démonstration absent ({storyboard_path.name}).")
    storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
    notes: list[str] = []
    wanted = {entry.get("sha256") for entry in storyboard.get("frozen", {}).get("assets", [])}
    if not wanted <= set(_versions_by_digest(repo, project_id)):
        catalog = json.loads((bundled_root / VOLTIX_CATALOG.relative_to(BUNDLED_ROOT)).read_text(encoding="utf-8"))
        local = set(_versions_by_digest(repo, project_id))
        catalog_digests = {v.get("sha256") for a in catalog["assets"] for v in a["versions"]}
        if not catalog_digests <= local:
            result = import_catalog_with_files(repo, project_id, catalog, data_root, bundled_root)
            notes.append(f"Catalogue Voltix importé : {len(result.id_map)} asset(s), {result.copied} fichier(s) copié(s).")
            notes.extend(result.warnings)
        seed_files({"assets": [{"versions": [{"manifest": {"files": [
            {"path": COAX_PATH, "sha256": animation_worker.sha256_file(bundled_root / COAX_PATH)}]}}]}]},
            data_root, bundled_root)
        manifest = coax_manifest(data_root)
        if manifest_sha256(manifest) not in _versions_by_digest(repo, project_id):
            coax = repo.create(project_id, kind="prop", name=COAX_NAME)
            repo.add_version(project_id, coax.id, manifest, notes="Schéma technique de la démo #150")
            notes.append("Schéma « câble coaxial 75 Ω » ajouté au catalogue.")
    else:
        notes.append("Assets de la démo déjà présents dans le catalogue du projet.")
    notes.append("Démo #150 muette dans Lody : la voix provisoire de la vidéo de référence n'est pas rejouée.")
    return adopt_storyboard(repo, project_id, storyboard), notes
