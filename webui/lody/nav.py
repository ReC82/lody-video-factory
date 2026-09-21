"""Navigation par paramètres d'URL : un rechargement rouvre la même page.

  (aucun)                   → accueil
  ?vue=nouveau              → création
  ?projet=<id>              → page du projet
  ?projet=<id>&vue=modifier → modification
  ?projet=<id>&vue=production → nouvelle production
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

VIEW_HOME = "home"
VIEW_NEW = "nouveau"
VIEW_PROJECT = "projet"
VIEW_EDIT = "modifier"
VIEW_PRODUCTION = "production"


@dataclass(frozen=True)
class Route:
    view: str
    project_id: str | None = None


def current_route() -> Route:
    view = str(st.query_params.get("vue", "")).lower()
    project_id = str(st.query_params.get("projet", "")).strip() or None
    if project_id:
        if view == VIEW_EDIT:
            return Route(VIEW_EDIT, project_id)
        if view == VIEW_PRODUCTION:
            return Route(VIEW_PRODUCTION, project_id)
        return Route(VIEW_PROJECT, project_id)
    if view == VIEW_NEW:
        return Route(VIEW_NEW)
    return Route(VIEW_HOME)


def go(view: str = VIEW_HOME, project_id: str | None = None) -> None:
    """Change de page (utilisable comme ``on_click``)."""
    params: dict[str, str] = {}
    if project_id:
        params["projet"] = project_id
        if view in (VIEW_EDIT, VIEW_PRODUCTION):
            params["vue"] = view
    elif view == VIEW_NEW:
        params["vue"] = VIEW_NEW
    st.query_params.clear()
    st.query_params.update(params)


def flash(kind: str, message: str) -> None:
    """Message affiché une fois, en haut de la prochaine page."""
    st.session_state["_flash"] = (kind, message)
