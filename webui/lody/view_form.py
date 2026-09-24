"""Création d'un projet (la modification se fait dans la page Paramètres)."""

from __future__ import annotations

import streamlit as st

from lody import catalog, nav
from lody.catalog import PROVIDER_KIND_BY_FIELD
from lody.projects import ProjectValidationError, ProjectRepository
from lody.generation.models import CapabilityState as CS
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


_USABLE = (CS.READY, CS.NOT_NEEDED, CS.DISABLED)
_SUFFIX = {CS.READY: " — configuré", CS.NOT_CONFIGURED: " — à configurer", CS.UNAVAILABLE: " — indisponible",
           CS.UNVERIFIED: " — non vérifié"}
States = dict[tuple[str, str], CS]


def provider_select(prefix: str, field: str, label: str, options, current: str, states: States,
                    *, new: bool = False) -> None:
    """Liste de fournisseurs avec leur état réel.

    Pour un nouveau projet, la valeur par défaut est le premier fournisseur réellement utilisable. Un choix
    explicite n'est jamais remplacé en silence : s'il n'est pas utilisable, on le dit sous la liste.
    """
    kind = PROVIDER_KIND_BY_FIELD[field]
    values = catalog.values(options)
    if new and states.get((kind, current), CS.READY) not in _USABLE:
        current = next((value for value in values if states.get((kind, value), CS.READY) in _USABLE), current)

    def display(value: str) -> str:
        return catalog.label(options, value) + _SUFFIX.get(states.get((kind, value), CS.READY), "")

    key = f"{prefix}_{field}"
    # setdefault (jamais index=) : si un autre composant a déjà pré-rempli cette clé avant que ce widget ne
    # soit instancié (ex. #59 : sélection d'une voix ElevenLabs qui doit aussi renseigner voice_provider),
    # passer index= EN PLUS d'une valeur déjà présente dans session_state est ambigu pour Streamlit — la
    # valeur réellement soumise au submit suit alors index=, pas la pré-sélection (vérifié : le formulaire
    # enregistrait la voix précédente malgré un widget affichant la bonne valeur). setdefault ne touche rien
    # quand la clé existe déjà, donc ce changement est un no-op pour tout appelant qui n'en pré-remplit pas.
    st.session_state.setdefault(key, current if current in values else values[0])
    st.selectbox(label, values, format_func=display, key=key)
    chosen = st.session_state.get(f"{prefix}_{field}", current)
    state = states.get((kind, chosen), CS.READY)
    if state not in _USABLE:
        st.markdown(
            f'<p class="field-warn" role="status">« {esc(catalog.label(options, chosen))} » n’est pas utilisable pour '
            "l’instant (fournisseur non configuré ou indisponible sur le serveur). Aucun autre fournisseur n’est choisi "
            "à ta place : choisis-en un qui est configuré, ou fais régler celui-ci.</p>",
            unsafe_allow_html=True,
        )
    error_under(field)


def render(repo: ProjectRepository, states: States) -> None:
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
                                _DEFAULTS["text_provider"], states, new=True)
                provider_select(PREFIX, "visual_provider", "Images et vidéos", catalog.VISUAL_PROVIDERS,
                                _DEFAULTS["visual_provider"], states, new=True)
                provider_select(PREFIX, "voice_provider", "Voix", catalog.VOICE_PROVIDERS,
                                _DEFAULTS["voice_provider"], states, new=True)
                st.text_input(
                    "Voix choisie", placeholder="Ex. Kev - Young, Dynamic and Bright",
                    max_chars=80, key=f"{PREFIX}_voice_name",
                )
                error_under("voice_name")
                provider_select(PREFIX, "music_provider", "Musique", catalog.MUSIC_PROVIDERS,
                                _DEFAULTS["music_provider"], states, new=True)
                st.caption("Les clés d’accès des fournisseurs ne se saisissent jamais ici : elles restent dans la configuration du serveur.")
                error_under("settings")

            with st.container(horizontal=True, key=f"{PREFIX}_actions"):
                st.form_submit_button("Créer le projet", type="primary", icon=":material/check:",
                                      on_click=_submit, args=(repo,))
                st.form_submit_button("Annuler", on_click=_cancel)
