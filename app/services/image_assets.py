"""Transfert d'une image de référence déjà validée, pour la continuité visuelle d'un personnage (#92).

Même principe que ``app/services/audio_assets.py`` (#86) : upload → référence opaque → consommation par
un appel ``/videos`` ultérieur qui fournit EXACTEMENT le même ``production_scope``. Différence délibérée
de cycle de vie : une référence VISUELLE doit pouvoir être utilisée PLUSIEURS FOIS au sein d'une même
tâche (une fois par scène où le personnage apparaît), alors qu'un asset audio (#86) n'est consommé qu'une
seule fois pour toute la tâche — ``resolve_image_asset`` ne marque donc JAMAIS l'asset comme consommé,
seul un TTL depuis la création le fait expirer (``UNRESOLVED_TTL_SECONDS``, assez long pour couvrir une
génération d'images multi-scènes complète).

La source de vérité STABLE (la référence canonique d'un personnage, réutilisée entre productions) vit côté
Lody (``lody.reference_images``, #38) : cet upload est une retransmission À CHAQUE production qui utilise
ce personnage, jamais une régénération — les octets envoyés sont strictement les mêmes à chaque fois.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from loguru import logger
from PIL import Image, UnidentifiedImageError

from app.utils import utils

# PNG/JPEG/WEBP : les formats déjà acceptés par gpt-image (voir la documentation OpenAI images/edits) et
# déjà ceux que lody.reference_images (#38) valide côté Lody — jamais une liste inventée séparément.
SUPPORTED_IMAGE_ASSET_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
_IMAGE_FORMATS_BY_EXTENSION = {
    ".png": frozenset({"PNG"}), ".jpg": frozenset({"JPEG"}), ".jpeg": frozenset({"JPEG"}),
    ".webp": frozenset({"WEBP"}),
}

# 25 Mo : marge confortable sous la limite documentée de l'API images/edits (actuellement 50 Mo), jamais
# une image de référence de personnage n'approche cette taille en pratique.
MAX_IMAGE_ASSET_UPLOAD_BYTES = 25 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_INTERNAL_UPLOAD_PREFIX = ".image-asset-upload-"

MIN_PRODUCTION_SCOPE_LENGTH = 16
MAX_PRODUCTION_SCOPE_LENGTH = 256

# Généreux : couvre la génération d'images de TOUTES les scènes d'une production (observé jusqu'à
# plusieurs minutes par image en conditions réelles, voir le diagnostic #92), pas une seule consommation.
UNRESOLVED_TTL_SECONDS = 60 * 60
_CLEANUP_INTERVAL_SECONDS = 5 * 60

_ASSET_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_METADATA_SUFFIX = ".meta.json"
_METADATA_FILE_PATTERN = re.compile(r"^([0-9a-f]{32})\.meta\.json$")
_ASSET_FILE_PATTERN = re.compile(
    r"^[0-9a-f]{32}(" + "|".join(re.escape(extension) for extension in SUPPORTED_IMAGE_ASSET_EXTENSIONS) + r")$"
)


class ImageAssetError(ValueError):
    """L'upload ou la référence ne satisfait pas les exigences de sécurité ou de format — réponse 400."""


class ImageAssetServiceError(RuntimeError):
    """Panne d'infrastructure (système de fichiers) — réponse 500, jamais confondue avec une erreur cliente."""


@dataclass
class _AssetRecord:
    file_path: str
    production_scope: str
    created_at: float


_assets: dict[str, _AssetRecord] = {}
_registry_lock = threading.Lock()
_last_cleanup_monotonic: float | None = None


def uploaded_image_asset_dir(create: bool = True) -> str:
    """Dossier dédié, hors de ``storage/tasks`` et de ``storage/local_videos`` — jamais servi par
    ``/stream``/``/download``, cycle de vie et autorisation propres à ce mécanisme."""
    return utils.storage_dir("image_assets", create=create)


def _remove_file_quietly(file_path: str) -> None:
    if not file_path:
        return
    try:
        os.remove(file_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(f"failed to remove image asset file: path={file_path}, error={str(exc)}")


def _validate_production_scope(production_scope: str) -> str:
    if not isinstance(production_scope, str):
        raise ImageAssetError("production_scope must be a string")
    value = production_scope.strip()
    if len(value) < MIN_PRODUCTION_SCOPE_LENGTH:
        raise ImageAssetError("production_scope is too short to be a valid authorization token")
    if len(value) > MAX_PRODUCTION_SCOPE_LENGTH:
        raise ImageAssetError("production_scope is too long")
    if any(ord(character) < 32 for character in value):
        raise ImageAssetError("production_scope contains invalid characters")
    return value


def _guess_extension(filename: str) -> str:
    extension = Path((filename or "").replace("\\", "/").split("/")[-1]).suffix.lower()
    if extension not in SUPPORTED_IMAGE_ASSET_EXTENSIONS:
        supported = ", ".join(ext.removeprefix(".").upper() for ext in SUPPORTED_IMAGE_ASSET_EXTENSIONS)
        raise ImageAssetError(f"unsupported image asset format; supported formats: {supported}")
    return extension


def _stage_upload(filename: str, source: BinaryIO) -> tuple[str, str, int]:
    """Même stratégie que ``audio_assets._stage_upload`` : écrit par blocs, taille bornée, fichier
    temporaire du MÊME dossier que le stockage final pour un ``os.replace`` atomique."""
    extension = _guess_extension(filename)
    try:
        target_dir = uploaded_image_asset_dir(create=True)
    except OSError as exc:
        raise ImageAssetServiceError("failed to prepare image asset storage") from exc

    temp_path = ""
    total_bytes = 0
    try:
        try:
            source.seek(0)
        except (AttributeError, OSError) as exc:
            raise ImageAssetError("image asset upload is not seekable") from exc
        descriptor, temp_path = tempfile.mkstemp(prefix=_INTERNAL_UPLOAD_PREFIX, suffix=extension, dir=target_dir)
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = source.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise ImageAssetError("image asset upload must be binary")
                total_bytes += len(chunk)
                if total_bytes > MAX_IMAGE_ASSET_UPLOAD_BYTES:
                    raise ImageAssetError("image asset file exceeds the 25 MB limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total_bytes == 0:
            raise ImageAssetError("image asset file is empty")
        return extension, temp_path, total_bytes
    except Exception as exc:
        _remove_file_quietly(temp_path)
        if isinstance(exc, ImageAssetError):
            raise
        if isinstance(exc, OSError):
            raise ImageAssetServiceError("failed to stage image asset upload") from exc
        raise


def _validate_image_file(path: str, extension: str) -> None:
    """Décodage RÉEL (jamais seulement l'extension déclarée), comme ``material_upload``'s image checks :
    un fichier renommé ou corrompu échoue ici, même s'il a passé la simple vérification d'extension."""
    allowed_formats = _IMAGE_FORMATS_BY_EXTENSION[extension]
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as probe:
            actual_format = (probe.format or "").upper()
            width, height = probe.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageAssetError("image asset file is not a valid, decodable image") from exc
    if actual_format not in allowed_formats:
        raise ImageAssetError(f"image content does not match its {extension} extension")
    if width < 64 or height < 64:
        raise ImageAssetError("image asset is too small to be a usable reference")


def _metadata_path(target_dir: str, asset_id: str) -> str:
    return os.path.join(target_dir, f"{asset_id}{_METADATA_SUFFIX}")


def _write_metadata(target_dir: str, asset_id: str, record: _AssetRecord, extension: str) -> None:
    payload = {"production_scope": record.production_scope, "created_at": record.created_at, "extension": extension}
    descriptor, temp_path = tempfile.mkstemp(prefix=_INTERNAL_UPLOAD_PREFIX, suffix=".json", dir=target_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(payload, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, _metadata_path(target_dir, asset_id))
    except OSError:
        _remove_file_quietly(temp_path)
        raise


def _read_metadata(target_dir: str, asset_id: str) -> _AssetRecord | None:
    try:
        with open(_metadata_path(target_dir, asset_id), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    try:
        extension = str(payload["extension"])
        if extension not in SUPPORTED_IMAGE_ASSET_EXTENSIONS:
            return None
        image_path = os.path.join(target_dir, f"{asset_id}{extension}")
        if not os.path.isfile(image_path):
            return None
        return _AssetRecord(file_path=image_path, production_scope=str(payload["production_scope"]),
                            created_at=float(payload["created_at"]))
    except (KeyError, TypeError, ValueError):
        return None


def cleanup(now: float | None = None, force: bool = False) -> int:
    """TTL seul (pas de notion de consommation, voir le docstring du module) : un asset expire
    ``UNRESOLVED_TTL_SECONDS`` après sa création, quel que soit le nombre de fois où il a été résolu."""
    global _last_cleanup_monotonic
    monotonic_now = time.monotonic()
    with _registry_lock:
        if not force and _last_cleanup_monotonic is not None and \
           monotonic_now - _last_cleanup_monotonic < _CLEANUP_INTERVAL_SECONDS:
            return 0
        _last_cleanup_monotonic = monotonic_now

        current_time = time.time() if now is None else now
        target_dir = uploaded_image_asset_dir(create=False)
        if not os.path.isdir(target_dir):
            return 0
        try:
            with os.scandir(target_dir) as entries:
                names = [entry.name for entry in entries]
        except OSError as exc:
            logger.warning(f"failed to scan image asset storage: error={str(exc)}")
            return 0

        removed = 0
        seen_asset_ids: set[str] = set()
        for name in names:
            match = _METADATA_FILE_PATTERN.fullmatch(name)
            if not match:
                continue
            asset_id = match.group(1)
            seen_asset_ids.add(asset_id)
            record = _read_metadata(target_dir, asset_id)
            meta_path = os.path.join(target_dir, name)
            if record is None or current_time - record.created_at >= UNRESOLVED_TTL_SECONDS:
                _remove_file_quietly(record.file_path if record else "")
                _remove_file_quietly(meta_path)
                _assets.pop(asset_id, None)
                removed += 1

        for name in names:
            if not _ASSET_FILE_PATTERN.fullmatch(name) or name[:32] in seen_asset_ids:
                continue
            path = os.path.join(target_dir, name)
            try:
                age = current_time - os.stat(path).st_mtime
            except OSError:
                continue
            if age >= UNRESOLVED_TTL_SECONDS:
                _remove_file_quietly(path)
                _assets.pop(name[:32], None)
                removed += 1
        return removed


def save_image_asset_upload(filename: str, source: BinaryIO, production_scope: str) -> str:
    """Valide, décode réellement et stocke une image de référence sous un nom opaque.

    Lève ``ImageAssetError`` (entrée cliente invalide) ou ``ImageAssetServiceError`` (panne serveur).
    """
    scope = _validate_production_scope(production_scope)
    extension, temp_path, total_bytes = _stage_upload(filename, source)
    target_dir = os.path.dirname(temp_path)
    asset_id = uuid4().hex
    target_path = os.path.join(target_dir, f"{asset_id}{extension}")
    record = _AssetRecord(file_path=target_path, production_scope=scope, created_at=time.time())
    try:
        _validate_image_file(temp_path, extension)
        os.replace(temp_path, target_path)
        _write_metadata(target_dir, asset_id, record, extension)
    except ImageAssetError:
        _remove_file_quietly(temp_path)
        raise
    except OSError as exc:
        _remove_file_quietly(temp_path)
        _remove_file_quietly(target_path)
        _remove_file_quietly(_metadata_path(target_dir, asset_id))
        raise ImageAssetServiceError("failed to persist image asset upload") from exc

    with _registry_lock:
        _assets[asset_id] = record
    logger.debug(f"image asset stored: asset_id={asset_id}, size={total_bytes} bytes")
    cleanup()
    return asset_id


def resolve_image_asset(asset_id: str, production_scope: str) -> str:
    """Résout une référence d'asset — RÉSOLUBLE PLUSIEURS FOIS (jamais marquée consommée, voir le
    docstring du module), tant que le TTL n'est pas expiré et que ``production_scope`` correspond
    (comparaison à temps constant, ce champ joue le rôle d'un secret d'autorisation).

    Lève ``ImageAssetError`` avec un message TOUJOURS identique, que l'asset soit inconnu, expiré, ou lié
    à un ``production_scope`` différent — jamais de distinction qui laisserait deviner lequel.
    """
    cleanup()
    generic_error = ImageAssetError("unknown or expired image asset reference")
    if not isinstance(asset_id, str) or not _ASSET_ID_PATTERN.fullmatch(asset_id):
        raise generic_error
    scope = str(production_scope or "")
    target_dir = uploaded_image_asset_dir(create=False)
    with _registry_lock:
        record = _assets.get(asset_id)
        if record is None:
            record = _read_metadata(target_dir, asset_id)
            if record is not None:
                _assets[asset_id] = record
        if record is None:
            raise generic_error
        if not secrets.compare_digest(record.production_scope.encode("utf-8"), scope.encode("utf-8")):
            raise generic_error
        return record.file_path
