"""Écran d'accueil de Lody Video Factory (prototype).

Aucune génération n'est branchée : le bouton principal ne fait que composer un
brouillon de demande en mémoire de session. Aucun fournisseur (LLM, voix, image,
musique) n'est importé ni appelé depuis ce module.
"""

from __future__ import annotations

import streamlit as st

from lody.profiles import DEFAULT_PROJECT_KEY, PROJECTS, ProjectProfile, get_project
from lody.theme import esc, inject_theme

MIN_REQUEST_LENGTH = 8
PROJECT_STATE_KEY = "project_key"
LAST_PROJECT_STATE_KEY = "_last_project_key"
REQUEST_STATE_KEY = "request_text"
DRAFT_STATE_KEY = "draft"
ERROR_STATE_KEY = "request_error"

_PROJECT_KEYS = [project.key for project in PROJECTS]
_LOGO_SVG = (
    '<svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">'
    '<defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="var(--accent-1)"/>'
    '<stop offset="1" stop-color="var(--accent-3)"/></linearGradient></defs>'
    '<rect width="32" height="32" rx="9" fill="url(#lg)"/>'
    '<path d="M12.5 9.5v13l10-6.5z" fill="#06101C"/></svg>'
)
_EMPTY_ICON_SVG = (
    '<svg viewBox="0 0 48 48" width="44" height="44" fill="none" aria-hidden="true">'
    '<rect x="13" y="6" width="22" height="36" rx="5" stroke="currentColor" stroke-width="2"/>'
    '<path d="M21.5 20.5v7l6-3.5z" fill="currentColor"/></svg>'
)


# ---------------------------------------------------------------------------
# État de session
# ---------------------------------------------------------------------------
def init_state() -> None:
    """Initialise le projet actif depuis l'URL (?projet=…) au premier rendu."""
    if PROJECT_STATE_KEY not in st.session_state:
        requested = str(st.query_params.get("projet", "")).lower()
        initial = requested if requested in _PROJECT_KEYS else DEFAULT_PROJECT_KEY
        st.session_state[PROJECT_STATE_KEY] = initial
        st.session_state[LAST_PROJECT_STATE_KEY] = initial
    st.session_state.setdefault(REQUEST_STATE_KEY, "")


def _on_project_change() -> None:
    selected = st.session_state.get(PROJECT_STATE_KEY)
    if selected not in _PROJECT_KEYS:
        # Un clic sur le projet déjà actif le désélectionnerait : on le rétablit.
        st.session_state[PROJECT_STATE_KEY] = st.session_state[LAST_PROJECT_STATE_KEY]
        return
    st.session_state[LAST_PROJECT_STATE_KEY] = selected
    st.session_state[ERROR_STATE_KEY] = False


def current_project() -> ProjectProfile:
    return get_project(st.session_state.get(PROJECT_STATE_KEY))


def _use_example(example: str) -> None:
    st.session_state[REQUEST_STATE_KEY] = example
    st.session_state[ERROR_STATE_KEY] = False


def _soon(feature: str) -> None:
    st.toast(f"{feature} : disponible dans un prochain ticket.", icon=":material/schedule:")


def _prepare_draft(project: ProjectProfile) -> None:
    """Compose un brouillon en mémoire. N'appelle AUCUN fournisseur."""
    request = str(st.session_state.get(REQUEST_STATE_KEY, "")).strip()
    if len(request) < MIN_REQUEST_LENGTH:
        st.session_state[ERROR_STATE_KEY] = True
        st.session_state[DRAFT_STATE_KEY] = None
        return
    st.session_state[ERROR_STATE_KEY] = False
    st.session_state[DRAFT_STATE_KEY] = {
        "projet": project.name,
        "demande": request,
        "profil": {
            "format": f"{project.format} · {project.duration}",
            "voix": project.voice,
            "style": project.style,
            "sous_titres": project.subtitles,
        },
        "fournisseurs_appeles": [],
    }


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------
def render_header() -> None:
    with st.container(horizontal=True, vertical_alignment="center", key="topbar"):
        st.markdown(
            f'<div class="brand">{_LOGO_SVG}<span class="brand-name">Lody Video Factory</span>'
            '<span class="badge">Prototype</span></div>',
            unsafe_allow_html=True,
        )
        st.segmented_control(
            "Projet",
            options=_PROJECT_KEYS,
            format_func=lambda key: get_project(key).name,
            selection_mode="single",
            key=PROJECT_STATE_KEY,
            on_change=_on_project_change,
            label_visibility="collapsed",
            width="content",
        )
        st.button(
            "Tâches",
            icon=":material/format_list_bulleted:",
            type="tertiary",
            key="nav_tasks",
            on_click=_soon,
            args=("Tâches",),
        )
        st.button(
            "Paramètres du projet",
            icon=":material/tune:",
            type="tertiary",
            key="nav_settings",
            on_click=_soon,
            args=("Paramètres du projet",),
        )


def render_hero(project: ProjectProfile) -> None:
    st.markdown(
        '<section class="hero">'
        f'<p class="eyebrow">{esc(project.name)}</p>'
        f'<h1 class="hero-title">{esc(project.headline)}</h1>'
        f'<p class="hero-sub">{esc(project.tagline)}</p>'
        "</section>",
        unsafe_allow_html=True,
    )


def render_composer(project: ProjectProfile) -> None:
    with st.container(key="composer"):
        st.markdown('<h2 class="composer-title">Que veux-tu créer ?</h2>', unsafe_allow_html=True)
        st.text_area(
            "Que veux-tu créer ?",
            key=REQUEST_STATE_KEY,
            placeholder="Décris la vidéo que tu veux…",
            height=132,
            max_chars=2000,
            label_visibility="collapsed",
        )
        if st.session_state.get(ERROR_STATE_KEY):
            st.markdown(
                '<p class="field-error" role="alert">Décris d’abord ta vidéo en quelques mots.</p>',
                unsafe_allow_html=True,
            )
        with st.container(horizontal=True, vertical_alignment="center", key="composer_actions"):
            st.markdown(
                f'<p class="example"><span>Exemple</span> {esc(project.example)}</p>',
                unsafe_allow_html=True,
            )
            st.button(
                "Utiliser l’exemple",
                type="tertiary",
                key="use_example",
                on_click=_use_example,
                args=(project.example,),
            )
            st.button(
                "Générer la vidéo",
                type="primary",
                icon=":material/auto_awesome:",
                key="generate",
                on_click=_prepare_draft,
                args=(project,),
            )


def render_draft(project: ProjectProfile) -> None:
    draft = st.session_state.get(DRAFT_STATE_KEY)
    if not draft or draft.get("projet") != project.name:
        return
    with st.container(key="draft"):
        st.markdown(
            '<span class="badge badge-accent">Prototype</span>'
            '<h3 class="draft-title">Demande prête — aucune génération lancée</h3>'
            '<p class="draft-text">Ce prototype n’appelle aucun fournisseur (texte, voix, image, musique) : '
            "rien n’a été envoyé ni facturé. Voici le brief qui serait transmis au moteur.</p>",
            unsafe_allow_html=True,
        )
        with st.expander("Voir le brief"):
            st.json(draft, expanded=True)


def render_profile_card(project: ProjectProfile) -> None:
    rows = (
        ("Format", f"{project.format} · {project.duration}"),
        ("Voix", project.voice),
        ("Style", project.style),
        ("Sous-titres", project.subtitles),
    )
    items = "".join(f"<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>" for label, value in rows)
    st.markdown(
        '<section class="card profile-card" aria-label="Profil du projet">'
        '<p class="card-eyebrow">Profil du projet <span class="muted">· démo</span></p>'
        f'<h3 class="card-title">{esc(project.name)}</h3>'
        f'<dl class="kv">{items}</dl>'
        "</section>",
        unsafe_allow_html=True,
    )


def render_recents(project: ProjectProfile) -> None:
    st.markdown(
        '<section class="card recents-card" aria-label="Générations récentes">'
        '<p class="card-eyebrow">Générations récentes</p>'
        f'<div class="empty">{_EMPTY_ICON_SVG}'
        '<p class="empty-title">Rien ici pour l’instant</p>'
        f'<p class="empty-text">Les vidéos créées pour {esc(project.name)} apparaîtront ici, '
        "avec leur statut et leur aperçu.</p></div>"
        "</section>",
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    st.markdown(
        '<p class="footnote">Prototype visuel · l’ancienne interface reste disponible en parallèle.</p>',
        unsafe_allow_html=True,
    )


def render() -> None:
    init_state()
    project = current_project()
    st.query_params["projet"] = project.key
    inject_theme(project)
    render_header()
    render_hero(project)
    render_composer(project)
    render_draft(project)
    with st.container(key="lower"):
        left, right = st.columns([1, 1.15], gap="medium")
        with left:
            render_profile_card(project)
        with right:
            render_recents(project)
    render_footer()
