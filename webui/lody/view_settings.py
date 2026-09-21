"""Paramètres d'un projet : identité, contenu, visuels, voix et consignes permanentes.

Tout est stocké séparément pour chaque projet (colonnes SQLite + ``settings["brief"]``) et
alimente automatiquement le brief de la page « Nouvelle production ».
"""

from __future__ import annotations

import streamlit as st

from lody import brief as brief_lib
from lody import catalog, nav
from lody.projects import Project, ProjectNotFound, ProjectRepository, ProjectValidationError
from lody.theme import esc
from lody.view_form import ERRORS_KEY, States, error_under, provider_select


def _prefix(project_id: str) -> str:
    return f"set_{project_id}"


def _submit(repo: ProjectRepository, project_id: str) -> None:
    ss = st.session_state
    p = _prefix(project_id)
    duration = ss.get(f"{p}_duration") or (45, 60)
    scenes = ss.get(f"{p}_scenes") or (6, 8)
    try:
        project = repo.get(project_id)
        new_brief = {
            "duration_min": duration[0],
            "duration_max": duration[1],
            "audience": ss.get(f"{p}_audience"),
            "orientation": ss.get(f"{p}_orientation"),
            "narration_pace": ss.get(f"{p}_pace") or brief_lib.DEFAULT_BRIEF["narration_pace"],
            "scenes_per_minute_min": scenes[0],
            "scenes_per_minute_max": scenes[1],
            "voice_id": ss.get(f"{p}_voice_id"),
            "voice_model": ss.get(f"{p}_voice_model"),
            "structure": str(ss.get(f"{p}_structure") or "").splitlines(),
            "standing_instructions": ss.get(f"{p}_instructions"),
            "visual_rules": ss.get(f"{p}_visual_rules"),
            "visual_avoid": str(ss.get(f"{p}_visual_avoid") or "").splitlines(),
        }
        updated = repo.update(
            project_id,
            name=ss.get(f"{p}_name"),
            description=ss.get(f"{p}_description"),
            language=ss.get(f"{p}_language"),
            format=ss.get(f"{p}_format"),
            tone=ss.get(f"{p}_tone"),
            visual_style=ss.get(f"{p}_visual_style"),
            text_provider=ss.get(f"{p}_text_provider"),
            visual_provider=ss.get(f"{p}_visual_provider"),
            voice_provider=ss.get(f"{p}_voice_provider"),
            voice_name=ss.get(f"{p}_voice_name"),
            music_provider=ss.get(f"{p}_music_provider"),
            settings={**project.settings, brief_lib.BRIEF_KEY: new_brief},
        )
    except ProjectValidationError as error:
        ss[ERRORS_KEY] = error.errors
        return
    except ProjectNotFound:
        nav.flash("error", "Ce projet n’existe plus.")
        nav.go()
        return
    nav.flash("success", "Paramètres enregistrés. Le brief de production les utilise dès maintenant.")
    nav.go(nav.VIEW_PROJECT, updated.id)


def _cancel(project_id: str) -> None:
    nav.go(nav.VIEW_PROJECT, project_id)


def render(repo: ProjectRepository, project: Project, states: States) -> None:
    p = _prefix(project.id)
    current = brief_lib.brief_settings(project.settings)

    st.markdown(
        '<section class="hero"><p class="eyebrow">Paramètres</p>'
        f'<h1 class="hero-title">{esc(project.name)}</h1>'
        '<p class="hero-sub">Ces réglages sont propres à ce projet : ils s’appliquent à toutes ses vidéos '
        "et sont repris automatiquement dans le brief de production.</p></section>",
        unsafe_allow_html=True,
    )
    if st.session_state.get(ERRORS_KEY):
        st.markdown(
            '<div class="banner banner-error" role="alert">Quelques points sont à corriger avant d’enregistrer. '
            "Ils sont indiqués dans les onglets concernés.</div>",
            unsafe_allow_html=True,
        )

    with st.container(key="form_card"):
        with st.form(f"{p}_form", border=False):
            tab_general, tab_content, tab_visual, tab_voice = st.tabs(
                ["Général", "Contenu", "Visuels", "Voix"]
            )

            with tab_general:
                st.text_input("Nom du projet", value=project.name, max_chars=80, key=f"{p}_name")
                error_under("name")
                st.text_area("Description", value=project.description, height=96, max_chars=500,
                             key=f"{p}_description")
                error_under("description")
                with st.container(horizontal=True, vertical_alignment="top", key=f"{p}_row_a"):
                    languages = catalog.values(catalog.LANGUAGES)
                    st.selectbox(
                        "Langue", languages,
                        index=languages.index(project.language) if project.language in languages else 0,
                        format_func=lambda value: catalog.label(catalog.LANGUAGES, value), key=f"{p}_language",
                    )
                    formats = catalog.values(catalog.FORMATS)
                    st.selectbox(
                        "Format vidéo", formats,
                        index=formats.index(project.format) if project.format in formats else 0,
                        format_func=lambda value: catalog.label(catalog.FORMATS, value), key=f"{p}_format",
                    )
                error_under("language")
                error_under("format")
                low, high = brief_lib.DURATION_LIMITS
                st.slider(
                    "Durée cible (secondes)", min_value=min(low, current["duration_min"]),
                    max_value=max(180, current["duration_max"]), step=5,
                    value=(current["duration_min"], current["duration_max"]), key=f"{p}_duration",
                )
                error_under("brief.duration_min")

            with tab_content:
                st.text_input("Public cible", value=current["audience"], max_chars=200, key=f"{p}_audience",
                              placeholder="Ex. Débutants complets")
                error_under("brief.audience")
                st.text_input("Orientation éditoriale", value=current["orientation"], max_chars=200,
                              key=f"{p}_orientation", placeholder="Ex. Progression rapide vers un sujet précis")
                error_under("brief.orientation")
                st.text_input("Ton éditorial", value=project.tone, max_chars=60, key=f"{p}_tone",
                              placeholder="Ex. Simple, honnête, dynamique")
                error_under("tone")
                st.text_area("Structure type d’une vidéo (une étape par ligne)",
                             value="\n".join(current["structure"]), height=210, key=f"{p}_structure",
                             help="Les étapes seront suivies dans cet ordre. Laisse vide pour une structure libre.")
                error_under("brief.structure")
                st.text_area("Consignes permanentes", value=current["standing_instructions"], height=320,
                             max_chars=brief_lib.INSTRUCTIONS_MAX, key=f"{p}_instructions",
                             help="Règles à respecter dans toutes les vidéos du projet (une par ligne).")
                error_under("brief.standing_instructions")

            with tab_visual:
                st.text_area("Style visuel", value=project.visual_style, height=110, max_chars=200,
                             key=f"{p}_visual_style", placeholder="Ex. Univers sombre et moderne, sans texte")
                error_under("visual_style")
                st.text_area("Consignes visuelles du projet", value=current["visual_rules"], height=110,
                             max_chars=brief_lib.VISUAL_RULES_MAX, key=f"{p}_visual_rules",
                             help="Univers, ambiance, type de scènes. Propre à ce projet : jamais partagé avec un autre.")
                error_under("brief.visual_rules")
                st.text_area("À ne jamais montrer (un élément par ligne)", value="\n".join(current["visual_avoid"]), height=130,
                             key=f"{p}_visual_avoid",
                             help="Liste négative propre à ce projet, ajoutée à chaque prompt d'image de ce projet uniquement.")
                error_under("brief.visual_avoid")
                st.slider(
                    "Rythme visuel (scènes par minute)", min_value=1, max_value=20,
                    value=(min(current["scenes_per_minute_min"], 20), min(current["scenes_per_minute_max"], 20)),
                    key=f"{p}_scenes",
                )
                error_under("brief.scenes_per_minute_min")
                st.caption("Une scène = un visuel affiché avant de passer au suivant.")

            with tab_voice:
                provider_select(p, "voice_provider", "Fournisseur de voix", catalog.VOICE_PROVIDERS,
                                project.voice_provider, states)
                st.text_input("Voix ElevenLabs", value=project.voice_name, max_chars=80, key=f"{p}_voice_name",
                              placeholder="Ex. Kev - Young, Dynamic and Bright")
                error_under("voice_name")
                st.text_input("Identifiant de la voix", value=current["voice_id"], max_chars=40, key=f"{p}_voice_id",
                              help="Repris depuis la bibliothèque de voix ElevenLabs. Ce n’est pas une clé d’accès.")
                error_under("brief.voice_id")
                paces = catalog.values(brief_lib.NARRATION_PACES)
                st.segmented_control(
                    "Rythme de narration", paces, default=current["narration_pace"],
                    format_func=lambda value: catalog.label(brief_lib.NARRATION_PACES, value), key=f"{p}_pace",
                )
                error_under("brief.narration_pace")
                with st.expander("Autres fournisseurs et modèle de voix"):
                    st.text_input("Modèle de voix", value=current["voice_model"], max_chars=60, key=f"{p}_voice_model")
                    error_under("brief.voice_model")
                    provider_select(p, "text_provider", "Rédaction du script", catalog.TEXT_PROVIDERS,
                                    project.text_provider, states)
                    provider_select(p, "visual_provider", "Images et vidéos", catalog.VISUAL_PROVIDERS,
                                    project.visual_provider, states)
                    provider_select(p, "music_provider", "Musique", catalog.MUSIC_PROVIDERS,
                                    project.music_provider, states)
                    st.caption("Les clés d’accès restent dans la configuration du serveur : elles ne se saisissent jamais ici.")

            error_under("settings")
            with st.container(horizontal=True, key=f"{p}_actions"):
                st.form_submit_button("Enregistrer les paramètres", type="primary", icon=":material/check:",
                                      on_click=_submit, args=(repo, project.id))
                st.form_submit_button("Annuler", on_click=_cancel, args=(project.id,))
