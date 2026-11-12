"""Planches de contrôle des rigs de Nora et Sam (#173), composées avec ``rig_pose.apply_pose``.

Usage : ``python scripts/animation/planche_personnages.py`` → ``docs/animation/<nom>/planche-<nom>.svg``
(le PNG versionné à côté en est une capture : ``--png`` le régénère avec un Chromium sans interface local, s'il
est installé). Bibliothèque standard uniquement ; aucun réseau.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESOURCE = ROOT / "resource"
DOCS = ROOT / "docs" / "animation"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rig_pose = _module("rig_pose")
humain = _module("humain_rig")

FIGURES = [
    ("face", None, None, "Face — neutre"),
    ("trois-quarts", None, None, "3/4 — neutre"),
    ("face", "sourire", None, "Sourire"),
    ("face", "surprise", None, "Surprise"),
    ("face", "confusion", None, "Confusion"),
    ("face", "concentration", None, "Concentration"),
    ("face", "sourire", "saluer", "Geste : saluer"),
    ("face", "neutre", "montrer", "Geste : montrer"),
    ("face", "concentration", "tenir-cable", "Geste : tenir le câble"),
    ("face", "sourire", "expliquer", "Geste : expliquer"),
    ("trois-quarts", "neutre", "pencher-tete", "3/4 — pencher la tête"),
    ("trois-quarts", "sourire", "montrer", "3/4 — montrer"),
]


def build_sheet(key: str, resource: Path = RESOURCE) -> str:
    c = humain.CHARACTERS[key]
    base = resource / "animation-assets" / key / "v1"
    rig = json.loads((base / f"{key}-rig.json").read_text(encoding="utf-8"))
    views = {name: (base / f"{key}-{name}.svg").read_text(encoding="utf-8") for name in humain.VIEWS}
    _, props = rig_pose._load_catalog(resource)  # accessoires partagés (câble SDI…) du catalogue Voltix
    width, cw, ch = 1200, 300, 480
    rows = (len(FIGURES) + 3) // 4
    visemes_y = 110 + rows * ch
    height = visemes_y + 250
    root = ET.Element(rig_pose._tag("svg"), {"viewBox": f"0 0 {width} {height}", "width": str(width),
                                              "height": str(height)})
    ET.SubElement(root, rig_pose._tag("title")).text = f"{c['name']} — planche de contrôle du rig v1 (#173)"
    ET.SubElement(root, rig_pose._tag("rect"), {"width": str(width), "height": str(height), "fill": "#FFFFFF"})
    rig_pose._text(root, width / 2, 50, f"{c['name']}, {c['role']} — planche de contrôle du rig v1", 32, "700")
    rig_pose._text(root, width / 2, 84, "Personnage fictif de laboratoire — à valider. Générée depuis le rig SVG.",
                   17, "400")
    for i, (view, expression, gesture, label) in enumerate(FIGURES):
        figure = rig_pose.apply_pose(views[view], rig, rig_pose.pose_for(rig, expression, gesture), props=props)
        rig_pose.prefix_ids(figure, f"c{i}-")
        rig_pose._cell(root, figure, (i % 4) * cw, 100 + (i // 4) * ch, cw, ch, label, view_box="-80 30 760 870")

    rig_pose._text(root, width / 2, visemes_y + 6, "Visèmes français (bouche pilotée par la voix)", 22, "700")
    hx, hy, rx, ry = c["head"]
    names = list(rig["visemes"])
    vw = width / len(names)
    for i, name in enumerate(names):
        mouth = rig["visemes"][name].get("mouth", name)
        figure = rig_pose.apply_pose(views["face"], rig, rig_pose.merge(rig["rest"], {"select": {"mouth-visemes": mouth}}))
        rig_pose.prefix_ids(figure, f"v{i}-")
        box = f"{hx - rx - 10} {hy - ry - 10} {2 * rx + 20} {2 * ry + 20}"
        rig_pose._cell(root, figure, i * vw, visemes_y + 20, vw, 220, name, view_box=box)
    return ET.tostring(root, encoding="unicode") + "\n"


def sheet_path(key: str) -> Path:
    return DOCS / key / f"planche-{key}.svg"


def _chromium() -> str | None:
    found = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if found:
        return found
    cache = Path.home() / ".cache" / "ms-playwright"
    shells = sorted(cache.glob("chromium_headless_shell-*/chrome-*/chrome-headless-shell"))
    return str(shells[-1]) if shells else None


def to_png(svg: Path) -> Path:
    """Capture PNG de la planche avec un Chromium sans interface local (aucun réseau)."""
    binary = _chromium()
    if not binary:
        raise SystemExit("Aucun Chromium local : PNG non régénéré.")
    root = ET.parse(svg).getroot()
    width, height = root.get("width"), root.get("height")
    png = svg.with_suffix(".png")
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "page.html"
        page.write_text(f"<html><body style='margin:0'>{svg.read_text(encoding='utf-8')}</body></html>",
                        encoding="utf-8")
        subprocess.run([binary, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                        f"--user-data-dir={tmp}/profil", f"--window-size={width},{height}",
                        f"--screenshot={png}", page.as_uri()], check=True, capture_output=True,
                       env={**os.environ, "HOME": tmp})
    return png


if __name__ == "__main__":
    for key in humain.CHARACTERS:
        path = sheet_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build_sheet(key), encoding="utf-8")
        print(path.relative_to(ROOT))
        if "--png" in sys.argv:
            print(to_png(path).relative_to(ROOT))
    sys.exit(0)
