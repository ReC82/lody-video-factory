"""Sélection optionnelle des personnages et du lieu d'une production (epic #29, ticket #35).

Contrat : au lancement, les IDs sélectionnés sont résolus dans le **projet courant** puis copiés en un
instantané immuable (``snapshot.narrative_context``), aux côtés de l'instantané des paramètres du projet
(voir ``make_snapshot``). Modifier ou désactiver ensuite un personnage ou un lieu ne change donc jamais
une production déjà préparée ou lancée — exactement le principe déjà appliqué aux paramètres du projet.

Aucune colonne SQLite dédiée : le ``snapshot`` JSON existant (v3, ``lody/db.py``) suffit déjà à porter des
données arbitraires immuables par production, et il est déjà protégé par le garde-fou secrets (``GUARDED``
dans ``generation/store.py``). Ajouter une table ne ferait que dupliquer ce mécanisme.

Ce module ne lit ni n'écrit ni script, ni voix, ni prompt d'image : c'est volontairement hors de portée
de ce ticket (voir tickets ultérieurs pour l'injection réelle dans le pipeline de génération).
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
