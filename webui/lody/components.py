"""Éléments d'interface partagés."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st

from lody import nav
from lody.projects import Project
from lody.generation.models import CapabilityState, CapabilityStatus
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


_PILL_TEXT = {
    CapabilityState.READY: ("ok", "Prêt"), CapabilityState.NOT_NEEDED: ("muted", "Non nécessaire"),
    CapabilityState.DISABLED: ("muted", "Désactivée"), CapabilityState.NOT_CONFIGURED: ("warn", "À configurer"),
    CapabilityState.UNAVAILABLE: ("fail", "Indisponible"), CapabilityState.UNVERIFIED: ("warn", "Non vérifié"),
}


def capability_pill(status: CapabilityStatus | None) -> str:
    """Pastille d'état réel d'un fournisseur (configuré, à configurer, indisponible, non vérifié)."""
    if status is None:
        return ""
    kind, text = _PILL_TEXT[status.state]
    return f'<span class="pill pill-{kind}" title="{esc(status.message)}">{esc(text)}</span>'


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


# Sous-pages d'un projet (#212) : « Retour au projet » s'affiche en premier, sous l'en-tête commun.
PROJECT_SUBVIEWS = (nav.VIEW_SETTINGS, nav.VIEW_PRODUCTION, nav.VIEW_TRACK, nav.VIEW_V2,
                    nav.VIEW_CHARACTERS, nav.VIEW_LOCATIONS)


def render_project_back(project: Project | None, view: str) -> None:
    """« ← Retour au projet » immédiatement sous l'en-tête, au-dessus du titre de la page (#212).

    Jamais dans un expander ni en bas de page : visible dès le premier écran sur mobile. Le lien
    « Tous les projets » de l'en-tête reste disponible, en second plan. Le retour passe par ``nav.go`` :
    les brouillons persistés (#189) et les réglages enregistrés sont conservés."""
    if project is None or view not in PROJECT_SUBVIEWS:
        return
    with st.container(key="project_back_bar"):
        st.button("Retour au projet", icon=":material/arrow_back:", key="nav_project",
                  on_click=nav.go, args=(nav.VIEW_PROJECT, project.id),
                  help=f"Revenir à la page du projet « {project.name} »")


def render_flash() -> None:
    flash = st.session_state.pop("_flash", None)
    if flash:
        kind, message = flash
        st.markdown(
            f'<div class="banner banner-{esc(kind)}" role="status">{esc(message)}</div>',
            unsafe_allow_html=True,
        )


def render_footer() -> None:
    # Attribution MIT du projet d'origine : le NOM du projet et sa licence sont ce que la licence exige
    # (la notice complète vit dans LICENSE, à la racine du dépôt). Aucun lien vers le compte d'origine :
    # l'attribution ne doit pas créer de dépendance au namespace historique.
    st.markdown(
        '<p class="footnote">Lody Video Factory est construit sur '
        "MoneyPrinterTurbo (licence MIT).</p>",
        unsafe_allow_html=True,
    )
