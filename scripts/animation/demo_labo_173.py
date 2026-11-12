"""Courte animation locale de contrôle : Nora et Sam dans une même scène, rendue par le moteur Remotion (#173).

Écrit une entrée de scène ``lody.animation-scene/1`` (le format que le worker #120 passe au moteur) avec les
deux marionnettes et le câble SDI de Voltix comme accessoire, puis appelle ``renderers/remotion`` : MP4
muet de 4 s en 1080 × 1920. Aucun fournisseur externe, aucune voix (la bouche suit le texte des répliques),
aucune donnée de production. Prérequis : ``npm ci`` dans ``renderers/remotion`` (Chromium de Remotion).

Usage : ``python scripts/animation/demo_labo_173.py --out /tmp/labo-173 [--still 45]``
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "resource" / "animation-assets"
FPS, WIDTH, HEIGHT, FRAMES = 30, 1080, 1920, 120


def _puppet_files(key: str) -> dict[str, str]:
    base = ASSETS / key / "v1"
    return {"base": str(base / f"{key}-face.svg"), "rig": str(base / f"{key}-rig.json")}


def scene_input() -> dict:
    info = {"width": 600, "height": 900, "anchor": {"x": 0.5, "y": 0.98}, "kind": "character"}
    nora = {"id": "nora", "type": "asset", "asset": {"id": "ast_173000000001", "version": 1},
            "position": [{"frame": 0, "x": 320, "y": 1640}], "scale": [{"frame": 0, "value": 1.45}],
            "expressions": [{"frame": 0, "name": "sourire"}, {"frame": 70, "name": "neutre"}],
            "gestures": [{"start": 4, "end": 34, "name": "saluer"}, {"start": 70, "end": 100, "name": "expliquer"}],
            "lipsync": True}
    sam = {"id": "sam", "type": "asset", "asset": {"id": "ast_173000000002", "version": 1},
           "position": [{"frame": 0, "x": 770, "y": 1640}], "scale": [{"frame": 0, "value": 1.45}],
           "expressions": [{"frame": 0, "name": "neutre"}, {"frame": 50, "name": "concentration"}],
           "gestures": [{"start": 40, "end": 110, "name": "tenir-cable"}],
           "lipsync": False}
    scene = {
        "id": "labo", "duration_frames": FRAMES,
        "intent": {"narration": "Bonjour, moi c'est Nora. Et voici Sam."},
        "render": {
            "layers": [{"id": "personnages", "objects": [nora, sam]}],
            "speech": {"speaker": "nora", "cues": [{"text": "Bonjour, moi c'est Nora.", "start": 6, "end": 40},
                                                   {"text": "Et voici Sam !", "start": 74, "end": 98}]},
            "fallback": {"type": "still", "frame": 60},
        },
    }
    return {
        "schema": "lody.animation-scene/1", "canvas": {"fps": FPS, "width": WIDTH, "height": HEIGHT}, "index": 1,
        "scene": scene,
        "assets": {"ast_173000000001@1": _puppet_files("nora"), "ast_173000000002@1": _puppet_files("sam"),
                   "ast_136000000002@1": {"base": str(ASSETS / "cable-sdi/v1/cable-sdi.svg")}},
        "asset_info": {"ast_173000000001@1": info, "ast_173000000002@1": info,
                       "ast_136000000002@1": {"kind": "prop", "slug": "cable-sdi", "width": 420, "height": 300,
                                              "anchor": {"x": 0.5, "y": 0.5}}},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--still", type=int, help="image unique (PNG) au lieu du MP4")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    source = args.out / "labo-input.json"
    source.write_text(json.dumps(scene_input(), ensure_ascii=False, indent=2), encoding="utf-8")
    cli = ROOT / "renderers" / "remotion" / "bin" / "lody-remotion.mjs"
    command = ["node", str(cli), "--input", str(source)]
    if args.still is not None:
        command += ["--still", str(args.still), "--output", str(args.out / f"labo-{args.still}.png")]
    else:
        command += ["--output", str(args.out / "labo-173.mp4")]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
