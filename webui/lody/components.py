"""Éléments d'interface partagés."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

from lody import nav
from lody.catalog import PROVIDER_KIND_BY_FIELD, PROVIDER_REQUIREMENTS
from lody.projects import Project
from lody.provider_status import Readiness, is_ready, is_unverified
from lody.theme import DEFAULT_ACCENT, PALETTES, esc

_MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")

LOGO_SVG = (
    '<svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">'
    '<defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="var(--accent-1)"/>'
    '<stop offset="1" stop-color="var(--accent-3)"/></linearGradient></defs>'
    '<rect width="32" height="32" rx="9" fill="url(#lg)"/>'
    '<path d="M12.5 9.5v13l10-6.5z" fill="#06101C"/></svg>'
)
EMPTY_ICON_SVG = (
    '<svg viewBox="0 0 48 48" width="48" height="48" fill="none" aria-hidden="true">'
    '<rect x="13" y="6" width="22" height="36" rx="5" stroke="currentColor" stroke-width="2"/>'
    '<path d="M21.5 20.5v7l6-3.5z" fill="currentColor"/></svg>'
)


def accent_for(project: Project | None) -> tuple[str, str, str]:
    """Accent d'un projet : réglage explicite, sinon dérivé de son identifiant."""
    if project is None:
        return DEFAULT_ACCENT
    chosen = project.settings.get("accent")
    if isinstance(chosen, int) and 0 <= chosen < len(PALETTES):
        return PALETTES[chosen]
    return PALETTES[sum(map(ord, project.id)) % len(PALETTES)]


def format_datetime(iso_value: str) -> str:
    """« 21 sept. 2026 à 02:14 » dans le fuseau LODY_TIMEZONE (défaut Europe/Paris)."""
    try:
        moment = datetime.fromisoformat(iso_value)
    except ValueError:
        return iso_value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        moment = moment.astimezone(ZoneInfo(os.environ.get("LODY_TIMEZONE", "Europe/Paris")))
    except Exception:  # fuseau indisponible : on garde l'heure UTC
        moment = moment.astimezone(timezone.utc)
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year} à {moment:%H:%M}"


def status_badge(project: Project) -> str:
    if project.is_archived:
        return '<span class="status status-archived">Archivé</span>'
    return '<span class="status status-active">Actif</span>'


def readiness_pill(project: Project, field: str, table: Readiness) -> str:
    """« Prêt » / « Clé à configurer » — uniquement pour les fournisseurs à clé."""
    kind = PROVIDER_KIND_BY_FIELD[field]
    value = getattr(project, field)
    if (kind, value) not in PROVIDER_REQUIREMENTS:
        return ""
    if is_unverified(kind, value, table):
        return '<span class="pill pill-muted" title="Lody ne peut pas lire la configuration du serveur">Non vérifiée</span>'
    if is_ready(kind, value, table):
        return '<span class="pill pill-ok">Prêt</span>'
    return '<span class="pill pill-warn">Clé à configurer</span>'


def render_header(with_home_link: bool) -> None:
    with st.container(horizontal=True, vertical_alignment="center", key="topbar"):
        st.markdown(
            f'<div class="brand">{LOGO_SVG}<span class="brand-name">Lody Video Factory</span>'
            '<span class="badge">Bêta</span></div>',
            unsafe_allow_html=True,
        )
        if with_home_link:
            st.button(
                "Tous les projets",
                icon=":material/arrow_back:",
                type="tertiary",
                key="nav_home",
                on_click=nav.go,
            )


def render_flash() -> None:
    flash = st.session_state.pop("_flash", None)
    if flash:
        kind, message = flash
        st.markdown(
            f'<div class="banner banner-{esc(kind)}" role="status">{esc(message)}</div>',
            unsafe_allow_html=True,
        )


def render_footer() -> None:
    st.markdown(
        '<p class="footnote">Lody Video Factory est construit sur '
        '<a href="https://github.com/harry0703/MoneyPrinterTurbo" target="_blank" rel="noopener">'
        "MoneyPrinterTurbo</a> (licence MIT).</p>",
        unsafe_allow_html=True,
    )
