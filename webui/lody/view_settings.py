"""Paramètres d'un projet : identité, contenu, visuels, voix et consignes permanentes.

Tout est stocké séparément pour chaque projet (colonnes SQLite + ``settings["brief"]``) et
alimente automatiquement le brief de la page « Nouvelle production ».
"""

from __future__ import annotations

import json

import streamlit as st

from lody import brief as brief_lib
from lody import catalog, nav, project_transfer, voice_picker
from lody.characters import CharacterRepository
from lody.generation.publication import slugify
from lody.locations import LocationRepository
from lody.projects import Project, ProjectNotFound, ProjectRepository, ProjectValidationError
from lody.theme import esc
from lody.view_form import ERRORS_KEY, States, error_under, provider_select


def _prefix(project_id: str) -> str:
    return f"set_{project_id}"


def _transfer_prefix(project_id: str) -> str:
    return f"impexp_{project_id}"


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
            "publication": {
                "playlist": ss.get(f"{p}_pub_playlist"), "category": ss.get(f"{p}_pub_category"),
                "hashtags": ss.get(f"{p}_pub_hashtags"), "tags": ss.get(f"{p}_pub_tags"),
                "disclaimer": ss.get(f"{p}_pub_disclaimer"), "series_blurb": ss.get(f"{p}_pub_blurb"),
                "comment_prompt": ss.get(f"{p}_pub_comment"), "made_for_kids": bool(ss.get(f"{p}_pub_kids")),
                "no_financial_claims": bool(ss.get(f"{p}_pub_nofin")),
            },
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


# -- import / export de configuration (#44) -----------------------------------------------------------------------
def _export_bytes(repo: ProjectRepository, character_repo: CharacterRepository, location_repo: LocationRepository,
                  project_id: str) -> bytes:
    project = repo.get(project_id)
    payload = project_transfer.export_project(
        project, character_repo.list_for_project(project_id), location_repo.list_for_project(project_id))
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _template_bytes() -> bytes:
    return json.dumps(project_transfer.blank_template(), ensure_ascii=False, indent=2).encode("utf-8")


def _confirm_import(repo: ProjectRepository, project_id: str, preview: project_transfer.ImportPreview) -> None:
    t = _transfer_prefix(project_id)
    try:
        created = project_transfer.commit_import(repo.db_path, preview)
    except project_transfer.TransferError as error:
        nav.flash("error", f"Import impossible : {error}")
        return
    st.session_state.pop(f"{t}_uploader", None)
    renamed = " (le nom demandé existait déjà, un nom distinct a été utilisé)" if preview.name_was_renamed else ""
    nav.flash("success", f"Projet « {created.name} » créé depuis le fichier importé{renamed}.")
    nav.go(nav.VIEW_PROJECT, created.id)


def _render_transfer_preview(preview: project_transfer.ImportPreview) -> None:
    if preview.errors:
        lines = [f"{esc(issue.path)} — {esc(issue.message)}" if issue.path else esc(issue.message)
                 for issue in preview.errors]
        st.markdown(
            '<div class="banner banner-error" role="alert"><strong>Fichier invalide, rien n’a été créé :</strong><br>'
            + "<br>".join(lines) + "</div>",
            unsafe_allow_html=True,
        )
        return
    summary = preview.project_summary
    details = [
        f"Langue : {catalog.label(catalog.LANGUAGES, summary.get('language', ''))}",
        f"Format : {catalog.label(catalog.FORMATS, summary.get('format', ''))}",
        f"Type de contenu : {catalog.label(catalog.CONTENT_TYPES, summary.get('content_type', ''))}",
        f"Ton : {summary.get('tone') or '—'}",
        f"Personnages ({len(preview.character_names)}) : {', '.join(preview.character_names) or 'aucun'}",
        f"Lieux ({len(preview.location_names)}) : {', '.join(preview.location_names) or 'aucun'}",
    ]
    st.markdown(
        f'<div class="banner banner-info" role="status">Aperçu — aucune écriture pour l’instant.<br>'
        f'Nouveau projet : <strong>{esc(preview.project_name)}</strong><br>'
        + "<br>".join(esc(line) for line in details) + "</div>",
        unsafe_allow_html=True,
    )
    if preview.warnings:
        st.markdown(
            '<div class="banner banner-info" role="status">'
            + "<br>".join(esc(warning) for warning in preview.warnings) + "</div>",
            unsafe_allow_html=True,
        )


def _render_import_export(repo: ProjectRepository, character_repo: CharacterRepository,
                          location_repo: LocationRepository, project: Project) -> None:
    t = _transfer_prefix(project.id)
    with st.container(key="import_export_card"):
        st.markdown('<p class="card-eyebrow">Import / Export</p>', unsafe_allow_html=True)
        st.caption("Exporte la configuration de ce projet, ou importe un fichier pour en créer un nouveau. "
                   "L’import ne modifie jamais un projet existant.")
        with st.container(horizontal=True, key=f"{t}_export_actions"):
            st.download_button("Exporter ce projet", data=_export_bytes(repo, character_repo, location_repo, project.id),
                               file_name=f"lody-export-{slugify(project.name, 40)}.json", mime="application/json",
                               key=f"{t}_export")
            st.download_button("Télécharger un modèle vierge", data=_template_bytes(),
                               file_name="lody-modele-projet.json", mime="application/json", key=f"{t}_template")

        uploaded = st.file_uploader("Importer une configuration (fichier .json)", type=["json"],
                                    key=f"{t}_uploader")
        if uploaded is not None:
            preview = project_transfer.preview_import(
                uploaded.getvalue(), existing_project_names=[p.name for p in repo.list_projects()])
            _render_transfer_preview(preview)
            st.button("Confirmer la création du projet", type="primary", icon=":material/check:",
                      key=f"{t}_confirm", disabled=not preview.is_valid,
                      on_click=_confirm_import, args=(repo, project.id, preview))


def render(repo: ProjectRepository, project: Project, states: States, character_repo: CharacterRepository,
          location_repo: LocationRepository) -> None:
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

    # #55/#59 : hors du formulaire ci-dessous (un st.button n'y est pas autorisé) — une sélection pré-remplit
    # les champs manuels de l'onglet Voix (mêmes clés session_state), à enregistrer ensuite normalement.
    if project.voice_provider == "elevenlabs":
        voice_picker.render(p, name_key=f"{p}_voice_name", voice_id_key=f"{p}_voice_id",
                           current_voice_id=current["voice_id"])

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
                pub = current["publication"]
                with st.expander("Publication (YouTube, TikTok) — propre à ce projet"):
                    st.text_input("Playlist suggérée", value=pub.get("playlist", ""), max_chars=100, key=f"{p}_pub_playlist")
                    st.text_input("Catégorie suggérée", value=pub.get("category", ""), max_chars=60, key=f"{p}_pub_category")
                    st.text_input("Hashtags du projet (séparés par des espaces)", value=" ".join(pub.get("hashtags", [])), key=f"{p}_pub_hashtags")
                    error_under("brief.publication.hashtags")
                    st.text_area("Mots-clés (tags) du projet, séparés par des virgules", value=", ".join(pub.get("tags", [])), height=80, key=f"{p}_pub_tags")
                    error_under("brief.publication.tags")
                    st.text_area("Mention à ajouter aux descriptions", value=pub.get("disclaimer", ""), height=80, max_chars=300, key=f"{p}_pub_disclaimer")
                    st.text_area("Présentation de la série (descriptions)", value=pub.get("series_blurb", ""), height=80, max_chars=300, key=f"{p}_pub_blurb")
                    st.text_input("Question du commentaire épinglé", value=pub.get("comment_prompt", ""), max_chars=300, key=f"{p}_pub_comment")
                    st.checkbox("Vidéos destinées aux enfants", value=bool(pub.get("made_for_kids")), key=f"{p}_pub_kids")
                    st.checkbox("Vérifier l’absence de promesse financière et de conseil d’investissement",
                                value=bool(pub.get("no_financial_claims")), key=f"{p}_pub_nofin")

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
                # setdefault (jamais value=) : voir le commentaire de provider_select — une sélection dans le
                # catalogue ElevenLabs (#59) pré-remplit ces clés avant que ces widgets ne soient instanciés.
                st.session_state.setdefault(f"{p}_voice_name", project.voice_name)
                st.text_input("Voix ElevenLabs", max_chars=80, key=f"{p}_voice_name",
                              placeholder="Ex. Kev - Young, Dynamic and Bright")
                error_under("voice_name")
                st.session_state.setdefault(f"{p}_voice_id", current["voice_id"])
                st.text_input("Identifiant de la voix", max_chars=40, key=f"{p}_voice_id",
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

    _render_import_export(repo, character_repo, location_repo, project)
