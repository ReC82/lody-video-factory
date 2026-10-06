"""Templates de production : modèle, validation et dépôt SQLite (#110, PR A).

Un template est une **recette réutilisable** d'un projet, appliquée à une nouvelle production. Il ne
remplace jamais les réglages permanents du projet (providers, voix, style visuel global, personnages,
lieux, plateformes) : ceux-là restent la propriété du projet.

``payload`` est un **recouvrement PARTIEL du brief** : une clé absente signifie « hériter du projet ».
C'est ce qui permet de distinguer, à l'application (PR B), une valeur héritée du projet d'une valeur
imposée par le template — impossible si le template portait un brief complet, où rien ne distinguerait un
réglage voulu d'un défaut recopié.

Les clés de recouvrement sont validées en **réutilisant ``lody.brief``** (ses limites, ses plages, ses
listes de choix) : aucune limite n'est redéfinie ici, sinon les deux vocabulaires divergeraient en silence.

Trois champs du ticket n'ont aucun consommateur dans la chaîne de génération (``GenerationRequest`` ne
porte ni hook, ni style de sous-titres, ni règle de musique narrative) : ``hook_notes``,
``subtitle_notes`` et ``music_notes`` sont donc stockés comme champs propres, et **repliés dans
``standing_instructions``** au moment de l'application (PR B), que le moteur consomme déjà. Aucun réglage
inerte dans l'interface, aucune modification du moteur.

Portée : **par projet**, comme les personnages et les lieux — un projet n'hérite jamais des règles d'un
autre. La réutilisation entre projets passe par ``duplicate_to_project``, explicite et traçable.

Aucune clé API (``secrets_guard`` est appliqué au nom, à la description et à toute chaîne du payload).
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import brief as brief_lib
from lody import db
from lody.secrets_guard import SECRET_MESSAGE, find_secret_path

logger = logging.getLogger("lody.templates")

NAME_MIN, NAME_MAX = 2, 80
DESCRIPTION_MAX = 500

# Clés de recouvrement : SOUS-ENSEMBLE de DEFAULT_BRIEF, donc rien à maintenir en double. Les réglages
# permanents du projet (providers, voix, style visuel, plateformes) n'y figurent délibérément pas : un
# template est une recette d'épisode, pas une redéfinition du projet.
OVERLAY_KEYS: tuple[str, ...] = (
    "duration_min",
    "duration_max",
    "narration_pace",
    "scenes_per_minute_min",
    "scenes_per_minute_max",
    "structure",
    "standing_instructions",
    "visual_rules",
    "visual_avoid",
)
# Les bornes d'un intervalle se valident ensemble (brief.validate_brief compare min <= max) : déclarer
# l'une sans l'autre donnerait une comparaison contre une valeur par défaut, pas contre l'intention.
RANGE_PAIRS: tuple[tuple[str, str], ...] = (
    ("duration_min", "duration_max"),
    ("scenes_per_minute_min", "scenes_per_minute_max"),
)

# Champs propres au template, repliés dans les consignes à l'application (voir le docstring du module).
NOTE_KEYS: tuple[str, ...] = ("hook_notes", "subtitle_notes", "music_notes")
NOTE_MAX = 1000
NOTE_LABELS = {
    "hook_notes": "Les consignes de hook",
    "subtitle_notes": "Le style de sous-titres",
    "music_notes": "La règle de musique",
}

PAYLOAD_KEYS: frozenset[str] = frozenset((*OVERLAY_KEYS, *NOTE_KEYS))
EDITABLE_FIELDS: tuple[str, ...] = ("name", "description", "payload", "is_active")
DEFAULTS: dict[str, Any] = {"description": "", "payload": {}, "is_active": True}


class TemplateError(Exception):
    """Erreur de base de la couche templates."""


class TemplateValidationError(TemplateError):
    """Données invalides : ``errors`` associe un champ à un message lisible."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


class TemplateNotFound(TemplateError):
    pass


@dataclass
class ProductionTemplate:
    id: str
    project_id: str
    name: str
    description: str
    payload: dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""

    def overlay(self) -> dict[str, Any]:
        """Seules les clés de brief réellement imposées par ce template."""
        return {key: copy.deepcopy(value) for key, value in self.payload.items() if key in OVERLAY_KEYS}

    def notes(self) -> dict[str, str]:
        """Consignes propres au template, non vides (repliées dans les consignes à l'application)."""
        return {key: self.payload[key] for key in NOTE_KEYS if self.payload.get(key)}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_payload(raw: Any) -> dict[str, Any]:
    """Nettoie et valide un recouvrement partiel. Lève ``TemplateValidationError``.

    Les clés de recouvrement passent par ``brief.validate_brief`` — exactement les mêmes limites, plages et
    listes de choix que le brief d'un projet. On ne conserve ensuite QUE les clés réellement déclarées : le
    brief complet construit pour la validation ne doit pas se retrouver figé dans le template, sinon chaque
    défaut du brief deviendrait une valeur imposée.
    """
    if not isinstance(raw, dict):
        raise TemplateValidationError({"payload": "Le contenu du template est invalide."})

    errors: dict[str, str] = {}
    unknown = sorted(set(raw) - PAYLOAD_KEYS)
    if unknown:
        errors["payload"] = "Réglage inconnu dans le template : " + ", ".join(unknown) + "."

    declared = {key: raw[key] for key in OVERLAY_KEYS if key in raw}
    for low, high in RANGE_PAIRS:
        if (low in declared) != (high in declared):
            errors[f"payload.{low}"] = (
                f"Renseigne « {low} » et « {high} » ensemble : une borne seule ne peut pas être vérifiée."
            )

    clean: dict[str, Any] = {}
    if declared:
        # Brief complet UNIQUEMENT pour la validation : les valeurs par défaut comblent les clés absentes,
        # puis on les jette (voir le docstring).
        merged = {**copy.deepcopy(brief_lib.DEFAULT_BRIEF), **declared}
        validated, brief_errors = brief_lib.validate_brief(merged)
        prefix = f"{brief_lib.BRIEF_KEY}."
        for path, message in brief_errors.items():
            key = path[len(prefix):] if path.startswith(prefix) else path
            if key in declared or key in {low for low, _ in RANGE_PAIRS}:
                errors.setdefault(f"payload.{key}", message)
        for key in declared:
            if key in validated:
                clean[key] = copy.deepcopy(validated[key])

    for key in NOTE_KEYS:
        if key not in raw:
            continue
        value = " ".join(str(raw[key] or "").split())
        if len(value) > NOTE_MAX:
            errors[f"payload.{key}"] = f"{NOTE_LABELS[key]} est trop long ({NOTE_MAX} caractères maximum)."
        if value:
            clean[key] = value

    if not errors and (leaked := find_secret_path(clean)):
        errors["payload"] = f"{SECRET_MESSAGE} ({leaked})"
    if errors:
        raise TemplateValidationError(errors)
    return clean


def validate_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Nettoie et valide un jeu complet de champs. Lève ``TemplateValidationError``."""
    errors: dict[str, str] = {}
    clean: dict[str, Any] = {}

    name = " ".join(str(fields.get("name") or "").split())
    if not name:
        errors["name"] = "Donne un nom à ce template."
    elif len(name) < NAME_MIN:
        errors["name"] = f"Le nom doit faire au moins {NAME_MIN} caractères."
    elif len(name) > NAME_MAX:
        errors["name"] = f"Le nom est trop long ({NAME_MAX} caractères maximum)."
    clean["name"] = name

    description = str(fields.get("description") or "").strip()
    if len(description) > DESCRIPTION_MAX:
        errors["description"] = f"La description est trop longue ({DESCRIPTION_MAX} caractères maximum)."
    clean["description"] = description

    try:
        clean["payload"] = validate_payload(fields.get("payload", {}) or {})
    except TemplateValidationError as error:
        errors.update(error.errors)
        clean["payload"] = {}

    clean["is_active"] = bool(fields.get("is_active", True))

    for key in ("name", "description"):
        if key not in errors and find_secret_path(clean[key]):
            errors[key] = SECRET_MESSAGE
    if errors:
        raise TemplateValidationError(errors)
    return clean


class TemplateRepository:
    """Accès SQLite aux templates de production. Une connexion courte par opération (thread-safe)."""

    def __init__(self, db_path: str | Path, clock: Callable[[], str] = _utc_now):
        self.db_path = Path(db_path)
        self._clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

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

    def _migrate(self) -> None:
        with self._connect() as connection:
            db.migrate(connection)

    @staticmethod
    def _project_exists(connection: sqlite3.Connection, project_id: str) -> bool:
        return connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is not None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> ProductionTemplate:
        data = dict(row)
        data["is_active"] = bool(data["is_active"])
        try:
            parsed = json.loads(data.get("payload") or "{}")
        except ValueError:  # ligne écrite hors de cette couche : on ne devine pas un contenu
            parsed = {}
        data["payload"] = parsed if isinstance(parsed, dict) else {}
        return ProductionTemplate(**data)

    @staticmethod
    def _values(clean: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": clean["name"],
            "description": clean["description"],
            "payload": json.dumps(clean["payload"], ensure_ascii=False),
            "is_active": int(clean["is_active"]),
        }

    def _check_name_unique(self, connection: sqlite3.Connection, project_id: str, name: str,
                           exclude_id: str | None) -> None:
        """Unique PAR PROJET seulement (même nom permis ailleurs) : ``casefold()`` applicatif, comme
        ``ProjectRepository``/``CharacterRepository`` — plus correct que ``COLLATE NOCASE``, limité à
        l'ASCII, qui laisserait passer « SHORT PÉDAGOGIQUE » face à « Short pédagogique »."""
        wanted = name.casefold()
        for row in connection.execute(
            "SELECT id, name FROM production_templates WHERE project_id = ?", (project_id,)
        ):
            if row["id"] != exclude_id and row["name"].casefold() == wanted:
                raise TemplateValidationError({"name": "Un template de ce projet porte déjà ce nom."})

    # -- lecture ------------------------------------------------------------------------------------------
    def get(self, project_id: str, template_id: str) -> ProductionTemplate:
        """Lève ``TemplateNotFound`` si l'identifiant n'existe pas OU appartient à un autre projet : même
        refus dans les deux cas, pour ne jamais laisser deviner qu'un template existe ailleurs."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM production_templates WHERE id = ? AND project_id = ?",
                (template_id, project_id),
            ).fetchone()
        if row is None:
            raise TemplateNotFound(template_id)
        return self._from_row(row)

    def list_for_project(self, project_id: str, *, include_inactive: bool = True) -> list[ProductionTemplate]:
        query = "SELECT * FROM production_templates WHERE project_id = ?"
        if not include_inactive:
            query += " AND is_active = 1"
        query += " ORDER BY is_active DESC, name COLLATE NOCASE"
        with self._connect() as connection:
            rows = connection.execute(query, (project_id,)).fetchall()
        return [self._from_row(row) for row in rows]

    def count_for_project(self, project_id: str, *, include_inactive: bool = True) -> int:
        query = "SELECT COUNT(*) FROM production_templates WHERE project_id = ?"
        if not include_inactive:
            query += " AND is_active = 1"
        with self._connect() as connection:
            return int(connection.execute(query, (project_id,)).fetchone()[0])

    # -- écriture -----------------------------------------------------------------------------------------
    def create(self, project_id: str, **fields: Any) -> ProductionTemplate:
        clean = validate_fields({**copy.deepcopy(DEFAULTS), **fields})
        now = self._clock()
        template_id = f"tpl_{uuid.uuid4().hex[:12]}"
        values = self._values(clean)
        with self._connect() as connection:
            if not self._project_exists(connection, project_id):
                raise TemplateValidationError({"project_id": "Ce projet n’existe pas."})
            self._check_name_unique(connection, project_id, clean["name"], None)
            connection.execute(
                "INSERT INTO production_templates (id, project_id, created_at, updated_at, "
                + ", ".join(EDITABLE_FIELDS)
                + ") VALUES (?, ?, ?, ?, "
                + ", ".join("?" for _ in EDITABLE_FIELDS)
                + ")",
                (template_id, project_id, now, now, *(values[key] for key in EDITABLE_FIELDS)),
            )
        logger.info("template créé : %s (projet %s)", template_id, project_id)
        return self.get(project_id, template_id)

    def update(self, project_id: str, template_id: str, **fields: Any) -> ProductionTemplate:
        unknown = set(fields) - set(EDITABLE_FIELDS)
        if unknown:
            raise TemplateValidationError({"_": "Champ inconnu : " + ", ".join(sorted(unknown))})
        current = self.get(project_id, template_id)  # lève TemplateNotFound si projet/id ne correspondent pas
        merged = {key: getattr(current, key) for key in EDITABLE_FIELDS}
        merged.update(fields)
        clean = validate_fields(merged)
        values = self._values(clean)
        with self._connect() as connection:
            self._check_name_unique(connection, project_id, clean["name"], template_id)
            connection.execute(
                "UPDATE production_templates SET " + ", ".join(f"{key} = ?" for key in EDITABLE_FIELDS)
                + ", updated_at = ? WHERE id = ? AND project_id = ?",
                (*(values[key] for key in EDITABLE_FIELDS), self._clock(), template_id, project_id),
            )
        logger.info("template modifié : %s", template_id)
        return self.get(project_id, template_id)

    def _set_active(self, project_id: str, template_id: str, active: bool) -> ProductionTemplate:
        self.get(project_id, template_id)  # lève TemplateNotFound : jamais d'effet croisé entre projets
        with self._connect() as connection:
            connection.execute(
                "UPDATE production_templates SET is_active = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                (int(active), self._clock(), template_id, project_id),
            )
        return self.get(project_id, template_id)

    def deactivate(self, project_id: str, template_id: str) -> ProductionTemplate:
        """Retire le template des choix proposés, SANS le supprimer : les productions déjà créées gardent
        leur ``template_snapshot`` et restent donc reproductibles (la suppression définitive est #114)."""
        return self._set_active(project_id, template_id, False)

    def restore(self, project_id: str, template_id: str) -> ProductionTemplate:
        return self._set_active(project_id, template_id, True)

    def duplicate_to_project(self, project_id: str, template_id: str, target_project_id: str,
                             *, name: str | None = None) -> ProductionTemplate:
        """Copie ce template vers un AUTRE projet (ou le même, sous un autre nom).

        Seule passerelle entre projets, et elle est explicite : rien n'est jamais partagé implicitement.
        La copie est indépendante — modifier l'original ne touche pas la copie. Le nom est suffixé si le
        projet cible en a déjà un identique, plutôt que d'échouer sur une collision que l'utilisateur n'a
        pas provoquée.
        """
        source = self.get(project_id, template_id)
        wanted = " ".join((name or source.name).split())
        existing = {template.name.casefold() for template in self.list_for_project(target_project_id)}
        candidate, suffix = wanted, 2
        while candidate.casefold() in existing:
            candidate = f"{wanted} ({suffix})"[:NAME_MAX]
            suffix += 1
        return self.create(target_project_id, name=candidate, description=source.description,
                           payload=copy.deepcopy(source.payload), is_active=source.is_active)
