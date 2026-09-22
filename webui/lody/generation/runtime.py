"""Assemblage du service de production pour l'application (sans Streamlit : testable)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from lody import settings
from lody.characters import CharacterRepository
from lody.generation import demo, mpt_connector
from lody.generation.kit_service import KitService
from lody.generation.kit_store import KitRepository
from lody.generation.service import ProductionService
from lody.generation.store import ProductionRepository
from lody.locations import LocationRepository
from lody.projects import ProjectRepository

logger = logging.getLogger("lody.runtime")

DEFAULT_PROVIDER = mpt_connector.PROVIDER_ID
DEMO_PROVIDER = demo.PROVIDER_ID


def build_service() -> ProductionService:
    """Ouvre la base, enregistre les connecteurs et reprend les productions non terminées.

    La reprise ne fait que *demander l'état* au moteur : aucun appel payant, aucun relancement.
    """
    repo = ProductionRepository(settings.db_path())
    providers = {
        mpt_connector.PROVIDER_ID: mpt_connector.build_from_environment(),
        demo.PROVIDER_ID: demo.DemoConnector(),
    }
    # character_repo/location_repo (#35) : sans écran de sélection encore (#34), rien ne les utilise ; ils
    # rendent prepare() capable de résoudre une sélection dès que cet écran existera, sans nouveau branchement.
    service = ProductionService(repo, providers, ThreadPoolExecutor(max_workers=2, thread_name_prefix="lody-production"),
                                character_repo=CharacterRepository(settings.db_path()),
                                location_repo=LocationRepository(settings.db_path()))
    try:
        service.resume_active()
    except Exception as error:  # le démarrage de l'interface ne doit jamais dépendre du moteur
        logger.warning("reprise des productions impossible pour l'instant (%s)", type(error).__name__)
    return service


def build_kit_service(service: ProductionService) -> KitService:
    """Service des kits de publication (local et gratuit ; un seul fond payant à la fois, après confirmation)."""
    return KitService(KitRepository(settings.db_path()), service, ProjectRepository(settings.db_path()),
                      ThreadPoolExecutor(max_workers=1, thread_name_prefix="lody-kit"))
