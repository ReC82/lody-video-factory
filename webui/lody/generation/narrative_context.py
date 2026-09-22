"""Sélection optionnelle des personnages et du lieu d'une production (epic #29, tickets #35/#36).

Contrat : au lancement, les IDs sélectionnés sont résolus dans le **projet courant** puis copiés en un
instantané immuable (``snapshot.narrative_context``), aux côtés de l'instantané des paramètres du projet
(voir ``make_snapshot``). Modifier ou désactiver ensuite un personnage ou un lieu ne change donc jamais
une production déjà préparée ou lancée — exactement le principe déjà appliqué aux paramètres du projet.

Aucune colonne SQLite dédiée : le ``snapshot`` JSON existant (v3, ``lody/db.py``) suffit déjà à porter des
données arbitraires immuables par production, et il est déjà protégé par le garde-fou secrets (``GUARDED``
dans ``generation/store.py``). Ajouter une table ne ferait que dupliquer ce mécanisme.

``render_prompt_block`` (#36) lit ce ``narrative_context`` DÉJÀ RÉSOLU (jamais les tables éditables) et
produit le texte à ajouter au prompt d'écriture du SCRIPT uniquement — voir ``generation/service.py``
(``_run``) et ``generation/mpt_connector.py`` (``script_prompt``). Aucune voix, aucun prompt d'image :
c'est volontairement hors de portée (voir tickets ultérieurs pour ces pipelines).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from lody.characters import Character, CharacterNotFound, CharacterRepository
from lody.locations import Location, LocationNotFound, LocationRepository

# Version du *format* du bloc narrative_context (indépendante de ``snapshot["version"]``, qui ne
# couvre que les paramètres du projet) : à incrémenter si sa forme change un jour.
NARRATIVE_CONTEXT_VERSION = 1

# Champs copiés dans le snapshot : identité + descriptions effectives, rien de plus (pas de champ voix
# ni de "reference_prompt", non utilisés tant qu'aucune injection dans le pipeline n'existe — voir le
# docstring du module. Ça garde le snapshot petit et évite de coupler ce ticket à des choix qui ne sont
# pas encore faits.
_CHARACTER_FIELDS = (
    "id", "name", "role", "personality", "visual_description", "speech_style",
    "permanent_elements", "continuity_notes", "is_primary",
)
_LOCATION_FIELDS = ("id", "name", "location_type", "description", "continuity_notes", "is_primary")


class NarrativeContextError(Exception):
    """Sélection invalide : identifiant inconnu, d'un autre projet, ou élément désactivé.

    Message déjà lisible par l'utilisateur (traduit en ``LaunchError`` par le service appelant).
    """


def _character_snapshot(character: Character) -> dict[str, Any]:
    return {field: getattr(character, field) for field in _CHARACTER_FIELDS}


def _location_snapshot(location: Location) -> dict[str, Any]:
    return {field: getattr(location, field) for field in _LOCATION_FIELDS}


def resolve_narrative_context(
    project_id: str,
    character_ids: Sequence[str],
    location_id: str | None,
    character_repo: CharacterRepository,
    location_repo: LocationRepository,
) -> dict[str, Any]:
    """Résout la sélection facultative en un instantané immuable.

    Renvoie ``{}`` si rien n'est sélectionné (aucun personnage, aucun lieu) : c'est le cas de toutes les
    productions historiques et de toute nouvelle production sans sélection — comportement inchangé.

    Lève ``NarrativeContextError`` si un identifiant :
    - n'existe pas, ou appartient à un autre projet que ``project_id`` (même refus dans les deux cas,
      comme ``CharacterRepository.get``/``LocationRepository.get``, pour ne rien laisser deviner) ;
    - désigne un élément désactivé (seuls les éléments actifs sont sélectionnables).
    """
    characters: list[dict[str, Any]] = []
    seen: set[str] = set()
    for character_id in character_ids:
        if not character_id or character_id in seen:
            continue  # doublon silencieusement ignoré (même personnage coché deux fois)
        seen.add(character_id)
        try:
            character = character_repo.get(project_id, character_id)
        except CharacterNotFound:
            raise NarrativeContextError(f"Personnage introuvable dans ce projet : {character_id}") from None
        if not character.is_active:
            raise NarrativeContextError(f"Ce personnage est désactivé : {character.name}")
        characters.append(_character_snapshot(character))

    location: dict[str, Any] | None = None
    if location_id:
        try:
            found = location_repo.get(project_id, location_id)
        except LocationNotFound:
            raise NarrativeContextError(f"Lieu introuvable dans ce projet : {location_id}") from None
        if not found.is_active:
            raise NarrativeContextError(f"Ce lieu est désactivé : {found.name}")
        location = _location_snapshot(found)

    if not characters and location is None:
        return {}
    return {"version": NARRATIVE_CONTEXT_VERSION, "characters": characters, "location": location}


# -- rendu dans le prompt d'écriture du script (#36) ------------------------------------------------------------

# Sous-ensemble RESTREINT de _CHARACTER_FIELDS/_LOCATION_FIELDS : seulement ce qui est utile à un SCRIPT (jamais
# la description visuelle ni les éléments permanents, réservés à un futur ticket d'injection image ; jamais
# l'identifiant technique, ni "is_primary" qui n'a de sens que pour l'écran de sélection).
_SCRIPT_CHARACTER_FIELDS = ("role", "personality", "speech_style", "continuity_notes")
_SCRIPT_LOCATION_FIELDS = ("location_type", "description", "continuity_notes")
_FIELD_LABELS = {
    "role": "rôle", "personality": "personnalité", "speech_style": "style de parole", "continuity_notes": "continuité",
    "location_type": "type", "description": "description",
}

# Borne INDÉPENDANTE de la limite globale du prompt (``script_prompt``'s ``limit``) : garantit que ce bloc ne
# peut jamais, à lui seul, dominer le budget du prompt — quel que soit le nombre de personnages sélectionnés.
NARRATIVE_BLOCK_MAX = 900
_HEADER = (
    "### Contexte narratif de cette production (instantané figé de #34/#35 — à distinguer de la demande "
    "ci-dessous, qui reste seule à définir le sujet) ###\n"
    "Utilise ces éléments seulement s’ils servent naturellement le récit : ne force pas leur présence dans "
    "chaque phrase, et n’invente aucun personnage ou lieu qui ne soit pas listé ici."
)
_FOOTER = "### Fin du contexte narratif ###"
_TRUNCATION_NOTE = "(liste tronquée pour tenir dans la limite du prompt)"


def _detail_line(item: dict[str, Any], fields: Sequence[str]) -> str:
    details = "; ".join(f"{_FIELD_LABELS[field]} : {item[field]}" for field in fields if item.get(field))
    return details


def _character_line(character: dict[str, Any]) -> str:
    details = _detail_line(character, _SCRIPT_CHARACTER_FIELDS)
    name = character.get("name", "")
    return f"- {name}" + (f" — {details}" if details else "")


def _location_line(location: dict[str, Any]) -> str:
    details = _detail_line(location, _SCRIPT_LOCATION_FIELDS)
    name = location.get("name", "")
    return f"Lieu : {name}" + (f" — {details}" if details else "")


def render_prompt_block(narrative_context: dict[str, Any] | None) -> str:
    """Bloc de contexte narratif, clairement délimité, à ajouter au prompt d'écriture du script.

    ``narrative_context`` DOIT venir du ``snapshot`` déjà figé d'une production (jamais des tables
    ``characters``/``locations``, qui peuvent avoir changé depuis) : voir ``resolve_narrative_context``.

    Renvoie une chaîne VIDE — et donc un prompt strictement identique à avant #36 — si ``narrative_context``
    est ``None``, absent, ou ``{}`` : c'est le cas de toute production sans sélection (#34), et de toute
    production antérieure à #35 qui n'a jamais eu ce bloc dans son snapshot.

    Ordre déterministe (celui, déjà figé, des personnages dans le snapshot, puis le lieu en dernier) ; taille
    bornée par ``NARRATIVE_BLOCK_MAX`` (troncature nette entre éléments, jamais au milieu d'un champ).
    """
    if not narrative_context:
        return ""
    lines = [_character_line(character) for character in narrative_context.get("characters") or []]
    location = narrative_context.get("location")
    if location:
        lines.append(_location_line(location))
    if not lines:
        return ""

    kept: list[str] = []
    used = len(_HEADER) + len(_FOOTER) + 2
    for line in lines:
        if used + len(line) + 1 > NARRATIVE_BLOCK_MAX:
            kept.append(_TRUNCATION_NOTE)
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join([_HEADER, *kept, _FOOTER])
