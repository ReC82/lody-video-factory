"""Génère les rigs SVG des personnages adultes du laboratoire, Nora et Sam (#173).

Source unique de la **version 1** : les SVG, les rigs ``lody.rig/1`` et le catalogue
``labo-personnages-catalogue.json`` de ``resource/animation-assets/`` sont produits par ce script
(``python scripts/animation/humain_rig.py``) ; un test vérifie qu'ils n'ont pas dérivé. Toute retouche se
publie en ``v2/`` : la v1 reste immuable (les vidéos qui l'ont figée ne changent pas).

Nora et Sam sont des personnages **fictifs**, inventés pour le laboratoire, sans lien avec une personne réelle.
Voltix (#136) n'est pas modifié : ce module ne touche à aucun de ses fichiers.

Mêmes conventions que Voltix (voir ``docs/lody-personnages-labo.md``) :

- ``left`` / ``right`` = côté **du personnage** (son bras gauche est à droite de l'image) ;
- chaque groupe articulé porte ``data-pivot="x y"`` (canevas 600 × 900, avant pose) ;
- un groupe à variantes porte ``data-variants`` ; chaque enfant ``data-variant="nom"`` ; une seule visible ;
- ``data-slot`` marque un point d'accroche d'accessoire (les accessoires sont des assets séparés).

Mêmes noms de variantes que Voltix (sourcils, paupières, visèmes, mains) : le moteur de rendu (lipsync,
clignement automatique) les pilote sans adaptation. Pas de ressorts : un humain marche sur ses jambes
(``legs-left|right``) ; les poses n'utilisent ni ``lift`` ni ``air``.

Aucun réseau, aucune dépendance hors bibliothèque standard.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "resource" / "animation-assets"
CATALOG_NAME = "labo-personnages-catalogue.json"

W, H = 600, 900
GROUND = 882
STROKE = 5
CONTOUR = "#2A1F14"  # contour brun très foncé, jamais noir pur (comme Voltix)
COMMON = {
    "blanc": "#FFFFFF", "pupille": "#1A0F08", "bouche": "#6E2230", "langue": "#D9566B",
    "dents-trait": "#D8D0C2",
}

# ------------------------------------------------------------------------------------------- personnages
#
# Proportions stylisées d'adulte (≈ 5,5 têtes) : un visage assez grand pour rester lisible en 9:16 sur
# téléphone, sans l'allure enfantine d'une mascotte. Les deux silhouettes diffèrent nettement : Nora est
# élancée (épaules étroites, casque d'intercom, carré asymétrique), Sam est trapu (épaules larges, gilet
# orange évasé, cheveux courts bouclés et barbe courte).

CHARACTERS: dict[str, dict] = {
    "nora": {
        "name": "Nora",
        "role": "technicienne vidéo",
        "palette": {
            "peau": "#E3B08F", "peau-ombre": "#C99372", "cheveux": "#4A2C22", "cheveux-reflet": "#6B4234",
            "sourcils": "#3A2219", "iris": "#3E6B5A", "levres": "#B5655E",
            "tenue": "#1F2B4D", "tenue-ombre": "#16203B", "tenue-liseré": "#4FA3C7",
            "haut": "#D9DEE6", "ceinture": "#2B2F36", "boucle": "#B4BAC1",
            "boitier": "#3C4148", "boitier-ecran": "#7FD0E8", "casque": "#2E3238", "casque-mousse": "#555B63",
            "badge": "#F4F3EE", "cordon": "#4FA3C7",
            "pantalon": "#1F2B4D", "chaussure": "#2B2F36", "semelle": "#15171A",
        },
        "head": (300, 168, 56, 70), "neck": (236, 280, 17),
        "shoulder_y": 292, "shoulder_half": 84, "waist": (450, 60), "hip": (528, 70),
        "arm": {"shoulder": 70, "elbow": (82, 414), "wrist": (88, 520), "upper_w": 30, "fore_w": 26},
        "leg": {"x": 34, "w": 44, "ankle": 842}, "hold": (8, -50),
        "hair": "carre", "headset": True, "beard": False, "vest": False,
    },
    "sam": {
        "name": "Sam",
        "role": "technicien multimédia",
        "palette": {
            "peau": "#8A5A3C", "peau-ombre": "#73482E", "cheveux": "#1E1A18", "cheveux-reflet": "#3A322D",
            "sourcils": "#1E1A18", "iris": "#4A2E1C", "levres": "#6E3B2C", "barbe": "#2A221E",
            "tenue": "#5E6672", "tenue-ombre": "#4B525C", "gilet": "#F2802A", "gilet-ombre": "#D96A17",
            "bande": "#C9CED4", "poche": "#E07320", "stylo": "#2D6CDF",
            "ceinture": "#2B2F36", "boucle": "#B4BAC1",
            "pantalon": "#3A3F47", "chaussure": "#30343B", "semelle": "#F1F1EE",
        },
        "head": (300, 182, 62, 70), "neck": (246, 296, 23),
        "shoulder_y": 306, "shoulder_half": 112, "waist": (456, 94), "hip": (532, 92),
        "arm": {"shoulder": 96, "elbow": (110, 422), "wrist": (116, 522), "upper_w": 38, "fore_w": 32},
        "leg": {"x": 42, "w": 56, "ankle": 842}, "hold": (8, -70),
        "hair": "boucles", "headset": False, "beard": True, "vest": True,
    },
}

# Décalages des deux vues. « r » = côté droit du personnage (gauche de l'image), « l » = côté gauche.
VIEWS = {
    "face": {"title": "vue de face", "face_dx": 0, "far_k": 1.0, "mouth_sx": 1.0, "body_k": 1.0,
             "far_arm_behind": False, "nose_dx": 0, "ear_l": True, "hair_dx": 0},
    "trois-quarts": {"title": "vue de trois quarts", "face_dx": 18, "far_k": 0.78, "mouth_sx": 0.86,
                     "body_k": 0.9, "far_arm_behind": True, "nose_dx": 20, "ear_l": False, "hair_dx": 8},
}


def _f(value: float) -> str:
    text = f"{value:.1f}"
    return "0" if text in ("0.0", "-0.0") else (text[:-2] if text.endswith(".0") else text)


def _outline(width: float = STROKE) -> str:
    return f'stroke="{CONTOUR}" stroke-width="{_f(width)}" stroke-linejoin="round"'


def _tube(d: str, width: float, fill: str) -> str:
    """Membre « tuyau » : contour foncé épais puis remplissage plus fin, extrémités rondes."""
    return (f'<path d="{d}" fill="none" stroke="{CONTOUR}" stroke-width="{_f(width + 2 * STROKE)}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path d="{d}" fill="none" stroke="{fill}" stroke-width="{_f(width)}" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def _line(x0, y0, x1, y1) -> str:
    return f"M{_f(x0)},{_f(y0)} L{_f(x1)},{_f(y1)}"


class Figure:
    """Calcule les éléments d'un personnage pour une vue donnée."""

    def __init__(self, key: str, view: str):
        self.key, self.view = key, view
        self.c, self.v = CHARACTERS[key], VIEWS[view]
        self.p = self.c["palette"]
        hx, hy, rx, ry = self.c["head"]
        self.hx, self.hy, self.rx, self.ry = hx, hy, rx, ry
        self.k = self.v["body_k"]

    # ---------------------------------------------------------------------------------------- tête

    def eyes_pos(self) -> dict[str, tuple[float, float, float, float]]:
        dx, far = self.v["face_dx"], self.v["far_k"]
        y = self.hy - 2
        return {"right": (self.hx - 24 + dx, y, 11, 13), "left": (self.hx + 24 + dx * 0.9, y, 11 * far, 13)}

    def mouth_pos(self) -> tuple[float, float]:
        return self.hx + self.v["face_dx"] * 0.9, self.hy + 40

    def head_shape(self) -> str:
        """Visage : ovale légèrement anguleux au menton (adulte), oreilles derrière."""
        hx, hy, rx, ry = self.hx, self.hy, self.rx, self.ry
        dx = self.v["face_dx"] * 0.35
        d = (f"M{_f(hx - rx)},{_f(hy - 6)} C{_f(hx - rx)},{_f(hy - ry - 8)} {_f(hx + rx)},{_f(hy - ry - 8)} "
             f"{_f(hx + rx)},{_f(hy - 6)} C{_f(hx + rx)},{_f(hy + 30)} {_f(hx + 22 + dx)},{_f(hy + ry)} "
             f"{_f(hx + dx)},{_f(hy + ry)} C{_f(hx - 22 + dx)},{_f(hy + ry)} {_f(hx - rx)},{_f(hy + 30)} "
             f"{_f(hx - rx)},{_f(hy - 6)} Z")
        return d

    def ears(self) -> str:
        p, out = self.p, []
        sides = [("right", self.hx - self.rx + 2 + self.v["face_dx"] * 0.2)]
        if self.v["ear_l"]:
            sides.append(("left", self.hx + self.rx - 2))
        for side, x in sides:
            out.append(f'<ellipse id="ear-{side}" cx="{_f(x)}" cy="{_f(self.hy + 6)}" rx="10" ry="16" '
                       f'fill="{p["peau"]}" {_outline()}/>')
        return "".join(out)

    def hair_back(self) -> str:
        p, hx, hy, rx, ry = self.p, self.hx, self.hy, self.rx, self.ry
        dx = self.v["hair_dx"]
        if self.c["hair"] == "carre":
            # Carré mi-long : masse derrière le visage jusqu'à la mâchoire.
            d = (f"M{_f(hx - rx - 16 + dx)},{_f(hy + 44)} C{_f(hx - rx - 30 + dx)},{_f(hy - ry - 6)} "
                 f"{_f(hx + rx + 30 + dx)},{_f(hy - ry - 6)} {_f(hx + rx + 16 + dx)},{_f(hy + 44)} "
                 f"Q{_f(hx + rx + 2 + dx)},{_f(hy + 56)} {_f(hx + rx - 14 + dx)},{_f(hy + 46)} "
                 f"L{_f(hx - rx + 14 + dx)},{_f(hy + 46)} Q{_f(hx - rx - 2 + dx)},{_f(hy + 56)} "
                 f"{_f(hx - rx - 16 + dx)},{_f(hy + 44)} Z")
            return f'<g id="hair"><path d="{d}" fill="{p["cheveux"]}" {_outline()}/></g>'
        return '<g id="hair"/>'

    def hair_front(self) -> str:
        p, hx, hy, rx, ry = self.p, self.hx, self.hy, self.rx, self.ry
        dx = self.v["hair_dx"]
        if self.c["hair"] == "carre":
            # Frange asymétrique : raie côté gauche du personnage, mèche balayée vers sa droite.
            d = (f"M{_f(hx - rx - 4 + dx)},{_f(hy + 10)} C{_f(hx - rx - 10 + dx)},{_f(hy - ry - 20)} "
                 f"{_f(hx + rx + 12 + dx)},{_f(hy - ry - 22)} {_f(hx + rx + 4 + dx)},{_f(hy + 4)} "
                 f"Q{_f(hx + rx - 8 + dx)},{_f(hy - 30)} {_f(hx + 18 + dx)},{_f(hy - 44)} "
                 f"Q{_f(hx - 20 + dx)},{_f(hy - 30)} {_f(hx - rx + 8 + dx)},{_f(hy - 14)} "
                 f"Q{_f(hx - rx + 2 + dx)},{_f(hy)} {_f(hx - rx - 4 + dx)},{_f(hy + 10)} Z")
            shine = (f"M{_f(hx - 26 + dx)},{_f(hy - ry + 4)} Q{_f(hx + dx)},{_f(hy - ry - 6)} "
                     f"{_f(hx + 26 + dx)},{_f(hy - ry + 2)}")
            return (f'<g id="hair-front"><path d="{d}" fill="{p["cheveux"]}" {_outline()}/>'
                    f'<path d="{shine}" fill="none" stroke="{p["cheveux-reflet"]}" stroke-width="6" '
                    f'stroke-linecap="round"/></g>')
        # Cheveux courts bouclés : calotte festonnée qui suit le crâne.
        n, top = 9, []
        for i in range(n + 1):
            a = math.pi * (1 + i / n)
            top.append((hx + dx * 0.5 + (rx + 6) * math.cos(a), hy - 18 + (ry + 4) * math.sin(a)))
        d = f"M{_f(hx - rx - 4)},{_f(hy - 4)} L{_f(top[0][0])},{_f(top[0][1])}"
        for (x0, y0), (x1, y1) in zip(top, top[1:]):
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            nx, ny = mx - (hx + dx * 0.5), my - (hy - 18)
            norm = math.hypot(nx, ny) or 1
            d += f" Q{_f(mx + 12 * nx / norm)},{_f(my + 12 * ny / norm)} {_f(x1)},{_f(y1)}"
        d += (f" L{_f(hx + rx + 4)},{_f(hy - 4)} Q{_f(hx + rx - 6)},{_f(hy - 34)} {_f(hx + 10 + dx)},{_f(hy - 40)} "
              f"Q{_f(hx - rx + 4)},{_f(hy - 36)} {_f(hx - rx - 4)},{_f(hy - 4)} Z")
        curls = "".join(
            f'<circle cx="{_f(hx + dx * 0.5 + ox)}" cy="{_f(hy - ry + oy)}" r="5" fill="none" '
            f'stroke="{p["cheveux-reflet"]}" stroke-width="3"/>'
            for ox, oy in ((-30, 4), (-6, -6), (20, 0), (40, 14), (-44, 20)))
        return f'<g id="hair-front"><path d="{d}" fill="{p["cheveux"]}" {_outline()}/>{curls}</g>'

    def beard(self) -> str:
        if not self.c["beard"]:
            return ""
        p, hx, hy, ry = self.p, self.hx, self.hy, self.ry
        dx = self.v["face_dx"] * 0.35
        rx = self.rx
        # Barbe courte : bande le long de la mâchoire, moustache fine ; la bouche reste dégagée.
        d = (f"M{_f(hx - rx + 2)},{_f(hy + 4)} C{_f(hx - rx + 2)},{_f(hy + 36)} {_f(hx - 24 + dx)},{_f(hy + ry + 6)} "
             f"{_f(hx + dx)},{_f(hy + ry + 6)} C{_f(hx + 24 + dx)},{_f(hy + ry + 6)} {_f(hx + rx - 2)},{_f(hy + 36)} "
             f"{_f(hx + rx - 2)},{_f(hy + 4)} Q{_f(hx + rx - 10)},{_f(hy + 40)} {_f(hx + 16 + dx)},{_f(hy + 56)} "
             f"Q{_f(hx + dx)},{_f(hy + 62)} {_f(hx - 16 + dx)},{_f(hy + 56)} Q{_f(hx - rx + 10)},{_f(hy + 40)} "
             f"{_f(hx - rx + 2)},{_f(hy + 4)} Z")
        mx, my = self.mouth_pos()
        mous = (f"M{_f(mx - 20)},{_f(my - 6)} Q{_f(mx)},{_f(my - 16)} {_f(mx + 20)},{_f(my - 6)} "
                f"Q{_f(mx)},{_f(my - 10)} {_f(mx - 20)},{_f(my - 6)} Z")
        return (f'<g id="beard"><path d="{d}" fill="{p["barbe"]}" {_outline(3)}/>'
                f'<path d="{mous}" fill="{p["barbe"]}" stroke="{p["barbe"]}" stroke-width="5" '
                f'stroke-linejoin="round"/></g>')

    def nose(self) -> str:
        hx, hy = self.hx + self.v["nose_dx"] * 0.9, self.hy
        if self.view == "face":
            d = f"M{_f(hx - 3)},{_f(hy + 6)} Q{_f(hx - 7)},{_f(hy + 20)} {_f(hx)},{_f(hy + 22)} Q{_f(hx + 5)},{_f(hy + 22)} {_f(hx + 7)},{_f(hy + 19)}"
        else:
            d = f"M{_f(hx + 2)},{_f(hy + 2)} Q{_f(hx + 12)},{_f(hy + 18)} {_f(hx + 6)},{_f(hy + 22)} Q{_f(hx)},{_f(hy + 24)} {_f(hx - 6)},{_f(hy + 21)}"
        return (f'<g id="nose"><path d="{d}" fill="none" stroke="{self.p["peau-ombre"]}" stroke-width="4" '
                f'stroke-linecap="round" stroke-linejoin="round"/></g>')

    # Décalages (x, y) des sourcils par rapport au centre de l'œil : x > 0 = vers le nez pour l'œil de gauche
    # de l'image ; l'autre œil est mis en miroir. Une paire (droite, gauche) permet l'asymétrie.
    BROWS = {
        "neutre": (((-14, -21), (-1, -26), (12, -22)),) * 2,
        "joie": (((-14, -25), (-1, -31), (12, -26)),) * 2,
        "surprise": (((-14, -30), (-1, -38), (12, -32)),) * 2,
        "confusion": (((-14, -29), (-1, -37), (12, -31)), ((-14, -19), (0, -20), (12, -17))),
        "concentration": (((-14, -24), (0, -22), (12, -16)),) * 2,
    }

    def eyebrows(self) -> str:
        eyes = self.eyes_pos()
        parts = ['<g id="eyebrows" data-variants="">']
        for name, pair in self.BROWS.items():
            right, left = pair if len(pair) == 2 else (pair[0], pair[0])
            hidden = "" if name == "neutre" else ' display="none"'
            paths = []
            for (cx, cy, rx, _ry), offsets, sign in ((eyes["right"], right, 1), (eyes["left"], left, -1)):
                s = rx / 11
                (ax, ay), (bx, by), (ex, ey) = offsets
                d = (f"M{_f(cx + sign * ax * s)},{_f(cy + ay)} Q{_f(cx + sign * bx * s)},{_f(cy + by)} "
                     f"{_f(cx + sign * ex * s)},{_f(cy + ey)}")
                paths.append(f'<path d="{d}" fill="none" stroke="{self.p["sourcils"]}" stroke-width="6" '
                             f'stroke-linecap="round"/>')
            parts.append(f'<g id="eyebrows-{name}" data-variant="{name}"{hidden}>{"".join(paths)}</g>')
        parts.append("</g>")
        return "".join(parts)

    def eyes(self) -> tuple[str, str, str]:
        p = self.p
        eyes, pupils, half, closed = [], [], [], []
        for side, (cx, cy, rx, ry) in self.eyes_pos().items():
            eyes.append(f'<ellipse id="eye-{side}" cx="{_f(cx)}" cy="{_f(cy)}" rx="{_f(rx)}" ry="{_f(ry)}" '
                        f'fill="{COMMON["blanc"]}" {_outline(3.5)}/>')
            k = rx / 11
            px, py = cx + (1.5 if side == "right" else -1.5) * k, cy + 1
            pupils.append(
                f'<g clip-path="url(#clip-eye-{side})"><g id="pupil-{side}" data-pivot="{_f(px)} {_f(py)}">'
                f'<ellipse cx="{_f(px)}" cy="{_f(py)}" rx="{_f(7.5 * k)}" ry="7.5" fill="{p["iris"]}"/>'
                f'<ellipse cx="{_f(px)}" cy="{_f(py)}" rx="{_f(4 * k)}" ry="4" fill="{COMMON["pupille"]}"/>'
                f'<circle cx="{_f(px + 2.5 * k)}" cy="{_f(py - 3)}" r="2" fill="{COMMON["blanc"]}"/></g></g>')
            half.append(
                f'<g clip-path="url(#clip-eye-{side})"><rect x="{_f(cx - rx - 3)}" y="{_f(cy - ry - 3)}" '
                f'width="{_f(2 * rx + 6)}" height="{_f(ry + 3)}" fill="{p["peau"]}"/></g>'
                f'<path d="M{_f(cx - rx + 1)},{_f(cy)} Q{_f(cx)},{_f(cy + 2)} {_f(cx + rx - 1)},{_f(cy)}" '
                f'fill="none" stroke="{CONTOUR}" stroke-width="3.5" stroke-linecap="round"/>'
                f'<ellipse cx="{_f(cx)}" cy="{_f(cy)}" rx="{_f(rx)}" ry="{_f(ry)}" fill="none" {_outline(3.5)}/>')
            closed.append(
                f'<ellipse cx="{_f(cx)}" cy="{_f(cy)}" rx="{_f(rx + 1)}" ry="{_f(ry + 1)}" fill="{p["peau"]}"/>'
                f'<path d="M{_f(cx - rx)},{_f(cy + 1)} Q{_f(cx)},{_f(cy + 7)} {_f(cx + rx)},{_f(cy + 1)}" '
                f'fill="none" stroke="{CONTOUR}" stroke-width="4" stroke-linecap="round"/>')
        lids = ('<g id="lids" data-variants="">'
                '<g id="lids-ouverts" data-variant="ouverts"/>'
                f'<g id="lids-mi-clos" data-variant="mi-clos" display="none">{"".join(half)}</g>'
                f'<g id="lids-fermes" data-variant="fermes" display="none">{"".join(closed)}</g></g>')
        return f'<g id="eyes">{"".join(eyes)}</g>', f'<g id="pupils">{"".join(pupils)}</g>', lids

    # Bouches dessinées autour de (0, 0), largeur ≈ 40 : les sept premières sont les visèmes français.
    def _mouth_shape(self, name: str, d: str, teeth: str = "", tongue: str = "") -> str:
        clip = f'<clipPath id="clip-mouth-{name}"><path d="{d}"/></clipPath>'
        deco = f'<g clip-path="url(#clip-mouth-{name})">{tongue}{teeth}</g>' if (teeth or tongue) else ""
        return (f'{clip}<path d="{d}" fill="{COMMON["bouche"]}"/>{deco}'
                f'<path d="{d}" fill="none" stroke="{self.p["levres"]}" stroke-width="3.5" '
                f'stroke-linejoin="round"/>')

    @staticmethod
    def _teeth(y0: float, height: float, half: float) -> str:
        return (f'<rect x="{_f(-half)}" y="{_f(y0 - 10)}" width="{_f(2 * half)}" height="{_f(height + 10)}" '
                f'fill="{COMMON["blanc"]}"/><line x1="0" y1="{_f(y0)}" x2="0" y2="{_f(y0 + height)}" '
                f'stroke="{COMMON["dents-trait"]}" stroke-width="1.5"/>')

    @staticmethod
    def _tongue(cy: float, rx: float = 10, ry: float = 5) -> str:
        return f'<ellipse cx="0" cy="{_f(cy)}" rx="{_f(rx)}" ry="{_f(ry)}" fill="{COMMON["langue"]}"/>'

    def mouths(self) -> dict[str, str]:
        lip, ms = self.p["levres"], self._mouth_shape
        return {
            "mbp": (f'<path d="M-15,1 Q0,3 15,1" fill="none" stroke="{lip}" stroke-width="6" '
                    f'stroke-linecap="round"/><path d="M-15,1 Q0,3 15,1" fill="none" stroke="{CONTOUR}" '
                    f'stroke-width="2" stroke-linecap="round"/>'),
            "a": ms("a", "M-14,-4 Q0,-7 14,-4 Q14,17 0,20 Q-14,17 -14,-4 Z", self._teeth(-4, 4, 14),
                    self._tongue(17)),
            "e": ms("e", "M-17,-3 Q0,-6 17,-3 Q13,11 0,12 Q-13,11 -17,-3 Z", self._teeth(-3, 4, 17),
                    self._tongue(11, 11, 4)),
            "i": ms("i", "M-19,-2 Q0,-5 19,-2 Q12,7 0,7 Q-12,7 -19,-2 Z", self._teeth(-2, 6, 19)),
            "o": ms("o", "M0,-6 Q10,-6 10,5 Q10,17 0,17 Q-10,17 -10,5 Q-10,-6 0,-6 Z", tongue=self._tongue(14, 6, 3)),
            "ou": (ms("ou", "M0,-2 Q6,-2 6,4 Q6,10 0,10 Q-6,10 -6,4 Q-6,-2 0,-2 Z")
                   + f'<path d="M-9,-4 Q0,-8 9,-4" fill="none" stroke="{lip}" stroke-width="3" '
                     f'stroke-linecap="round"/>'),
            "fv": (ms("fv", "M-15,-3 Q0,-6 15,-3 Q12,7 0,7 Q-12,7 -15,-3 Z", self._teeth(-3, 5, 15))
                   + f'<path d="M-13,6 Q0,10 13,6" fill="none" stroke="{lip}" stroke-width="4" '
                     f'stroke-linecap="round"/>'),
            "repos": (f'<path d="M-15,0 Q0,6 15,0" fill="none" stroke="{CONTOUR}" stroke-width="3.5" '
                      f'stroke-linecap="round"/>'),
            "sourire": ms("sourire", "M-20,-4 Q0,0 20,-4 Q16,14 0,15 Q-16,14 -20,-4 Z", self._teeth(-4, 5, 20),
                          self._tongue(13, 10, 4)),
            "grimace": (f'<path d="M-16,4 Q-10,-2 -4,3 Q2,8 8,2 Q12,-2 16,2" fill="none" stroke="{CONTOUR}" '
                        f'stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>'),
        }

    def mouth(self) -> str:
        mx, my = self.mouth_pos()
        sx = self.v["mouth_sx"]
        parts = [f'<g id="mouth-visemes" data-variants="" transform="matrix({_f(sx) if sx != 1 else 1} 0 0 1 '
                 f'{_f(mx)} {_f(my)})">']
        for name, body in self.mouths().items():
            hidden = "" if name == "repos" else ' display="none"'
            parts.append(f'<g id="mouth-{name}" data-variant="{name}"{hidden}>{body}</g>')
        parts.append("</g>")
        return "".join(parts)

    def headset(self) -> str:
        if not self.c["headset"]:
            return ""
        p, hx, hy, rx, ry = self.p, self.hx, self.hy, self.rx, self.ry
        dx = self.v["hair_dx"]
        band = (f"M{_f(hx - rx - 8 + dx * 0.4)},{_f(hy + 2)} C{_f(hx - rx - 12 + dx)},{_f(hy - ry - 34)} "
                f"{_f(hx + rx + 12 + dx)},{_f(hy - ry - 34)} {_f(hx + rx + 8)},{_f(hy + 2)}")
        cup_x, cup_y = hx - rx - 8 + dx * 0.4, hy + 6
        mx, my = self.mouth_pos()
        boom = f"M{_f(cup_x + 4)},{_f(cup_y + 14)} Q{_f(cup_x + 10)},{_f(my + 8)} {_f(mx - 24)},{_f(my + 4)}"
        far = ""
        if self.view == "face":
            far = (f'<rect x="{_f(hx + rx - 2)}" y="{_f(hy - 14)}" width="18" height="36" rx="8" '
                   f'fill="{p["casque"]}" {_outline(4)}/>')
        return (f'<g id="headset">'
                f'{_tube(band, 7, p["casque"])}{far}'
                f'<rect x="{_f(cup_x - 12)}" y="{_f(cup_y - 22)}" width="24" height="44" rx="10" '
                f'fill="{p["casque"]}" {_outline(4)}/>'
                f'<rect x="{_f(cup_x - 6)}" y="{_f(cup_y - 14)}" width="12" height="28" rx="5" '
                f'fill="{p["casque-mousse"]}"/>'
                f'<path d="{boom}" fill="none" stroke="{CONTOUR}" stroke-width="7" stroke-linecap="round"/>'
                f'<path d="{boom}" fill="none" stroke="{p["casque"]}" stroke-width="3" stroke-linecap="round"/>'
                f'<ellipse cx="{_f(mx - 24)}" cy="{_f(my + 4)}" rx="6" ry="5" fill="{p["casque"]}" {_outline(3)}/>'
                f'</g>')

    def head(self) -> str:
        p = self.p
        nx, ny0 = self.hx, self.c["neck"][0]
        eyes, pupils, lids = self.eyes()
        return (f'<g id="head" data-pivot="{_f(nx)} {_f(ny0 + 8)}">'
                + self.hair_back() + self.ears()
                + f'<path id="face-skin" d="{self.head_shape()}" fill="{p["peau"]}" {_outline()}/>'
                + self.beard()
                + f'<g id="face">{eyes}{pupils}{lids}{self.eyebrows()}{self.nose()}{self.mouth()}</g>'
                + self.hair_front() + self.headset()
                + "</g>")

    # ---------------------------------------------------------------------------------------- corps

    def neck(self) -> str:
        y0, y1, half = self.c["neck"]
        x = self.hx + self.v["face_dx"] * 0.2
        return (f'<g id="neck"><rect x="{_f(x - half)}" y="{_f(y0)}" width="{_f(2 * half)}" '
                f'height="{_f(y1 - y0)}" fill="{self.p["peau"]}" {_outline()}/>'
                f'<rect x="{_f(x - half + 3)}" y="{_f(y0 + 4)}" width="{_f(2 * half - 6)}" height="10" '
                f'fill="{self.p["peau-ombre"]}"/></g>')

    def torso_path(self, inset: float = 0) -> str:
        c, k = self.c, self.k
        sy, sh = c["shoulder_y"], c["shoulder_half"] * k - inset
        (wy, wh), (hy, hh) = c["waist"], c["hip"]
        wh, hh = wh * k - inset, hh * k - inset
        return (f"M{_f(300 - sh + 22)},{_f(sy - 10 + inset)} L{_f(300 + sh - 22)},{_f(sy - 10 + inset)} "
                f"Q{_f(300 + sh)},{_f(sy - 6 + inset)} {_f(300 + sh)},{_f(sy + 24)} "
                f"L{_f(300 + wh)},{_f(wy)} L{_f(300 + hh)},{_f(hy)} L{_f(300 - hh)},{_f(hy)} "
                f"L{_f(300 - wh)},{_f(wy)} L{_f(300 - sh)},{_f(sy + 24)} "
                f"Q{_f(300 - sh)},{_f(sy - 6 + inset)} {_f(300 - sh + 22)},{_f(sy - 10 + inset)} Z")

    def torso(self) -> str:
        p, c = self.p, self.c
        sy = c["shoulder_y"]
        (wy, _), (hy, hh) = c["waist"], c["hip"]
        hh *= self.k
        body = self.torso_path()
        clip = "url(#clip-torso)"
        cx = 300 + self.v["face_dx"] * 0.25
        out = [f'<g id="body"><path d="{body}" fill="{p["tenue"]}"/>']
        # Ombre plate unique côté gauche du personnage (droite de l'image).
        out.append(f'<g clip-path="{clip}"><path d="M{_f(cx + 40)},{sy - 20} L{_f(420)},{sy - 20} L420,{hy + 10} '
                   f'L{_f(cx + 20)},{hy + 10} Z" fill="{p["tenue-ombre"]}" opacity="0.55"/></g>')
        if c["vest"]:
            out.append(self._vest(cx, sy, wy, hy, clip))
        else:
            out.append(self._jacket(cx, sy, wy, hy, clip))
        belt_y = hy - 26
        out.append(f'<g clip-path="{clip}"><rect x="160" y="{_f(belt_y)}" width="280" height="18" '
                   f'fill="{p["ceinture"]}"/></g>'
                   f'<rect x="{_f(cx - 12)}" y="{_f(belt_y - 2)}" width="24" height="22" rx="4" fill="none" '
                   f'stroke="{p["boucle"]}" stroke-width="4"/>')
        out.append(f'<path d="{body}" fill="none" {_outline()}/></g>')
        return "".join(out)

    def _jacket(self, cx, sy, wy, hy, clip) -> str:
        """Nora : veste bleu nuit à col en V sur haut clair, liserés cyan, badge sur cordon."""
        p = self.p
        collar = (f"M{_f(cx - 30)},{_f(sy - 10)} L{_f(cx)},{_f(sy + 44)} L{_f(cx + 30)},{_f(sy - 10)} Z")
        return (f'<path d="{collar}" fill="{p["haut"]}" {_outline(4)}/>'
                f'<g clip-path="{clip}">'
                f'<path d="M{_f(cx)},{_f(sy + 44)} L{_f(cx)},{_f(hy)}" stroke="{p["tenue-ombre"]}" stroke-width="4"/>'
                f'<path d="M{_f(cx - 30)},{_f(sy - 10)} L{_f(cx)},{_f(sy + 44)} L{_f(cx + 30)},{_f(sy - 10)}" '
                f'fill="none" stroke="{p["tenue-liseré"]}" stroke-width="4"/>'
                f'</g>'
                f'<path d="M{_f(cx - 22)},{_f(sy - 4)} Q{_f(cx - 6)},{_f(sy + 66)} {_f(cx + 34)},{_f(sy + 92)}" '
                f'fill="none" stroke="{p["cordon"]}" stroke-width="4" stroke-linecap="round"/>'
                f'<path d="M{_f(cx + 22)},{_f(sy - 4)} Q{_f(cx + 34)},{_f(sy + 50)} {_f(cx + 34)},{_f(sy + 92)}" '
                f'fill="none" stroke="{p["cordon"]}" stroke-width="4" stroke-linecap="round"/>'
                f'<g id="badge"><rect x="{_f(cx + 20)}" y="{_f(sy + 92)}" width="30" height="40" rx="4" '
                f'fill="{p["badge"]}" {_outline(3)}/><rect x="{_f(cx + 26)}" y="{_f(sy + 98)}" width="18" '
                f'height="14" rx="2" fill="{p["tenue-liseré"]}"/><path d="M{_f(cx + 26)},{_f(sy + 120)} '
                f'L{_f(cx + 44)},{_f(sy + 120)} M{_f(cx + 26)},{_f(sy + 126)} L{_f(cx + 40)},{_f(sy + 126)}" '
                f'stroke="{CONTOUR}" stroke-width="2"/></g>')

    def _vest(self, cx, sy, wy, hy, clip) -> str:
        """Sam : gilet orange ouvert sur t-shirt gris, deux bandes réfléchissantes, poche avec stylo."""
        p = self.p
        sh = self.c["shoulder_half"] * self.k
        hh = self.c["hip"][1] * self.k
        left = (f"M{_f(300 - sh + 18)},{_f(sy - 8)} L{_f(cx - 30)},{_f(sy - 8)} L{_f(cx - 14)},{_f(hy)} "
                f"L{_f(300 - hh - 10)},{_f(hy)} L{_f(300 - sh)},{_f(sy + 24)} Z")
        right = (f"M{_f(300 + sh - 18)},{_f(sy - 8)} L{_f(cx + 30)},{_f(sy - 8)} L{_f(cx + 14)},{_f(hy)} "
                 f"L{_f(300 + hh + 10)},{_f(hy)} L{_f(300 + sh)},{_f(sy + 24)} Z")
        strips = "".join(
            f'<rect x="150" y="{_f(y)}" width="300" height="12" fill="{p["bande"]}"/>' for y in (sy + 96, sy + 150))
        return (f'<path d="M{_f(cx - 26)},{_f(sy - 10)} Q{_f(cx)},{_f(sy + 12)} {_f(cx + 26)},{_f(sy - 10)}" '
                f'fill="none" stroke="{CONTOUR}" stroke-width="4"/>'
                f'<g id="vest"><clipPath id="clip-vest"><path d="{left}"/><path d="{right}"/></clipPath>'
                f'<path d="{left}" fill="{p["gilet"]}"/><path d="{right}" fill="{p["gilet"]}"/>'
                f'<g clip-path="url(#clip-vest)">{strips}<rect x="{_f(cx + 20)}" y="{_f(sy - 20)}" width="200" '
                f'height="{_f(hy - sy + 40)}" fill="{p["gilet-ombre"]}" opacity="0.45"/></g>'
                f'<path d="{left}" fill="none" {_outline(4)}/><path d="{right}" fill="none" {_outline(4)}/>'
                f'<rect x="{_f(cx - 70)}" y="{_f(sy + 40)}" width="34" height="34" rx="4" fill="{p["poche"]}" '
                f'{_outline(3)}/><rect x="{_f(cx - 62)}" y="{_f(sy + 28)}" width="6" height="22" rx="2" '
                f'fill="{p["stylo"]}" {_outline(2)}/>'
                f'<rect x="{_f(cx + 34)}" y="{_f(hy - 70)}" width="38" height="32" rx="4" fill="{p["poche"]}" '
                f'{_outline(3)}/></g>')

    def beltpack(self) -> str:
        """Nora : boîtier d'intercom à la ceinture (côté droit du personnage)."""
        if not self.c["headset"]:
            return ""
        p = self.p
        hy, hh = self.c["hip"][0], self.c["hip"][1] * self.k
        x, y = 300 - hh - 2, hy - 46
        return (f'<g id="beltpack"><rect x="{_f(x - 2)}" y="{_f(y)}" width="36" height="50" rx="6" '
                f'fill="{p["boitier"]}" {_outline(4)}/><rect x="{_f(x + 5)}" y="{_f(y + 8)}" width="22" '
                f'height="10" rx="2" fill="{p["boitier-ecran"]}"/><circle cx="{_f(x + 16)}" cy="{_f(y + 32)}" '
                f'r="6" fill="{p["casque-mousse"]}" {_outline(2)}/></g>')

    def _hand(self, side: str, wx: float, wy: float, mirror: bool) -> str:
        """Main stylisée orientée vers le bas, poignet en (wx, wy) ; trois variantes et un point d'accroche."""
        skin = self.p["peau"]
        s = -1 if mirror else 1  # côté pouce : vers le corps
        cx, cy = wx, wy + 16

        def finger(x0, y0, x1, y1, w=7):
            return _tube(_line(x0, y0, x1, y1), w, skin)

        palm = (f'<rect x="{_f(cx - 13)}" y="{_f(cy - 13)}" width="26" height="28" rx="11" fill="{skin}" '
                f'{_outline(4)}/>')
        palm_fill = f'<rect x="{_f(cx - 10)}" y="{_f(cy - 10)}" width="20" height="22" rx="9" fill="{skin}"/>'
        open_ = (finger(cx - 8 * s, cy + 6, cx - 11 * s, cy + 26) + finger(cx - 2 * s, cy + 8, cx - 2 * s, cy + 30)
                 + finger(cx + 5 * s, cy + 8, cx + 6 * s, cy + 29) + finger(cx + 10 * s, cy + 5, cx + 13 * s, cy + 23)
                 + finger(cx + 12 * s, cy - 4, cx + 23 * s, cy + 8) + palm + palm_fill)
        fist = (f'<rect x="{_f(cx - 15)}" y="{_f(cy - 12)}" width="30" height="30" rx="13" fill="{skin}" '
                f'{_outline(4)}/>'
                f'<path d="M{_f(cx - 9)},{_f(cy + 8)} L{_f(cx + 9)},{_f(cy + 8)}" stroke="{CONTOUR}" '
                f'stroke-width="2.5" stroke-linecap="round"/>'
                f'<ellipse cx="{_f(cx + 13 * s)}" cy="{_f(cy - 2)}" rx="6" ry="9" fill="{skin}" {_outline(3.5)}/>')
        index = finger(cx, cy + 10, cx, cy + 40) + fist
        return (f'<g id="hand-{side}" data-pivot="{_f(wx)} {_f(wy)}" data-variants="">'
                f'<g id="hand-{side}-ouverte" data-variant="ouverte">{open_}</g>'
                f'<g id="hand-{side}-poing" data-variant="poing" display="none">{fist}</g>'
                f'<g id="hand-{side}-index" data-variant="index" display="none">{index}</g>'
                f'<g id="hand-{side}-slot" data-slot="" data-pivot="{_f(cx)} {_f(cy + 2)}"/></g>')

    def arm(self, side: str) -> str:
        a, p = self.c["arm"], self.p
        mirror = side == "left"
        k = self.k
        dx = 0
        if self.view == "trois-quarts":
            dx = -16 if mirror else 6

        def X(off):  # noqa: N802 - coordonnée miroir autour de l'axe du corps
            return 300 + off * k + dx if mirror else 300 - off * k + dx

        sh = (X(a["shoulder"]), self.c["shoulder_y"] + 14)
        el = (X(a["elbow"][0]), a["elbow"][1])
        wr = (X(a["wrist"][0]), a["wrist"][1])
        sleeve = p["tenue"]
        return (f'<g id="arm-{side}" data-pivot="{_f(sh[0])} {_f(sh[1])}">'
                f'{_tube(_line(*sh, *el), a["upper_w"], sleeve)}'
                f'<g id="forearm-{side}" data-pivot="{_f(el[0])} {_f(el[1])}">'
                f'{_tube(_line(*el, *wr), a["fore_w"], sleeve)}'
                f'<path d="M{_f(wr[0] - a["fore_w"] / 2 - 1)},{_f(wr[1] - 6)} '
                f'L{_f(wr[0] + a["fore_w"] / 2 + 1)},{_f(wr[1] - 6)}" stroke="{p["tenue-ombre"]}" '
                f'stroke-width="5" stroke-linecap="round"/>'
                f'{self._hand(side, wr[0], wr[1] + 4, mirror)}</g></g>')

    def leg(self, side: str) -> str:
        lg, p = self.c["leg"], self.p
        mirror = side == "left"
        x = 300 + (lg["x"] if mirror else -lg["x"]) * self.k
        if self.view == "trois-quarts":
            x += 6 if mirror else -2
        hip_y = self.c["hip"][0] - 14
        return (f'<g id="legs-{side}" data-pivot="{_f(x)} {_f(hip_y)}">'
                f'{_tube(_line(x, hip_y, x, lg["ankle"]), lg["w"], p["pantalon"])}</g>')

    def shoe(self, side: str) -> str:
        lg, p = self.c["leg"], self.p
        mirror = side == "left"
        x = 300 + (lg["x"] if mirror else -lg["x"]) * self.k
        d = 1 if mirror else -1
        if self.view == "trois-quarts":
            x += 6 if mirror else -2
            d = 1
        half = lg["w"] / 2 + 4

        def X(o):  # noqa: N802
            return _f(x + d * o)

        body = (f"M{X(-half)},{GROUND} L{X(-half)},{GROUND - 26} Q{X(-half)},{GROUND - 44} {X(0)},{GROUND - 44} "
                f"Q{X(half + 6)},{GROUND - 44} {X(half + 18)},{GROUND - 22} L{X(half + 22)},{GROUND} Z")
        sole = f"M{X(-half)},{GROUND - 8} L{X(half + 22)},{GROUND - 8} L{X(half + 22)},{GROUND} L{X(-half)},{GROUND} Z"
        return (f'<g id="shoe-{side}"><path d="{body}" fill="{p["chaussure"]}"/><path d="{sole}" '
                f'fill="{p["semelle"]}"/><path d="{body}" fill="none" {_outline()}/></g>')

    def accessories(self) -> str:
        c = self.c
        cx = 300 + self.v["face_dx"] * 0.25
        belt = 300 - c["hip"][1] * self.k + 16
        return ('<g id="accessories">'
                f'<g id="slot-chest" data-slot="" data-pivot="{_f(cx)} {_f(c["waist"][0] - 20)}"/>'
                f'<g id="slot-belt" data-slot="" data-pivot="{_f(belt)} {_f(c["hip"][0] - 20)}"/></g>')

    def build(self) -> str:
        c = self.c
        hip_y = c["hip"][0]
        eyes = self.eyes_pos()
        defs = (f'<defs><clipPath id="clip-torso"><path d="{self.torso_path()}"/></clipPath>'
                + "".join(f'<clipPath id="clip-eye-{side}"><ellipse cx="{_f(e[0])}" cy="{_f(e[1])}" '
                          f'rx="{_f(e[2])}" ry="{_f(e[3])}"/></clipPath>' for side, e in eyes.items())
                + "</defs>")
        far = self.v["far_arm_behind"]
        upper = (f'<g id="upper" data-pivot="300 {_f(hip_y)}">'
                 + (self.arm("left") if far else "")
                 + self.neck() + self.torso() + self.beltpack() + self.head() + self.accessories()
                 + self.arm("right") + ("" if far else self.arm("left"))
                 + "</g>")
        title = f'{c["name"]} — {self.v["title"]}'
        return _svg(title, W, H, defs
                    + f'<g id="shadow"><ellipse cx="300" cy="{GROUND + 2}" rx="130" ry="11" fill="#000000" '
                      f'opacity="0.16"/></g>'
                    + f'<g id="puppet" data-pivot="300 {GROUND}">'
                    + self.leg("right") + self.leg("left")
                    + f'<g id="shoes">{self.shoe("right")}{self.shoe("left")}</g>'
                    + upper + "</g>")


def build_character(key: str, view: str) -> str:
    return Figure(key, view).build()


# ------------------------------------------------------------------------------------------------ rig

PARTS = ["puppet", "shadow", "upper", "body", "neck", "head", "hair", "hair-front", "face", "eyes", "pupils",
         "lids", "eyebrows", "nose", "mouth-visemes", "arm-left", "arm-right", "forearm-left", "forearm-right",
         "hand-left", "hand-right", "legs-left", "legs-right", "shoes", "accessories"]
EXTRA_PARTS = {"nora": ["headset", "beltpack", "badge"], "sam": ["beard", "vest"]}
VARIANTS = {
    "eyebrows": list(Figure.BROWS),
    "lids": ["ouverts", "mi-clos", "fermes"],
    "mouth-visemes": ["mbp", "a", "e", "i", "o", "ou", "fv", "repos", "sourire", "grimace"],
    "hand-left": ["ouverte", "poing", "index"],
    "hand-right": ["ouverte", "poing", "index"],
}
SLOTS = ["slot-chest", "slot-belt", "hand-left-slot", "hand-right-slot"]
ROTATED = ["head", "arm-left", "arm-right", "forearm-left", "forearm-right", "hand-left", "hand-right"]


def build_rig(key: str) -> dict:
    """Rig ``lody.rig/1`` : mêmes noms d'expressions, de visèmes et de variantes que Voltix."""
    def key_(frame, **pose):
        return {"frame": frame, "pose": pose}

    # « tenir-cable » : épaule puis coude calculés pour que les deux poings se rejoignent devant le ventre.
    arm, fore = CHARACTERS[key]["hold"]

    return {
        "schema": "lody.rig/1",
        "asset": key,
        "version": 1,
        "convention": ("left/right = côté du personnage (son bras gauche est à droite de l'image). Rotations en "
                       "degrés, sens horaire positif (repère SVG). Pivots : attribut data-pivot des groupes du "
                       "SVG. Personnage humain : pas de ressorts, ni lift ni air dans les gestes."),
        "parts": PARTS + EXTRA_PARTS[key],
        "variants": VARIANTS,
        "slots": SLOTS,
        "rest": {
            "select": {"eyebrows": "neutre", "lids": "ouverts", "mouth-visemes": "repos", "hand-left": "ouverte",
                       "hand-right": "ouverte"},
            "rotate": {name: 0 for name in ROTATED},
            "pupils": [0, 0], "lift": 0, "squash": 0, "air": 0,
        },
        "expressions": {
            "neutre": {"select": {"eyebrows": "neutre", "lids": "ouverts", "mouth-visemes": "repos"},
                       "pupils": [0, 0]},
            "sourire": {"select": {"eyebrows": "joie", "lids": "ouverts", "mouth-visemes": "sourire"},
                        "pupils": [0, 0]},
            "surprise": {"select": {"eyebrows": "surprise", "lids": "ouverts", "mouth-visemes": "o"},
                         "pupils": [0, -2]},
            "confusion": {"select": {"eyebrows": "confusion", "lids": "ouverts", "mouth-visemes": "grimace"},
                          "pupils": [3, -3], "rotate": {"head": 5}},
            "concentration": {"select": {"eyebrows": "concentration", "lids": "mi-clos", "mouth-visemes": "mbp"},
                              "pupils": [0, 3]},
        },
        "visemes": {
            "mbp": {"phonemes": ["m", "b", "p"], "exemple": "maman, bonjour, pomme"},
            "a": {"phonemes": ["a", "â"], "exemple": "câble, ça va"},
            "e": {"phonemes": ["é", "è", "ê", "e", "eu"], "exemple": "vidéo, fête, le"},
            "i": {"phonemes": ["i", "y", "s", "z", "t", "d", "n", "l", "k", "g", "r"],
                  "exemple": "signal, si, tension"},
            "o": {"phonemes": ["o", "au", "on", "an", "en"], "exemple": "vidéo, son, an"},
            "ou": {"phonemes": ["ou", "u", "w", "ch", "j"], "exemple": "pour, tu, oui, chou"},
            "fv": {"phonemes": ["f", "v"], "exemple": "fil, vingt"},
            "silence": {"phonemes": [], "mouth": "repos"},
        },
        "gestures": {
            "cligner": {"frames": 6, "sheet_key": 2, "keys": [
                key_(0, select={"lids": "ouverts"}), key_(1, select={"lids": "mi-clos"}),
                key_(2, select={"lids": "fermes"}), key_(4, select={"lids": "mi-clos"}),
                key_(6, select={"lids": "ouverts"})]},
            "saluer": {"frames": 30, "sheet_key": 2, "keys": [
                key_(0),
                key_(6, rotate={"arm-left": -150, "forearm-left": -20, "head": 4}, select={"hand-left": "ouverte"}),
                key_(11, rotate={"arm-left": -150, "forearm-left": -45}),
                key_(16, rotate={"arm-left": -150, "forearm-left": -15}),
                key_(21, rotate={"arm-left": -150, "forearm-left": -45}),
                key_(30, rotate={"arm-left": 0, "forearm-left": 0, "head": 0})]},
            "montrer": {"frames": 24, "sheet_key": 1, "keys": [
                key_(0),
                key_(8, rotate={"arm-right": 100, "forearm-right": 6, "head": -4}, select={"hand-right": "index"},
                     pupils=[-4, 0]),
                key_(24, rotate={"arm-right": 100, "forearm-right": 6, "head": -4}, select={"hand-right": "index"},
                     pupils=[-4, 0])]},
            "tenir-cable": {"frames": 12, "sheet_key": 1, "keys": [
                key_(0),
                key_(12, rotate={"arm-right": arm, "forearm-right": fore, "arm-left": -arm, "forearm-left": -fore},
                     select={"hand-right": "poing", "hand-left": "poing"}, pupils=[0, 4],
                     accessories=[{"asset": "cable-sdi", "slot": "slot-chest", "scale": 0.5}])]},
            "expliquer": {"frames": 30, "sheet_key": 1, "keys": [
                key_(0),
                key_(8, rotate={"arm-right": 24, "forearm-right": -96, "hand-right": -10},
                     select={"hand-right": "ouverte"}),
                key_(16, rotate={"arm-right": 30, "forearm-right": -84, "hand-right": 6}),
                key_(24, rotate={"arm-right": 24, "forearm-right": -96, "hand-right": -10}),
                key_(30, rotate={"arm-right": 0, "forearm-right": 0, "hand-right": 0})]},
            "pencher-tete": {"frames": 20, "sheet_key": 1, "keys": [
                key_(0), key_(8, rotate={"head": 8}, pupils=[2, -2]), key_(20, rotate={"head": 8}, pupils=[2, -2])]},
        },
    }


# ------------------------------------------------------------------------------------------- écriture

def _svg(title: str, width: int, height: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}"><title>{title}</title>{body}</svg>\n')


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rig_text(key: str) -> str:
    return json.dumps(build_rig(key), ensure_ascii=False, indent=2) + "\n"


def outputs() -> dict[str, str]:
    """Chemin relatif (au dossier ``resource/``) → contenu, pour toute la version 1 de Nora et Sam."""
    out: dict[str, str] = {}
    for key in CHARACTERS:
        for view in VIEWS:
            out[f"animation-assets/{key}/v1/{key}-{view}.svg"] = build_character(key, view)
        out[f"animation-assets/{key}/v1/{key}-rig.json"] = _rig_text(key)
    return out


def _manifest_sha(manifest: dict) -> str:
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


ASSET_IDS = {"nora": "ast_173000000001", "sam": "ast_173000000002"}
DESCRIPTIONS = {
    "nora": "Technicienne vidéo fictive (#173) : tenue bleu nuit, casque d'intercom, boîtier à la ceinture, "
            "badge sur cordon ; silhouette élancée. Personnage de laboratoire, à valider.",
    "sam": "Technicien multimédia fictif (#173) : gilet orange à bandes réfléchissantes sur t-shirt gris, "
           "cheveux courts bouclés, barbe courte ; silhouette trapue. Personnage de laboratoire, à valider.",
}


def build_catalog(resource: Path) -> dict:
    assets = []
    for key, c in CHARACTERS.items():
        rig = json.loads((resource / f"animation-assets/{key}/v1/{key}-rig.json").read_text(encoding="utf-8"))
        base = f"animation-assets/{key}/v1/{key}"
        manifest = {
            "format": "svg", "width": W, "height": H, "anchor": {"x": 0.5, "y": round(GROUND / H, 4)},
            "parts": rig["parts"], "expressions": sorted(rig["expressions"]), "gestures": sorted(rig["gestures"]),
            "files": [{"role": role, "path": rel, "sha256": _sha(resource / rel)} for role, rel in (
                ("base", f"{base}-face.svg"), ("trois-quarts", f"{base}-trois-quarts.svg"), ("rig", f"{base}-rig.json"))],
        }
        assets.append({
            "id": ASSET_IDS[key], "name": f"{c['name']} — marionnette", "kind": "character",
            "description": DESCRIPTIONS[key], "is_active": True, "character": c["name"], "location": None,
            "versions": [{"version": 1, "manifest": manifest, "sha256": _manifest_sha(manifest),
                          "notes": "Version 1 (laboratoire) : vues de face et 3/4, 5 expressions, 7 visèmes, "
                                   f"{len(rig['gestures'])} gestes."}],
        })
    return {"schema": "lody.animation-assets/1", "assets": assets}


def write_all(resource: Path = ROOT / "resource") -> list[Path]:
    written = []
    for rel, content in outputs().items():
        path = resource / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    catalog = resource / "animation-assets" / CATALOG_NAME
    catalog.write_text(json.dumps(build_catalog(resource), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written.append(catalog)
    return written


if __name__ == "__main__":
    for item in write_all():
        print(item.relative_to(ROOT))
    sys.exit(0)
