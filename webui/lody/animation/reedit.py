"""Plan de réédition ciblée (#121) : ce qu'une V2 du storyboard animé refait, et ce qu'elle réutilise.

``plan(avant, après)`` compare deux storyboards ``lody.animation/1`` scène par scène (appariées par leur ``id``
stable) et répond, **sans rien rendre ni appeler** :

- quelles scènes sont réutilisées telles quelles (cache de scène ``scene_cache``) et lesquelles sont à refaire ;
- **pourquoi** : geste, expression, mouvement, plan (caméra), texte affiché, réplique, timecodes, durée,
  transitions, assets, repli — ou seulement l'intention narrative, qui ne refait rien ;
- les scènes dont la **voix** est à régénérer (texte de réplique modifié) : synthèse externe payante, donc
  jamais lancée d'office (accord de coût requis) ; les autres voix sont conservées ;
- le temps de rendu à dépenser et celui évité (mesuré dans le manifeste de la V1 quand il est fourni) ;
- les contrôles qualité du nouveau storyboard (``qa.static_checks``), gratuits.

La décision « réutilisée » est exactement celle du cache du worker : même vue moteur de la scène
(``scene_cache.engine_view``), mêmes versions d'assets. Une exception est annoncée : si une scène PRÉCÉDENTE
change de durée et qu'une voix enregistrée est utilisée, la fenêtre de voix de la scène se décale et le worker
la refera (synchro labiale) — le plan le signale par ``voice_shift``.
"""

from __future__ import annotations

from typing import Any

from lody.animation import qa
from lody.animation.contract import canonical_json
from lody.animation.scene_cache import engine_view

RENDER_SPEED_FACTOR = 7.0  # ≈ 7× le temps réel (benchmark #118) — même facteur que le préflight du fournisseur
MOTION_KEYS = ("position", "scale", "rotation", "opacity", "easing", "start", "end")
LABELS = {
    "intent": "intention narrative (sans effet sur l'image)",
    "duration": "durée",
    "gestures": "geste",
    "expressions": "expression",
    "motion": "mouvement",
    "lipsync": "synchro labiale",
    "objects": "objets ajoutés ou retirés",
    "assets": "version d'asset",
    "text": "texte affiché",
    "speech_text": "réplique",
    "speech_timing": "timecodes de parole",
    "camera": "plan (caméra)",
    "transitions": "transitions",
    "fallback": "image de repli",
    "layers": "calques",
}


def _same(a: Any, b: Any) -> bool:
    return canonical_json(a) == canonical_json(b)


def _objects(scene: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f"{layer['id']}/{obj['id']}": obj for layer in scene["render"]["layers"] for obj in layer["objects"]}


def scene_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Nature des différences entre deux versions d'une même scène (clés de ``LABELS``), ordre stable."""
    found: list[str] = []

    def add(kind: str) -> None:
        if kind not in found:
            found.append(kind)

    if not _same(before.get("intent"), after.get("intent")):
        add("intent")
    if before["duration_frames"] != after["duration_frames"]:
        add("duration")
    rb, ra = before["render"], after["render"]
    if [layer["id"] for layer in rb["layers"]] != [layer["id"] for layer in ra["layers"]]:
        add("layers")
    left, right = _objects(before), _objects(after)
    if set(left) != set(right):
        add("objects")
    for key in sorted(set(left) & set(right)):
        a, b = left[key], right[key]
        if a.get("type") != b.get("type"):
            add("objects")
            continue
        if a.get("type") == "asset" and not _same(a.get("asset"), b.get("asset")):
            add("assets")
        if a.get("type") == "text" and not _same(a.get("text"), b.get("text")):
            add("text")
        for field, kind in (("gestures", "gestures"), ("expressions", "expressions"), ("lipsync", "lipsync")):
            if not _same(a.get(field), b.get(field)):
                add(kind)
        if any(not _same(a.get(field), b.get(field)) for field in MOTION_KEYS):
            add("motion")
    sb, sa = rb.get("speech") or {}, ra.get("speech") or {}
    cues_b, cues_a = sb.get("cues") or [], sa.get("cues") or []
    if [c["text"] for c in cues_b] != [c["text"] for c in cues_a] or sb.get("speaker") != sa.get("speaker"):
        add("speech_text")
    if [(c["start"], c["end"]) for c in cues_b] != [(c["start"], c["end"]) for c in cues_a]:
        add("speech_timing")
    if not _same(rb.get("camera"), ra.get("camera")):
        add("camera")
    if not _same(rb.get("transition_in"), ra.get("transition_in")) or not _same(
            rb.get("transition_out"), ra.get("transition_out")):
        add("transitions")
    if not _same(rb.get("fallback"), ra.get("fallback")):
        add("fallback")
    if not found and not _same(engine_view(before), engine_view(after)):
        add("layers")  # différence résiduelle (ordre des objets, champ facultatif) : prudence, scène à refaire
    return found


def _voice_text(scene: dict[str, Any]) -> list[str]:
    return [cue["text"] for cue in ((scene["render"].get("speech") or {}).get("cues") or [])]


def plan(before: dict[str, Any], after: dict[str, Any], *, previous_manifest: dict[str, Any] | None = None,
         has_voice: bool = False) -> dict[str, Any]:
    """Plan ``lody.animation-reedit-plan/1`` d'une V2 (voir l'en-tête du module). Aucun rendu, aucun appel."""
    canvas_changed = not _same(before.get("canvas"), after.get("canvas"))
    fps = after["canvas"]["fps"]
    previous = {scene["id"]: (index, scene) for index, scene in enumerate(before["scenes"], 1)}
    measured = {item.get("id"): int(item.get("render_ms") or 0) + int(item.get("render_ms_saved") or 0)
                for item in (previous_manifest or {}).get("scenes") or []}
    scenes, offset_before, offset_after = [], {}, {}
    running = 0
    for scene in before["scenes"]:
        offset_before[scene["id"]] = running
        running += scene["duration_frames"]
    running = 0
    for scene in after["scenes"]:
        offset_after[scene["id"]] = running
        running += scene["duration_frames"]

    for index, scene in enumerate(after["scenes"], 1):
        seconds = scene["duration_frames"] / fps
        entry: dict[str, Any] = {"index": index, "id": scene["id"], "seconds": round(seconds, 3)}
        if scene["id"] not in previous:
            entry.update(action="render", reason="nouvelle scène", changes=["new"], voice="new" if _voice_text(scene)
                         else "none")
        else:
            old_index, old = previous[scene["id"]]
            changes = scene_changes(old, scene)
            visual = [change for change in changes if change != "intent"]
            entry["previous_index"] = old_index
            entry["changes"] = changes
            if canvas_changed:
                entry.update(action="render", reason="format de l'image modifié")
            elif visual:
                entry.update(action="render", reason=", ".join(LABELS[change] for change in visual))
            else:
                entry.update(action="reuse", reason="inchangée" if not changes else LABELS["intent"])
            shifted = offset_before[scene["id"]] != offset_after[scene["id"]] or "duration" in changes
            if has_voice and shifted and entry["action"] == "reuse":
                entry["voice_shift"] = True  # la fenêtre de voix se décale : refaite par le worker avec la même voix
                entry.update(action="render", reason="décalage de la voix (durée d'une scène précédente modifiée)")
            if _voice_text(old) != _voice_text(scene):
                entry["voice"] = "regenerate" if _voice_text(scene) else "removed"
            else:
                entry["voice"] = "keep" if _voice_text(scene) else "none"
            if scene["id"] in measured and measured[scene["id"]]:
                entry["previous_render_ms"] = measured[scene["id"]]
        entry["estimated_render_seconds"] = round(seconds * RENDER_SPEED_FACTOR) if entry["action"] == "render" else 0
        scenes.append(entry)

    removed = [{"index": index, "id": scene_id} for scene_id, (index, _) in previous.items()
               if scene_id not in {scene["id"] for scene in after["scenes"]}]
    reused = [entry for entry in scenes if entry["action"] == "reuse"]
    rendered = [entry for entry in scenes if entry["action"] == "render"]
    saved = sum(entry["previous_render_ms"] / 1000 if entry.get("previous_render_ms")
                else entry["seconds"] * RENDER_SPEED_FACTOR for entry in reused)
    voice_new = [entry["index"] for entry in scenes if entry.get("voice") in ("regenerate", "new")]
    voice_kept = [entry["index"] for entry in scenes if entry.get("voice") == "keep"]
    return {
        "schema": "lody.animation-reedit-plan/1",
        "canvas_changed": canvas_changed,
        "scenes": scenes,
        "removed": removed,
        "summary": {
            "scenes_total": len(scenes), "scenes_reused": len(reused), "scenes_to_render": len(rendered),
            "render_seconds": sum(entry["estimated_render_seconds"] for entry in rendered),
            "render_seconds_saved": round(saved),
            "render_seconds_saved_measured": all(entry.get("previous_render_ms") for entry in reused) and bool(reused),
            "voice_to_regenerate": voice_new, "voice_kept": voice_kept,
            "external_calls": 0,
            "cost_note": ("Aucun appel externe : le rendu 2D est local et gratuit." if not voice_new else
                          f"Voix à régénérer pour {len(voice_new)} scène(s) : synthèse payante, jamais lancée sans "
                          "votre accord sur le coût. Les autres voix sont conservées."),
        },
        "qa": qa.static_checks(after, has_voice=has_voice),
    }


def describe(result: dict[str, Any]) -> str:
    """Résumé lisible (français) d'un plan, pour l'outil en ligne de commande et l'interface."""
    summary = result["summary"]
    lines = [f"{summary['scenes_reused']} scène(s) réutilisée(s), {summary['scenes_to_render']} à refaire "
             f"(≈ {summary['render_seconds']} s de rendu, ≈ {summary['render_seconds_saved']} s évitées"
             + (", mesurées" if summary["render_seconds_saved_measured"] else ", estimées") + ")."]
    if result["canvas_changed"]:
        lines.append("Format de l'image modifié : toutes les scènes sont à refaire.")
    for entry in result["scenes"]:
        mark = "=" if entry["action"] == "reuse" else "↻"
        voice = {"regenerate": " — voix à régénérer", "new": " — voix à créer", "removed": " — réplique retirée"}.get(
            entry.get("voice", ""), "")
        lines.append(f"  {mark} {entry['index']:>2}. {entry['id']} : {entry['reason']}{voice}")
    for entry in result["removed"]:
        lines.append(f"  − {entry['id']} (ancienne scène {entry['index']}) : retirée")
    lines.append(summary["cost_note"])
    for item in result["qa"]:
        if item["level"] in ("warning", "error"):
            lines.append(f"  ⚠ {item['message']}")
    return "\n".join(lines)
