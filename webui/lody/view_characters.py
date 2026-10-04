"""Personnages récurrents d'un projet (ticket #32) : liste, création, modification, activation.

Entièrement facultatif : zéro personnage reste le cas nominal. Rien ici n'est lu par une production —
existante, en cours ou future — tant qu'aucune sélection explicite n'existe (#34+). Utilise uniquement
le modèle et le dépôt livrés par #30 (``lody.characters``).
"""

from __future__ import annotations

import json

import streamlit as st

from lody import brief as brief_lib
from lody import catalog, nav, reference_images, resource_transfer, voice_picker
from lody.characters import Character, CharacterNotFound, CharacterRepository, CharacterValidationError
from lody.components import EMPTY_ICON_SVG
from lody.generation import mpt_connector
from lody.generation.models import ProviderError
from lody.generation.publication import slugify
from lody.generation.runtime import DEFAULT_PROVIDER
from lody.generation.service import ProductionService
from lody.projects import Project
from lody.theme import esc

FORM_KEY = "_char_form"  # (project_id, character_id ou None) tant que le formulaire est ouvert ; absent = fermé.
CONFIRM_KEY = "_char_confirm_deactivate"
ERRORS_KEY = "_char_form_errors"


def _form_target(project_id: str) -> tuple[bool, str | None]:
    """(formulaire ouvert ?, personnage édité ou None) pour CE projet.

    Ignore et referme tout état laissé par un autre projet : une navigation manipulée (``?projet=`` changé
    sans repasser par un lien) ne doit jamais montrer un formulaire préparé pour un autre projet."""
    stored = st.session_state.get(FORM_KEY)
    if not stored:
        return False, None
    stored_project, character_id = stored
    if stored_project != project_id:
        st.session_state.pop(FORM_KEY, None)
        return False, None
    return True, character_id


def _prefix(project_id: str, target: str | None) -> str:
    return f"char_{project_id}_{target or 'new'}"


def _clear_form_state(project_id: str) -> None:
    for key in [k for k in st.session_state if str(k).startswith(f"char_{project_id}_")]:
        del st.session_state[key]
    st.session_state.pop(ERRORS_KEY, None)


def _start_create(project_id: str) -> None:
    _clear_form_state(project_id)
    st.session_state[FORM_KEY] = (project_id, None)


def _start_edit(project_id: str, character_id: str) -> None:
    _clear_form_state(project_id)
    st.session_state[FORM_KEY] = (project_id, character_id)


def _cancel_form(project_id: str) -> None:
    _clear_form_state(project_id)
    st.session_state.pop(FORM_KEY, None)


def _ask_deactivate(character_id: str) -> None:
    st.session_state[CONFIRM_KEY] = character_id


def _cancel_deactivate() -> None:
    st.session_state.pop(CONFIRM_KEY, None)


def _deactivate(repo: CharacterRepository, project_id: str, character_id: str) -> None:
    st.session_state.pop(CONFIRM_KEY, None)
    try:
        character = repo.deactivate(project_id, character_id)
    except CharacterNotFound:
        nav.flash("error", "Ce personnage n’existe plus.")
        return
    nav.flash("success", f"« {character.name} » est désactivé. Il n’apparaîtra pas dans les choix, "
                         "mais reste modifiable et peut être réactivé à tout moment.")


def _activate(repo: CharacterRepository, project_id: str, character_id: str) -> None:
    try:
        character = repo.activate(project_id, character_id)
    except CharacterNotFound:
        nav.flash("error", "Ce personnage n’existe plus.")
        return
    nav.flash("success", f"« {character.name} » est de nouveau actif.")


def _submit(repo: CharacterRepository, project_id: str, target: str | None) -> None:
    p = _prefix(project_id, target)
    ss = st.session_state
    fields = dict(
        name=ss.get(f"{p}_name", ""), role=ss.get(f"{p}_role", ""), personality=ss.get(f"{p}_personality", ""),
        visual_description=ss.get(f"{p}_visual_description", ""), reference_prompt=ss.get(f"{p}_reference_prompt", ""),
        speech_style=ss.get(f"{p}_speech_style", ""), voice_provider=ss.get(f"{p}_voice_provider", ""),
        voice_name=ss.get(f"{p}_voice_name", ""), external_voice_id=ss.get(f"{p}_external_voice_id", ""),
        permanent_elements=ss.get(f"{p}_permanent_elements", ""), continuity_notes=ss.get(f"{p}_continuity_notes", ""),
        is_primary=bool(ss.get(f"{p}_is_primary")),
    )
    # #38 : validée AVANT tout enregistrement — un envoi invalide ne doit ni créer/modifier le personnage à
    # moitié, ni laisser croire qu'une image a été prise en compte.
    uploaded = ss.get(f"{p}_reference_image")
    image_bytes = uploaded.getvalue() if uploaded is not None else None
    remove_image = bool(ss.get(f"{p}_reference_image_remove"))
    if image_bytes is not None:
        try:
            reference_images.validate(image_bytes)
        except reference_images.ReferenceImageError as error:
            ss[ERRORS_KEY] = {"reference_image": str(error)}
            return
    try:
        if target:
            character = repo.update(project_id, target, **fields)
            message = f"« {character.name} » est modifié."
        else:
            character = repo.create(project_id, **fields)
            message = f"« {character.name} » est ajouté."
    except CharacterValidationError as error:
        ss[ERRORS_KEY] = error.errors
        return
    except CharacterNotFound:
        nav.flash("error", "Ce personnage n’existe plus.")
        _cancel_form(project_id)
        return
    if image_bytes is not None:
        try:
            repo.set_reference_image(project_id, character.id, image_bytes)
        except CharacterValidationError as error:
            _cancel_form(project_id)
            nav.flash("error", f"{message} L’image n’a pas pu être enregistrée : "
                               f"{error.errors.get('reference_image', 'erreur inconnue')}")
            return
    elif remove_image:
        repo.clear_reference_image(project_id, character.id)
    _cancel_form(project_id)
    nav.flash("success", message)


def _error_under(field: str) -> None:
    message = st.session_state.get(ERRORS_KEY, {}).get(field)
    if message:
        st.markdown(f'<p class="field-error" role="alert">{esc(message)}</p>', unsafe_allow_html=True)


def _voice_summary(character: Character) -> str:
    if not character.voice_provider and not character.voice_name:
        return ""
    label = catalog.label(catalog.VOICE_PROVIDERS, character.voice_provider) if character.voice_provider else ""
    return " · ".join(filter(None, [label, character.voice_name]))


def _row(repo: CharacterRepository, project: Project, character: Character) -> None:
    badges = []
    if character.is_primary:
        badges.append('<span class="pill pill-ok">Principal</span>')
    if not character.is_active:
        badges.append('<span class="pill pill-muted">Inactif</span>')
    details = [esc(character.role)] if character.role else []
    voice = _voice_summary(character)
    if voice:
        details.append(esc(voice))
    meta_text = " · ".join(details) or "Rôle non précisé"
    with st.container(horizontal=True, vertical_alignment="center", key=f"item_row_{character.id}"):
        st.markdown(
            f'<div class="prod-info"><p class="prod-title">{esc(character.name)}</p>'
            f'<p class="prod-meta"><span>{meta_text}</span>{"".join(badges)}</p></div>',
            unsafe_allow_html=True,
        )
        with st.container(horizontal=True, key=f"item_actions_{character.id}"):
            st.button("Modifier", key=f"edit_{character.id}", icon=":material/edit:",
                      on_click=_start_edit, args=(project.id, character.id))
            if character.is_active:
                st.button("Désactiver", key=f"deactivate_{character.id}", type="tertiary",
                          on_click=_ask_deactivate, args=(character.id,))
            else:
                st.button("Réactiver", key=f"activate_{character.id}", type="tertiary",
                          on_click=_activate, args=(repo, project.id, character.id))
    if st.session_state.get(CONFIRM_KEY) == character.id:
        with st.container(key=f"confirm_deactivate_{character.id}"):
            st.markdown(
                f'<p class="confirm-title">Désactiver « {esc(character.name)} » ?</p>'
                '<p class="confirm-text">Il n’apparaîtra plus dans les choix d’une prochaine production, '
                "mais reste modifiable et peut être réactivé à tout moment.</p>",
                unsafe_allow_html=True,
            )
            with st.container(horizontal=True, key=f"confirm_deactivate_actions_{character.id}"):
                st.button("Oui, désactiver", type="primary", key=f"confirm_deactivate_yes_{character.id}",
                          on_click=_deactivate, args=(repo, project.id, character.id))
                st.button("Annuler", key=f"cancel_deactivate_{character.id}", on_click=_cancel_deactivate)


# Contraintes GÉNÉRIQUES (jamais spécifiques à un personnage, donc jamais un codage en dur de nom/trait) :
# sans elles, un prompt de fiche seul peut produire un montage à plusieurs vignettes avec un titre incrusté
# (observé en validation réelle #92 — inutilisable comme référence de continuité visuelle, voir aussi
# ``thumbnail.background_prompt`` qui pose la même garde pour le fond de miniature).
_REFERENCE_PORTRAIT_GUARD = (
    "Portrait de référence : une SEULE image, un seul personnage, jamais un montage ni une grille de "
    "plusieurs scènes, jamais de bandeau ni de titre. Cadrage buste ou plan rapproché, pose neutre et "
    "visage bien visible, fond simple et neutre. Aucun texte, aucune lettre, aucun chiffre, aucun logo, "
    "aucune marque, aucun filigrane dans l'image."
)


def _reference_prompt_from_sheet(character: Character) -> str:
    """Prompt de proposition de référence, dérivé UNIQUEMENT de la fiche du personnage (#92) — jamais un
    nom/trait codé en dur ailleurs dans la plateforme : tout vient de ces quatre champs, dans cet ordre.
    La garde de cadrage/propreté (``_REFERENCE_PORTRAIT_GUARD``) est générique, jamais spécifique à un
    personnage : elle s'applique à toute fiche, vide ou non."""
    parts = [part for part in (
        character.visual_description, character.reference_prompt, character.permanent_elements,
    ) if part.strip()]
    base = " ".join(parts) if parts else f"Portrait de {character.name}, personnage de fiction, style neutre, fond simple."
    return base + " " + _REFERENCE_PORTRAIT_GUARD


def _generate_reference_proposal(service: ProductionService, p: str, character: Character) -> None:
    ss = st.session_state
    provider = service.provider(DEFAULT_PROVIDER)
    try:
        image_bytes = provider.generate_reference_proposal(_reference_prompt_from_sheet(character))
        reference_images.validate(image_bytes)
    except (ProviderError, reference_images.ReferenceImageError) as error:
        ss[f"{p}_proposal_error"] = str(getattr(error, "message", error))
        ss.pop(f"{p}_proposal_bytes", None)
        return
    ss[f"{p}_proposal_bytes"] = image_bytes
    ss.pop(f"{p}_proposal_error", None)


def _validate_reference_proposal(repo: CharacterRepository, project_id: str, character_id: str, p: str) -> None:
    image_bytes = st.session_state.get(f"{p}_proposal_bytes")
    if not image_bytes:
        return
    try:
        repo.set_reference_image(project_id, character_id, image_bytes)
    except CharacterValidationError as error:
        nav.flash("error", f"La proposition n’a pas pu être enregistrée : "
                           f"{error.errors.get('reference_image', 'erreur inconnue')}")
        return
    st.session_state.pop(f"{p}_proposal_bytes", None)
    nav.flash("success", "Référence visuelle enregistrée.")


def _discard_reference_proposal(p: str) -> None:
    st.session_state.pop(f"{p}_proposal_bytes", None)
    st.session_state.pop(f"{p}_proposal_error", None)


def _render_reference_proposal(repo: CharacterRepository, project: Project, service: ProductionService,
                               current: Character | None) -> None:
    """Génère UNE proposition de référence (payant) depuis la fiche DÉJÀ ENREGISTRÉE du personnage,
    la prévisualise, et ne l'enregistre qu'après validation explicite (#92) — jamais une régénération
    automatique, jamais un remplacement silencieux d'une référence existante. HORS du formulaire (un
    st.button simple n'est pas permis dans un st.form) : disponible seulement en modification d'un
    personnage déjà créé, dont la fiche visuelle existe donc déjà."""
    if current is None or current.reference_image:
        return
    p = _prefix(project.id, current.id)
    provider = service.provider(DEFAULT_PROVIDER)
    with st.container(key="reference_proposal"):
        st.markdown('<p class="card-eyebrow">Proposition de référence visuelle</p>', unsafe_allow_html=True)
        if not provider.supports_reference_images:
            st.caption("Le fournisseur d’images configuré ne sait pas encore utiliser une image de référence "
                      "pour la génération des scènes : une proposition peut être créée et enregistrée, mais "
                      "elle ne sera pas transmise tant que ce fournisseur ne le permet pas.")
        proposal = st.session_state.get(f"{p}_proposal_bytes")
        error = st.session_state.get(f"{p}_proposal_error")
        if proposal:
            st.image(proposal, width=200, caption="Proposition — pas encore enregistrée")
            with st.container(horizontal=True, key="reference_proposal_actions"):
                st.button("Valider cette référence", type="primary", icon=":material/check:",
                          key=f"{p}_validate_proposal", on_click=_validate_reference_proposal,
                          args=(repo, project.id, current.id, p))
                st.button("Rejeter", key=f"{p}_discard_proposal", type="tertiary",
                          on_click=_discard_reference_proposal, args=(p,))
        else:
            if error:
                st.markdown(f'<p class="field-error" role="alert">{esc(error)}</p>', unsafe_allow_html=True)
            st.caption("Générée à partir de la description visuelle, du prompt de référence et des éléments "
                      "permanents déjà enregistrés pour ce personnage. Appel payant.")
            st.button("Générer une proposition de référence", icon=":material/auto_awesome:",
                      key=f"{p}_generate_proposal", on_click=_generate_reference_proposal,
                      args=(service, p, current))


# -- essai vocal comparatif (#92, diagnostic du jeu vocal) --------------------------------------------------------

_VOICE_LAB_SAMPLE_TEXT = (
    "Voyageur, j'ai une quête urgente pour toi ! Enfin, c'est ce que je dis tous les jours, en fait. "
    "Attends, je l'ai déjà dit combien de fois, ça ?"
)


def _voice_lab_presets(project: Project) -> list[tuple[str, str, str, dict]]:
    """Trois réglages comparables pour LA MÊME voix déjà configurée (#92) — uniquement des champs
    officiellement documentés par ElevenLabs pour tout modèle hors Eleven v4 (stability/similarity_boost/
    style/use_speaker_boost/speed, voir https://elevenlabs.io/docs/api-reference/text-to-speech/convert),
    jamais une balise non confirmée compatible avec le modèle employé (eleven_multilingual_v2)."""
    pace = brief_lib.brief_settings(project.settings)["narration_pace"]
    configured_speed = mpt_connector.voice_rate_for_pace(pace)
    return [
        ("current", "Réglages actuellement envoyés en production",
         f"stability=0.5, similarity_boost=0.75, style=0.0, speed=1.0 — la cadence réellement configurée "
         f"pour ce projet ({configured_speed}×) n'était jusqu'ici jamais transmise à ElevenLabs (voir le bilan).",
         {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True, "speed": 1.0}),
        ("expressive", "Plus expressif (stabilité et style), même cadence",
         "stability=0.3, similarity_boost=0.75, style=0.35, speed=1.0 — isole l'effet de l'expressivité, "
         "sans changer le rythme : accélérer seul ne suffit pas à rendre la voix plus vivante.",
         {"stability": 0.3, "similarity_boost": 0.75, "style": 0.35, "use_speaker_boost": True, "speed": 1.0}),
        ("expressive_paced", "Plus expressif + cadence réellement configurée",
         f"stability=0.3, similarity_boost=0.75, style=0.35, speed={configured_speed} — ajoute la cadence "
         "du projet par-dessus les mêmes réglages d'expressivité.",
         {"stability": 0.3, "similarity_boost": 0.75, "style": 0.35, "use_speaker_boost": True, "speed": configured_speed}),
    ]


def _generate_voice_preview(service: ProductionService, p: str, character: Character, preset_key: str,
                            text: str, settings: dict) -> None:
    ss = st.session_state
    provider = service.provider(DEFAULT_PROVIDER)
    try:
        audio_bytes = provider.generate_voice_preview(text, character.external_voice_id, settings)
    except ProviderError as error:
        ss[f"{p}_voice_preview_{preset_key}_error"] = str(getattr(error, "message", error))
        ss.pop(f"{p}_voice_preview_{preset_key}_bytes", None)
        return
    ss[f"{p}_voice_preview_{preset_key}_bytes"] = audio_bytes
    ss.pop(f"{p}_voice_preview_{preset_key}_error", None)


# #92 (suite) : la direction (ton, hésitation, pause, réflexion) évolue au fil des répliques — eleven_
# multilingual_v2 n'offre aucun levier documenté pour faire varier cela DANS un seul appel (voice_settings
# s'applique à tout l'appel, pas phrase par phrase ; aucune balise audio confirmée compatible avec ce
# modèle). eleven_v3 (model_id officiel, voir https://elevenlabs.io/docs/best-practices/prompting/eleven-v3)
# supporte des balises entre crochets interprétées comme des indications de jeu — JAMAIS prononcées —
# combinées aux points de suspension/majuscules déjà recommandés pour les pauses et l'emphase (SSML non
# supporté par ce modèle). Un SEUL appel continu par variante (jamais plusieurs segments recollés) :
# le "request stitching" officiel n'est de toute façon pas disponible pour eleven_v3, et une génération
# ininterrompue garde nativement le contexte de toute la séquence, sans raccord à soigner.
# Mêmes MOTS strictement identiques entre les deux variantes (seules les balises et leur densité varient) ;
# mêmes réglages de voix dans les deux cas, pour isoler l'effet du JEU de celui des réglages globaux.
_VOICE_LAB_V3_SETTINGS = {
    "model_id": "eleven_v3", "stability": 0.3, "similarity_boost": 0.75, "style": 0.35,
    "use_speaker_boost": True, "speed": 1.0,
}
_VOICE_LAB_V3_PRESETS: list[tuple[str, str, str]] = [
    ("v3_subtle", "eleven_v3 — interprétation subtile et naturelle",
     "[matter-of-fact] Voyageur, j'ai une quête urgente pour toi ! [hesitates] Enfin... c'est ce que je dis "
     "à chaque fois. [reflective] Attends... je l'ai déjà dit combien de fois, ça ?"),
    ("v3_marked", "eleven_v3 — prise de conscience plus marquée, sans caricature",
     "[matter-of-fact] Voyageur, j'ai une quête urgente pour toi ! [pause] [hesitates] Enfin... c'est ce que "
     "je dis à chaque fois. [pause] [reflective] [hesitates] Attends... je l'ai déjà dit combien de fois, ça ?"),
]


def _render_voice_lab(repo: CharacterRepository, service: ProductionService, project: Project,
                      current: Character | None) -> None:
    """Essai vocal comparatif pour LA MÊME voix déjà configurée (#92, diagnostic du jeu vocal) — ne
    remplace jamais la voix utilisée en production : les réglages comparés ici ne s'appliquent nulle part
    ailleurs tant qu'aucun choix explicite n'est fait. HORS du formulaire, comme la proposition de
    référence (un ``st.button`` simple n'est pas permis dans un ``st.form``). Seulement pour une voix
    ElevenLabs déjà configurée (seul fournisseur dont le moteur sait générer un essai isolé aujourd'hui)."""
    if current is None or current.voice_provider != "elevenlabs" or not current.external_voice_id:
        return
    p = _prefix(project.id, current.id)
    provider = service.provider(DEFAULT_PROVIDER)
    with st.container(key="voice_lab"):
        st.markdown('<p class="card-eyebrow">Essai vocal comparatif</p>', unsafe_allow_html=True)
        if not provider.supports_voice_preview:
            st.caption("Le moteur configuré ne sait pas encore générer un essai vocal isolé.")
            return
        st.caption("Même voix déjà configurée pour ce personnage, mêmes répliques, réglages différents — "
                  "jamais un remplacement de la voix utilisée en production. Appels payants.")
        text = st.text_area("Répliques de l'essai (texte seul, aucune indication scénique)",
                            value=_VOICE_LAB_SAMPLE_TEXT, key=f"{p}_voice_lab_text", height=100)
        for preset_key, label, detail, settings in _voice_lab_presets(project):
            st.markdown(f"**{esc(label)}**")
            st.caption(detail)
            error = st.session_state.get(f"{p}_voice_preview_{preset_key}_error")
            audio = st.session_state.get(f"{p}_voice_preview_{preset_key}_bytes")
            if error:
                st.markdown(f'<p class="field-error" role="alert">{esc(error)}</p>', unsafe_allow_html=True)
            if audio:
                st.audio(audio, format="audio/mp3")
            button_label = "Régénérer cet extrait" if (audio or error) else "Générer cet extrait"
            st.button(button_label, key=f"{p}_voice_preview_{preset_key}_generate",
                      on_click=_generate_voice_preview, args=(service, p, current, preset_key, text, settings))

        st.markdown('<p class="card-eyebrow">Direction par réplique (eleven_v3)</p>', unsafe_allow_html=True)
        st.caption("Le ton évolue phrase par phrase (assurance → hésitation → prise de conscience), un seul "
                  "appel continu par variante pour garder le contexte de toute la séquence. Mêmes mots dans "
                  "les deux variantes, mêmes réglages de voix — seules les balises de jeu entre crochets "
                  "changent. Les crochets sont une direction interprétée par le modèle, jamais prononcés. "
                  "Modèle ElevenLabs officiel eleven_v3 (jamais la voix ni le modèle de production).")
        for preset_key, label, tagged_text in _VOICE_LAB_V3_PRESETS:
            st.markdown(f"**{esc(label)}**")
            st.code(tagged_text, language=None)
            error = st.session_state.get(f"{p}_voice_preview_{preset_key}_error")
            audio = st.session_state.get(f"{p}_voice_preview_{preset_key}_bytes")
            if error:
                st.markdown(f'<p class="field-error" role="alert">{esc(error)}</p>', unsafe_allow_html=True)
            if audio:
                st.audio(audio, format="audio/mp3")
            button_label = "Régénérer cet extrait" if (audio or error) else "Générer cet extrait"
            st.button(button_label, key=f"{p}_voice_preview_{preset_key}_generate",
                      on_click=_generate_voice_preview,
                      args=(service, p, current, preset_key, tagged_text, _VOICE_LAB_V3_SETTINGS))

        _render_voice_direction_apply(repo, project, current)


def _apply_voice_direction(repo: CharacterRepository, project_id: str, character_id: str, p: str) -> None:
    try:
        repo.set_voice_direction(project_id, character_id, _VOICE_LAB_V3_SETTINGS)
    except CharacterValidationError as error:
        nav.flash("error", f"Réglages non enregistrés : {error.errors.get('voice_direction', 'erreur inconnue')}")
        return
    nav.flash("success", "Modèle et réglages eleven_v3 enregistrés pour ce personnage.")


def _clear_voice_direction(repo: CharacterRepository, project_id: str, character_id: str) -> None:
    repo.clear_voice_direction(project_id, character_id)
    nav.flash("success", "Direction vocale retirée : ce personnage reprend les réglages par défaut du moteur.")


def _render_voice_direction_apply(repo: CharacterRepository, project: Project, current: Character) -> None:
    """Applique le modèle/réglages eleven_v3 choisis APRÈS écoute (#92) — un choix explicite, jamais un
    défaut silencieux : ce personnage seul est concerné, jamais les autres personnages ni le projet. Ne
    touche jamais à ``voice_provider``/``external_voice_id`` (la voix elle-même reste celle déjà
    configurée)."""
    applied = current.voice_direction
    st.markdown('<p class="card-eyebrow">Appliquer un choix</p>', unsafe_allow_html=True)
    if applied:
        st.caption(f"Déjà appliqué à {esc(current.name)} : " + ", ".join(f"{k}={v}" for k, v in applied.items()))
        st.button("Retirer (revenir aux réglages par défaut du moteur)", key=f"voice_direction_clear_{current.id}",
                  on_click=_clear_voice_direction, args=(repo, project.id, current.id))
    else:
        st.caption(f"Après écoute, applique le modèle et les réglages eleven_v3 ci-dessus à {esc(current.name)} "
                  "uniquement (jamais aux autres personnages ni au projet). La voix elle-même ne change pas.")
        st.button("Appliquer ce réglage eleven_v3 à ce personnage", type="primary",
                  key=f"voice_direction_apply_{current.id}",
                  on_click=_apply_voice_direction, args=(repo, project.id, current.id, _prefix(project.id, current.id)))


def _render_form(repo: CharacterRepository, project: Project, target: str | None, current: Character | None) -> None:
    p = _prefix(project.id, target)
    title = f"Modifier « {current.name} »" if current else "Nouveau personnage"

    with st.container(key="form_card"):
        st.markdown(f'<p class="card-eyebrow">{esc(title)}</p>', unsafe_allow_html=True)
        with st.form(f"{p}_form", border=False):
            with st.container(horizontal=True, vertical_alignment="top", key=f"form_{p}_row_a"):
                st.text_input("Nom (obligatoire)", value=current.name if current else "", max_chars=80,
                              key=f"{p}_name", placeholder="Ex. Gaston")
                st.text_input("Rôle", value=current.role if current else "", max_chars=60, key=f"{p}_role",
                              placeholder="Ex. Guide, antagoniste, mascotte…")
            _error_under("name")
            _error_under("role")
            st.text_area("Personnalité", value=current.personality if current else "", height=90, max_chars=500,
                         key=f"{p}_personality", placeholder="Traits de caractère, tics de langage…")
            _error_under("personality")
            st.text_area("Description visuelle", value=current.visual_description if current else "", height=90,
                         max_chars=500, key=f"{p}_visual_description",
                         placeholder="Apparence stable d’une vidéo à l’autre.")
            _error_under("visual_description")
            st.text_area("Prompt de référence", value=current.reference_prompt if current else "", height=90,
                         max_chars=500, key=f"{p}_reference_prompt",
                         help="Utilisé plus tard, uniquement si ce personnage est sélectionné dans une production.")
            _error_under("reference_prompt")
            st.text_area("Style d’expression", value=current.speech_style if current else "", height=80,
                         max_chars=300, key=f"{p}_speech_style", placeholder="Comment il ou elle s’exprime.")
            _error_under("speech_style")
            st.text_area("Éléments permanents", value=current.permanent_elements if current else "", height=80,
                         max_chars=500, key=f"{p}_permanent_elements",
                         placeholder="Détails qui ne doivent jamais changer (ex. porte toujours un chapeau).")
            _error_under("permanent_elements")
            st.text_area("Notes de continuité", value=current.continuity_notes if current else "", height=80,
                         max_chars=500, key=f"{p}_continuity_notes",
                         placeholder="Ce qu’il faut retenir pour rester cohérent d’une vidéo à l’autre.")
            _error_under("continuity_notes")

            with st.expander("Voix (facultatif)"):
                # #62 : catalogue ElevenLabs intégré ICI (plus de bloc séparé au-dessus du formulaire, plus de
                # duplication de la voix sélectionnée). Affiché aussi pour un nouveau personnage (le
                # fournisseur n'est pas encore choisi) ; masqué seulement si un fournisseur déjà enregistré et
                # différent d'ElevenLabs est connu — dans ce cas, la saisie manuelle est le seul chemin.
                show_catalog = current is None or current.voice_provider in ("", "elevenlabs")
                picker = voice_picker.render(
                    p, name_key=f"{p}_voice_name", voice_id_key=f"{p}_external_voice_id",
                    current_voice_id=current.external_voice_id if current else "",
                ) if show_catalog else voice_picker.PickerResult(available=False, matched=False)

                has_existing_value = bool((current.voice_name if current else "")
                                          or (current.external_voice_id if current else ""))
                # Repliée seulement quand le catalogue a clairement pris le relais (correspondance trouvée) ;
                # dépliée dans tous les autres cas (catalogue indisponible, absent, ou valeur non représentée)
                # pour ne jamais cacher silencieusement une voix déjà configurée — voir le docstring #62.
                manual_expanded = not picker.matched and (has_existing_value or not picker.available)
                with st.expander("Saisie manuelle", expanded=manual_expanded):
                    st.caption("Pour une voix absente du catalogue, un ancien identifiant encore valide, ou un "
                              "autre fournisseur.")
                    providers = ("",) + catalog.values(catalog.VOICE_PROVIDERS)
                    chosen = current.voice_provider if current else ""
                    # setdefault (jamais index=/value=) : voir le commentaire équivalent de provider_select — une
                    # sélection dans le catalogue ElevenLabs (#59) pré-remplit ces clés avant que ces widgets ne
                    # soient instanciés ; passer index=/value= EN PLUS d'une valeur déjà en session_state est
                    # ambigu pour Streamlit et fait gagner l'ancienne valeur à l'enregistrement.
                    st.session_state.setdefault(f"{p}_voice_provider", chosen if chosen in providers else providers[0])
                    st.selectbox(
                        "Fournisseur de voix", providers,
                        format_func=lambda value: catalog.label(catalog.VOICE_PROVIDERS, value) if value else "Aucune voix propre",
                        key=f"{p}_voice_provider",
                    )
                    _error_under("voice_provider")
                    st.session_state.setdefault(f"{p}_voice_name", current.voice_name if current else "")
                    st.text_input("Nom de la voix", max_chars=80,
                                  key=f"{p}_voice_name", placeholder="Ex. Kev - Young, Dynamic and Bright")
                    _error_under("voice_name")
                    st.session_state.setdefault(f"{p}_external_voice_id", current.external_voice_id if current else "")
                    st.text_input("Identifiant de voix chez le fournisseur",
                                  max_chars=100, key=f"{p}_external_voice_id",
                                  help="Repris depuis la bibliothèque de voix du fournisseur. Ce n’est jamais une "
                                       "clé d’accès : une valeur qui y ressemble est refusée.")
                    _error_under("external_voice_id")

            with st.expander("Image de référence (facultatif)"):
                st.caption("PNG, JPEG ou WEBP, 8 Mo maximum, 64 à 4096 pixels de côté. Utilisée seulement par "
                          "les générateurs d’images qui savent réellement s’appuyer sur une référence — les "
                          "autres continuent de fonctionner sans elle, à partir des descriptions ci-dessus.")
                if current and current.reference_image:
                    try:
                        data = reference_images.read_bytes(current.reference_image, project.id, "characters",
                                                            current.id)
                    except ValueError:
                        st.caption("L’image enregistrée est introuvable.")
                    else:
                        st.image(data, width=160)
                    st.checkbox("Retirer l’image actuelle", key=f"{p}_reference_image_remove")
                st.file_uploader("Remplacer l’image" if (current and current.reference_image) else "Image",
                                 type=["png", "jpg", "jpeg", "webp"], key=f"{p}_reference_image")
                _error_under("reference_image")

            st.checkbox("Personnage principal", value=current.is_primary if current else False,
                        key=f"{p}_is_primary", help="Purement indicatif : plusieurs personnages peuvent être principaux.")

            with st.container(horizontal=True, key=f"form_{p}_actions"):
                st.form_submit_button("Enregistrer", type="primary", icon=":material/check:",
                                      on_click=_submit, args=(repo, project.id, target))
                st.form_submit_button("Annuler", on_click=_cancel_form, args=(project.id,))


# -- import / export partiel des personnages (#57) -----------------------------------------------------------------
def _transfer_prefix(project_id: str) -> str:
    return f"charimp_{project_id}"


def _export_bytes(repo: CharacterRepository, project_id: str) -> bytes:
    payload = resource_transfer.export_characters(repo.list_for_project(project_id))
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _template_bytes() -> bytes:
    return json.dumps(resource_transfer.character_template(), ensure_ascii=False, indent=2).encode("utf-8")


def _confirm_character_import(repo: CharacterRepository, project_id: str,
                              preview: resource_transfer.ResourcePreview) -> None:
    t = _transfer_prefix(project_id)
    try:
        created = resource_transfer.commit_character_import(repo.db_path, project_id, preview)
    except resource_transfer.TransferError as error:
        nav.flash("error", f"Import impossible : {error}")
        return
    st.session_state.pop(f"{t}_uploader", None)
    names = ", ".join(character.name for character in created)
    nav.flash("success", f"{len(created)} personnage(s) importé(s) : {names}." if created
             else "Fichier importé : aucun personnage à ajouter.")


def _render_transfer_preview(preview: resource_transfer.ResourcePreview) -> None:
    if preview.errors:
        lines = [f"{esc(issue.path)} — {esc(issue.message)}" if issue.path else esc(issue.message)
                 for issue in preview.errors]
        st.markdown(
            '<div class="banner banner-error" role="alert"><strong>Fichier invalide, rien n’a été importé :'
            '</strong><br>' + "<br>".join(lines) + "</div>", unsafe_allow_html=True,
        )
        return
    names = ", ".join(preview.item_names) if preview.item_names else "aucun"
    st.markdown(
        '<div class="banner banner-info" role="status">Aperçu — aucune écriture pour l’instant.<br>'
        f'{len(preview.item_names)} personnage(s) à importer : {esc(names)}</div>', unsafe_allow_html=True,
    )
    if preview.warnings:
        st.markdown('<div class="banner banner-info" role="status">'
                   + "<br>".join(esc(warning) for warning in preview.warnings) + "</div>", unsafe_allow_html=True)


def _render_import_export(repo: CharacterRepository, project: Project) -> None:
    t = _transfer_prefix(project.id)
    with st.container(key="char_import_export_card"):
        st.markdown('<p class="card-eyebrow">Import / Export</p>', unsafe_allow_html=True)
        st.caption("Exporte les personnages de ce projet, ou importe un fichier pour en ajouter — sans jamais "
                   "toucher aux autres réglages du projet ni écraser un personnage déjà existant.")
        with st.container(horizontal=True, key=f"{t}_export_actions"):
            st.download_button("Exporter les personnages", data=_export_bytes(repo, project.id),
                               file_name=f"lody-personnages-{slugify(project.name, 40)}.json",
                               mime="application/json", key=f"{t}_export")
            st.download_button("Télécharger un modèle", data=_template_bytes(),
                               file_name="lody-modele-personnages.json", mime="application/json",
                               key=f"{t}_template")

        uploaded = st.file_uploader("Importer des personnages (fichier .json)", type=["json"], key=f"{t}_uploader")
        if uploaded is not None:
            existing = repo.list_for_project(project.id)
            preview = resource_transfer.preview_character_import(
                uploaded.getvalue(), existing_names=[character.name for character in existing],
                existing_primary_count=sum(1 for character in existing if character.is_primary))
            _render_transfer_preview(preview)
            st.button("Confirmer l’import", type="primary", icon=":material/check:", key=f"{t}_confirm",
                      disabled=not preview.is_valid, on_click=_confirm_character_import,
                      args=(repo, project.id, preview))


def render(repo: CharacterRepository, project: Project, service: ProductionService) -> None:
    st.markdown(
        '<section class="hero"><p class="eyebrow">Personnages</p>'
        f'<h1 class="hero-title">{esc(project.name)}</h1>'
        '<p class="hero-sub">Facultatif : décris les personnages récurrents de ce projet pour les retrouver '
        "plus tard dans une production. Sans personnage, tes vidéos se génèrent exactement comme aujourd’hui."
        "</p></section>",
        unsafe_allow_html=True,
    )

    if project.is_archived:
        st.markdown(
            '<div class="banner banner-info" role="status">Ce projet est archivé : restaure-le pour gérer '
            "ses personnages.</div>",
            unsafe_allow_html=True,
        )
        st.button("Retour au projet", icon=":material/arrow_back:", key="char_back_archived",
                  on_click=nav.go, args=(nav.VIEW_PROJECT, project.id))
        return

    with st.container(horizontal=True, key="project_actions"):
        st.button("Retour au projet", icon=":material/arrow_back:", type="tertiary", key="char_back",
                  on_click=nav.go, args=(nav.VIEW_PROJECT, project.id))

    is_open, target = _form_target(project.id)
    current: Character | None = None
    if is_open and target:
        try:
            current = repo.get(project.id, target)
        except CharacterNotFound:
            # Identifiant inconnu ou navigation manipulée (id d'un autre projet) : referme sans planter.
            st.session_state.pop(FORM_KEY, None)
            is_open, target = False, None
            st.markdown(
                '<div class="banner banner-error" role="alert">Ce personnage n’existe plus.</div>',
                unsafe_allow_html=True,
            )
    if st.session_state.get(ERRORS_KEY):
        st.markdown(
            '<div class="banner banner-error" role="alert">Quelques points sont à corriger avant d’enregistrer.</div>',
            unsafe_allow_html=True,
        )

    characters = repo.list_for_project(project.id)
    if not characters:
        with st.container(key="empty_state"):
            st.markdown(
                f'<div class="empty">{EMPTY_ICON_SVG}<p class="empty-title">Aucun personnage pour l’instant</p>'
                '<p class="empty-text">Cette fonction est facultative : sans personnage, tes vidéos se génèrent '
                "normalement. Ajoute un personnage seulement si tu veux le retrouver d’une vidéo à l’autre.</p></div>",
                unsafe_allow_html=True,
            )
    else:
        with st.container(key="characters"):
            st.markdown('<p class="card-eyebrow">Personnages du projet</p>', unsafe_allow_html=True)
            for character in characters:
                _row(repo, project, character)

    if not is_open:
        st.button("Ajouter un personnage", type="primary", icon=":material/add:", key="add_character",
                  on_click=_start_create, args=(project.id,))
    else:
        _render_reference_proposal(repo, project, service, current)
        _render_voice_lab(repo, service, project, current)
        _render_form(repo, project, target, current)

    _render_import_export(repo, project)
