"""Création et modification d'un projet."""

from __future__ import annotations

import streamlit as st

from lody import catalog, nav
from lody.catalog import PROVIDER_KIND_BY_FIELD
from lody.projects import Project, ProjectNotFound, ProjectRepository, ProjectValidationError
from lody.provider_status import Readiness, is_ready
from lody.theme import esc

ERRORS_KEY = "form_errors"
_FORM_FIELDS = (
    "name", "description", "content_type", "language", "platforms", "format", "tone",
    "visual_style", "text_provider", "visual_provider", "voice_provider", "voice_name", "music_provider",
)


def _prefix(mode: str, project_id: str | None) -> str:
    return f"form_{mode}_{project_id or 'new'}"


def _clear_form_state(prefix: str) -> None:
    for key in [k for k in st.session_state if str(k).startswith(prefix + "_")]:
        del st.session_state[key]


def _collect(prefix: str) -> dict:
    values = {field: st.session_state.get(f"{prefix}_{field}") for field in _FORM_FIELDS}
    values["platforms"] = list(values["platforms"] or [])
    return values


def _submit(repo: ProjectRepository, mode: str, project_id: str | None) -> None:
    prefix = _prefix(mode, project_id)
    fields = _collect(prefix)
    try:
        if mode == "edit" and project_id:
            project = repo.update(project_id, **fields)
            message = "Modifications enregistrées."
        else:
            project = repo.create(**fields)
            message = f"Projet « {project.name} » créé. Tu peux maintenant commencer une production."
    except ProjectValidationError as error:
        st.session_state[ERRORS_KEY] = error.errors
        return
    except ProjectNotFound:
        nav.flash("error", "Ce projet n’existe plus.")
        nav.go()
        return
    st.session_state.pop(ERRORS_KEY, None)
    _clear_form_state(prefix)
    nav.flash("success", message)
    nav.go(nav.VIEW_PROJECT, project.id)


def _cancel(mode: str, project_id: str | None) -> None:
    st.session_state.pop(ERRORS_KEY, None)
    _clear_form_state(_prefix(mode, project_id))
    if project_id:
        nav.go(nav.VIEW_PROJECT, project_id)
    else:
        nav.go()


def _error_under(field: str) -> None:
    message = st.session_state.get(ERRORS_KEY, {}).get(field)
    if message:
        st.markdown(f'<p class="field-error" role="alert">{esc(message)}</p>', unsafe_allow_html=True)


def _provider_select(prefix: str, field: str, label: str, options, current: str, table: Readiness) -> None:
    kind = PROVIDER_KIND_BY_FIELD[field]
    values = catalog.values(options)

    def display(value: str) -> str:
        text = catalog.label(options, value)
        if (kind, value) in catalog.PROVIDER_REQUIREMENTS and not is_ready(kind, value, table):
            return f"{text} — clé à configurer"
        return text

    st.selectbox(
        label,
        values,
        index=values.index(current) if current in values else 0,
        format_func=display,
        key=f"{prefix}_{field}",
    )
    _error_under(field)


def render(repo: ProjectRepository, table: Readiness, project: Project | None = None) -> None:
    mode = "edit" if project else "new"
    prefix = _prefix(mode, project.id if project else None)
    current = {
        field: getattr(project, field) if project else default
        for field, default in {
            "name": "", "description": "", "content_type": "pedagogique", "language": "fr-FR",
            "platforms": ["youtube_shorts", "tiktok", "instagram_reels"], "format": "9:16",
            "tone": "Pédagogique et dynamique", "visual_style": "", "text_provider": "openai",
            "visual_provider": "openai_image", "voice_provider": "elevenlabs", "voice_name": "",
            "music_provider": "none",
        }.items()
    }

    title = f"Modifier « {project.name} »" if project else "Nouveau projet"
    subtitle = (
        "Ajuste les réglages de ce projet."
        if project
        else "Quelques informations suffisent : tu pourras tout modifier ensuite."
    )
    st.markdown(
        f'<section class="hero"><p class="eyebrow">{"Modification" if project else "Création"}</p>'
        f'<h1 class="hero-title">{title}</h1><p class="hero-sub">{subtitle}</p></section>',
        unsafe_allow_html=True,
    )

    errors = st.session_state.get(ERRORS_KEY)
    if errors:
        st.markdown(
            '<div class="banner banner-error" role="alert">Quelques points sont à corriger avant de continuer.</div>',
            unsafe_allow_html=True,
        )

    with st.container(key="form_card"):
        with st.form(f"{prefix}_form", border=False):
            st.text_input(
                "Nom du projet (obligatoire)",
                value=current["name"],
                placeholder="Ex. Ma chaîne Web3",
                max_chars=80,
                key=f"{prefix}_name",
            )
            _error_under("name")
            st.text_area(
                "Description (facultative)",
                value=current["description"],
                placeholder="En une phrase : de quoi parlent tes vidéos ?",
                height=96,
                max_chars=500,
                key=f"{prefix}_description",
            )
            _error_under("description")

            with st.container(horizontal=True, vertical_alignment="top", key=f"{prefix}_row"):
                types = catalog.values(catalog.CONTENT_TYPES)
                st.selectbox(
                    "Type de contenu",
                    types,
                    index=types.index(current["content_type"]) if current["content_type"] in types else 0,
                    format_func=lambda value: catalog.label(catalog.CONTENT_TYPES, value),
                    key=f"{prefix}_content_type",
                )
                languages = catalog.values(catalog.LANGUAGES)
                st.selectbox(
                    "Langue",
                    languages,
                    index=languages.index(current["language"]) if current["language"] in languages else 0,
                    format_func=lambda value: catalog.label(catalog.LANGUAGES, value),
                    key=f"{prefix}_language",
                )
            _error_under("content_type")
            _error_under("language")

            st.pills(
                "Où veux-tu publier ?",
                catalog.values(catalog.PLATFORMS),
                selection_mode="multi",
                default=current["platforms"],
                format_func=lambda value: catalog.label(catalog.PLATFORMS, value),
                key=f"{prefix}_platforms",
            )
            _error_under("platforms")

            with st.expander("Paramètres avancés"):
                formats = catalog.values(catalog.FORMATS)
                st.selectbox(
                    "Format",
                    formats,
                    index=formats.index(current["format"]) if current["format"] in formats else 0,
                    format_func=lambda value: catalog.label(catalog.FORMATS, value),
                    key=f"{prefix}_format",
                )
                tone_values = list(catalog.values(catalog.TONES))
                if current["tone"] and current["tone"] not in tone_values:
                    tone_values.append(current["tone"])
                st.selectbox(
                    "Ton éditorial",
                    tone_values,
                    index=tone_values.index(current["tone"]) if current["tone"] in tone_values else 0,
                    key=f"{prefix}_tone",
                )
                _error_under("tone")
                st.text_input(
                    "Style visuel",
                    value=current["visual_style"],
                    placeholder="Ex. Sombre et moderne, tons bleus, sans texte",
                    max_chars=200,
                    key=f"{prefix}_visual_style",
                )
                _error_under("visual_style")
                _provider_select(prefix, "text_provider", "Rédaction du script", catalog.TEXT_PROVIDERS,
                                 current["text_provider"], table)
                _provider_select(prefix, "visual_provider", "Images et vidéos", catalog.VISUAL_PROVIDERS,
                                 current["visual_provider"], table)
                _provider_select(prefix, "voice_provider", "Voix", catalog.VOICE_PROVIDERS,
                                 current["voice_provider"], table)
                st.text_input(
                    "Voix choisie",
                    value=current["voice_name"] if project else "",
                    placeholder="Ex. Kev - Young, Dynamic and Bright",
                    max_chars=80,
                    key=f"{prefix}_voice_name",
                )
                _error_under("voice_name")
                _provider_select(prefix, "music_provider", "Musique", catalog.MUSIC_PROVIDERS,
                                 current["music_provider"], table)
                st.caption("Les clés d’accès des fournisseurs ne se saisissent jamais ici : elles restent dans la configuration du serveur.")
                _error_under("settings")

            with st.container(horizontal=True, key=f"{prefix}_actions"):
                st.form_submit_button(
                    "Enregistrer" if project else "Créer le projet",
                    type="primary",
                    icon=":material/check:",
                    on_click=_submit,
                    args=(repo, mode, project.id if project else None),
                )
                st.form_submit_button(
                    "Annuler",
                    on_click=_cancel,
                    args=(mode, project.id if project else None),
                )
