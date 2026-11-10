"""Gel d'un storyboard animé pour une production (#119).

``freeze_document`` transforme un storyboard de travail (références ``"latest"`` permises) en document
**figé** : chaque référence porte une version explicite, le bloc ``frozen`` liste les versions utilisées
avec l'empreinte de leur manifeste, et l'empreinte du contenu rend toute retouche ultérieure détectable.
C'est ce document qui est enregistré dans ``productions.animation`` (immuable après confirmation, repris
tel quel par une V2) : publier une nouvelle version d'un asset ne change jamais une production existante.

Le gel vérifie aussi ce que le contrat seul ne peut pas savoir : l'asset existe dans CE projet et est
actif, la version existe, chaque expression ou geste est déclaré par cette version, la synchro labiale
vise une marionnette de personnage et le repli d'image fixe vise un décor.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from lody.animation.assets import AnimationAssetRepository, AssetNotFound
from lody.animation.contract import (
    AnimationValidationError,
    document_sha256,
    iter_asset_refs,
    validate_document,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def freeze_document(repo: AnimationAssetRepository, project_id: str, raw: Any, *,
                    clock: Callable[[], str] = _utc_now) -> dict[str, Any]:
    """Renvoie le storyboard figé (validé avec ``require_pinned``). Lève ``AnimationValidationError``."""
    document = validate_document(raw)
    document.pop("frozen", None)  # regel d'un document déjà figé : on repart du contenu
    errors: dict[str, str] = {}
    pinned: dict[tuple[str, int], dict[str, Any]] = {}
    for path, ref in iter_asset_refs(document):
        try:
            asset = repo.get(project_id, ref["id"])
            version = repo.get_version(project_id, ref["id"], ref["version"])
        except AssetNotFound:
            errors[path] = "asset ou version introuvable dans le catalogue de ce projet."
            continue
        if not asset.is_active:
            errors[path] = f"l'asset « {asset.name} » est désactivé."
            continue
        ref["version"] = version.version
        pinned[(asset.id, version.version)] = {"id": asset.id, "version": version.version, "sha256": version.sha256,
                                                "kind": asset.kind, "name": asset.name, "_manifest": version.manifest}
        if path.endswith("fallback.asset") and asset.kind != "background":
            errors[path] = "le repli d'image fixe doit être un décor (asset « background »)."
    if errors:
        raise AnimationValidationError(errors)

    for si, scene in enumerate(document["scenes"]):
        for li, layer in enumerate(scene["render"]["layers"]):
            for oi, obj in enumerate(layer["objects"]):
                if obj["type"] != "asset":
                    continue
                path = f"scenes[{si}].render.layers[{li}].objects[{oi}]"
                entry = pinned[(obj["asset"]["id"], obj["asset"]["version"])]
                manifest = entry["_manifest"]
                for key, label in (("expressions", "expression"), ("gestures", "geste")):
                    declared = set(manifest.get(key) or [])
                    for ei, event in enumerate(obj.get(key) or []):
                        if event["name"] not in declared:
                            errors[f"{path}.{key}[{ei}].name"] = (
                                f"{label} « {event['name']} » non déclaré(e) par « {entry['name']} » "
                                f"v{entry['version']}.")
                if obj.get("lipsync") and entry["kind"] != "character":
                    errors[f"{path}.lipsync"] = "la synchro labiale s'applique à une marionnette de personnage."
    if errors:
        raise AnimationValidationError(errors)

    document["frozen"] = {
        "at": clock(),
        "assets": [{key: value for key, value in entry.items() if key != "_manifest"}
                   for _, entry in sorted(pinned.items())],
        "sha256": document_sha256(document),
    }
    return validate_document(document, require_pinned=True)


def remap_assets(document: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
    """Remplace les identifiants d'assets (après ``import_catalog``) et retire le bloc ``frozen`` : le
    document doit être refigé dans le projet cible, où il désignera les MÊMES versions et manifestes."""
    remapped = copy.deepcopy(document)
    remapped.pop("frozen", None)
    for path, ref in iter_asset_refs(remapped):
        if ref["id"] not in id_map:
            raise AnimationValidationError({path: "asset absent du catalogue importé."})
        ref["id"] = id_map[ref["id"]]
    return remapped
