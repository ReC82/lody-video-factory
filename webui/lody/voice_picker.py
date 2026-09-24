"""Sélecteur de voix ElevenLabs recherchable (ticket #55), réutilisé par les paramètres du projet et par la
fiche d'un personnage. Ne lit ni n'affiche jamais la clé ElevenLabs — voir ``generation/elevenlabs_voices.py``.

Design : ce composant vit HORS de tout ``st.form`` (un ``st.button`` n'est pas autorisé à l'intérieur d'un
formulaire Streamlit). Choisir une voix ici PRÉ-REMPLIT les champs de saisie manuelle du formulaire concerné
(mêmes clés ``session_state``) ; il faut ensuite enregistrer normalement (bouton du formulaire) pour
appliquer le choix — exactement comme n'importe quel autre champ édité à la main. Ce composant lui-même
n'écrit jamais en base : la saisie manuelle reste le seul chemin qui enregistre réellement quelque chose.
"""

from __future__ import annotations

import streamlit as st

from lody.generation import elevenlabs_voices as ev
from lody.theme import esc

MAX_RESULTS_SHOWN = 20  # « aucune liste immense impossible à utiliser » (ticket #55) : surtout sur mobile


def _select(name_key: str, voice_id_key: str, name: str, voice_id: str) -> None:
    st.session_state[name_key] = name
    st.session_state[voice_id_key] = voice_id


def _request_refresh(flag_key: str) -> None:
    st.session_state[flag_key] = True


def render(prefix: str, *, name_key: str, voice_id_key: str) -> None:
    """``name_key``/``voice_id_key`` : clés ``session_state`` DÉJÀ utilisées par les champs manuels du
    formulaire appelant (nom de la voix / identifiant) — une sélection les pré-remplit, rien de plus."""
    search_key = f"{prefix}_ev_search"
    refresh_flag = f"{prefix}_ev_refresh"
    force_refresh = bool(st.session_state.pop(refresh_flag, False))

    with st.container(key=f"{prefix}_ev_picker"):
        st.markdown('<p class="card-eyebrow">Voix ElevenLabs (recherche)</p>', unsafe_allow_html=True)
        st.caption("Choisis une voix ci-dessous : son nom et son identifiant sont pré-remplis dans les champs "
                   "manuels — enregistre ensuite normalement pour appliquer ton choix.")
        with st.container(horizontal=True, vertical_alignment="center", key=f"{prefix}_ev_toolbar"):
            st.text_input("Rechercher une voix par nom", key=search_key, placeholder="Ex. Rachel")
            st.button("Actualiser les voix", icon=":material/refresh:", key=f"{prefix}_ev_refresh_btn",
                      on_click=_request_refresh, args=(refresh_flag,))

        with st.spinner("Chargement du catalogue de voix…"):
            result = ev.resolve_catalog(force_refresh=force_refresh)

        if not result.available:
            message = result.error or "Catalogue de voix indisponible pour l’instant."
            st.markdown(
                f'<div class="banner banner-info" role="status">{esc(message)} '
                "La saisie manuelle de l’identifiant reste disponible ci-dessous.</div>",
                unsafe_allow_html=True,
            )
            return

        if result.stale:
            st.caption("⚠️ Actualisation impossible pour l’instant : liste peut-être obsolète.")

        matches = ev.search_voices(result.voices, st.session_state.get(search_key, ""))
        if not matches:
            st.caption("Aucune voix ne correspond à cette recherche.")
            return
        shown = matches[:MAX_RESULTS_SHOWN]
        hidden = max(0, len(matches) - MAX_RESULTS_SHOWN)
        for voice in shown:
            _render_row(prefix, voice, name_key, voice_id_key)
        if hidden:
            st.caption(f"+{hidden} autre(s) résultat(s) : affine la recherche pour les voir.")
        footer = f"{len(result.voices)} voix accessibles au compte configuré"
        if result.fetched_at:
            footer += f" · actualisé {result.fetched_at}"
        st.caption(footer + ".")


def _render_row(prefix: str, voice: ev.VoiceInfo, name_key: str, voice_id_key: str) -> None:
    meta_bits = [voice.category] if voice.category else []
    meta_bits += [f"{key} : {value}" for key, value in list(voice.labels.items())[:3]]
    meta = " · ".join(filter(None, meta_bits))
    with st.container(horizontal=True, vertical_alignment="center", key=f"{prefix}_ev_row_{voice.voice_id}"):
        st.markdown(
            f'<div class="prod-info"><p class="prod-title">{esc(voice.name)}</p>'
            f'<p class="prod-meta"><span>{esc(voice.voice_id)}</span>'
            + (f'<span>{esc(meta)}</span>' if meta else "") + "</p></div>",
            unsafe_allow_html=True,
        )
        if voice.preview_url:
            # URL publique ElevenLabs (aperçu) : le navigateur la lit directement, la clé API n'intervient jamais ici.
            st.audio(voice.preview_url)
        st.button("Utiliser cette voix", key=f"{prefix}_ev_pick_{voice.voice_id}", type="tertiary",
                  on_click=_select, args=(name_key, voice_id_key, voice.name, voice.voice_id))
