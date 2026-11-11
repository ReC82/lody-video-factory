"""Lexique de prononciation d'un projet (#162) : comment la VOIX lit un sigle, sans toucher à l'écran.

Constat : les voix épellent les sigles de façon heurtée (« S-D-I », « H-D-M-I »). Une entrée du lexique
associe à une **forme écrite** exacte (``SDI``, ``HDMI``, ``3G-SDI``) :

* ``spoken``   — la **lecture demandée**, en graphie parlée (« èss-dé-i », « hachdémi »...) : c'est CE texte
  qui part au moteur de voix, jamais les sous-titres ;
* ``phonetic`` — une transcription phonétique propre au fournisseur, **conservée mais jamais envoyée** :
  les modèles ElevenLabs utilisés en production (``eleven_multilingual_v2``, ``eleven_v3``) n'ont pas de
  support confirmé des balises de phonèmes. Elle documente l'intention, en attendant une preuve d'écoute ;
* ``subtitle`` — la forme affichée en sous-titre (vide = forme écrite, le cas normal : les sigles restent
  typographiquement exacts à l'écran).

Portée : **par projet**, filtrable par langue (``fr`` couvre ``fr-FR``) et par personnage (une entrée d'un
personnage l'emporte sur l'entrée générale de même forme écrite).

**Aucune prononciation n'est figée sans écoute** : une entrée n'est appliquée en production qu'une fois
**validée** (``validated_at``), ce que l'interface ne propose qu'après un aperçu audio. Toute modification
de la forme écrite ou de la lecture annule la validation. Lody ne fournit aucune lecture par défaut : les
variantes proposées par ``candidate_readings`` sont des pistes à écouter, jamais appliquées d'office.

La substitution est sûre : sensible à la casse (un sigle est exact), limites de mot strictes (``SDI`` ne
touche ni ``3G-SDI`` ni ``SDIs``), expressions les plus longues d'abord, tirets typographiques reconnus.

Contrôle éditorial (``audit``) : sigles non couverts par le lexique, entrées encore à écouter (bloquant :
aucun MP4 avant validation des essais oraux) et surenchère d'acronymes (règle « un concept par Short »,
voir ``docs/lody-pronunciation.md``).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import db
from lody.secrets_guard import SECRET_MESSAGE, find_secret_path

logger = logging.getLogger("lody.pronunciation")

WRITTEN_MAX = 40
SPOKEN_MAX = 120
NOTES_MAX = 300
# Règle éditoriale « un concept par Short » : au-delà, le texte empile les sigles (avertissement).
MAX_DISTINCT_ACRONYMS = 3
EDITABLE_FIELDS: tuple[str, ...] = ("written", "spoken", "phonetic", "subtitle", "language", "character_id", "notes")
# Champs dont la modification annule la validation (ce qu'on a écouté n'est plus ce qui sera prononcé).
_HEARD_FIELDS = ("written", "spoken", "language", "character_id")
_LANGUAGE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
_DASHES = "-‐‑‒–"
_DASH_CLASS = "[" + re.escape(_DASHES) + "]"
# Un sigle : jeton sans minuscule, au moins une majuscule, au moins deux caractères (SDI, 4K, 12G-SDI).
_ACRONYM = re.compile(r"(?<![\w" + re.escape(_DASHES) + r"])[0-9A-Z]*[A-Z][0-9A-Z]*(?:" + _DASH_CLASS
                      + r"[0-9A-Z]+)*(?![\w])")
_FORBIDDEN_SPOKEN = re.compile(r"[\[\]{}<>]")


class PronunciationError(Exception):
    """Erreur de base de la couche lexique."""


class PronunciationValidationError(PronunciationError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(errors.values()))
        self.errors = errors


class EntryNotFound(PronunciationError):
    pass


@dataclass
class LexiconEntry:
    id: str = ""
    project_id: str = ""
    written: str = ""
    spoken: str = ""
    phonetic: str = ""
    subtitle: str = ""
    language: str = ""
    character_id: str = ""
    notes: str = ""
    validated_at: str = ""
    validated_with: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def validated(self) -> bool:
        return bool(self.validated_at)

    @property
    def display(self) -> str:
        """Forme affichée à l'écran : jamais la lecture parlée."""
        return self.subtitle or self.written

    def to_snapshot(self) -> dict[str, str]:
        """Ce qui est figé dans l'instantané d'une production (reproductible, sans dépendre de la table)."""
        return {key: getattr(self, key) for key in ("written", "spoken", "subtitle", "language", "character_id")}

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> LexiconEntry:
        # langue et personnage déjà filtrés au moment du gel : jamais re-filtrés à la relecture.
        return cls(**{key: str(data.get(key) or "") for key in ("written", "spoken", "subtitle")},
                   validated_at="snapshot")


@dataclass(frozen=True)
class Rendering:
    """Deux textes issus du MÊME script : l'un pour la voix, l'autre pour l'écran."""

    spoken: str
    subtitle: str
    applied: tuple[str, ...] = ()


@dataclass
class EditorialAudit:
    acronyms: list[str] = field(default_factory=list)      # sigles distincts, dans l'ordre d'apparition
    uncovered: list[str] = field(default_factory=list)     # sans entrée de lexique (avertissement)
    pending: list[str] = field(default_factory=list)       # entrée présente mais jamais validée (bloquant)
    overloaded: bool = False                               # surenchère d'acronymes (avertissement)

    @property
    def blocking(self) -> bool:
        return bool(self.pending)

    def messages(self) -> list[tuple[str, bool]]:
        """Messages lisibles en français, ``(texte, bloquant)``."""
        out: list[tuple[str, bool]] = []
        if self.pending:
            out.append(("Prononciation à écouter et valider avant toute génération : " + ", ".join(self.pending)
                        + ". Aucune vidéo ne part tant que ces essais oraux ne sont pas validés.", True))
        if self.uncovered:
            out.append(("Sigles sans prononciation enregistrée (la voix risque de les épeler lettre par lettre) : "
                        + ", ".join(self.uncovered) + ".", False))
        if self.overloaded:
            out.append((f"{len(self.acronyms)} sigles différents dans ce texte : un concept par Short, chaque terme "
                        "nécessaire expliqué dans la même phrase ou par une illustration.", False))
        return out


# -- logique pure ------------------------------------------------------------------------------------------
def language_matches(entry_language: str, language: str) -> bool:
    """``""`` couvre toutes les langues ; ``fr`` couvre ``fr-FR`` ; ``fr-FR`` ne couvre que ``fr-FR``."""
    if not entry_language:
        return True
    if not language:
        return False
    if "-" in entry_language:
        return entry_language.lower() == language.lower()
    return language.lower().split("-")[0] == entry_language.lower()


def _key(written: str) -> str:
    return re.sub(_DASH_CLASS, "-", written)


def applicable(entries: Iterable[LexiconEntry], language: str, character_id: str = "", *,
               validated_only: bool = True) -> list[LexiconEntry]:
    """Entrées valables pour cette langue et ce personnage, une seule par forme écrite (le personnage
    l'emporte sur la règle générale), triées de la plus longue à la plus courte."""
    chosen: dict[str, LexiconEntry] = {}
    for entry in entries:
        if validated_only and not entry.validated:
            continue
        if not language_matches(entry.language, language):
            continue
        if entry.character_id and entry.character_id != character_id:
            continue
        key = _key(entry.written)
        current = chosen.get(key)
        if current is None or (entry.character_id and not current.character_id) or (
                bool(entry.character_id) == bool(current.character_id) and len(entry.language) > len(current.language)):
            chosen[key] = entry
    return sorted(chosen.values(), key=lambda item: (-len(item.written), item.written))


def _pattern(writtens: Sequence[str]) -> re.Pattern[str] | None:
    if not writtens:
        return None
    alternatives = []
    for written in sorted(set(writtens), key=lambda value: (-len(value), value)):
        parts = [re.escape(part) for part in re.split(_DASH_CLASS, written)]
        alternatives.append(_DASH_CLASS.join(parts))
    return re.compile(r"(?<![\w" + re.escape(_DASHES) + r"])(?:" + "|".join(alternatives) + r")(?![\w]|"
                      + _DASH_CLASS + r"\w)")


def render(text: str, entries: Sequence[LexiconEntry]) -> Rendering:
    """Applique les entrées DÉJÀ sélectionnées (voir ``applicable``) en un seul passage : une lecture
    substituée n'est jamais re-substituée."""
    by_key = {_key(entry.written): entry for entry in entries}
    pattern = _pattern([entry.written for entry in entries])
    if not text or pattern is None:
        return Rendering(text, text)
    applied: list[str] = []

    def entry_of(match: re.Match[str]) -> LexiconEntry:
        entry = by_key[_key(match.group(0))]
        if entry.written not in applied:
            applied.append(entry.written)
        return entry

    spoken = pattern.sub(lambda match: entry_of(match).spoken, text)
    # Sous-titres : le texte d'origine EXACT (tiret insécable compris), sauf forme de sous-titre explicite.
    subtitle = pattern.sub(lambda match: by_key[_key(match.group(0))].subtitle or match.group(0), text)
    return Rendering(spoken, subtitle, tuple(applied))


def find_acronyms(text: str) -> list[str]:
    seen: list[str] = []
    for match in _ACRONYM.finditer(text or ""):
        token = match.group(0)
        if len(token) >= 2 and token not in seen:
            seen.append(token)
    return seen


def audit(text: str, entries: Iterable[LexiconEntry], language: str, character_id: str = "") -> EditorialAudit:
    """Contrôle éditorial avant génération, sans aucun appel réseau."""
    every = applicable(entries, language, character_id, validated_only=False)
    validated = {_key(entry.written) for entry in every if entry.validated}
    known = {_key(entry.written): entry for entry in every}
    result = EditorialAudit(acronyms=find_acronyms(text))
    pattern = _pattern([entry.written for entry in every])
    present = {_key(match.group(0)) for match in pattern.finditer(text or "")} if pattern else set()
    for key in sorted(present):
        if key not in validated:
            result.pending.append(known[key].written)
    for token in result.acronyms:
        if _key(token) not in known:
            result.uncovered.append(token)
    result.overloaded = len(result.acronyms) > MAX_DISTINCT_ACRONYMS
    return result


def preview_sentence(entry: LexiconEntry, language: str = "fr") -> str:
    """Phrase courte d'aperçu, ce que la voix dira réellement (rythme, liaisons, pauses en contexte)."""
    if language.lower().startswith("fr") or not language:
        return f"On branche le câble {entry.spoken}. Voilà : {entry.spoken}, c'est simple."
    return f"Plug in the {entry.spoken} cable. That's it: {entry.spoken}, simple."


def candidate_readings(written: str) -> list[str]:
    """Pistes de graphie parlée À ÉCOUTER (français), jamais appliquées d'office : épellation liée par
    traits d'union (rythme continu, sans lettres disjointes) et forme compacte. Chiffres laissés tels quels."""
    letters = {"A": "a", "B": "bé", "C": "cé", "D": "dé", "E": "e", "F": "èf", "G": "gé", "H": "ache", "I": "i",
               "J": "ji", "K": "ka", "L": "èl", "M": "èm", "N": "èn", "O": "o", "P": "pé", "Q": "ku", "R": "èr",
               "S": "èss", "T": "té", "U": "u", "V": "vé", "W": "double-vé", "X": "ix", "Y": "i-grec", "Z": "zèd"}
    groups = re.split(_DASH_CLASS, written.strip())
    joined, compact = [], []
    for group in groups:
        syllables = [letters.get(char, char) for char in group if char.strip()]
        joined.append("-".join(syllables))
        compact.append("".join(syllables))
    readings = [" ".join(joined), " ".join(compact)]
    return [reading for index, reading in enumerate(readings) if reading and reading not in readings[:index]]


# -- validation des champs ---------------------------------------------------------------------------------
def validate_fields(fields: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    clean = {key: " ".join(str(fields.get(key) or "").split()) for key in EDITABLE_FIELDS}
    if not clean["written"]:
        errors["written"] = "Indique la forme écrite (ex. SDI)."
    elif len(clean["written"]) > WRITTEN_MAX:
        errors["written"] = f"La forme écrite est trop longue ({WRITTEN_MAX} caractères maximum)."
    if not clean["spoken"]:
        errors["spoken"] = "Indique la lecture demandée (comment la voix doit le dire)."
    elif len(clean["spoken"]) > SPOKEN_MAX:
        errors["spoken"] = f"La lecture est trop longue ({SPOKEN_MAX} caractères maximum)."
    elif _FORBIDDEN_SPOKEN.search(clean["spoken"]):
        errors["spoken"] = "La lecture ne peut contenir ni crochets, ni accolades, ni chevrons."
    if len(clean["phonetic"]) > SPOKEN_MAX:
        errors["phonetic"] = f"La transcription phonétique est trop longue ({SPOKEN_MAX} caractères maximum)."
    if len(clean["subtitle"]) > WRITTEN_MAX:
        errors["subtitle"] = f"La forme de sous-titre est trop longue ({WRITTEN_MAX} caractères maximum)."
    if clean["language"] and not _LANGUAGE.match(clean["language"]):
        errors["language"] = "Langue attendue au format « fr » ou « fr-FR » (vide = toutes les langues)."
    if len(clean["notes"]) > NOTES_MAX:
        errors["notes"] = f"Les notes sont trop longues ({NOTES_MAX} caractères maximum)."
    for key in EDITABLE_FIELDS:
        if key not in errors and clean[key] and find_secret_path(clean[key]):
            errors[key] = SECRET_MESSAGE
    if errors:
        raise PronunciationValidationError(errors)
    return clean


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- dépôt SQLite ------------------------------------------------------------------------------------------
class PronunciationRepository:
    """Accès SQLite au lexique. Une connexion courte par opération (thread-safe)."""

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
    def _from_row(row: sqlite3.Row) -> LexiconEntry:
        return LexiconEntry(**{key: row[key] for key in row.keys()})

    def _check_unique(self, connection: sqlite3.Connection, project_id: str, clean: dict[str, str],
                      exclude_id: str | None) -> None:
        for row in connection.execute("SELECT * FROM pronunciation_lexicon WHERE project_id = ?", (project_id,)):
            if row["id"] == exclude_id:
                continue
            if (_key(row["written"]) == _key(clean["written"]) and row["language"] == clean["language"]
                    and row["character_id"] == clean["character_id"]):
                raise PronunciationValidationError(
                    {"written": "Cette expression a déjà une prononciation pour cette langue et ce personnage."})

    def get(self, project_id: str, entry_id: str) -> LexiconEntry:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM pronunciation_lexicon WHERE id = ? AND project_id = ?",
                                     (entry_id, project_id)).fetchone()
        if row is None:
            raise EntryNotFound(entry_id)
        return self._from_row(row)

    def list_for_project(self, project_id: str) -> list[LexiconEntry]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM pronunciation_lexicon WHERE project_id = ? "
                                      "ORDER BY written COLLATE NOCASE, language, character_id", (project_id,)).fetchall()
        return [self._from_row(row) for row in rows]

    def create(self, project_id: str, **fields: Any) -> LexiconEntry:
        clean = validate_fields(fields)
        now = self._clock()
        entry_id = f"pron_{uuid.uuid4().hex[:12]}"
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise PronunciationValidationError({"project_id": "Ce projet n’existe pas."})
            self._check_unique(connection, project_id, clean, None)
            connection.execute(
                "INSERT INTO pronunciation_lexicon (id, project_id, created_at, updated_at, "
                + ", ".join(EDITABLE_FIELDS) + ") VALUES (?, ?, ?, ?, " + ", ".join("?" for _ in EDITABLE_FIELDS) + ")",
                (entry_id, project_id, now, now, *(clean[key] for key in EDITABLE_FIELDS)))
        logger.info("prononciation créée : %s (projet %s)", entry_id, project_id)
        return self.get(project_id, entry_id)

    def update(self, project_id: str, entry_id: str, **fields: Any) -> LexiconEntry:
        unknown = set(fields) - set(EDITABLE_FIELDS)
        if unknown:
            raise PronunciationValidationError({"_": "Champ inconnu : " + ", ".join(sorted(unknown))})
        current = self.get(project_id, entry_id)
        merged = {**{key: getattr(current, key) for key in EDITABLE_FIELDS}, **fields}
        clean = validate_fields(merged)
        heard_changed = any(clean[key] != getattr(current, key) for key in _HEARD_FIELDS)
        with self._connect() as connection:
            self._check_unique(connection, project_id, clean, entry_id)
            reset = ", validated_at = '', validated_with = ''" if heard_changed else ""
            connection.execute(
                "UPDATE pronunciation_lexicon SET " + ", ".join(f"{key} = ?" for key in EDITABLE_FIELDS)
                + reset + ", updated_at = ? WHERE id = ? AND project_id = ?",
                (*(clean[key] for key in EDITABLE_FIELDS), self._clock(), entry_id, project_id))
        return self.get(project_id, entry_id)

    def mark_validated(self, project_id: str, entry_id: str, heard_with: str) -> LexiconEntry:
        """Validation APRÈS écoute : ``heard_with`` décrit la voix et le modèle de l'aperçu entendu."""
        self.get(project_id, entry_id)
        label = " ".join(str(heard_with or "").split())[:120]
        if not label:
            raise PronunciationValidationError({"validated_with": "Indique la voix utilisée pour l'écoute."})
        with self._connect() as connection:
            connection.execute("UPDATE pronunciation_lexicon SET validated_at = ?, validated_with = ?, updated_at = ? "
                               "WHERE id = ? AND project_id = ?",
                               (self._clock(), label, self._clock(), entry_id, project_id))
        return self.get(project_id, entry_id)

    def unvalidate(self, project_id: str, entry_id: str) -> LexiconEntry:
        self.get(project_id, entry_id)
        with self._connect() as connection:
            connection.execute("UPDATE pronunciation_lexicon SET validated_at = '', validated_with = '', updated_at = ? "
                               "WHERE id = ? AND project_id = ?", (self._clock(), entry_id, project_id))
        return self.get(project_id, entry_id)

    def delete(self, project_id: str, entry_id: str) -> None:
        self.get(project_id, entry_id)
        with self._connect() as connection:
            connection.execute("DELETE FROM pronunciation_lexicon WHERE id = ? AND project_id = ?", (entry_id, project_id))


def snapshot(entries: Iterable[LexiconEntry], language: str, character_id: str = "") -> dict[str, Any]:
    """Partie ``pronunciation`` de l'instantané d'une production : entrées validées figées + formes écrites
    encore à écouter (pour bloquer la génération si le script les emploie)."""
    every = list(entries)
    validated = applicable(every, language, character_id)
    pending = [entry.written for entry in applicable(every, language, character_id, validated_only=False)
               if not entry.validated and _key(entry.written) not in {_key(item.written) for item in validated}]
    return {"entries": [entry.to_snapshot() for entry in validated], "pending": pending}


def audit_snapshot(text: str, frozen: dict[str, Any] | None) -> EditorialAudit:
    """``audit`` à partir de l'instantané figé d'une production (jamais la table, qui peut avoir changé)."""
    frozen = frozen or {}
    entries = [LexiconEntry.from_snapshot(item) for item in frozen.get("entries") or []]
    entries += [LexiconEntry(written=written) for written in frozen.get("pending") or []]
    return audit(text, entries, language="")


def render_snapshot(text: str, frozen: dict[str, Any] | None) -> Rendering:
    entries = [LexiconEntry.from_snapshot(item) for item in (frozen or {}).get("entries") or []]
    return render(text, applicable(entries, language=""))

