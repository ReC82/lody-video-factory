"""Quatre décors 2D réutilisables du laboratoire pédagogique (#175) et leur catalogue importable.

Source unique de la **version 1** : ``python scripts/animation/decors_labo.py`` écrit, pour chaque décor,
dans ``resource/animation-assets/<décor>/v1/`` :

- ``<décor>.svg`` — le décor complet (1080 × 1920, 9:16) = couches ``fond`` + ``objets`` + ``avant`` ;
- ``<décor>-fond.svg``, ``<décor>-objets.svg``, ``<décor>-avant.svg`` — les mêmes couches séparées, pour
  intercaler les personnages : fond → objets → **personnages** → avant-plan ;
- ``<décor>-scene.json`` (``lody.decor/1``) — zones (sous-titres, titre, action), emplacements des
  personnages et points d'intérêt des objets (boîte englobante et point de visée pour flèches et zooms),
  calculés depuis la géométrie des SVG.

puis ``resource/animation-assets/labo-decors-catalogue.json`` (``lody.animation-assets/1``, type
``background``). Un test vérifie que les fichiers versionnés sont exactement ceux du générateur. Une retouche
à la main se publie en **version 2** dans un nouveau dossier ``v2/`` : la v1 reste immuable.

Règles de dessin : formes simples à coordonnées absolues (rect, circle, ellipse, line, polygon, polyline),
aucun ``transform``, aucun texte, aucune marque, aucun dégradé ni ressource externe ; la bande des
sous-titres (bas de l'image) ne contient que le sol uni. Bibliothèque standard uniquement, aucun réseau.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "resource" / "animation-assets"
CATALOG_NAME = "labo-decors-catalogue.json"
SVG_NS = "http://www.w3.org/2000/svg"

W, H = 1080, 1920
LAYERS = ("fond", "objets", "avant")
DECOR_SCHEMA = "lody.decor/1"
# Bande des sous-titres : Remotion (LodyScene) les pose à 80 % de la hauteur ; marge au-dessus et en dessous.
SUBTITLES = {"x": 40, "y": 1500, "width": 1000, "height": 360}
TITLE = {"x": 60, "y": 60, "width": 960, "height": 170}
ACTION = {"x": 0, "y": 0, "width": W, "height": 1500}
WALL_BOTTOM = 1240   # jonction mur / sol
FEET = 1480          # pieds des personnages : juste au-dessus de la bande des sous-titres
CHARACTER_HEIGHT = 820  # hauteur conseillée d'un personnage debout (pixels du canevas)

C = {
    "contour": "#22262E",
    "noir": "#1B1D22",
    "gris-fonce": "#3A3F48",
    "gris": "#6C737E",
    "gris-clair": "#B9C0C9",
    "metal": "#D3D8DE",
    "blanc": "#F6F7F9",
    "ecran": "#2B3A4F",
    "ecran-reflet": "#3E516B",
    "vert": "#3FB56B",
    "rouge": "#E2433B",
    "bleu-vga": "#2F64C9",
    "ambre": "#F2B233",
}

ASSET_IDS = {
    "decor-salon": "ast_175000000001",
    "decor-bureau": "ast_175000000002",
    "decor-plateau": "ast_175000000003",
    "decor-regie": "ast_175000000004",
}


# --------------------------------------------------------------------------------------------- primitives
def _n(value: float) -> str:
    value = round(value, 1)
    return str(int(value)) if value == int(value) else str(value)


def _attrs(**attrs) -> str:
    parts = []
    for key, value in attrs.items():
        if value is None:
            continue
        name = key.rstrip("_").replace("_", "-")
        parts.append(f'{name}="{_n(value) if isinstance(value, (int, float)) else value}"')
    return " ".join(parts)


def rect(x, y, w, h, fill, stroke=C["contour"], sw=4, rx=None, **extra) -> str:
    return f"<rect {_attrs(x=x, y=y, width=w, height=h, rx=rx, fill=fill, stroke=stroke, stroke_width=sw if stroke != 'none' else None, **extra)}/>"


def circle(cx, cy, r, fill, stroke=C["contour"], sw=3) -> str:
    return f"<circle {_attrs(cx=cx, cy=cy, r=r, fill=fill, stroke=stroke, stroke_width=sw if stroke != 'none' else None)}/>"


def line(x1, y1, x2, y2, stroke=C["contour"], sw=4) -> str:
    return f'<line {_attrs(x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, stroke_width=sw)} stroke-linecap="round"/>'


def _pts(points) -> str:
    return " ".join(f"{_n(x)},{_n(y)}" for x, y in points)


def poly(points, fill, stroke=C["contour"], sw=4) -> str:
    return (f'<polygon points="{_pts(points)}" {_attrs(fill=fill, stroke=stroke, stroke_width=sw if stroke != "none" else None)}'
            ' stroke-linejoin="round"/>')


def cable(points, color=C["noir"], sw=12) -> str:
    return (f'<polyline points="{_pts(points)}" fill="none" stroke="{color}" stroke-width="{sw}"'
            ' stroke-linecap="round" stroke-linejoin="round"/>')


def group(gid: str, *children: str, **data) -> str:
    extra = "".join(f' data-{k.replace("_", "-")}="{v}"' for k, v in data.items())
    return f'<g id="{gid}"{extra}>' + "".join(children) + "</g>"


def _rotate(points, cx, cy, degrees):
    a = math.radians(degrees)
    cos, sin = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * cos - (y - cy) * sin, cy + (x - cx) * sin + (y - cy) * cos) for x, y in points]


# ------------------------------------------------------------------------ connecteurs (formes normalisées)
def hdmi_port(cx, cy, w=44, angle=0, fill=C["noir"]) -> str:
    """Embase HDMI type A : trapèze à pans coupés en bas (plus large en haut), languette centrale."""
    h = w * 0.32
    x0, y0 = cx - w / 2, cy - h / 2
    outer = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h * 0.55), (x0 + w - h * 0.45, y0 + h),
             (x0 + h * 0.45, y0 + h), (x0, y0 + h * 0.55)]
    tongue = [(cx - w * 0.36, cy - h * 0.12), (cx + w * 0.36, cy - h * 0.12),
              (cx + w * 0.36, cy + h * 0.08), (cx - w * 0.36, cy + h * 0.08)]
    return (poly(_rotate(outer, cx, cy, angle), fill, C["gris-clair"], 2)
            + poly(_rotate(tongue, cx, cy, angle), C["gris"], "none"))


def dp_port(cx, cy, w=40, angle=0) -> str:
    """Embase DisplayPort : rectangle à un seul coin coupé (asymétrique, détrompeur)."""
    h = w * 0.4
    x0, y0 = cx - w / 2, cy - h / 2
    outer = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0 + h * 0.5, y0 + h), (x0, y0 + h * 0.5)]
    tongue = [(cx - w * 0.34, cy - h * 0.1), (cx + w * 0.38, cy - h * 0.1),
              (cx + w * 0.38, cy + h * 0.1), (cx - w * 0.34, cy + h * 0.1)]
    return (poly(_rotate(outer, cx, cy, angle), C["noir"], C["gris-clair"], 2)
            + poly(_rotate(tongue, cx, cy, angle), C["gris"], "none"))


def vga_port(cx, cy, w=70) -> str:
    """Embase VGA (D-Sub HD15) : trapèze en D, 15 contacts sur 3 rangées de 5, deux vis de fixation."""
    h = w * 0.38
    x0, y0 = cx - w / 2, cy - h / 2
    shell = [(x0, y0), (x0 + w, y0), (x0 + w - h * 0.25, y0 + h), (x0 + h * 0.25, y0 + h)]
    out = [rect(cx - w * 0.85, cy - h * 0.95, w * 1.7, h * 1.9, C["bleu-vga"], C["contour"], 2, rx=3),
           poly(shell, C["noir"], C["gris-clair"], 2)]
    for row, (count, offset) in enumerate(((5, 0.0), (5, 0.5), (5, 0.0))):
        for i in range(count):
            px = x0 + h * 0.3 + (i + offset * 0.5) * (w - h * 0.6) / 4.6
            py = y0 + h * (0.25 + row * 0.25)
            out.append(circle(px, py, 1.6, C["gris-clair"], "none"))
    out += [circle(cx - w * 0.68, cy, 5, C["metal"], C["contour"], 1.5),
            circle(cx + w * 0.68, cy, 5, C["metal"], C["contour"], 1.5)]
    return "".join(out)


def bnc_port(cx, cy, r=12) -> str:
    """Embase BNC vue de face : fût métallique, isolant blanc, contact central."""
    return (circle(cx, cy, r, C["metal"], C["contour"], 2) + circle(cx, cy, r * 0.55, C["blanc"], "none")
            + circle(cx, cy, r * 0.18, C["ambre"], "none"))


# ----------------------------------------------------------------------------------------------- décors
def _wall_floor(wall: str, floor: str, base: str) -> str:
    return group("fond",
                 rect(0, 0, W, WALL_BOTTOM, wall, "none"),
                 rect(0, WALL_BOTTOM, W, H - WALL_BOTTOM, floor, "none"),
                 rect(0, WALL_BOTTOM - 14, W, 14, base, "none"),
                 line(0, WALL_BOTTOM, W, WALL_BOTTOM, C["contour"], 3))


def salon() -> dict:
    fond = _wall_floor("#ECE3D2", "#B38A63", "#D9CDB8")
    tv_x, tv_y, tv_w, tv_h = 210, 560, 660, 372  # 16:9
    meuble = group("meuble-tv",
                   rect(250, 1060, 580, 250, "#C7A27C"),
                   rect(282, 1120, 516, 170, "#8F6E4E", C["contour"], 3),
                   rect(270, 1310, 26, 24, C["gris-fonce"], "none"),
                   rect(784, 1310, 26, 24, C["gris-fonce"], "none"))
    pied = group("pied-tv",
                 rect(515, tv_y + tv_h, 50, 60, C["gris-fonce"]),
                 rect(430, 1040, 220, 20, C["gris-fonce"], rx=6))
    tv = group("tv",
               rect(tv_x, tv_y, tv_w, tv_h, C["noir"], rx=10),
               rect(tv_x + 14, tv_y + 14, tv_w - 28, tv_h - 42, C["ecran"], "none"),
               poly([(tv_x + 380, tv_y + 14), (tv_x + 520, tv_y + 14), (tv_x + 300, tv_y + tv_h - 28),
                     (tv_x + 160, tv_y + tv_h - 28)], C["ecran-reflet"], "none"),
               circle(tv_x + tv_w - 36, tv_y + tv_h - 14, 4, C["rouge"], "none"))
    # Panneau de connexion latéral du téléviseur (côté droit) : embases HDMI orientées vers le côté.
    tv_panel = group("prise-hdmi-tv",
                     rect(tv_x + tv_w, 780, 30, 130, C["gris-fonce"], rx=4),
                     hdmi_port(tv_x + tv_w + 15, 812, 36, 90),
                     hdmi_port(tv_x + tv_w + 15, 868, 36, 90))
    box = group("box",
                rect(360, 1200, 300, 90, C["gris-fonce"], rx=8),
                rect(380, 1222, 120, 30, C["noir"], "none", rx=4),
                circle(630, 1236, 7, C["vert"], "none"))
    box_port = group("prise-hdmi-box",
                     rect(660, 1206, 34, 78, C["gris"], rx=4),
                     hdmi_port(677, 1245, 34, 90))
    # Câble HDMI : sort de la box, longe l'arrière du meuble puis remonte jusqu'au panneau du téléviseur.
    cable_hdmi = group("cable-hdmi",
                       cable([(706, 1245), (740, 1245), (740, 1080), (930, 1080), (930, 868), (904, 868)]),
                       rect(694, 1233, 24, 24, C["noir"], C["gris-clair"], 2, rx=3),
                       rect(894, 856, 24, 24, C["noir"], C["gris-clair"], 2, rx=3))
    telecommande = group("telecommande",
                         rect(120, 1222, 110, 30, C["noir"], rx=12),
                         circle(140, 1237, 5, C["rouge"], "none"),
                         circle(165, 1237, 4, C["gris-clair"], "none"),
                         circle(185, 1237, 4, C["gris-clair"], "none"),
                         circle(205, 1237, 4, C["gris-clair"], "none"))
    table_basse = group("table-basse",
                     rect(70, 1252, 210, 22, "#C7A27C"),
                     rect(90, 1274, 16, 70, "#8F6E4E", C["contour"], 3),
                     rect(244, 1274, 16, 70, "#8F6E4E", C["contour"], 3))
    objets = group("objets", meuble, pied, tv, tv_panel, box, cable_hdmi, box_port, table_basse, telecommande)
    avant = group("avant", group("accoudoir",
                                 rect(-20, 1300, 120, 190, "#5E7D9A", rx=26),
                                 rect(-20, 1300, 120, 40, "#6E8FAD", "none", rx=20)))
    return {
        "slug": "decor-salon", "name": "Décor — salon (box, HDMI, téléviseur)",
        "description": ("Salon schématique : téléviseur sur meuble bas, box sur l'étagère, câble HDMI visible "
                        "de la box au panneau latéral du téléviseur, télécommande. Sans marque ni texte."),
        "layers": {"fond": fond, "objets": objets, "avant": avant},
        "labels": {"tv": "Téléviseur", "pied-tv": "Pied du téléviseur", "prise-hdmi-tv": "Entrées HDMI du téléviseur",
                   "box": "Box TV", "prise-hdmi-box": "Sortie HDMI de la box", "cable-hdmi": "Câble HDMI",
                   "meuble-tv": "Meuble TV", "telecommande": "Télécommande", "table-basse": "Table basse",
                   "accoudoir": "Accoudoir du canapé (avant-plan)"},
        "places": [("gauche", 190, "À gauche, près de la table basse"),
                   ("centre", 540, "Au centre, devant le meuble TV"),
                   ("droite", 900, "À droite, près du câble HDMI")],
    }


def bureau() -> dict:
    fond = _wall_floor("#DCE4EC", "#8D99A7", "#C6D0DA")
    desk_top = 1040
    bureau_ = group("plan-de-travail",
                    rect(40, desk_top, 1000, 30, "#C9B08E"),
                    rect(70, desk_top + 30, 26, 260, C["gris-fonce"]),
                    rect(984, desk_top + 30, 26, 260, C["gris-fonce"]),
                    rect(96, desk_top + 30, 888, 14, "#A58C6B", "none"))
    # Écran moderne relié en DisplayPort (embase sur la tranche inférieure).
    ecran_dp = group("ecran-displayport",
                     rect(80, 600, 440, 248, C["noir"], rx=8),
                     rect(92, 612, 416, 224, C["ecran"], "none"),
                     poly([(330, 612), (420, 612), (270, 836), (180, 836)], C["ecran-reflet"], "none"),
                     rect(280, 848, 40, 170, C["gris-fonce"]),
                     rect(210, 1018, 180, 22, C["gris-fonce"], rx=6))
    prise_dp_ecran = group("prise-dp-ecran", dp_port(420, 860, 34))
    # Ancien écran 4:3, entrée VGA.
    ecran_vga = group("ecran-vga",
                      rect(550, 680, 270, 220, "#CFCBC1", rx=10),
                      rect(570, 698, 230, 172, C["ecran"], "none"),
                      rect(660, 900, 50, 118, "#B5B0A5"),
                      rect(610, 1018, 150, 22, "#B5B0A5", rx=6))
    prise_vga_ecran = group("prise-vga-ecran", vga_port(760, 918, 40))
    # Tour PC vue de dos : alimentation en haut, connectique carte mère, carte graphique en bas.
    tour = group("tour-pc",
                 rect(850, 620, 190, 420, C["gris-fonce"], rx=8),
                 rect(866, 640, 158, 96, C["gris"], C["contour"], 3),
                 circle(945, 688, 36, C["gris-fonce"], C["contour"], 3),
                 line(915, 688, 975, 688, C["gris"], 3), line(945, 658, 945, 718, C["gris"], 3),
                 rect(866, 760, 70, 160, C["gris"], C["contour"], 3),
                 *[rect(950, 760 + i * 24, 74, 14, C["gris-fonce"], C["contour"], 2) for i in range(4)],
                 rect(866, 940, 158, 70, C["gris"], C["contour"], 3))
    sortie_vga = group("sortie-vga-pc", vga_port(901, 880, 44))
    sorties_dp = group("sortie-dp-pc", dp_port(910, 975, 32), dp_port(980, 975, 32))
    usb = group("ports-usb-pc", *[rect(886, 776 + i * 22, 30, 12, C["noir"], C["gris-clair"], 2) for i in range(3)])
    cable_dp = group("cable-displayport",
                     cable([(910, 990), (910, 1028), (430, 1028), (420, 870)]),
                     rect(408, 866, 24, 22, C["noir"], C["gris-clair"], 2, rx=3))
    cable_vga = group("cable-vga",
                      cable([(901, 896), (836, 896), (836, 932), (782, 932), (768, 924)], C["gris-fonce"], 12),
                      rect(744, 908, 32, 22, C["bleu-vga"], C["contour"], 2, rx=3))
    clavier = group("clavier",
                    rect(260, 1012, 300, 28, C["gris-clair"], rx=6),
                    *[rect(274 + i * 28, 1018, 20, 8, C["blanc"], "none", rx=2) for i in range(10)])
    souris = group("souris", rect(590, 1012, 40, 28, C["gris-clair"], rx=13))
    objets = group("objets", bureau_, ecran_dp, ecran_vga, tour, cable_dp, cable_vga, prise_dp_ecran,
                   prise_vga_ecran, sortie_vga, sorties_dp, usb, clavier, souris)
    avant = group("avant", group("dossier-chaise",
                                 rect(930, 1150, 170, 230, C["gris-fonce"], rx=30),
                                 rect(1000, 1380, 24, 100, C["noir"], "none")))
    return {
        "slug": "decor-bureau", "name": "Décor — bureau (PC, DisplayPort, VGA)",
        "description": ("Bureau schématique : tour PC vue de dos (carte graphique DisplayPort, sortie VGA), "
                        "écran 16:9 relié en DisplayPort, ancien écran 4:3 relié en VGA, clavier, souris."),
        "layers": {"fond": fond, "objets": objets, "avant": avant},
        "labels": {"plan-de-travail": "Bureau", "ecran-displayport": "Écran DisplayPort",
                   "prise-dp-ecran": "Entrée DisplayPort de l'écran", "ecran-vga": "Ancien écran VGA",
                   "prise-vga-ecran": "Entrée VGA de l'écran", "tour-pc": "Tour PC (vue de dos)",
                   "sortie-vga-pc": "Sortie VGA du PC", "sortie-dp-pc": "Sorties DisplayPort de la carte graphique",
                   "ports-usb-pc": "Ports USB", "cable-displayport": "Câble DisplayPort", "cable-vga": "Câble VGA",
                   "clavier": "Clavier", "souris": "Souris", "dossier-chaise": "Dossier de chaise (avant-plan)"},
        "places": [("gauche", 180, "À gauche, devant l'écran DisplayPort"),
                   ("centre", 540, "Au centre, devant le bureau"),
                   ("droite", 860, "À droite, près de la tour PC")],
    }


def plateau() -> dict:
    fond = group("fond",
                 rect(0, 0, W, WALL_BOTTOM, "#2C323C", "none"),
                 rect(0, WALL_BOTTOM, W, H - WALL_BOTTOM, "#4A505A", "none"),
                 line(0, WALL_BOTTOM, W, WALL_BOTTOM, C["contour"], 3),
                 rect(380, 330, 660, 860, "#3C6497", "none"),
                 rect(380, 1190, 660, 50, "#355A88", "none"),
                 line(0, 120, W, 120, C["gris"], 10))
    projecteurs = []
    for gid, cx in (("projecteur-gauche", 300), ("projecteur-droit", 800)):
        projecteurs.append(group(gid,
                                 line(cx, 120, cx, 170, C["gris"], 8),
                                 rect(cx - 60, 170, 120, 90, C["gris-fonce"], rx=10),
                                 circle(cx, 262, 40, "#FBE7A1", C["contour"], 4)))
    # Caméra de studio de profil, objectif vers la droite ; voyant tally rouge sur le dessus.
    camera = group("camera",
                   rect(150, 760, 280, 150, C["gris-fonce"], rx=12),
                   rect(170, 690, 130, 70, C["noir"], rx=8),
                   rect(176, 698, 22, 54, C["ecran"], "none"),
                   poly([(430, 790), (600, 780), (600, 890), (430, 880)], C["noir"]),
                   poly([(600, 772), (660, 752), (660, 918), (600, 898)], C["gris-fonce"]),
                   rect(330, 735, 70, 25, C["gris"], rx=6))
    tally = group("tally", circle(405, 742, 14, C["rouge"], C["contour"], 3))
    sortie_sdi = group("sortie-sdi-camera", bnc_port(172, 880, 12))
    tete = group("tete-trepied",
                 rect(240, 910, 100, 40, C["gris"]),
                 line(250, 930, 80, 1010, C["gris-clair"], 14))
    trepied = group("trepied",
                    line(290, 950, 160, 1390, C["gris-clair"], 14),
                    line(290, 950, 290, 1400, C["gris-clair"], 14),
                    line(290, 950, 420, 1390, C["gris-clair"], 14),
                    line(195, 1270, 385, 1270, C["gris"], 8),
                    circle(160, 1404, 16, C["noir"], C["contour"], 3),
                    circle(290, 1414, 16, C["noir"], C["contour"], 3),
                    circle(420, 1404, 16, C["noir"], C["contour"], 3))
    cable_sdi = group("cable-camera", cable([(172, 880), (130, 880), (110, 1200), (40, 1440), (-20, 1450)],
                                            C["noir"], 10))
    moniteur = group("moniteur-retour",
                     rect(600, 1260, 220, 140, C["noir"], rx=8),
                     rect(612, 1272, 196, 110, C["ecran"], "none"),
                     rect(690, 1400, 40, 30, C["gris-fonce"], "none"))
    marque = group("marque-sol", line(860, 1420, 940, 1460, C["ambre"], 10), line(940, 1420, 860, 1460, C["ambre"], 10))
    objets = group("objets", *projecteurs, cable_sdi, trepied, tete, camera, tally, sortie_sdi, moniteur, marque)
    avant = group("avant", group("bureau-presentateur",
                                 rect(640, 1060, 420, 34, "#E6E9EE"),
                                 poly([(660, 1094), (1040, 1094), (1010, 1240), (690, 1240)], "#C4CBD5")))
    return {
        "slug": "decor-plateau", "name": "Décor — plateau de télévision (caméra professionnelle)",
        "description": ("Plateau schématique : caméra de studio de profil sur trépied à roulettes, voyant tally, "
                        "sortie SDI (BNC) et câble, deux projecteurs, fond bleu, bureau de présentation, "
                        "moniteur de retour, marque au sol."),
        "layers": {"fond": fond, "objets": objets, "avant": avant},
        "labels": {"projecteur-gauche": "Projecteur gauche", "projecteur-droit": "Projecteur droit",
                   "camera": "Caméra de studio", "tally": "Voyant tally", "sortie-sdi-camera": "Sortie SDI (BNC)",
                   "tete-trepied": "Tête et bras de panoramique", "trepied": "Trépied à roulettes",
                   "cable-camera": "Câble de la caméra", "moniteur-retour": "Moniteur de retour",
                   "marque-sol": "Marque au sol", "bureau-presentateur": "Bureau du présentateur (avant-plan)"},
        "places": [("presentateur", 850, "Derrière le bureau du présentateur (jambes masquées par l'avant-plan)"),
                   ("centre", 540, "Au centre, entre la caméra et le bureau"),
                   ("cadreur", 110, "À gauche, derrière la caméra (cadreur)")],
    }


def regie() -> dict:
    fond = _wall_floor("#262B33", "#3A4049", "#30363F")
    mur = [rect(70, 230, 940, 560, C["noir"], rx=12)]
    tiles = [("source-1", 100, 560, "#5D8FC9"), ("source-2", 330, 560, "#6BA36E"),
             ("source-3", 560, 560, "#C98F5D"), ("source-4", 790, 560, "#8C7AB8")]
    for gid, x, y, color in tiles:
        mur.append(rect(x, y, 200, 200, color, C["gris-fonce"], 4))
    mur.append(rect(100, 260, 430, 270, C["ecran"], C["vert"], 10))
    mur.append(rect(550, 260, 430, 270, C["ecran"], C["rouge"], 10))
    mur_moniteurs = group("mur-moniteurs", *mur)
    pvw = group("apercu-pvw", rect(100, 260, 430, 270, "none", C["vert"], 10))
    pgm = group("programme-pgm", rect(550, 260, 430, 270, "none", C["rouge"], 10))
    pupitre = group("pupitre",
                    rect(40, 1060, 1000, 36, "#4E5663"),
                    rect(60, 1096, 960, 220, "#3F4651", C["contour"], 4))
    boutons = []
    for i in range(8):
        x = 300 + i * 52
        boutons.append(rect(x, 940, 38, 30, C["rouge"] if i == 2 else C["gris"], C["contour"], 2, rx=5))
        boutons.append(rect(x, 990, 38, 30, C["vert"] if i == 3 else C["gris"], C["contour"], 2, rx=5))
    melangeur = group("melangeur",
                      poly([(260, 910), (820, 910), (850, 1060), (230, 1060)], C["gris-fonce"]),
                      *boutons)
    tbar = group("levier-t-bar", rect(752, 930, 40, 110, C["noir"], C["gris"], 3, rx=8),
                 rect(740, 960, 64, 20, C["gris-clair"], rx=6))
    rang_pgm = group("rangee-programme", rect(292, 934, 422, 42, "none", C["rouge"], 3, rx=6))
    rang_pvw = group("rangee-apercu", rect(292, 984, 422, 42, "none", C["vert"], 3, rx=6))
    rack = group("baie-bnc",
                 rect(890, 900, 130, 160, C["noir"], rx=6),
                 *[bnc_port(915 + (i % 4) * 27, 940 + (i // 4) * 40, 9) for i in range(12)])
    objets = group("objets", mur_moniteurs, pvw, pgm, pupitre, melangeur, tbar, rang_pgm, rang_pvw, rack)
    avant = group("avant", group("dossier-fauteuil",
                                 rect(-20, 1170, 170, 230, "#1F232A", rx=30),
                                 rect(40, 1400, 24, 80, C["noir"], "none")))
    return {
        "slug": "decor-regie", "name": "Décor — salle de contrôle / régie vidéo",
        "description": ("Régie schématique : mur de moniteurs (aperçu PVW cadre vert, programme PGM cadre rouge, "
                        "quatre sources), pupitre, mélangeur avec rangées programme/aperçu et levier T-bar, "
                        "baie de connecteurs BNC."),
        "layers": {"fond": fond, "objets": objets, "avant": avant},
        "labels": {"mur-moniteurs": "Mur de moniteurs", "apercu-pvw": "Aperçu (PVW)", "programme-pgm": "Programme (PGM)",
                   "pupitre": "Pupitre de régie", "melangeur": "Mélangeur vidéo", "levier-t-bar": "Levier T-bar",
                   "rangee-programme": "Rangée programme", "rangee-apercu": "Rangée aperçu",
                   "baie-bnc": "Baie de connecteurs BNC", "dossier-fauteuil": "Dossier de fauteuil (avant-plan)"},
        "places": [("gauche", 170, "À gauche, devant le pupitre"),
                   ("centre", 540, "Au centre, face au mur de moniteurs"),
                   ("droite", 900, "À droite, près de la baie BNC")],
    }


DECORS = (salon, bureau, plateau, regie)


# ------------------------------------------------------------------------------------------- géométrie
def _floats(text: str) -> list[float]:
    return [float(v) for v in text.replace(",", " ").split()]


def element_bbox(el: ET.Element) -> tuple[float, float, float, float] | None:
    """Boîte (x0, y0, x1, y1) d'une forme simple, demi-épaisseur de trait comprise ; None pour un groupe."""
    tag = el.tag.rsplit("}", 1)[-1]
    sw = float(el.get("stroke-width", 0)) / 2 if el.get("stroke", "none") != "none" else 0.0
    if tag == "rect":
        x, y, w, h = (float(el.get(k)) for k in ("x", "y", "width", "height"))
        box = (x, y, x + w, y + h)
    elif tag == "circle":
        cx, cy, r = (float(el.get(k)) for k in ("cx", "cy", "r"))
        box = (cx - r, cy - r, cx + r, cy + r)
    elif tag == "ellipse":
        cx, cy, rx, ry = (float(el.get(k)) for k in ("cx", "cy", "rx", "ry"))
        box = (cx - rx, cy - ry, cx + rx, cy + ry)
    elif tag == "line":
        x1, y1, x2, y2 = (float(el.get(k)) for k in ("x1", "y1", "x2", "y2"))
        box = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    elif tag in ("polygon", "polyline"):
        values = _floats(el.get("points"))
        xs, ys = values[0::2], values[1::2]
        box = (min(xs), min(ys), max(xs), max(ys))
    elif tag == "g":
        return None
    else:
        raise ValueError(f"forme non prise en charge : {tag}")
    return (box[0] - sw, box[1] - sw, box[2] + sw, box[3] + sw)


def group_bbox(el: ET.Element) -> tuple[float, float, float, float]:
    boxes = [b for b in (element_bbox(child) for child in el.iter()) if b]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def clip(box) -> list[int]:
    """Boîte bornée au canevas, en [x, y, largeur, hauteur] entiers."""
    x0, y0 = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
    x1, y1 = min(W, math.ceil(box[2])), min(H, math.ceil(box[3]))
    return [x0, y0, x1 - x0, y1 - y0]


# ------------------------------------------------------------------------------------------- fichiers
def svg_document(*layers: str, title: str) -> str:
    body = "\n  ".join(layers)
    return (f'<svg xmlns="{SVG_NS}" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n'
            f"  <title>{title}</title>\n  {body}\n</svg>\n")


def _scene(spec: dict, full_svg: str) -> dict:
    root = ET.fromstring(full_svg)
    ids = {el.get("id"): el for el in root.iter() if el.get("id")}
    hotspots = []
    for layer in ("objets", "avant"):
        for child in ids[layer]:
            gid = child.get("id")
            box = clip(group_bbox(child))
            hotspots.append({"id": gid, "label": spec["labels"][gid], "layer": layer, "bbox": box,
                             "point": [box[0] + box[2] // 2, box[1] + box[3] // 2]})
    slug = spec["slug"]
    return {
        "schema": DECOR_SCHEMA,
        "decor": slug,
        "version": 1,
        "canvas": {"width": W, "height": H},
        "layers": [{"id": layer, "file": f"{slug}-{layer}.svg"} for layer in LAYERS],
        "order": ["fond", "objets", "personnages", "avant"],
        "zones": {"subtitles": SUBTITLES, "title": TITLE, "action": ACTION},
        "floor_y": WALL_BOTTOM,
        "places": [{"id": pid, "x": x, "y": FEET, "height": CHARACTER_HEIGHT, "note": note}
                   for pid, x, note in spec["places"]],
        "hotspots": hotspots,
    }


def build_decor(spec: dict) -> dict[str, str]:
    """Fichiers d'un décor, chemins relatifs à ``resource/``."""
    slug = spec["slug"]
    base = f"animation-assets/{slug}/v1"
    layers = spec["layers"]
    full = svg_document(*(layers[name] for name in LAYERS), title=spec["name"])
    out = {f"{base}/{slug}.svg": full}
    for name in LAYERS:
        out[f"{base}/{slug}-{name}.svg"] = svg_document(layers[name], title=f"{spec['name']} — couche {name}")
    out[f"{base}/{slug}-scene.json"] = json.dumps(_scene(spec, full), ensure_ascii=False, indent=2) + "\n"
    return out


def specs() -> list[dict]:
    return [factory() for factory in DECORS]


def outputs() -> dict[str, str]:
    files: dict[str, str] = {}
    for spec in specs():
        files.update(build_decor(spec))
    return files


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest_sha(manifest: dict) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_catalog(files: dict[str, str] | None = None) -> dict:
    files = outputs() if files is None else files
    assets = []
    for spec in specs():
        slug = spec["slug"]
        base = f"animation-assets/{slug}/v1"
        scene = json.loads(files[f"{base}/{slug}-scene.json"])
        roles = [("base", f"{base}/{slug}.svg")] + [(name, f"{base}/{slug}-{name}.svg") for name in LAYERS]
        roles.append(("scene", f"{base}/{slug}-scene.json"))
        manifest = {
            "format": "svg", "width": W, "height": H, "anchor": {"x": 0.5, "y": 0.5},
            "parts": list(LAYERS) + [spot["id"] for spot in scene["hotspots"]],
            "files": [{"role": role, "path": rel, "sha256": _sha_text(files[rel])} for role, rel in roles],
        }
        assets.append({"id": ASSET_IDS[slug], "name": spec["name"], "kind": "background",
                       "description": spec["description"], "is_active": True, "character": None, "location": None,
                       "versions": [{"version": 1, "manifest": manifest, "sha256": _manifest_sha(manifest),
                                     "notes": "Version 1 (#175) : 9:16, couches fond/objets/avant, fiche de scène."}]})
    return {"schema": "lody.animation-assets/1", "assets": assets}


def write_all(resource: Path = ROOT / "resource") -> list[Path]:
    files = outputs()
    written = []
    for rel, content in files.items():
        path = resource / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    catalog = resource / "animation-assets" / CATALOG_NAME
    catalog.write_text(json.dumps(build_catalog(files), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written.append(catalog)
    return written


if __name__ == "__main__":
    for written in write_all():
        print(written.relative_to(ROOT))
    sys.exit(0)
