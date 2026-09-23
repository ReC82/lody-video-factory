"""Assemblage de l'application : base de données, routage, pages."""

from __future__ import annotations

import logging

import streamlit as st

from lody import nav, settings
from lody import view_characters, view_form, view_home, view_locations, view_production, view_project, view_settings, \
    view_system_settings, view_tracking, view_v2
from lody.characters import CharacterRepository
from lody.generation.kit_service import KitService
from lody.generation.runtime import DEFAULT_PROVIDER, build_kit_service, build_service
from lody.generation.service import ProductionService
from lody.components import accent_for, render_flash, render_footer, render_header
from lody.locations import LocationRepository
from lody.projects import ProjectNotFound, ProjectRepository
from lody.seeds import SEED_PROJECTS
from lody.theme import DEFAULT_ACCENT, inject_theme

logger = logging.getLogger("lody.app")


@st.cache_resource(show_spinner=False)
def get_repository() -> ProjectRepository:
    """Ouvre la base (créée si besoin) et ajoute les exemples manquants, sans doublon."""
    repo = ProjectRepository(settings.db_path())
    if settings.seed_defaults_enabled():
        repo.seed_defaults(SEED_PROJECTS)
    return repo


@st.cache_resource(show_spinner=False)
def get_production_service() -> ProductionService:
    """Service de production partagé (threads de génération, reprise des productions non terminées)."""
    return build_service()


@st.cache_resource(show_spinner=False)
def get_kit_service() -> KitService:
    """Service des kits de publication, construit sur le service de production partagé."""
    return build_kit_service(get_production_service())


@st.cache_resource(show_spinner=False)
def get_character_repository() -> CharacterRepository:
    """Personnages récurrents (#30/#32) : facultatifs, jamais lus par le pipeline de génération."""
    return CharacterRepository(settings.db_path())


@st.cache_resource(show_spinner=False)
def get_location_repository() -> LocationRepository:
    """Lieux récurrents (#31/#33) : facultatifs, jamais lus par le pipeline de génération."""
    return LocationRepository(settings.db_path())


def _render_storage_error() -> None:
    inject_theme(DEFAULT_ACCENT)
    render_header(False)
    st.markdown(
        '<div class="banner banner-error" role="alert"><strong>Les projets sont momentanément inaccessibles.</strong> '
        "Le dossier de données n’est pas disponible en écriture. Réessaie dans un instant ; "
        "si le problème persiste, contacte l’administrateur.</div>",
        unsafe_allow_html=True,
    )


def render() -> None:
    try:
        repo = get_repository()
        service = get_production_service()
    except Exception as error:  # base illisible ou volume non inscriptible
        logger.error("ouverture de la base impossible : %s", type(error).__name__)
        _render_storage_error()
        return

    route = nav.current_route()
    if route.view == nav.VIEW_SYSTEM_SETTINGS and not settings.system_settings_enabled():
        # Verrou de mise en ligne : cette page n'existe pas tant qu'elle n'est pas explicitement activée
        # (elle ne doit jamais être accessible avant que le site ait une authentification).
        nav.go()
        route = nav.Route(nav.VIEW_HOME)
    project = None
    if route.project_id:
        try:
            project = repo.get(route.project_id)
        except ProjectNotFound:
            nav.flash("error", "Ce projet est introuvable.")
            nav.go()
            route = nav.Route(nav.VIEW_HOME)
    if project is not None and project.is_archived and route.view in (
        nav.VIEW_SETTINGS, nav.VIEW_PRODUCTION, nav.VIEW_V2, nav.VIEW_CHARACTERS, nav.VIEW_LOCATIONS,
    ):
        nav.flash("info", "Ce projet est archivé : restaure-le pour continuer.")
        nav.go(nav.VIEW_PROJECT, project.id)
        route = nav.Route(nav.VIEW_PROJECT, project.id)

    inject_theme(accent_for(project))
    render_header(with_home_link=route.view != nav.VIEW_HOME)
    render_flash()

    if route.view == nav.VIEW_HOME:
        view_home.render(repo)
    elif route.view == nav.VIEW_NEW:
        view_form.render(repo, service.option_states(None, DEFAULT_PROVIDER))
    elif route.view == nav.VIEW_SETTINGS and project:
        view_settings.render(repo, project, service.option_states(project, DEFAULT_PROVIDER),
                             get_character_repository(), get_location_repository())
    elif route.view == nav.VIEW_PRODUCTION and project:
        view_production.render(project, service, get_character_repository(), get_location_repository())
    elif route.view == nav.VIEW_TRACK and project and route.production_id:
        view_tracking.render(service, project, route.production_id, get_kit_service())
    elif route.view == nav.VIEW_V2 and project and route.production_id:
        view_v2.render(service, project, route.production_id)
    elif route.view == nav.VIEW_CHARACTERS and project:
        view_characters.render(get_character_repository(), project)
    elif route.view == nav.VIEW_LOCATIONS and project:
        view_locations.render(get_location_repository(), project)
    elif route.view == nav.VIEW_SYSTEM_SETTINGS:
        view_system_settings.render()
    elif project:
        view_project.render(repo, project, service)
    render_footer()
