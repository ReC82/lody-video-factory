"""Suivi d'une production et résultat (vidéo, script, storyboard, paramètres, coût estimé)."""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st

from lody import catalog, nav
from lody.components import format_datetime
from lody.generation import costing
from lody.generation.models import ACTIVE_STATUSES, STATUS_LABELS, ProductionStatus as S
from lody.generation.runtime import DEMO_PROVIDER
from lody.generation.service import ProductionService, request_of
from lody.generation.safety import redact
from lody.generation.store import Production
from lody.projects import Project
from lody.theme import esc

REFRESH_SECONDS = 4

_STATUS_CLASS = {
    S.BROUILLON: "muted", S.EN_ATTENTE_CONFIRMATION: "muted", S.CONFIRMEE: "live", S.EN_FILE: "live",
    S.EN_COURS: "live", S.TERMINEE: "ok", S.ECHEC: "fail", S.ANNULEE: "muted",
}
_STATUS_MESSAGE = {
    S.CONFIRMEE: "Ta confirmation est enregistrée : la production démarre.",
    S.EN_FILE: "La demande est envoyée au moteur, qui va la prendre en charge.",
    S.EN_COURS: "La vidéo est en cours de fabrication. Tu peux quitter cette page : la production continue et sera retrouvée ici.",
    S.TERMINEE: "La vidéo est prête.",
    S.ECHEC: "La génération s’est arrêtée. Le script et le storyboard déjà produits sont conservés.",
    S.EN_ATTENTE_CONFIRMATION: "En attente de ta confirmation.",
    S.BROUILLON: "Brouillon : rien n’a été lancé.",
}


def status_pill(production: Production) -> str:
    return (f'<span class="run-status run-{_STATUS_CLASS[production.status]}">'
            f"{esc(STATUS_LABELS[production.status])}</span>")


def _parse(value: str | None) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment and moment.tzinfo is None else moment


def elapsed_text(production: Production, now: datetime | None = None) -> str:
    start = _parse(production.started_at) or _parse(production.confirmed_at)
    if not start:
        return "—"
    end = _parse(production.finished_at) or now or datetime.now(timezone.utc)
    seconds = max(int((end - start).total_seconds()), 0)
    minutes, secs = divmod(seconds, 60)
    return f"{minutes} min {secs:02d} s" if minutes else f"{secs} s"


def _progress_html(production: Production) -> str:
    """Barre uniquement si le moteur fournit une vraie valeur ; sinon, l'étape seule."""
    value = production.progress
    if production.status not in ACTIVE_STATUSES or value is None or value < 5:
        return ""
    return (
        f'<div class="run-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{value}" '
        f'aria-label="Avancement du moteur"><span style="width:{value}%"></span></div>'
        f'<p class="muted-note">Avancement du moteur : {value} %. Ce sont les jalons du moteur, pas une estimation du temps restant.</p>'
    )


def status_card_html(production: Production) -> str:
    step = production.current_step or STATUS_LABELS[production.status]
    step_html = "" if step == STATUS_LABELS[production.status] else f'<p class="run-step">{esc(step)}</p>'
    demo = '<span class="badge badge-accent">Démonstration</span>' if production.provider == DEMO_PROVIDER else ""
    warning = ""
    if production.status in ACTIVE_STATUSES and production.error_message:
        warning = (f'<div class="banner banner-info" role="status">Dernière vérification impossible : '
                   f"{esc(production.error_message)} Nouvelle tentative automatique.</div>")
    return (
        f'<div class="run-head">{status_pill(production)}{demo}'
        f'<span class="run-elapsed">Temps écoulé : {esc(elapsed_text(production))}</span></div>'
        f"{step_html}"
        f'<p class="run-message">{esc(_STATUS_MESSAGE.get(production.status, ""))}</p>'
        f"{_progress_html(production)}{warning}"
    )


def _kv(pairs: list[tuple[str, str]]) -> str:
    items = "".join(f"<div><dt>{esc(a)}</dt><dd>{esc(b) or '—'}</dd></div>" for a, b in pairs)
    return f'<dl class="kv">{items}</dl>'


def parameters_html(production: Production) -> str:
    request = request_of(production)
    voice = " · ".join(filter(None, [catalog.label(catalog.VOICE_PROVIDERS, request.voice.provider), request.voice.name]))
    return _kv([
        ("Langue", catalog.label(catalog.LANGUAGES, request.language)),
        ("Format", catalog.label(catalog.FORMATS, request.aspect)),
        ("Durée cible", f"{request.duration_min} à {request.duration_max} secondes"),
        ("Ton", request.tone),
        ("Voix", voice),
        ("Modèle de voix", request.voice.model),
        ("Sous-titres", "Phrase entière"),
        ("Script", "écrit par le moteur" if production.script_source == "generated" else (
            "fourni ou modifié par toi" if production.script_source == "manual" else "repris de la version précédente")),
        ("Images", catalog.label(catalog.VISUAL_PROVIDERS, request.visual_provider)),
        ("Musique", catalog.label(catalog.MUSIC_PROVIDERS, request.music_provider)),
    ])


def cost_text(production: Production) -> str:
    if production.cost_high is None:
        return "tarif non configuré"
    text = costing.format_range(production.cost_low, production.cost_high, production.cost_currency)
    return text + (" (partiel)" if production.cost_partial else "")


def storyboard_html(production: Production) -> str:
    if not production.storyboard:
        return '<p class="muted-note">Aucun storyboard enregistré.</p>'
    items = "".join(
        f'<li class="scene"><span class="scene-n">{int(scene["index"])}</span><div>'
        f'<p class="scene-narration">{esc(scene["narration"])}</p>'
        f'<p class="scene-prompt"><span>Prompt visuel</span> {esc(scene["prompt"])}</p></div></li>'
        for scene in production.storyboard
    )
    return f'<ol class="scenes">{items}</ol>'


def _go_v2(service: ProductionService, project: Project, production: Production) -> None:
    try:
        draft = service.create_v2(production.id)
    except Exception as error:
        nav.flash("error", getattr(error, "message", "Impossible de créer la V2 pour l’instant."))
        return
    nav.go(nav.VIEW_V2, project.id, draft.id)


def _retry(project: Project, production: Production) -> None:
    """Prépare une NOUVELLE tentative : sujet et éléments valides repris, jamais la confirmation ni le coût."""
    keys = {name: f"{name}_{project.id}" for name in ("request", "script", "demo", "draft", "retry", "request_error")}
    st.session_state[keys["request"]] = production.subject
    st.session_state[keys["script"]] = production.script
    st.session_state[keys["demo"]] = production.provider == DEMO_PROVIDER
    st.session_state[keys["retry"]] = production.id
    st.session_state.pop(keys["draft"], None)
    st.session_state[keys["request_error"]] = ""
    nav.go(nav.VIEW_PRODUCTION, project.id)


def attempt_note(service: ProductionService, production: Production) -> str:
    """« nouvelle tentative de V1 » quand la production suit directement une tentative échouée."""
    if production.parent_production_id:
        try:
            parent = service.repo.get(production.parent_production_id)
        except LookupError:
            return ""
        if parent.status is S.ECHEC:
            return f"nouvelle tentative de {parent.label}"
    return ""


def _versions(service: ProductionService, project: Project, production: Production) -> None:
    chain = service.repo.chain(production.root_production_id)
    if len(chain) < 2:
        return
    with st.container(horizontal=True, key="versions_bar"):
        st.markdown('<span class="muted-note">Versions</span>', unsafe_allow_html=True)
        for item in chain:
            st.button(item.label, key=f"version_{item.id}", disabled=item.id == production.id,
                      type="primary" if item.id == production.id else "secondary",
                      on_click=nav.go, args=(nav.VIEW_TRACK, project.id, item.id))


def _render_result(service: ProductionService, project: Project, production: Production) -> None:
    try:
        video_path = service.resolve_video(production)
    except ValueError:
        st.markdown(
            '<div class="banner banner-error" role="alert"><strong>Vidéo introuvable.</strong> Le fichier de cette '
            "production n’est plus disponible ou n’est pas autorisé. Le script et le storyboard restent consultables.</div>",
            unsafe_allow_html=True,
        )
        video_path = None
    with st.container(key="result"):
        repaired = _asset(production, "repaired_video") is not None
        st.markdown('<p class="card-eyebrow">Résultat</p><h2 class="brief-title">'
                    + ("Rendu original (conservé)" if repaired else "Ta vidéo est prête") + "</h2>", unsafe_allow_html=True)
        if video_path is not None:
            with st.container(key="player"):
                st.video(str(video_path), format="video/mp4")
            st.download_button("Télécharger la vidéo", data=lambda: video_path.read_bytes(),
                               file_name=f"{project.name}-{production.label}.mp4".replace(" ", "-"), mime="video/mp4",
                               icon=":material/download:", key=f"download_{production.id}", type="primary")
        duration = f"{production.video_duration:.0f} secondes" if production.video_duration else "non communiquée"
        st.markdown(_kv([
            ("Durée finale", duration),
            ("Générée le", format_datetime(production.finished_at or production.updated_at)),
            ("Version", f"{production.label}" + (" (démonstration)" if production.provider == DEMO_PROVIDER else "")),
            ("Coût estimé", cost_text(production)),
            ("Coût réel", "non mesurable : le moteur ne fournit pas le coût effectivement facturé"),
        ]), unsafe_allow_html=True)
        for warning in production.warnings:
            st.markdown(f'<div class="banner banner-info" role="status">Avertissement : {esc(warning)}</div>',
                        unsafe_allow_html=True)
        with st.container(horizontal=True, key="result_actions"):
            st.button("Créer une V2", type="primary", icon=":material/edit_note:", key="create_v2",
                      on_click=_go_v2, args=(service, project, production))
            st.button("Retour au projet", icon=":material/arrow_back:", type="tertiary", key="back_project_result",
                      on_click=nav.go, args=(nav.VIEW_PROJECT, project.id))
        st.markdown('<p class="muted-note">Une V2 conserve cette version intacte et lance de nouveaux appels payants.</p>',
                    unsafe_allow_html=True)


def _asset(production: Production, kind: str) -> dict | None:
    return next((asset for asset in production.assets if asset.get("kind") == kind), None)


def _render_repair(service: ProductionService, project: Project, production: Production) -> None:
    """Rendu corrigé (réparation technique des sous-titres) : présenté en premier, l'original reste disponible."""
    repaired = _asset(production, "repaired_video")
    if not repaired:
        return
    try:
        repaired_path = service.resolve_asset(production, repaired["ref"])
    except ValueError:
        return
    with st.container(key="repair"):
        st.markdown(
            '<p class="card-eyebrow">Réparation technique</p><h2 class="brief-title">Rendu corrigé</h2>'
            '<div class="banner banner-info" role="note">Les sous-titres ont été refaits à partir des médias déjà générés '
            "(voix, images, musique) : <strong>aucune nouvelle génération, aucun fournisseur appelé, rien de facturé</strong>. "
            "Seul l’espacement des apostrophes a changé ; le rendu original est conservé ci-dessous.</div>",
            unsafe_allow_html=True,
        )
        with st.container(key="player_repaired"):
            st.video(str(repaired_path), format="video/mp4")
        st.download_button("Télécharger le rendu corrigé", data=lambda: repaired_path.read_bytes(),
                           file_name=f"{project.name}-{production.label}-corrige.mp4".replace(" ", "-"),
                           mime="video/mp4", icon=":material/download:", key=f"download_repaired_{production.id}")


def _render_details(production: Production) -> None:
    with st.expander("Script utilisé", expanded=production.status is S.TERMINEE):
        st.markdown(f'<p class="script-text">{esc(production.script) or "Aucun script pour l’instant."}</p>',
                    unsafe_allow_html=True)
    with st.expander("Storyboard et prompts visuels"):
        st.markdown(storyboard_html(production), unsafe_allow_html=True)
    with st.expander("Paramètres principaux"):
        st.markdown(parameters_html(production), unsafe_allow_html=True)
    with st.expander("Brief utilisé"):
        st.markdown(_kv([("Demande", production.brief.get("demande", production.subject)),
                         ("Public", production.brief.get("public", "")),
                         ("Ton", production.brief.get("ton", ""))]), unsafe_allow_html=True)


def _table(pairs: list[tuple[str, object]]) -> str:
    return _kv([(label, redact(value)) for label, value in pairs])


def _render_diagnostic(production: Production) -> None:
    """Traçabilité complète d'une production : d'où vient chaque prompt. Aucune clé n'est jamais affichée."""
    snapshot, trace = production.snapshot, production.trace
    with st.expander("Diagnostic administrateur — traçabilité de la production"):
        if not snapshot:
            st.markdown('<div class="banner banner-info" role="note">Production antérieure à la traçabilité : aucun instantané ni '
                        "prompt final n’a été enregistré. Le storyboard ci-dessous est ce que Lody a envoyé ; le moteur a pu y ajouter "
                        "son propre gabarit d’images (non enregistré à l’époque).</div>", unsafe_allow_html=True)
        project = snapshot.get("project", {})
        st.markdown('<p class="card-eyebrow">Projet source et instantané</p>' + _table([
            ("Projet", f"{project.get('name', '—')} ({project.get('id', production.project_id)})"),
            ("Production", f"{production.id} · {production.label} · {production.provider}"),
            ("Version des paramètres", snapshot.get("version", "—")),
            ("Instantané créé le", format_datetime(snapshot["captured_at"]) if snapshot.get("captured_at") else "—"),
            ("Hérité de", snapshot.get("inherited_from") or "— (paramètres du projet à la préparation)"),
            ("Paramètres du projet modifiés le", format_datetime(project["updated_at"]) if project.get("updated_at") else "—"),
        ]), unsafe_allow_html=True)
        brief = production.brief or {}
        st.markdown('<p class="card-eyebrow">Brief utilisé</p>' + _table([
            ("Demande", brief.get("demande", production.subject)), ("Public", brief.get("public", "")),
            ("Orientation", brief.get("orientation", "")), ("Ton", brief.get("ton", "")),
            ("Style visuel", (brief.get("visuels") or {}).get("style", "")),
            ("Consignes visuelles", (brief.get("visuels") or {}).get("regles", "")),
            ("À ne jamais montrer", " ; ".join((brief.get("visuels") or {}).get("a_eviter", []))),
            ("Structure", " → ".join(brief.get("structure", []))),
        ]), unsafe_allow_html=True)
        script_request = trace.get("script_request")
        st.markdown('<p class="card-eyebrow">Prompts du script</p>', unsafe_allow_html=True)
        if script_request:
            st.markdown("**Prompt éditorial envoyé** (langue : " + esc(script_request.get("video_language", "")) + ")")
            st.code(redact(script_request.get("video_script_prompt", "")), language=None)
            st.markdown("**Prompt système final** : " + esc(redact(script_request.get("custom_system_prompt", "défaut du moteur"))))
        else:
            st.markdown('<p class="muted-note">Script fourni ou repris : aucun prompt de script envoyé.</p>', unsafe_allow_html=True)
        template = trace.get("image_template", {})
        if template.get("applied"):
            st.markdown('<div class="banner banner-error" role="alert"><strong>Gabarit d’images global du moteur appliqué.</strong> '
                        "Il est ajouté par le moteur à chaque image de chaque projet (origine : "
                        + esc(template.get("origin", "")) + ").</div>", unsafe_allow_html=True)
            st.code(redact(template.get("text", "")), language=None)
        scenes = trace.get("scenes") or [{"index": scene["index"], "prompt_sent": scene["prompt"], "final_prompt": "",
                                          "engine_template_applied": False} for scene in production.storyboard]
        st.markdown('<p class="card-eyebrow">Storyboard : prompt exact de chaque scène</p>', unsafe_allow_html=True)
        narrations = {scene["index"]: scene["narration"] for scene in production.storyboard}
        for scene in scenes:
            st.markdown(f"**Scène {int(scene['index'])}** — {esc(redact(narrations.get(scene['index'], '')))}")
            st.code(redact(scene["prompt_sent"]), language=None)
            if scene.get("engine_template_applied"):
                st.markdown("_Prompt final réellement envoyé au modèle d’images (après ajout du gabarit du moteur) :_")
                st.code(redact(scene["final_prompt"]), language=None)
        if trace.get("engine_params"):
            st.markdown('<p class="card-eyebrow">Paramètres transmis au moteur</p>' + _table(
                [(str(key), value) for key, value in trace["engine_params"].items()]), unsafe_allow_html=True)
        if trace.get("origins"):
            st.markdown('<p class="card-eyebrow">Origine de chaque valeur</p>' + _table(
                [(item["item"], item["origin"]) for item in trace["origins"]]), unsafe_allow_html=True)
        st.markdown('<p class="muted-note">Aucune clé ni secret n’est jamais affiché ou enregistré ici.</p>', unsafe_allow_html=True)


def render(service: ProductionService, project: Project, production_id: str) -> None:
    try:
        production = service.repo.get(production_id)
        if production.project_id != project.id:
            raise LookupError(production_id)
    except LookupError:
        nav.flash("error", "Cette production est introuvable.")
        nav.go(nav.VIEW_PROJECT, project.id)
        st.rerun()
        return

    st.markdown(
        f'<section class="hero"><p class="eyebrow">{esc(project.name)} · {esc(production.label)}'
        f'{" · " + esc(attempt_note(service, production)) if attempt_note(service, production) else ""}</p>'
        f'<h1 class="hero-title">Suivi de la production</h1>'
        f'<p class="hero-sub">{esc(production.subject)}</p></section>',
        unsafe_allow_html=True,
    )
    _versions(service, project, production)

    @st.fragment(run_every=REFRESH_SECONDS if production.is_active else None)
    def live() -> None:
        current = service.refresh(production_id)
        with st.container(key="run_card"):
            st.markdown(status_card_html(current), unsafe_allow_html=True)
            if current.status is S.ECHEC:
                st.markdown(f'<div class="banner banner-error" role="alert">{esc(current.error_message)}</div>',
                            unsafe_allow_html=True)
            # Ces boutons changent de page : dans un fragment, un on_click ne relancerait que le fragment.
            # On agit donc dans le corps du fragment puis on relance TOUTE l'application (st.rerun).
            with st.container(horizontal=True, key="run_actions"):
                if current.is_active:
                    st.button("Actualiser", icon=":material/refresh:", key="refresh_run")
                if current.status is S.ECHEC:
                    if st.button("Préparer à nouveau", type="primary", icon=":material/replay:", key="retry_run"):
                        _retry(project, current)
                        st.rerun()
                    if current.script and st.button("Créer une V2 avec ce script", icon=":material/edit_note:",
                                                    key="v2_from_failed"):
                        _go_v2(service, project, current)
                        st.rerun()
                if current.status is not S.TERMINEE:  # le résultat a ses propres actions
                    if st.button("Retour au projet", icon=":material/arrow_back:", type="tertiary", key="back_project_run"):
                        nav.go(nav.VIEW_PROJECT, project.id)
                        st.rerun()
        if production.is_active and not current.is_active:
            st.rerun()  # la production vient de se terminer : recharger toute la page pour afficher le résultat

    live()
    latest = service.repo.get(production_id)
    if latest.status is S.TERMINEE:
        _render_repair(service, project, latest)
        _render_result(service, project, latest)
    if latest.script or latest.storyboard:
        _render_details(latest)
    _render_diagnostic(latest)
