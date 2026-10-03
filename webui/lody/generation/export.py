"""Exports autonomes d'une production : configuration complète et journal des événements (ticket #91).

Objectif : permettre d'analyser une production directement dans ChatGPT (ou toute autre IA, ou un humain),
à partir de DEUX fichiers TXT indépendants, téléchargeables directement sans ZIP. Aucune donnée n'est
reconstruite depuis la configuration ACTUELLE du projet/des personnages/des lieux : tout vient de ce qui est
déjà figé sur CETTE production (``snapshot``, ``trace``, ``params``, ``storyboard``, ``script``, ``brief``,
``assets``) — exactement la même source que ``lody.view_tracking._render_diagnostic``, jamais les tables
éditables qui ont pu changer depuis.

Aucun secret : la sélection de champs ci-dessous n'inclut jamais une clé API, et chaque valeur texte passe
en plus par ``lody.generation.safety.redact`` (défense en profondeur — voir ``_clean``).

Fonctions PURES : aucun appel réseau, aucun nouvel appel payant. Compatibles avec une production en cours
(instantané partiel, clairement identifié comme tel), échouée, terminée, ou antérieure à ce ticket (les clés
absentes sont alors explicitement signalées comme indisponibles — jamais inventées, voir ``_UNAVAILABLE``).

Le journal des événements (``build_logs_export``) repose sur ``trace["events"]``, alimenté par
``ProductionService`` (#91, additif) à chaque étape réelle depuis la confirmation : avant ce ticket, cette
clé n'existe pas, et seuls les horodatages déjà présents par ailleurs (créée/confirmée/démarrée/terminée)
sont alors reconstitués, avec un événement explicite signalant l'absence du détail fin.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from lody.generation.safety import redact
from lody.generation.store import Production

EXPORT_SCHEMA_VERSION = 1
UNAVAILABLE = "indisponible (information non enregistrée pour cette production)"


# Clés dont la VALEUR est un identifiant ou une référence de fichier — jamais du texte libre — à préserver
# telles quelles : ``redact()`` masque aussi tout jeton opaque d'au moins 32 caractères mêlant lettres et
# chiffres (règle volontairement large, pensée pour du texte libre), ce qu'est justement un UUID de tâche
# moteur ou un chemin de référence. Ce sont pourtant exactement les identifiants que le ticket #91 demande
# de CONSERVER pour corréler production/tâche/scène ("en conservant les identifiants utiles comme voice_id
# et noms de modèle"). Texte libre (script, prompts, brief, avertissements...) continue de passer par
# ``redact`` normalement : seules ces clés précises y échappent.
_IDENTIFIER_KEYS = {
    "id", "ref", "production_id", "projet_id", "project_id", "tache_moteur_id", "task_id",
    "production_parente_id", "parent_production_id", "production_racine_id", "root_production_id",
    "character_id", "location_id", "voice_id", "external_voice_id", "reference_image", "video_ref",
}


def _clean(value: Any, key: str | None = None) -> Any:
    """Applique ``redact`` récursivement à toute chaîne de texte LIBRE (défense en profondeur, en plus de la
    sélection explicite des champs ci-dessous, qui n'inclut jamais une clé d'API) — sauf aux identifiants et
    références de fichiers (voir ``_IDENTIFIER_KEYS``), jamais du texte libre, toujours à conserver lisibles."""
    if isinstance(value, str):
        if key in _IDENTIFIER_KEYS or (key or "").endswith(("_id", "_ref")):
            return value
        return redact(value)
    if isinstance(value, dict):
        return {k: _clean(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item, key) for item in value]
    return value


def _cost_summary(production: Production) -> dict[str, Any]:
    measured = "indisponible : le moteur ne fournit pas le coût effectivement facturé par les fournisseurs"
    if production.cost_high is None:
        return {"estimation": UNAVAILABLE, "mesure": measured}
    return {
        "estimation_min": str(production.cost_low) if production.cost_low is not None else None,
        "estimation_max": str(production.cost_high), "devise": production.cost_currency,
        "estimation_partielle": production.cost_partial, "detail_estimation": production.cost_detail,
        "mesure": measured,
    }


def _scene_image_correspondence(production: Production, images: list[dict[str, str]] | None) -> Any:
    """Correspondance scène → image RÉELLEMENT reçue → horodatage de réception (#92).

    ``images`` vient de ``provider.list_scene_images()`` (même fonction que le suivi #93), déjà dans
    l'ordre chronologique de réception — JAMAIS recalculée ici. Associée aux scènes PAR POSITION : le
    moteur génère une image par terme de ``video_terms`` dans l'ordre (vérifié dans
    ``app/services/material.py:_download_videos_openai_image_on_demand``), mais peut s'arrêter avant la
    fin de la liste une fois la durée de l'audio couverte — une scène sans image correspondante le dit
    EXPLICITEMENT, jamais une image inventée ou mal associée."""
    storyboard = production.storyboard or []
    if images is None:
        return "indisponible : liste des images reçues non fournie à cet export"
    if not storyboard:
        return UNAVAILABLE
    rows = []
    for position, scene in enumerate(storyboard):
        image = images[position] if position < len(images) else None
        rows.append({
            "scene": scene.get("index"),
            "image_recue": image.get("name") if image else None,
            "recue_le": image.get("captured_at") if image else None,
            "statut": "reçue" if image else "aucune image dédiée reçue pour cette scène (couverture de "
                                             "durée du moteur atteinte avant cette scène, ou génération encore en cours)",
        })
    return rows


def build_config_export(production: Production, images: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Instantané JSON autonome et versionné de la configuration RÉELLEMENT utilisée par CETTE production
    (paramètres résolus, personnages/lieu snapshotés, prompts envoyés, script, storyboard, voix, coût...).

    ``images`` (optionnel) : images RÉELLEMENT reçues (``provider.list_scene_images()``, #93) — permet
    d'ajouter la correspondance scène → image → horodatage (#92). Toujours calculable, même en cours
    (``partiel`` le dit alors explicitement) ou pour une production antérieure à ce ticket (les clés
    absentes affichent ``UNAVAILABLE``, jamais une valeur inventée)."""
    snapshot = production.snapshot or {}
    trace = production.trace or {}
    params = production.params or {}
    request = params.get("request") or UNAVAILABLE
    engine_params = params.get("engine") or UNAVAILABLE
    script_request = trace.get("script_request")

    config: dict[str, Any] = {
        "schema": "lody.production_config_export", "schema_version": EXPORT_SCHEMA_VERSION,
        "genere_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "instantane_partiel": production.status.value in ("CONFIRMEE", "EN_FILE", "EN_COURS"),
        "identifiants": {
            "production_id": production.id, "projet_id": production.project_id,
            "tache_moteur_id": production.external_task_id or None,
            "version": production.version, "label": production.label,
            "production_parente_id": production.parent_production_id,
            "production_racine_id": production.root_production_id,
        },
        "statut": {
            "status": production.status.value, "etape_courante": production.current_step,
            "cree_le": production.created_at, "confirme_le": production.confirmed_at,
            "demarre_le": production.started_at, "termine_le": production.finished_at,
            "dernier_sondage_le": production.last_polled_at, "mis_a_jour_le": production.updated_at,
        },
        "fournisseur": {"id": production.provider, "script_source": production.script_source or UNAVAILABLE},
        "demande_initiale": {"sujet": production.subject, "brief": production.brief or UNAVAILABLE},
        "parametres_resolus_de_la_demande": request,
        "parametres_transmis_au_moteur": engine_params,
        "contexte_narratif_instantane": snapshot.get("narrative_context") or "aucune sélection pour cette production",
        "voix": {
            "resolue": params.get("voice_resolution") or UNAVAILABLE,
            "snapshotee": (snapshot.get("request") or {}).get("voice") or UNAVAILABLE,
            "transmise_trace": trace.get("voice") or UNAVAILABLE,
        },
        "mode_script": trace.get("script_mode") or UNAVAILABLE,
        "script": {
            "final_valide_et_transmis": production.script or UNAVAILABLE,
            "prompt_editorial_envoye": (script_request or {}).get("video_script_prompt")
                                       if script_request else "non applicable : script fourni ou repris, aucun prompt envoyé",
            "prompt_systeme": (script_request or {}).get("custom_system_prompt", "défaut du moteur")
                              if script_request else None,
        },
        "storyboard": [
            {"index": scene.get("index"), "narration": scene.get("narration"), "action": scene.get("action", ""),
             "prompt_image_final": scene.get("prompt"), "duree_estimee_s": scene.get("seconds")}
            for scene in (production.storyboard or [])
        ] or UNAVAILABLE,
        "scenes_prompts_envoyes_au_moteur": trace.get("scenes") or UNAVAILABLE,
        "correspondance_scene_image": _scene_image_correspondence(production, images),
        "modele_images": trace.get("image_model") or UNAVAILABLE,
        "gabarit_images_moteur": trace.get("image_template") or UNAVAILABLE,
        "parametres_sous_titres_montage_musique": trace.get("engine_params") or UNAVAILABLE,
        "origine_de_chaque_valeur": trace.get("origins") or UNAVAILABLE,
        "cout": _cost_summary(production),
        "resultat": {
            "video_ref": production.video_ref or None, "duree_s": production.video_duration,
            "assets": production.assets or [], "avertissements": list(production.warnings or []),
        },
        "erreur": ({"code": production.error_code, "message": production.error_message}
                  if production.error_code or production.error_message else None),
        "limites_connues": [
            "Le coût réellement facturé par les fournisseurs n'est pas exposé par le moteur : seule l'estimation figure ici.",
            "Les tentatives automatiques internes au moteur (ex. relance d'une génération d'image après échec) ne sont "
            "pas comptées séparément : seul le résultat final transmis est visible ici.",
        ],
    }
    return _clean(config)


def config_export_text(production: Production, images: list[dict[str, str]] | None = None) -> str:
    """Texte UTF-8 autonome (JSON indenté) : contenu de ``production_<id>_config.txt``."""
    return json.dumps(build_config_export(production, images), indent=2, ensure_ascii=False, default=str)


def _event(at: str | None, label: str, **detail: Any) -> dict[str, Any]:
    """Mêmes clés EXACTEMENT que ``ProductionService._log_event`` (``at``/``event``/``detail``) : les
    événements synthétisés ici et ceux réellement enregistrés par le service pendant la production
    (``trace["events"]``) doivent pouvoir se mélanger dans une seule liste chronologique sans distinction."""
    return {"at": at or UNAVAILABLE, "event": label, **({"detail": detail} if detail else {})}


def build_logs_export(production: Production) -> list[dict[str, Any]]:
    """Journal chronologique des événements RÉELLEMENT enregistrés pour cette production (liste de dicts,
    utilisée aussi bien par ``logs_export_text`` que par un test ou un futur export JSONL)."""
    trace = production.trace or {}
    events: list[dict[str, Any]] = [_event(production.created_at, "production créée", sujet=production.subject)]
    if production.confirmed_at:
        events.append(_event(production.confirmed_at, "confirmée par l'utilisateur"))
    if production.started_at:
        events.append(_event(production.started_at, "démarrage du traitement"))
    recorded = trace.get("events")
    if recorded:
        events.extend(recorded)
    else:
        events.append(_event(None, "journal détaillé indisponible : production antérieure au ticket #91, "
                                   "ou aucun événement n'a été enregistré au-delà des horodatages ci-dessus"))
    if production.last_polled_at:
        events.append(_event(production.last_polled_at, "dernier sondage du moteur",
                             etape=production.current_step, progression=production.progress))
    if production.status.value == "ECHEC" and (production.error_code or production.error_message):
        events.append(_event(production.finished_at, "échec final", code=production.error_code,
                             message=production.error_message))
    if production.status.value == "TERMINEE" and production.finished_at:
        events.append(_event(production.finished_at, "terminée", duree_s=production.video_duration))
    for warning in production.warnings or []:
        events.append(_event(None, "avertissement", texte=warning))
    return [_clean(event) for event in events]


def logs_export_text(production: Production) -> str:
    """Texte UTF-8 autonome, une ligne par événement : contenu de ``production_<id>_logs.txt``."""
    lines = [
        f"Journal de production — {production.id} ({production.label}, statut : {production.status.value})",
        "Format : [horodatage ISO 8601 UTC, ou 'indisponible'] événement — détail JSON éventuel.",
        "",
    ]
    for event in build_logs_export(production):
        line = f"[{event.get('at', UNAVAILABLE)}] {event.get('event', '')}"
        detail = event.get("detail")
        if detail:
            line += " — " + json.dumps(detail, ensure_ascii=False, default=str)
        lines.append(line)
    return "\n".join(lines)
