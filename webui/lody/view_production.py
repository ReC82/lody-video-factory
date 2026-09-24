"""Nouvelle production : sujet → estimation → confirmation unique → suivi.

Aucun appel payant n'a lieu ici avant le clic « Confirmer et générer la vidéo » : « Préparer la
génération » ne crée qu'un brouillon local avec son estimation.

Sélection facultative de personnages et d'un lieu (#34) : section repliable, entièrement optionnelle,
qui ne propose que les éléments actifs du projet courant (voir ``_active_characters``/``_active_locations``).
Rien n'est imposé automatiquement — le personnage ou lieu « principal » n'est qu'indiqué visuellement
dans la liste — et la sélection ne change strictement rien tant qu'elle reste vide (voir #35 pour le
snapshot immuable qui la porte au lancement).
"""

from __future__ import annotations

import streamlit as st

from lody import brief as brief_lib
from lody import nav, view_estimate
from lody.characters import Character, CharacterRepository
from lody.generation.models import PreflightReport
from lody.generation.runtime import DEFAULT_PROVIDER, DEMO_PROVIDER
from lody.generation.service import (
    AlreadyRunning,
    LaunchError,
    ProductionService,
    build_request,
    request_of,
)
from lody.generation.store import Production
from lody.locations import Location, LocationRepository
from lody.projects import Project
from lody.theme import esc

MIN_REQUEST_LENGTH = 8
_GENERIC_EXAMPLE = "Explique simplement un sujet de ton domaine en moins d’une minute."
LABEL_PREPARE = "Préparer la génération"
LABEL_CONFIRM = "Confirmer et générer la vidéo"


def _keys(project: Project) -> dict[str, str]:
    return {
        "request": f"request_{project.id}",
        "script": f"script_{project.id}",
        "demo": f"demo_{project.id}",
        "draft": f"draft_{project.id}",
        "error": f"request_error_{project.id}",
        "accept": f"accept_partial_{project.id}",
        "running": f"running_{project.id}",
        "retry": f"retry_{project.id}",
        "characters": f"chars_sel_{project.id}",
        "location": f"loc_sel_{project.id}",
    }


def _active_characters(repo: CharacterRepository, project: Project) -> list[Character]:
    return repo.list_for_project(project.id, include_inactive=False)


def _active_locations(repo: LocationRepository, project: Project) -> list[Location]:
    return repo.list_for_project(project.id, include_inactive=False)


def _character_label(character: Character) -> str:
    return character.name + (" · Principal" if character.is_primary else "")


def _location_label(location: Location) -> str:
    return location.name + (" · Principal" if location.is_primary else "")


def selected_narrative(project: Project) -> tuple[list[str], str | None]:
    """La sélection actuelle (personnages, lieu), lue depuis l'état de session de CE projet uniquement."""
    keys = _keys(project)
    return list(st.session_state.get(keys["characters"], [])), st.session_state.get(keys["location"]) or None


def _render_narrative_section(project: Project, character_repo: CharacterRepository,
                              location_repo: LocationRepository) -> None:
    """Section repliable, facultative : ne propose que les personnages/lieux ACTIFS du projet courant.

    Toute sélection devenue invalide (désactivation concurrente pendant que l'écran est ouvert, ou
    bascule vers un autre projet) est silencieusement retirée avant l'affichage des widgets : jamais
    d'erreur, jamais d'identifiant fantôme envoyé à ``service.prepare()``.
    """
    keys = _keys(project)
    characters = _active_characters(character_repo, project)
    locations = _active_locations(location_repo, project)
    if not characters and not locations:
        return  # rien à proposer : le formulaire reste identique à avant ce ticket

    character_ids = [character.id for character in characters]
    valid_selection = [cid for cid in st.session_state.get(keys["characters"], []) if cid in character_ids]
    if valid_selection != st.session_state.get(keys["characters"]):
        st.session_state[keys["characters"]] = valid_selection

    location_ids = [location.id for location in locations]
    current_location = st.session_state.get(keys["location"])
    if current_location and current_location not in location_ids:
        st.session_state[keys["location"]] = None

    with st.expander("Personnages et lieu (facultatif)", expanded=False):
        st.caption(
            "Entièrement facultatif : sans sélection, ta vidéo se génère exactement comme aujourd’hui. "
            "Une sélection est figée au lancement (elle ne change plus ensuite) et peut être reprise dans le "
            "script et/ou les prompts d’images, selon ce que le fournisseur choisi prend réellement en "
            "charge. Jamais la voix. Le diagnostic de la production (page de suivi) montre précisément ce "
            "qui a été appliqué."
        )
        if characters:
            by_id = {character.id: character for character in characters}
            st.multiselect(
                "Personnages actifs de ce projet", character_ids,
                format_func=lambda cid: _character_label(by_id[cid]),
                key=keys["characters"],
            )
        if locations:
            by_loc_id = {location.id: location for location in locations}
            st.selectbox(
                "Lieu actif de ce projet", [None, *location_ids],
                format_func=lambda lid: "Aucun lieu" if lid is None else _location_label(by_loc_id[lid]),
                key=keys["location"],
            )


def example_for(project: Project) -> str:
    example = project.settings.get("example_request")
    return str(example) if example else _GENERIC_EXAMPLE


def _use_example(project: Project) -> None:
    keys = _keys(project)
    st.session_state[keys["request"]] = example_for(project)
    st.session_state[keys["error"]] = ""


def _provider_id(project: Project) -> str:
    return DEMO_PROVIDER if st.session_state.get(_keys(project)["demo"]) else DEFAULT_PROVIDER


def _draft_narrative(draft: Production) -> tuple[list[str], str | None]:
    narrative = (draft.snapshot or {}).get("narrative_context") or {}
    character_ids = [character["id"] for character in narrative.get("characters") or []]
    location = narrative.get("location")
    return character_ids, (location or {}).get("id")


def fresh_draft(service: ProductionService, project: Project) -> Production | None:
    """Le brouillon enregistré, seulement s'il correspond EXACTEMENT à ce qui est à l'écran et au projet."""
    keys = _keys(project)
    draft_id = st.session_state.get(keys["draft"])
    if not draft_id:
        return None
    try:
        draft = service.repo.get(draft_id)
    except LookupError:
        return None
    text = " ".join(str(st.session_state.get(keys["request"], "")).split())
    script = str(st.session_state.get(keys["script"], "")).strip()
    if draft.status.value != "EN_ATTENTE_CONFIRMATION" or draft.provider != _provider_id(project):
        return None
    current_request = build_request(project, text, script)
    draft_request = request_of(draft)
    # #70 : la voix du brouillon peut avoir été résolue vers celle d'un personnage sélectionné (jamais celle
    # du projet nu que build_request() vient de recalculer) — comparée séparément plus bas, via la sélection
    # de personnages/lieu elle-même (inchangée = voix toujours fraîche, la résolution étant une fonction pure
    # du snapshot déjà figé). L'ignorer ici évite de considérer à tort un brouillon frais comme périmé.
    if draft.subject != text or draft_request.with_updates(voice=current_request.voice) != current_request:
        return None
    if draft.parent_production_id != (st.session_state.get(keys["retry"]) or None):
        return None
    if not draft.snapshot or draft.snapshot.get("project", {}).get("id") != project.id:
        return None  # estimation sans instantané (ou d'un autre projet) : à préparer à nouveau
    character_ids, location_id = selected_narrative(project)
    draft_character_ids, draft_location_id = _draft_narrative(draft)
    if set(draft_character_ids) != set(character_ids) or draft_location_id != location_id:
        return None  # sélection de personnages/lieu changée depuis cette estimation : à refaire
    return draft


def _primary(project: Project, service: ProductionService) -> None:
    """Un seul bouton : prépare l'estimation, puis (brouillon à jour) confirme et lance."""
    keys = _keys(project)
    st.session_state[keys["error"]] = ""
    st.session_state.pop(keys["running"], None)
    text = str(st.session_state.get(keys["request"], ""))
    script = str(st.session_state.get(keys["script"], ""))
    character_ids, location_id = selected_narrative(project)
    try:
        draft = fresh_draft(service, project)
        if draft is None:
            prepared = service.prepare(project, text, provider_id=_provider_id(project), script=script,
                                       draft_id=st.session_state.get(keys["draft"]),
                                       retry_of=st.session_state.get(keys["retry"]) or None,
                                       character_ids=character_ids, location_id=location_id)
            st.session_state[keys["draft"]] = prepared.id
            return
        launched = service.confirm(draft.id, accept_partial=bool(st.session_state.get(keys["accept"])))
        st.session_state.pop(keys["draft"], None)
        st.session_state.pop(keys["retry"], None)
        nav.go(nav.VIEW_TRACK, project.id, launched.id)
    except AlreadyRunning as error:
        st.session_state[keys["error"]] = error.message
        st.session_state[keys["running"]] = error.production_id
    except LaunchError as error:
        st.session_state[keys["error"]] = error.message


def _rows(pairs: list[tuple[str, str]]) -> str:
    items = "".join(f"<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>" for label, value in pairs if value)
    return f'<dl class="kv">{items}</dl>' if items else '<p class="muted-note">Non renseigné</p>'


def brief_html(brief: dict) -> str:
    """Aperçu lisible du brief (toutes les valeurs sont échappées)."""
    request = (
        f'<p class="brief-request">{esc(brief["demande"])}</p>'
        if brief["demande"]
        else '<p class="brief-request brief-placeholder">Ta demande apparaîtra ici dès que tu l’auras saisie.</p>'
    )
    stats = "".join(
        f'<div class="stat"><span class="stat-value">{esc(value)}</span><span class="stat-label">{esc(label)}</span></div>'
        for label, value in (
            ("Durée cible", brief["duree_cible"]),
            ("Texte estimé", brief["reperes"]["mots"]),
            ("Visuels estimés", brief["reperes"]["scenes"]),
        )
    )
    steps = (
        '<ol class="timeline">' + "".join(f"<li>{esc(step)}</li>" for step in brief["structure"]) + "</ol>"
        if brief["structure"]
        else '<p class="muted-note">Structure libre</p>'
    )
    rules = (
        '<ul class="rules">'
        + "".join(f"<li>{esc(line)}</li>" for line in brief["consignes_permanentes"].splitlines() if line.strip())
        + "</ul>"
        if brief["consignes_permanentes"]
        else '<p class="muted-note">Aucune consigne permanente</p>'
    )
    voice = brief["voix"]
    voice_label = " · ".join(filter(None, [voice["fournisseur"], voice["nom"]]))
    return (
        f'<div class="brief-block brief-wide"><p class="card-eyebrow">Demande</p>{request}</div>'
        f'<div class="brief-block brief-wide"><div class="stats">{stats}</div>'
        '<p class="muted-note">Repères estimés à partir du rythme de narration et du rythme visuel du projet.</p></div>'
        f'<div class="brief-block"><p class="card-eyebrow">Public et ton</p>'
        f'{_rows([("Public", brief["public"]), ("Orientation", brief["orientation"]), ("Ton", brief["ton"])])}</div>'
        f'<div class="brief-block"><p class="card-eyebrow">Format</p>'
        f'{_rows([("Format", brief["format"]), ("Langue", brief["langue"]), ("Narration", brief["narration"])])}</div>'
        f'<div class="brief-block brief-wide"><p class="card-eyebrow">Visuels et voix</p>'
        f'{_rows([("Style", brief["visuels"]["style"]), ("Consignes visuelles", brief["visuels"]["regles"]), ("À ne jamais montrer", " ; ".join(brief["visuels"]["a_eviter"])), ("Rythme", brief["visuels"]["rythme"]), ("Voix", voice_label)])}</div>'
        f'<div class="brief-block brief-wide"><p class="card-eyebrow">Structure de la vidéo</p>{steps}</div>'
        f'<div class="brief-block brief-wide"><p class="card-eyebrow">Consignes permanentes</p>{rules}</div>'
    )


def _render_retry_notice(service: ProductionService, keys: dict[str, str], retry_id: str) -> None:
    try:
        failed = service.repo.get(retry_id)
    except LookupError:
        st.session_state.pop(keys["retry"], None)
        return
    kept = ("Le script déjà écrit est conservé (aucun nouvel appel texte) : modifie-le ou vide le champ pour en faire écrire un nouveau. "
            if failed.script else "")
    st.markdown(
        f'<div class="banner banner-info" role="status"><strong>Nouvelle tentative de {esc(failed.label)}.</strong> '
        f"La tentative échouée reste dans l’historique. Le sujet est conservé. {esc(kept)}"
        "Rien de la tentative précédente n’est repris côté coût ou confirmation : une nouvelle estimation et une nouvelle "
        "confirmation sont nécessaires.</div>",
        unsafe_allow_html=True,
    )


def render(project: Project, service: ProductionService, character_repo: CharacterRepository,
          location_repo: LocationRepository) -> None:
    keys = _keys(project)
    st.markdown(
        f'<section class="hero"><p class="eyebrow">{esc(project.name)}</p>'
        '<h1 class="hero-title">Nouvelle production</h1>'
        '<p class="hero-sub">Décris la vidéo que tu veux : les réglages du projet sont ajoutés automatiquement.</p></section>',
        unsafe_allow_html=True,
    )
    active = service.repo.active_for_project(project.id)
    for running in active:
        service.refresh(running.id)
    active = service.repo.active_for_project(project.id)

    retry_id = st.session_state.get(keys["retry"])
    if retry_id:
        _render_retry_notice(service, keys, retry_id)
    draft = fresh_draft(service, project)
    report: PreflightReport | None = service.preflight(draft) if draft else None
    partial_ok = bool(st.session_state.get(keys["accept"])) or not (draft and draft.cost_partial and draft.provider != DEMO_PROVIDER)
    blocked = bool(active) or (draft is not None and (not report.ready or not partial_ok))

    with st.container(key="composer"):
        st.markdown('<h2 class="composer-title">Que veux-tu créer ?</h2>', unsafe_allow_html=True)
        st.text_area(
            "Que veux-tu créer ?",
            key=keys["request"],
            placeholder="Décris la vidéo que tu veux…",
            height=132,
            max_chars=2000,
            label_visibility="collapsed",
        )
        if st.session_state.get(keys["error"]):
            st.markdown(
                f'<p class="field-error" role="alert">{esc(st.session_state[keys["error"]])}</p>',
                unsafe_allow_html=True,
            )
        with st.expander("J’ai déjà mon script (optionnel)", expanded=bool(st.session_state.get(keys["script"]))):
            st.text_area("Script à utiliser", key=keys["script"], height=150, max_chars=8000,
                         placeholder="Colle ici ton texte : aucun script ne sera écrit par un fournisseur.")
        _render_narrative_section(project, character_repo, location_repo)
        st.toggle("Mode démonstration : simuler la génération, sans aucun fournisseur", key=keys["demo"])
        with st.container(horizontal=True, vertical_alignment="center", key="composer_actions"):
            st.markdown(
                f'<p class="example"><span>Exemple</span> {esc(example_for(project))}</p>',
                unsafe_allow_html=True,
            )
            st.button("Utiliser l’exemple", type="tertiary", key="use_example",
                      on_click=_use_example, args=(project,))
            st.button(LABEL_PREPARE if draft is None else LABEL_CONFIRM, type="primary",
                      icon=":material/auto_awesome:" if draft is None else ":material/rocket_launch:",
                      key="generate", on_click=_primary, args=(project, service), disabled=blocked)

    for running in active:
        st.markdown(
            f'<div class="banner banner-info" role="status">Une génération est déjà en cours pour ce projet '
            f"({esc(running.label)}). Attends sa fin avant d’en lancer une autre.</div>",
            unsafe_allow_html=True,
        )
        st.button("Voir le suivi", key=f"see_running_{running.id}", icon=":material/monitoring:",
                  on_click=nav.go, args=(nav.VIEW_TRACK, project.id, running.id))
    running_id = st.session_state.get(keys["running"])
    if running_id and not active:
        st.session_state.pop(keys["running"], None)

    if draft is not None:
        view_estimate.render_panel(draft, report, demo=draft.provider == DEMO_PROVIDER, key=project.id, project=project)

    # Aperçu vivant : la demande saisie + les paramètres enregistrés du projet.
    live_request = str(st.session_state.get(keys["request"], ""))
    brief = brief_lib.build_brief(project, live_request)
    with st.container(key="brief_preview"):
        with st.container(horizontal=True, vertical_alignment="center", key="brief_head"):
            st.markdown(
                '<div><p class="card-eyebrow">Réglages du projet</p>'
                '<h2 class="brief-title">Brief final</h2></div>',
                unsafe_allow_html=True,
            )
            st.button("Ajuster les paramètres", icon=":material/tune:", key="adjust_settings",
                      on_click=nav.go, args=(nav.VIEW_SETTINGS, project.id))
        with st.expander("Voir le brief transmis à la génération", expanded=draft is None):
            st.markdown(f'<div class="brief-grid">{brief_html(brief)}</div>', unsafe_allow_html=True)
        with st.expander("Version texte à copier"):
            st.code(brief_lib.brief_to_text(brief), language=None)

    with st.container(horizontal=True, key="production_nav"):
        st.button("Retour au projet", icon=":material/arrow_back:", type="tertiary", key="back_project",
                  on_click=nav.go, args=(nav.VIEW_PROJECT, project.id))
