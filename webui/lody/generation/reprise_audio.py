"""« Reprendre l'audio » : refaire UNIQUEMENT la voix d'une production terminée (#92, direction vocale par
réplique), en réutilisant les images déjà générées — jamais régénérées, jamais un nouvel appel au
fournisseur d'images.

Limite du moteur : son API ne sait pas reprendre seulement la voix (``POST /api/v1/videos`` relance tout le
pipeline, donc images et musique payantes en plus de la voix). Ses fonctions internes (celles qu'il a
utilisées pour la production d'origine) ne travaillent toutefois que sur des **fichiers locaux** une fois
l'audio produit : montage et incrustation. Cet outil les enchaîne sur un NOUVEL audio réel :

    (nouvel) audio.mp3 + sous-titres recalculés sur ses timings réels + LES MÊMES clips image déjà
    générés (copiés, jamais régénérés)  →  nouveau montage  →  nouveau rendu final

Garanties :

* UN SEUL appel fournisseur : la voix (ElevenLabs). Aucun appel image, aucun nouveau script.
* le script et le storyboard validés restent EXACTEMENT ceux de la production d'origine — seul le texte
  envoyé à la voix peut porter des balises de jeu (``[hesitates]``...), jamais les sous-titres ;
* les clips image sont COPIÉS (jamais déplacés ni modifiés) dans le nouveau dossier de tâche, pour que la
  nouvelle production reste résoluble par tous les mécanismes existants (suivi, export) ;
* la production d'origine n'est jamais modifiée ; le résultat devient une NOUVELLE VERSION (même lignée,
  ``parent_production_id``), pour comparer les deux à l'écoute.

Deux exécutions, deux conteneurs (voir ``scripts/lody-reprise-audio.sh``) :

    python3 reprise_audio.py run --task-dir <tâche d'origine> --spoken-text-file <f> --subtitle-text-file <f>
        --voice-id <id> [--model-id ...] [--stability ...] ...   (dans le conteneur du moteur, appel réel)
    python3 -m lody.generation.reprise_audio register --production <id> < rapport.json   (dans Lody)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import socket
import sys
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

KIND_AUDIO = "audio"
KIND_SUBTITLE = "subtitle"
KIND_CLIP = "clip"
REPORT_KIND = "voice_reprise"


class ReprisError(Exception):
    """Reprise impossible ; le message est lisible et ne contient aucun secret."""


@contextlib.contextmanager
def _allow_network_only_for(allowed_host_suffixes: tuple[str, ...]) -> Iterator[None]:
    """Défense en profondeur symétrique à ``repair_render.network_blocked`` : ICI un appel réseau est
    attendu (la voix), mais seulement vers le fournisseur de voix — jamais vers un autre hôte (ex. un
    fournisseur d'images appelé par erreur par du code réutilisé)."""
    real_create_connection = socket.create_connection

    def guarded(address: Any, *args: Any, **kwargs: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else str(address)
        if not any(host.endswith(suffix) for suffix in allowed_host_suffixes):
            raise RuntimeError(f"appel réseau interdit pendant la reprise audio : hôte non autorisé ({host})")
        return real_create_connection(address, *args, **kwargs)

    socket.create_connection = guarded  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.create_connection = real_create_connection  # type: ignore[assignment]


def _inputs(task_dir: Path) -> dict[str, Any]:
    """Assets existants nécessaires ; erreur claire si l'un manque. Les clips sont triés comme
    ``mpt_connector.list_scene_images`` (date d'écriture, puis nom) : même ordre que les scènes."""
    script = task_dir / "script.json"
    if not script.is_file():
        raise ReprisError("script.json introuvable : la tâche d'origine ne peut pas être reconstituée.")
    data = json.loads(script.read_text(encoding="utf-8"))
    clips = sorted(
        task_dir.glob("openai-image-*.png.mp4"),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
    )
    if not clips:
        raise ReprisError("Aucune image déjà générée trouvée : rien à réutiliser pour cette tâche.")
    return {"script_json": data, "clips": clips}


def reprise(
    task_dir: Path,
    *,
    spoken_text: str,
    subtitle_text: str,
    voice_id: str,
    model_id: str,
    voice_settings: dict[str, Any],
    elevenlabs_tts: Callable[..., Any],
    get_audio_duration: Callable[[str], float],
    generate_subtitle: Callable[..., Any],
    combine_videos: Callable[..., Any],
    generate_video: Callable[..., bool],
    make_video_params: Callable[[dict[str, Any]], Any],
    task_dir_for: Callable[[str], str],
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Refait la voix. Les callables viennent du moteur (ou de faux, en test)."""
    files = _inputs(task_dir)
    params_dict = dict(files["script_json"].get("params") or {})
    new_task_id = str(uuid.uuid4())
    new_dir = Path(task_dir_for(new_task_id))
    new_dir.mkdir(parents=True, exist_ok=True)

    # 1. voix RÉELLE (seul appel fournisseur de cette reprise) — balises envoyées, jamais aux sous-titres.
    audio_path = new_dir / "audio.mp3"
    sub_maker = elevenlabs_tts(
        text=spoken_text, voice_id=voice_id, voice_file=str(audio_path), model_id=model_id,
        voice_settings_override=voice_settings, subtitle_text=subtitle_text,
    )
    if sub_maker is None or not audio_path.is_file():
        raise ReprisError("La synthèse vocale a échoué : voir les journaux du moteur pour la réponse du fournisseur.")
    audio_duration = get_audio_duration(str(audio_path))
    if not audio_duration or audio_duration <= 0:
        raise ReprisError("Durée audio nulle ou invalide : reprise annulée, rien n'a été enregistré.")

    # 2. clips image COPIÉS (jamais régénérés) dans le nouveau dossier, pour rester résolubles partout.
    copied_clips: list[Path] = []
    for clip in files["clips"]:
        target = new_dir / clip.name
        shutil.copy2(clip, target)
        png = clip.with_suffix("")  # "...png.mp4" -> "...png"
        if png.is_file():
            shutil.copy2(png, new_dir / png.name)
        copied_clips.append(target)

    # 3. sous-titres : MÊME fonction que la production normale, texte propre (sans balise), timing réel.
    video_params = make_video_params(params_dict)
    subtitle_path = generate_subtitle(new_task_id, video_params, subtitle_text, sub_maker, str(audio_path))

    # 4. montage : mêmes clips, nouvelle durée audio réelle — match_materials_to_script (déjà envoyé par
    # Lody) répartit équitablement la durée entre les dix scènes, aucune n'est abandonnée (#92).
    combined_path = new_dir / "combined-1.mp4"
    combine_videos(
        combined_video_path=str(combined_path), video_paths=[str(p) for p in copied_clips],
        audio_file=str(audio_path), video_aspect=params_dict.get("video_aspect", "9:16"),
        video_fit_mode=params_dict.get("video_fit_mode", "cover"),
        video_concat_mode=params_dict.get("video_concat_mode", "sequential"),
        video_transition_mode=params_dict.get("video_transition_mode"),
        max_clip_duration=params_dict.get("video_clip_duration", 6),
        threads=params_dict.get("n_threads", 2), clip_speed=params_dict.get("video_clip_speed", 1.0),
        match_materials_to_script=bool(params_dict.get("match_materials_to_script"))
        and params_dict.get("video_source") == "openai_image",
    )
    if not combined_path.is_file():
        raise ReprisError("Le montage n'a produit aucun fichier exploitable.")

    # 5. rendu final : incrustation des sous-titres, mêmes réglages visuels que l'original.
    final_path = new_dir / "final-1.mp4"
    generate_video(
        video_path=str(combined_path), audio_path=str(audio_path), subtitle_path=subtitle_path or "",
        output_file=str(final_path), params=video_params,
    )
    if not final_path.is_file() or final_path.stat().st_size < 1024:
        raise ReprisError("Le rendu final n'a produit aucun fichier exploitable.")

    report = {
        "kind": REPORT_KIND,
        "summary": "Nouvelle version : voix reprise (direction vocale eleven_v3), images réutilisées à l'identique.",
        "source_task_id": task_dir.name,
        "new_task_id": new_task_id,
        "created_at": now().isoformat(timespec="seconds"),
        "provider_calls": 1,
        "reused_clip_count": len(copied_clips),
        "voice_direction": {
            "voice_id": voice_id, "model_id": model_id, "voice_settings": voice_settings,
            "spoken_text_sent": spoken_text, "subtitle_text": subtitle_text,
        },
        "audio_duration_s": audio_duration,
        "video_ref": f"tasks/{new_task_id}/final-1.mp4",
        "assets": [
            {"kind": KIND_AUDIO, "ref": f"tasks/{new_task_id}/audio.mp3"},
            *([{"kind": KIND_SUBTITLE, "ref": f"tasks/{new_task_id}/subtitle.srt"}] if subtitle_path else []),
            *[{"kind": KIND_CLIP, "ref": f"tasks/{new_task_id}/{p.name}"} for p in copied_clips],
        ],
    }
    (new_dir / "reprise.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _combine_videos_with_enum_concat_mode(combine_videos: Callable[..., Any], concat_mode_cls: Any,
                                          **kwargs: Any) -> Any:
    """``combine_videos()`` accède à ``video_concat_mode.value`` directement (jamais ``getattr`` défensif,
    contrairement à ``video_transition_mode``) — même conversion que ``task.py:_run_pipeline`` avant
    d'appeler ``generate_final_videos()``, nécessaire ici puisque ``script.json`` stocke la valeur brute."""
    mode = kwargs.get("video_concat_mode")
    if isinstance(mode, str):
        kwargs["video_concat_mode"] = concat_mode_cls(mode)
    return combine_videos(**kwargs)


def _engine_reprise(
    task_dir: Path, *, spoken_text: str, subtitle_text: str, voice_id: str, model_id: str,
    voice_settings: dict[str, Any],
) -> dict[str, Any]:  # pragma: no cover - exécuté dans le conteneur du moteur
    """Câblage réel : fonctions du moteur (ce conteneur les possède déjà)."""
    from functools import partial

    from app.models.schema import VideoConcatMode, VideoParams
    from app.services import task as task_service
    from app.services import video
    from app.services import voice as voice_service
    from app.utils import utils

    names = set(getattr(VideoParams, "model_fields", None) or getattr(VideoParams, "__pydantic_fields__", {}))
    combine_videos = partial(_combine_videos_with_enum_concat_mode, video.combine_videos, VideoConcatMode)

    with _allow_network_only_for((".elevenlabs.io",)):
        return reprise(
            task_dir, spoken_text=spoken_text, subtitle_text=subtitle_text, voice_id=voice_id,
            model_id=model_id, voice_settings=voice_settings,
            elevenlabs_tts=voice_service.elevenlabs_tts, get_audio_duration=voice_service.get_audio_duration,
            generate_subtitle=task_service.generate_subtitle, combine_videos=combine_videos,
            generate_video=video.generate_video,
            make_video_params=lambda params: VideoParams(**{k: v for k, v in params.items() if k in names}),
            task_dir_for=utils.task_dir,
        )


def register(production_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """Enregistre le résultat comme une NOUVELLE VERSION de la production (dans Lody) — l'originale reste
    intacte et reste consultable pour comparer à l'écoute."""
    from lody import settings
    from lody.generation.models import ProductionStatus
    from lody.generation.store import ProductionRepository

    if report.get("kind") != REPORT_KIND:
        raise ReprisError("Ce rapport ne correspond pas à une reprise audio.")
    repo = ProductionRepository(settings.db_path())
    original = repo.get(production_id)
    if original.status != ProductionStatus.TERMINEE or not original.external_task_id:
        raise ReprisError("Seule une production terminée peut recevoir une reprise audio.")
    if report.get("source_task_id") != original.external_task_id:
        raise ReprisError("Ce rapport ne correspond pas à cette production.")

    voice_direction = report.get("voice_direction") or {}
    trace = {
        **original.trace,
        "voice_direction": {
            "applied": True, "model_id": voice_direction.get("model_id", ""),
            "voice_settings": voice_direction.get("voice_settings", {}),
            "spoken_text_sent": voice_direction.get("spoken_text_sent", ""),
            "subtitle_text": voice_direction.get("subtitle_text", ""),
        },
    }
    created = repo.create(
        project_id=original.project_id, subject=original.subject, provider=original.provider,
        status=ProductionStatus.TERMINEE, parent_production_id=production_id,
        brief=original.brief, script=original.script, script_source=original.script_source,
        storyboard=original.storyboard, visual_prompts=original.visual_prompts, params=original.params,
        snapshot=original.snapshot,
        cost_currency=original.cost_currency, cost_low=original.cost_low, cost_high=original.cost_high,
        cost_partial=original.cost_partial, cost_detail=original.cost_detail,
        confirmed_at=report["created_at"], external_task_id=report["new_task_id"],
        started_at=report["created_at"], finished_at=report["created_at"],
        video_ref=report["video_ref"], video_duration=float(report.get("audio_duration_s") or 0.0),
        assets=report.get("assets", []),
        warnings=[report.get("summary", "Nouvelle version : voix reprise.")],
        trace=trace,
    )
    return {"production": created.id, "version": created.version, "parent": production_id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reprendre UNIQUEMENT la voix d'une production, images réutilisées.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="refait la voix (à lancer dans le conteneur du moteur)")
    run.add_argument("--task-dir", type=Path, required=True)
    run.add_argument("--spoken-text-file", type=Path, required=True)
    run.add_argument("--subtitle-text-file", type=Path, required=True)
    run.add_argument("--voice-id", required=True)
    run.add_argument("--model-id", required=True)
    run.add_argument("--stability", type=float, required=True)
    run.add_argument("--similarity-boost", type=float, required=True)
    run.add_argument("--style", type=float, required=True)
    run.add_argument("--speed", type=float, required=True)
    run.add_argument("--speaker-boost", type=lambda v: v.lower() in ("1", "true", "yes"), required=True)
    reg = sub.add_parser("register", help="enregistre la nouvelle version dans Lody (rapport JSON sur stdin)")
    reg.add_argument("--production", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            report = _engine_reprise(
                args.task_dir,
                spoken_text=args.spoken_text_file.read_text(encoding="utf-8"),
                subtitle_text=args.subtitle_text_file.read_text(encoding="utf-8"),
                voice_id=args.voice_id, model_id=args.model_id,
                voice_settings={
                    "stability": args.stability, "similarity_boost": args.similarity_boost,
                    "style": args.style, "use_speaker_boost": args.speaker_boost, "speed": args.speed,
                },
            )
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(json.dumps(register(args.production, json.loads(sys.stdin.read())), ensure_ascii=False))
    except ReprisError as error:
        print(f"Reprise impossible : {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
