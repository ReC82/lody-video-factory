"""Assemblage du service de production pour l'application (sans Streamlit : testable)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from lody import settings
from lody.generation import demo, mpt_connector
from lody.generation.service import ProductionService
from lody.generation.store import ProductionRepository

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
    service = ProductionService(repo, providers, ThreadPoolExecutor(max_workers=2, thread_name_prefix="lody-production"))
    try:
        service.resume_active()
    except Exception as error:  # le démarrage de l'interface ne doit jamais dépendre du moteur
        logger.warning("reprise des productions impossible pour l'instant (%s)", type(error).__name__)
    return service
