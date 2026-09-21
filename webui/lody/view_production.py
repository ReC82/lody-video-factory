"""Nouvelle production : demande + brief final (la génération n'est pas branchée)."""

from __future__ import annotations

import streamlit as st

from lody import brief as brief_lib
from lody import nav
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
    """Fige le brief en mémoire de session. N'appelle AUCUN fournisseur."""
    keys = _keys(project)
    request = str(st.session_state.get(keys["request"], "")).strip()
    if len(request) < MIN_REQUEST_LENGTH:
        st.session_state[keys["error"]] = True
        st.session_state[keys["draft"]] = None
        return
    st.session_state[keys["error"]] = False
    st.session_state[keys["draft"]] = {**brief_lib.build_brief(project, request), "fournisseurs_appeles": []}


def _rows(pairs: list[tuple[str, str]]) -> str:
    items = "".join(f"<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>" for label, value in pairs if value)
    return f'<dl class="kv">{items}</dl>' if items else '<p class="muted-note">Non renseigné</p>'


def brief_html(brief: dict) -> str:
    """Aperçu lisible du brief (toutes les valeurs sont échappées)."""
    request = (
        f'<p class="brief-request">{esc(brief["demande"])}</p>'
        if brief["demande"]
        else '<p class="brief-request brief-placeholder">Ta demande apparaîtra ici dès que tu l’auras saisie.</p>'
    )
    stats = "".join(
        f'<div class="stat"><span class="stat-value">{esc(value)}</span><span class="stat-label">{esc(label)}</span></div>'
        for label, value in (
            ("Durée cible", brief["duree_cible"]),
            ("Texte estimé", brief["reperes"]["mots"]),
            ("Visuels estimés", brief["reperes"]["scenes"]),
        )
    )
    steps = (
        '<ol class="timeline">' + "".join(f"<li>{esc(step)}</li>" for step in brief["structure"]) + "</ol>"
        if brief["structure"]
        else '<p class="muted-note">Structure libre</p>'
    )
    rules = (
        '<ul class="rules">'
        + "".join(f"<li>{esc(line)}</li>" for line in brief["consignes_permanentes"].splitlines() if line.strip())
        + "</ul>"
        if brief["consignes_permanentes"]
        else '<p class="muted-note">Aucune consigne permanente</p>'
    )
    voice = brief["voix"]
    voice_label = " · ".join(filter(None, [voice["fournisseur"], voice["nom"]]))
    return (
        f'<div class="brief-block brief-wide"><p class="card-eyebrow">Demande</p>{request}</div>'
        f'<div class="brief-block brief-wide"><div class="stats">{stats}</div>'
        '<p class="muted-note">Repères estimés à partir du rythme de narration et du rythme visuel du projet.</p></div>'
        f'<div class="brief-block"><p class="card-eyebrow">Public et ton</p>'
        f'{_rows([("Public", brief["public"]), ("Orientation", brief["orientation"]), ("Ton", brief["ton"])])}</div>'
        f'<div class="brief-block"><p class="card-eyebrow">Format</p>'
        f'{_rows([("Format", brief["format"]), ("Langue", brief["langue"]), ("Narration", brief["narration"])])}</div>'
        f'<div class="brief-block brief-wide"><p class="card-eyebrow">Visuels et voix</p>'
        f'{_rows([("Style", brief["visuels"]["style"]), ("Rythme", brief["visuels"]["rythme"]), ("Voix", voice_label)])}</div>'
        f'<div class="brief-block brief-wide"><p class="card-eyebrow">Structure de la vidéo</p>{steps}</div>'
        f'<div class="brief-block brief-wide"><p class="card-eyebrow">Consignes permanentes</p>{rules}</div>'
    )


def render(project: Project) -> None:
    keys = _keys(project)
    st.markdown(
        f'<section class="hero"><p class="eyebrow">{esc(project.name)}</p>'
        '<h1 class="hero-title">Nouvelle production</h1>'
        '<p class="hero-sub">Décris la vidéo que tu veux : les réglages du projet sont ajoutés automatiquement.</p></section>',
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
                "Le brief ci-dessous est celui qui sera transmis au moteur.</p>",
                unsafe_allow_html=True,
            )

    # Aperçu vivant : la demande saisie + les paramètres enregistrés du projet.
    live_request = str(st.session_state.get(keys["request"], ""))
    brief = brief_lib.build_brief(project, live_request)
    with st.container(key="brief_preview"):
        with st.container(horizontal=True, vertical_alignment="center", key="brief_head"):
            st.markdown(
                '<div><p class="card-eyebrow">Aperçu avant génération</p>'
                '<h2 class="brief-title">Brief final</h2></div>',
                unsafe_allow_html=True,
            )
            st.button("Ajuster les paramètres", icon=":material/tune:", key="adjust_settings",
                      on_click=nav.go, args=(nav.VIEW_SETTINGS, project.id))
        st.markdown(f'<div class="brief-grid">{brief_html(brief)}</div>', unsafe_allow_html=True)
        with st.expander("Version texte à copier"):
            st.code(brief_lib.brief_to_text(brief), language=None)

    with st.container(horizontal=True, key="production_nav"):
        st.button("Retour au projet", icon=":material/arrow_back:", type="tertiary", key="back_project",
                  on_click=nav.go, args=(nav.VIEW_PROJECT, project.id))
