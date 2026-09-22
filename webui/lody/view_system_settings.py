"""« Paramètres système » : les clés d'API du moteur, saisies une fois, jamais réaffichées.

Verrouillée derrière DEUX niveaux indépendants : ``settings.system_settings_enabled()`` (désactivé par défaut,
aucun lien nulle part tant qu'il ne l'est pas) ET une session administrateur réelle (``admin_auth``), revérifiée
à chaque affichage ET avant chaque écriture — pas seulement au chargement de la page, pour que l'expiration de
session s'applique même en cours d'utilisation. Voir docs/lody-auth.md.

Rien sur cette page ne relit jamais une valeur : ni un bouton d'affichage, ni d'export, ni de copie. Avant que
la session ne soit validée, RIEN n'est calculé à partir d'un secret — pas même une pastille Configurée/Absente.
L'état affiché ensuite vient de deux sources qui ne contiennent jamais de secret : le rapport de capacités du
moteur (``engine_facts``, déjà utilisé pour le préflight) et le statut d'application (``secrets_store.status``,
écrit par le script hôte, avec les dates de dernière modification et de dernier contrôle).
"""

from __future__ import annotations

import time

import streamlit as st

from lody import settings
from lody.components import format_datetime
from lody.generation import admin_auth, engine_facts, secrets_store
from lody.generation.secrets_fields import FIELD_ORDER, FIELDS
from lody.theme import esc

_IN_FLIGHT_LABEL = {
    "applying": "Enregistrement…", "restarting": "Redémarrage du moteur…",
    "verifying": "Vérification auprès du fournisseur…", "restoring": "Restauration de la configuration précédente…",
}
_TERMINAL_LABEL = {
    "ok": ("ok", "Configurée"), "invalid": ("fail", "Invalide"),
    "unverified": ("warn", "Configurée (non vérifiée auprès du fournisseur)"),
    "expired": ("fail", "Expirée — ancienne configuration restaurée"),
    "rejected": ("fail", "Invalide — ancienne configuration restaurée"),
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


def _pill(field_path: str, facts: engine_facts.EngineFacts) -> tuple[str, dict]:
    """(HTML de la pastille, statut brut) — le statut est réutilisé pour les dates, pas besoin de le relire."""
    live = secrets_store.field_status(field_path)
    if live["state"] in _IN_FLIGHT_LABEL:
        return f'<span class="pill pill-warn">{esc(_IN_FLIGHT_LABEL[live["state"]])}</span>', live
    if live["state"] in _TERMINAL_LABEL:
        kind, text = _TERMINAL_LABEL[live["state"]]
        title = f' title="{esc(live["message"])}"' if live.get("message") else ""
        return f'<span class="pill pill-{kind}"{title}>{esc(text)}</span>', live
    configured, last4 = _current(field_path, facts)
    if not configured:
        return '<span class="pill pill-muted">Absente</span>', live
    hint = f" (se termine par {esc(last4)})" if last4 else ""
    return f'<span class="pill pill-ok">Configurée{hint}</span>', live


def _is_busy(field_path: str) -> bool:
    return secrets_store.field_status(field_path)["state"] in _IN_FLIGHT_LABEL


def _dates_line(live: dict) -> str:
    parts = []
    if live.get("modified_at"):
        parts.append(f"modifiée le {esc(format_datetime(live['modified_at']))}")
    if live.get("checked_at"):
        parts.append(f"dernier contrôle le {esc(format_datetime(live['checked_at']))}")
    return f'<p class="sys-field-dates">{" · ".join(parts)}</p>' if parts else ""


def _save_secret(field_path: str) -> None:
    ss = st.session_state
    p = _prefix()
    value_key, confirm_key = f"{p}_{field_path}", f"{p}_{field_path}_confirm"
    value, confirm = str(ss.get(value_key, "")), str(ss.get(confirm_key, ""))
    # Purgés immédiatement, y compris en cas d'échec : un mot de passe/une clé tapée ne doit jamais rester dans
    # un champ de formulaire ni dans st.session_state au-delà de cette fonction.
    ss[value_key], ss[confirm_key] = "", ""
    if value != confirm:
        st.session_state["_sys_flash"] = ("error", "Les deux champs ne correspondent pas : rien n’a été enregistré.")
        return
    try:
        secrets_store.set_pending(field_path, value)
        ss[f"{value_key}_saved_at"] = time.time()
        st.session_state["_sys_flash"] = ("success", "Enregistré : application en cours (voir l’état ci-dessous).")
    except secrets_store.ShapeError as error:
        st.session_state["_sys_flash"] = ("error", str(error))


def _save_provider(field_path: str, value: str) -> None:
    try:
        secrets_store.set_pending(field_path, value)
    except secrets_store.ShapeError as error:
        st.session_state["_sys_flash"] = ("error", str(error))


def _field_block(field_path: str, facts: engine_facts.EngineFacts) -> None:
    spec = FIELDS[field_path]
    pill_html, live = _pill(field_path, facts)
    badge = f'<span class="sys-field-capability">{esc(spec.capability)}</span>' if spec.capability else ""
    st.markdown(
        f'<div class="sys-field"><div class="sys-field-head"><span class="sys-field-label">{esc(spec.label)}</span>'
        f'<span class="sys-field-provider">{esc(spec.provider_label)}</span>{badge}</div>{pill_html}'
        f'{_dates_line(live)}</div>',
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
    columns = st.columns(2)
    with columns[0]:
        st.text_input("Nouvelle clé", key=f"{_prefix()}_{field_path}", type="password", disabled=busy,
                      placeholder=f"Nouvelle clé {spec.provider_label}…")
    with columns[1]:
        st.text_input("Confirmation", key=f"{_prefix()}_{field_path}_confirm", type="password", disabled=busy,
                      placeholder="Retape la même clé…")
    label = "Remplacer la clé" if _current(field_path, facts)[0] else "Ajouter une clé"
    st.button(label, key=f"{_prefix()}_{field_path}_save", disabled=busy, type="primary",
              on_click=_save_secret, args=(field_path,))


def _other_engine_providers(facts: engine_facts.EngineFacts) -> None:
    """Fournisseurs de script que le MOTEUR reconnaît mais que le catalogue de Lody ne propose pas de choisir :
    lecture seule, aucun formulaire (Lody n'enverrait jamais de requête à travers eux)."""
    others = sorted((name, present) for name, present in facts.llm_key.items() if name != "openai")
    if not others:
        return
    with st.expander(f"Autres fournisseurs reconnus par le moteur ({len(others)})"):
        st.markdown('<p class="muted-note">Non gérés depuis cette page : le catalogue de Lody ne propose pas de '
                    "les sélectionner. Affichés pour information seulement.</p>", unsafe_allow_html=True)
        rows = "".join(
            f'<div class="sys-other-row"><span>{esc(name)}</span>'
            f'<span class="pill pill-{"ok" if present else "muted"}">{"Configuré" if present else "Absent"}</span></div>'
            for name, present in others)
        st.markdown(f'<div class="sys-other-list">{rows}</div>', unsafe_allow_html=True)


def _logout() -> None:
    st.markdown(
        f'<form method="post" action="{esc(admin_auth.logout_url())}">'
        '<button type="submit" class="sys-logout-btn">Se déconnecter</button></form>',
        unsafe_allow_html=True,
    )


def render() -> None:
    if not settings.system_settings_enabled():
        st.markdown('<div class="banner banner-error" role="alert">Cette page est désactivée.</div>',
                    unsafe_allow_html=True)
        return
    # Revérifié à CHAQUE affichage (jamais mis en cache dans st.session_state) : voir la doc du module.
    session = admin_auth.current_session()
    if session is None:
        with st.container(key="sys_locked"):
            st.markdown(
                '<p class="card-eyebrow">Administration</p><h2 class="brief-title">Authentification requise</h2>'
                '<p class="muted-note">Cette page gère les clés d’API du moteur : elle n’est accessible qu’à '
                "l’administrateur connecté. Rien n’est affiché ni chargé tant que la session n’est pas validée.</p>",
                unsafe_allow_html=True,
            )
            st.markdown(f'<a class="sys-login-link" href="{esc(admin_auth.login_url("/?vue=systeme"))}">'
                        "Se connecter</a>", unsafe_allow_html=True)
        return

    with st.container(key="sys_settings"):
        with st.container(horizontal=True, vertical_alignment="center", key="sys_head"):
            st.markdown(
                '<p class="card-eyebrow">Administration</p><h2 class="brief-title">Paramètres système</h2>'
                f'<p class="muted-note">Connecté en tant que <strong>{esc(session.username)}</strong>. Les clés sont '
                "enregistrées puis appliquées automatiquement (le moteur redémarre, sa santé est vérifiée, puis la clé "
                "est confirmée auprès du fournisseur). Une valeur enregistrée n’est <strong>jamais</strong> réaffichée : "
                "cette page ne propose ni affichage, ni export, ni copie.</p>",
                unsafe_allow_html=True,
            )
            _logout()
        flash = st.session_state.pop("_sys_flash", None)
        if flash:
            st.markdown(f'<div class="banner banner-{esc(flash[0])}" role="alert">{esc(flash[1])}</div>', unsafe_allow_html=True)
        facts = _facts()
        any_busy = False
        for field_path in FIELD_ORDER:
            _field_block(field_path, facts)
            any_busy = any_busy or _is_busy(field_path)
        _other_engine_providers(facts)
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
