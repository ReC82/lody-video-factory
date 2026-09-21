"""Création d'un projet (la modification se fait dans la page Paramètres)."""

from __future__ import annotations

import streamlit as st

from lody import catalog, nav
from lody.catalog import PROVIDER_KIND_BY_FIELD
from lody.projects import ProjectValidationError, ProjectRepository
from lody.provider_status import Readiness, is_ready
from lody.theme import esc

ERRORS_KEY = "form_errors"
PREFIX = "form_new"
_FORM_FIELDS = (
    "name", "description", "content_type", "language", "platforms", "format", "tone",
    "visual_style", "text_provider", "visual_provider", "voice_provider", "voice_name", "music_provider",
)
_DEFAULTS = {
    "name": "", "description": "", "content_type": "pedagogique", "language": "fr-FR",
    "platforms": ["youtube_shorts", "tiktok", "instagram_reels"], "format": "9:16",
    "tone": "Pédagogique et dynamique", "visual_style": "", "text_provider": "openai",
    "visual_provider": "openai_image", "voice_provider": "elevenlabs", "voice_name": "",
    "music_provider": "none",
}


def _clear_form_state() -> None:
    for key in [k for k in st.session_state if str(k).startswith(PREFIX + "_")]:
        del st.session_state[key]


def _collect() -> dict:
    values = {field: st.session_state.get(f"{PREFIX}_{field}") for field in _FORM_FIELDS}
    values["platforms"] = list(values["platforms"] or [])
    return values


def _submit(repo: ProjectRepository) -> None:
    try:
        project = repo.create(**_collect())
    except ProjectValidationError as error:
        st.session_state[ERRORS_KEY] = error.errors
        return
    st.session_state.pop(ERRORS_KEY, None)
    _clear_form_state()
    nav.flash("success", f"Projet « {project.name} » créé. Ouvre ses paramètres pour le personnaliser.")
    nav.go(nav.VIEW_PROJECT, project.id)


def _cancel() -> None:
    st.session_state.pop(ERRORS_KEY, None)
    _clear_form_state()
    nav.go()


def error_under(field: str) -> None:
    """Affiche sous un champ le message d'erreur stocké par la dernière validation."""
    message = st.session_state.get(ERRORS_KEY, {}).get(field)
    if message:
        st.markdown(f'<p class="field-error" role="alert">{esc(message)}</p>', unsafe_allow_html=True)


def provider_select(prefix: str, field: str, label: str, options, current: str, table: Readiness) -> None:
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
    error_under(field)


def render(repo: ProjectRepository, table: Readiness) -> None:
    st.markdown(
        '<section class="hero"><p class="eyebrow">Création</p><h1 class="hero-title">Nouveau projet</h1>'
        '<p class="hero-sub">Quelques informations suffisent : tu pourras tout régler ensuite '
        "dans les paramètres du projet.</p></section>",
        unsafe_allow_html=True,
    )
    if st.session_state.get(ERRORS_KEY):
        st.markdown(
            '<div class="banner banner-error" role="alert">Quelques points sont à corriger avant de continuer.</div>',
            unsafe_allow_html=True,
        )

    with st.container(key="form_card"):
        with st.form(f"{PREFIX}_form", border=False):
            st.text_input(
                "Nom du projet (obligatoire)",
                placeholder="Ex. Ma chaîne Web3",
                max_chars=80,
                key=f"{PREFIX}_name",
            )
            error_under("name")
            st.text_area(
                "Description (facultative)",
                placeholder="En une phrase : de quoi parlent tes vidéos ?",
                height=96,
                max_chars=500,
                key=f"{PREFIX}_description",
            )
            error_under("description")

            with st.container(horizontal=True, vertical_alignment="top", key=f"{PREFIX}_row"):
                types = catalog.values(catalog.CONTENT_TYPES)
                st.selectbox(
                    "Type de contenu", types, format_func=lambda value: catalog.label(catalog.CONTENT_TYPES, value),
                    key=f"{PREFIX}_content_type",
                )
                languages = catalog.values(catalog.LANGUAGES)
                st.selectbox(
                    "Langue", languages, format_func=lambda value: catalog.label(catalog.LANGUAGES, value),
                    key=f"{PREFIX}_language",
                )
            error_under("content_type")
            error_under("language")

            st.pills(
                "Où veux-tu publier ?",
                catalog.values(catalog.PLATFORMS),
                selection_mode="multi",
                default=_DEFAULTS["platforms"],
                format_func=lambda value: catalog.label(catalog.PLATFORMS, value),
                key=f"{PREFIX}_platforms",
            )
            error_under("platforms")

            with st.expander("Paramètres avancés"):
                formats = catalog.values(catalog.FORMATS)
                st.selectbox(
                    "Format", formats, format_func=lambda value: catalog.label(catalog.FORMATS, value),
                    key=f"{PREFIX}_format",
                )
                tones = list(catalog.values(catalog.TONES))
                st.selectbox("Ton éditorial", tones, key=f"{PREFIX}_tone")
                error_under("tone")
                st.text_input(
                    "Style visuel", placeholder="Ex. Sombre et moderne, tons bleus, sans texte",
                    max_chars=200, key=f"{PREFIX}_visual_style",
                )
                error_under("visual_style")
                provider_select(PREFIX, "text_provider", "Rédaction du script", catalog.TEXT_PROVIDERS,
                                _DEFAULTS["text_provider"], table)
                provider_select(PREFIX, "visual_provider", "Images et vidéos", catalog.VISUAL_PROVIDERS,
                                _DEFAULTS["visual_provider"], table)
                provider_select(PREFIX, "voice_provider", "Voix", catalog.VOICE_PROVIDERS,
                                _DEFAULTS["voice_provider"], table)
                st.text_input(
                    "Voix choisie", placeholder="Ex. Kev - Young, Dynamic and Bright",
                    max_chars=80, key=f"{PREFIX}_voice_name",
                )
                error_under("voice_name")
                provider_select(PREFIX, "music_provider", "Musique", catalog.MUSIC_PROVIDERS,
                                _DEFAULTS["music_provider"], table)
                st.caption("Les clés d’accès des fournisseurs ne se saisissent jamais ici : elles restent dans la configuration du serveur.")
                error_under("settings")

            with st.container(horizontal=True, key=f"{PREFIX}_actions"):
                st.form_submit_button("Créer le projet", type="primary", icon=":material/check:",
                                      on_click=_submit, args=(repo,))
                st.form_submit_button("Annuler", on_click=_cancel)
