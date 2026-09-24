"""Sélecteur de voix ElevenLabs — selectbox unique et recherchable (ticket #59, remplace la liste de fiches
livrée par #55 ; intégré directement dans la section « Voix » des formulaires depuis #62, qui a retiré la
duplication entre ce composant et les champs manuels). Réutilisé par les paramètres du projet et par la
fiche d'un personnage. Lit UNIQUEMENT le catalogue assaini préparé côté hôte (``elevenlabs_voices.
resolve_catalog``) : ce composant, comme le reste du conteneur Lody, ne voit jamais ``config.toml`` ni la
clé ElevenLabs — mécanisme de récupération inchangé depuis #55, voir le docstring de
``generation/elevenlabs_voices.py``.

Design : ce composant vit maintenant À L'INTÉRIEUR du formulaire appelant (dans la section « Voix », #62),
contrairement à #55/#59 où il vivait hors du ``st.form`` — ``st.button()`` n'y étant pas autorisé, il n'y a
plus de bouton « Actualiser » ici : ``resolve_catalog()`` relit déjà le rapport à chaque rendu (aucun cache
Python/Streamlit), donc toute interaction avec CE formulaire (recherche, filtre, sélection) montre déjà le
contenu le plus récent du fichier sans action dédiée. Choisir une voix PRÉ-REMPLIT les mêmes clés
``session_state`` que les champs de saisie manuelle du formulaire (``voice_provider``/``name_key``/
``voice_id_key``, dérivées de ``prefix``) ; il faut ensuite enregistrer normalement (bouton du formulaire)
pour appliquer le choix — la sélection seule ne sauvegarde rien.

Le selectbox garde toujours une option vide en tête de liste (``_PLACEHOLDER``) : quand le filtre ou la
recherche change et que la voix choisie disparaît des options, Streamlit réinitialise silencieusement le
widget sur sa PREMIÈRE option — grâce au placeholder, cette réinitialisation retombe sur « aucune sélection »
plutôt que sur une autre voix prise au hasard, donc n'écrase jamais ``name_key``/``voice_id_key`` (qui ne
sont modifiés que lorsque l'utilisateur choisit explicitement une voix RÉELLE, jamais sur une réinitialisation
automatique).

``render()`` renvoie un ``PickerResult`` (``available``/``matched``) que l'appelant utilise pour décider si
sa propre section « Saisie manuelle » doit être dépliée par défaut (ticket #62) : une valeur existante non
représentée par le catalogue — ancien identifiant, autre fournisseur, catalogue indisponible — n'est jamais
cachée silencieusement derrière un repli."""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from lody.generation import elevenlabs_voices as ev
from lody.theme import esc

_PLACEHOLDER = ""  # toujours l'option 0 : voir le docstring du module


@dataclass(frozen=True)
class PickerResult:
    available: bool  # le catalogue a au moins une voix (indépendamment du filtre/de la recherche courants)
    matched: bool     # ``current_voice_id`` correspond à une voix du catalogue


_UNAVAILABLE = PickerResult(available=False, matched=False)


def render(prefix: str, *, name_key: str, voice_id_key: str, current_voice_id: str = "") -> PickerResult:
    """``name_key``/``voice_id_key`` : clés ``session_state`` DÉJÀ utilisées par les champs manuels du
    formulaire appelant (nom de la voix / identifiant) — une sélection les pré-remplit, ainsi que
    ``f"{prefix}_voice_provider"`` (mis à ``"elevenlabs"``), rien de plus.

    ``current_voice_id`` : identifiant déjà enregistré (projet ou personnage), passé EXPLICITEMENT par
    l'appelant plutôt que lu depuis ``session_state[voice_id_key]`` — ce composant peut être rendu avant que
    ce widget-là n'ait eu l'occasion d'initialiser sa propre entrée de session_state au premier rendu de la
    page. Sert à présélectionner la voix déjà connue si elle fait partie du catalogue (compatibilité ancien
    ``voice_id``, #59), et à calculer ``PickerResult.matched``."""
    search_key = f"{prefix}_ev_search"
    generated_only_key = f"{prefix}_ev_generated_only"
    select_key = f"{prefix}_ev_select"
    last_applied_key = f"{prefix}_ev_last_applied"
    provider_key = f"{prefix}_voice_provider"

    with st.container(key=f"{prefix}_ev_picker"):
        st.text_input("Rechercher une voix (nom, identifiant, langue…)", key=search_key,
                      placeholder="Ex. Rachel, fr…")
        st.checkbox("Uniquement les voix générées", value=True, key=generated_only_key,
                    help="Catégorie exacte « generated » du catalogue ElevenLabs. Décoche pour voir aussi les "
                         "voix « professional » et les autres catégories accessibles à ce compte.")

        result = ev.resolve_catalog()
        if result.error:
            st.markdown(f'<div class="banner banner-info" role="status">{esc(result.error)}</div>',
                       unsafe_allow_html=True)
            return _UNAVAILABLE
        if result.empty:
            st.markdown(
                '<div class="banner banner-info" role="status">Le catalogue préparé côté hôte ne contient '
                "aucune voix pour ce compte. La saisie manuelle de l’identifiant reste disponible ci-dessous."
                "</div>", unsafe_allow_html=True,
            )
            return _UNAVAILABLE
        if result.stale:
            st.caption("⚠️ Ce catalogue date de plus de 30 jours : demande à l’administrateur de le régénérer "
                      "(./scripts/lody-elevenlabs-voices-report.sh).")

        matched = bool(current_voice_id) and any(voice.voice_id == current_voice_id for voice in result.voices)

        generated_only = bool(st.session_state.get(generated_only_key, True))
        pool = [voice for voice in result.voices if voice.category == "generated"] if generated_only else list(result.voices)
        if generated_only and not pool:
            st.caption("Aucune voix « generated » disponible sur ce compte : décoche « Uniquement les voix "
                      "générées » ci-dessus pour voir les autres catégories (ex. « professional »).")
            return PickerResult(available=True, matched=matched)

        matches = ev.search_voices(pool, st.session_state.get(search_key, ""))
        if not matches:
            st.caption("Aucune voix ne correspond à cette recherche.")
            return PickerResult(available=True, matched=matched)

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
        return PickerResult(available=True, matched=matched)
