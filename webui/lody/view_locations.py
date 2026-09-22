"""Lieux récurrents d'un projet (ticket #33) : liste, création, modification, activation.

Symétrique à ``view_characters.py`` (#32). Entièrement facultatif : zéro lieu reste le cas nominal. Rien
ici n'est lu par une production — existante, en cours ou future — tant qu'aucune sélection explicite
n'existe (#34+). Utilise uniquement le modèle et le dépôt livrés par #31 (``lody.locations``).
"""

from __future__ import annotations

import streamlit as st

from lody import nav
from lody.components import EMPTY_ICON_SVG
from lody.locations import Location, LocationNotFound, LocationRepository, LocationValidationError
from lody.projects import Project
from lody.theme import esc

FORM_KEY = "_loc_form"  # (project_id, lieu_id ou None) tant que le formulaire est ouvert ; absent = fermé.
CONFIRM_KEY = "_loc_confirm_deactivate"
ERRORS_KEY = "_loc_form_errors"


def _form_target(project_id: str) -> tuple[bool, str | None]:
    """(formulaire ouvert ?, lieu édité ou None) pour CE projet.

    Ignore et referme tout état laissé par un autre projet : une navigation manipulée (``?projet=`` changé
    sans repasser par un lien) ne doit jamais montrer un formulaire préparé pour un autre projet."""
    stored = st.session_state.get(FORM_KEY)
    if not stored:
        return False, None
    stored_project, location_id = stored
    if stored_project != project_id:
        st.session_state.pop(FORM_KEY, None)
        return False, None
    return True, location_id


def _prefix(project_id: str, target: str | None) -> str:
    return f"loc_{project_id}_{target or 'new'}"


def _clear_form_state(project_id: str) -> None:
    for key in [k for k in st.session_state if str(k).startswith(f"loc_{project_id}_")]:
        del st.session_state[key]
    st.session_state.pop(ERRORS_KEY, None)


def _start_create(project_id: str) -> None:
    _clear_form_state(project_id)
    st.session_state[FORM_KEY] = (project_id, None)


def _start_edit(project_id: str, location_id: str) -> None:
    _clear_form_state(project_id)
    st.session_state[FORM_KEY] = (project_id, location_id)


def _cancel_form(project_id: str) -> None:
    _clear_form_state(project_id)
    st.session_state.pop(FORM_KEY, None)


def _ask_deactivate(location_id: str) -> None:
    st.session_state[CONFIRM_KEY] = location_id


def _cancel_deactivate() -> None:
    st.session_state.pop(CONFIRM_KEY, None)


def _deactivate(repo: LocationRepository, project_id: str, location_id: str) -> None:
    st.session_state.pop(CONFIRM_KEY, None)
    try:
        location = repo.deactivate(project_id, location_id)
    except LocationNotFound:
        nav.flash("error", "Ce lieu n’existe plus.")
        return
    nav.flash("success", f"« {location.name} » est désactivé. Il n’apparaîtra pas dans les choix, "
                         "mais reste modifiable et peut être réactivé à tout moment.")


def _activate(repo: LocationRepository, project_id: str, location_id: str) -> None:
    try:
        location = repo.activate(project_id, location_id)
    except LocationNotFound:
        nav.flash("error", "Ce lieu n’existe plus.")
        return
    nav.flash("success", f"« {location.name} » est de nouveau actif.")


def _submit(repo: LocationRepository, project_id: str, target: str | None) -> None:
    p = _prefix(project_id, target)
    ss = st.session_state
    fields = dict(
        name=ss.get(f"{p}_name", ""), location_type=ss.get(f"{p}_location_type", ""),
        description=ss.get(f"{p}_description", ""), reference_prompt=ss.get(f"{p}_reference_prompt", ""),
        continuity_notes=ss.get(f"{p}_continuity_notes", ""), is_primary=bool(ss.get(f"{p}_is_primary")),
    )
    try:
        if target:
            location = repo.update(project_id, target, **fields)
            message = f"« {location.name} » est modifié."
        else:
            location = repo.create(project_id, **fields)
            message = f"« {location.name} » est ajouté."
    except LocationValidationError as error:
        ss[ERRORS_KEY] = error.errors
        return
    except LocationNotFound:
        nav.flash("error", "Ce lieu n’existe plus.")
        _cancel_form(project_id)
        return
    _cancel_form(project_id)
    nav.flash("success", message)


def _error_under(field: str) -> None:
    message = st.session_state.get(ERRORS_KEY, {}).get(field)
    if message:
        st.markdown(f'<p class="field-error" role="alert">{esc(message)}</p>', unsafe_allow_html=True)


def _row(repo: LocationRepository, project: Project, location: Location) -> None:
    badges = []
    if location.is_primary:
        badges.append('<span class="pill pill-ok">Principal</span>')
    if not location.is_active:
        badges.append('<span class="pill pill-muted">Inactif</span>')
    details = [esc(location.location_type)] if location.location_type else []
    if location.continuity_notes:
        note = location.continuity_notes
        details.append(esc(note if len(note) <= 80 else note[:79] + "…"))
    meta_text = " · ".join(details) or "Type non précisé"
    with st.container(horizontal=True, vertical_alignment="center", key=f"item_row_{location.id}"):
        st.markdown(
            f'<div class="prod-info"><p class="prod-title">{esc(location.name)}</p>'
            f'<p class="prod-meta"><span>{meta_text}</span>{"".join(badges)}</p></div>',
            unsafe_allow_html=True,
        )
        with st.container(horizontal=True, key=f"item_actions_{location.id}"):
            st.button("Modifier", key=f"edit_{location.id}", icon=":material/edit:",
                      on_click=_start_edit, args=(project.id, location.id))
            if location.is_active:
                st.button("Désactiver", key=f"deactivate_{location.id}", type="tertiary",
                          on_click=_ask_deactivate, args=(location.id,))
            else:
                st.button("Réactiver", key=f"activate_{location.id}", type="tertiary",
                          on_click=_activate, args=(repo, project.id, location.id))
    if st.session_state.get(CONFIRM_KEY) == location.id:
        with st.container(key=f"confirm_deactivate_{location.id}"):
            st.markdown(
                f'<p class="confirm-title">Désactiver « {esc(location.name)} » ?</p>'
                '<p class="confirm-text">Il n’apparaîtra plus dans les choix d’une prochaine production, '
                "mais reste modifiable et peut être réactivé à tout moment.</p>",
                unsafe_allow_html=True,
            )
            with st.container(horizontal=True, key=f"confirm_deactivate_actions_{location.id}"):
                st.button("Oui, désactiver", type="primary", key=f"confirm_deactivate_yes_{location.id}",
                          on_click=_deactivate, args=(repo, project.id, location.id))
                st.button("Annuler", key=f"cancel_deactivate_{location.id}", on_click=_cancel_deactivate)


def _render_form(repo: LocationRepository, project: Project, target: str | None, current: Location | None) -> None:
    p = _prefix(project.id, target)
    title = f"Modifier « {current.name} »" if current else "Nouveau lieu"

    with st.container(key="form_card"):
        st.markdown(f'<p class="card-eyebrow">{esc(title)}</p>', unsafe_allow_html=True)
        with st.form(f"{p}_form", border=False):
            with st.container(horizontal=True, vertical_alignment="top", key=f"form_{p}_row_a"):
                st.text_input("Nom (obligatoire)", value=current.name if current else "", max_chars=80,
                              key=f"{p}_name", placeholder="Ex. Place du marché")
                st.text_input("Type de lieu", value=current.location_type if current else "", max_chars=60,
                              key=f"{p}_location_type", placeholder="Ex. Extérieur, intérieur, studio…")
            _error_under("name")
            _error_under("location_type")
            st.text_area("Description", value=current.description if current else "", height=90, max_chars=500,
                         key=f"{p}_description", placeholder="Ambiance, éléments visuels stables…")
            _error_under("description")
            st.text_area("Prompt de référence", value=current.reference_prompt if current else "", height=90,
                         max_chars=500, key=f"{p}_reference_prompt",
                         help="Utilisé plus tard, uniquement si ce lieu est sélectionné dans une production.")
            _error_under("reference_prompt")
            st.text_area("Notes de continuité", value=current.continuity_notes if current else "", height=90,
                         max_chars=500, key=f"{p}_continuity_notes",
                         placeholder="Ce qu’il faut retenir pour rester cohérent d’une vidéo à l’autre.")
            _error_under("continuity_notes")

            st.checkbox("Lieu principal", value=current.is_primary if current else False,
                        key=f"{p}_is_primary", help="Purement indicatif : plusieurs lieux peuvent être principaux.")

            with st.container(horizontal=True, key=f"form_{p}_actions"):
                st.form_submit_button("Enregistrer", type="primary", icon=":material/check:",
                                      on_click=_submit, args=(repo, project.id, target))
                st.form_submit_button("Annuler", on_click=_cancel_form, args=(project.id,))


def render(repo: LocationRepository, project: Project) -> None:
    st.markdown(
        '<section class="hero"><p class="eyebrow">Lieux</p>'
        f'<h1 class="hero-title">{esc(project.name)}</h1>'
        '<p class="hero-sub">Facultatif : décris les lieux récurrents de ce projet pour les retrouver '
        "plus tard dans une production. Sans lieu, tes vidéos se génèrent exactement comme aujourd’hui."
        "</p></section>",
        unsafe_allow_html=True,
    )

    if project.is_archived:
        st.markdown(
            '<div class="banner banner-info" role="status">Ce projet est archivé : restaure-le pour gérer '
            "ses lieux.</div>",
            unsafe_allow_html=True,
        )
        st.button("Retour au projet", icon=":material/arrow_back:", key="loc_back_archived",
                  on_click=nav.go, args=(nav.VIEW_PROJECT, project.id))
        return

    with st.container(horizontal=True, key="project_actions"):
        st.button("Retour au projet", icon=":material/arrow_back:", type="tertiary", key="loc_back",
                  on_click=nav.go, args=(nav.VIEW_PROJECT, project.id))

    is_open, target = _form_target(project.id)
    current: Location | None = None
    if is_open and target:
        try:
            current = repo.get(project.id, target)
        except LocationNotFound:
            # Identifiant inconnu ou navigation manipulée (id d'un autre projet) : referme sans planter.
            st.session_state.pop(FORM_KEY, None)
            is_open, target = False, None
            st.markdown(
                '<div class="banner banner-error" role="alert">Ce lieu n’existe plus.</div>',
                unsafe_allow_html=True,
            )
    if st.session_state.get(ERRORS_KEY):
        st.markdown(
            '<div class="banner banner-error" role="alert">Quelques points sont à corriger avant d’enregistrer.</div>',
            unsafe_allow_html=True,
        )

    locations = repo.list_for_project(project.id)
    if not locations:
        with st.container(key="empty_state"):
            st.markdown(
                f'<div class="empty">{EMPTY_ICON_SVG}<p class="empty-title">Aucun lieu pour l’instant</p>'
                '<p class="empty-text">Cette fonction est facultative : sans lieu, tes vidéos se génèrent '
                "normalement. Ajoute un lieu seulement si tu veux le retrouver d’une vidéo à l’autre.</p></div>",
                unsafe_allow_html=True,
            )
    else:
        with st.container(key="locations"):
            st.markdown('<p class="card-eyebrow">Lieux du projet</p>', unsafe_allow_html=True)
            for location in locations:
                _row(repo, project, location)

    if not is_open:
        st.button("Ajouter un lieu", type="primary", icon=":material/add:", key="add_location",
                  on_click=_start_create, args=(project.id,))
    else:
        _render_form(repo, project, target, current)
