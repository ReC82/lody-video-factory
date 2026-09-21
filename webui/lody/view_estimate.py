"""Estimation du coût et résumé de confirmation (production initiale et V2).

Tout est lu dans la production enregistrée (jamais recalculé dans la vue) : ce qui est affiché est
exactement ce qui sera confirmé. Les valeurs sont échappées avant d'entrer dans le HTML.
"""

from __future__ import annotations

from decimal import Decimal

import streamlit as st

from lody import catalog
from lody.generation import costing
from lody.generation.models import ReadinessIssue
from lody.generation.service import request_of
from lody.generation.store import Production
from lody.theme import esc

_UNPRICED = "tarif non configuré"
_PROVIDER_CATALOGS = {
    "text": catalog.TEXT_PROVIDERS, "visual": catalog.VISUAL_PROVIDERS,
    "voice": catalog.VOICE_PROVIDERS, "music": catalog.MUSIC_PROVIDERS,
}


def _span(low: int, high: int, singular: str, plural: str | None = None) -> str:
    plural = plural or singular + "s"
    if low == high:
        return f"{low:,} {singular if low <= 1 else plural}".replace(",", " ")
    return f"{low:,} à {high:,} {plural}".replace(",", " ")


def _quantity(line: dict) -> str:
    low, high = line["quantity"]
    component = line["component"]
    if component == "text":
        return "1 script écrit" if high else "script fourni : aucun appel"
    if component == "visual":
        return _span(low, high, "image")
    if component == "voice":
        return "≈ " + _span(low, high, "caractère")
    return "1 morceau" if high else "—"


def _amount(line: dict, currency: str) -> str:
    if line["status"] == "free":
        return "Gratuit"
    if line["status"] == "unpriced":
        return f'<span class="est-unpriced">{_UNPRICED}</span>'
    low, high = Decimal(line["low"]), Decimal(line["high"])
    return esc(costing.format_range(low, high, currency))


def estimate_html(production: Production) -> str:
    detail = production.cost_detail
    currency = production.cost_currency
    rows = []
    for line in detail.get("lines", []):
        provider = catalog.label(_PROVIDER_CATALOGS[line["component"]], line["provider"])
        rows.append(
            '<div class="est-row"><div class="est-what">'
            f'<span class="est-label">{esc(line["label"])}</span>'
            f'<span class="est-provider">{esc(provider)}</span>'
            f'<span class="est-qty">{esc(_quantity(line))}</span></div>'
            f'<div class="est-amount">{_amount(line, currency)}</div></div>'
        )
    total = costing.format_range(production.cost_low, production.cost_high, currency)
    if production.cost_partial and production.cost_high is not None:
        total += " (partiel)"
    elif production.cost_high is None:
        total = _UNPRICED
    partial_note = (
        '<p class="est-warning" role="alert"><strong>Total partiel.</strong> Au moins un tarif n’est pas '
        "configuré : le montant ci-dessus ne couvre pas tous les postes.</p>"
        if production.cost_partial else ""
    )
    return (
        f'<div class="est-rows">{"".join(rows)}</div>'
        f'<div class="est-total"><span>Total estimé</span><strong>{esc(total)}</strong></div>{partial_note}'
        '<p class="muted-note">Montants indicatifs, calculés à partir des tarifs configurés et de la durée visée : '
        "le coût réel peut différer et n’est pas mesuré.</p>"
    )


def summary_html(production: Production) -> str:
    request = request_of(production)
    scenes = production.cost_detail.get("scenes", [0, 0])
    seconds = production.cost_detail.get("audio_seconds", [request.duration_min, request.duration_max])
    duration = f"{seconds[0]} s" if seconds[0] == seconds[1] else f"{seconds[0]} à {seconds[1]} secondes"
    providers = " · ".join([
        f"Script : {catalog.label(catalog.TEXT_PROVIDERS, request.text_provider) if not request.script else 'fourni par toi'}",
        f"Images : {catalog.label(catalog.VISUAL_PROVIDERS, request.visual_provider)}",
        f"Voix : {catalog.label(catalog.VOICE_PROVIDERS, request.voice.provider)}",
        f"Musique : {catalog.label(catalog.MUSIC_PROVIDERS, request.music_provider)}",
    ])
    rows = [
        ("Sujet", production.subject),
        ("Durée cible", duration),
        ("Scènes estimées", _span(scenes[0], scenes[1], "scène")),
        ("Fournisseurs", providers),
    ]
    items = "".join(f"<div><dt>{esc(label)}</dt><dd>{esc(value)}</dd></div>" for label, value in rows)
    return f'<dl class="kv est-summary">{items}</dl>'


def render_issues(issues: list[ReadinessIssue]) -> None:
    for issue in issues:
        kind = "error" if issue.blocking else "info"
        st.markdown(f'<div class="banner banner-{kind}" role="alert">{esc(issue.message)}</div>', unsafe_allow_html=True)


def render_panel(production: Production, issues: list[ReadinessIssue], *, demo: bool, key: str,
                 v2: bool = False) -> bool:
    """Affiche estimation + résumé. Retourne True si la confirmation peut être proposée.

    Bloque tant qu'un problème bloquant existe ; exige une case cochée si le total est partiel.
    """
    with st.container(key=f"estimate_{key}"):
        st.markdown(
            '<p class="card-eyebrow">Avant de lancer</p><h2 class="brief-title">Estimation et confirmation</h2>',
            unsafe_allow_html=True,
        )
        if demo:
            st.markdown(
                '<div class="banner banner-info" role="status"><strong>Mode démonstration.</strong> La génération sera '
                "simulée : aucun fournisseur n’est appelé et rien n’est facturé. Les montants ci-dessous montrent ce que "
                "coûterait une vraie génération.</div>",
                unsafe_allow_html=True,
            )
        st.markdown(summary_html(production) + estimate_html(production), unsafe_allow_html=True)
        if not demo:
            what = "une V2 entraîne de nouveaux appels payants et régénère tous les médias" if v2 else (
                "plusieurs appels payants (script, images, voix, musique)")
            st.markdown(
                '<div class="banner banner-info" role="note"><strong>Une seule confirmation.</strong> '
                f"Après ton accord, {esc(what)} seront lancés automatiquement, sans autre validation.</div>",
                unsafe_allow_html=True,
            )
        render_issues(issues)
        blocking = any(issue.blocking for issue in issues)
        accepted = True
        if production.cost_partial and not demo:
            accepted = st.checkbox(
                "Je comprends que ce total est partiel : certains tarifs ne sont pas configurés.",
                key=f"accept_partial_{key}",
            )
        return not blocking and accepted
