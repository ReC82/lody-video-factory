"""Écran « Nouvelle version » d'une vidéo animée (#159) : modifier une scène sans JSON ni re-rendu complet.

Parcours : liste des scènes (miniature, temps, dialogue, personnage, schémas) → « Modifier cette scène »
(expression, geste, texte dit, texte affiché, version d'asset, cadrage, durée) → plan de réédition AVANT
dépense → « Aperçu rapide » ½ résolution (gratuit, sans créer de version) → « Générer la V2 ». La version
précédente reste intacte et consultable ; dans le suivi, la comparaison V1/V2 et le contrôle qualité.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from lody import nav
from lody.animation import scene_edit
from lody.animation.assets import AssetNotFound
from lody.generation import reedit_v2
from lody.generation.models import ProductionStatus as S
from lody.generation.service import AlreadyRunning, LaunchError, ProductionService
from lody.generation.store import Production
from lody.projects import Project
from lody.theme import esc

LEVEL_LABELS = {"error": "Erreur", "warning": "Avertissement", "info": "Info"}
VOICE_LABELS = {"regenerate": "réplique modifiée", "new": "nouvelle réplique", "removed": "réplique retirée"}


def _seconds(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + " s"


def _keys(production: Production) -> dict[str, str]:
    return {name: f"reedit_{name}_{production.id}" for name in ("editing", "error", "consent", "notice")}


# -- données ---------------------------------------------------------------------------------------------------
def _declared(service: ProductionService, production: Production, asset: scene_edit.AssetUse) -> dict[str, Any]:
    """Expressions, gestes et versions disponibles pour un asset (catalogue du projet), sans jamais échouer."""
    connector = reedit_v2.connector_of(service, production)
    empty = {"expressions": [], "gestures": [], "versions": [asset.version]}
    if connector is None:
        return empty
    try:
        repo = connector.assets()
        manifest = repo.get_version(production.project_id, asset.asset_id, asset.version).manifest
        versions = [item.version for item in repo.list_versions(production.project_id, asset.asset_id)]
    except (AssetNotFound, RuntimeError, ValueError):
        return empty
    return {"expressions": list(manifest.get("expressions") or []), "gestures": list(manifest.get("gestures") or []),
            "versions": sorted(set(versions) | {asset.version})}


def _with_current(options: list[str], current: str) -> list[str]:
    return options if current in options else [current, *options]


# -- actions ---------------------------------------------------------------------------------------------------
def _start_editing(production: Production, scene_id: str) -> None:
    keys = _keys(production)
    st.session_state[keys["editing"]] = scene_id
    st.session_state[keys["error"]] = ""


def _stop_editing(production: Production) -> None:
    st.session_state.pop(_keys(production)["editing"], None)


def _save(service: ProductionService, production: Production, scene_id: str, changes: dict[str, Any]) -> bool:
    keys = _keys(production)
    st.session_state[keys["error"]] = ""
    if not scene_edit.has_changes(changes):
        st.session_state[keys["error"]] = "Aucune modification à enregistrer dans cette scène."
        return False
    try:
        current = service.repo.get(production.id)  # toujours l'état enregistré, jamais celui du rendu précédent
        document = scene_edit.edit_scene(current.animation, scene_id, changes)
        service.prepare_animation_v2(production.id, document)
    except scene_edit.SceneEditError as error:
        st.session_state[keys["error"]] = str(error)
        return False
    except LaunchError as error:
        st.session_state[keys["error"]] = error.message
        return False
    st.session_state[keys["notice"]] = "Scène enregistrée : le plan de réédition ci-dessous est à jour."
    st.session_state.pop(keys["consent"], None)  # un nouveau plan demande un nouvel accord
    return True


def _start_preview(service: ProductionService, production: Production) -> None:
    keys = _keys(production)
    st.session_state[keys["error"]] = ""
    try:
        reedit_v2.start_preview(service, service.repo.get(production.id))
    except LaunchError as error:
        st.session_state[keys["error"]] = error.message


def _generate(service: ProductionService, project: Project, production: Production) -> None:
    keys = _keys(production)
    st.session_state[keys["error"]] = ""
    try:
        launched = service.confirm(production.id, accept_partial=True)  # rendu 2D local : coût fournisseur nul
        nav.go(nav.VIEW_TRACK, project.id, launched.id)
    except (AlreadyRunning, LaunchError) as error:
        st.session_state[keys["error"]] = error.message


# -- rendu -----------------------------------------------------------------------------------------------------
def _scene_card(service: ProductionService, production: Production, summary: scene_edit.SceneSummary,
                thumbs: dict[str, Any], plan_entry: dict[str, Any] | None) -> None:
    with st.container(key=f"scene_{summary.id}", border=True):
        left, right = st.columns([1, 4], vertical_alignment="top")
        with left:
            if summary.id in thumbs:
                st.image(str(thumbs[summary.id]), width=110)
            else:
                st.markdown('<p class="muted-note">Pas encore d’image</p>', unsafe_allow_html=True)
        with right:
            badge = ""
            if plan_entry is not None:
                badge = (' · <strong>à recalculer</strong> (' + esc(plan_entry["reason"]) + ")"
                         if plan_entry["action"] == "render" else " · réutilisée telle quelle")
            st.markdown(
                f'<p class="card-eyebrow">Scène {summary.index} · {_seconds(summary.start_seconds)} → '
                f'{_seconds(summary.end_seconds)} ({_seconds(summary.seconds)}){badge}</p>'
                + (f'<p class="muted-note">{esc(summary.purpose)}</p>' if summary.purpose else ""),
                unsafe_allow_html=True)
            lines = []
            for item in summary.characters:
                gestures = ", ".join(name for _, _, name in item.gestures) or "aucun"
                expressions = ", ".join(name for _, name in item.expressions) or "neutre"
                lines.append(f"<li><strong>{esc(item.name)}</strong> — gestes : {esc(gestures)} ; "
                             f"expression : {esc(expressions)}</li>")
            for text, start, end in summary.cues:
                who = esc(summary.speaker) + " : " if summary.speaker else ""
                lines.append(f"<li>🗨 {who}« {esc(text)} » <span class=\"muted-note\">({_seconds(start)}–"
                             f"{_seconds(end)})</span></li>")
            for _, content, _style in summary.texts:
                lines.append(f"<li>Texte affiché : « {esc(content)} »</li>")
            schemas = [f"{esc(item.name)} (v{item.version})" for item in summary.schemas]
            if schemas:
                lines.append("<li>Schémas et décors : " + ", ".join(schemas) + "</li>")
            st.markdown(f'<ul class="scene-lines">{"".join(lines)}</ul>', unsafe_allow_html=True)
            st.button("Modifier cette scène", icon=":material/edit:", key=f"edit_scene_{summary.id}",
                      on_click=_start_editing, args=(production, summary.id))


def _forget_widgets(production: Production, scene_id: str) -> None:
    """Oublie les saisies du formulaire d'une scène : rouvert, il repart des valeurs enregistrées."""
    suffix = f"_{production.id}_{scene_id}"
    for key in [k for k in st.session_state if suffix in str(k) and not str(k).startswith("reedit_")]:
        del st.session_state[key]


def _cancel(production: Production, scene_id: str) -> None:
    _forget_widgets(production, scene_id)
    _stop_editing(production)


def _submit(service: ProductionService, production: Production, scene_id: str, widgets: list[tuple[str, Any, str, Any]],
            framing: tuple[str, str], seconds: tuple[str, float]) -> None:
    """Rappel du bouton « Enregistrer la scène » : lit le formulaire, enregistre seulement ce qui a changé."""
    picked: dict[str, Any] = {"expressions": {}, "gestures": {}, "cues": {}, "texts": {}, "versions": {}}
    for category, change_key, widget_key, before in widgets:
        value = st.session_state.get(widget_key, before)
        if value != before:
            picked[category][change_key] = value
    framing_key, framing_before = framing
    if st.session_state.get(framing_key, framing_before) != framing_before:
        picked["framing"] = st.session_state[framing_key]
    seconds_key, seconds_before = seconds
    if abs(float(st.session_state.get(seconds_key, seconds_before)) - seconds_before) >= 0.05:
        picked["seconds"] = float(st.session_state[seconds_key])
    if _save(service, production, scene_id, picked):
        _forget_widgets(production, scene_id)
        _stop_editing(production)


def _editor(service: ProductionService, production: Production, summary: scene_edit.SceneSummary) -> None:
    keys = _keys(production)
    prefix = f"{production.id}_{summary.id}"
    fps = production.animation["canvas"]["fps"]
    widgets: list[tuple[str, Any, str, Any]] = []  # (catégorie, clé du changement, clé du widget, valeur actuelle)
    with st.form(key=f"scene_form_{prefix}", border=True):
        st.markdown(f'<h3 class="composer-title">Modifier la scène {summary.index}</h3>'
                    '<p class="muted-note">Seule cette scène sera recalculée. Les choix proposés sont ceux que '
                    'le personnage ou le schéma sait faire (déclarés dans le catalogue).</p>', unsafe_allow_html=True)
        for number, item in enumerate(summary.assets):
            declared = _declared(service, production, item)
            st.markdown(f"**{esc(item.name)}** · {scene_edit.KIND_LABELS.get(item.kind, item.kind)}")
            for position, (frame, name) in enumerate(item.expressions):
                options = _with_current(declared["expressions"], name)
                key = f"expr_{prefix}_{number}_{position}"
                st.selectbox(f"Expression à {_seconds(frame / fps)}", options, index=options.index(name), key=key,
                             help="Visage du personnage à partir de cet instant de la scène.")
                widgets.append(("expressions", (item.key, position), key, name))
            for position, (start, end, name) in enumerate(item.gestures):
                options = _with_current(declared["gestures"], name)
                key = f"gesture_{prefix}_{number}_{position}"
                st.selectbox(f"Geste de {_seconds(start / fps)} à {_seconds(end / fps)}", options,
                             index=options.index(name), key=key,
                             help="Mouvement du personnage pendant ce moment ; le moment lui-même ne change pas.")
                widgets.append(("gestures", (item.key, position), key, name))
            if len(declared["versions"]) > 1:
                key = f"version_{prefix}_{number}"
                st.selectbox(f"Version de « {item.name} »", declared["versions"],
                             index=declared["versions"].index(item.version), format_func=lambda value: f"v{value}",
                             key=key, help="Les versions du catalogue du projet. La version précédente de la vidéo "
                                           "garde la sienne.")
                widgets.append(("versions", item.key, key, item.version))
        for position, (text, start, end) in enumerate(summary.cues):
            key = f"cue_{prefix}_{position}"
            st.text_input(f"Texte dit {position + 1} ({_seconds(start)}–{_seconds(end)})", value=text, max_chars=60,
                          key=key, help="Ce que dit le personnage ; c'est aussi le sous-titre affiché.")
            widgets.append(("cues", position, key, text))
        for number, (object_key, content, _style) in enumerate(summary.texts):
            key = f"text_{prefix}_{number}"
            st.text_input(f"Texte affiché « {content[:30]} »", value=content, max_chars=200, key=key,
                          help="Titre ou étiquette écrite à l'image (pas lue par la voix).")
            widgets.append(("texts", object_key, key, content))
        current_framing = scene_edit.framing_of(summary.zoom)
        st.radio("Cadrage", list(scene_edit.FRAMINGS), index=list(scene_edit.FRAMINGS).index(current_framing),
                 format_func=lambda key: scene_edit.FRAMINGS[key][0], horizontal=True, key=f"framing_{prefix}",
                 help="Distance de la caméra au début de la scène ; un mouvement de caméra existant est conservé.")
        st.number_input("Durée de la scène (secondes)", min_value=scene_edit.MIN_SCENE_SECONDS, max_value=120.0,
                        value=float(summary.seconds), step=0.5, format="%.1f", key=f"seconds_{prefix}",
                        help="Toute l'animation de la scène (gestes, répliques) est étirée ou resserrée.")
        with st.container(horizontal=True):
            # Rappels (on_click) : l'enregistrement a lieu AVANT le rendu suivant, qui affiche déjà le nouveau plan.
            st.form_submit_button("Enregistrer la scène", type="primary", icon=":material/save:", on_click=_submit,
                                  args=(service, production, summary.id, widgets,
                                        (f"framing_{prefix}", current_framing), (f"seconds_{prefix}", summary.seconds)))
            st.form_submit_button("Annuler", type="tertiary", on_click=_cancel, args=(production, summary.id))
    if st.session_state.get(keys["error"]):
        st.markdown(f'<p class="field-error" role="alert">{esc(st.session_state[keys["error"]])}</p>',
                    unsafe_allow_html=True)


def _plan_panel(plan: dict[str, Any], cost: str, needs_consent: bool, production: Production) -> None:
    summary = plan["summary"]
    st.markdown('<h2 class="composer-title">Plan de réédition — avant toute dépense</h2>', unsafe_allow_html=True)
    cols = st.columns(4)
    cols[0].metric("Scènes réutilisées", summary["scenes_reused"], help="Reprises octet pour octet depuis la version précédente.")
    cols[1].metric("Scènes à recalculer", summary["scenes_to_render"])
    cols[2].metric("Rendu estimé", f"≈ {summary['render_seconds']} s",
                   help="Environ 7 fois la durée des scènes à recalculer (mesure #118).")
    cols[3].metric("Temps évité", f"≈ {summary['render_seconds_saved']} s",
                   help="Mesuré lors du rendu de la version précédente." if summary["render_seconds_saved_measured"]
                   else "Estimé (la version précédente n'a pas de mesure).")
    rows = []
    for entry in plan["scenes"]:
        voice = VOICE_LABELS.get(entry.get("voice", ""), "")
        rows.append(f"<li>{'↻' if entry['action'] == 'render' else '✓'} Scène {entry['index']} — "
                    f"{'à recalculer' if entry['action'] == 'render' else 'réutilisée'} : {esc(entry['reason'])}"
                    + (f" ; {voice}" if voice else "") + "</li>")
    for entry in plan["removed"]:
        rows.append(f"<li>− Ancienne scène {entry['index']} retirée</li>")
    st.markdown(f'<ul class="scene-lines">{"".join(rows)}</ul>', unsafe_allow_html=True)
    st.markdown(f'<div class="banner banner-info" role="note">{esc(cost)}</div>', unsafe_allow_html=True)
    if needs_consent:
        st.checkbox("J’accepte que la voix des répliques modifiées soit régénérée (coût de synthèse vocale)",
                    key=_keys(production)["consent"])
    problems = [item for item in plan["qa"] if item["level"] in ("warning", "error")]
    if problems:
        with st.expander(f"Contrôle qualité du storyboard ({len(problems)} point(s) à vérifier)"):
            for item in problems:
                st.markdown(f"- **{LEVEL_LABELS.get(item['level'], item['level'])}** : {esc(item['message'])}")


def _preview_panel(service: ProductionService, production: Production) -> None:
    st.markdown('<h2 class="composer-title">Aperçu rapide</h2><p class="muted-note">Demi-résolution, '
                'sans créer de version ni rien confirmer ; les scènes inchangées sont reprises.</p>',
                unsafe_allow_html=True)
    state = reedit_v2.preview_state(service, production)

    @st.fragment(run_every=4 if state.state in ("queued", "running") else None)
    def live() -> None:
        current = reedit_v2.preview_state(service, service.repo.get(production.id))
        if current.state == "done" and current.video is not None:
            with st.container(key="preview_player"):
                st.video(str(current.video), format="video/mp4")
        elif current.state in ("queued", "running"):
            progress = f" — {current.progress} %" if current.progress else ""
            st.markdown(f'<p class="muted-note" role="status">Aperçu en cours : {esc(current.step)}{progress}</p>',
                        unsafe_allow_html=True)
        elif current.state == "failed":
            st.markdown(f'<div class="banner banner-error" role="alert">{esc(current.error)}</div>',
                        unsafe_allow_html=True)
        if current.state != state.state:
            st.rerun()

    live()
    if state.state in ("none", "failed"):
        st.button("Aperçu rapide", icon=":material/play_circle:", key="preview_v2",
                  on_click=_start_preview, args=(service, production))


def render_editor(service: ProductionService, project: Project, production: Production, parent: Production) -> None:
    keys = _keys(production)
    st.markdown(
        f'<section class="hero"><p class="eyebrow">{esc(project.name)} · {esc(production.label)}</p>'
        '<h1 class="hero-title">Nouvelle version</h1>'
        f'<p class="hero-sub">Modifie une ou plusieurs scènes de la {esc(parent.label)} : {esc(production.subject)}</p></section>',
        unsafe_allow_html=True)
    st.markdown(
        f'<div class="banner banner-info" role="note"><strong>La {esc(parent.label)} reste intacte.</strong> Seules les '
        "scènes modifiées sont recalculées ; les autres sont reprises telles quelles. Rendu 2D local : aucun appel "
        "payant.</div>", unsafe_allow_html=True)
    if st.session_state.get(keys["notice"]):
        st.markdown(f'<div class="banner banner-success" role="status">{esc(st.session_state.pop(keys["notice"]))}</div>',
                    unsafe_allow_html=True)

    plan = reedit_v2.plan_for(service, production)
    by_id = {entry["id"]: entry for entry in plan["scenes"]} if plan else {}
    edited = production.status is S.EN_ATTENTE_CONFIRMATION and plan is not None and any(
        entry.get("changes") != [] for entry in plan["scenes"])
    thumbs = reedit_v2.scene_thumbnails(service, parent)
    editing = st.session_state.get(keys["editing"])
    st.markdown('<h2 class="composer-title">Scènes</h2>', unsafe_allow_html=True)
    for summary in scene_edit.summarize(production.animation):
        if summary.id == editing:
            _editor(service, production, summary)
        else:
            _scene_card(service, production, summary, thumbs, by_id.get(summary.id) if edited else None)
    if st.session_state.get(keys["error"]) and not editing:
        st.markdown(f'<p class="field-error" role="alert">{esc(st.session_state[keys["error"]])}</p>',
                    unsafe_allow_html=True)

    if not edited:
        st.markdown('<p class="muted-note">Aucune scène modifiée pour l’instant : choisis « Modifier cette scène ».</p>',
                    unsafe_allow_html=True)
    else:
        cost, needs_consent = reedit_v2.voice_notice(plan, reedit_v2.has_voice(service, parent))
        _plan_panel(plan, cost, needs_consent, production)
        _preview_panel(service, production)
        report = service.preflight(production)
        consent = not needs_consent or bool(st.session_state.get(keys["consent"]))
        if not report.ready:
            st.markdown('<div class="banner banner-error" role="alert">Le rendu ne peut pas partir pour l’instant : '
                        + esc(" ".join(issue.message for issue in report.to_issues()[:2])) + "</div>",
                        unsafe_allow_html=True)
    with st.container(horizontal=True, vertical_alignment="center", key="reedit_actions"):
        if edited:
            st.button(f"Générer la {production.label}", type="primary", icon=":material/rocket_launch:", key="generate_v2",
                      on_click=_generate, args=(service, project, production), disabled=not report.ready or not consent)
        st.button(f"Revenir à la {parent.label}", type="tertiary", icon=":material/arrow_back:", key="back_parent",
                  on_click=nav.go, args=(nav.VIEW_TRACK, project.id, parent.id))


# -- suivi : comparaison V1/V2 et contrôle qualité --------------------------------------------------------------
def render_comparison(service: ProductionService, project: Project, production: Production) -> None:
    """Dans le suivi d'une version animée terminée : réutilisation, temps évité, QA et comparaison."""
    data = reedit_v2.comparison(service, production)
    if not data:
        return
    with st.container(key="reedit_comparison", border=True):
        st.markdown('<p class="card-eyebrow">Rendu 2D</p><h2 class="brief-title">Réédition et contrôle qualité</h2>',
                    unsafe_allow_html=True)
        cols = st.columns(3)
        cols[0].metric("Scènes réutilisées", data["scenes_reused"])
        cols[1].metric("Scènes recalculées", data["scenes_rendered"])
        cols[2].metric("Temps de rendu évité", _seconds(data["render_ms_saved"] / 1000),
                       help="Durée de rendu mesurée à l'origine des scènes réutilisées.")
        status = (data["qa"] or {}).get("status", "")
        label = {"ok": "aucun défaut détecté", "warning": "avertissements", "error": "erreurs à corriger"}.get(status,
                                                                                                         "non disponible")
        problems = [item for item in data["qa_checks"] if item.get("level") in ("warning", "error")]
        with st.expander(f"Contrôle qualité : {label}" + (f" ({len(problems)})" if problems else "")):
            if not data["qa_checks"]:
                st.markdown("Aucun contrôle enregistré pour ce rendu.")
            for item in data["qa_checks"]:
                scene = f" (scène {item['scene']})" if item.get("scene") else ""
                st.markdown(f"- **{LEVEL_LABELS.get(item.get('level'), item.get('level'))}**{scene} : "
                            f"{esc(item.get('message', ''))}")
        parent = data["parent"]
        if parent is not None and data["previous_video"] is not None:
            st.markdown(f"**Comparer avec la {esc(parent.label)}**")
            try:
                current = service.resolve_video(production)
            except ValueError:
                current = None
            left, right = st.columns(2)
            with left:
                st.caption(parent.label + " (conservée)")
                st.video(str(data["previous_video"]), format="video/mp4")
            with right:
                st.caption(production.label)
                if current is not None:
                    st.video(str(current), format="video/mp4")
            st.button(f"Revenir à la {parent.label}", icon=":material/history:", key="back_previous_version",
                      on_click=nav.go, args=(nav.VIEW_TRACK, project.id, parent.id))
