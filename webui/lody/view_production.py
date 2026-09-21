"""Nouvelle production : brief de la vidéo (génération non branchée)."""

from __future__ import annotations

import streamlit as st

from lody import catalog, nav
from lody.projects import Project
from lody.theme import esc

MIN_REQUEST_LENGTH = 8
_GENERIC_EXAMPLE = "Explique simplement un sujet de ton domaine en moins d’une minute."


def _keys(project: Project) -> dict[str, str]:
    return {
        "request": f"request_{project.id}",
        "draft": f"draft_{project.id}",
        "error": f"request_error_{project.id}",
    }


def example_for(project: Project) -> str:
    example = project.settings.get("example_request")
    return str(example) if example else _GENERIC_EXAMPLE


def _use_example(project: Project) -> None:
    keys = _keys(project)
    st.session_state[keys["request"]] = example_for(project)
    st.session_state[keys["error"]] = False


def _prepare_draft(project: Project) -> None:
    """Compose un brouillon en mémoire de session. N'appelle AUCUN fournisseur."""
    keys = _keys(project)
    request = str(st.session_state.get(keys["request"], "")).strip()
    if len(request) < MIN_REQUEST_LENGTH:
        st.session_state[keys["error"]] = True
        st.session_state[keys["draft"]] = None
        return
    st.session_state[keys["error"]] = False
    st.session_state[keys["draft"]] = {
        "projet": project.name,
        "demande": request,
        "format": catalog.label(catalog.FORMATS, project.format),
        "langue": catalog.label(catalog.LANGUAGES, project.language),
        "ton": project.tone,
        "style_visuel": project.visual_style,
        "voix": project.voice_name,
        "fournisseurs_appeles": [],
    }


def render(project: Project) -> None:
    keys = _keys(project)
    st.markdown(
        f'<section class="hero"><p class="eyebrow">{esc(project.name)}</p>'
        '<h1 class="hero-title">Nouvelle production</h1>'
        '<p class="hero-sub">Décris la vidéo que tu veux : on prépare le brief.</p></section>',
        unsafe_allow_html=True,
    )
    with st.container(key="composer"):
        st.markdown('<h2 class="composer-title">Que veux-tu créer ?</h2>', unsafe_allow_html=True)
        st.text_area(
            "Que veux-tu créer ?",
            key=keys["request"],
            placeholder="Décris la vidéo que tu veux…",
            height=132,
            max_chars=2000,
            label_visibility="collapsed",
        )
        if st.session_state.get(keys["error"]):
            st.markdown(
                '<p class="field-error" role="alert">Décris d’abord ta vidéo en quelques mots.</p>',
                unsafe_allow_html=True,
            )
        with st.container(horizontal=True, vertical_alignment="center", key="composer_actions"):
            st.markdown(
                f'<p class="example"><span>Exemple</span> {esc(example_for(project))}</p>',
                unsafe_allow_html=True,
            )
            st.button("Utiliser l’exemple", type="tertiary", key="use_example",
                      on_click=_use_example, args=(project,))
            st.button("Générer la vidéo", type="primary", icon=":material/auto_awesome:", key="generate",
                      on_click=_prepare_draft, args=(project,))

    draft = st.session_state.get(keys["draft"])
    if draft:
        with st.container(key="draft"):
            st.markdown(
                '<span class="badge badge-accent">Bêta</span>'
                '<h3 class="draft-title">Brief prêt — aucune génération lancée</h3>'
                '<p class="draft-text">La génération de vidéos n’est pas encore branchée : rien n’a été envoyé '
                "à un fournisseur (texte, voix, image, musique) et rien n’a été facturé. "
                "Voici le brief qui sera transmis au moteur.</p>",
                unsafe_allow_html=True,
            )
            with st.expander("Voir le brief"):
                st.json(draft, expanded=True)

    with st.container(horizontal=True, key="production_nav"):
        st.button("Retour au projet", icon=":material/arrow_back:", type="tertiary", key="back_project",
                  on_click=nav.go, args=(nav.VIEW_PROJECT, project.id))
