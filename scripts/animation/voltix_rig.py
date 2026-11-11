"""Génère le rig SVG de Voltix, ses accessoires et le catalogue ``lody.animation-assets/1`` (#136).

Source unique de la **version 1** : les SVG de ``resource/animation-assets/`` sont produits par ce script
(``python scripts/animation/voltix_rig.py``) et un test vérifie qu'ils n'ont pas dérivé. Pour une retouche
à la main (Inkscape…), publier une **version 2** dans un nouveau dossier ``v2/`` : la v1 reste immuable.

Conventions du rig (voir ``docs/lody-voltix.md``) :

- ``left`` / ``right`` = côté **du personnage** (son bras gauche est à droite de l'image) ;
- chaque groupe articulé porte ``data-pivot="x y"`` (coordonnées du canevas 600 × 900, avant pose) ;
- un groupe à variantes porte ``data-variants`` ; chaque enfant ``data-variant="nom"`` ; une seule
  variante visible à la fois (``display="none"`` sur les autres) ;
- ``data-slot`` marque un point d'accroche d'accessoire (les accessoires sont des assets séparés).

Aucun réseau, aucune dépendance hors bibliothèque standard.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "resource" / "animation-assets"
CATALOG = ASSETS / "voltix-catalogue.json"

W, H = 600, 900
GROUND = 882

# Palette canonique (reprise dans la fiche personnage).
P = {
    "jaune": "#F7C325",       # corps, bras, mains
    "jaune-ombre": "#E3A812",  # ombre plate unique
    "jaune-reflet": "#FFE48A",
    "meches": "#EFAE17",       # mèches pointues
    "contour": "#2A1F14",      # contour brun très foncé (jamais noir pur)
    "sourcils": "#3B2716",
    "blanc": "#FFFFFF",
    "iris": "#6B3E1E",
    "pupille": "#1A0F08",
    "bouche": "#7A2230",
    "langue": "#E5566B",
    "dents-trait": "#D8D0C2",
    "charbon": "#3C3F44",      # ceinture
    "sacoche": "#4C5157",
    "boucle": "#B4BAC1",
    "ressort": "#1C1C1E",
    "ressort-reflet": "#6E7379",
    "chaussure": "#8C939B",
    "semelle": "#4B5056",
    "manche-outil": "#E8562A",
    "metal": "#C9CED4",
    "metal-ombre": "#8E959C",
    "isolant": "#F4F3EE",
    "broche": "#D9A520",
    "cable": "#18181A",
}
STROKE = 6

# Paramètres des deux vues. « r » = côté droit du personnage (gauche de l'image), « l » = côté gauche.
VIEWS = {
    "face": {
        "title": "Voltix — vue de face",
        "eye_r": (232, 330, 50, 60), "eye_l": (368, 330, 50, 60),
        "mouth": (300, 1.0), "buckle": 300, "hair_dx": 0, "pouch_dx": 0,
        "arm_r_dx": 0, "arm_l_dx": 0, "far_arm_behind": False,
        "spring_r": 238, "spring_l": 362, "shoe_r": (238, -1), "shoe_l": (362, 1),
    },
    "trois-quarts": {
        "title": "Voltix — vue de trois quarts",
        "eye_r": (262, 332, 47, 60), "eye_l": (392, 332, 36, 56),
        "mouth": (334, 0.86), "buckle": 345, "hair_dx": 18, "pouch_dx": 14,
        "arm_r_dx": 12, "arm_l_dx": -34, "far_arm_behind": True,
        "spring_r": 262, "spring_l": 368, "shoe_r": (256, 1), "shoe_l": (372, 1),
    },
}

BODY = (300, 370, 195)
SPRING_TOP, SPRING_BOTTOM = 545, 812


def _f(value: float) -> str:
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _pts(points) -> str:
    return " ".join(f"{_f(x)},{_f(y)}" for x, y in points)


def _tube(d: str, width: int, fill: str) -> str:
    """Membre « tuyau » : contour foncé épais puis remplissage plus fin, extrémités rondes."""
    return (f'<path d="{d}" fill="none" stroke="{P["contour"]}" stroke-width="{width + 2 * STROKE}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="{d}" fill="none" stroke="{fill}" stroke-width="{width}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def _outline(extra: str = "") -> str:
    return f'stroke="{P["contour"]}" stroke-width="{STROKE}" stroke-linejoin="round"{extra}'


# --------------------------------------------------------------------------------------------- personnage

def _hair(dx: float) -> tuple[str, str]:
    spikes = [(150, 262), (112, 196), (176, 214), (160, 132), (226, 188), (246, 92), (290, 172), (334, 86),
              (348, 178), (410, 112), (398, 198), (470, 160), (444, 236), (490, 246), (452, 276)]
    back = _pts((x + dx, y) for x, y in spikes)
    curl = (f"M{_f(262 + dx)},182 Q{_f(286 + dx)},146 {_f(326 + dx)},158 "
            f"Q{_f(302 + dx)},172 {_f(312 + dx)},200 Q{_f(290 + dx)},184 {_f(262 + dx)},196 Z")
    return (f'<g id="hair"><polygon points="{back}" fill="{P["meches"]}" {_outline()}/></g>',
            f'<g id="hair-front"><path d="{curl}" fill="{P["meches"]}" {_outline()}/></g>')


def _body() -> str:
    cx, cy, r = BODY
    return (f'<g id="body">'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{P["jaune"]}"/>'
            f'<path d="M{cx + r},{cy} A{r},{r} 0 0 1 {cx},{cy + r} Q{cx + 140},{cy + 130} {cx + r},{cy} Z" '
            f'fill="{P["jaune-ombre"]}" clip-path="url(#clip-body)"/>'
            f'<ellipse cx="{cx - 92}" cy="{cy - 118}" rx="42" ry="20" transform="rotate(-32 {cx - 92} {cy - 118})" '
            f'fill="{P["jaune-reflet"]}"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" {_outline()}/>'
            f'</g>')


def _brow(cx: float, cy: float, s: float, mirror: bool, offsets) -> str:
    sign = -1 if mirror else 1
    (ax, ay), (bx, by), (ex, ey) = offsets
    d = (f"M{_f(cx + sign * ax * s)},{_f(cy + ay)} Q{_f(cx + sign * bx * s)},{_f(cy + by)} "
         f"{_f(cx + sign * ex * s)},{_f(cy + ey)}")
    return (f'<path d="{d}" fill="none" stroke="{P["sourcils"]}" stroke-width="19" stroke-linecap="round"/>')


# Décalages (x, y) des sourcils par rapport au centre de l'œil, pour l'œil côté image gauche ; l'autre est
# mis en miroir. Une paire (droite, gauche) permet les expressions asymétriques.
BROWS = {
    "neutre": (((-44, -78), (-4, -98), (40, -82)),) * 2,
    "joie": (((-42, -88), (-4, -110), (38, -92)),) * 2,
    "surprise": (((-42, -104), (-2, -132), (38, -110)),) * 2,
    "confusion": (((-42, -100), (-4, -127), (38, -106)), ((-42, -76), (0, -80), (40, -70))),
    "concentration": (((-42, -92), (0, -88), (42, -70)),) * 2,
}


def _eyebrows(v: dict) -> str:
    rx_, ry_ = v["eye_r"], v["eye_l"]
    parts = ['<g id="eyebrows" data-variants="">']
    for name, (right, left) in ((n, o if len(o) == 2 else (o[0], o[0])) for n, o in BROWS.items()):
        hidden = "" if name == "neutre" else ' display="none"'
        parts.append(f'<g id="eyebrows-{name}" data-variant="{name}"{hidden}>'
                     f'{_brow(rx_[0], rx_[1], rx_[2] / 50, False, right)}'
                     f'{_brow(ry_[0], ry_[1], ry_[2] / 50, True, left)}</g>')
    parts.append("</g>")
    return "".join(parts)


def _eyes(v: dict) -> tuple[str, str, str]:
    eyes, pupils, lids_open, lids_half, lids_closed = [], [], [], [], []
    for side, (cx, cy, rx, ry) in (("right", v["eye_r"]), ("left", v["eye_l"])):
        eyes.append(f'<ellipse id="eye-{side}" cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{P["blanc"]}" '
                    f'{_outline()}/>')
        px, py = cx + (6 if side == "right" else -6) * rx / 50, cy + 8
        k = rx / 50
        pupils.append(
            f'<g clip-path="url(#clip-eye-{side})"><g id="pupil-{side}" data-pivot="{_f(px)} {_f(py)}">'
            f'<ellipse cx="{_f(px)}" cy="{_f(py)}" rx="{_f(27 * k)}" ry="27" fill="{P["iris"]}"/>'
            f'<ellipse cx="{_f(px)}" cy="{_f(py)}" rx="{_f(15 * k)}" ry="15" fill="{P["pupille"]}"/>'
            f'<circle cx="{_f(px + 9 * k)}" cy="{_f(py - 10)}" r="7" fill="{P["blanc"]}"/></g></g>')
        top = cy - ry - 4
        lids_half.append(
            f'<g clip-path="url(#clip-eye-{side})"><rect x="{cx - rx - 4}" y="{top}" width="{2 * rx + 8}" '
            f'height="{ry + 4}" fill="{P["jaune"]}"/></g>'
            f'<path d="M{cx - rx + 2},{cy} Q{cx},{cy + 6} {cx + rx - 2},{cy}" fill="none" '
            f'stroke="{P["contour"]}" stroke-width="{STROKE}" stroke-linecap="round"/>'
            f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="none" {_outline()}/>')
        lids_closed.append(
            f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{P["jaune"]}" {_outline()}/>'
            f'<path d="M{cx - rx + 6},{cy + 4} Q{cx},{cy + 30} {cx + rx - 6},{cy + 4}" fill="none" '
            f'stroke="{P["contour"]}" stroke-width="7" stroke-linecap="round"/>')
    lids = ('<g id="lids" data-variants="">'
            f'<g id="lids-ouverts" data-variant="ouverts">{"".join(lids_open)}</g>'
            f'<g id="lids-mi-clos" data-variant="mi-clos" display="none">{"".join(lids_half)}</g>'
            f'<g id="lids-fermes" data-variant="fermes" display="none">{"".join(lids_closed)}</g></g>')
    return f'<g id="eyes">{"".join(eyes)}</g>', f'<g id="pupils">{"".join(pupils)}</g>', lids


def _mouth_shape(name: str, d: str, teeth: str = "", tongue: str = "") -> str:
    inner = f'<path d="{d}" fill="{P["bouche"]}"/>'
    clip = f'<clipPath id="clip-mouth-{name}"><path d="{d}"/></clipPath>'
    deco = ""
    if teeth or tongue:
        deco = f'<g clip-path="url(#clip-mouth-{name})">{tongue}{teeth}</g>'
    return f'{clip}{inner}{deco}<path d="{d}" fill="none" {_outline()}/>'


def _teeth(y0: float, y1: float, x0: float, x1: float, gaps=(-20, 0, 20)) -> str:
    lines = "".join(f'<line x1="{300 + g}" y1="{y0}" x2="{300 + g}" y2="{y1}" stroke="{P["dents-trait"]}" '
                    f'stroke-width="2.5"/>' for g in gaps)
    return f'<rect x="{x0}" y="{y0 - 20}" width="{x1 - x0}" height="{y1 - y0 + 20}" fill="{P["blanc"]}"/>{lines}'


def _tongue(cy: float, rx: float = 30, ry: float = 13) -> str:
    return f'<ellipse cx="300" cy="{cy}" rx="{rx}" ry="{ry}" fill="{P["langue"]}"/>'


# Formes de bouche dessinées autour de (300, 445). Les sept premières sont les visèmes français.
MOUTHS = {
    "mbp": lambda: (f'<path d="M266,447 Q300,454 334,447" fill="none" stroke="{P["contour"]}" stroke-width="9" '
                    f'stroke-linecap="round"/>'),
    "a": lambda: _mouth_shape("a", "M262,428 Q300,420 338,428 Q344,492 300,498 Q256,492 262,428 Z",
                              _teeth(426, 440, 250, 350), _tongue(488)),
    "e": lambda: _mouth_shape("e", "M246,432 Q300,424 354,432 Q342,472 300,474 Q258,472 246,432 Z",
                              _teeth(426, 442, 240, 360, (-30, -10, 10, 30)), _tongue(470, 34, 10)),
    "i": lambda: _mouth_shape("i", "M244,438 Q300,430 356,438 Q332,460 300,460 Q268,460 244,438 Z",
                              _teeth(430, 452, 236, 364, (-30, -10, 10, 30))),
    "o": lambda: _mouth_shape("o", "M300,422 Q328,424 328,455 Q326,486 300,488 Q274,486 272,455 Q274,424 300,422 Z",
                              tongue=_tongue(480, 18, 9)),
    "ou": lambda: (_mouth_shape("ou", "M300,436 Q316,438 316,453 Q315,468 300,470 Q285,468 284,453 Q285,438 300,436 Z")
                   + f'<path d="M290,432 Q300,428 310,432" fill="none" stroke="{P["contour"]}" stroke-width="4" '
                     f'stroke-linecap="round"/>'),
    "fv": lambda: _mouth_shape("fv", "M262,438 Q300,432 338,438 Q330,464 300,465 Q270,464 262,438 Z",
                               _teeth(432, 450, 266, 334, (-12, 12))),
    "repos": lambda: (f'<path d="M262,438 Q300,466 338,438" fill="none" stroke="{P["contour"]}" stroke-width="7" '
                      f'stroke-linecap="round"/>'),
    "sourire": lambda: _mouth_shape("sourire", "M232,422 Q300,432 368,422 Q360,500 300,502 Q240,500 232,422 Z",
                                    _teeth(430, 446, 228, 372, (-40, -20, 0, 20, 40)), _tongue(492, 36, 15)),
    "grimace": lambda: (f'<path d="M260,452 Q273,438 287,450 Q300,464 313,450 Q327,438 340,452" fill="none" '
                        f'stroke="{P["contour"]}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'),
}
VISEMES = ("mbp", "a", "e", "i", "o", "ou", "fv")


def _mouth(v: dict) -> str:
    mx, sx = v["mouth"]
    transform = "" if (mx, sx) == (300, 1.0) else f' transform="matrix({sx} 0 0 1 {_f(mx - 300 * sx)} 0)"'
    parts = [f'<g id="mouth-visemes" data-variants=""{transform}>']
    for name, draw in MOUTHS.items():
        hidden = "" if name == "repos" else ' display="none"'
        parts.append(f'<g id="mouth-{name}" data-variant="{name}"{hidden}>{draw()}</g>')
    parts.append("</g>")
    return "".join(parts)


def _toolbelt(v: dict) -> str:
    bx, pdx = v["buckle"], v["pouch_dx"]
    band = "M90,494 Q300,540 510,494 L510,532 Q300,580 90,532 Z"
    return (f'<g id="toolbelt">'
            f'<g clip-path="url(#clip-body)"><path d="{band}" fill="{P["charbon"]}"/>'
            f'<path d="M90,494 Q300,540 510,494" fill="none" {_outline()}/>'
            f'<path d="M90,532 Q300,580 510,532" fill="none" {_outline()}/></g>'
            f'<rect x="{bx - 20}" y="{513 if bx == 300 else 511}" width="40" height="32" rx="6" fill="none" '
            f'stroke="{P["contour"]}" stroke-width="13"/>'
            f'<rect x="{bx - 20}" y="{513 if bx == 300 else 511}" width="40" height="32" rx="6" fill="none" '
            f'stroke="{P["boucle"]}" stroke-width="6"/>'
            f'<g id="toolbelt-pouch">'
            f'<rect x="{158 + pdx}" y="470" width="16" height="44" rx="4" fill="{P["manche-outil"]}" {_outline()}/>'
            f'<rect x="{184 + pdx}" y="478" width="10" height="34" rx="3" fill="{P["metal"]}" {_outline()}/>'
            f'<rect x="{140 + pdx}" y="498" width="74" height="76" rx="14" fill="{P["sacoche"]}" {_outline()}/>'
            f'<path d="M{140 + pdx},{520} Q{177 + pdx},{534} {214 + pdx},{520} L{214 + pdx},{510} '
            f'Q{214 + pdx},{498} {200 + pdx},{498} L{154 + pdx},{498} Q{140 + pdx},{498} {140 + pdx},{510} Z" '
            f'fill="{P["charbon"]}" {_outline()}/>'
            f'<circle cx="{177 + pdx}" cy="527" r="5" fill="{P["boucle"]}"/></g>'
            f'</g>')


def _hand(side: str, wx: float, wy: float, mirror: bool) -> str:
    """Main en moufle orientée vers le bas, poignet en (wx, wy). Trois variantes et un point d'accroche."""
    s = -1 if mirror else 1  # côté pouce : vers le corps

    def finger(x0, y0, x1, y1):
        return _tube(f"M{_f(x0)},{_f(y0)} L{_f(x1)},{_f(y1)}", 10, P["jaune"])

    cx, cy = wx, wy + 24
    open_ = (finger(cx - 10 * s, cy + 8, cx - 16 * s, cy + 32) + finger(cx, cy + 12, cx, cy + 38)
             + finger(cx + 10 * s, cy + 8, cx + 16 * s, cy + 32) + finger(cx + 15 * s, cy - 6, cx + 29 * s, cy + 8)
             + f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="20" fill="{P["jaune"]}" {_outline()}/>'
             + f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="14" fill="{P["jaune"]}"/>')
    fist = (f'<circle cx="{_f(cx)}" cy="{_f(cy + 2)}" r="23" fill="{P["jaune"]}" {_outline()}/>'
            f'<path d="M{_f(cx - 13)},{_f(cy + 10)} Q{_f(cx)},{_f(cy + 18)} {_f(cx + 13)},{_f(cy + 10)}" '
            f'fill="none" stroke="{P["contour"]}" stroke-width="3.5" stroke-linecap="round"/>'
            f'<ellipse cx="{_f(cx + 17 * s)}" cy="{_f(cy - 5)}" rx="9" ry="11" fill="{P["jaune"]}" {_outline()}/>')
    index = finger(cx, cy + 14, cx, cy + 50) + fist
    return (f'<g id="hand-{side}" data-pivot="{_f(wx)} {_f(wy)}" data-variants="">'
            f'<g id="hand-{side}-ouverte" data-variant="ouverte">{open_}</g>'
            f'<g id="hand-{side}-poing" data-variant="poing" display="none">{fist}</g>'
            f'<g id="hand-{side}-index" data-variant="index" display="none">{index}</g>'
            f'<g id="hand-{side}-slot" data-slot="" data-pivot="{_f(cx)} {_f(cy + 2)}"/></g>')


def _arm(side: str, dx: float) -> str:
    mirror = side == "left"  # bras gauche du personnage = à droite de l'image

    def X(x):  # noqa: N802 - coordonnée miroir
        return 600 - x + dx if mirror else x + dx

    sh, el, wr = (X(120), 404), (X(88), 470), (X(80), 532)
    return (f'<g id="arm-{side}" data-pivot="{_f(sh[0])} {sh[1]}">'
            f'{_tube(f"M{_f(sh[0])},{sh[1]} L{_f(el[0])},{el[1]}", 30, P["jaune"])}'
            f'<g id="forearm-{side}" data-pivot="{_f(el[0])} {el[1]}">'
            f'{_tube(f"M{_f(el[0])},{el[1]} L{_f(wr[0])},{wr[1]}", 28, P["jaune"])}'
            f'{_hand(side, wr[0], wr[1], mirror)}</g></g>')


def _spring(side: str, cx: float) -> str:
    """Ressort métallique noir en zigzag arrondi ; pivot au pied (la compression garde le sol fixe)."""
    turns, half = 12, 26
    step = (SPRING_BOTTOM - SPRING_TOP) / turns
    points = [(cx, SPRING_TOP)] + [(cx + (half if i % 2 else -half), SPRING_TOP + i * step)
                                   for i in range(1, turns)] + [(cx, SPRING_BOTTOM)]
    d = "M" + " L".join(f"{_f(x)},{_f(y)}" for x, y in points)
    return (f'<g id="springs-{side}" data-pivot="{_f(cx)} {SPRING_BOTTOM}" '
            f'data-length="{SPRING_BOTTOM - SPRING_TOP}">'
            f'<path d="{d}" fill="none" stroke="{P["ressort"]}" stroke-width="13" stroke-linecap="round" '
            f'stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
            f'<path d="{d}" fill="none" stroke="{P["ressort-reflet"]}" stroke-width="3" stroke-linecap="round" '
            f'stroke-linejoin="round" vector-effect="non-scaling-stroke" transform="translate(-2 -2)"/></g>')


def _shoe(side: str, cx: float, direction: int) -> str:
    d = direction

    def x(o):
        return _f(cx + d * o)

    body = (f"M{x(-52)},{GROUND} L{x(-52)},846 Q{x(-50)},800 {x(2)},798 Q{x(34)},798 {x(42)},822 "
            f"Q{x(80)},828 {x(80)},858 L{x(80)},{GROUND} Z")
    sole = f"M{x(-52)},866 L{x(80)},866 L{x(80)},{GROUND} L{x(-52)},{GROUND} Z"
    return (f'<g id="shoe-{side}">'
            f'<path d="{body}" fill="{P["chaussure"]}"/>'
            f'<path d="{sole}" fill="{P["semelle"]}"/>'
            f'<path d="{body}" fill="none" {_outline()}/>'
            f'<path d="M{x(14)},816 L{x(30)},812 M{x(16)},830 L{x(34)},826" stroke="{P["contour"]}" '
            f'stroke-width="4" stroke-linecap="round"/>'
            f'<ellipse cx="{_f(cx)}" cy="806" rx="24" ry="8" fill="{P["semelle"]}" {_outline()}/></g>')


def build_character(view: str) -> str:
    v = VIEWS[view]
    cx, cy, r = BODY
    hair_back, hair_front = _hair(v["hair_dx"])
    eyes, pupils, lids = _eyes(v)
    arm_r, arm_l = _arm("right", v["arm_r_dx"]), _arm("left", v["arm_l_dx"])
    defs = (f'<defs><clipPath id="clip-body"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>'
            + "".join(f'<clipPath id="clip-eye-{side}"><ellipse cx="{e[0]}" cy="{e[1]}" rx="{e[2]}" ry="{e[3]}"/>'
                      f'</clipPath>' for side, e in (("right", v["eye_r"]), ("left", v["eye_l"])))
            + "</defs>")
    upper = (f'<g id="upper" data-pivot="300 {SPRING_TOP}">'
             + (arm_l if v["far_arm_behind"] else "")
             + hair_back + _body() + hair_front + _toolbelt(v)
             + f'<g id="face">{eyes}{pupils}{lids}{_eyebrows(v)}{_mouth(v)}</g>'
             + '<g id="accessories"><g id="slot-belly" data-slot="" data-pivot="300 512"/>'
               '<g id="slot-belt" data-slot="" data-pivot="177 540"/></g>'
             + arm_r + ("" if v["far_arm_behind"] else arm_l)
             + "</g>")
    shoes = (f'<g id="shoes">{_shoe("right", *v["shoe_r"])}{_shoe("left", *v["shoe_l"])}</g>')
    return _svg(v["title"], W, H, defs
                + f'<g id="shadow"><ellipse cx="300" cy="{GROUND + 2}" rx="150" ry="12" fill="#000000" '
                  f'opacity="0.16"/></g>'
                + f'<g id="puppet" data-pivot="300 {GROUND}">'
                + _spring("right", v["spring_r"]) + _spring("left", v["spring_l"]) + upper + shoes + "</g>")


# --------------------------------------------------------------------------------------------- accessoires

def build_bnc_male() -> str:
    """Fiche BNC mâle, vue de profil (prise vers la droite) + face avant : broche centrale, isolant blanc,
    manchon de verrouillage à baïonnette avec sa fente en J."""
    o = _outline()
    return _svg("BNC mâle 75 Ω", 420, 180, (
        f'<g id="cable"><rect x="0" y="74" width="96" height="32" fill="{P["cable"]}" {o}/></g>'
        f'<g id="manchon-souple"><path d="M90,66 L170,72 L170,108 L90,114 Z" fill="{P["cable"]}" {o}/>'
        f'<path d="M112,70 L112,110 M134,71 L134,109 M156,72 L156,108" stroke="#4A4A4E" stroke-width="3"/></g>'
        f'<g id="sertissage"><rect x="168" y="70" width="40" height="40" rx="4" fill="{P["metal-ombre"]}" {o}/></g>'
        f'<g id="corps"><rect x="206" y="62" width="44" height="56" rx="5" fill="{P["metal"]}" {o}/></g>'
        f'<g id="baionnette"><rect x="248" y="44" width="92" height="92" rx="10" fill="{P["metal"]}" {o}/>'
        + "".join(f'<line x1="{x}" y1="48" x2="{x}" y2="62" stroke="{P["metal-ombre"]}" stroke-width="3"/>'
                  f'<line x1="{x}" y1="118" x2="{x}" y2="132" stroke="{P["metal-ombre"]}" stroke-width="3"/>'
                  for x in range(258, 300, 8))
        + f'<path d="M340,78 L304,78 Q296,78 296,86 L296,96 L314,96" fill="none" stroke="{P["contour"]}" '
          f'stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>'
          f'<path d="M340,78 L304,78 Q296,78 296,86 L296,96 L314,96" fill="none" stroke="{P["metal-ombre"]}" '
          f'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/></g>'
        f'<g id="face-avant"><ellipse cx="340" cy="90" rx="16" ry="46" fill="{P["metal-ombre"]}" {o}/>'
        f'<ellipse cx="342" cy="90" rx="10" ry="28" fill="{P["isolant"]}" {o}/></g>'
        f'<g id="broche"><path d="M342,86 L390,86 L398,90 L390,94 L342,94 Z" fill="{P["broche"]}" {o}/></g>'
        f'<g id="etiquette"><text x="130" y="160" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" '
        f'font-weight="700" fill="{P["contour"]}" text-anchor="middle">75 Ω</text></g>'))


def build_bnc_female() -> str:
    """Embase BNC femelle de panneau, vue de profil (ouverture vers la gauche) : douille centrale dans
    l'isolant, fût avec deux ergots de baïonnette, écrou hexagonal et filetage."""
    o = _outline()
    return _svg("BNC femelle (embase)", 300, 200, (
        f'<g id="fut"><path d="M40,54 L150,54 L150,146 L40,146" fill="{P["metal"]}" {o}/>'
        f'<ellipse cx="40" cy="100" rx="18" ry="46" fill="none" {o}/></g>'
        f'<g id="face-avant"><ellipse cx="40" cy="100" rx="18" ry="46" fill="{P["metal-ombre"]}" {o}/>'
        f'<ellipse cx="38" cy="100" rx="11" ry="30" fill="{P["isolant"]}" {o}/>'
        f'<ellipse cx="36" cy="100" rx="4" ry="8" fill="{P["contour"]}"/></g>'
        f'<g id="ergots"><rect x="78" y="34" width="20" height="22" rx="5" fill="{P["metal-ombre"]}" {o}/>'
        f'<rect x="78" y="144" width="20" height="22" rx="5" fill="{P["metal-ombre"]}" {o}/></g>'
        f'<g id="ecrou"><path d="M150,30 L196,30 L208,100 L196,170 L150,170 L140,100 Z" fill="{P["metal-ombre"]}" '
        f'{o}/><line x1="150" y1="100" x2="204" y2="100" stroke="{P["contour"]}" stroke-width="3"/></g>'
        f'<g id="filetage"><rect x="206" y="66" width="70" height="68" fill="{P["metal"]}" {o}/>'
        + "".join(f'<line x1="{x}" y1="68" x2="{x + 6}" y2="132" stroke="{P["metal-ombre"]}" stroke-width="3"/>'
                  for x in range(214, 272, 10))
        + "</g>"))


def build_cable() -> str:
    """Câble coaxial SDI 75 Ω noir, enroulé en bobine, avec une fiche BNC mâle et une étiquette."""
    loops = "".join(
        f'<ellipse cx="{150 + dx}" cy="{132 + dy}" rx="104" ry="76" fill="none" stroke="{P["contour"]}" '
        f'stroke-width="20"/><ellipse cx="{150 + dx}" cy="{132 + dy}" rx="104" ry="76" fill="none" '
        f'stroke="{P["cable"]}" stroke-width="12"/>'
        for dx, dy in ((-10, 6), (0, 0), (10, -6), (18, 4)))
    tail = "M262,150 Q300,196 322,222"
    body = build_bnc_male().split("</title>", 1)[1].rsplit("</svg>", 1)[0]
    body = re.sub(r'<g id="etiquette">.*?</g>', "", body)
    plug = ('<g transform="translate(318 214) rotate(48) scale(0.32) translate(-200 -90)">'
            + re.sub(r' id="[^"]+"', "", body) + "</g>")
    return _svg("Câble SDI coaxial 75 Ω", 420, 300, (
        f'<g id="bobine">{loops}</g>'
        f'<g id="extremite">{_tube(tail, 12, P["cable"])}</g>'
        f'<g id="connecteur">{plug}</g>'
        f'<g id="etiquette"><rect x="18" y="226" width="150" height="44" rx="8" fill="{P["jaune"]}" '
        f'{_outline()}/><text x="93" y="256" font-family="DejaVu Sans, Arial, sans-serif" font-size="22" '
        f'font-weight="700" fill="{P["contour"]}" text-anchor="middle">SDI 75 Ω</text></g>'))


def build_screwdriver() -> str:
    o = _outline()
    return _svg("Tournevis", 320, 80, (
        f'<g id="manche"><rect x="8" y="16" width="120" height="48" rx="20" fill="{P["manche-outil"]}" {o}/>'
        + "".join(f'<line x1="{x}" y1="26" x2="{x}" y2="54" stroke="#B8401C" stroke-width="4" '
                  f'stroke-linecap="round"/>' for x in (36, 56, 76, 96))
        + f'</g><g id="tige"><rect x="126" y="34" width="150" height="12" fill="{P["metal"]}" {o}/></g>'
        f'<g id="pointe"><path d="M274,32 L304,36 L304,44 L274,48 Z" fill="{P["metal-ombre"]}" {o}/></g>'))


# --------------------------------------------------------------------------------------------- écriture

def _svg(title: str, width: int, height: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}"><title>{title}</title>{body}</svg>\n')


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def outputs() -> dict[str, str]:
    """Chemin relatif (au dossier ``resource/``) → contenu SVG, pour toute la version 1."""
    return {
        "animation-assets/voltix/v1/voltix-face.svg": build_character("face"),
        "animation-assets/voltix/v1/voltix-trois-quarts.svg": build_character("trois-quarts"),
        "animation-assets/cable-sdi/v1/cable-sdi.svg": build_cable(),
        "animation-assets/bnc-male/v1/bnc-male.svg": build_bnc_male(),
        "animation-assets/bnc-femelle/v1/bnc-femelle.svg": build_bnc_female(),
        "animation-assets/tournevis/v1/tournevis.svg": build_screwdriver(),
    }


def _manifest_sha(manifest: dict) -> str:
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_catalog(resource: Path) -> dict:
    def files(*pairs):
        return [{"role": role, "path": rel, "sha256": _sha(resource / rel)} for role, rel in pairs]

    rig = json.loads((resource / "animation-assets/voltix/v1/voltix-rig.json").read_text(encoding="utf-8"))
    voltix = {
        "format": "svg", "width": W, "height": H, "anchor": {"x": 0.5, "y": round(GROUND / H, 4)},
        "parts": rig["parts"], "expressions": sorted(rig["expressions"]), "gestures": sorted(rig["gestures"]),
        "files": files(("base", "animation-assets/voltix/v1/voltix-face.svg"),
                       ("trois-quarts", "animation-assets/voltix/v1/voltix-trois-quarts.svg"),
                       ("rig", "animation-assets/voltix/v1/voltix-rig.json")),
    }

    def prop(width, height, parts, rel, anchor=(0.5, 0.5)):
        return {"format": "svg", "width": width, "height": height, "anchor": {"x": anchor[0], "y": anchor[1]},
                "parts": parts, "files": files(("base", rel))}

    entries = [
        ("ast_136000000001", "Voltix — marionnette", "character",
         "Mascotte jaune aux jambes à ressort (#136). Concept approchant la référence choisie : à valider.",
         "Voltix", voltix, "Version 1 : vues de face et 3/4, 5 expressions, 7 visèmes, 5 gestes."),
        ("ast_136000000002", "Câble SDI 75 Ω enroulé", "prop", "Câble coaxial noir enroulé, fiche BNC mâle.", None,
         prop(420, 300, ["bobine", "extremite", "connecteur", "etiquette"],
              "animation-assets/cable-sdi/v1/cable-sdi.svg"), ""),
        ("ast_136000000003", "Fiche BNC mâle", "prop",
         "Profil : broche centrale, isolant, manchon à baïonnette avec fente en J.", None,
         prop(420, 180, ["cable", "manchon-souple", "sertissage", "corps", "baionnette", "face-avant", "broche",
                         "etiquette"], "animation-assets/bnc-male/v1/bnc-male.svg"), ""),
        ("ast_136000000004", "Embase BNC femelle", "prop",
         "Profil : douille centrale, fût avec deux ergots de baïonnette, écrou, filetage.", None,
         prop(300, 200, ["face-avant", "fut", "ergots", "ecrou", "filetage"],
              "animation-assets/bnc-femelle/v1/bnc-femelle.svg"), ""),
        ("ast_136000000005", "Tournevis", "prop", "Outil générique rangé dans la sacoche de Voltix.", None,
         prop(320, 80, ["manche", "tige", "pointe"], "animation-assets/tournevis/v1/tournevis.svg"), ""),
    ]
    assets = []
    for asset_id, name, kind, description, character, manifest, notes in entries:
        assets.append({"id": asset_id, "name": name, "kind": kind, "description": description, "is_active": True,
                       "character": character, "location": None,
                       "versions": [{"version": 1, "manifest": manifest, "sha256": _manifest_sha(manifest),
                                     "notes": notes}]})
    return {"schema": "lody.animation-assets/1", "assets": assets}


def write_all(resource: Path = ROOT / "resource") -> list[Path]:
    written = []
    for rel, content in outputs().items():
        path = resource / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    catalog = resource / "animation-assets" / "voltix-catalogue.json"
    catalog.write_text(json.dumps(build_catalog(resource), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written.append(catalog)
    return written


if __name__ == "__main__":
    for item in write_all():
        print(item.relative_to(ROOT))
    sys.exit(0)
