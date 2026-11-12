"""Pose d'une marionnette SVG décrite par un rig ``lody.rig/1`` et planche de contrôle de Voltix (#136).

Le rig (``voltix-rig.json``) ne contient que des **données** : rotations autour des pivots déclarés dans le
SVG (``data-pivot``), choix de variantes (``data-variants`` / ``data-variant``), regard, compression des
ressorts (``lift``, ignorés si la marionnette n'en a pas), écrasement du haut du corps (``squash``), saut
(``air``) et accessoires accrochés à des points ``data-slot``. Ce module applique une pose à un SVG et compose la planche de contrôle ; il sert à
VÉRIFIER le rig (pivots, ancrages, variantes) avant que le moteur de rendu (#120) ne le consomme.

Usage : ``python scripts/animation/rig_pose.py`` → ``docs/animation/voltix/planche-voltix.svg``.
Bibliothèque standard uniquement ; aucun réseau, aucun code exécuté depuis les fichiers lus.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "resource"
SHEET = ROOT / "docs" / "animation" / "voltix" / "planche-voltix.svg"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
POSE_KEYS = {"select", "rotate", "pupils", "lift", "squash", "air", "accessories"}


class RigError(ValueError):
    pass


def _tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def _index(root: ET.Element) -> dict[str, ET.Element]:
    return {el.get("id"): el for el in root.iter() if el.get("id")}


def _pivot(el: ET.Element) -> tuple[float, float]:
    raw = el.get("data-pivot")
    if raw is None:
        raise RigError(f"« {el.get('id')} » n'a pas de data-pivot.")
    x, y = (float(v) for v in raw.split())
    return x, y


def _prepend(el: ET.Element, transform: str) -> None:
    current = el.get("transform")
    el.set("transform", f"{transform} {current}" if current else transform)


def merge(*poses: dict[str, Any]) -> dict[str, Any]:
    """Fusion « maintien » : une clé absente garde la valeur précédente (dictionnaires fusionnés)."""
    out: dict[str, Any] = {}
    for pose in poses:
        unknown = set(pose) - POSE_KEYS
        if unknown:
            raise RigError("clé(s) de pose inconnue(s) : " + ", ".join(sorted(unknown)))
        for key, value in pose.items():
            if isinstance(value, dict):
                out[key] = {**out.get(key, {}), **value}
            else:
                out[key] = copy.deepcopy(value)
    return out


def pose_for(rig: dict, expression: str | None = None, gesture: str | None = None,
             key: int | None = None) -> dict[str, Any]:
    """Pose complète : repos, puis expression, puis les clés du geste jusqu'à l'indice ``key`` inclus."""
    poses = [rig["rest"]]
    if expression:
        poses.append(rig["expressions"][expression])
    if gesture:
        spec = rig["gestures"][gesture]
        last = spec["sheet_key"] if key is None else key
        poses.extend(item["pose"] for item in spec["keys"][: last + 1])
    return merge(*poses)


def _prop_group(prop_svg: str, anchor: tuple[float, float], at: tuple[float, float], scale: float,
                rotate: float) -> ET.Element:
    prop = ET.fromstring(prop_svg)
    _, _, width, height = (float(v) for v in prop.get("viewBox").split())
    group = ET.Element(_tag("g"))
    group.set("transform", f"translate({at[0]} {at[1]}) rotate({rotate}) scale({scale}) "
                           f"translate({-anchor[0] * width} {-anchor[1] * height})")
    for child in prop:
        if child.tag != _tag("title"):
            group.append(child)
    return group


def apply_pose(svg_text: str, rig: dict, pose: dict[str, Any], *, props: dict[str, tuple[str, tuple]] | None = None
               ) -> ET.Element:
    """Applique ``pose`` à la marionnette ``svg_text`` et renvoie la racine SVG modifiée.

    ``props`` : nom d'accessoire → (SVG, ancrage (x, y) en fraction) pour les accessoires de la pose."""
    root = ET.fromstring(svg_text)
    ids = _index(root)

    def need(name: str) -> ET.Element:
        if name not in ids:
            raise RigError(f"groupe « {name} » absent du SVG.")
        return ids[name]

    for group, variant in pose.get("select", {}).items():
        parent = need(group)
        if parent.get("data-variants") is None:
            raise RigError(f"« {group} » n'est pas un groupe à variantes.")
        found = False
        for child in parent:
            name = child.get("data-variant")
            if name is None:
                continue
            if name == variant:
                found = True
                child.attrib.pop("display", None)
            else:
                child.set("display", "none")
        if not found:
            raise RigError(f"variante « {variant} » absente de « {group} ».")

    for group, angle in pose.get("rotate", {}).items():
        if angle:
            el = need(group)
            x, y = _pivot(el)
            _prepend(el, f"rotate({angle} {x} {y})")

    dx, dy = pose.get("pupils", (0, 0))
    if dx or dy:
        for side in ("left", "right"):
            _prepend(need(f"pupil-{side}"), f"translate({dx} {dy})")

    lift, squash, air = pose.get("lift", 0), pose.get("squash", 0), pose.get("air", 0)
    if lift or squash:
        upper = need("upper")
        px, py = _pivot(upper)
        _prepend(upper, f"translate(0 {-lift}) translate({px} {py}) scale({1 + squash} {1 - squash}) "
                        f"translate({-px} {-py})")
        for side in ("left", "right"):
            spring = ids.get(f"springs-{side}")
            if spring is None:  # marionnette sans ressorts (personnage humain, #173) : jambes inchangées
                continue
            sx, sy = _pivot(spring)
            length = float(spring.get("data-length"))
            ratio = (length + lift) / length
            if ratio <= 0.2:
                raise RigError("compression des ressorts excessive.")
            _prepend(spring, f"translate({sx} {sy}) scale(1 {ratio:.4f}) translate({-sx} {-sy})")
    if air:
        _prepend(need("puppet"), f"translate(0 {-air})")
        shadow = need("shadow")
        k = max(0.4, 1 - air / 250)
        _prepend(shadow, f"translate(300 884) scale({k:.3f} {k:.3f}) translate(-300 -884)")

    for item in pose.get("accessories", []):
        slot = need(item["slot"])
        if slot.get("data-slot") is None:
            raise RigError(f"« {item['slot']} » n'est pas un point d'accroche.")
        if not props or item["asset"] not in props:
            raise RigError(f"accessoire « {item['asset']} » non fourni.")
        prop_svg, anchor = props[item["asset"]]
        slot.append(_prop_group(prop_svg, anchor, _pivot(slot), item.get("scale", 1), item.get("rotate", 0)))
    return root


def prefix_ids(root: ET.Element, prefix: str) -> ET.Element:
    """Préfixe tous les identifiants (et leurs références) pour juxtaposer plusieurs copies sans collision."""
    for el in root.iter():
        if el.get("id"):
            el.set("id", prefix + el.get("id"))
        for attr, value in list(el.attrib.items()):
            if "url(#" in value:
                el.set(attr, re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{prefix}{m.group(1)})", value))
    return root


# --------------------------------------------------------------------------------------------- planche

def _load_catalog(resource: Path) -> tuple[dict, dict[str, tuple[str, tuple]]]:
    catalog = json.loads((resource / "animation-assets" / "voltix-catalogue.json").read_text(encoding="utf-8"))
    props: dict[str, tuple[str, tuple]] = {}
    for asset in catalog["assets"]:
        if asset["kind"] != "prop":
            continue
        manifest = asset["versions"][-1]["manifest"]
        rel = manifest["files"][0]["path"]
        slug = rel.split("/")[1]
        props[slug] = ((resource / rel).read_text(encoding="utf-8"),
                       (manifest["anchor"]["x"], manifest["anchor"]["y"]))
    return catalog, props


def _text(parent: ET.Element, x: float, y: float, content: str, size: int = 20, weight: str = "600") -> None:
    el = ET.SubElement(parent, _tag("text"), {
        "x": str(x), "y": str(y), "font-family": "DejaVu Sans, Arial, sans-serif", "font-size": str(size),
        "font-weight": weight, "fill": "#2A1F14", "text-anchor": "middle"})
    el.text = content


def _cell(parent: ET.Element, figure: ET.Element, x: float, y: float, w: float, h: float, label: str,
          view_box: str | None = None) -> None:
    ET.SubElement(parent, _tag("rect"), {"x": str(x + 4), "y": str(y + 4), "width": str(w - 8),
                                          "height": str(h - 8), "rx": "14", "fill": "#F4F1EA"})
    nested = ET.SubElement(parent, _tag("svg"), {
        "x": str(x + 8), "y": str(y + 8), "width": str(w - 16), "height": str(h - 44),
        "viewBox": view_box or figure.get("viewBox"), "preserveAspectRatio": "xMidYMid meet"})
    for child in figure:
        if child.tag != _tag("title"):
            nested.append(child)
    _text(parent, x + w / 2, y + h - 16, label, 18)


def build_sheet(resource: Path = RESOURCE) -> str:
    rig = json.loads((resource / "animation-assets/voltix/v1/voltix-rig.json").read_text(encoding="utf-8"))
    views = {name: (resource / f"animation-assets/voltix/v1/voltix-{name}.svg").read_text(encoding="utf-8")
             for name in ("face", "trois-quarts")}
    _, props = _load_catalog(resource)
    width, cw, ch = 1200, 300, 480
    figures = [
        ("face", None, None, None, "Face — neutre"),
        ("trois-quarts", None, None, None, "3/4 — neutre"),
        ("face", "sourire", None, None, "Sourire"),
        ("face", "surprise", None, None, "Surprise"),
        ("face", "confusion", None, None, "Confusion"),
        ("face", "concentration", None, None, "Concentration"),
        ("face", "sourire", "saluer", None, "Geste : saluer"),
        ("face", "neutre", "montrer", None, "Geste : montrer"),
        ("face", "concentration", "tenir-cable", None, "Geste : tenir le câble"),
        ("face", "concentration", "rebond", 1, "Rebond : compression"),
        ("face", "sourire", "rebond", 3, "Rebond : envol"),
        ("trois-quarts", "sourire", "montrer", None, "3/4 — montrer"),
    ]
    rows = (len(figures) + 3) // 4
    visemes_y = 110 + rows * ch
    props_y = visemes_y + 230
    height = props_y + 270
    root = ET.Element(_tag("svg"), {"viewBox": f"0 0 {width} {height}", "width": str(width),
                                     "height": str(height)})
    ET.SubElement(root, _tag("title")).text = "Voltix — planche de contrôle du rig v1 (#136)"
    ET.SubElement(root, _tag("rect"), {"width": str(width), "height": str(height), "fill": "#FFFFFF"})
    _text(root, width / 2, 50, "Voltix — planche de contrôle du rig v1", 34, "700")
    _text(root, width / 2, 84, "Concept approchant la référence choisie — à valider. Générée depuis le rig SVG.",
          17, "400")
    for i, (view, expression, gesture, key, label) in enumerate(figures):
        figure = apply_pose(views[view], rig, pose_for(rig, expression, gesture, key), props=props)
        prefix_ids(figure, f"c{i}-")
        _cell(root, figure, (i % 4) * cw, 100 + (i // 4) * ch, cw, ch, label)

    _text(root, width / 2, visemes_y + 6, "Visèmes français (bouche pilotée par la voix)", 22, "700")
    names = list(rig["visemes"])
    vw = width / len(names)
    for i, name in enumerate(names):
        mouth = rig["visemes"][name].get("mouth", name)
        figure = apply_pose(views["face"], rig, merge(rig["rest"], {"select": {"mouth-visemes": mouth}}))
        prefix_ids(figure, f"v{i}-")
        _cell(root, figure, i * vw, visemes_y + 20, vw, 200, name, view_box="215 385 170 112")

    _text(root, width / 2, props_y + 6, "Accessoires (assets séparés de la marionnette)", 22, "700")
    labels = {"cable-sdi": "Câble SDI 75 Ω", "bnc-male": "BNC mâle", "bnc-femelle": "BNC femelle (embase)",
              "tournevis": "Tournevis"}
    for i, (slug, (svg_text, _anchor)) in enumerate(props.items()):
        figure = ET.fromstring(svg_text)
        prefix_ids(figure, f"p{i}-")
        _cell(root, figure, i * cw, props_y + 20, cw, 240, labels.get(slug, slug))
    return ET.tostring(root, encoding="unicode") + "\n"


if __name__ == "__main__":
    SHEET.parent.mkdir(parents=True, exist_ok=True)
    SHEET.write_text(build_sheet(), encoding="utf-8")
    print(SHEET.relative_to(ROOT))
    sys.exit(0)
