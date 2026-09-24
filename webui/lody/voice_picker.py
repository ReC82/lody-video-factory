"""Sélecteur de voix ElevenLabs — selectbox unique et recherchable (ticket #59, remplace la liste de fiches
livrée par #55). Réutilisé par les paramètres du projet et par la fiche d'un personnage. Lit UNIQUEMENT le
catalogue assaini préparé côté hôte (``elevenlabs_voices.resolve_catalog``) : ce composant, comme le reste
du conteneur Lody, ne voit jamais ``config.toml`` ni la clé ElevenLabs — mécanisme de récupération inchangé
depuis #55, voir le docstring de ``generation/elevenlabs_voices.py``.

Design : ce composant vit HORS de tout ``st.form`` (un ``st.button`` n'y est pas autorisé). Choisir une voix
ici PRÉ-REMPLIT les champs de saisie manuelle du formulaire concerné (``voice_provider``/``name_key``/
``voice_id_key`` — mêmes clés ``session_state`` que les widgets du formulaire, dérivées de ``prefix``) ; il
faut ensuite enregistrer normalement (bouton du formulaire) pour appliquer le choix — la sélection seule ne
sauvegarde rien, la saisie manuelle reste le seul chemin qui écrit réellement en base.

Le selectbox garde toujours une option vide en tête de liste (``_PLACEHOLDER``) : quand le filtre ou la
recherche change et que la voix choisie disparaît des options, Streamlit réinitialise silencieusement le
widget sur sa PREMIÈRE option — grâce au placeholder, cette réinitialisation retombe sur « aucune sélection »
plutôt que sur une autre voix prise au hasard, donc n'écrase jamais ``name_key``/``voice_id_key`` (qui ne
sont modifiés que lorsque l'utilisateur choisit explicitement une voix RÉELLE, jamais sur une réinitialisation
automatique).
"""

from __future__ import annotations

import streamlit as st

from lody.generation import elevenlabs_voices as ev
from lody.theme import esc

_PLACEHOLDER = ""  # toujours l'option 0 : voir le docstring du module


def _mark_checked(flag_key: str) -> None:
    st.session_state[flag_key] = True


def render(prefix: str, *, name_key: str, voice_id_key: str, current_voice_id: str = "") -> None:
    """``name_key``/``voice_id_key`` : clés ``session_state`` DÉJÀ utilisées par les champs manuels du
    formulaire appelant (nom de la voix / identifiant) — une sélection les pré-remplit, ainsi que
    ``f"{prefix}_voice_provider"`` (mis à ``"elevenlabs"``), rien de plus.

    ``current_voice_id`` : identifiant déjà enregistré (projet ou personnage), passé EXPLICITEMENT par
    l'appelant plutôt que lu depuis ``session_state[voice_id_key]`` — ce composant est rendu AVANT le
    formulaire (voir le docstring du module), donc ce widget-là n'a pas encore eu l'occasion d'initialiser
    sa propre entrée de session_state au premier rendu de la page. Sert uniquement à présélectionner la
    voix déjà connue si elle fait partie du catalogue (compatibilité ancien ``voice_id``, #59)."""
    search_key = f"{prefix}_ev_search"
    generated_only_key = f"{prefix}_ev_generated_only"
    select_key = f"{prefix}_ev_select"
    last_applied_key = f"{prefix}_ev_last_applied"
    checked_flag = f"{prefix}_ev_checked"
    provider_key = f"{prefix}_voice_provider"

    just_checked = bool(st.session_state.pop(checked_flag, False))

    with st.container(key=f"{prefix}_ev_picker"):
        st.markdown('<p class="card-eyebrow">Voix ElevenLabs (recherche)</p>', unsafe_allow_html=True)
        st.caption("Choisis une voix dans la liste ci-dessous : son fournisseur, son nom et son identifiant "
                   "sont pré-remplis dans les champs manuels — enregistre ensuite normalement pour appliquer "
                   "ton choix.")
        with st.container(horizontal=True, vertical_alignment="center", key=f"{prefix}_ev_toolbar"):
            st.text_input("Rechercher (nom, identifiant, langue…)", key=search_key, placeholder="Ex. Rachel, fr…")
            st.button("Actualiser les voix", icon=":material/refresh:", key=f"{prefix}_ev_refresh_btn",
                      help="Relit le catalogue préparé côté hôte. Ne contacte jamais ElevenLabs directement : "
                           "un nouveau contenu exige que l'administrateur relance le script hôte.",
                      on_click=_mark_checked, args=(checked_flag,))
        st.checkbox("Uniquement les voix générées", value=True, key=generated_only_key,
                    help="Catégorie exacte « generated » du catalogue ElevenLabs. Décoche pour voir aussi les "
                         "voix « professional » et les autres catégories accessibles à ce compte.")
        if just_checked:
            st.caption("Fichier relu à l’instant.")

        result = ev.resolve_catalog()
        if result.error:
            st.markdown(f'<div class="banner banner-info" role="status">{esc(result.error)}</div>',
                       unsafe_allow_html=True)
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

        generated_only = bool(st.session_state.get(generated_only_key, True))
        pool = [voice for voice in result.voices if voice.category == "generated"] if generated_only else list(result.voices)
        if generated_only and not pool:
            st.caption("Aucune voix « generated » disponible sur ce compte : décoche « Uniquement les voix "
                      "générées » ci-dessus pour voir les autres catégories (ex. « professional »).")
            return

        matches = ev.search_voices(pool, st.session_state.get(search_key, ""))
        if not matches:
            st.caption("Aucune voix ne correspond à cette recherche.")
            return

        by_id = {voice.voice_id: voice for voice in matches}
        options = [_PLACEHOLDER, *by_id.keys()]

        def _format(voice_id: str) -> str:
            if not voice_id:
                return "— Choisir une voix —"
            voice = by_id[voice_id]
            return f"{voice.name} — {voice.voice_id} — {voice.category or '—'}"

        if select_key not in st.session_state:
            # Premier rendu de ce sélecteur : pré-sélectionne la voix déjà enregistrée si elle fait partie
            # des options actuelles (config existante ou choix précédent) — sinon reste sur le placeholder.
            # ``current_voice_id`` (paramètre explicite) en priorité ; session_state[voice_id_key] en repli
            # (utile si ce rendu est déclenché après une interaction qui l'a déjà peuplé dans ce run).
            current_value = current_voice_id or str(st.session_state.get(voice_id_key, ""))
            st.session_state[select_key] = current_value if current_value in by_id else _PLACEHOLDER

        chosen = st.selectbox("Choisir une voix", options=options, format_func=_format, key=select_key)

        if chosen and chosen != st.session_state.get(last_applied_key):
            voice = by_id[chosen]
            st.session_state[provider_key] = "elevenlabs"
            st.session_state[name_key] = voice.name
            st.session_state[voice_id_key] = voice.voice_id
            st.session_state[last_applied_key] = chosen

        if chosen:
            voice = by_id[chosen]
            st.caption("Identifiant complet (voice_id) :")
            st.code(voice.voice_id, language=None)  # bloc de code Streamlit : icône de copie intégrée
            detail_bits = [voice.category] + [f"{key} : {value}" for key, value in voice.labels.items()]
            st.caption(" · ".join(filter(None, detail_bits)) or "Aucun détail supplémentaire.")
            if voice.preview_url:
                # URL publique ElevenLabs (validée HTTPS par elevenlabs_voices) : le navigateur la lit
                # directement, la clé API n'intervient jamais ici. Un seul aperçu : celui de la sélection.
                st.audio(voice.preview_url)

        footer = f"{len(matches)} voix correspondante(s) sur {len(result.voices)} au total"
        if result.generated_at:
            footer += f" · catalogue généré côté hôte le {result.generated_at}"
        st.caption(footer + ".")
