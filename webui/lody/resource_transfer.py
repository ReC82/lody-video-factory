"""Import/export partiel de personnages et de lieux dans un projet EXISTANT (ticket #57).

Suite de #44 (``lody.project_transfer``) : réutilise EXACTEMENT ses définitions de champs, ses
exemples et sa validation — ``CHARACTER_FIELDS``/``LOCATION_FIELDS``, ``example_item``,
``load_json``, ``validate_items``, ``FieldIssue``/``TransferError`` — aucune seconde définition
métier, aucun risque de dérive silencieuse entre les deux formats.

Ce module n'ajoute que ce qui est réellement différent de #44 :

* une enveloppe « une seule ressource » (``{schema_version, resource_type, characters}`` ou
  ``{schema_version, resource_type, locations}``) plutôt que l'enveloppe complète d'un projet
  ({project, characters, locations}) — un fichier de personnages ne peut jamais être accepté
  comme fichier de lieux, et inversement (vérifié via ``resource_type``) ;
* une cible : le projet **déjà existant** actuellement ouvert — jamais de nouveau projet créé,
  jamais un autre paramètre du projet touché ;
* une politique de collision MVP : un nom déjà présent dans CE projet est un conflit **bloquant**
  (pas de fusion/mise à jour silencieuse — voir ``ResourcePreview.conflicts``) ;
* un avertissement (jamais bloquant : le modèle de données actuel n'impose déjà aucune unicité du
  personnage/lieu « principal », voir ``characters.py``/``locations.py``) si l'import ferait
  exister plus d'un élément principal dans le projet après coup — l'aperçu l'annonce précisément.

Mêmes garanties de sécurité que #44 : aucun ``id``/``project_id``/``created_at``/``updated_at`` du
fichier n'est jamais utilisé (régénérés localement), aucun secret, écriture atomique (tout ou
rien), aucune écriture avant confirmation explicite.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody.characters import Character, CharacterRepository, CharacterValidationError
from lody.characters import validate_fields as validate_character_fields
from lody.locations import Location, LocationRepository, LocationValidationError
from lody.locations import validate_fields as validate_location_fields
from lody.project_transfer import (
    CHARACTER_DEFAULTS,
    CHARACTER_EXAMPLE_PRIMARY,
    CHARACTER_EXAMPLE_SECONDARY,
    CHARACTER_FIELDS,
    LOCATION_DEFAULTS,
    LOCATION_EXAMPLE_PRIMARY,
    LOCATION_EXAMPLE_SECONDARY,
    LOCATION_FIELDS,
    SCHEMA_VERSION,
    FieldIssue,
    TransferError,
    example_item,
    load_json,
    validate_items,
)

RESOURCE_CHARACTERS = "characters"
RESOURCE_LOCATIONS = "locations"


@dataclass
class ResourcePreview:
    """Aperçu sans écriture (étape 1). ``is_valid`` conditionne l'affichage du bouton de
    confirmation — voir ``lody.project_transfer.ImportPreview`` pour le même principe côté #44."""

    resource_type: str
    item_names: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # noms déjà présents dans LE PROJET CIBLE
    warnings: list[str] = field(default_factory=list)
    errors: list[FieldIssue] = field(default_factory=list)
    clean: list[dict[str, Any]] | None = None  # éléments nettoyés, prêts pour commit_*_import

    @property
    def is_valid(self) -> bool:
        return not self.errors and self.clean is not None


# -- export -----------------------------------------------------------------------------------------------------
def export_characters(characters: Iterable[Character]) -> dict[str, Any]:
    """Un, plusieurs, ou tous les personnages d'un projet (la sélection est de la responsabilité de
    l'appelant — cette fonction se contente d'exporter ce qu'on lui donne)."""
    return {"schema_version": SCHEMA_VERSION, "resource_type": RESOURCE_CHARACTERS,
            "characters": [{key: getattr(character, key) for key in CHARACTER_FIELDS} for character in characters]}


def export_locations(locations: Iterable[Location]) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "resource_type": RESOURCE_LOCATIONS,
            "locations": [{key: getattr(location, key) for key in LOCATION_FIELDS} for location in locations]}


def character_template() -> dict[str, Any]:
    """Même source de vérité que #44 (``example_item``/``CHARACTER_FIELDS``/les exemples partagés) :
    ne peut pas dériver silencieusement du format réellement accepté par ``preview_character_import``."""
    return {"schema_version": SCHEMA_VERSION, "resource_type": RESOURCE_CHARACTERS,
            "characters": [example_item(CHARACTER_FIELDS, CHARACTER_DEFAULTS, override)
                           for override in (CHARACTER_EXAMPLE_PRIMARY, CHARACTER_EXAMPLE_SECONDARY)]}


def location_template() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "resource_type": RESOURCE_LOCATIONS,
            "locations": [example_item(LOCATION_FIELDS, LOCATION_DEFAULTS, override)
                          for override in (LOCATION_EXAMPLE_PRIMARY, LOCATION_EXAMPLE_SECONDARY)]}


# -- aperçu (validation, jamais d'écriture) ------------------------------------------------------------------------
def _preview(raw: bytes | str, *, resource_type: str, list_key: str, known_fields: tuple[str, ...],
            defaults: dict[str, Any], validate_fn, error_type: type[Exception],
            existing_names: Iterable[str], existing_primary_count: int) -> ResourcePreview:
    data, errors = load_json(raw)
    warnings: list[str] = []
    if data is None:
        return ResourcePreview(resource_type, errors=errors)

    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        errors.append(FieldIssue("schema_version", "Doit être un nombre entier."))
    elif version != SCHEMA_VERSION:
        errors.append(FieldIssue(
            "schema_version",
            f"Version de schéma « {version} » non prise en charge (seule la version {SCHEMA_VERSION} l'est)."))

    actual_type = data.get("resource_type")
    if actual_type != resource_type:
        label = "personnages" if resource_type == RESOURCE_CHARACTERS else "lieux"
        errors.append(FieldIssue(
            "resource_type",
            f"Ce fichier est du type « {actual_type or 'absent'} », un fichier de {label} est attendu ici "
            "(resource_type doit valoir " + f"« {resource_type} »)."))

    warnings.extend(f"Champ inconnu ignoré : {key}"
                    for key in sorted(set(data) - {"schema_version", "resource_type", list_key}))

    clean_items, item_warnings, item_errors = validate_items(
        data.get(list_key), list_key, known_fields, defaults, validate_fn, error_type)
    warnings += item_warnings
    errors += item_errors

    if errors:
        return ResourcePreview(resource_type, item_names=[item["name"] for item in clean_items],
                               warnings=warnings, errors=errors)

    existing_casefold = {name.casefold() for name in existing_names}
    conflicts = [item["name"] for item in clean_items if item["name"].casefold() in existing_casefold]
    if conflicts:
        plural = "s" if len(conflicts) > 1 else ""
        errors.append(FieldIssue(
            list_key,
            f"{len(conflicts)} nom{plural} déjà présent{plural} dans ce projet : {', '.join(conflicts)}. "
            "Renomme l'élément dans le fichier, ou renomme l'existant, puis réessaie — aucun écrasement "
            "silencieux n'est possible."))
        return ResourcePreview(resource_type, item_names=[item["name"] for item in clean_items],
                               conflicts=conflicts, warnings=warnings, errors=errors)

    new_primary_count = existing_primary_count + sum(1 for item in clean_items if item.get("is_primary"))
    if new_primary_count > 1:
        warnings.append(
            f"{new_primary_count} éléments seraient marqués « principal » dans ce projet après cet import "
            "(le modèle actuel ne l'interdit pas, mais vérifie que c'est bien voulu).")

    return ResourcePreview(resource_type, item_names=[item["name"] for item in clean_items],
                           warnings=warnings, errors=[], clean=clean_items)


def preview_character_import(raw: bytes | str, *, existing_names: Iterable[str] = (),
                             existing_primary_count: int = 0) -> ResourcePreview:
    """Ne lève jamais : les erreurs sont dans le résultat (même principe que #44)."""
    return _preview(raw, resource_type=RESOURCE_CHARACTERS, list_key="characters",
                    known_fields=CHARACTER_FIELDS, defaults=CHARACTER_DEFAULTS,
                    validate_fn=validate_character_fields, error_type=CharacterValidationError,
                    existing_names=existing_names, existing_primary_count=existing_primary_count)


def preview_location_import(raw: bytes | str, *, existing_names: Iterable[str] = (),
                            existing_primary_count: int = 0) -> ResourcePreview:
    return _preview(raw, resource_type=RESOURCE_LOCATIONS, list_key="locations",
                    known_fields=LOCATION_FIELDS, defaults=LOCATION_DEFAULTS,
                    validate_fn=validate_location_fields, error_type=LocationValidationError,
                    existing_names=existing_names, existing_primary_count=existing_primary_count)


# -- import : création atomique dans le projet EXISTANT --------------------------------------------------------------
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _project_row_exists(connection: sqlite3.Connection, project_id: str) -> bool:
    return connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is not None


def _refuse_name_conflicts(connection: sqlite3.Connection, table: str, project_id: str,
                           clean_items: list[dict[str, Any]]) -> None:
    """Re-vérifie DANS LA MÊME transaction : l'aperçu a pu calculer des noms libres un instant plus tôt,
    qu'une autre écriture concurrente a entre-temps pris (voir la même précaution dans
    ``project_transfer._refuse_if_name_taken``) — jamais un écrasement silencieux."""
    existing = {row[0].casefold() for row in connection.execute(
        f"SELECT name FROM {table} WHERE project_id = ?", (project_id,))}  # noqa: S608 — table = littéral fixe
    taken = [item["name"] for item in clean_items if item["name"].casefold() in existing]
    if taken:
        raise TransferError(
            f"Nom(s) pris entre-temps par un autre ajout : {', '.join(taken)}. Recharge le fichier pour réessayer.")


def commit_character_import(db_path: str | Path, project_id: str, preview: ResourcePreview) -> list[Character]:
    """Crée les personnages du lot dans ``project_id`` — tout ou rien, dans une seule transaction. Ne
    touche à AUCUN autre champ du projet, ne crée jamais de nouveau projet."""
    if not preview.is_valid or preview.clean is None:
        raise TransferError("Aperçu invalide : rien à importer.")
    CharacterRepository(db_path)  # migre la base si besoin, aucune écriture ici
    now = _utc_now()
    created_ids: list[str] = []
    connection = sqlite3.connect(str(db_path), timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if not _project_row_exists(connection, project_id):
            raise TransferError("Ce projet n'existe plus.")
        _refuse_name_conflicts(connection, "characters", project_id, preview.clean)
        for item in preview.clean:
            character_id = f"chr_{uuid.uuid4().hex[:12]}"
            values = CharacterRepository._values(item)
            connection.execute(
                "INSERT INTO characters (id, project_id, created_at, updated_at, " + ", ".join(CHARACTER_FIELDS)
                + ") VALUES (?, ?, ?, ?, " + ", ".join("?" for _ in CHARACTER_FIELDS) + ")",
                (character_id, project_id, now, now, *(values[key] for key in CHARACTER_FIELDS)),
            )
            created_ids.append(character_id)
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()
    repo = CharacterRepository(db_path)
    return [repo.get(project_id, character_id) for character_id in created_ids]


def commit_location_import(db_path: str | Path, project_id: str, preview: ResourcePreview) -> list[Location]:
    if not preview.is_valid or preview.clean is None:
        raise TransferError("Aperçu invalide : rien à importer.")
    LocationRepository(db_path)
    now = _utc_now()
    created_ids: list[str] = []
    connection = sqlite3.connect(str(db_path), timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if not _project_row_exists(connection, project_id):
            raise TransferError("Ce projet n'existe plus.")
        _refuse_name_conflicts(connection, "locations", project_id, preview.clean)
        for item in preview.clean:
            location_id = f"loc_{uuid.uuid4().hex[:12]}"
            values = LocationRepository._values(item)
            connection.execute(
                "INSERT INTO locations (id, project_id, created_at, updated_at, " + ", ".join(LOCATION_FIELDS)
                + ") VALUES (?, ?, ?, ?, " + ", ".join("?" for _ in LOCATION_FIELDS) + ")",
                (location_id, project_id, now, now, *(values[key] for key in LOCATION_FIELDS)),
            )
            created_ids.append(location_id)
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()
    repo = LocationRepository(db_path)
    return [repo.get(project_id, location_id) for location_id in created_ids]
