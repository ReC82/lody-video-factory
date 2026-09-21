"""« Réparer le rendu » : refaire UNIQUEMENT l'incrustation des sous-titres d'une production terminée.

Limite du moteur : son API ne sait pas reprendre seulement le montage (``POST /api/v1/videos`` relance tout le
pipeline, donc voix, images et musique payantes). Sa fonction interne ``generate_video`` — celle qu'il a utilisée
pour la production — ne travaille toutefois que sur des **fichiers locaux** : vidéo assemblée, voix, SRT, musique.
Cet outil l'appelle sur les assets déjà générés :

    ``combined-1.mp4`` + ``audio.mp3`` + ``subtitle.srt`` (normalisé) + ``<musique>-bgm-1.mp3``  →  rendu corrigé

Garanties :

* aucun appel fournisseur : le réseau est bloqué pendant le montage (toute tentative lève une erreur) ;
* le rendu original n'est jamais modifié ; une copie vérifiée par empreinte est archivée ;
* le rendu corrigé est un fichier distinct, identifié comme *réparation technique* (``repair/repair.json``) ;
* rien n'est enregistré comme nouvelle production, rien n'est facturé.

Deux exécutions, deux conteneurs (voir ``scripts/lody-repair-render.sh``) :

    python3 repair_render.py run --task-dir <dossier de la tâche> [--font …]    (dans le conteneur du moteur)
    python3 -m lody.generation.repair_render register --production <id> < rapport.json   (dans Lody)
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import shutil
import socket
import sys
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # importé comme module de Lody, ou copié à côté de typography.py dans le conteneur du moteur
    from lody.generation import typography
except ImportError:  # pragma: no cover - chemin du conteneur du moteur
    import typography  # type: ignore[no-redef]

REPAIR_DIR = "repair"
ORIGINAL_COPY = "final-1.original.mp4"
REPAIRED = "final-1-repaired.mp4"
REPAIRED_SRT = "subtitle.repaired.srt"
REPORT = "repair.json"
KIND_REPAIRED = "repaired_video"
KIND_ORIGINAL = "original_video"
SUBTITLE_PARAM_FONT = "font_name"


class RepairError(Exception):
    """Réparation impossible ; le message est lisible et ne contient aucun secret."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@contextlib.contextmanager
def network_blocked() -> Iterator[None]:
    """Toute tentative de connexion réseau lève une erreur : la réparation ne peut appeler aucun fournisseur."""
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("appel réseau interdit pendant la réparation du rendu")

    saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)
    socket.socket.connect = refuse  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = refuse  # type: ignore[method-assign,assignment]
    socket.create_connection = refuse  # type: ignore[assignment]
    socket.getaddrinfo = refuse  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo = saved  # type: ignore[method-assign,assignment]


def _inputs(task_dir: Path) -> dict[str, Path]:
    """Assets existants nécessaires au montage ; erreur claire si l'un manque."""
    script = task_dir / "script.json"
    if not script.is_file():
        raise RepairError("script.json introuvable : la tâche ne peut pas être reconstituée.")
    data = json.loads(script.read_text(encoding="utf-8"))
    bgm_type = str((data.get("params") or {}).get("bgm_type") or "")
    found = {
        "script": script, "video": task_dir / "combined-1.mp4", "audio": task_dir / "audio.mp3",
        "subtitle": task_dir / "subtitle.srt", "original": task_dir / "final-1.mp4",
    }
    if bgm_type:
        candidates = sorted(task_dir.glob(f"{bgm_type}-bgm-1.*"))
        if candidates:
            found["bgm"] = candidates[0]
    missing = [name for name in ("video", "audio", "subtitle", "original") if not found[name].is_file()]
    if missing:
        raise RepairError("Assets manquants pour réparer sans rien régénérer : " + ", ".join(found[m].name for m in missing) + ".")
    return found


def repair(task_dir: Path, *, font: str, engine_params: Callable[[dict[str, Any]], Any],
           generate_video: Callable[..., bool], fonts_dir: Path | None = None,
           now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    """Refait l'incrustation. ``engine_params`` et ``generate_video`` viennent du moteur (ou d'un faux, en test)."""
    files = _inputs(task_dir)
    data = json.loads(files["script"].read_text(encoding="utf-8"))
    params = dict(data.get("params") or {})
    if not params.get("subtitle_enabled", True):
        raise RepairError("Cette production n'a pas de sous-titres incrustés : rien à réparer.")
    if fonts_dir is not None and not (fonts_dir / font).is_file():
        raise RepairError(f"Police introuvable : {font}.")
    out = task_dir / REPAIR_DIR
    out.mkdir(exist_ok=True)

    # 1. l'original est archivé (copie vérifiée) et n'est jamais modifié
    original_hash = _sha256(files["original"])
    archive = out / ORIGINAL_COPY
    if not archive.is_file():
        shutil.copy2(files["original"], archive)
    if _sha256(archive) != original_hash:
        raise RepairError("La copie du rendu original ne correspond pas à l'original : réparation annulée.")

    # 2. sous-titres : SRT du moteur normalisé (protection), original intact
    fixed_srt = out / REPAIRED_SRT
    subtitle_lines_fixed = typography.normalize_subtitle_file(files["subtitle"], fixed_srt)

    # 3. mêmes paramètres qu'à l'origine, police corrigée
    font_before = str(params.get(SUBTITLE_PARAM_FONT, ""))
    params[SUBTITLE_PARAM_FONT] = font
    engine = engine_params(params)

    # 4. montage local uniquement, réseau bloqué
    partial = out / "final-1-repaired.partial.mp4"
    partial.unlink(missing_ok=True)
    with network_blocked():
        bgm_ok = generate_video(
            video_path=str(files["video"]), audio_path=str(files["audio"]), subtitle_path=str(fixed_srt),
            output_file=str(partial), params=engine, bgm_file_override=str(files["bgm"]) if "bgm" in files else None)
    if not partial.is_file() or partial.stat().st_size < 1024:
        raise RepairError("Le montage n'a produit aucun fichier exploitable.")
    if "bgm" in files and not bgm_ok:
        partial.unlink(missing_ok=True)
        raise RepairError("Le mixage de la musique a échoué : le rendu corrigé n'a pas été retenu.")
    target = out / REPAIRED
    partial.replace(target)

    report = {
        "kind": "technical_repair",
        "summary": "Réparation technique : incrustation des sous-titres refaite (espacement des apostrophes).",
        "task_id": task_dir.name,
        "created_at": now().isoformat(timespec="seconds"),
        "provider_calls": 0,
        "reused_assets": [files[k].name for k in ("video", "audio", "subtitle", "bgm") if k in files],
        "original": {"file": ORIGINAL_COPY, "sha256": original_hash, "bytes": files["original"].stat().st_size,
                     "untouched": _sha256(files["original"]) == original_hash},
        "repaired": {"file": REPAIRED, "sha256": _sha256(target), "bytes": target.stat().st_size},
        "font": {"before": font_before, "after": font},
        "subtitle_lines_normalized": subtitle_lines_fixed,
    }
    (out / REPORT).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _engine_repair(task_dir: Path, font: str) -> dict[str, Any]:  # pragma: no cover - exécuté dans le conteneur du moteur
    """Câblage réel : VideoParams et generate_video du moteur (ce conteneur les possède déjà)."""
    from app.models.schema import VideoParams
    from app.services import video
    from app.utils import utils

    names = set(getattr(VideoParams, "model_fields", None) or getattr(VideoParams, "__pydantic_fields__", {}))
    return repair(
        task_dir, font=font, fonts_dir=Path(utils.font_dir()), generate_video=video.generate_video,
        engine_params=lambda params: VideoParams(**{k: v for k, v in params.items() if k in names}))


def register(production_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """Enregistre le rendu corrigé comme assets de la production (dans Lody). Idempotent ; V7 elle-même reste intacte."""
    from lody import settings
    from lody.generation import mpt_connector
    from lody.generation.models import ExternalTask
    from lody.generation.store import ProductionRepository

    repo = ProductionRepository(settings.db_path())
    production = repo.get(production_id)
    if production.status.value != "TERMINEE" or not production.external_task_id:
        raise RepairError("Seule une production terminée peut recevoir un rendu corrigé.")
    if report.get("task_id") != production.external_task_id or report.get("kind") != "technical_repair":
        raise RepairError("Ce rapport ne correspond pas à cette production.")
    connector = mpt_connector.build_from_environment()
    task = ExternalTask(production.provider, production.external_task_id)
    refs = {KIND_REPAIRED: f"tasks/{task.task_id}/{REPAIR_DIR}/{REPAIRED}",
            KIND_ORIGINAL: f"tasks/{task.task_id}/{REPAIR_DIR}/{ORIGINAL_COPY}"}
    for ref in refs.values():
        try:
            connector.resolve_asset(task, ref)  # existe, appartient à la tâche, chemin sûr
        except ValueError as error:
            raise RepairError(f"Fichier de réparation invalide ({error}).") from error
    kept = [dict(asset) for asset in production.assets if asset.get("kind") not in refs]
    note = report.get("summary", "")
    kept += [{"kind": kind, "ref": ref, "note": note} for kind, ref in refs.items()]
    warning = "Rendu corrigé disponible (réparation technique des sous-titres) ; le rendu original est conservé."
    warnings = [w for w in production.warnings if not w.startswith("Rendu corrigé disponible")] + [warning]
    repo.update(production_id, assets=kept, warnings=warnings)
    return {"production": production_id, "assets": [a["kind"] for a in kept]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Réparer le rendu (sous-titres) sans aucun appel fournisseur.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="refait l'incrustation (à lancer dans le conteneur du moteur)")
    run.add_argument("--task-dir", type=Path, required=True)
    run.add_argument("--font", default=typography.LATIN_SAFE_FONT)
    reg = sub.add_parser("register", help="enregistre le rendu corrigé dans Lody (rapport JSON sur stdin)")
    reg.add_argument("--production", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            print(json.dumps(_engine_repair(args.task_dir, args.font), ensure_ascii=False))
        else:
            print(json.dumps(register(args.production, json.loads(sys.stdin.read())), ensure_ascii=False))
    except RepairError as error:
        print(f"Réparation impossible : {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
