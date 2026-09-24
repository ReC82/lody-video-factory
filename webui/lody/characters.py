"""Personnages récurrents d'un projet : modèle, validation et dépôt SQLite (epic #29, ticket #30).

Entièrement facultatif : un projet sans personnage fonctionne exactement comme avant ce ticket. Aucun écran
Streamlit, aucun brief, aucun storyboard, aucun payload de génération ne lit cette table ici — ce sera l'objet
de tickets ultérieurs (#32 pour l'administration, #34+ pour la sélection). Aucune clé API (voir
``secrets_guard`` : appliqué à tous les champs texte).

Image de référence facultative (#38) : ``reference_image`` porte une référence de fichier déjà validée par
``lody.reference_images`` (jamais des octets bruts). Volontairement HORS de ``EDITABLE_FIELDS`` — donc hors de
``validate_fields``/``update`` génériques et de l'import/export JSON (#44/#57) : accepter cette valeur depuis
un JSON arbitraire reviendrait à faire confiance à un chemin non validé. Seuls ``set_reference_image`` et
``clear_reference_image`` (plus bas) peuvent la modifier.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import catalog, db, reference_images
from lody.secrets_guard import SECRET_MESSAGE, find_secret_path

logger = logging.getLogger("lody.characters")

NAME_MIN, NAME_MAX = 2, 80
ROLE_MAX = 60
PERSONALITY_MAX = 500
VISUAL_DESCRIPTION_MAX = 500
REFERENCE_PROMPT_MAX = 500
SPEECH_STYLE_MAX = 300
VOICE_PROVIDER_MAX = 40
VOICE_NAME_MAX = 80
EXTERNAL_VOICE_ID_MAX = 100
PERMANENT_ELEMENTS_MAX = 500
CONTINUITY_NOTES_MAX = 500

EDITABLE_FIELDS = (
    "name", "role", "personality", "visual_description", "reference_prompt", "speech_style",
    "voice_provider", "voice_name", "external_voice_id", "permanent_elements", "continuity_notes",
    "is_primary", "is_active",
)
DEFAULTS: dict[str, Any] = {
    "role": "", "personality": "", "visual_description": "", "reference_prompt": "", "speech_style": "",
    "voice_provider": "", "voice_name": "", "external_voice_id": "", "permanent_elements": "",
    "continuity_notes": "", "is_primary": False, "is_active": True,
}


class CharacterError(Exception):
    """Erreur de base de la couche personnages."""


class CharacterValidationError(CharacterError):
    """Données invalides : ``errors`` associe un champ à un message lisible."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


class CharacterNotFound(CharacterError):
    pass


@dataclass
class Character:
    id: str
    project_id: str
    name: str
    role: str
    personality: str
    visual_description: str
    reference_prompt: str
    speech_style: str
    voice_provider: str
    voice_name: str
    external_voice_id: str
    permanent_elements: str
    continuity_notes: str
    reference_image: str
    is_primary: bool
    is_active: bool
    created_at: str
    updated_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Nettoie et valide un jeu complet de champs. Lève ``CharacterValidationError``."""
    errors: dict[str, str] = {}
    clean: dict[str, Any] = {}

    def text(key: str, maximum: int, too_long: str, required: str | None = None) -> None:
        raw = fields.get(key, DEFAULTS.get(key, ""))
        value = " ".join(str(raw or "").split())
        if required and not value:
            errors[key] = required
        elif len(value) > maximum:
            errors[key] = too_long
        clean[key] = value

    text("name", NAME_MAX, f"Le nom est trop long ({NAME_MAX} caractères maximum).",
         "Donne un nom au personnage.")
    if "name" not in errors and len(clean["name"]) < NAME_MIN:
        errors["name"] = f"Le nom doit faire au moins {NAME_MIN} caractères."
    text("role", ROLE_MAX, f"Le rôle est trop long ({ROLE_MAX} caractères maximum).")
    text("personality", PERSONALITY_MAX, f"La personnalité est trop longue ({PERSONALITY_MAX} caractères maximum).")
    text("visual_description", VISUAL_DESCRIPTION_MAX,
         f"La description visuelle est trop longue ({VISUAL_DESCRIPTION_MAX} caractères maximum).")
    text("reference_prompt", REFERENCE_PROMPT_MAX,
         f"Le prompt de référence est trop long ({REFERENCE_PROMPT_MAX} caractères maximum).")
    text("speech_style", SPEECH_STYLE_MAX, f"Le style d’expression est trop long ({SPEECH_STYLE_MAX} caractères maximum).")
    text("voice_name", VOICE_NAME_MAX, f"Le nom de la voix est trop long ({VOICE_NAME_MAX} caractères maximum).")
    text("external_voice_id", EXTERNAL_VOICE_ID_MAX,
         f"L’identifiant de voix est trop long ({EXTERNAL_VOICE_ID_MAX} caractères maximum).")
    text("permanent_elements", PERMANENT_ELEMENTS_MAX,
         f"Les éléments permanents sont trop longs ({PERMANENT_ELEMENTS_MAX} caractères maximum).")
    text("continuity_notes", CONTINUITY_NOTES_MAX,
         f"Les notes de continuité sont trop longues ({CONTINUITY_NOTES_MAX} caractères maximum).")

    raw_provider = " ".join(str(fields.get("voice_provider", "") or "").split())
    if not raw_provider:
        clean["voice_provider"] = ""
    else:
        # #63 : accepte aussi le libellé affiché dans l'interface (ex. « ElevenLabs »), pas seulement
        # l'identifiant technique (« elevenlabs ») — normalisation exacte et insensible à la casse
        # uniquement, voir catalog.normalize(). Jamais d'ambiguïté : chaque option est unique dans ce
        # catalogue.
        normalized = catalog.normalize(catalog.VOICE_PROVIDERS, raw_provider)
        if normalized is None:
            errors["voice_provider"] = (
                "Choisis un fournisseur de voix parmi : "
                f"{catalog.allowed_values_text(catalog.VOICE_PROVIDERS)} (ou laisse vide)."
            )
            clean["voice_provider"] = raw_provider
        else:
            clean["voice_provider"] = normalized

    clean["is_primary"] = bool(fields.get("is_primary", DEFAULTS["is_primary"]))
    clean["is_active"] = bool(fields.get("is_active", DEFAULTS["is_active"]))

    # Garde-fou secrets : tout champ texte. C'est justement le risque nommé par le ticket #30 pour
    # external_voice_id (confondu avec une clé d'API), étendu par cohérence à tous les champs texte.
    for key in ("name", "role", "personality", "visual_description", "reference_prompt", "speech_style",
               "voice_provider", "voice_name", "external_voice_id", "permanent_elements", "continuity_notes"):
        if key not in errors and find_secret_path(clean.get(key)):
            errors[key] = SECRET_MESSAGE

    if errors:
        raise CharacterValidationError(errors)
    return clean


class CharacterRepository:
    """Accès SQLite aux personnages. Une connexion courte par opération (thread-safe)."""

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
    def _from_row(row: sqlite3.Row) -> Character:
        data = dict(row)
        data["is_primary"] = bool(data["is_primary"])
        data["is_active"] = bool(data["is_active"])
        return Character(**data)

    @staticmethod
    def _values(clean: dict[str, Any]) -> dict[str, Any]:
        values = {key: clean[key] for key in EDITABLE_FIELDS}
        values["is_primary"] = int(clean["is_primary"])
        values["is_active"] = int(clean["is_active"])
        return values

    # -- lecture ------------------------------------------------------------------------------------------------
    def get(self, project_id: str, character_id: str) -> Character:
        """Lève ``CharacterNotFound`` si l'identifiant n'existe pas OU appartient à un autre projet : c'est le
        même refus dans les deux cas, pour ne jamais laisser deviner qu'un personnage existe ailleurs."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM characters WHERE id = ? AND project_id = ?", (character_id, project_id)
            ).fetchone()
        if row is None:
            raise CharacterNotFound(character_id)
        return self._from_row(row)

    def list_for_project(self, project_id: str, *, include_inactive: bool = True) -> list[Character]:
        """Tri stable : personnage principal actif d'abord, puis nom (voir le critère d'acceptation #30)."""
        query = "SELECT * FROM characters WHERE project_id = ?"
        params: list[Any] = [project_id]
        if not include_inactive:
            query += " AND is_active = 1"
        query += " ORDER BY is_active DESC, is_primary DESC, name COLLATE NOCASE"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._from_row(row) for row in rows]

    def count_for_project(self, project_id: str) -> int:
        with self._connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM characters WHERE project_id = ?", (project_id,)
            ).fetchone()[0]

    # -- écriture -----------------------------------------------------------------------------------------------
    def _check_name_unique(self, connection: sqlite3.Connection, project_id: str, name: str,
                           exclude_id: str | None) -> None:
        """Unique PAR PROJET seulement (même nom permis dans un autre projet) : ``casefold()`` applicatif,
        comme ``ProjectRepository`` — plus correct que ``COLLATE NOCASE`` (ASCII seul) pour l'accentuation."""
        wanted = name.casefold()
        for row in connection.execute("SELECT id, name FROM characters WHERE project_id = ?", (project_id,)):
            if row["id"] != exclude_id and row["name"].casefold() == wanted:
                raise CharacterValidationError({"name": "Un personnage porte déjà ce nom dans ce projet."})

    def create(self, project_id: str, **fields: Any) -> Character:
        clean = validate_fields({**DEFAULTS, **fields})
        now = self._clock()
        character_id = f"chr_{uuid.uuid4().hex[:12]}"
        values = self._values(clean)
        with self._connect() as connection:
            if not self._project_exists(connection, project_id):
                raise CharacterValidationError({"project_id": "Ce projet n’existe pas."})
            self._check_name_unique(connection, project_id, clean["name"], None)
            connection.execute(
                "INSERT INTO characters (id, project_id, created_at, updated_at, "
                + ", ".join(EDITABLE_FIELDS)
                + ") VALUES (?, ?, ?, ?, "
                + ", ".join("?" for _ in EDITABLE_FIELDS)
                + ")",
                (character_id, project_id, now, now, *(values[key] for key in EDITABLE_FIELDS)),
            )
        logger.info("personnage créé : %s (projet %s)", character_id, project_id)
        return self.get(project_id, character_id)

    def update(self, project_id: str, character_id: str, **fields: Any) -> Character:
        unknown = set(fields) - set(EDITABLE_FIELDS)
        if unknown:
            raise CharacterValidationError({"_": "Champ inconnu : " + ", ".join(sorted(unknown))})
        current = self.get(project_id, character_id)  # lève CharacterNotFound si projet/id ne correspondent pas
        merged = {key: getattr(current, key) for key in EDITABLE_FIELDS}
        merged.update(fields)
        clean = validate_fields(merged)
        values = self._values(clean)
        with self._connect() as connection:
            self._check_name_unique(connection, project_id, clean["name"], character_id)
            connection.execute(
                "UPDATE characters SET " + ", ".join(f"{key} = ?" for key in EDITABLE_FIELDS)
                + ", updated_at = ? WHERE id = ? AND project_id = ?",
                (*(values[key] for key in EDITABLE_FIELDS), self._clock(), character_id, project_id),
            )
        logger.info("personnage modifié : %s", character_id)
        return self.get(project_id, character_id)

    def _set_active(self, project_id: str, character_id: str, active: bool) -> Character:
        self.get(project_id, character_id)  # lève CharacterNotFound : jamais d'effet croisé entre projets
        with self._connect() as connection:
            connection.execute(
                "UPDATE characters SET is_active = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                (int(active), self._clock(), character_id, project_id),
            )
        logger.info("personnage %s : %s", character_id, "activé" if active else "désactivé")
        return self.get(project_id, character_id)

    def activate(self, project_id: str, character_id: str) -> Character:
        return self._set_active(project_id, character_id, True)

    def deactivate(self, project_id: str, character_id: str) -> Character:
        """Désactivation, jamais de suppression destructive dans ce MVP (voir le critère d'acceptation #30) :
        l'historique (snapshots de productions passées) reste cohérent même si le personnage n'est plus proposé."""
        return self._set_active(project_id, character_id, False)

    # -- image de référence (#38) ---------------------------------------------------------------------------------
    def set_reference_image(self, project_id: str, character_id: str, data: bytes) -> Character:
        """Valide puis enregistre l'image de référence de CE personnage. Ne touche à aucun autre champ.

        Lève ``CharacterNotFound`` si l'identifiant n'existe pas ou appartient à un autre projet (jamais
        d'écriture croisée) ; ``CharacterValidationError`` si l'image est invalide (voir
        ``reference_images.save`` : taille, type réel, dimensions)."""
        self.get(project_id, character_id)
        try:
            stored = reference_images.save(project_id, "characters", character_id, data)
        except reference_images.ReferenceImageError as error:
            raise CharacterValidationError({"reference_image": str(error)}) from error
        with self._connect() as connection:
            connection.execute(
                "UPDATE characters SET reference_image = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                (stored.ref, self._clock(), character_id, project_id),
            )
        logger.info("image de référence enregistrée : %s", character_id)
        return self.get(project_id, character_id)

    def clear_reference_image(self, project_id: str, character_id: str) -> Character:
        """Retire la référence enregistrée. Le fichier reste sur disque (voir le docstring de
        ``lody.reference_images`` : jamais de suppression destructive, une production déjà préparée peut
        toujours résoudre l'ancienne référence copiée dans son snapshot)."""
        self.get(project_id, character_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE characters SET reference_image = '', updated_at = ? WHERE id = ? AND project_id = ?",
                (self._clock(), character_id, project_id),
            )
        logger.info("image de référence retirée : %s", character_id)
        return self.get(project_id, character_id)
