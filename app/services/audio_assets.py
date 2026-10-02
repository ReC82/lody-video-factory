"""Transfert d'un fichier audio déjà synthétisé, pour une future orchestration multi-locuteurs (#39).

Prérequis #86 : aujourd'hui, le moteur ne sait recevoir qu'**un seul** `voice_name` par vidéo et
synthétise lui-même tout `video_script` (voir `app/services/voice.py:tts`, `app/services/task.py:
generate_audio`). Aucun appel HTTP existant ne permet à un appelant externe (Lody) de faire aboutir une
piste audio déjà assemblée (potentiellement multi-voix) jusqu'au montage final : `custom_audio_file`
(`resolve_custom_audio_file`, `app/services/task.py`) exige, pour tout appelant HTTP
(`allow_server_file_input=False`, volontaire — voir le commentaire à la ligne ~1669 de ce fichier), que
le fichier soit déjà dans le dossier de la tâche vidéo, qui n'existe pas encore avant l'appel — une
frontière de sécurité délibérée, pas un oubli.

Ce module ajoute un mécanisme **séparé et minimal** : upload → référence opaque → consommation unique
par un appel `/videos` ultérieur qui fournit EXACTEMENT le même `production_scope`. Aucun assemblage
multi-segments, aucune génération multi-voix, aucun changement du format du script : c'est le périmètre
de #87 (dépend de celui-ci), pas de #86.

Cycle de vie d'un asset :

    créé (upload validé, fichier stocké sous un nom opaque)
      -> réservé (en attente de consommation, TTL ``UNRESOLVED_TTL_SECONDS``)
      -> consommé (une seule fois, par un ``/videos`` dont le ``production_scope`` correspond)
      -> nettoyé (TTL ``CONSUMED_GRACE_SECONDS`` après consommation, ou ``UNRESOLVED_TTL_SECONDS``
         après création s'il n'a jamais été consommé)

``production_scope`` est un jeton OPAQUE choisi par l'appelant (Lody), traité comme un secret — jamais
le `production_id` lui-même (déjà visible dans l'URL/les journaux côté Lody, donc pas un secret) : voir
``lody.generation.safety.new_audio_asset_scope``. Il lie un upload à EXACTEMENT une consommation : sans
lui (ou avec une valeur différente), la référence est refusée avec un message générique, que l'asset
soit inconnu, expiré, déjà consommé, ou lié à un autre ``production_scope`` — jamais de distinction qui
laisserait deviner lequel de ces cas s'est produit.

Registre en mémoire du PROCESSUS (même limitation, déjà documentée et acceptée, que ``MemoryState`` pour
les tâches : un redémarrage perd le registre). Le nettoyage par motif de nom de fichier (``_ASSET_FILE_
PATTERN``) reste donc une seconde ligne de défense indépendante du registre, pour les fichiers orphelins
qu'un redémarrage aurait rendus invisibles à celui-ci.
"""

from __future__ import annotations

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

from app.services import bgm as bgm_service
from app.utils import utils

# Même liste que les musiques de fond (``bgm.SUPPORTED_BGM_EXTENSIONS``) : ce sont déjà les formats
# audio que le FFmpeg de ce dépôt décode (voir ``bgm._validate_audio``) et que ``AudioFileClip``
# (moviepy, montage final) sait lire — jamais une liste inventée indépendamment du moteur réel.
SUPPORTED_AUDIO_ASSET_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma")

# Un segment de dialogue/narration peut durer plusieurs minutes en qualité studio ; 50 Mo reste très
# généreux (largement au-delà d'une heure de MP3 128 kbps) tout en bornant le risque de déni de service
# par fichier volumineux — même ordre de grandeur que ``bgm.MAX_BGM_UPLOAD_BYTES`` (30 Mo).
MAX_AUDIO_ASSET_UPLOAD_BYTES = 50 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_INTERNAL_UPLOAD_PREFIX = ".audio-asset-upload-"

# Jeton d'autorisation : jamais trivialement court (défense en profondeur même si l'appelant est
# censé envoyer un jeton de haute entropie, voir new_audio_asset_scope côté Lody) ni déraisonnablement
# long (évite qu'un client abuse du champ pour stocker autre chose).
MIN_PRODUCTION_SCOPE_LENGTH = 16
MAX_PRODUCTION_SCOPE_LENGTH = 256

# Durée de vie : généreuse pour couvrir la préparation + confirmation + file d'attente côté Lody, sans
# laisser un asset jamais réclamé occuper du stockage indéfiniment.
UNRESOLVED_TTL_SECONDS = 30 * 60
# Après consommation, le fichier doit encore exister le temps que generate_audio() en lise la durée et
# que le montage le copie/l'utilise ; 10 minutes est largement suffisant pour cette seule lecture locale.
CONSUMED_GRACE_SECONDS = 10 * 60
_CLEANUP_INTERVAL_SECONDS = 5 * 60

_WINDOWS_INVALID_FILENAME_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)

# Nom de fichier final : toujours <uuid4 hex><extension>, jamais dérivé du nom fourni par le client —
# sert aussi de motif pour le nettoyage des orphelins après un redémarrage (registre en mémoire perdu).
_ASSET_FILE_PATTERN = re.compile(
    r"^[0-9a-f]{32}(" + "|".join(re.escape(extension) for extension in SUPPORTED_AUDIO_ASSET_EXTENSIONS) + r")$"
)


class AudioAssetError(ValueError):
    """L'upload ou la référence ne satisfait pas les exigences de sécurité ou de format — réponse 400."""


class AudioAssetServiceError(RuntimeError):
    """Panne d'infrastructure (FFmpeg, système de fichiers) — réponse 500, jamais confondue avec une erreur cliente."""


@dataclass
class _AssetRecord:
    file_path: str
    production_scope: str
    created_at: float
    consumed_at: float | None = None


_assets: dict[str, _AssetRecord] = {}
_registry_lock = threading.Lock()
_last_cleanup_monotonic: float | None = None


def uploaded_audio_asset_dir(create: bool = True) -> str:
    """Dossier dédié, hors de ``storage/tasks`` (jamais servi par ``/stream``/``/download``, qui ne
    résolvent que dans le dossier des tâches — voir ``app/controllers/v1/video.py``) et hors de
    ``storage/bgm`` (séparé des musiques de fond, cycle de vie et autorisation différents)."""
    return utils.storage_dir("audio_assets", create=create)


def _remove_file_quietly(file_path: str) -> None:
    if not file_path:
        return
    try:
        os.remove(file_path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(f"failed to remove audio asset file: path={file_path}, error={str(exc)}")


def sanitize_audio_asset_filename(filename: str) -> str:
    """Même règle que ``bgm.sanitize_upload_filename`` (noms Windows interdits, traversée de chemin,
    extension dans la liste blanche) — jamais utilisé directement comme chemin de stockage : seul le
    nom final ``<uuid4 hex><extension>`` touche le disque (voir ``save_audio_asset_upload``)."""
    safe_name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if (
        not safe_name
        or safe_name in {".", ".."}
        or len(safe_name) > 255
        or any(ord(character) < 32 for character in safe_name)
        or any(character in _WINDOWS_INVALID_FILENAME_CHARS for character in safe_name)
        or safe_name.lower().startswith(_INTERNAL_UPLOAD_PREFIX)
    ):
        raise AudioAssetError("invalid audio asset filename")
    windows_basename = safe_name.split(".", 1)[0].rstrip(" .").upper()
    if windows_basename in _WINDOWS_RESERVED_FILENAMES:
        raise AudioAssetError("invalid audio asset filename")
    if Path(safe_name).suffix.lower() not in SUPPORTED_AUDIO_ASSET_EXTENSIONS:
        supported = ", ".join(ext.removeprefix(".").upper() for ext in SUPPORTED_AUDIO_ASSET_EXTENSIONS)
        raise AudioAssetError(f"unsupported audio asset format; supported formats: {supported}")
    return safe_name


def _validate_production_scope(production_scope: str) -> str:
    if not isinstance(production_scope, str):
        raise AudioAssetError("production_scope must be a string")
    value = production_scope.strip()
    if len(value) < MIN_PRODUCTION_SCOPE_LENGTH:
        raise AudioAssetError("production_scope is too short to be a valid authorization token")
    if len(value) > MAX_PRODUCTION_SCOPE_LENGTH:
        raise AudioAssetError("production_scope is too long")
    if any(ord(character) < 32 for character in value):
        raise AudioAssetError("production_scope contains invalid characters")
    return value


def _stage_upload(filename: str, source: BinaryIO) -> tuple[str, str, int]:
    """Écrit le flux reçu dans un fichier temporaire du MÊME dossier que le stockage final (pour que le
    ``os.replace`` ultérieur soit atomique), par blocs et avec une limite de taille stricte — jamais
    l'intégralité du flux chargée en mémoire avant de connaître sa taille réelle."""
    safe_name = sanitize_audio_asset_filename(filename)
    try:
        target_dir = uploaded_audio_asset_dir(create=True)
    except OSError as exc:
        raise AudioAssetServiceError("failed to prepare audio asset storage") from exc

    temp_path = ""
    total_bytes = 0
    try:
        try:
            source.seek(0)
        except (AttributeError, OSError) as exc:
            raise AudioAssetError("audio asset upload is not seekable") from exc

        descriptor, temp_path = tempfile.mkstemp(
            prefix=_INTERNAL_UPLOAD_PREFIX, suffix=Path(safe_name).suffix.lower(), dir=target_dir,
        )
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = source.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray, memoryview)):
                    raise AudioAssetError("audio asset upload must be binary")
                total_bytes += len(chunk)
                if total_bytes > MAX_AUDIO_ASSET_UPLOAD_BYTES:
                    raise AudioAssetError("audio asset file exceeds the 50 MB limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total_bytes == 0:
            raise AudioAssetError("audio asset file is empty")
        return safe_name, temp_path, total_bytes
    except Exception as exc:
        _remove_file_quietly(temp_path)
        if isinstance(exc, AudioAssetError):
            raise
        if isinstance(exc, OSError):
            raise AudioAssetServiceError("failed to stage audio asset upload") from exc
        raise


def cleanup(now: float | None = None, force: bool = False) -> int:
    """Nettoyage à basse fréquence (au plus une fois par ``_CLEANUP_INTERVAL_SECONDS``, sauf
    ``force=True`` réservé aux tests) : enlève du registre et du disque les assets expirés
    (jamais consommés après ``UNRESOLVED_TTL_SECONDS``, ou consommés depuis plus de
    ``CONSUMED_GRACE_SECONDS``), PUIS balaie le disque à la recherche de fichiers orphelins (nom
    conforme à ``_ASSET_FILE_PATTERN``, absents du registre — ex. après un redémarrage) plus vieux que
    ``UNRESOLVED_TTL_SECONDS``. Ne touche jamais un fichier dont le nom ne correspond pas à ce motif."""
    global _last_cleanup_monotonic
    monotonic_now = time.monotonic()
    with _registry_lock:
        if not force and _last_cleanup_monotonic is not None and \
           monotonic_now - _last_cleanup_monotonic < _CLEANUP_INTERVAL_SECONDS:
            return 0
        _last_cleanup_monotonic = monotonic_now

        current_time = time.time() if now is None else now
        expired_ids = []
        for asset_id, record in _assets.items():
            if record.consumed_at is not None:
                if current_time - record.consumed_at >= CONSUMED_GRACE_SECONDS:
                    expired_ids.append(asset_id)
            elif current_time - record.created_at >= UNRESOLVED_TTL_SECONDS:
                expired_ids.append(asset_id)
        removed = 0
        for asset_id in expired_ids:
            record = _assets.pop(asset_id, None)
            if record:
                _remove_file_quietly(record.file_path)
                removed += 1

        target_dir = uploaded_audio_asset_dir(create=False)
        if os.path.isdir(target_dir):
            try:
                with os.scandir(target_dir) as entries:
                    stale_orphans = [entry for entry in entries
                                    if entry.name not in _assets and _ASSET_FILE_PATTERN.fullmatch(entry.name)]
            except OSError as exc:
                logger.warning(f"failed to scan audio asset storage: error={str(exc)}")
                stale_orphans = []
            for entry in stale_orphans:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    age = current_time - entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if age >= UNRESOLVED_TTL_SECONDS:
                    _remove_file_quietly(entry.path)
                    removed += 1
        return removed


def save_audio_asset_upload(filename: str, source: BinaryIO, production_scope: str) -> str:
    """Valide, décode réellement (FFmpeg, jamais seulement l'extension/le type MIME déclaré) et stocke
    un fichier audio déjà synthétisé sous un nom opaque — AUCUN appel TTS, AUCUNE génération.

    Lève ``AudioAssetError`` (entrée cliente invalide) ou ``AudioAssetServiceError`` (panne serveur).
    Renvoie un ``asset_id`` imprévisible (``uuid4().hex``, 128 bits), jamais dérivé du nom de fichier ni
    du ``production_scope`` fourni.
    """
    scope = _validate_production_scope(production_scope)
    safe_name, temp_path, total_bytes = _stage_upload(filename, source)
    try:
        # Décodage RÉEL du flux audio (pas une simple lecture d'en-tête) : un fichier renommé avec une
        # fausse extension, ou un contenu corrompu, échoue ici même s'il a passé sanitize_* ci-dessus.
        bgm_service.validate_audio_file(temp_path, timeout_seconds=30)

        asset_id = uuid4().hex
        stored_name = f"{asset_id}{Path(safe_name).suffix.lower()}"
        target_path = os.path.join(os.path.dirname(temp_path), stored_name)
        os.replace(temp_path, target_path)
    except bgm_service.BgmUploadError as exc:
        _remove_file_quietly(temp_path)
        raise AudioAssetError(str(exc)) from exc
    except bgm_service.BgmServiceError as exc:
        _remove_file_quietly(temp_path)
        raise AudioAssetServiceError(str(exc)) from exc
    except OSError as exc:
        _remove_file_quietly(temp_path)
        raise AudioAssetServiceError("failed to persist audio asset upload") from exc

    with _registry_lock:
        _assets[asset_id] = _AssetRecord(file_path=target_path, production_scope=scope, created_at=time.time())
    logger.debug(f"audio asset stored: asset_id={asset_id}, size={total_bytes} bytes")
    cleanup()
    return asset_id


def resolve_audio_asset(asset_id: str, production_scope: str) -> str:
    """Résout (et marque consommée) une référence d'asset pour un ``/videos`` en cours de création.

    Lève ``AudioAssetError`` avec un message TOUJOURS identique, que l'asset soit inconnu, expiré,
    déjà consommé, ou lié à un ``production_scope`` différent — jamais de distinction qui laisserait
    deviner lequel de ces cas s'est produit. Comparaison du jeton à temps constant
    (``secrets.compare_digest``) : ce champ joue le rôle d'un secret d'autorisation.
    """
    cleanup()
    generic_error = AudioAssetError("unknown or expired audio asset reference")
    if not isinstance(asset_id, str) or not asset_id:
        raise generic_error
    scope = str(production_scope or "")
    with _registry_lock:
        record = _assets.get(asset_id)
        if record is None:
            raise generic_error
        if record.consumed_at is not None:
            raise generic_error
        if not secrets.compare_digest(record.production_scope.encode("utf-8"), scope.encode("utf-8")):
            raise generic_error
        record.consumed_at = time.time()
        return record.file_path
