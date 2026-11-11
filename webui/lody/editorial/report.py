"""Vue lisible du contrôle éditorial (#167) : verdict, alertes et tableau script ↔ storyboard scène par scène."""

from __future__ import annotations

from typing import Any

from lody.editorial.checks import A_REVOIR, BLOQUANT, INFO, Report

ICONS = {BLOQUANT: "⛔", A_REVOIR: "⚠️", INFO: "ℹ️"}
VERDICT_TEXT = {"prêt": "✅ Prêt : aucune alerte à traiter avant génération.",
                "à relire": "⚠️ À relire : des points sont à vérifier ou à justifier avant génération.",
                "à corriger": "⛔ À corriger : au moins une alerte bloque la génération."}


def _cell(text: str, limit: int = 160) -> str:
    text = " ".join(str(text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def to_markdown(document: dict[str, Any], report: Report, *, title: str = "") -> str:
    brief = document["brief"]
    counts = report.counts()
    settings = report.settings
    low, high = settings["scene_seconds"]
    lines = [f"# Contrôle éditorial — {title or brief.get('title') or 'épisode'}", "",
             f"**{VERDICT_TEXT[report.verdict]}**", "",
             f"- Alertes : {counts[BLOQUANT]} bloquante(s), {counts[A_REVOIR]} à revoir, {counts[INFO]} info.",
             f"- Objectif : {_cell(brief.get('objective') or '—', 300)}",
             f"- Public : {settings['audience']} ; notion(s) nouvelle(s) : "
             + (", ".join(f"« {t} »" for t in report.new_terms) or "—"),
             f"- Durée : parole {report.speech_total_s:.1f} s, scènes {report.planned_total_s:.1f} s "
             f"(débit {settings['words_per_second']:g} mots/s, scènes de {low:g} à {high:g} s"
             + (f", cible {settings['target_seconds'][0]:g}–{settings['target_seconds'][1]:g} s" if
                settings["target_seconds"] else "") + ").", ""]
    general = [f for f in report.findings if f.scene is None]
    if general:
        lines += ["## Brief", ""]
        lines += [f"- {ICONS[f.level]} **{f.level}** `{f.path}` — {f.message}"
                  + (f" *Piste : {f.suggestion}*" if f.suggestion else "") for f in general]
        lines.append("")
    lines += ["## Script ↔ storyboard", "",
              "| # | Scène | Durée | Voix | Ce qu'on entend | Ce qu'on voit | Écran | Alertes |",
              "|---|---|---|---|---|---|---|---|"]
    timings = {t.id: t for t in report.timings}
    for index, scene in enumerate(document["scenes"]):
        timing = timings[scene["id"]]
        alerts = report.for_scene(scene["id"])
        marks = " ".join(f"{ICONS[f.level]} {f.code}" for f in alerts if f.level != INFO) or "—"
        lines.append(f"| {index + 1} | `{scene['id']}` | {timing.planned_s:.1f} s | "
                     f"{timing.speech_s:.1f} s{' (mesurée)' if timing.measured else ''} | "
                     f"{_cell(' '.join(scene['captions']) or scene['spoken'])} | {_cell(scene['visual'])} | "
                     f"{_cell(' / '.join(scene['displayed'])) or '—'} | {marks} |")
    lines.append("")
    detailed = [f for f in report.findings if f.scene is not None]
    if detailed:
        lines += ["## Alertes par scène (à corriger dans le champ indiqué)", ""]
        for f in detailed:
            lines.append(f"- {ICONS[f.level]} **{f.level}** — scène `{f.scene}`, champ `{f.path}` : {f.message}"
                         + (f" *Piste : {f.suggestion}*" if f.suggestion else ""))
        lines.append("")
    lines += ["_Contrôle hors réseau, aucune correction automatique : les pistes sont des propositions à "
              "valider par un humain. L'écoute des prononciations reste nécessaire avant tout rendu._", ""]
    return "\n".join(lines)
