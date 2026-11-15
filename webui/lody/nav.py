"""Navigation par paramètres d'URL : un rechargement rouvre la même page.

  (aucun)                   → accueil
  ?vue=nouveau              → création
  ?projet=<id>              → page du projet
  ?projet=<id>&vue=parametres → paramètres du projet (« modifier » reste accepté)
  ?projet=<id>&vue=production → nouvelle production
  ?projet=<id>&vue=suivi&production=<id> → suivi et résultat d'une production
  ?projet=<id>&vue=v2&production=<id>    → création d'une V2 (script modifiable)
  ?projet=<id>&vue=personnages          → personnages récurrents du projet (#32)
  ?projet=<id>&vue=lieux                → lieux récurrents du projet (#33)
  ?vue=personnages                      → personnages de tous les projets, en lecture (#213)
  ?vue=ressources                       → lieux et éléments animés de tous les projets, en lecture (#213)

Sans ``projet``, ``personnages`` mène à la vue transverse ; avec ``projet``, à la page du projet,
exactement comme avant (aucun lien profond existant ne change de destination).
"""

from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

VIEW_HOME = "home"
VIEW_NEW = "nouveau"
VIEW_PROJECT = "projet"
VIEW_SETTINGS = "parametres"
VIEW_PRODUCTION = "production"
VIEW_TRACK = "suivi"
VIEW_V2 = "v2"
VIEW_CHARACTERS = "personnages"
VIEW_LOCATIONS = "lieux"
# Domaines de premier niveau (#213) : identifiants internes distincts des sous-pages d'un projet.
VIEW_ALL_CHARACTERS = "tous_personnages"
VIEW_RESOURCES = "ressources"
TOP_LEVEL_VIEWS = (VIEW_HOME, VIEW_ALL_CHARACTERS, VIEW_RESOURCES)
# Accessible uniquement en connaissant ce paramètre : aucun bouton ni lien n'y mène nulle part dans l'interface
# (verrouillée en plus par settings.system_settings_enabled(), désactivée par défaut — voir docs/lody-secrets.md).
VIEW_SYSTEM_SETTINGS = "systeme"


@dataclass(frozen=True)
class Route:
    view: str
    project_id: str | None = None
    production_id: str | None = None


def current_route() -> Route:
    view = str(st.query_params.get("vue", "")).lower()
    project_id = str(st.query_params.get("projet", "")).strip() or None
    production_id = str(st.query_params.get("production", "")).strip() or None
    if project_id:
        if view in (VIEW_TRACK, VIEW_V2) and production_id:
            return Route(view, project_id, production_id)
        if view in (VIEW_SETTINGS, "modifier"):
            return Route(VIEW_SETTINGS, project_id)
        if view == VIEW_PRODUCTION:
            return Route(VIEW_PRODUCTION, project_id)
        if view in (VIEW_CHARACTERS, VIEW_LOCATIONS):
            return Route(view, project_id)
        return Route(VIEW_PROJECT, project_id)
    if view == VIEW_NEW:
        return Route(VIEW_NEW)
    if view == VIEW_CHARACTERS:
        return Route(VIEW_ALL_CHARACTERS)
    if view == VIEW_RESOURCES:
        return Route(VIEW_RESOURCES)
    if view == VIEW_SYSTEM_SETTINGS:
        return Route(VIEW_SYSTEM_SETTINGS)
    return Route(VIEW_HOME)


def go(view: str = VIEW_HOME, project_id: str | None = None, production_id: str | None = None) -> None:
    """Change de page (utilisable comme ``on_click``)."""
    params: dict[str, str] = {}
    # Une nouvelle page repart toujours des valeurs enregistrées : on oublie erreurs et saisies non validées.
    st.session_state.pop("form_errors", None)
    prefixes = (f"set_{project_id}_", f"char_{project_id}_", f"loc_{project_id}_",
               f"impexp_{project_id}_", f"charimp_{project_id}_", f"locimp_{project_id}_") if project_id else ()
    for key in [k for k in st.session_state if str(k).startswith(prefixes)]:
        del st.session_state[key]
    # Personnages/lieux (#32/#33) : un formulaire ou une confirmation laissés ouverts ne doivent jamais
    # survivre à un changement de page (nouvelle page = état propre, comme pour le reste de la navigation).
    for key in ("_char_form", "_char_confirm_deactivate", "_loc_form", "_loc_confirm_deactivate"):
        st.session_state.pop(key, None)
    if project_id:
        params["projet"] = project_id
        if view in (VIEW_SETTINGS, VIEW_PRODUCTION, VIEW_CHARACTERS, VIEW_LOCATIONS):
            params["vue"] = view
        elif view in (VIEW_TRACK, VIEW_V2) and production_id:
            params["vue"] = view
            params["production"] = production_id
    elif view == VIEW_NEW:
        params["vue"] = VIEW_NEW
    elif view == VIEW_ALL_CHARACTERS:
        params["vue"] = VIEW_CHARACTERS
    elif view == VIEW_RESOURCES:
        params["vue"] = VIEW_RESOURCES
    st.query_params.clear()
    st.query_params.update(params)


def flash(kind: str, message: str) -> None:
    """Message affiché une fois, en haut de la prochaine page."""
    st.session_state["_flash"] = (kind, message)
