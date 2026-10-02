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

    créé (upload validé, fichier + métadonnées stockés sous un nom opaque)
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

**Persistance (déploiement observé : un seul processus, ``main.py`` appelle ``uvicorn.run()`` sans
``workers=``, ``enable_redis = false`` dans ``config.toml`` — voir le diagnostic posté sur la PR #88).**
Le dict en mémoire (``_assets``) n'est qu'un CACHE du processus en cours ; la source de vérité est le
fichier ``<asset_id>.meta.json`` écrit de façon atomique à côté de l'audio (même dossier,
``tempfile`` + ``os.replace``, comme le fichier audio lui-même). Un redémarrage du processus perd le
cache mais JAMAIS les métadonnées : ``resolve_audio_asset``/``cleanup`` relisent le fichier sur demande
et reconstruisent un enregistrement identique (même ``production_scope``, même ``consumed_at``) — un
asset valide reste donc résolvable avec son autorisation intacte, et un asset déjà consommé ou expiré
reste refusé, même après un redémarrage. Comme le déploiement actuel est mono-processus, ce mécanisme
sert la PERSISTANCE (survivre à un redémarrage), pas le PARTAGE entre processus concurrents ; si ce
service tournait un jour derrière plusieurs workers, ce fichier serait alors aussi le point de partage
(il n'y a qu'une seule source de vérité, jamais un cache qui pourrait diverger entre processus).
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

# Identifiant opaque : TOUJOURS cette forme (uuid4().hex) — validé avant toute construction de chemin,
# car un ``asset_id`` fourni par un appelant ne doit jamais, même indirectement, influencer un chemin de
# fichier au-delà de ce motif strict (défense contre une traversée de chemin via ce champ).
_ASSET_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_METADATA_SUFFIX = ".meta.json"
_METADATA_FILE_PATTERN = re.compile(r"^([0-9a-f]{32})\.meta\.json$")

# Nom de fichier audio final : toujours <uuid4 hex><extension>, jamais dérivé du nom fourni par le
# client — sert aussi de motif pour le nettoyage des orphelins sans métadonnées (crash entre les deux
# écritures, voir ``cleanup``).
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


# Cache PROCESSUS de ce que les fichiers ``.meta.json`` contiennent déjà de façon durable — jamais la
# seule source de vérité (voir ``resolve_audio_asset``/``cleanup``, qui relisent toujours le disque sur
# un cache manquant).
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


def _metadata_path(target_dir: str, asset_id: str) -> str:
    return os.path.join(target_dir, f"{asset_id}{_METADATA_SUFFIX}")


def _write_metadata(target_dir: str, asset_id: str, record: _AssetRecord, extension: str) -> None:
    """Persiste l'enregistrement de façon atomique (tempfile du MÊME dossier + ``os.replace``) : seule
    source de vérité qui doit survivre à un redémarrage du processus — jamais seulement le cache mémoire."""
    payload = {
        "production_scope": record.production_scope,
        "created_at": record.created_at,
        "consumed_at": record.consumed_at,
        "extension": extension,
    }
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
    """Relit un enregistrement depuis le disque (reconstruction après un redémarrage du processus, le
    cache mémoire étant alors vide) — ``None`` pour tout fichier absent, corrompu, incomplet, ou dont
    l'audio associé n'existe plus : traité comme une référence inconnue, jamais une exception qui
    remonterait jusqu'au client."""
    try:
        with open(_metadata_path(target_dir, asset_id), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    try:
        extension = str(payload["extension"])
        if extension not in SUPPORTED_AUDIO_ASSET_EXTENSIONS:
            return None
        audio_path = os.path.join(target_dir, f"{asset_id}{extension}")
        if not os.path.isfile(audio_path):
            return None
        consumed_at = payload.get("consumed_at")
        return _AssetRecord(
            file_path=audio_path,
            production_scope=str(payload["production_scope"]),
            created_at=float(payload["created_at"]),
            consumed_at=float(consumed_at) if consumed_at is not None else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


def cleanup(now: float | None = None, force: bool = False) -> int:
    """Nettoyage à basse fréquence (au plus une fois par ``_CLEANUP_INTERVAL_SECONDS``, sauf
    ``force=True`` réservé aux tests), TOUJOURS depuis le disque (seule source de vérité, voir le
    docstring du module) : pour chaque ``<asset_id>.meta.json`` trouvé, supprime l'asset (audio +
    métadonnées + entrée de cache) s'il est expiré (jamais consommé après ``UNRESOLVED_TTL_SECONDS``,
    ou consommé depuis plus de ``CONSUMED_GRACE_SECONDS``) ou si ses métadonnées sont illisibles/
    incomplètes. Balaie ensuite les fichiers audio SANS métadonnées (crash entre les deux écritures) sur
    la seule base de leur âge de modification. Ne touche jamais un fichier dont le nom ne correspond à
    aucun des deux motifs."""
    global _last_cleanup_monotonic
    monotonic_now = time.monotonic()
    with _registry_lock:
        if not force and _last_cleanup_monotonic is not None and \
           monotonic_now - _last_cleanup_monotonic < _CLEANUP_INTERVAL_SECONDS:
            return 0
        _last_cleanup_monotonic = monotonic_now

        current_time = time.time() if now is None else now
        target_dir = uploaded_audio_asset_dir(create=False)
        if not os.path.isdir(target_dir):
            return 0
        try:
            with os.scandir(target_dir) as entries:
                names = [entry.name for entry in entries]
        except OSError as exc:
            logger.warning(f"failed to scan audio asset storage: error={str(exc)}")
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
            if record is None:
                # Métadonnées illisibles/incomplètes, ou audio déjà absent : jamais résolvable.
                _remove_file_quietly(meta_path)
                _assets.pop(asset_id, None)
                removed += 1
                continue
            expired = (
                (record.consumed_at is not None and current_time - record.consumed_at >= CONSUMED_GRACE_SECONDS)
                or (record.consumed_at is None and current_time - record.created_at >= UNRESOLVED_TTL_SECONDS)
            )
            if expired:
                _remove_file_quietly(record.file_path)
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


def save_audio_asset_upload(filename: str, source: BinaryIO, production_scope: str) -> str:
    """Valide, décode réellement (FFmpeg, jamais seulement l'extension/le type MIME déclaré) et stocke
    un fichier audio déjà synthétisé sous un nom opaque, avec ses métadonnées persistées à côté (voir le
    docstring du module) — AUCUN appel TTS, AUCUNE génération.

    Lève ``AudioAssetError`` (entrée cliente invalide) ou ``AudioAssetServiceError`` (panne serveur).
    Renvoie un ``asset_id`` imprévisible (``uuid4().hex``, 128 bits), jamais dérivé du nom de fichier ni
    du ``production_scope`` fourni.
    """
    scope = _validate_production_scope(production_scope)
    safe_name, temp_path, total_bytes = _stage_upload(filename, source)
    target_dir = os.path.dirname(temp_path)
    asset_id = uuid4().hex
    extension = Path(safe_name).suffix.lower()
    target_path = os.path.join(target_dir, f"{asset_id}{extension}")
    record = _AssetRecord(file_path=target_path, production_scope=scope, created_at=time.time())
    try:
        # Décodage RÉEL du flux audio (pas une simple lecture d'en-tête) : un fichier renommé avec une
        # fausse extension, ou un contenu corrompu, échoue ici même s'il a passé sanitize_* ci-dessus.
        bgm_service.validate_audio_file(temp_path, timeout_seconds=30)
        os.replace(temp_path, target_path)
        _write_metadata(target_dir, asset_id, record, extension)
    except bgm_service.BgmUploadError as exc:
        _remove_file_quietly(temp_path)
        raise AudioAssetError(str(exc)) from exc
    except bgm_service.BgmServiceError as exc:
        _remove_file_quietly(temp_path)
        raise AudioAssetServiceError(str(exc)) from exc
    except OSError as exc:
        # Jamais laisser un fichier audio persistant sans ses métadonnées : sans elles, il ne serait
        # jamais résolvable par un redémarrage ultérieur (voir _read_metadata) — repli complet.
        _remove_file_quietly(temp_path)
        _remove_file_quietly(target_path)
        _remove_file_quietly(_metadata_path(target_dir, asset_id))
        raise AudioAssetServiceError("failed to persist audio asset upload") from exc

    with _registry_lock:
        _assets[asset_id] = record
    logger.debug(f"audio asset stored: asset_id={asset_id}, size={total_bytes} bytes")
    cleanup()
    return asset_id


def resolve_audio_asset(asset_id: str, production_scope: str) -> str:
    """Résout (et marque consommée, de façon PERSISTANTE) une référence d'asset pour un ``/videos`` en
    cours de création.

    Lève ``AudioAssetError`` avec un message TOUJOURS identique, que l'asset soit inconnu, expiré,
    déjà consommé, ou lié à un ``production_scope`` différent — jamais de distinction qui laisserait
    deviner lequel de ces cas s'est produit. Comparaison du jeton à temps constant
    (``secrets.compare_digest``) : ce champ joue le rôle d'un secret d'autorisation. Fonctionne
    identiquement après un redémarrage du processus (reconstruction depuis le disque, voir
    ``_read_metadata``) : un asset valide reste résolvable avec son autorisation intacte, un asset
    expiré/consommé/mal autorisé reste refusé.
    """
    cleanup()
    generic_error = AudioAssetError("unknown or expired audio asset reference")
    if not isinstance(asset_id, str) or not _ASSET_ID_PATTERN.fullmatch(asset_id):
        raise generic_error
    scope = str(production_scope or "")
    target_dir = uploaded_audio_asset_dir(create=False)
    with _registry_lock:
        record = _assets.get(asset_id)
        if record is None:
            record = _read_metadata(target_dir, asset_id)  # reconstruction après redémarrage
            if record is not None:
                _assets[asset_id] = record
        if record is None:
            raise generic_error
        if record.consumed_at is not None:
            raise generic_error
        if not secrets.compare_digest(record.production_scope.encode("utf-8"), scope.encode("utf-8")):
            raise generic_error
        record.consumed_at = time.time()
        try:
            _write_metadata(target_dir, asset_id, record, Path(record.file_path).suffix.lower())
        except OSError as exc:
            # La consommation DOIT être persistée pour empêcher tout double usage après un redémarrage ;
            # si l'écriture échoue, on refuse plutôt que de renvoyer un chemin dont la consommation ne
            # serait pas garantie durable — jamais un succès qui ne survivrait pas à un redémarrage.
            record.consumed_at = None
            logger.warning(f"failed to persist audio asset consumption: asset_id={asset_id}, error={str(exc)}")
            raise AudioAssetServiceError("failed to persist audio asset consumption") from exc
        return record.file_path
