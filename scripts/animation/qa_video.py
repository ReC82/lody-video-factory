"""Contrôle qualité automatique d'un MP4 Remotion 9:16 (#166), à la demande.

    uv run --no-sync python scripts/animation/qa_video.py <dossier de rendu | fichier.mp4> [--out DOSSIER]

Le dossier est soit celui d'une tâche du worker 2D (``out.mp4`` + ``job.json`` + ``manifest.json``), soit un
dossier versionné comme ``docs/animation/voltix-150`` (``*.mp4`` + ``storyboard.json`` + ``asset-info.json``).
Écrit ``qa.json`` et ``index.html`` (rapport autonome à ouvrir dans un navigateur) dans ``<dossier>/qa`` par
défaut. Local et gratuit : ffmpeg/ffprobe seulement, aucun appel réseau. Code de sortie 1 s'il y a une erreur.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT)]

from lody.animation import video_qa  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Contrôle qualité automatique d'un rendu 2D (#166).")
    parser.add_argument("target", type=Path, help="dossier du rendu ou fichier MP4")
    parser.add_argument("--out", type=Path, default=None, help="dossier du rapport (défaut : <dossier>/qa)")
    parser.add_argument("--ffmpeg", default=None, help="exécutable ffmpeg (défaut : celui du PATH)")
    args = parser.parse_args(argv)
    try:
        report = video_qa.run(args.target, args.out, ffmpeg=args.ffmpeg)
    except ValueError as error:
        print(f"Contrôle impossible : {error}", file=sys.stderr)
        return 2
    counts = report["summary"]
    for item in report["findings"]:
        print(f"[{item['level']:7}] {item['message']}")
    target = args.target if args.target.is_dir() else args.target.parent
    out = args.out or target / video_qa.QA_DIR
    print(f"\n{counts['error']} erreur(s), {counts['warning']} à vérifier, {counts['info']} info(s) — "
          f"rapport : {out / 'index.html'}")
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
