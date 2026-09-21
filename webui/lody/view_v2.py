"""Création d'une V2 : script modifiable, durée et coût recalculés, nouvelle confirmation."""

from __future__ import annotations

import streamlit as st

from lody import nav, view_estimate
from lody.generation.models import ProductionStatus as S
from lody.generation.provider import WORDS_PER_MINUTE
from lody.generation.runtime import DEMO_PROVIDER
from lody.generation.service import (
    AlreadyRunning,
    LaunchError,
    ProductionService,
    request_of,
)
from lody.generation.store import Production
from lody.projects import Project
from lody.theme import esc
from lody.view_tracking import storyboard_html


def _keys(production: Production) -> dict[str, str]:
    return {"script": f"v2_script_{production.id}", "error": f"v2_error_{production.id}",
            "accept": f"accept_partial_{production.id}"}


def approximate_length(script: str, pace: str) -> str:
    words = len(script.split())
    seconds = round(words / WORDS_PER_MINUTE.get(pace, WORDS_PER_MINUTE["normal"]) * 60)
    return f"≈ {seconds} secondes · {words} mots"


def _recalculate(service: ProductionService, production: Production) -> None:
    keys = _keys(production)
    st.session_state[keys["error"]] = ""
    try:
        service.prepare_v2(production.id, str(st.session_state.get(keys["script"], "")))
    except LaunchError as error:
        st.session_state[keys["error"]] = error.message


def _confirm(service: ProductionService, project: Project, production: Production) -> None:
    keys = _keys(production)
    st.session_state[keys["error"]] = ""
    try:
        launched = service.confirm(production.id, accept_partial=bool(st.session_state.get(keys["accept"])))
        nav.go(nav.VIEW_TRACK, project.id, launched.id)
    except AlreadyRunning as error:
        st.session_state[keys["error"]] = error.message
    except LaunchError as error:
        st.session_state[keys["error"]] = error.message


def render(service: ProductionService, project: Project, production_id: str) -> None:
    try:
        production = service.repo.get(production_id)
        if production.project_id != project.id or not production.parent_production_id:
            raise LookupError(production_id)
    except LookupError:
        nav.flash("error", "Cette version est introuvable.")
        nav.go(nav.VIEW_PROJECT, project.id)
        st.rerun()
        return
    if production.status not in (S.BROUILLON, S.EN_ATTENTE_CONFIRMATION):
        nav.go(nav.VIEW_TRACK, project.id, production.id)  # déjà lancée : on ouvre son suivi
        st.rerun()
        return

    keys = _keys(production)
    parent = service.repo.get(production.parent_production_id)
    request = request_of(production)
    demo = production.provider == DEMO_PROVIDER
    st.session_state.setdefault(keys["script"], production.script)
    st.markdown(
        f'<section class="hero"><p class="eyebrow">{esc(project.name)} · {esc(production.label)}</p>'
        '<h1 class="hero-title">Créer une V2</h1>'
        f'<p class="hero-sub">Modifie le script de la {esc(parent.label)}, puis relance : {esc(production.subject)}</p></section>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="banner banner-info" role="note"><strong>Une V2 régénère tout.</strong> Voix, images et musique sont '
        "produites de nouveau (nouveaux appels payants) : aucun média de la version précédente n’est réutilisé dans cette "
        f"version du produit. La {esc(parent.label)} reste intacte.</div>",
        unsafe_allow_html=True,
    )

    text = str(st.session_state.get(keys["script"], ""))
    fresh = production.status is S.EN_ATTENTE_CONFIRMATION and production.script == text.strip()
    with st.container(key="composer"):
        st.markdown('<h2 class="composer-title">Script de la V2</h2>', unsafe_allow_html=True)
        st.text_area("Script de la V2", key=keys["script"], height=320, max_chars=8000, label_visibility="collapsed")
        st.markdown(f'<p class="muted-note">Durée approximative : {esc(approximate_length(text, request.narration_pace))}</p>',
                    unsafe_allow_html=True)
        if st.session_state.get(keys["error"]):
            st.markdown(f'<p class="field-error" role="alert">{esc(st.session_state[keys["error"]])}</p>',
                        unsafe_allow_html=True)
        report = service.preflight(production) if fresh else None
        accepted = bool(st.session_state.get(keys["accept"])) or not production.cost_partial or demo
        with st.container(horizontal=True, vertical_alignment="center", key="composer_actions"):
            if fresh:
                st.button("Confirmer et générer la V2", type="primary", icon=":material/rocket_launch:", key="confirm_v2",
                          on_click=_confirm, args=(service, project, production),
                          disabled=not report.ready or not accepted)
            else:
                st.button("Recalculer la durée et le coût", type="primary", icon=":material/calculate:", key="recalculate_v2",
                          on_click=_recalculate, args=(service, production))
            st.button("Retour à la " + parent.label, type="tertiary", icon=":material/arrow_back:", key="back_parent",
                      on_click=nav.go, args=(nav.VIEW_TRACK, project.id, parent.id))
    if fresh:
        view_estimate.render_panel(production, report, demo=demo, key=production.id, v2=True, project=project)
        with st.expander("Storyboard recalculé"):
            st.markdown(storyboard_html(production), unsafe_allow_html=True)
