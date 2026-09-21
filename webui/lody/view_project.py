"""Page d'un projet : résumé, production, modification, archivage."""

from __future__ import annotations

import streamlit as st

from lody import brief as brief_lib
from lody import catalog, nav
from lody.components import format_datetime, readiness_pill, status_badge
from lody.generation.models import ACTIVE_STATUSES
from lody.generation.service import ProductionService
from lody.view_tracking import status_pill
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
    settings = brief_lib.brief_settings(project.settings)
    platforms = ", ".join(catalog.label(catalog.PLATFORMS, value) for value in project.platforms) or "Non précisées"
    duration = f"{settings['duration_min']} à {settings['duration_max']} secondes"
    steps = settings["structure"]
    content = "".join([
        _row("Type", catalog.label(catalog.CONTENT_TYPES, project.content_type)),
        _row("Langue", catalog.label(catalog.LANGUAGES, project.language)),
        _row("Format", catalog.label(catalog.FORMATS, project.format)),
        _row("Durée cible", duration),
        _row("Plateformes", platforms),
    ])
    editorial = "".join([
        _row("Public", settings["audience"]),
        _row("Orientation", settings["orientation"]),
        _row("Ton", project.tone),
    ])
    voice_label = " · ".join(filter(None, [catalog.label(catalog.VOICE_PROVIDERS, project.voice_provider),
                                            project.voice_name]))
    visuals = "".join([
        _row("Style", project.visual_style),
        _row("Rythme visuel", f"{settings['scenes_per_minute_min']} à {settings['scenes_per_minute_max']} scènes par minute"),
        _row("Narration", catalog.label(brief_lib.NARRATION_PACES, settings["narration_pace"])),
        _row("Voix", voice_label, readiness_pill(project, "voice_provider", table)),
    ])
    generation = "".join([
        _row("Script", catalog.label(catalog.TEXT_PROVIDERS, project.text_provider),
             readiness_pill(project, "text_provider", table)),
        _row("Images", catalog.label(catalog.VISUAL_PROVIDERS, project.visual_provider),
             readiness_pill(project, "visual_provider", table)),
        _row("Musique", catalog.label(catalog.MUSIC_PROVIDERS, project.music_provider),
             readiness_pill(project, "music_provider", table)),
    ])
    structure_card = ""
    if steps:
        structure_card = (
            '<section class="card card-wide"><p class="card-eyebrow">Structure type d’une vidéo</p>'
            '<ol class="timeline">' + "".join(f"<li>{esc(step)}</li>" for step in steps) + "</ol></section>"
        )
    instructions = ""
    if settings["standing_instructions"]:
        items = "".join(f"<li>{esc(line)}</li>" for line in settings["standing_instructions"].splitlines() if line.strip())
        instructions = (
            '<section class="card card-wide"><p class="card-eyebrow">Consignes permanentes</p>'
            f'<ul class="rules">{items}</ul></section>'
        )
    return (
        f'<section class="card"><p class="card-eyebrow">Contenu</p><dl class="kv">{content}</dl></section>'
        f'<section class="card"><p class="card-eyebrow">Éditorial</p><dl class="kv">{editorial}</dl></section>'
        f'<section class="card"><p class="card-eyebrow">Visuels et voix</p><dl class="kv">{visuals}</dl></section>'
        f'<section class="card"><p class="card-eyebrow">Fournisseurs</p><dl class="kv">{generation}</dl></section>'
        f"{structure_card}{instructions}"
    )


def _render_productions(service: ProductionService, project: Project) -> None:
    """Productions du projet (V1, V2…) : on retrouve toujours une génération en cours."""
    productions = service.repo.list_for_project(project.id)
    if not productions:
        return
    for production in productions:
        if production.status in ACTIVE_STATUSES:
            service.refresh(production.id)
    productions = service.repo.list_for_project(project.id)
    with st.container(key="productions"):
        st.markdown('<p class="card-eyebrow">Productions</p><h2 class="brief-title">Tes vidéos</h2>', unsafe_allow_html=True)
        for production in productions:
            if production.status.value in ("BROUILLON", "EN_ATTENTE_CONFIRMATION"):
                continue  # brouillons non lancés : pas de vidéo à suivre
            with st.container(horizontal=True, vertical_alignment="center", key=f"prod_row_{production.id}"):
                st.markdown(
                    f'<div class="prod-info"><p class="prod-title">{esc(production.label)} · {esc(production.subject)}</p>'
                    f'<p class="prod-meta">{status_pill(production)} '
                    f'<span>{esc(format_datetime(production.created_at))}</span></p></div>',
                    unsafe_allow_html=True,
                )
                st.button("Ouvrir", key=f"open_prod_{production.id}", icon=":material/open_in_new:",
                          on_click=nav.go, args=(nav.VIEW_TRACK, project.id, production.id))


def render(repo: ProjectRepository, project: Project, table: Readiness, service: ProductionService) -> None:
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
            "Restaure-le pour démarrer une production ou modifier ses paramètres.</div>",
            unsafe_allow_html=True,
        )
        with st.container(horizontal=True, key="project_actions"):
            st.button("Restaurer ce projet", type="primary", icon=":material/unarchive:",
                      key="restore_project", on_click=_restore, args=(repo, project.id))
    else:
        with st.container(horizontal=True, key="project_actions"):
            st.button("Commencer une production", type="primary", icon=":material/play_arrow:",
                      key="start_production", on_click=nav.go, args=(nav.VIEW_PRODUCTION, project.id))
            st.button("Paramètres", icon=":material/tune:", key="settings_project",
                      on_click=nav.go, args=(nav.VIEW_SETTINGS, project.id))
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

    _render_productions(service, project)
    with st.container(key="summary"):
        st.markdown(_summary_cards(project, table), unsafe_allow_html=True)
