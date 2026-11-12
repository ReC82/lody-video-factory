"""Brouillon persistant de la page « Nouvelle production » (#189) : une saisie par projet, jamais perdue.

Streamlit retire de ``st.session_state`` les valeurs des widgets qui ne sont plus affichés : un aller-retour
vers « Personnages » (choix de la voix) ou un rechargement du navigateur vidait le sujet, le script, la
sélection et le storyboard en attente. Ce module enregistre cette saisie en SQLite, projet par projet.

Ce n'est PAS une production : aucune estimation, aucun coût, aucun appel à un fournisseur. Le contenu est
strictement borné (longueurs du formulaire, IDs, storyboard ``lody.animation/1`` validé), versionné
(``DRAFT_VERSION``) et passé au garde-fou secrets. Aucun asset ni fichier n'est recopié : le storyboard
ne référence que des versions d'assets déjà présentes dans le catalogue du projet.

Un brouillon illisible, d'une version inconnue ou invalide n'empêche jamais d'ouvrir la page : il est ignoré
(``get`` renvoie ``None``) et sera remplacé par la prochaine saisie.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import db
from lody.animation.contract import AnimationValidationError, validate_document
from lody.secrets_guard import find_secret_path

logger = logging.getLogger("lody.compose_drafts")

DRAFT_VERSION = 1
REQUEST_MAX = 2000  # mêmes limites que les champs de view_production
SCRIPT_MAX = 8000
ID_MAX = 64
MAX_CHARACTERS = 50
STORYBOARD_MAX_BYTES = 1_000_000
LABEL_MAX = 200
STORYBOARD_SOURCES = ("demo150", "upload")


class DraftValidationError(ValueError):
    """Contenu de brouillon refusé (jamais enregistré)."""


@dataclass
class ComposeDraft:
    """Saisie de « Nouvelle production » pour UN projet (voir ``to_payload`` pour le JSON stocké)."""

    request: str = ""
    script: str = ""
    character_ids: list[str] = field(default_factory=list)
    location_id: str | None = None
    demo: bool = False
    storyboard: dict[str, Any] | None = None  # {"source", "label", "document"} ou None
    prepared_id: str | None = None  # estimation déjà préparée (revalidée par fresh_draft à chaque affichage)
    updated_at: str = ""

    def is_empty(self) -> bool:
        return not (self.request.strip() or self.script.strip() or self.character_ids or self.location_id
                    or self.demo or self.storyboard)

    def to_payload(self) -> dict[str, Any]:
        return {"version": DRAFT_VERSION, "request": self.request, "script": self.script,
                "character_ids": list(self.character_ids), "location_id": self.location_id, "demo": self.demo,
                "storyboard": self.storyboard, "prepared_id": self.prepared_id}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _identifier(value: Any, path: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > ID_MAX or not value.isprintable():
        raise DraftValidationError(f"{path} : identifiant invalide.")
    return value


def validate_storyboard(value: Any) -> dict[str, Any] | None:
    """``{"source": demo150|upload, "label": str, "document": lody.animation/1}`` validé, ou ``None``."""
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("source") not in STORYBOARD_SOURCES:
        raise DraftValidationError("storyboard : source inconnue.")
    label = value.get("label") or ""
    if not isinstance(label, str) or len(label) > LABEL_MAX:
        raise DraftValidationError("storyboard : libellé invalide.")
    try:
        document = validate_document(value.get("document"))
    except AnimationValidationError as error:
        raise DraftValidationError(f"storyboard : {error}") from None
    if len(json.dumps(document, ensure_ascii=False).encode("utf-8")) > STORYBOARD_MAX_BYTES:
        raise DraftValidationError("storyboard : document trop volumineux pour un brouillon.")
    return {"source": value["source"], "label": label, "document": document}


def validate_payload(payload: Any) -> ComposeDraft:
    """JSON stocké → ``ComposeDraft`` propre. Lève ``DraftValidationError`` (version inconnue comprise)."""
    if not isinstance(payload, dict):
        raise DraftValidationError("brouillon : objet JSON attendu.")
    if payload.get("version") != DRAFT_VERSION:
        raise DraftValidationError(f"brouillon : version {payload.get('version')!r} non prise en charge.")
    request, script = payload.get("request") or "", payload.get("script") or ""
    if not isinstance(request, str) or len(request) > REQUEST_MAX:
        raise DraftValidationError("sujet : texte invalide ou trop long.")
    if not isinstance(script, str) or len(script) > SCRIPT_MAX:
        raise DraftValidationError("script : texte invalide ou trop long.")
    raw_ids = payload.get("character_ids") or []
    if not isinstance(raw_ids, list) or len(raw_ids) > MAX_CHARACTERS:
        raise DraftValidationError("personnages : liste invalide.")
    character_ids: list[str] = []
    for index, value in enumerate(raw_ids):
        clean = _identifier(value, f"personnages[{index}]")
        if clean and clean not in character_ids:
            character_ids.append(clean)
    draft = ComposeDraft(
        request=request, script=script, character_ids=character_ids,
        location_id=_identifier(payload.get("location_id"), "lieu"), demo=payload.get("demo") is True,
        storyboard=validate_storyboard(payload.get("storyboard")),
        prepared_id=_identifier(payload.get("prepared_id"), "estimation"),
    )
    # Garde-fou secrets sur les seuls textes libres (motifs de clés connus : un script peut contenir de longs
    # identifiants techniques sans être une clé) ; le storyboard est déjà borné par son contrat.
    if find_secret_path({"request": draft.request, "script": draft.script}, opaque=False):
        raise DraftValidationError("brouillon : un champ ressemble à une clé secrète, il n'est pas enregistré.")
    return draft


class ComposeDraftRepository:
    """Une ligne par projet dans ``production_compose_drafts`` ; jamais de lecture croisée entre projets."""

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

    def get(self, project_id: str) -> ComposeDraft | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, updated_at FROM production_compose_drafts WHERE project_id = ?", (project_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            draft = validate_payload(json.loads(row["payload"]))
        except (ValueError, DraftValidationError) as error:
            logger.warning("brouillon du projet %s ignoré : %s", project_id, error)
            return None
        draft.updated_at = row["updated_at"]
        return draft

    def save(self, project_id: str, draft: ComposeDraft) -> ComposeDraft:
        """Valide puis enregistre (création ou remplacement). Sans écriture si rien n'a changé."""
        clean = validate_payload(draft.to_payload())
        payload = json.dumps(clean.to_payload(), ensure_ascii=False, sort_keys=True)
        now = self._clock()
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise LookupError(f"projet inconnu : {project_id}")
            row = connection.execute(
                "SELECT payload, updated_at FROM production_compose_drafts WHERE project_id = ?", (project_id,),
            ).fetchone()
            if row is not None and row["payload"] == payload:
                clean.updated_at = row["updated_at"]
                return clean
            connection.execute(
                "INSERT INTO production_compose_drafts (project_id, version, payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (project_id) DO UPDATE SET "
                "version = excluded.version, payload = excluded.payload, updated_at = excluded.updated_at",
                (project_id, DRAFT_VERSION, payload, now, now),
            )
        clean.updated_at = now
        return clean

    def delete(self, project_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM production_compose_drafts WHERE project_id = ?", (project_id,))
