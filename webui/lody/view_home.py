"""Accueil : projets récents, projets archivés, état vide."""

from __future__ import annotations

import streamlit as st

from lody import catalog, nav
from lody.components import EMPTY_ICON_SVG, format_datetime, status_badge
from lody.projects import Project, ProjectNotFound, ProjectRepository, STATUS_ACTIVE, STATUS_ARCHIVED
from lody.theme import esc


def _restore(repo: ProjectRepository, project_id: str) -> None:
    try:
        project = repo.restore(project_id)
    except ProjectNotFound:
        nav.flash("error", "Ce projet n’existe plus.")
        return
    nav.flash("success", f"« {project.name} » est de nouveau actif.")


def _card_html(project: Project) -> str:
    chips = [
        catalog.label(catalog.CONTENT_TYPES, project.content_type),
        project.format,
        catalog.label(catalog.LANGUAGES, project.language),
    ]
    chip_html = "".join(f'<span class="chip">{esc(chip)}</span>' for chip in chips)
    description = (
        f'<p class="card-desc">{esc(project.description)}</p>'
        if project.description
        else '<p class="card-desc card-desc-empty">Pas encore de description.</p>'
    )
    return (
        f'<div class="card-head">{status_badge(project)}</div>'
        f'<h3 class="project-name">{esc(project.name)}</h3>'
        f"{description}"
        f'<div class="chips">{chip_html}</div>'
        f'<p class="card-meta">Modifié le {esc(format_datetime(project.updated_at))}</p>'
    )


def _render_cards(projects: list[Project], repo: ProjectRepository, prefix: str) -> None:
    with st.container(key=f"grid_{prefix}"):
        for project in projects:
            with st.container(key=f"card_{prefix}_{project.id}"):
                st.markdown(_card_html(project), unsafe_allow_html=True)
                with st.container(horizontal=True, key=f"card_actions_{prefix}_{project.id}"):
                    st.button(
                        "Ouvrir",
                        key=f"open_{prefix}_{project.id}",
                        icon=":material/arrow_forward:",
                        icon_position="right",
                        on_click=nav.go,
                        args=(nav.VIEW_PROJECT, project.id),
                    )
                    if project.is_archived:
                        st.button(
                            "Restaurer",
                            key=f"restore_{prefix}_{project.id}",
                            type="tertiary",
                            on_click=_restore,
                            args=(repo, project.id),
                        )


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

    if active:
        _render_cards(active, repo, "active")
    else:
        st.markdown(
            '<div class="card soft"><p class="empty-title">Aucun projet actif</p>'
            '<p class="empty-text">Restaure un projet archivé ou crée-en un nouveau.</p></div>',
            unsafe_allow_html=True,
        )

    if archived:
        with st.expander(f"Projets archivés ({len(archived)})"):
            _render_cards(archived, repo, "archived")
