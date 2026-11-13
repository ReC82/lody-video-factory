"""Accueil : liste compacte des projets (#195), recherche, tri, projets archivés, état vide.

Mobile d'abord : chaque projet est une ligne repliable (nom + chevron). Un appui déplie les détails ;
l'ouverture du projet reste une action distincte (« Ouvrir »). Aucune action destructive dans la liste.
Recherche, tri et dernier projet ouvert survivent à un aller-retour vers un projet (clés de session
non liées aux widgets, que Streamlit oublierait dès que la page n'est plus affichée).
"""

from __future__ import annotations

import re

import streamlit as st

from lody import catalog, nav
from lody.components import EMPTY_ICON_SVG, format_datetime
from lody.project_list import SORT_OPTIONS, SORT_RECENT, filter_projects, sort_projects
from lody.projects import Project, ProjectNotFound, ProjectRepository, STATUS_ACTIVE, STATUS_ARCHIVED
from lody.theme import esc

SEARCH_KEY, SORT_KEY, ARCHIVED_KEY = "home_search", "home_sort", "home_show_archived"
_SAVED = {SEARCH_KEY: "_home_search", SORT_KEY: "_home_sort", ARCHIVED_KEY: "_home_show_archived"}
_DEFAULTS = {SEARCH_KEY: "", SORT_KEY: SORT_RECENT, ARCHIVED_KEY: False}
LAST_OPENED_KEY = "_home_last_opened"
_MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|:~<>$])")


def _restore(repo: ProjectRepository, project_id: str) -> None:
    try:
        project = repo.restore(project_id)
    except ProjectNotFound:
        nav.flash("error", "Ce projet n’existe plus.")
        return
    st.session_state[LAST_OPENED_KEY] = project_id
    nav.flash("success", f"« {project.name} » est de nouveau actif.")


def _open(project_id: str) -> None:
    st.session_state[LAST_OPENED_KEY] = project_id
    nav.go(nav.VIEW_PROJECT, project_id)


def _clear_search() -> None:
    st.session_state[SEARCH_KEY] = ""
    st.session_state[_SAVED[SEARCH_KEY]] = ""


def _prepare_widget_state() -> None:
    """Redonne aux widgets la valeur mémorisée : Streamlit efface l'état d'un widget absent de la page."""
    for key, saved in _SAVED.items():
        if key not in st.session_state:
            st.session_state[key] = st.session_state.get(saved, _DEFAULTS[key])


def _remember(key: str, value) -> None:
    st.session_state[_SAVED[key]] = value


def label_text(name: str) -> str:
    """Nom du projet sûr pour un libellé Streamlit (qui interprète le Markdown)."""
    return _MARKDOWN_SPECIALS.sub(r"\\\1", " ".join(name.split())) or "Sans nom"


def _details_html(project: Project) -> str:
    chips = [
        catalog.label(catalog.CONTENT_TYPES, project.content_type),
        project.format,
        catalog.label(catalog.LANGUAGES, project.language),
    ]
    chip_html = "".join(f'<span class="chip">{esc(chip)}</span>' for chip in chips)
    description = (
        f'<p class="row-desc">{esc(project.description)}</p>'
        if project.description
        else '<p class="row-desc row-desc-empty">Pas encore de description.</p>'
    )
    archived = '<span class="status status-archived">Archivé</span> · ' if project.is_archived else ""
    return (
        f"{description}"
        f'<div class="chips">{chip_html}</div>'
        f'<p class="row-meta">{archived}Modifié le {esc(format_datetime(project.updated_at))}</p>'
    )


def _render_rows(projects: list[Project], repo: ProjectRepository, prefix: str) -> None:
    last_opened = st.session_state.get(LAST_OPENED_KEY)
    with st.container(key=f"plist_{prefix}"):
        for project in projects:
            with st.container(key=f"prow_{prefix}_{project.id}"):
                with st.expander(label_text(project.name), expanded=project.id == last_opened,
                                 key=f"pexp_{prefix}_{project.id}"):
                    st.markdown(_details_html(project), unsafe_allow_html=True)
                    with st.container(horizontal=True, key=f"prow_actions_{prefix}_{project.id}"):
                        st.button(
                            "Ouvrir",
                            key=f"open_{prefix}_{project.id}",
                            type="primary",
                            icon=":material/arrow_forward:",
                            icon_position="right",
                            on_click=_open,
                            args=(project.id,),
                        )
                        if project.is_archived:
                            st.button(
                                "Restaurer",
                                key=f"restore_{prefix}_{project.id}",
                                on_click=_restore,
                                args=(repo, project.id),
                            )


def _count_text(shown: int, total: int, query: str) -> str:
    if query:
        return f"{shown} résultat{'s' if shown > 1 else ''} sur {total}"
    return f"{total} projet{'s' if total > 1 else ''}"


def render(repo: ProjectRepository) -> None:
    active = repo.list_projects(STATUS_ACTIVE)
    archived = repo.list_projects(STATUS_ARCHIVED)
    is_empty = not active and not archived

    if is_empty:
        title, subtitle = "Bienvenue sur Lody Video Factory", "Crée ton premier projet pour organiser tes vidéos."
    else:
        title, subtitle = "Tes projets vidéo", "Retrouve tes projets, ou lance-en un nouveau."

    with st.container(horizontal=True, vertical_alignment="bottom", key="hero_row"):
        st.markdown(
            '<section class="hero"><p class="eyebrow">Projets</p>'
            f'<h1 class="hero-title">{esc(title)}</h1><p class="hero-sub">{esc(subtitle)}</p></section>',
            unsafe_allow_html=True,
        )
        if not is_empty:
            st.button(
                "Nouveau projet",
                type="primary",
                icon=":material/add:",
                key="new_project",
                on_click=nav.go,
                args=(nav.VIEW_NEW,),
            )

    if is_empty:
        with st.container(key="empty_state"):
            st.markdown(
                f'<div class="empty">{EMPTY_ICON_SVG}<p class="empty-title">Aucun projet pour l’instant</p>'
                '<p class="empty-text">Un projet regroupe le style, la voix et les plateformes d’une série de vidéos.</p></div>',
                unsafe_allow_html=True,
            )
            st.button(
                "Créer mon premier projet",
                type="primary",
                icon=":material/add:",
                key="new_project_empty",
                on_click=nav.go,
                args=(nav.VIEW_NEW,),
            )
        return

    _prepare_widget_state()
    with st.container(horizontal=True, vertical_alignment="center", key="list_toolbar"):
        query = st.text_input(
            "Rechercher un projet",
            key=SEARCH_KEY,
            placeholder="Rechercher un projet…",
            icon=":material/search:",
            label_visibility="collapsed",
        ) or ""
        order = st.segmented_control(
            "Trier par", SORT_OPTIONS, key=SORT_KEY, label_visibility="collapsed",
        ) or SORT_RECENT
    _remember(SEARCH_KEY, query)
    _remember(SORT_KEY, order)
    query = query.strip()

    shown_active = sort_projects(filter_projects(active, query), order)
    shown_archived = sort_projects(filter_projects(archived, query), order)
    total = len(active) + len(archived)

    with st.container(horizontal=True, vertical_alignment="center", key="list_status"):
        st.markdown(f'<p class="list-count">{esc(_count_text(len(shown_active) + len(shown_archived), total, query))}</p>',
                    unsafe_allow_html=True)
        if query:
            st.button("Effacer la recherche", key="clear_search", type="tertiary",
                      icon=":material/close:", on_click=_clear_search)

    if query and not shown_active and not shown_archived:
        st.markdown(
            f'<div class="card soft"><p class="empty-title">Aucun projet ne correspond à « {esc(query)} »</p>'
            '<p class="empty-text">Essaie un autre mot du nom ou de la description.</p></div>',
            unsafe_allow_html=True,
        )
        return

    if shown_active:
        _render_rows(shown_active, repo, "active")
    elif not query:
        st.markdown(
            '<div class="card soft"><p class="empty-title">Aucun projet actif</p>'
            '<p class="empty-text">Restaure un projet archivé ou crée-en un nouveau.</p></div>',
            unsafe_allow_html=True,
        )

    if query:
        # Une recherche parcourt aussi les archives : un projet archivé se retrouve sans manipulation de plus.
        if shown_archived:
            st.markdown(f'<p class="list-section">Archivés ({len(shown_archived)})</p>', unsafe_allow_html=True)
            _render_rows(shown_archived, repo, "archived")
    elif archived:
        show = st.toggle(f"Afficher les projets archivés ({len(archived)})", key=ARCHIVED_KEY)
        _remember(ARCHIVED_KEY, show)
        if show:
            _render_rows(shown_archived, repo, "archived")
