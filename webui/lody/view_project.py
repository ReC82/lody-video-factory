"""Page d'un projet : résumé, production, modification, archivage."""

from __future__ import annotations

import streamlit as st

from lody import catalog, nav
from lody.components import format_datetime, readiness_pill, status_badge
from lody.projects import Project, ProjectNotFound, ProjectRepository
from lody.provider_status import Readiness
from lody.theme import esc

CONFIRM_KEY = "_confirm_archive_project_id"


def _ask_archive(project_id: str) -> None:
    st.session_state[CONFIRM_KEY] = project_id


def _cancel_archive() -> None:
    st.session_state.pop(CONFIRM_KEY, None)


def _archive(repo: ProjectRepository, project_id: str) -> None:
    st.session_state.pop(CONFIRM_KEY, None)
    try:
        project = repo.archive(project_id)
    except ProjectNotFound:
        nav.flash("error", "Ce projet n’existe plus.")
        nav.go()
        return
    nav.flash("success", f"« {project.name} » est archivé. Tu peux le restaurer depuis l’accueil.")
    nav.go()


def _restore(repo: ProjectRepository, project_id: str) -> None:
    try:
        project = repo.restore(project_id)
    except ProjectNotFound:
        nav.flash("error", "Ce projet n’existe plus.")
        nav.go()
        return
    nav.flash("success", f"« {project.name} » est de nouveau actif.")
    nav.go(nav.VIEW_PROJECT, project.id)


def _row(label: str, value: str, extra: str = "") -> str:
    return f"<div><dt>{esc(label)}</dt><dd>{esc(value) or '—'}{extra}</dd></div>"


def _summary_cards(project: Project, table: Readiness) -> str:
    platforms = ", ".join(catalog.label(catalog.PLATFORMS, value) for value in project.platforms) or "Non précisées"
    content = "".join(
        [
            _row("Type", catalog.label(catalog.CONTENT_TYPES, project.content_type)),
            _row("Langue", catalog.label(catalog.LANGUAGES, project.language)),
            _row("Format", catalog.label(catalog.FORMATS, project.format)),
            _row("Plateformes", platforms),
        ]
    )
    editorial = "".join(
        [
            _row("Ton", project.tone),
            _row("Style visuel", project.visual_style),
            _row("Durée visée", str(project.settings.get("target_duration", "")) or "Libre"),
        ]
    )
    generation = "".join(
        [
            _row("Script", catalog.label(catalog.TEXT_PROVIDERS, project.text_provider),
                 readiness_pill(project, "text_provider", table)),
            _row("Images", catalog.label(catalog.VISUAL_PROVIDERS, project.visual_provider),
                 readiness_pill(project, "visual_provider", table)),
            _row("Voix", " · ".join(filter(None, [catalog.label(catalog.VOICE_PROVIDERS, project.voice_provider), project.voice_name])),
                 readiness_pill(project, "voice_provider", table)),
            _row("Musique", catalog.label(catalog.MUSIC_PROVIDERS, project.music_provider),
                 readiness_pill(project, "music_provider", table)),
        ]
    )
    return (
        f'<section class="card"><p class="card-eyebrow">Contenu</p><dl class="kv">{content}</dl></section>'
        f'<section class="card"><p class="card-eyebrow">Style éditorial</p><dl class="kv">{editorial}</dl></section>'
        f'<section class="card"><p class="card-eyebrow">Génération</p><dl class="kv">{generation}</dl></section>'
    )


def render(repo: ProjectRepository, project: Project, table: Readiness) -> None:
    description = (
        f'<p class="hero-sub">{esc(project.description)}</p>' if project.description else ""
    )
    st.markdown(
        f'<section class="hero"><p class="eyebrow">{status_badge(project)}</p>'
        f'<h1 class="hero-title">{esc(project.name)}</h1>{description}'
        f'<p class="meta-line">Créé le {esc(format_datetime(project.created_at))}'
        f" · Modifié le {esc(format_datetime(project.updated_at))}</p></section>",
        unsafe_allow_html=True,
    )

    if project.is_archived:
        st.markdown(
            '<div class="banner banner-info" role="status">Ce projet est archivé. '
            "Restaure-le pour démarrer une production ou le modifier.</div>",
            unsafe_allow_html=True,
        )
        with st.container(horizontal=True, key="project_actions"):
            st.button("Restaurer ce projet", type="primary", icon=":material/unarchive:",
                      key="restore_project", on_click=_restore, args=(repo, project.id))
    else:
        with st.container(horizontal=True, key="project_actions"):
            st.button("Commencer une production", type="primary", icon=":material/play_arrow:",
                      key="start_production", on_click=nav.go, args=(nav.VIEW_PRODUCTION, project.id))
            st.button("Modifier", icon=":material/edit:", key="edit_project",
                      on_click=nav.go, args=(nav.VIEW_EDIT, project.id))
            st.button("Archiver", icon=":material/archive:", type="tertiary", key="archive_project",
                      on_click=_ask_archive, args=(project.id,))

        if st.session_state.get(CONFIRM_KEY) == project.id:
            with st.container(key="confirm_archive_box"):
                st.markdown(
                    f'<p class="confirm-title">Archiver « {esc(project.name)} » ?</p>'
                    '<p class="confirm-text">Le projet quitte la liste principale mais reste consultable : '
                    "tu pourras le restaurer à tout moment.</p>",
                    unsafe_allow_html=True,
                )
                with st.container(horizontal=True, key="confirm_actions"):
                    st.button("Oui, archiver", type="primary", key="confirm_archive",
                              on_click=_archive, args=(repo, project.id))
                    st.button("Annuler", key="cancel_archive", on_click=_cancel_archive)

    with st.container(key="summary"):
        st.markdown(_summary_cards(project, table), unsafe_allow_html=True)
