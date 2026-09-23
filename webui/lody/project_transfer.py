"""Export / import complet de configuration de projet (ticket #44).

Format JSON explicite et versionné (``schema_version``). Réutilise la validation et les champs des
modules existants (``projects``, ``characters``, ``locations``) : aucune seconde définition métier
incompatible n'est créée ici, les tuples ``*_FIELDS`` ci-dessous SONT les tuples ``EDITABLE_FIELDS``
de ces modules.

Politique documentée sur les champs inconnus d'un fichier importé : ignorés silencieusement, mais
signalés comme avertissement non bloquant (tolérance aux fichiers produits par une version plus
récente ou plus ancienne de ce format, jamais un blocage surprise pour une clé superflue).

L'import ne modifie jamais un projet existant : il crée toujours un nouveau projet (``commit_import``),
avec de nouveaux identifiants pour le projet, ses personnages et ses lieux. Aucun secret, identifiant
interne, project_id, timestamp ou chemin local n'est jamais exporté ni accepté à l'import.

Toute PR qui ajoute un champ persistant à ``Project``/``Character``/``Location`` doit vérifier son impact
ici : voir la règle et les tests de couverture dans ``docs/lody-project-transfer.md``.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody.characters import (
    DEFAULTS as CHARACTER_DEFAULTS,
    EDITABLE_FIELDS as CHARACTER_FIELDS,
    Character,
    CharacterRepository,
    CharacterValidationError,
    validate_fields as validate_character_fields,
)
from lody.locations import (
    DEFAULTS as LOCATION_DEFAULTS,
    EDITABLE_FIELDS as LOCATION_FIELDS,
    Location,
    LocationRepository,
    LocationValidationError,
    validate_fields as validate_location_fields,
)
from lody.projects import (
    DEFAULTS as PROJECT_DEFAULTS,
    EDITABLE_FIELDS as PROJECT_FIELDS,
    NAME_MAX as PROJECT_NAME_MAX,
    Project,
    ProjectRepository,
    ProjectValidationError,
    validate_fields as validate_project_fields,
)
from lody.secrets_guard import find_secret_path

SCHEMA_VERSION = 1
# Taille et volumétrie généreuses pour un usage réel (un projet a rarement plus de quelques personnages
# ou lieux), mais bornées pour ne jamais laisser un fichier extravagant bloquer le serveur.
MAX_FILE_BYTES = 512 * 1024
MAX_ITEMS = 50

_KNOWN_TOP_KEYS = {"schema_version", "project", "characters", "locations"}


class TransferError(Exception):
    """Erreur de base de l'export/import de configuration."""


@dataclass(frozen=True)
class FieldIssue:
    """Un problème précisément localisé, ex. ``characters[1].name``."""

    path: str
    message: str


class TransferValidationError(TransferError):
    """Levée seulement pour un fichier totalement inexploitable (JSON invalide, trop volumineux, pas un objet)."""

    def __init__(self, errors: list[FieldIssue]):
        self.errors = errors
        super().__init__("; ".join(f"{e.path}: {e.message}" if e.path else e.message for e in errors))


@dataclass
class ImportPreview:
    """Aperçu sans écriture (étape 1) : toujours renvoyé, jamais levé — voir ``preview_import``."""

    project_name: str
    name_was_renamed: bool
    project_summary: dict[str, Any]
    character_names: list[str]
    location_names: list[str]
    warnings: list[str] = field(default_factory=list)
    errors: list[FieldIssue] = field(default_factory=list)
    clean: dict[str, Any] | None = None  # projet/personnages/lieux nettoyés, prêts pour commit_import

    @property
    def is_valid(self) -> bool:
        return not self.errors and self.clean is not None


# -- export -------------------------------------------------------------------------------------------------------
def export_project(project: Project, characters: Iterable[Character], locations: Iterable[Location]) -> dict[str, Any]:
    """Configuration exportable d'un projet : champs fonctionnels uniquement, aucun secret.

    Jamais d'identifiant interne, de ``project_id``, de timestamp ou de chemin local : seuls les champs
    ``EDITABLE_FIELDS`` de chaque modèle sont repris. Défense en profondeur : les valeurs stockées ont déjà
    passé le garde-fou anti-secrets à l'écriture (``secrets_guard``, appliqué par chaque ``validate_fields``),
    mais on revérifie ici l'ensemble du paquet avant qu'il ne quitte le serveur.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "project": {key: copy.deepcopy(getattr(project, key)) for key in PROJECT_FIELDS},
        "characters": [{key: getattr(character, key) for key in CHARACTER_FIELDS} for character in characters],
        "locations": [{key: getattr(location, key) for key in LOCATION_FIELDS} for location in locations],
    }
    leaked = find_secret_path(payload)
    if leaked:  # ne devrait jamais arriver (voir ci-dessus) : refus explicite plutôt qu'un export silencieux
        raise TransferError(f"Export bloqué : une valeur ressemble à un secret ({leaked}).")
    return payload


def _example(known_fields: tuple[str, ...], defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Un exemple dont le JEU DE CLÉS provient de ``known_fields`` (donc de la même source de vérité que
    l'export/import), jamais d'une liste tapée séparément : un champ ajouté à ``EDITABLE_FIELDS`` apparaît
    ici automatiquement (avec sa valeur par défaut tant qu'aucun exemple n'est fourni), et un champ retiré
    disparaît de même — impossible de diverger en silence (exigence complémentaire du ticket #44)."""
    return {key: overrides.get(key, defaults.get(key, "")) for key in known_fields}


# Valeurs d'exemple pour un modèle vierge lisible : seules celles utiles à la démonstration sont listées
# ici, tout champ absent de ces dictionnaires retombe simplement sur sa valeur par défaut (``_example``).
_PROJECT_EXAMPLE = {
    "name": "Mon nouveau projet",
    "description": "Décris ici l'objectif et le ton général de ce projet.",
    "visual_style": "Décris ici le style visuel voulu (univers, ambiance, ce qu'il faut éviter).",
    "tone": "Clair et précis",
    "platforms": ["youtube_shorts", "tiktok"],
}
_CHARACTER_EXAMPLE_PRIMARY = {
    "name": "Personnage principal", "role": "Protagoniste",
    "personality": "Curieux, bienveillant, un peu maladroit.",
    "visual_description": "Silhouette simple, couleurs vives, sans texte ni logo.",
    "reference_prompt": "portrait stylisé, univers coloré, cohérent d'une scène à l'autre",
    "speech_style": "Phrases courtes, ton chaleureux.",
    "permanent_elements": "Porte toujours le même carnet.",
    "continuity_notes": "Reste cohérent d'une vidéo à l'autre.",
    "is_primary": True,
}
_CHARACTER_EXAMPLE_SECONDARY = {
    "name": "Personnage secondaire", "role": "Allié",
    "personality": "Calme, précis, complémentaire du personnage principal.",
    "is_primary": False,
}
_LOCATION_EXAMPLE_PRIMARY = {
    "name": "Lieu principal", "location_type": "Intérieur",
    "description": "Décris ici l'ambiance, la lumière, les éléments récurrents de ce lieu.",
    "reference_prompt": "plan large, lumière douce, mêmes couleurs à chaque apparition",
    "continuity_notes": "Toujours le même agencement d'une vidéo à l'autre.",
    "is_primary": True,
}
_LOCATION_EXAMPLE_SECONDARY = {"name": "Lieu secondaire", "location_type": "Extérieur", "is_primary": False}


def blank_template() -> dict[str, Any]:
    """Modèle vierge téléchargeable, avec deux personnages et deux lieux d'exemple.

    Chaque exemple est construit par ``_example()`` en itérant ``PROJECT_FIELDS``/``CHARACTER_FIELDS``/
    ``LOCATION_FIELDS`` — les mêmes tuples que ceux utilisés par ``export_project``/``preview_import`` : ce
    modèle ne contient donc aucune liste de champs maintenue séparément et ne peut pas devenir obsolète en
    silence (voir ``test_blank_template_field_names_come_from_the_same_fields_tuples_as_import_export``).
    Valeurs génériques, clairement à remplacer ; aucun secret réel.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "project": _example(PROJECT_FIELDS, PROJECT_DEFAULTS, _PROJECT_EXAMPLE),
        "characters": [_example(CHARACTER_FIELDS, CHARACTER_DEFAULTS, override)
                       for override in (_CHARACTER_EXAMPLE_PRIMARY, _CHARACTER_EXAMPLE_SECONDARY)],
        "locations": [_example(LOCATION_FIELDS, LOCATION_DEFAULTS, override)
                      for override in (_LOCATION_EXAMPLE_PRIMARY, _LOCATION_EXAMPLE_SECONDARY)],
    }


# -- lecture du fichier ---------------------------------------------------------------------------------------------
def _load_json(raw: bytes | str) -> tuple[dict[str, Any] | None, list[FieldIssue]]:
    data_bytes = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data_bytes) > MAX_FILE_BYTES:
        return None, [FieldIssue("", f"Fichier trop volumineux ({MAX_FILE_BYTES // 1024} Ko maximum).")]
    try:
        text = data_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None, [FieldIssue("", "Le fichier n'est pas de l'UTF-8 valide.")]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return None, [FieldIssue("", f"JSON invalide : {error.msg} (ligne {error.lineno}).")]
    if not isinstance(data, dict):
        return None, [FieldIssue("", "Le fichier doit contenir un objet JSON (dictionnaire).")]
    return data, []


def _reject_non_bool(raw_item: dict[str, Any], path: str, keys: tuple[str, ...]) -> list[FieldIssue]:
    return [FieldIssue(f"{path}.{key}", "Doit être un booléen (true/false).")
            for key in keys if key in raw_item and not isinstance(raw_item[key], bool)]


def _validate_project(raw: Any) -> tuple[dict[str, Any] | None, list[str], list[FieldIssue]]:
    if raw is None:
        return None, [], [FieldIssue("project", "La section « project » est obligatoire.")]
    if not isinstance(raw, dict):
        return None, [], [FieldIssue("project", "Doit être un objet JSON.")]
    warnings = [f"Champ inconnu ignoré : project.{key}" for key in sorted(set(raw) - set(PROJECT_FIELDS))]
    errors = _reject_non_bool(raw, "project", ())  # aucun champ booléen direct au niveau projet
    if errors:
        return None, warnings, errors
    try:
        clean = validate_project_fields({**copy.deepcopy(PROJECT_DEFAULTS),
                                         **{k: raw[k] for k in PROJECT_FIELDS if k in raw}})
    except ProjectValidationError as error:
        return None, warnings, [FieldIssue(f"project.{f}" if f != "_" else "project", m)
                                for f, m in error.errors.items()]
    return clean, warnings, []


def _validate_items(raw: Any, section: str, known_fields: tuple[str, ...], defaults: dict[str, Any],
                    validate_fn, error_type: type[Exception]) -> tuple[list[dict[str, Any]], list[str], list[FieldIssue]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        return [], [], [FieldIssue(section, "Doit être une liste JSON.")]
    if len(raw) > MAX_ITEMS:
        return [], [], [FieldIssue(section, f"{MAX_ITEMS} éléments maximum.")]
    warnings: list[str] = []
    errors: list[FieldIssue] = []
    clean_items: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, raw_item in enumerate(raw):
        path = f"{section}[{index}]"
        if not isinstance(raw_item, dict):
            errors.append(FieldIssue(path, "Doit être un objet JSON."))
            continue
        warnings.extend(f"Champ inconnu ignoré : {path}.{key}" for key in sorted(set(raw_item) - set(known_fields)))
        bool_errors = _reject_non_bool(raw_item, path, ("is_primary", "is_active"))
        if bool_errors:
            errors.extend(bool_errors)
            continue
        try:
            clean = validate_fn({**copy.deepcopy(defaults), **{k: raw_item[k] for k in known_fields if k in raw_item}})
        except error_type as error:  # CharacterValidationError / LocationValidationError
            errors.extend(FieldIssue(f"{path}.{f}" if f != "_" else path, m) for f, m in error.errors.items())
            continue
        key = clean["name"].casefold()
        if key in seen:
            errors.append(FieldIssue(f"{path}.name",
                                     f"Même nom que {section}[{seen[key]}] : un nom ne peut apparaître qu'une fois."))
        else:
            seen[key] = index
        clean_items.append(clean)
    return clean_items, warnings, errors


def _unique_project_name(name: str, existing_names: Iterable[str]) -> tuple[str, bool]:
    """Nom du futur projet, rendu distinct s'il existe déjà — jamais d'écrasement (voir ``commit_import``)."""
    existing = {n.casefold() for n in existing_names}
    if name.casefold() not in existing:
        return name, False

    def candidate(suffix: str) -> str:
        budget = PROJECT_NAME_MAX - len(suffix)
        base = name if len(name) <= budget else name[:max(budget, 1)].rstrip()
        return f"{base}{suffix}"

    result = candidate(" (import)")
    attempt = 2
    while result.casefold() in existing:
        result = candidate(f" (import {attempt})")
        attempt += 1
    return result, True


def preview_import(raw: bytes | str, *, existing_project_names: Iterable[str] = ()) -> ImportPreview:
    """Étape 1 : lecture, validation et aperçu — AUCUNE écriture. Ne lève jamais : les erreurs sont dans le résultat."""
    data, errors = _load_json(raw)
    warnings: list[str] = []
    if data is None:
        return ImportPreview("", False, {}, [], [], warnings, errors, None)

    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        errors.append(FieldIssue("schema_version", "Doit être un nombre entier."))
    elif version != SCHEMA_VERSION:
        errors.append(FieldIssue(
            "schema_version",
            f"Version de schéma « {version} » non prise en charge (seule la version {SCHEMA_VERSION} l'est)."))
    warnings.extend(f"Champ inconnu ignoré : {key}" for key in sorted(set(data) - _KNOWN_TOP_KEYS))

    clean_project, w, e = _validate_project(data.get("project"))
    warnings += w
    errors += e
    clean_characters, w, e = _validate_items(data.get("characters"), "characters", CHARACTER_FIELDS,
                                             CHARACTER_DEFAULTS, validate_character_fields, CharacterValidationError)
    warnings += w
    errors += e
    clean_locations, w, e = _validate_items(data.get("locations"), "locations", LOCATION_FIELDS,
                                            LOCATION_DEFAULTS, validate_location_fields, LocationValidationError)
    warnings += w
    errors += e

    if errors:
        return ImportPreview(
            project_name=(clean_project or {}).get("name", ""), name_was_renamed=False, project_summary={},
            character_names=[c["name"] for c in clean_characters], location_names=[loc["name"] for loc in clean_locations],
            warnings=warnings, errors=errors, clean=None)

    final_name, renamed = _unique_project_name(clean_project["name"], existing_project_names)
    if renamed:
        warnings.append(f"Le nom « {clean_project['name']} » existe déjà : le projet sera créé sous « {final_name} ».")
    clean_project = {**clean_project, "name": final_name}
    return ImportPreview(
        project_name=final_name, name_was_renamed=renamed,
        project_summary={key: clean_project[key] for key in (
            "description", "language", "format", "content_type", "tone",
            "text_provider", "visual_provider", "voice_provider", "music_provider")},
        character_names=[c["name"] for c in clean_characters],
        location_names=[loc["name"] for loc in clean_locations],
        warnings=warnings, errors=[],
        clean={"project": clean_project, "characters": clean_characters, "locations": clean_locations},
    )


# -- import : création atomique ---------------------------------------------------------------------------------------
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def commit_import(db_path: str | Path, preview: ImportPreview) -> Project:
    """Étape 2 : crée le projet, ses personnages et ses lieux — tout ou rien, dans une seule transaction.

    Chaque dépôt (``ProjectRepository``/``CharacterRepository``/``LocationRepository``) ouvre normalement
    sa propre connexion SQLite courte : les enchaîner donnerait trois transactions indépendantes, donc une
    écriture partielle possible si la deuxième ou la troisième échoue. On réutilise leur validation (déjà
    faite par ``preview_import``) et leur mise en forme des colonnes (``_values``), mais on exécute les
    insertions sur UNE connexion partagée, avec retour arrière complet au moindre problème — aucun projet
    incomplet ne peut rester en base.
    """
    if not preview.is_valid or preview.clean is None:
        raise TransferError("Aperçu invalide : rien à importer.")
    # Ouvre/migre la base si besoin (aucune écriture) ; garantit aussi que les tables existent avant le BEGIN ci-dessous.
    ProjectRepository(db_path)
    CharacterRepository(db_path)
    LocationRepository(db_path)

    clean = preview.clean
    now = _utc_now()
    project_id = f"prj_{uuid.uuid4().hex[:12]}"
    connection = sqlite3.connect(str(db_path), timeout=10)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _refuse_if_name_taken(connection, clean["project"]["name"])
        values = ProjectRepository._values(clean["project"])
        connection.execute(
            "INSERT INTO projects (id, status, created_at, updated_at, " + ", ".join(PROJECT_FIELDS)
            + ") VALUES (?, ?, ?, ?, " + ", ".join("?" for _ in PROJECT_FIELDS) + ")",
            (project_id, "active", now, now, *(values[key] for key in PROJECT_FIELDS)),
        )
        for character in clean["characters"]:
            character_id = f"chr_{uuid.uuid4().hex[:12]}"
            cvalues = CharacterRepository._values(character)
            connection.execute(
                "INSERT INTO characters (id, project_id, created_at, updated_at, " + ", ".join(CHARACTER_FIELDS)
                + ") VALUES (?, ?, ?, ?, " + ", ".join("?" for _ in CHARACTER_FIELDS) + ")",
                (character_id, project_id, now, now, *(cvalues[key] for key in CHARACTER_FIELDS)),
            )
        for location in clean["locations"]:
            location_id = f"loc_{uuid.uuid4().hex[:12]}"
            lvalues = LocationRepository._values(location)
            connection.execute(
                "INSERT INTO locations (id, project_id, created_at, updated_at, " + ", ".join(LOCATION_FIELDS)
                + ") VALUES (?, ?, ?, ?, " + ", ".join("?" for _ in LOCATION_FIELDS) + ")",
                (location_id, project_id, now, now, *(lvalues[key] for key in LOCATION_FIELDS)),
            )
    except Exception:
        connection.rollback()
        raise
    else:
        connection.commit()
    finally:
        connection.close()
    return ProjectRepository(db_path).get(project_id)


def _refuse_if_name_taken(connection: sqlite3.Connection, name: str) -> None:
    """Re-vérifie dans LA MÊME transaction : ``preview_import`` a pu calculer un nom libre un instant plus tôt,
    qu'un autre import concurrent a entre-temps pris (déploiement mono-administrateur : risque faible, mais
    jamais un écrasement silencieux — voir la contrainte « jamais un projet existant écrasé »)."""
    wanted = name.casefold()
    for row in connection.execute("SELECT name FROM projects"):
        if row[0].casefold() == wanted:
            raise TransferError(
                "Le nom proposé vient d'être pris par un autre projet : recharge le fichier pour réessayer.")
