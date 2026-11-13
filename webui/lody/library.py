"""Vues transverses Personnages / Ressources (#213) : agrégation EN LECTURE SEULE, sans Streamlit.

État réel des données (vérifié dans ``lody.db``) : personnages, lieux et assets d'animation ont tous un
``project_id NOT NULL``. Il n'existe aucune bibliothèque globale. Ces vues ne font donc que LISTER, projet
par projet, ce qui existe déjà ; chaque entrée garde son projet d'origine et s'ouvre dans ce projet. Rien
n'est copié, partagé ni rendu visible dans un autre projet : la réutilisation entre projets reste
l'export/import explicite (#57). Une bibliothèque partagée demanderait une migration additive séparée
(voir ``docs/lody-navigation.md``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from lody import catalog, nav
from lody.project_list import fold

KIND_CHARACTER = "character"
KIND_LOCATION = "location"
KIND_ASSET = "asset"

# Libellés des types d'assets d'animation (``lody.animation.contract.ASSET_KINDS``).
ASSET_KIND_LABELS = {
    "character": "Marionnette",
    "background": "Décor",
    "prop": "Accessoire",
    "overlay": "Habillage",
}


@dataclass(frozen=True)
class LibraryEntry:
    """Une ligne d'une vue transverse : toujours rattachée à SON projet (jamais « globale »)."""

    kind: str
    id: str
    name: str
    subtitle: str
    project_id: str
    project_name: str
    detail: str = ""
    voice: str = ""
    target_view: str = nav.VIEW_PROJECT

    @property
    def label(self) -> str:
        return " · ".join(part for part in (self.name, self.subtitle) if part)


def character_voice_text(character: Any) -> str:
    """Voix qui sera réellement utilisée pour ce personnage, d'après le modèle existant.

    Même règle que ``narrative_context.resolve_voice`` et ``animation.audio.voice_plan`` : la voix de la
    fiche n'est retenue que si le fournisseur ET l'identifiant sont renseignés ; sinon, voix du projet
    (production classique) ou voix provisoire (rendu 2D). Aucun identifiant n'est affiché.
    """
    provider_raw = str(getattr(character, "voice_provider", "") or "").strip()
    voice_id = str(getattr(character, "external_voice_id", "") or "").strip()
    provider = catalog.normalize(catalog.VOICE_PROVIDERS, provider_raw) if provider_raw else None
    if provider and voice_id:
        name = str(getattr(character, "voice_name", "") or "").strip() or "voix choisie"
        return f"Voix propre : {name} ({catalog.label(catalog.VOICE_PROVIDERS, provider)})"
    return "Pas de voix propre : voix du projet (vidéo classique) ou voix provisoire (2D)"


def character_entries(projects: Iterable[Any], characters_by_project: dict[str, list[Any]]) -> list[LibraryEntry]:
    entries: list[LibraryEntry] = []
    for project in projects:
        for character in characters_by_project.get(project.id, []):
            if not character.is_active:
                continue
            subtitle = " · ".join(part for part in (character.role.strip(), "principal" if character.is_primary else "")
                                  if part)
            entries.append(LibraryEntry(
                kind=KIND_CHARACTER, id=character.id, name=character.name, subtitle=subtitle,
                project_id=project.id, project_name=project.name,
                detail=character.personality or character.visual_description,
                voice=character_voice_text(character), target_view=nav.VIEW_CHARACTERS,
            ))
    return entries


def location_entries(projects: Iterable[Any], locations_by_project: dict[str, list[Any]]) -> list[LibraryEntry]:
    entries: list[LibraryEntry] = []
    for project in projects:
        for location in locations_by_project.get(project.id, []):
            if not location.is_active:
                continue
            subtitle = " · ".join(part for part in (location.location_type.strip(),
                                                    "principal" if location.is_primary else "") if part)
            entries.append(LibraryEntry(
                kind=KIND_LOCATION, id=location.id, name=location.name, subtitle=subtitle,
                project_id=project.id, project_name=project.name, detail=location.description,
                target_view=nav.VIEW_LOCATIONS,
            ))
    return entries


def asset_entries(projects: Iterable[Any], assets_by_project: dict[str, list[Any]]) -> list[LibraryEntry]:
    entries: list[LibraryEntry] = []
    for project in projects:
        for asset in assets_by_project.get(project.id, []):
            if not asset.is_active:
                continue
            entries.append(LibraryEntry(
                kind=KIND_ASSET, id=asset.id, name=asset.name,
                subtitle=ASSET_KIND_LABELS.get(asset.kind, asset.kind), project_id=project.id,
                project_name=project.name, detail=asset.description, target_view=nav.VIEW_PROJECT,
            ))
    return entries


def matches(entry: LibraryEntry, query: str) -> bool:
    """Chaque mot doit apparaître (sans casse ni accents) dans le nom, le sous-titre, le projet ou le détail."""
    terms = fold(query).split()
    if not terms:
        return True
    haystack = fold(" ".join((entry.name, entry.subtitle, entry.project_name, entry.detail)))
    return all(term in haystack for term in terms)


def filter_entries(entries: Iterable[LibraryEntry], query: str) -> list[LibraryEntry]:
    return [entry for entry in entries if matches(entry, query)]


def group_by_project(entries: Iterable[LibraryEntry]) -> list[tuple[str, str, list[LibraryEntry]]]:
    """(id du projet, nom du projet, entrées) dans l'ordre d'arrivée des projets, entrées triées par nom."""
    groups: dict[str, tuple[str, list[LibraryEntry]]] = {}
    for entry in entries:
        groups.setdefault(entry.project_id, (entry.project_name, []))[1].append(entry)
    return [(project_id, name, sorted(items, key=lambda item: fold(item.name)))
            for project_id, (name, items) in groups.items()]
