"""Liste des projets de l'accueil (#195) : recherche tolérante et tri, sans Streamlit (testable seul).

La recherche ignore la casse, les accents et les espaces superflus ; chaque mot saisi doit apparaître
dans le nom, la description ou le type de vidéo du projet (« cable sdi » trouve « Câble SDI »).
"""

from __future__ import annotations

import unicodedata

from lody import catalog
from lody.projects import Project

SORT_RECENT = "Récents"
SORT_NAME = "Nom A→Z"
SORT_OPTIONS = (SORT_RECENT, SORT_NAME)


def fold(text: str) -> str:
    """Texte comparable : minuscules, sans accents ni espaces multiples."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.casefold().split())


def _haystack(project: Project) -> str:
    return fold(" ".join((project.name, project.description,
                          catalog.label(catalog.CONTENT_TYPES, project.content_type))))


def matches(project: Project, query: str) -> bool:
    terms = fold(query).split()
    if not terms:
        return True
    haystack = _haystack(project)
    return all(term in haystack for term in terms)


def filter_projects(projects: list[Project], query: str) -> list[Project]:
    return [project for project in projects if matches(project, query)]


def sort_projects(projects: list[Project], order: str | None) -> list[Project]:
    """« Récents » : dernière modification d'abord (ordre par défaut) ; « Nom A→Z » : alphabétique sans accents."""
    if order == SORT_NAME:
        return sorted(projects, key=lambda project: (fold(project.name), project.name))
    return sorted(projects, key=lambda project: (project.updated_at, project.created_at), reverse=True)
