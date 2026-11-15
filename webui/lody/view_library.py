"""Navigation globale en 3 domaines et vues transverses Personnages / Ressources (#213).

- ``render_global_nav`` : barre compacte *Projets / Personnages / Ressources*, affichée sur les pages de
  premier niveau seulement (sur les pages d'un projet, l'en-tête garde « Tous les projets » et #212 ajoute
  « Retour au projet »).
- ``render_characters`` / ``render_resources`` : listes EN LECTURE SEULE de ce qui existe dans les projets
  actifs. Chaque ligne indique son projet et s'ouvre dans ce projet ; aucune modification ici, aucune copie
  entre projets (voir ``lody.library``).

Mobile d'abord : mêmes lignes repliables que la liste des projets (#195), recherche en haut, conservée
lors d'un aller-retour vers un projet.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from lody import library, nav, view_home
from lody.library import LibraryEntry
from lody.projects import Project, ProjectRepository, STATUS_ACTIVE
from lody.theme import esc

DOMAINS = (
    (nav.VIEW_HOME, "Projets", ":material/folder_open:"),
    (nav.VIEW_ALL_CHARACTERS, "Personnages", ":material/groups:"),
    (nav.VIEW_RESOURCES, "Ressources", ":material/photo_library:"),
)

CHAR_SEARCH_KEY, RES_SEARCH_KEY, RES_KIND_KEY = "lib_char_search", "lib_res_search", "lib_res_kind"
_SAVED = {CHAR_SEARCH_KEY: "_lib_char_search", RES_SEARCH_KEY: "_lib_res_search", RES_KIND_KEY: "_lib_res_kind"}
RES_LOCATIONS, RES_ASSETS = "Lieux", "Éléments animés"
_DEFAULTS = {CHAR_SEARCH_KEY: "", RES_SEARCH_KEY: "", RES_KIND_KEY: RES_LOCATIONS}

VOICE_HELP = (
    "**Qui parle avec quelle voix ?**\n\n"
    "- **Un personnage parle** : sa voix (fiche du personnage) est utilisée pour ses répliques. "
    "Elle se choisit dans le projet, page Personnages → Modifier.\n"
    "- **Vidéo classique** : la voix du personnage principal sélectionné (ou du seul personnage "
    "sélectionné) remplace la voix du projet. Sinon, c'est la **voix de narration du projet** "
    "(Paramètres du projet).\n"
    "- **Projet sans personnage** : voix de narration du projet.\n"
    "- **Personnage sans voix propre** : voix du projet en vidéo classique, voix provisoire signalée en "
    "rendu 2D.\n"
    "- **Figurant muet** : un personnage sans réplique ne parle pas. Il n'existe pas encore de case "
    "« muet » sur la fiche."
)
SCOPE_HELP = (
    "Chaque élément appartient à **un projet** et s'ouvre dans ce projet. Pour le réutiliser ailleurs, "
    "exporte-le puis importe-le dans l'autre projet (Personnages ou Lieux → Importer / exporter). "
    "Rien n'est partagé automatiquement entre projets."
)

AUDIO_HELP = (
    "**Audio** : la voix se choisit sur chaque personnage ; la voix de narration et la musique se règlent "
    "dans les paramètres du projet. Il n'y a pas encore de bibliothèque audio partagée."
)


def render_global_nav(active: str) -> None:
    """Trois boutons de même taille ; le domaine courant est mis en avant et désactivé."""
    with st.container(horizontal=True, key="global_nav"):
        for view, label, icon in DOMAINS:
            st.button(label, key=f"gnav_{view}", icon=icon, type="primary" if view == active else "secondary",
                      disabled=view == active, on_click=nav.go, args=(view,), width="stretch")


def _prepare_widget_state(keys: tuple[str, ...]) -> None:
    """Redonne aux widgets la valeur mémorisée : Streamlit efface l'état d'un widget absent de la page."""
    for key in keys:
        if key not in st.session_state:
            st.session_state[key] = st.session_state.get(_SAVED[key], _DEFAULTS[key])


def _remember(key: str, value: Any) -> None:
    st.session_state[_SAVED[key]] = value


def _clear(key: str) -> None:
    st.session_state[key] = ""
    st.session_state[_SAVED[key]] = ""


def _title(eyebrow: str, title: str) -> None:
    st.markdown(f'<section class="hero hero-compact"><p class="eyebrow">{esc(eyebrow)}</p>'
                f'<h1 class="hero-title">{esc(title)}</h1></section>', unsafe_allow_html=True)


def _search(key: str, placeholder: str) -> str:
    query = st.text_input(placeholder, key=key, placeholder=placeholder, icon=":material/search:",
                          label_visibility="collapsed") or ""
    _remember(key, query)
    return query.strip()


def _status(shown: int, total: int, query: str, key: str, nouns: tuple[str, str]) -> None:
    if query:
        text = f"{shown} résultat{'s' if shown > 1 else ''} sur {total}"
    else:
        text = f"{total} {nouns[1] if total > 1 else nouns[0]}"
    with st.container(horizontal=True, vertical_alignment="center", key=f"{key}_status"):
        st.markdown(f'<p class="list-count">{esc(text)}</p>', unsafe_allow_html=True)
        if query:
            st.button("Effacer la recherche", key=f"{key}_clear", type="tertiary", icon=":material/close:",
                      on_click=_clear, args=(key,))


def _empty(title: str, text: str) -> None:
    st.markdown(f'<div class="card soft"><p class="empty-title">{esc(title)}</p>'
                f'<p class="empty-text">{esc(text)}</p></div>', unsafe_allow_html=True)


def _open_label(entry: LibraryEntry) -> str:
    if entry.kind == library.KIND_CHARACTER:
        return "Ouvrir les personnages du projet"
    if entry.kind == library.KIND_LOCATION:
        return "Ouvrir les lieux du projet"
    return "Ouvrir le projet"


def _details_html(entry: LibraryEntry) -> str:
    parts = []
    if entry.detail:
        parts.append(f'<p class="row-desc">{esc(entry.detail)}</p>')
    if entry.voice:
        parts.append(f'<p class="row-desc">{esc(entry.voice)}</p>')
    parts.append(f'<div class="chips"><span class="chip">Dans le projet « {esc(entry.project_name)} »</span></div>')
    return "".join(parts)


def _render_groups(entries: list[LibraryEntry], prefix: str) -> None:
    for project_id, project_name, items in library.group_by_project(entries):
        st.markdown(f'<p class="list-section">{esc(project_name)} ({len(items)})</p>', unsafe_allow_html=True)
        with st.container(key=f"plist_{prefix}_{project_id}"):
            for entry in items:
                with st.container(key=f"prow_active_{prefix}_{entry.id}"):
                    with st.expander(view_home.label_text(entry.label), key=f"lexp_{prefix}_{entry.id}"):
                        st.markdown(_details_html(entry), unsafe_allow_html=True)
                        with st.container(horizontal=True, key=f"prow_actions_{prefix}_{entry.id}"):
                            st.button(_open_label(entry), key=f"lopen_{prefix}_{entry.id}", type="primary",
                                      icon=":material/arrow_forward:", icon_position="right",
                                      on_click=nav.go, args=(entry.target_view, entry.project_id))


def _active_projects(repo: ProjectRepository) -> list[Project]:
    # Les projets archivés sont exclus : leurs pages redirigent vers « restaure-le pour continuer ».
    return repo.list_projects(STATUS_ACTIVE)


def render_characters(repo: ProjectRepository, character_repo: Any) -> None:
    render_global_nav(nav.VIEW_ALL_CHARACTERS)
    _title("Tous les projets", "Personnages")
    projects = _active_projects(repo)
    entries = library.character_entries(
        projects, {project.id: character_repo.list_for_project(project.id, include_inactive=False)
                   for project in projects})
    with st.expander("Portée et voix : comment ça marche ?", icon=":material/help:", key="lib_char_help"):
        st.markdown(SCOPE_HELP)
        st.markdown(VOICE_HELP)
    if not entries:
        _empty("Aucun personnage pour l’instant",
               "Les personnages se créent dans un projet : ouvre un projet, puis « Personnages ».")
        return
    _prepare_widget_state((CHAR_SEARCH_KEY,))
    with st.container(horizontal=True, vertical_alignment="center", key="list_toolbar"):
        query = _search(CHAR_SEARCH_KEY, "Rechercher un personnage…")
    shown = library.filter_entries(entries, query)
    _status(len(shown), len(entries), query, CHAR_SEARCH_KEY, ("personnage", "personnages"))
    if not shown:
        _empty(f"Aucun personnage ne correspond à « {query} »", "Essaie un autre mot du nom, du rôle ou du projet.")
        return
    _render_groups(shown, "char")


def render_resources(repo: ProjectRepository, location_repo: Any, asset_repo: Any) -> None:
    render_global_nav(nav.VIEW_RESOURCES)
    _title("Tous les projets", "Ressources")
    projects = _active_projects(repo)
    with st.expander("Portée : comment ça marche ?", icon=":material/help:", key="lib_res_help"):
        st.markdown(SCOPE_HELP)
        st.markdown(AUDIO_HELP)
    _prepare_widget_state((RES_SEARCH_KEY, RES_KIND_KEY))
    with st.container(horizontal=True, vertical_alignment="center", key="list_toolbar"):
        query = _search(RES_SEARCH_KEY, "Rechercher une ressource…")
        kind = st.segmented_control("Type de ressource", (RES_LOCATIONS, RES_ASSETS), key=RES_KIND_KEY,
                                    label_visibility="collapsed") or RES_LOCATIONS
    _remember(RES_KIND_KEY, kind)
    if kind == RES_ASSETS:
        entries = library.asset_entries(
            projects, {project.id: asset_repo.list_for_project(project.id, include_inactive=False)
                       for project in projects})
        nouns = ("élément animé", "éléments animés")
        empty_title = "Aucun élément animé pour l’instant"
        empty_text = "Les marionnettes, décors et accessoires 2D sont enregistrés par projet, lors des essais d’animation."
    else:
        entries = library.location_entries(
            projects, {project.id: location_repo.list_for_project(project.id, include_inactive=False)
                       for project in projects})
        nouns = ("lieu", "lieux")
        empty_title = "Aucun lieu pour l’instant"
        empty_text = "Les lieux se créent dans un projet : ouvre un projet, puis « Lieux »."
    if not entries:
        _empty(empty_title, empty_text)
        return
    shown = library.filter_entries(entries, query)
    _status(len(shown), len(entries), query, RES_SEARCH_KEY, nouns)
    if not shown:
        _empty(f"Aucune ressource ne correspond à « {query} »", "Essaie un autre mot du nom, du type ou du projet.")
        return
    _render_groups(shown, "loc" if kind == RES_LOCATIONS else "asset")
