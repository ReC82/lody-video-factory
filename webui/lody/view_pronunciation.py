"""Section « Prononciation » des paramètres d'un projet (#162).

Saisir comment la voix doit lire une expression (SDI, HDMI, 3G-SDI...) sans jamais changer le sous-titre,
l'**écouter** dans une voix réellement retenue (projet ou personnage, avec son modèle), puis la **valider**.
Seules les entrées validées sont appliquées aux productions suivantes (voir ``lody.pronunciation``).

Chaque aperçu est un appel payant **explicite** (un bouton, jamais automatique) : une phrase courte, jamais
une vidéo. Le contrôle éditorial d'un texte est gratuit (aucun appel réseau).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import streamlit as st

from lody import brief as brief_lib
from lody import nav, pronunciation
from lody.characters import Character
from lody.generation.models import ProviderError
from lody.generation.runtime import DEFAULT_PROVIDER
from lody.generation.service import ProductionService
from lody.projects import Project
from lody.pronunciation import LexiconEntry, PronunciationError, PronunciationRepository, PronunciationValidationError
from lody.theme import esc

# Réglages envoyés en production quand aucune direction vocale propre n'est enregistrée (voir
# app/services/voice.elevenlabs_tts) : l'aperçu doit sonner comme la vidéo, pas comme un réglage d'essai.
_DEFAULT_SETTINGS = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "use_speaker_boost": True, "speed": 1.0}


@dataclass(frozen=True)
class PreviewVoice:
    key: str
    label: str
    voice_id: str
    settings: dict[str, Any]


def preview_voices(project: Project, characters: list[Character]) -> list[PreviewVoice]:
    """Voix ElevenLabs réellement retenues : celle du projet et celle de chaque personnage actif, avec le
    modèle et les réglages qui seront utilisés en production."""
    voices: list[PreviewVoice] = []
    brief = brief_lib.brief_settings(project.settings)
    if project.voice_provider == "elevenlabs" and brief.get("voice_id"):
        model = brief.get("voice_model") or "eleven_multilingual_v2"
        voices.append(PreviewVoice("project", f"Voix du projet — {project.voice_name or brief['voice_id']} ({model})",
                                   brief["voice_id"], {"model_id": model, **_DEFAULT_SETTINGS}))
    for character in characters:
        if not character.is_active or character.voice_provider != "elevenlabs" or not character.external_voice_id:
            continue
        direction = dict(character.voice_direction or {})
        model = direction.get("model_id") or brief.get("voice_model") or "eleven_multilingual_v2"
        settings = {**_DEFAULT_SETTINGS, **direction, "model_id": model}
        voices.append(PreviewVoice(f"char:{character.id}", f"{character.name} — {character.voice_name or 'voix'} ({model})",
                                   character.external_voice_id, settings))
    return voices


def _prefix(project_id: str) -> str:
    return f"pron_{project_id}"


def _audio_key(p: str, entry_id: str, voice_key: str, variant: str) -> str:
    return f"{p}_audio_{entry_id}_{voice_key}_{variant}"


def _listen(service: ProductionService, p: str, entry: LexiconEntry, voice: PreviewVoice, variant: str,
            language: str) -> None:
    ss = st.session_state
    key = _audio_key(p, entry.id, voice.key, variant)
    reading = entry.spoken if variant == "spoken" else entry.written
    text = pronunciation.preview_sentence(LexiconEntry(written=entry.written, spoken=reading), language)
    try:
        ss[key] = service.provider(DEFAULT_PROVIDER).generate_voice_preview(text, voice.voice_id, voice.settings)
        ss.pop(key + "_error", None)
    except ProviderError as error:
        ss.pop(key, None)
        ss[key + "_error"] = str(getattr(error, "message", error))


def _save(repo: PronunciationRepository, project_id: str, p: str, entry_id: str | None) -> None:
    ss = st.session_state
    suffix = entry_id or "new"
    fields = {name: ss.get(f"{p}_{suffix}_{name}", "") for name in pronunciation.EDITABLE_FIELDS}
    try:
        if entry_id:
            before = repo.get(project_id, entry_id)
            after = repo.update(project_id, entry_id, **fields)
            message = ("Prononciation enregistrée. Elle doit être réécoutée et validée avant d'être utilisée."
                       if before.validated and not after.validated else "Prononciation enregistrée.")
        else:
            repo.create(project_id, **fields)
            message = "Expression ajoutée. Écoute-la puis valide-la : elle ne sera utilisée qu'après validation."
            for name in pronunciation.EDITABLE_FIELDS:
                ss.pop(f"{p}_new_{name}", None)
        ss.pop(f"{p}_{suffix}_errors", None)
        nav.flash("success", message)
    except PronunciationValidationError as error:
        ss[f"{p}_{suffix}_errors"] = error.errors
    except PronunciationError:
        nav.flash("error", "Cette expression n'existe plus.")


def _validate(repo: PronunciationRepository, project_id: str, entry_id: str, label: str) -> None:
    try:
        repo.mark_validated(project_id, entry_id, label)
        nav.flash("success", "Prononciation validée : elle s'appliquera aux prochaines productions.")
    except PronunciationError:
        nav.flash("error", "Validation impossible : l'expression n'existe plus.")


def _unvalidate(repo: PronunciationRepository, project_id: str, entry_id: str) -> None:
    try:
        repo.unvalidate(project_id, entry_id)
        nav.flash("success", "Validation retirée : cette prononciation n'est plus appliquée.")
    except PronunciationError:
        pass


def _delete(repo: PronunciationRepository, project_id: str, entry_id: str) -> None:
    try:
        repo.delete(project_id, entry_id)
        nav.flash("success", "Expression supprimée du lexique.")
    except PronunciationError:
        pass


def _fields(p: str, suffix: str, entry: LexiconEntry, characters: list[Character]) -> None:
    errors = st.session_state.get(f"{p}_{suffix}_errors") or {}

    def error_under(name: str) -> None:
        if errors.get(name):
            st.markdown(f'<p class="field-error" role="alert">{esc(errors[name])}</p>', unsafe_allow_html=True)

    st.text_input("Forme écrite (affichée à l'écran)", value=entry.written, key=f"{p}_{suffix}_written",
                  max_chars=pronunciation.WRITTEN_MAX, placeholder="SDI")
    error_under("written")
    st.text_input("Lecture demandée (ce que la voix dit)", value=entry.spoken, key=f"{p}_{suffix}_spoken",
                  max_chars=pronunciation.SPOKEN_MAX, placeholder="èss-dé-i",
                  help="Graphie parlée, sans crochets. Écoute plusieurs variantes avant de valider.")
    error_under("spoken")
    if entry.written:
        st.caption("Pistes à écouter (jamais appliquées d'office) : "
                   + " · ".join(pronunciation.candidate_readings(entry.written)))
    st.text_input("Forme dans les sous-titres (vide = forme écrite)", value=entry.subtitle,
                  key=f"{p}_{suffix}_subtitle", max_chars=pronunciation.WRITTEN_MAX)
    error_under("subtitle")
    st.text_input("Transcription phonétique propre au fournisseur (facultatif, conservée mais non envoyée)",
                  value=entry.phonetic, key=f"{p}_{suffix}_phonetic", max_chars=pronunciation.SPOKEN_MAX,
                  help="Les modèles ElevenLabs utilisés (eleven_multilingual_v2, eleven_v3) n'ont pas de support "
                       "confirmé des balises de phonèmes : seule la lecture demandée est envoyée.")
    error_under("phonetic")
    st.text_input("Langue (vide = toutes ; « fr » couvre fr-FR)", value=entry.language,
                  key=f"{p}_{suffix}_language", max_chars=5)
    error_under("language")
    options = [""] + [character.id for character in characters]
    names = {character.id: character.name for character in characters}
    st.selectbox("Personnage (facultatif : sinon pour tout le projet)", options,
                 index=options.index(entry.character_id) if entry.character_id in options else 0,
                 format_func=lambda value: names.get(value, "Tout le projet"), key=f"{p}_{suffix}_character_id")
    st.text_input("Notes (facultatif)", value=entry.notes, key=f"{p}_{suffix}_notes",
                  max_chars=pronunciation.NOTES_MAX)
    error_under("notes")


def _render_listen(service: ProductionService, repo: PronunciationRepository, project: Project, p: str,
                   entry: LexiconEntry, voices: list[PreviewVoice]) -> None:
    provider = service.provider(DEFAULT_PROVIDER)
    if not provider.supports_voice_preview:
        st.caption("Le moteur configuré ne sait pas encore générer un aperçu audio isolé.")
        return
    if not voices:
        st.caption("Aucune voix ElevenLabs n'est configurée pour ce projet ou ses personnages : choisis une voix "
                   "pour pouvoir écouter et valider cette prononciation.")
        return
    by_key = {voice.key: voice for voice in voices}
    voice_key = st.selectbox("Voix de l'écoute", list(by_key), format_func=lambda key: by_key[key].label,
                             key=f"{p}_{entry.id}_voice")
    voice = by_key[voice_key]
    st.caption("Phrase courte, appel payant à chaque écoute. Compare la lecture actuelle et la lecture demandée : "
               "juge le rythme, les liaisons et les pauses, pas seulement l'absence d'erreur.")
    heard = False
    for variant, label in (("written", f"Lecture actuelle de « {entry.written} » (sans lexique)"),
                           ("spoken", f"Lecture demandée : « {entry.spoken} »")):
        key = _audio_key(p, entry.id, voice.key, variant)
        st.markdown(f"**{esc(label)}**")
        if st.session_state.get(key + "_error"):
            st.markdown(f'<p class="field-error" role="alert">{esc(st.session_state[key + "_error"])}</p>',
                        unsafe_allow_html=True)
        audio = st.session_state.get(key)
        if audio:
            st.audio(audio, format="audio/mp3")
            heard = heard or variant == "spoken"
        st.button("Réécouter (nouvel appel)" if audio else "Écouter", key=f"{key}_btn", on_click=_listen,
                  args=(service, p, entry, voice, variant, project.language))
    if entry.validated:
        st.markdown(f'<p role="status">Validée le {esc(entry.validated_at[:10])} avec {esc(entry.validated_with)}.</p>',
                    unsafe_allow_html=True)
        st.button("Retirer la validation", key=f"{p}_{entry.id}_unvalidate", on_click=_unvalidate,
                  args=(repo, project.id, entry.id))
    else:
        st.button("Valider cette prononciation après écoute", key=f"{p}_{entry.id}_validate", type="primary",
                  disabled=not heard, on_click=_validate, args=(repo, project.id, entry.id, voice.label),
                  help=None if heard else "Écoute d'abord la lecture demandée dans cette voix.")


def _render_audit(project: Project, p: str, entries: list[LexiconEntry]) -> None:
    st.markdown('<p class="card-eyebrow">Contrôle éditorial d’un texte (gratuit)</p>', unsafe_allow_html=True)
    text = st.text_area("Colle un script pour vérifier ses sigles avant toute génération", key=f"{p}_audit_text",
                        height=100)
    if not text.strip():
        return
    audit = pronunciation.audit(text, entries, project.language)
    messages = audit.messages()
    for message, blocking in messages:
        css = "banner banner-error" if blocking else "banner"
        st.markdown(f'<div class="{css}" role="alert">{esc(message)}</div>', unsafe_allow_html=True)
    if not messages:
        st.markdown('<p role="status">Aucun sigle à risque dans ce texte.</p>', unsafe_allow_html=True)
    rendering = pronunciation.render(text, pronunciation.applicable(entries, project.language))
    st.markdown("**Texte envoyé à la voix**")
    st.code(rendering.spoken, language=None)
    st.markdown("**Texte des sous-titres**")
    st.code(rendering.subtitle, language=None)


def render(repo: PronunciationRepository, service: ProductionService, project: Project,
           characters: list[Character]) -> None:
    p = _prefix(project.id)
    entries = repo.list_for_project(project.id)
    voices = preview_voices(project, characters)
    names = {character.id: character.name for character in characters}
    with st.container(key="pronunciation_card"):
        st.markdown('<p class="card-eyebrow">Prononciation</p>', unsafe_allow_html=True)
        st.caption("Comment la voix lit un sigle (SDI, HDMI, 3G-SDI…), sans changer ce qui s'affiche à l'écran. "
                   "Une expression n'est utilisée dans les vidéos qu'après écoute et validation ; tant qu'une "
                   "expression présente dans le script reste à valider, la génération est refusée.")
        if not entries:
            st.caption("Aucune expression enregistrée pour ce projet.")
        for entry in entries:
            status = "Validée" if entry.validated else "À écouter"
            scope = names.get(entry.character_id, "tout le projet") + (f", {entry.language}" if entry.language else "")
            with st.expander(f"{entry.written} → {entry.spoken} · {status} · {scope}"):
                with st.form(f"{p}_{entry.id}_form", border=False):
                    _fields(p, entry.id, entry, characters)
                    st.form_submit_button("Enregistrer", on_click=_save, args=(repo, project.id, p, entry.id))
                _render_listen(service, repo, project, p, entry, voices)
                st.button("Supprimer cette expression", key=f"{p}_{entry.id}_delete", on_click=_delete,
                          args=(repo, project.id, entry.id))
        with st.expander("Ajouter une expression", expanded=not entries):
            with st.form(f"{p}_new_form", border=False):
                _fields(p, "new", LexiconEntry(), characters)
                st.form_submit_button("Ajouter au lexique", type="primary", on_click=_save,
                                      args=(repo, project.id, p, None))
        _render_audit(project, p, entries)
