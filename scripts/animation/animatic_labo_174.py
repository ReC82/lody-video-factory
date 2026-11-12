"""Animatic local et gratuit du projet expérimental #174 : storyboard → moteur Remotion, scène par scène, sans voix.

Lit ``docs/episodes/labo-camera-moniteur/storyboard.json``, épingle chaque asset en version 1, écrit une entrée
``lody.animation-scene/1`` par scène (le format que le worker #120 passe au moteur), appelle
``renderers/remotion`` puis assemble les scènes avec ffmpeg. Muet : la bouche suit le texte des répliques et les
sous-titres affichent le texte oral. Aucun fournisseur externe, aucune voix, aucune donnée de production.

Prérequis : ``npm ci`` dans ``renderers/remotion`` ; fichiers de Nora et Sam (#173) sous
``<people-root>/animation-assets/{nora,sam}/v1/`` avec ``labo-personnages-catalogue.json`` (par défaut le
dossier ``resource/`` du dépôt, une fois la PR #173 fusionnée).

Usage : ``python scripts/animation/animatic_labo_174.py --out /tmp/labo-174 [--people-root DIR] [--still N]``
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "resource"
STORYBOARD = ROOT / "docs" / "episodes" / "labo-camera-moniteur" / "storyboard.json"
CLI = ROOT / "renderers" / "remotion" / "bin" / "lody-remotion.mjs"


def _catalog_entries(root: Path, name: str) -> dict[str, tuple[dict, Path]]:
    catalog = json.loads((root / "animation-assets" / name).read_text(encoding="utf-8"))
    return {asset["id"]: (asset, root) for asset in catalog["assets"]}


def scene_inputs(people_root: Path) -> list[dict]:
    storyboard = json.loads(STORYBOARD.read_text(encoding="utf-8"))
    entries = {**_catalog_entries(people_root, "labo-personnages-catalogue.json"),
               **_catalog_entries(RESOURCE, "labo-174-catalogue.json")}
    inputs = []
    for index, scene in enumerate(storyboard["scenes"], 1):
        assets, info = {}, {}
        for layer in scene["render"]["layers"]:
            for obj in layer["objects"]:
                if obj["type"] != "asset":
                    continue
                obj["asset"]["version"] = 1
                asset, root = entries[obj["asset"]["id"]]
                manifest = asset["versions"][0]["manifest"]
                key = f"{asset['id']}@1"
                assets[key] = {item["role"]: str(root / item["path"]) for item in manifest["files"]
                               if item["role"] in ("base", "rig")}
                info[key] = {"kind": asset["kind"], "width": manifest["width"], "height": manifest["height"],
                             "anchor": manifest["anchor"],
                             "slug": Path(manifest["files"][0]["path"]).parent.parent.name}
        inputs.append({"schema": "lody.animation-scene/1", "canvas": storyboard["canvas"], "index": index,
                       "scene": scene, "assets": assets, "asset_info": info})
    return inputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--people-root", type=Path, default=RESOURCE,
                        help="dossier contenant animation-assets/ de Nora et Sam (#173)")
    parser.add_argument("--cli", type=Path, default=CLI, help="moteur lody-remotion.mjs à utiliser")
    parser.add_argument("--still", type=int, help="une image PNG au milieu de chaque scène, au lieu du MP4",
                        nargs="?", const=-1)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    parts = []
    for data in scene_inputs(args.people_root):
        scene = data["scene"]
        source = args.out / f"{data['index']:02d}-{scene['id']}-input.json"
        source.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        command = ["node", str(args.cli), "--input", str(source)]
        if args.still is not None:
            frame = scene["duration_frames"] // 2 if args.still < 0 else args.still
            target = args.out / f"{data['index']:02d}-{scene['id']}.png"
            command += ["--still", str(frame), "--output", str(target)]
        else:
            target = args.out / f"{data['index']:02d}-{scene['id']}.mp4"
            command += ["--output", str(target)]
        if subprocess.run(command, check=False).returncode:
            return 1
        parts.append(target)
    if args.still is None:
        listing = args.out / "scenes.txt"
        listing.write_text("".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8")
        final = args.out / "labo-174-animatic.mp4"
        result = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
                                 str(listing), "-c", "copy", str(final)], check=False)
        if result.returncode:
            return 1
        print(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
