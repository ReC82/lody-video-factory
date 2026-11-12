"""Planche de contrôle des quatre décors du laboratoire (#175).

Usage : ``python scripts/animation/planche_decors.py [--png]`` → ``docs/animation/decors-labo/planche-decors.svg``
(et ``.png`` avec ``--png``, capturé par un Chromium sans interface local ; aucun réseau).

Pour chaque décor : à gauche le décor seul, tel qu'il sera rendu ; à droite le même décor annoté — bande des
sous-titres (hachurée), zone de titre, emplacements des personnages (silhouette à la hauteur conseillée) et
boîtes des points d'intérêt de la fiche ``-scene.json``. Bibliothèque standard uniquement.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESOURCE = ROOT / "resource"
OUT = ROOT / "docs" / "animation" / "decors-labo" / "planche-decors.svg"


def _module(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


decors = _module("decors_labo")

SCALE = 0.4
CW, CH = round(decors.W * SCALE), round(decors.H * SCALE)
GAP, TOP, LABEL = 24, 96, 40


def _inner(svg_text: str) -> str:
    """Contenu d'un SVG de décor (sans l'élément racine ni le titre) pour l'imbriquer."""
    body = svg_text.split(">", 1)[1].rsplit("</svg>", 1)[0]
    return re.sub(r"<title>.*?</title>", "", body)


def _annotations(scene: dict) -> str:
    z = scene["zones"]["subtitles"]
    t = scene["zones"]["title"]
    out = [f'<rect x="{z["x"]}" y="{z["y"]}" width="{z["width"]}" height="{z["height"]}" fill="url(#hachures)" '
           'stroke="#FFFFFF" stroke-width="6" stroke-dasharray="24 12"/>',
           f'<rect x="{t["x"]}" y="{t["y"]}" width="{t["width"]}" height="{t["height"]}" fill="none" '
           'stroke="#FFFFFF" stroke-width="5" stroke-dasharray="16 12"/>']
    for spot in scene["hotspots"]:
        x, y, w, h = spot["bbox"]
        color = "#FF9F1C" if spot["layer"] == "objets" else "#E040FB"
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{color}" stroke-width="5"/>')
        out.append(f'<circle cx="{spot["point"][0]}" cy="{spot["point"][1]}" r="9" fill="{color}"/>')
    for place in scene["places"]:
        x, y, h = place["x"], place["y"], place["height"]
        out.append(f'<rect x="{x - h * 0.2}" y="{y - h}" width="{h * 0.4}" height="{h}" rx="{h * 0.2}" '
                   'fill="#00E5FF" fill-opacity="0.1" stroke="#00E5FF" stroke-width="6"/>')
        out.append(f'<line x1="{x - 70}" y1="{y}" x2="{x + 70}" y2="{y}" stroke="#00E5FF" stroke-width="10"/>')
    return "".join(out)


def build_sheet(resource: Path = RESOURCE) -> str:
    specs = decors.specs()
    width = GAP * 3 + CW * 2
    height = TOP + len(specs) * (CH + LABEL + GAP)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">',
             '<defs><pattern id="hachures" width="40" height="40" patternUnits="userSpaceOnUse">'
             '<rect width="40" height="40" fill="#FFFFFF" fill-opacity="0.12"/>'
             '<line x1="0" y1="40" x2="40" y2="0" stroke="#FFFFFF" stroke-opacity="0.6" stroke-width="6"/>'
             '</pattern></defs>',
             f'<rect width="{width}" height="{height}" fill="#F1F3F6"/>',
             f'<text x="{GAP}" y="44" font-family="DejaVu Sans, sans-serif" font-size="26" font-weight="700" '
             'fill="#22262E">Décors du laboratoire v1 (#175)</text>',
             f'<text x="{GAP}" y="78" font-family="DejaVu Sans, sans-serif" font-size="14" fill="#22262E">'
             'Gauche : rendu seul. Droite : sous-titres (hachures), titre (tirets), places (cyan), '
             'points d’intérêt (orange ; avant-plan en violet).</text>']
    for row, spec in enumerate(specs):
        slug = spec["slug"]
        base = resource / "animation-assets" / slug / "v1"
        full = _inner((base / f"{slug}.svg").read_text(encoding="utf-8"))
        scene = json.loads((base / f"{slug}-scene.json").read_text(encoding="utf-8"))
        y = TOP + row * (CH + LABEL + GAP)
        parts.append(f'<text x="{GAP}" y="{y + 28}" font-family="DejaVu Sans, sans-serif" font-size="22" '
                     f'font-weight="700" fill="#22262E">{spec["name"]}</text>')
        for col, extra in enumerate(("", _annotations(scene))):
            x = GAP + col * (CW + GAP)
            parts.append(f'<svg x="{x}" y="{y + LABEL}" width="{CW}" height="{CH}" '
                         f'viewBox="0 0 {decors.W} {decors.H}">{full}{extra}</svg>')
            parts.append(f'<rect x="{x}" y="{y + LABEL}" width="{CW}" height="{CH}" fill="none" stroke="#22262E" '
                         'stroke-width="2"/>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _chromium() -> str | None:
    found = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if found:
        return found
    shells = sorted((Path.home() / ".cache" / "ms-playwright").glob(
        "chromium_headless_shell-*/chrome-*/chrome-headless-shell"))
    return str(shells[-1]) if shells else None


def to_png(svg: Path) -> Path:
    """Capture PNG avec un Chromium sans interface local (aucun réseau)."""
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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_sheet(), encoding="utf-8")
    print(OUT.relative_to(ROOT))
    if "--png" in sys.argv:
        print(to_png(OUT).relative_to(ROOT))
    sys.exit(0)
