"""Catalogue d'assets d'animation 2D réutilisables et versionnés (#119).

Un **asset** est un élément graphique réutilisable d'un projet : la marionnette d'un personnage
(``character``), un décor (``background``), un accessoire (``prop``) ou une surcouche (``overlay``).
Chaque asset a des **versions immuables** (1, 2, 3…) : une version publiée n'est jamais modifiée (refus
applicatif ET déclencheur SQLite), on en ajoute une nouvelle. Une production fige la version qu'elle
utilise (``lody.animation.freeze``) : retoucher la marionnette d'un personnage ne modifie jamais une vidéo
déjà produite.

Lien avec l'existant : ``characters.py`` et ``locations.py`` ne sont PAS transformés en stockage de
calques. Un asset ``character`` peut simplement être relié à un personnage du même projet
(``character_id``), un asset ``background`` à un lieu (``location_id``). Ces liens sont des TEXT nus,
vérifiés à l'écriture, sans clé étrangère (même raison que ``productions.template_id`` : la
reproductibilité repose sur la version figée, pas sur la ligne liée).

Le **manifeste** d'une version décrit ce que le moteur peut animer : dimensions, point d'ancrage,
parties de la marionnette, expressions et gestes disponibles (les NOMS utilisés par le storyboard), et
les fichiers (chemins RELATIFS au dossier de données, avec empreinte). Les octets ne sont jamais en SQLite ;
le téléversement des fichiers et leur transfert entre instances viendront avec l'interface (#120).

Portée : par projet, comme les personnages, les lieux et les templates.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import db
from lody.animation.contract import (
    ASSET_KINDS,
    LATEST,
    SHA256,
    SLUG,
    canonical_json,
)
from lody.secrets_guard import SECRET_MESSAGE, find_secret_path

logger = logging.getLogger("lody.animation.assets")

CATALOG_SCHEMA = "lody.animation-assets/1"
NAME_MIN, NAME_MAX = 2, 80
DESCRIPTION_MAX = 500
NOTES_MAX = 500
FORMATS = ("svg", "png")
MAX_FILES = 64
MAX_NAMES = 64
FILE_EXTENSIONS = (".svg", ".png", ".webp", ".json")
_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)*$")
EDITABLE_FIELDS = ("name", "description", "character_id", "location_id", "is_active")


class AssetError(Exception):
    """Erreur de base du catalogue."""


class AssetValidationError(AssetError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


class AssetNotFound(AssetError):
    pass


@dataclass
class AnimationAsset:
    id: str
    project_id: str
    name: str
    kind: str
    description: str = ""
    character_id: str | None = None
    location_id: str | None = None
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""


@dataclass
class AssetVersion:
    asset_id: str
    version: int
    manifest: dict[str, Any] = field(default_factory=dict)
    sha256: str = ""
    notes: str = ""
    created_at: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def manifest_sha256(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


def validate_manifest(raw: Any) -> dict[str, Any]:
    """Valide le manifeste d'une version. Lève ``AssetValidationError`` (chemins ``manifest.…``)."""
    errors: dict[str, str] = {}
    if not isinstance(raw, dict):
        raise AssetValidationError({"manifest": "objet JSON attendu."})
    allowed = {"format", "width", "height", "anchor", "parts", "expressions", "gestures", "files"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        errors["manifest"] = "clé(s) inconnue(s) : " + ", ".join(map(str, unknown)) + "."
    missing = [key for key in ("format", "width", "height", "files") if key not in raw]
    if missing:
        errors.setdefault("manifest", "clé(s) obligatoire(s) absente(s) : " + ", ".join(missing) + ".")
    if errors:
        raise AssetValidationError(errors)

    if raw["format"] not in FORMATS:
        errors["manifest.format"] = "format : " + ", ".join(FORMATS) + "."
    for key in ("width", "height"):
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 8192:
            errors[f"manifest.{key}"] = "entier de 1 à 8192 attendu."
    if "anchor" in raw:
        anchor = raw["anchor"]
        if not isinstance(anchor, dict) or set(anchor) != {"x", "y"} or any(
                isinstance(anchor[k], bool) or not isinstance(anchor[k], (int, float)) or not 0 <= anchor[k] <= 1
                for k in ("x", "y")):
            errors["manifest.anchor"] = "point d'ancrage {x, y} attendu, en fraction de la taille (0 à 1)."
    for key in ("parts", "expressions", "gestures"):
        if key not in raw:
            continue
        names = raw[key]
        if not isinstance(names, list) or len(names) > MAX_NAMES or not all(
                isinstance(name, str) and SLUG.match(name) for name in names):
            errors[f"manifest.{key}"] = f"liste d'identifiants attendue ({MAX_NAMES} au plus)."
        elif len(set(names)) != len(names):
            errors[f"manifest.{key}"] = "identifiant en double."

    files = raw["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        errors["manifest.files"] = f"entre 1 et {MAX_FILES} fichier(s) attendu(s)."
    else:
        roles: set[str] = set()
        for index, item in enumerate(files):
            path = f"manifest.files[{index}]"
            if not isinstance(item, dict) or set(item) != {"role", "path", "sha256"}:
                errors[path] = "objet {role, path, sha256} attendu."
                continue
            if not isinstance(item["role"], str) or not SLUG.match(item["role"]):
                errors[f"{path}.role"] = "identifiant attendu (ex. base, head, mouth-open)."
            elif item["role"] in roles:
                errors[f"{path}.role"] = "rôle en double."
            else:
                roles.add(item["role"])
            value = item["path"]
            # Chemin RELATIF au dossier de données, sans remontée ni schéma d'URL : jamais une ressource distante.
            if (not isinstance(value, str) or len(value) > 200 or not _PATH.match(value)
                    or ".." in value.split("/") or not value.lower().endswith(FILE_EXTENSIONS)):
                errors[f"{path}.path"] = ("chemin relatif attendu (lettres, chiffres, . _ - /), extension "
                                          + ", ".join(FILE_EXTENSIONS) + ".")
            if not isinstance(item["sha256"], str) or not SHA256.match(item["sha256"]):
                errors[f"{path}.sha256"] = "empreinte SHA-256 hexadécimale (minuscules) attendue."
    if errors:
        raise AssetValidationError(errors)
    # Motifs de clés connus seulement : les empreintes SHA-256 des fichiers sont des jetons opaques légitimes.
    if leaked := find_secret_path(raw, "manifest", opaque=False):
        raise AssetValidationError({leaked: SECRET_MESSAGE})
    return copy.deepcopy(raw)


def _clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    errors: dict[str, str] = {}
    clean: dict[str, Any] = {}
    name = " ".join(str(fields.get("name") or "").split())
    if not NAME_MIN <= len(name) <= NAME_MAX:
        errors["name"] = f"Le nom doit faire entre {NAME_MIN} et {NAME_MAX} caractères."
    clean["name"] = name
    description = str(fields.get("description") or "").strip()
    if len(description) > DESCRIPTION_MAX:
        errors["description"] = f"La description est trop longue ({DESCRIPTION_MAX} caractères maximum)."
    clean["description"] = description
    clean["character_id"] = fields.get("character_id") or None
    clean["location_id"] = fields.get("location_id") or None
    clean["is_active"] = bool(fields.get("is_active", True))
    for key in ("name", "description"):
        if key not in errors and find_secret_path(clean[key]):
            errors[key] = SECRET_MESSAGE
    if errors:
        raise AssetValidationError(errors)
    return clean


class AnimationAssetRepository:
    """Accès SQLite au catalogue. Une connexion courte par opération, comme les autres dépôts Lody."""

    def __init__(self, db_path: str | Path, clock: Callable[[], str] = _utc_now):
        self.db_path = Path(db_path)
        self._clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            db.migrate(connection)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _asset(row: sqlite3.Row) -> AnimationAsset:
        data = dict(row)
        data["is_active"] = bool(data["is_active"])
        return AnimationAsset(**data)

    @staticmethod
    def _version(row: sqlite3.Row) -> AssetVersion:
        data = dict(row)
        data["manifest"] = json.loads(data["manifest"] or "{}")
        return AssetVersion(**data)

    # -- contrôles ------------------------------------------------------------------------------------
    @staticmethod
    def _check_links(connection: sqlite3.Connection, project_id: str, kind: str, clean: dict[str, Any]) -> None:
        errors: dict[str, str] = {}
        for key, table, expected_kind, label in (("character_id", "characters", "character", "personnage"),
                                                 ("location_id", "locations", "background", "lieu")):
            linked = clean[key]
            if linked is None:
                continue
            if kind != expected_kind:
                errors[key] = f"Seul un asset « {expected_kind} » peut être relié à un {label}."
            elif connection.execute(f"SELECT 1 FROM {table} WHERE id = ? AND project_id = ?",
                                    (linked, project_id)).fetchone() is None:
                errors[key] = f"Ce {label} n'existe pas dans ce projet."
        if errors:
            raise AssetValidationError(errors)

    @staticmethod
    def _check_name_unique(connection: sqlite3.Connection, project_id: str, name: str, exclude_id: str | None) -> None:
        wanted = name.casefold()
        for row in connection.execute("SELECT id, name FROM animation_assets WHERE project_id = ?", (project_id,)):
            if row["id"] != exclude_id and row["name"].casefold() == wanted:
                raise AssetValidationError({"name": "Un asset de ce projet porte déjà ce nom."})

    # -- lecture --------------------------------------------------------------------------------------
    def get(self, project_id: str, asset_id: str) -> AnimationAsset:
        """``AssetNotFound`` si l'asset n'existe pas OU appartient à un autre projet (même refus)."""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM animation_assets WHERE id = ? AND project_id = ?",
                                     (asset_id, project_id)).fetchone()
        if row is None:
            raise AssetNotFound(asset_id)
        return self._asset(row)

    def list_for_project(self, project_id: str, *, include_inactive: bool = True,
                         kind: str | None = None) -> list[AnimationAsset]:
        query, params = "SELECT * FROM animation_assets WHERE project_id = ?", [project_id]
        if not include_inactive:
            query += " AND is_active = 1"
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY is_active DESC, name COLLATE NOCASE"
        with self._connect() as connection:
            return [self._asset(row) for row in connection.execute(query, params)]

    def for_character(self, project_id: str, character_id: str) -> list[AnimationAsset]:
        return [asset for asset in self.list_for_project(project_id) if asset.character_id == character_id]

    def for_location(self, project_id: str, location_id: str) -> list[AnimationAsset]:
        return [asset for asset in self.list_for_project(project_id) if asset.location_id == location_id]

    def list_versions(self, project_id: str, asset_id: str) -> list[AssetVersion]:
        self.get(project_id, asset_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM animation_asset_versions WHERE asset_id = ? ORDER BY version", (asset_id,)).fetchall()
        return [self._version(row) for row in rows]

    def get_version(self, project_id: str, asset_id: str, version: int | str = LATEST) -> AssetVersion:
        """Version explicite, ou la plus récente (``"latest"``). ``AssetNotFound`` si absente."""
        self.get(project_id, asset_id)
        with self._connect() as connection:
            if version == LATEST:
                row = connection.execute("SELECT * FROM animation_asset_versions WHERE asset_id = ? "
                                         "ORDER BY version DESC LIMIT 1", (asset_id,)).fetchone()
            else:
                row = connection.execute("SELECT * FROM animation_asset_versions WHERE asset_id = ? AND version = ?",
                                         (asset_id, version)).fetchone()
        if row is None:
            raise AssetNotFound(f"{asset_id}@{version}")
        return self._version(row)

    # -- écriture -------------------------------------------------------------------------------------
    def create(self, project_id: str, *, kind: str, **fields: Any) -> AnimationAsset:
        if kind not in ASSET_KINDS:
            raise AssetValidationError({"kind": "Type d'asset : " + ", ".join(ASSET_KINDS) + "."})
        unknown = set(fields) - set(EDITABLE_FIELDS)
        if unknown:
            raise AssetValidationError({"_": "Champ inconnu : " + ", ".join(sorted(unknown))})
        clean = _clean_fields(fields)
        asset_id, now = f"ast_{uuid.uuid4().hex[:12]}", self._clock()
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise AssetValidationError({"project_id": "Ce projet n’existe pas."})
            self._check_name_unique(connection, project_id, clean["name"], None)
            self._check_links(connection, project_id, kind, clean)
            connection.execute(
                "INSERT INTO animation_assets (id, project_id, name, kind, description, character_id, location_id, "
                "is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (asset_id, project_id, clean["name"], kind, clean["description"], clean["character_id"],
                 clean["location_id"], int(clean["is_active"]), now, now))
        logger.info("asset d'animation créé : %s (%s, projet %s)", asset_id, kind, project_id)
        return self.get(project_id, asset_id)

    def update(self, project_id: str, asset_id: str, **fields: Any) -> AnimationAsset:
        """Modifie la fiche (nom, description, liens, activité). Le type et les versions ne changent jamais."""
        unknown = set(fields) - set(EDITABLE_FIELDS)
        if unknown:
            raise AssetValidationError({"_": "Champ non modifiable : " + ", ".join(sorted(unknown))})
        current = self.get(project_id, asset_id)
        clean = _clean_fields({**{key: getattr(current, key) for key in EDITABLE_FIELDS}, **fields})
        with self._connect() as connection:
            self._check_name_unique(connection, project_id, clean["name"], asset_id)
            self._check_links(connection, project_id, current.kind, clean)
            connection.execute(
                "UPDATE animation_assets SET name = ?, description = ?, character_id = ?, location_id = ?, "
                "is_active = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                (clean["name"], clean["description"], clean["character_id"], clean["location_id"],
                 int(clean["is_active"]), self._clock(), asset_id, project_id))
        return self.get(project_id, asset_id)

    def deactivate(self, project_id: str, asset_id: str) -> AnimationAsset:
        """Retire l'asset des choix proposés SANS rien supprimer : les storyboards figés restent valides."""
        return self.update(project_id, asset_id, is_active=False)

    def restore(self, project_id: str, asset_id: str) -> AnimationAsset:
        return self.update(project_id, asset_id, is_active=True)

    def add_version(self, project_id: str, asset_id: str, manifest: Any, *, notes: str = "",
                    _version: int | None = None) -> AssetVersion:
        """Publie une NOUVELLE version (numéro suivant). Les versions existantes ne sont jamais modifiées."""
        clean = validate_manifest(manifest)
        notes = str(notes or "").strip()
        if len(notes) > NOTES_MAX:
            raise AssetValidationError({"notes": f"Notes trop longues ({NOTES_MAX} caractères maximum)."})
        if find_secret_path(notes):
            raise AssetValidationError({"notes": SECRET_MESSAGE})
        self.get(project_id, asset_id)
        with self._connect() as connection:
            version = _version or connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM animation_asset_versions WHERE asset_id = ?",
                (asset_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO animation_asset_versions (asset_id, version, manifest, sha256, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (asset_id, version, json.dumps(clean, ensure_ascii=False), manifest_sha256(clean), notes,
                 self._clock()))
            connection.execute("UPDATE animation_assets SET updated_at = ? WHERE id = ?", (self._clock(), asset_id))
        logger.info("asset %s : version %s publiée", asset_id, version)
        return self.get_version(project_id, asset_id, version)

    # -- export / import ------------------------------------------------------------------------------
    def export_catalog(self, project_id: str) -> dict[str, Any]:
        """Catalogue complet d'un projet, versions comprises. Les liens sont exportés par NOM de personnage
        ou de lieu (les identifiants changent d'une instance à l'autre)."""
        with self._connect() as connection:
            characters = {row["id"]: row["name"] for row in connection.execute(
                "SELECT id, name FROM characters WHERE project_id = ?", (project_id,))}
            locations = {row["id"]: row["name"] for row in connection.execute(
                "SELECT id, name FROM locations WHERE project_id = ?", (project_id,))}
        items = []
        for asset in self.list_for_project(project_id):
            items.append({
                "id": asset.id, "name": asset.name, "kind": asset.kind, "description": asset.description,
                "is_active": asset.is_active,
                "character": characters.get(asset.character_id) if asset.character_id else None,
                "location": locations.get(asset.location_id) if asset.location_id else None,
                "versions": [{"version": v.version, "manifest": v.manifest, "sha256": v.sha256, "notes": v.notes}
                             for v in self.list_versions(project_id, asset.id)],
            })
        return {"schema": CATALOG_SCHEMA, "assets": items}

    def import_catalog(self, project_id: str, data: Any) -> tuple[dict[str, str], list[str]]:
        """Importe un catalogue exporté dans ``project_id``. Renvoie ({ancien id: nouvel id}, avertissements).

        Les numéros de version et les manifestes sont conservés à l'identique (mêmes empreintes) : un
        storyboard remappé (``freeze.remap_assets``) puis refigé désigne exactement le même contenu. Une
        empreinte qui ne correspond pas à son manifeste est refusée (fichier altéré). Un lien vers un
        personnage/lieu absent du projet cible est abandonné, avec un avertissement."""
        if not isinstance(data, dict) or data.get("schema") != CATALOG_SCHEMA or not isinstance(data.get("assets"), list):
            raise AssetValidationError({"schema": f"Catalogue « {CATALOG_SCHEMA} » attendu."})
        errors: dict[str, str] = {}
        for index, item in enumerate(data["assets"]):
            path = f"assets[{index}]"
            if not isinstance(item, dict) or not isinstance(item.get("versions"), list) or not item["versions"]:
                errors[path] = "asset invalide (versions absentes)."
                continue
            if item.get("kind") not in ASSET_KINDS:
                errors[f"{path}.kind"] = "Type d'asset : " + ", ".join(ASSET_KINDS) + "."
            expected = 1
            for vi, version in enumerate(item["versions"]):
                vpath = f"{path}.versions[{vi}]"
                if not isinstance(version, dict) or version.get("version") != expected:
                    errors[vpath] = "versions attendues consécutives à partir de 1."
                    break
                try:
                    clean = validate_manifest(version.get("manifest"))
                except AssetValidationError as error:
                    errors[vpath] = "; ".join(error.errors.values())
                    break
                if version.get("sha256") != manifest_sha256(clean):
                    errors[f"{vpath}.sha256"] = "empreinte différente du manifeste (fichier altéré)."
                expected += 1
        if errors:
            raise AssetValidationError(errors)

        with self._connect() as connection:
            characters = {row["name"].casefold(): row["id"] for row in connection.execute(
                "SELECT id, name FROM characters WHERE project_id = ?", (project_id,))}
            locations = {row["name"].casefold(): row["id"] for row in connection.execute(
                "SELECT id, name FROM locations WHERE project_id = ?", (project_id,))}
        existing = {asset.name.casefold() for asset in self.list_for_project(project_id)}
        id_map: dict[str, str] = {}
        warnings: list[str] = []
        for item in data["assets"]:
            name, suffix = str(item.get("name") or ""), 2
            candidate = name
            while candidate.casefold() in existing:
                candidate = f"{name} ({suffix})"[:NAME_MAX]
                suffix += 1
            existing.add(candidate.casefold())
            links: dict[str, Any] = {}
            for key, label, names in (("character", "personnage", characters), ("location", "lieu", locations)):
                wanted = item.get(key)
                if wanted:
                    if str(wanted).casefold() in names:
                        links[f"{key}_id"] = names[str(wanted).casefold()]
                    else:
                        warnings.append(f"« {candidate} » : {label} « {wanted} » absent du projet, lien non repris.")
            asset = self.create(project_id, kind=item.get("kind", ""), name=candidate,
                                description=item.get("description", ""), is_active=item.get("is_active", True), **links)
            for version in item["versions"]:
                self.add_version(project_id, asset.id, version["manifest"], notes=version.get("notes", ""),
                                 _version=version["version"])
            if isinstance(item.get("id"), str):
                id_map[item["id"]] = asset.id
        return id_map, warnings

