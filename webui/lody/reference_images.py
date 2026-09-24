"""Images de référence facultatives des personnages et des lieux (ticket #38).

Stockage hors SQLite : les octets vivent dans le volume de données (``<data_dir>/references/<projet>/
<entité>/<id>/``), SQLite ne garde que la référence relative déjà validée (colonne ``reference_image`` de
``characters``/``locations``, ajoutée par la migration 6 de ``lody.db``). Chaîne vide = aucune image :
comportement strictement inchangé pour tout personnage ou lieu qui n'en a pas.

Chaque enregistrement écrit un nom de fichier NEUF (UUID, jamais le nom fourni par le client) : remplacer
une image n'écrase ni ne supprime l'ancien fichier — il devient orphelin (plus référencé par aucun
personnage/lieu actif) mais reste résolvable. C'est délibéré : une production déjà préparée a copié la
référence *alors valide* dans son ``snapshot`` (voir ``generation.narrative_context``), et ne doit jamais
se retrouver avec un fichier manquant après coup (risque explicitement nommé par #38 : « référence
supprimée après lancement »). C'est aussi exactement la stratégie de secours documentée par le ticket lui
même (« conserver les fichiers/références sans les utiliser ») et cohérente avec le choix déjà fait pour
``characters.py``/``locations.py`` : jamais de suppression destructive.

Validation stricte, dans cet ordre : taille, puis type réel (Pillow lit la STRUCTURE du fichier — jamais son
extension ni le Content-Type déclaré par le navigateur, tous deux choisis par le client et sans valeur de
preuve), puis dimensions. Pillow est déjà une dépendance obligatoire de Streamlit (voir ``requirements.lody.txt``
→ ``streamlit`` → ``pillow`` : aucune dépendance supplémentaire n'est ajoutée par ce ticket).

La résolution d'un chemin réutilise ``generation.safety.resolve_within`` — même garde-fou anti-traversée que
les fichiers de kit de publication (#24, ``generation/kit_files.py``) — avec un préfixe qui isole STRICTEMENT
par PROJET et par ENTITÉ : un personnage ne peut jamais résoudre la référence d'un autre personnage, encore
moins d'un autre projet (ticket #38 : « isolation stricte par projet et par entité », « protection contre...
les accès inter-projets »).
"""

from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from lody import settings
from lody.generation.safety import resolve_within

MAX_BYTES = 8_000_000  # 8 Mo : large pour une image de référence, petit pour un flux détourné
MIN_DIMENSION = 64
MAX_DIMENSION = 4096
MAX_PIXELS = 30_000_000  # ~30 Mpx : une image "bombe" (peu d'octets compressés, énorme une fois décodée)

# Détection par le CONTENU réel (Pillow), jamais par l'extension ni le Content-Type déclaré par le client.
_ALLOWED_FORMATS = {"PNG": (".png", "image/png"), "JPEG": (".jpg", "image/jpeg"), "WEBP": (".webp", "image/webp")}
ALLOWED_SUFFIXES = tuple(extension for extension, _mime in _ALLOWED_FORMATS.values())

_PROJECT_ID = re.compile(r"^prj_[0-9a-f]{12}$")
# Préfixe d'identifiant attendu pour CE type d'entité (voir characters.py/locations.py : chr_/loc_) — un
# ``entity_id`` qui ne correspond pas au type déclaré est refusé, jamais silencieusement toléré.
_ENTITY_PREFIXES = {"characters": "chr_", "locations": "loc_"}


class ReferenceImageError(ValueError):
    """Message déjà présentable tel quel à l'utilisateur (jamais de détail technique interne)."""


@dataclass(frozen=True)
class StoredImage:
    ref: str
    mime: str
    width: int
    height: int
    bytes_size: int


def root() -> Path:
    return settings.data_dir() / "references"


def _validate_ids(project_id: str, entity_type: str, entity_id: str) -> None:
    if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
        raise ReferenceImageError("Projet invalide.")
    prefix = _ENTITY_PREFIXES.get(entity_type)
    if prefix is None:
        raise ReferenceImageError("Type d'élément invalide.")
    if not isinstance(entity_id, str) or not entity_id.startswith(prefix):
        raise ReferenceImageError("Élément invalide.")


def _inspect(data: bytes) -> tuple[str, str, int, int]:
    """(extension, mime, largeur, hauteur) si ``data`` est une image valide et acceptée, sinon lève
    ``ReferenceImageError``. Ne fait JAMAIS confiance à un nom de fichier ou un Content-Type déclaré."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ReferenceImageError("Aucune image reçue.")
    if len(data) > MAX_BYTES:
        raise ReferenceImageError(f"Image trop volumineuse ({MAX_BYTES // 1_000_000} Mo maximum).")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()  # structure complète (en-têtes, CRC…) sans décoder les pixels
        with Image.open(io.BytesIO(data)) as image:  # verify() invalide l'objet : réouverture nécessaire
            fmt = image.format
            width, height = image.size
    except Exception:
        # Pillow lève des exceptions de natures diverses (UnidentifiedImageError, OSError, decompression bomb,
        # erreurs internes de décodeur…) sur un contenu arbitraire et non fiable : on refuse dans tous les cas,
        # sans jamais laisser fuir un détail technique dans le message affiché.
        raise ReferenceImageError("Ce fichier n'est pas une image valide (PNG, JPEG ou WEBP).") from None
    info = _ALLOWED_FORMATS.get(fmt or "")
    if info is None:
        raise ReferenceImageError("Format d'image non accepté (PNG, JPEG ou WEBP uniquement).")
    extension, mime = info
    if width * height > MAX_PIXELS or width > MAX_DIMENSION or height > MAX_DIMENSION:
        raise ReferenceImageError(f"Image trop grande ({MAX_DIMENSION}×{MAX_DIMENSION} pixels maximum).")
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        raise ReferenceImageError(f"Image trop petite ({MIN_DIMENSION}×{MIN_DIMENSION} pixels minimum).")
    return extension, mime, width, height


def validate(data: bytes) -> None:
    """Valide seulement (taille/type/dimensions), sans rien écrire : pour un contrôle précoce côté formulaire,
    avant même que l'entité existe (ex. création d'un nouveau personnage avec image dès le départ). ``save``
    revalide de toute façon dans tous les cas — c'est lui la frontière de sécurité, jamais cet appel facultatif."""
    _inspect(bytes(data))


def save(project_id: str, entity_type: str, entity_id: str, data: bytes) -> StoredImage:
    """Valide puis enregistre ``data`` comme image de référence de cette entité, et renvoie sa référence.

    Écrit toujours un nom de fichier NEUF (UUID) : un fichier existant n'est jamais écrasé ni supprimé (voir
    le docstring du module). Écriture atomique (fichier temporaire puis renommage).
    """
    _validate_ids(project_id, entity_type, entity_id)
    extension, mime, width, height = _inspect(bytes(data))
    folder = root() / project_id / entity_type / entity_id
    folder.mkdir(parents=True, exist_ok=True)
    # 20 caractères hexadécimaux (comme les autres identifiants courts du dépôt, ex. ``chr_{uuid4().hex[:12]}``) :
    # UN uuid4().hex COMPLET (32 caractères) déclenche à tort le garde-fou « jeton opaque » de secrets_guard
    # (≥ 32 caractères mêlant lettres et chiffres) — ce nom de fichier n'est pourtant pas un secret. 80 bits
    # restent très largement suffisants pour éviter toute collision dans le dossier d'UNE seule entité.
    filename = f"{uuid.uuid4().hex[:20]}{extension}"
    target = folder / filename
    temp = folder / f".{filename}.tmp"
    temp.write_bytes(data)
    temp.replace(target)
    ref = f"references/{project_id}/{entity_type}/{entity_id}/{filename}"
    return StoredImage(ref=ref, mime=mime, width=width, height=height, bytes_size=len(data))


def resolve(ref: str, project_id: str, entity_type: str, entity_id: str) -> Path:
    """Chemin réel validé de ``ref``, seulement s'il appartient à CE projet ET cette entité — sinon
    ``ValueError`` (traversée de chemin, projet différent, entité différente : même refus dans tous les cas,
    pour ne jamais laisser deviner qu'un fichier existe ailleurs)."""
    _validate_ids(project_id, entity_type, entity_id)
    prefix = f"references/{project_id}/{entity_type}/{entity_id}"
    return resolve_within(settings.data_dir(), ref, prefix=prefix, suffixes=ALLOWED_SUFFIXES)


def read_bytes(ref: str, project_id: str, entity_type: str, entity_id: str) -> bytes:
    return resolve(ref, project_id, entity_type, entity_id).read_bytes()
