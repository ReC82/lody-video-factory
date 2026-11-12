"""Atelier 2D (#154) de la page « Nouvelle production », visible en mode Animation 2D / Hybride.

Trois actions, toutes gratuites et locales :
- importer un catalogue d'assets (JSON ``lody.animation-catalog``) dans le projet, fichiers fournis compris ;
- installer la démonstration Voltix #150 (catalogue + storyboard), utilisée ensuite comme storyboard ;
- lancer le préflight du worker 2D (moteur, Chrome, ffmpeg, ressources, isolement, licence Remotion).
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from lody import settings
from lody.animation import pilot
from lody.animation import preflight as worker_preflight
from lody.animation.assets import AnimationAssetRepository, AssetValidationError
from lody.animation.contract import AnimationValidationError
from lody.projects import Project
from lody.theme import esc

STATE_ICONS = {worker_preflight.OK: "✅", worker_preflight.WARNING: "⚠️", worker_preflight.ERROR: "⛔",
               worker_preflight.DECISION: "⚖️"}


def preset_key(project: Project) -> str:
    return f"animation_preset_{project.id}"


def _repo() -> AnimationAssetRepository:
    return AnimationAssetRepository(settings.db_path())


def adopt(project: Project, document: Any) -> Any:
    """Storyboard figé dans un autre projet → assets identiques de ce projet ; inchangé sinon (le gel signale)."""
    try:
        return pilot.adopt_storyboard(_repo(), project.id, document)
    except AnimationValidationError:
        return document


def _import_catalog(project: Project, uploaded: Any, notice: str) -> None:
    if uploaded is None:
        st.session_state[notice] = ("error", ["Choisis d'abord un fichier de catalogue (JSON)."])
        return
    try:
        catalog = json.loads(uploaded.getvalue().decode("utf-8"))
        result = pilot.import_catalog_with_files(_repo(), project.id, catalog, settings.data_dir())
    except (UnicodeDecodeError, ValueError):
        st.session_state[notice] = ("error", ["Le catalogue n'est pas un fichier JSON lisible (UTF-8)."])
    except AssetValidationError as error:
        st.session_state[notice] = ("error", [f"{path} : {message}" for path, message in error.errors.items()][:6])
    else:
        st.session_state[notice] = ("success", [f"{len(result.id_map)} asset(s) importé(s), {result.copied} fichier(s) "
                                                f"mis en place.", *result.warnings])


def _install_demo(project: Project, notice: str) -> None:
    try:
        storyboard, notes = pilot.install_voltix_demo(_repo(), project.id, settings.data_dir())
    except (OSError, ValueError, AssetValidationError, AnimationValidationError) as error:
        st.session_state[notice] = ("error", [f"Démo #150 impossible à installer : {error}"])
        return
    st.session_state[preset_key(project)] = storyboard
    st.session_state[notice] = ("success", ["Démo Voltix #150 prête : elle sert de storyboard tant qu'aucun "
                                            "fichier n'est importé ci-dessus.", *notes])


def _run_preflight(notice: str) -> None:
    st.session_state[notice] = ("checks", worker_preflight.run_checks())


def render(project: Project) -> None:
    p = f"pilot_{project.id}"
    notice = f"{p}_notice"
    with st.expander("Atelier 2D — catalogue d'assets, démo Voltix et vérification du worker"):
        catalog = st.file_uploader("Catalogue d'assets (JSON lody.animation-catalog)", type=["json"], key=f"{p}_catalog",
                                   help="Les fichiers fournis avec Lody sont recopiés après vérification d'empreinte.")
        with st.container(horizontal=True):
            st.button("Importer le catalogue", key=f"{p}_import", icon=":material/upload:",
                      on_click=_import_catalog, args=(project, catalog, notice))
            st.button("Installer la démo Voltix #150", key=f"{p}_demo", icon=":material/movie:",
                      on_click=_install_demo, args=(project, notice))
            st.button("Vérifier le worker 2D", key=f"{p}_preflight", icon=":material/fact_check:",
                      on_click=_run_preflight, args=(notice,))
        if st.session_state.get(preset_key(project)) is not None:
            st.caption("Storyboard en attente : démo Voltix #150 (remplacé par un fichier importé ci-dessus).")
        kind, payload = st.session_state.get(notice) or (None, None)
        if kind == "checks":
            for check in payload:
                st.markdown(f"{STATE_ICONS[check.state]} **{esc(check.label)}** — {esc(check.message)}")
                if check.detail:
                    st.caption(check.detail)
            blocking = [check for check in payload if check.blocking]
            (st.error if blocking else st.success)(
                f"{len(blocking)} point(s) bloquant(s)." if blocking else "Worker 2D prêt pour un rendu.")
        elif kind:
            (st.success if kind == "success" else st.error)(payload[0])
            for line in payload[1:]:
                st.caption(line)
