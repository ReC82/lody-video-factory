"""« Paramètres système » : les clés d'API du moteur, saisies une fois, jamais réaffichées.

Verrouillée derrière ``settings.system_settings_enabled()`` (désactivé par défaut, aucun lien nulle part tant
qu'elle ne l'est pas) : le site n'a aujourd'hui aucune authentification, et cette page ne doit jamais être
accessible tant que ce n'est pas corrigé (voir docs/lody-secrets.md).

Rien sur cette page ne relit jamais une valeur : ni un bouton d'affichage, ni d'export, ni de copie. Après
enregistrement, l'état affiché (Configurée / Absente / Invalide) vient de deux sources qui ne contiennent jamais
de secret : le rapport de capacités du moteur (``engine_facts``, déjà utilisé pour le préflight) et le statut
d'application (``secrets_store.status``, écrit par le script hôte).
"""

from __future__ import annotations

import time

import streamlit as st

from lody import settings
from lody.generation import engine_facts, secrets_store
from lody.generation.secrets_fields import FIELD_ORDER, FIELDS
from lody.theme import esc

_IN_FLIGHT_LABEL = {
    "applying": "Enregistrement…", "restarting": "Redémarrage du moteur…",
    "verifying": "Vérification auprès du fournisseur…", "restoring": "Restauration de la configuration précédente…",
}
_TERMINAL_LABEL = {
    "ok": ("ok", "Configurée"), "invalid": ("fail", "Invalide"),
    "unverified": ("warn", "Configurée (non vérifiée auprès du fournisseur)"),
    "rejected": ("fail", "Refusée par le fournisseur — ancienne configuration restaurée"),
    "rolled_back": ("warn", "Invalide — ancienne configuration restaurée"), "critical": ("fail", "Erreur critique"),
}
_POLL_SECONDS = 90  # au-delà, l'utilisateur peut toujours revenir sur la page : pas d'attente indéfinie


def _prefix() -> str:
    return "sys"


def _facts() -> engine_facts.EngineFacts:
    return engine_facts.resolve()


def _current(field_path: str, facts: engine_facts.EngineFacts) -> tuple[bool, str]:
    """(configurée ?, indice des 4 derniers caractères) d'après le rapport du moteur — jamais journalisé."""
    if field_path == "app.openai_api_key":
        return bool(facts.llm_key.get("openai")), facts.llm_key_last4.get("openai", "")
    if field_path == "app.openai_image_api_keys":
        return facts.image_key, facts.image_key_last4
    if field_path == "elevenlabs.api_key":
        return facts.eleven_key, facts.eleven_key_last4
    return False, ""


def _pill(field_path: str, facts: engine_facts.EngineFacts) -> str:
    live = secrets_store.field_status(field_path)
    if live["state"] in _IN_FLIGHT_LABEL:
        return f'<span class="pill pill-warn">{esc(_IN_FLIGHT_LABEL[live["state"]])}</span>'
    if live["state"] in _TERMINAL_LABEL:
        kind, text = _TERMINAL_LABEL[live["state"]]
        title = f' title="{esc(live["message"])}"' if live.get("message") else ""
        return f'<span class="pill pill-{kind}"{title}>{esc(text)}</span>'
    configured, last4 = _current(field_path, facts)
    if not configured:
        return '<span class="pill pill-muted">Absente</span>'
    hint = f" (se termine par {esc(last4)})" if last4 else ""
    return f'<span class="pill pill-ok">Configurée{hint}</span>'


def _is_busy(field_path: str) -> bool:
    return secrets_store.field_status(field_path)["state"] in _IN_FLIGHT_LABEL


def _save_secret(field_path: str) -> None:
    ss = st.session_state
    key = f"{_prefix()}_{field_path}"
    value = str(ss.get(key, ""))
    try:
        secrets_store.set_pending(field_path, value)
        ss[key] = ""  # jamais laissée dans le formulaire, même après un aller-retour de page
        ss[f"{key}_saved_at"] = time.time()
    except secrets_store.ShapeError as error:
        st.session_state["_sys_flash"] = ("error", str(error))


def _save_provider(field_path: str, value: str) -> None:
    try:
        secrets_store.set_pending(field_path, value)
    except secrets_store.ShapeError as error:
        st.session_state["_sys_flash"] = ("error", str(error))


def _field_block(field_path: str, facts: engine_facts.EngineFacts) -> None:
    spec = FIELDS[field_path]
    st.markdown(
        f'<div class="sys-field"><div class="sys-field-head"><span class="sys-field-label">{esc(spec.label)}</span>'
        f'<span class="sys-field-provider">{esc(spec.provider_label)}</span></div>{_pill(field_path, facts)}</div>',
        unsafe_allow_html=True,
    )
    busy = _is_busy(field_path)
    if spec.kind == "choice":
        current = facts.llm_provider or "(non défini)"
        st.markdown(f'<p class="muted-note">Actuellement actif : <strong>{esc(current)}</strong></p>', unsafe_allow_html=True)
        choice = st.selectbox("Nouveau fournisseur", spec.choices, key=f"{_prefix()}_{field_path}_choice", disabled=busy)
        st.button("Appliquer", key=f"{_prefix()}_{field_path}_apply", disabled=busy,
                  on_click=_save_provider, args=(field_path, choice))
        return
    st.text_input("Nouvelle valeur", key=f"{_prefix()}_{field_path}", type="password", disabled=busy,
                  label_visibility="collapsed", placeholder=f"Nouvelle clé {spec.provider_label}…")
    st.button("Enregistrer", key=f"{_prefix()}_{field_path}_save", disabled=busy, type="primary",
              on_click=_save_secret, args=(field_path,))


def render() -> None:
    if not settings.system_settings_enabled():
        st.markdown(
            '<div class="banner banner-error" role="alert">Cette page est désactivée.</div>',
            unsafe_allow_html=True,
        )
        return
    with st.container(key="sys_settings"):
        st.markdown(
            '<p class="card-eyebrow">Administration</p><h2 class="brief-title">Paramètres système</h2>'
            '<p class="muted-note">Les clés sont enregistrées puis appliquées automatiquement (le moteur redémarre '
            "et sa santé est vérifiée). Une valeur enregistrée n’est <strong>jamais</strong> réaffichée : cette page "
            "ne propose ni affichage, ni export, ni copie.</p>",
            unsafe_allow_html=True,
        )
        flash = st.session_state.pop("_sys_flash", None)
        if flash:
            st.markdown(f'<div class="banner banner-{esc(flash[0])}" role="alert">{esc(flash[1])}</div>', unsafe_allow_html=True)
        facts = _facts()
        any_busy = False
        for field_path in FIELD_ORDER:
            _field_block(field_path, facts)
            any_busy = any_busy or _is_busy(field_path)
        overall = secrets_store.status()
        if overall.get("state") == "critical":
            st.markdown(
                f'<div class="banner banner-error" role="alert"><strong>État critique.</strong> {esc(overall.get("message", ""))}</div>',
                unsafe_allow_html=True,
            )
        if any_busy:
            st.button("Actualiser", icon=":material/refresh:", key=f"{_prefix()}_refresh")
            _live()


@st.fragment(run_every=2)
def _live() -> None:
    """Poll borné (voir _POLL_SECONDS) tant qu'un champ est en cours d'application ; jamais de scrutation
    indéfinie côté serveur — le fragment s'arrête de se relancer une fois tous les champs revenus au repos."""
    ss = st.session_state
    started = ss.setdefault("_sys_live_started", time.time())
    if time.time() - started > _POLL_SECONDS:
        st.markdown('<p class="muted-note">Actualise la page pour voir la suite.</p>', unsafe_allow_html=True)
        return
    if not any(_is_busy(field_path) for field_path in FIELD_ORDER):
        del ss["_sys_live_started"]
        st.rerun()
