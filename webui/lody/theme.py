"""Chargement du thème : CSS statique + variables d'accent."""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

_CSS_FILE = Path(__file__).with_name("styles.css")

# Palettes d'accent (de, via, vers) : cyan → bleu → violet, sans effet néon.
PALETTES = (
    ("#22D3EE", "#38BDF8", "#60A5FA"),
    ("#60A5FA", "#818CF8", "#A78BFA"),
    ("#38BDF8", "#818CF8", "#C084FC"),
    ("#A78BFA", "#7DD3FC", "#22D3EE"),
)
DEFAULT_ACCENT = ("#22D3EE", "#60A5FA", "#A78BFA")


def inject_theme(accent: tuple[str, str, str] = DEFAULT_ACCENT) -> None:
    """Injecte la feuille de style et les variables d'accent.

    Les couleurs viennent de constantes internes, jamais d'une saisie utilisateur.
    """
    first, via, last = accent
    variables = f":root{{--accent-1:{first};--accent-2:{via};--accent-3:{last};}}"
    css = _CSS_FILE.read_text(encoding="utf-8")
    st.markdown(f"<style>{variables}\n{css}</style>", unsafe_allow_html=True)


def esc(value: object) -> str:
    """Échappe une valeur avant de l'insérer dans un bloc HTML."""
    return html.escape(str(value), quote=True)
