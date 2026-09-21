"""Navigation par paramètres d'URL : un rechargement rouvre la même page.

  (aucun)                   → accueil
  ?vue=nouveau              → création
  ?projet=<id>              → page du projet
  ?projet=<id>&vue=parametres → paramètres du projet (« modifier » reste accepté)
  ?projet=<id>&vue=production → nouvelle production
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

VIEW_HOME = "home"
VIEW_NEW = "nouveau"
VIEW_PROJECT = "projet"
VIEW_SETTINGS = "parametres"
VIEW_PRODUCTION = "production"


@dataclass(frozen=True)
class Route:
    view: str
    project_id: str | None = None


def current_route() -> Route:
    view = str(st.query_params.get("vue", "")).lower()
    project_id = str(st.query_params.get("projet", "")).strip() or None
    if project_id:
        if view in (VIEW_SETTINGS, "modifier"):
            return Route(VIEW_SETTINGS, project_id)
        if view == VIEW_PRODUCTION:
            return Route(VIEW_PRODUCTION, project_id)
        return Route(VIEW_PROJECT, project_id)
    if view == VIEW_NEW:
        return Route(VIEW_NEW)
    return Route(VIEW_HOME)


def go(view: str = VIEW_HOME, project_id: str | None = None) -> None:
    """Change de page (utilisable comme ``on_click``)."""
    params: dict[str, str] = {}
    # Une nouvelle page repart toujours des valeurs enregistrées : on oublie erreurs et saisies non validées.
    st.session_state.pop("form_errors", None)
    for key in [k for k in st.session_state if str(k).startswith(f"set_{project_id}_")] if project_id else []:
        del st.session_state[key]
    if project_id:
        params["projet"] = project_id
        if view in (VIEW_SETTINGS, VIEW_PRODUCTION):
            params["vue"] = view
    elif view == VIEW_NEW:
        params["vue"] = VIEW_NEW
    st.query_params.clear()
    st.query_params.update(params)


def flash(kind: str, message: str) -> None:
    """Message affiché une fois, en haut de la prochaine page."""
    st.session_state["_flash"] = (kind, message)
