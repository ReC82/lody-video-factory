"""Sélecteur de voix ElevenLabs recherchable (ticket #55), réutilisé par les paramètres du projet et par la
fiche d'un personnage. Lit UNIQUEMENT le catalogue assaini préparé côté hôte (``elevenlabs_voices.
resolve_catalog``) : ce composant, comme le reste du conteneur Lody, ne voit jamais ``config.toml`` ni la
clé ElevenLabs — voir le docstring de ``generation/elevenlabs_voices.py``.

Design : ce composant vit HORS de tout ``st.form`` (un ``st.button`` n'est pas autorisé à l'intérieur d'un
formulaire Streamlit). Choisir une voix ici PRÉ-REMPLIT les champs de saisie manuelle du formulaire concerné
(mêmes clés ``session_state``) ; il faut ensuite enregistrer normalement (bouton du formulaire) pour
appliquer le choix — exactement comme n'importe quel autre champ édité à la main. Ce composant lui-même
n'écrit jamais en base : la saisie manuelle reste le seul chemin qui enregistre réellement quelque chose.

Le bouton « Actualiser les voix » NE CONTACTE JAMAIS ElevenLabs (le conteneur Lody ne le peut pas) : il
relit simplement le fichier de rapport depuis le disque (``resolve_catalog`` le fait déjà à chaque rendu —
aucun cache Python/Streamlit ne s'interpose) et confirme visuellement que c'est fait. Un vrai rafraîchissement
du contenu exige de relancer ``scripts/lody-elevenlabs-voices-report.sh`` côté hôte (ou une rotation de la
clé ElevenLabs, qui le déclenche automatiquement — voir ``apply_secrets.py``).
"""

from __future__ import annotations

import streamlit as st

from lody.generation import elevenlabs_voices as ev
from lody.theme import esc

MAX_RESULTS_SHOWN = 20  # « aucune liste immense impossible à utiliser » (ticket #55) : surtout sur mobile


def _select(name_key: str, voice_id_key: str, name: str, voice_id: str) -> None:
    st.session_state[name_key] = name
    st.session_state[voice_id_key] = voice_id


def _mark_checked(flag_key: str) -> None:
    st.session_state[flag_key] = True


def render(prefix: str, *, name_key: str, voice_id_key: str) -> None:
    """``name_key``/``voice_id_key`` : clés ``session_state`` DÉJÀ utilisées par les champs manuels du
    formulaire appelant (nom de la voix / identifiant) — une sélection les pré-remplit, rien de plus."""
    search_key = f"{prefix}_ev_search"
    checked_flag = f"{prefix}_ev_checked"
    just_checked = bool(st.session_state.pop(checked_flag, False))

    with st.container(key=f"{prefix}_ev_picker"):
        st.markdown('<p class="card-eyebrow">Voix ElevenLabs (recherche)</p>', unsafe_allow_html=True)
        st.caption("Choisis une voix ci-dessous : son nom et son identifiant sont pré-remplis dans les champs "
                   "manuels — enregistre ensuite normalement pour appliquer ton choix.")
        with st.container(horizontal=True, vertical_alignment="center", key=f"{prefix}_ev_toolbar"):
            st.text_input("Rechercher une voix par nom", key=search_key, placeholder="Ex. Rachel")
            st.button("Actualiser les voix", icon=":material/refresh:", key=f"{prefix}_ev_refresh_btn",
                      help="Relit le catalogue préparé côté hôte. Ne contacte jamais ElevenLabs directement : "
                           "un nouveau contenu exige que l'administrateur relance le script hôte.",
                      on_click=_mark_checked, args=(checked_flag,))
        if just_checked:
            st.caption("Fichier relu à l’instant.")

        with st.spinner("Lecture du catalogue…"):
            result = ev.resolve_catalog()

        if result.error:
            st.markdown(
                f'<div class="banner banner-info" role="status">{esc(result.error)}</div>',
                unsafe_allow_html=True,
            )
            return
        if result.empty:
            st.markdown(
                '<div class="banner banner-info" role="status">Le catalogue préparé côté hôte ne contient '
                "aucune voix pour ce compte. La saisie manuelle de l’identifiant reste disponible ci-dessous."
                "</div>", unsafe_allow_html=True,
            )
            return

        if result.stale:
            st.caption("⚠️ Ce catalogue date de plus de 30 jours : demande à l’administrateur de le régénérer "
                      "(./scripts/lody-elevenlabs-voices-report.sh).")

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
        if result.generated_at:
            footer += f" · catalogue généré côté hôte le {result.generated_at}"
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
            # URL publique ElevenLabs (aperçu, validée HTTPS par elevenlabs_voices) : le navigateur la lit
            # directement, la clé API n'intervient jamais ici — voir _safe_preview_url.
            st.audio(voice.preview_url)
        st.button("Utiliser cette voix", key=f"{prefix}_ev_pick_{voice.voice_id}", type="tertiary",
                  on_click=_select, args=(name_key, voice_id_key, voice.name, voice.voice_id))
