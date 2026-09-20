"""Chargement du thème : CSS statique + variables d'accent du projet actif."""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from lody.profiles import ProjectProfile

_CSS_FILE = Path(__file__).with_name("styles.css")


def _read_css() -> str:
    return _CSS_FILE.read_text(encoding="utf-8")


def inject_theme(project: ProjectProfile) -> None:
    """Injecte la feuille de style et les variables d'accent du projet.

    Les couleurs viennent de constantes internes (``profiles.py``), jamais d'une
    saisie utilisateur : aucune injection n'est possible via cette voie.
    """
    accent_vars = (
        ":root{"
        f"--accent-1:{project.accent_from};"
        f"--accent-2:{project.accent_via};"
        f"--accent-3:{project.accent_to};"
        "}"
    )
    st.markdown(
        f"<style>{accent_vars}\n{_read_css()}</style>",
        unsafe_allow_html=True,
    )


def esc(value: object) -> str:
    """Échappe une valeur avant de l'insérer dans un bloc HTML."""
    return html.escape(str(value), quote=True)
