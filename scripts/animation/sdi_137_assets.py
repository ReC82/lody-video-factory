"""Schémas techniques de l'épisode #137 « Voltix explique le câble SDI » — SVG vectoriels éditables.

Même style graphique que Voltix (#136) : palette, contour ``#2A1F14`` (jamais noir pur) de 6 px, aplats
sans texture ni dégradé. Chaque schéma est un asset SÉPARÉ (calques nommés ``<g id>``) pour être animé
par étapes dans le storyboard ``lody.animation/1`` ; aucun n'est une image aplatie.

Exactitude technique (relue contre les fiches fabricants citées dans ``docs/animation/voltix-137``) :

- coaxial SDI 75 Ω, de l'intérieur vers l'extérieur : âme en cuivre, diélectrique (isolant), blindage
  (feuille + tresse), gaine ; proportions d'un câble type « 1694A » (âme ≈ 1 mm, diélectrique ≈ 4,6 mm,
  câble ≈ 7 mm) ;
- BNC : fiche mâle (broche centrale, isolant, bague de verrouillage à fente en J) SÉPARÉE de l'embase
  femelle (douille centrale, fût, deux ergots diamétralement opposés) ; verrouillage = insérer puis
  tourner la bague d'un quart de tour, l'ergot glisse dans la fente en J jusqu'à son cran ;
- débits nominaux SDI : HD-SDI 1,485 Gbit/s, 3G-SDI 2,97 Gbit/s, 12G-SDI 11,88 Gbit/s (barres
  proportionnelles) ; aucune distance maximale n'est affichée.

Usage :
    python scripts/animation/sdi_137_assets.py   # écrit les SVG v1 + resource/animation-assets/sdi-catalogue.json
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from voltix_rig import P, ROOT, _manifest_sha, _outline, _sha, _svg  # noqa: E402

FONT = 'font-family="DejaVu Sans, Arial, sans-serif"'
C = {
    **P,
    "cuivre": "#D98C3A", "cuivre-reflet": "#F2C28A", "tresse": "#B4BAC1", "tresse-trait": "#7E858D",
    "feuille": "#DDE2E8", "gaine": "#1E1E22", "gaine-reflet": "#45454B", "ecran": "#10131C",
    "signal": "#2BB3E8", "signal-fonce": "#1F7FB0", "audio": "#33C27A", "alerte": "#E8562A",
    "corps-appareil": "#5B626B", "panneau": "#3C3F44", "rouge": "#E04848", "vert": "#3DBE6A",
}
OUT = _outline()
THIN = f'stroke="{C["contour"]}" stroke-width="4" stroke-linejoin="round"'


def _text(x: float, y: float, content: str, size: int = 26, anchor: str = "middle", weight: int = 700,
          fill: str | None = None) -> str:
    return (f'<text x="{x}" y="{y}" {FONT} font-size="{size}" font-weight="{weight}" '
            f'fill="{fill or C["contour"]}" text-anchor="{anchor}">{content}</text>')


def _bnc_port(cx: float, cy: float, r: float = 16) -> str:
    """Embase BNC vue de face sur un panneau : fût métallique, isolant blanc, douille centrale, 2 ergots."""
    return (f'<rect x="{cx - r - 7}" y="{cy - 4}" width="{2 * r + 14}" height="8" rx="3" fill="{C["metal-ombre"]}" '
            f'{THIN}/><circle cx="{cx}" cy="{cy}" r="{r}" fill="{C["metal"]}" {THIN}/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r * 0.6:.1f}" fill="{C["isolant"]}" {THIN}/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r * 0.18:.1f}" fill="{C["contour"]}"/>')


# ------------------------------------------------------------------------------------------------ régie

def build_camera() -> str:
    """Caméra de plateau générique (sans marque), objectif vers la gauche, sortie SDI BNC à l'arrière."""
    return _svg("Caméra de plateau (sortie SDI)", 380, 320, (
        f'<g id="trepied"><path d="M190,236 L120,312 M190,236 L260,312 M190,236 L190,312" fill="none" '
        f'stroke="{C["contour"]}" stroke-width="14" stroke-linecap="round"/><path d="M190,236 L120,312 '
        f'M190,236 L260,312 M190,236 L190,312" fill="none" stroke="{C["corps-appareil"]}" stroke-width="6" '
        f'stroke-linecap="round"/><rect x="160" y="212" width="60" height="30" rx="6" fill="{C["panneau"]}" {OUT}/></g>'
        f'<g id="corps"><rect x="110" y="96" width="210" height="118" rx="18" fill="{C["corps-appareil"]}" {OUT}/>'
        f'<rect x="130" y="116" width="70" height="40" rx="8" fill="{C["panneau"]}" {THIN}/></g>'
        f'<g id="viseur"><rect x="196" y="52" width="104" height="50" rx="10" fill="{C["panneau"]}" {OUT}/>'
        f'<rect x="210" y="64" width="60" height="26" rx="4" fill="{C["ecran"]}"/></g>'
        f'<g id="objectif"><rect x="28" y="116" width="88" height="78" rx="8" fill="{C["panneau"]}" {OUT}/>'
        f'<ellipse cx="28" cy="155" rx="16" ry="42" fill="{C["corps-appareil"]}" {OUT}/>'
        f'<ellipse cx="26" cy="155" rx="8" ry="26" fill="{C["signal-fonce"]}"/>'
        + "".join(f'<line x1="{x}" y1="120" x2="{x}" y2="190" stroke="{C["metal-ombre"]}" stroke-width="3"/>'
                  for x in (60, 74, 88))
        + "</g>"
        f'<g id="sortie-sdi">{_bnc_port(296, 182, 15)}{_text(262, 154, "SDI OUT", 15, fill=C["isolant"])}</g>'))


def build_mixer() -> str:
    """Mélangeur vidéo générique : bus programme / prévisualisation, levier T-bar, entrées SDI en BNC."""
    buttons = "".join(
        f'<rect x="{150 + i * 42}" y="{y}" width="30" height="24" rx="5" fill="{fill if i == lit else C["metal"]}" '
        f'{THIN}/>' for y, fill, lit in ((70, C["rouge"], 1), (112, C["vert"], 2)) for i in range(5))
    return _svg("Mélangeur vidéo (entrées SDI)", 460, 270, (
        f'<g id="pupitre"><path d="M20,230 L60,40 L440,40 L440,230 Z" fill="{C["corps-appareil"]}" {OUT}/>'
        f'<rect x="20" y="226" width="420" height="30" rx="6" fill="{C["panneau"]}" {OUT}/></g>'
        f'<g id="entrees-sdi"><rect x="56" y="60" width="74" height="150" rx="10" fill="{C["panneau"]}" {THIN}/>'
        f'{_text(93, 84, "SDI IN", 15, fill=C["isolant"])}{_bnc_port(93, 118, 14)}{_bnc_port(93, 172, 14)}</g>'
        f'<g id="bus">{buttons}{_text(372, 88, "PGM", 17, "start", fill=C["isolant"])}'
        f'{_text(372, 130, "PVW", 17, "start", fill=C["isolant"])}</g>'
        f'<g id="t-bar"><rect x="214" y="160" width="132" height="56" rx="8" fill="{C["panneau"]}" {THIN}/>'
        f'<line x1="232" y1="188" x2="328" y2="188" stroke="{C["ecran"]}" stroke-width="8" stroke-linecap="round"/>'
        f'<rect x="262" y="170" width="16" height="36" rx="4" fill="{C["metal"]}" {THIN}/></g>'))


def build_monitor() -> str:
    """Moniteur de contrôle : cadre, écran noir (l'asset « mire » s'y superpose), pied, entrée SDI."""
    return _svg("Moniteur de contrôle (entrée SDI)", 400, 320, (
        f'<g id="pied"><rect x="180" y="250" width="40" height="40" fill="{C["corps-appareil"]}" {OUT}/>'
        f'<rect x="120" y="284" width="160" height="24" rx="10" fill="{C["panneau"]}" {OUT}/></g>'
        f'<g id="cadre"><rect x="20" y="20" width="360" height="236" rx="16" fill="{C["panneau"]}" {OUT}/></g>'
        f'<g id="ecran"><rect x="42" y="40" width="316" height="190" rx="6" fill="{C["ecran"]}"/></g>'
        f'<g id="entree-sdi">{_bnc_port(352, 244, 9)}</g>'))


def build_test_card() -> str:
    """Mire de barres de couleur générique (image « retrouvée » sur le moniteur), 316 × 190."""
    colors = ["#C0C0C0", "#C0C000", "#00C0C0", "#00C000", "#C000C0", "#C00000", "#0000C0"]
    width = 316 / len(colors)
    bars = "".join(f'<rect x="{i * width:.2f}" y="0" width="{width + 0.5:.2f}" height="140" fill="{c}"/>'
                   for i, c in enumerate(colors))
    return _svg("Mire de barres", 316, 190, (
        f'<g id="barres">{bars}<rect x="0" y="140" width="316" height="50" fill="#1A1A1A"/>'
        f'{_text(158, 176, "SDI ✓", 30, fill="#FFFFFF")}</g>'))


def build_straight_cable() -> str:
    """Câble coaxial SDI droit (noir) avec une fiche BNC à chaque extrémité, 760 × 80."""
    def plug(x: float, direction: int) -> str:
        tip = x + direction * 44
        return (f'<rect x="{min(x, tip)}" y="24" width="44" height="32" rx="5" fill="{C["metal"]}" {THIN}/>'
                f'<rect x="{min(x, tip) + (4 if direction > 0 else 30)}" y="20" width="10" height="40" rx="3" '
                f'fill="{C["metal-ombre"]}" {THIN}/>'
                f'<path d="M{tip},36 L{tip + direction * 12},40 L{tip},44 Z" fill="{C["broche"]}" {THIN}/>')
    return _svg("Câble coaxial SDI droit, fiches BNC", 760, 80, (
        f'<g id="gaine"><rect x="56" y="28" width="648" height="24" rx="10" fill="{C["gaine"]}" {OUT}/>'
        f'<line x1="70" y1="34" x2="690" y2="34" stroke="{C["gaine-reflet"]}" stroke-width="4" '
        f'stroke-linecap="round"/></g>'
        f'<g id="fiches">{plug(56, -1)}{plug(704, 1)}</g>'))


def build_packet() -> str:
    return _svg("Paquet de données numériques", 72, 72, (
        f'<g id="paquet"><rect x="6" y="6" width="60" height="60" rx="14" fill="{C["signal"]}" {OUT}/>'
        f'{_text(36, 45, "01", 24, fill="#FFFFFF")}</g>'))


# ------------------------------------------------------------------------------------- câble coaxial

# Coupe longitudinale : 4 calques dans un MÊME repère 1000 × 240 (axe y = 120), superposés de l'âme à la gaine.
# Chaque couche commence en x = 20 et s'arrête plus loin que celle qui l'entoure : en place, elles forment
# la coupe étagée classique ; décalées vers la gauche, elles « se séparent dans l'axe » sans changer d'ordre.
COAX_LONG = {
    "coax-ame": ("Âme en cuivre", 960, 11),
    "coax-isolant": ("Diélectrique (isolant)", 780, 38),
    "coax-blindage": ("Blindage : feuille + tresse", 600, 60),
    "coax-gaine": ("Gaine extérieure", 420, 76),
}


def build_coax_layer(key: str) -> str:
    title, end, half = COAX_LONG[key]
    top, bottom, h = 120 - half, 120 + half, 2 * half
    if key == "coax-ame":
        body = (f'<path d="M20,{top} L{end - 12},{top} Q{end},{top} {end},120 Q{end},{bottom} {end - 12},{bottom} '
                f'L20,{bottom} Z" fill="{C["cuivre"]}" stroke="{C["contour"]}" stroke-width="5" stroke-linejoin="round"/>'
                f'<line x1="30" y1="{top + 5}" x2="{end - 16}" y2="{top + 5}" stroke="{C["cuivre-reflet"]}" '
                f'stroke-width="3" stroke-linecap="round"/>')
    elif key == "coax-isolant":
        body = (f'<rect x="20" y="{top}" width="{end - 20}" height="{h}" fill="{C["isolant"]}" {OUT}/>'
                f'<line x1="30" y1="{top + 9}" x2="{end - 10}" y2="{top + 9}" stroke="#FFFFFF" stroke-width="5"/>')
    elif key == "coax-blindage":
        # Feuille d'aluminium (bande claire, dépasse un peu de la tresse) puis tresse croisée.
        body = (f'<defs><pattern id="tresse-137" width="16" height="16" patternUnits="userSpaceOnUse">'
                f'<rect width="16" height="16" fill="{C["tresse"]}"/><path d="M0,0 L16,16 M16,0 L0,16" '
                f'stroke="{C["tresse-trait"]}" stroke-width="3"/></pattern></defs>'
                f'<g id="feuille"><rect x="20" y="{top + 6}" width="{end - 20}" height="{h - 12}" '
                f'fill="{C["feuille"]}" {THIN}/></g>'
                f'<g id="tresse"><rect x="20" y="{top}" width="{end - 56}" height="{h}" fill="url(#tresse-137)" {OUT}/></g>')
    else:
        body = (f'<path d="M48,{top} L{end},{top} L{end},{bottom} L48,{bottom} Q20,{bottom} 20,120 Q20,{top} 48,{top} Z" '
                f'fill="{C["gaine"]}" {OUT}/><line x1="56" y1="{top + 16}" x2="{end - 16}" y2="{top + 16}" '
                f'stroke="{C["gaine-reflet"]}" stroke-width="8" stroke-linecap="round"/>')
    return _svg(f"Coaxial 75 Ω, coupe longitudinale — {title}", 1000, 240, f'<g id="{key}">{body}</g>')


def build_coax_cross_section() -> str:
    """Coupe transversale légendée (proportions d'un coaxial SDI type 1694A), 1000 × 560."""
    cx, cy = 270, 280
    rings = [  # (id, rayon, remplissage)
        ("gaine", 236, C["gaine"]), ("tresse", 196, "url(#tresse-x)"), ("feuille", 172, C["feuille"]),
        ("dielectrique", 162, C["isolant"]), ("ame", 36, C["cuivre"]),
    ]
    shapes = "".join(f'<g id="{i}"><circle cx="{cx}" cy="{cy}" r="{r}" fill="{f}" {OUT if i != "feuille" else THIN}/>'
                     f'</g>' for i, r, f in rings)
    # Légendes fléchées : (texte, rayon visé, angle en degrés, y de l'étiquette)
    labels = [("Gaine", 216, -38, 70), ("Blindage : tresse", 184, -16, 170), ("Blindage : feuille", 167, 6, 270),
              ("Diélectrique (isolant)", 100, 20, 380), ("Âme en cuivre", 24, 60, 480)]
    legend = ""
    for content, radius, angle, ly in labels:
        ax = cx + radius * math.cos(math.radians(angle))
        ay = cy + radius * math.sin(math.radians(angle))
        legend += (f'<path d="M{ax:.1f},{ay:.1f} L560,{ly} L590,{ly}" fill="none" stroke="{C["contour"]}" '
                   f'stroke-width="4" stroke-linejoin="round"/><circle cx="{ax:.1f}" cy="{ay:.1f}" r="7" '
                   f'fill="{C["jaune"]}" {THIN}/>{_text(600, ly + 10, content, 30, "start")}')
    return _svg("Coaxial 75 Ω, coupe transversale légendée", 1000, 560, (
        f'<defs><pattern id="tresse-x" width="14" height="14" patternUnits="userSpaceOnUse">'
        f'<rect width="14" height="14" fill="{C["tresse"]}"/><path d="M0,0 L14,14 M14,0 L0,14" '
        f'stroke="{C["tresse-trait"]}" stroke-width="3"/></pattern></defs>'
        f'<g id="coupe">{shapes}<circle cx="{cx - 10}" cy="{cy - 10}" r="9" fill="{C["cuivre-reflet"]}"/></g>'
        f'<g id="legendes">{legend}</g>'
        f'<g id="impedance">{_text(cx, 548, "Coaxial 75 Ω", 30)}</g>'))


# --------------------------------------------------------------------------------------------- BNC

def _jslot(x0: float, y0: float) -> str:
    """Fente en J de la bague, vue de profil « développée » (le pourtour déroulé à la verticale) : entrée
    axiale depuis l'avant (à droite), coude, rampe circonférentielle de 30 px vers le bas, cran de verrouillage.
    ``x0`` = bord avant de la bague, ``y0`` = hauteur de l'entrée."""
    d = f"M{x0},{y0} L{x0 - 42},{y0} Q{x0 - 52},{y0} {x0 - 52},{y0 + 10} L{x0 - 52},{y0 + 30} L{x0 - 40},{y0 + 30}"
    return (f'<path d="{d}" fill="none" stroke="{C["contour"]}" stroke-width="18" stroke-linecap="round" '
            f'stroke-linejoin="round"/><path d="{d}" fill="none" stroke="{C["metal-ombre"]}" stroke-width="9" '
            f'stroke-linecap="round" stroke-linejoin="round"/>')


def _state_row(y: float, state: str) -> str:
    """Une ligne de la planche BNC : fiche mâle (gauche) + embase femelle (droite), vue de profil.

    L'ergot de l'embase est vu de face sur le fût (x = 600, y - 15). La bague de la fiche est dessinée en
    transparence (vue « fantôme ») : on voit la broche centrale EN RETRAIT dans la bague, comme sur une vraie
    fiche BNC mâle, et l'ergot dans la fente. Inséré : l'ergot est au fond de l'entrée axiale. Verrouillé : la
    bague a tourné d'un quart de tour (sa fente est remontée de 30 px) et l'ergot est dans le cran ; la rampe
    a tiré la fiche puis le ressort l'a repoussée de 10 px dans le cran.
    """
    mx = {"debranche": 0, "insere": 240, "verrouille": 230}[state]
    x0 = 410 + mx  # bord avant de la bague
    female = (f'<rect x="560" y="{y - 46}" width="130" height="92" fill="{C["metal"]}" {OUT}/>'
              f'<path d="M690,{y - 70} L740,{y - 70} L752,{y} L740,{y + 70} L690,{y + 70} L680,{y} Z" '
              f'fill="{C["metal-ombre"]}" {OUT}/><rect x="750" y="{y - 32}" width="80" height="64" '
              f'fill="{C["metal"]}" {OUT}/>')
    male = (f'<rect x="{40 + mx}" y="{y - 16}" width="130" height="32" fill="{C["cable"]}" {OUT}/>'
            f'<rect x="{166 + mx}" y="{y - 22}" width="50" height="44" rx="4" fill="{C["metal-ombre"]}" {OUT}/>'
            f'<rect x="{214 + mx}" y="{y - 30}" width="40" height="60" rx="5" fill="{C["metal"]}" {OUT}/>')
    ring = (f'<rect x="{252 + mx}" y="{y - 62}" width="158" height="124" rx="10" fill="{C["metal"]}" '
            f'fill-opacity="0.6" {OUT}/>')
    # Isolant et broche centrale, en retrait dans la bague ; une fois insérés, ils sont dans l'embase (cachés).
    core = (f'<rect x="{254 + mx}" y="{y - 26}" width="120" height="52" fill="{C["isolant"]}" {THIN}/>'
            f'<path d="M{330 + mx},{y - 5} L{386 + mx},{y - 5} L{394 + mx},{y} L{386 + mx},{y + 5} L{330 + mx},{y + 5} Z" '
            f'fill="{C["broche"]}" {THIN}/>')
    slot_y = {"debranche": y - 15, "insere": y - 15, "verrouille": y - 45}[state]
    stud = (f'<circle cx="600" cy="{y - 15}" r="11" fill="{C["broche"]}" {THIN}/>')
    rotation = ""
    if state == "verrouille":
        rotation = (f'<path d="M{300 + mx},{y + 96} A80,26 0 0 0 {392 + mx},{y + 74}" fill="none" '
                    f'stroke="{C["alerte"]}" stroke-width="7" stroke-linecap="round"/>'
                    f'<path d="M{384 + mx},{y + 62} L{404 + mx},{y + 72} L{388 + mx},{y + 86} Z" fill="{C["alerte"]}"/>'
                    f'{_text(220 + mx, y + 104, "¼ de tour", 26, fill=C["alerte"])}')
    # Débranché : la fiche est devant l'embase, l'ergot visible sur le fût ; sinon l'ergot est par-dessus la
    # bague transparente, dans sa fente.
    layers = (male + ring + core + _jslot(x0, slot_y) + female + stud if state == "debranche"
              else male + core + female + ring + _jslot(x0, slot_y) + stud)
    return f'<g id="etat-{state}">{layers}{rotation}</g>'


def build_bnc_states() -> str:
    """Planche « débranché / inséré / verrouillé » (profil), 900 × 840. Fente en J et ergot visibles."""
    rows = [("debranche", "1. Débranché : la fiche mâle face à l'embase"),
            ("insere", "2. Inséré : l'ergot entre dans la fente en J"),
            ("verrouille", "3. Verrouillé : ¼ de tour, l'ergot dans son cran")]
    body = ""
    for i, (state, caption) in enumerate(rows):
        y = 150 + i * 270
        body += _text(40, y - 96, caption, 28, "start") + _state_row(y, state)
    body += (_text(250, 820, "Fiche mâle (bague à fente en J)", 22) +
             _text(700, 820, "Embase femelle (2 ergots)", 22))
    return _svg("BNC : débranché, inséré, verrouillé", 900, 840, body)


def build_bnc_ring_face() -> str:
    """Bague de verrouillage de la fiche mâle, vue de face (360 × 360) : anneau moleté percé de deux
    entrées de fente diamétralement opposées. Tournée de 90°, elle masque les ergots (verrouillage)."""
    c, r_out, r_in = 180, 150, 104
    knurl = "".join(
        f'<line x1="{c + r_out * math.cos(a):.1f}" y1="{c + r_out * math.sin(a):.1f}" '
        f'x2="{c + (r_out - 14) * math.cos(a):.1f}" y2="{c + (r_out - 14) * math.sin(a):.1f}" '
        f'stroke="{C["metal-ombre"]}" stroke-width="4"/>' for a in (i * math.pi / 18 for i in range(36)))
    ring = (f'<path d="M{c},{c - r_out} A{r_out},{r_out} 0 1 1 {c - 0.01},{c - r_out} Z '
            f'M{c},{c - r_in} A{r_in},{r_in} 0 1 0 {c + 0.01},{c - r_in} Z" fill="{C["metal"]}" fill-rule="evenodd" {OUT}/>')
    # Entrées de fente : encoches découpées en haut et en bas de l'anneau.
    cut = "".join(f'<rect x="{c - 17}" y="{y}" width="34" height="{r_out - r_in + 8}" rx="6" fill="{C["ecran"]}" '
                  f'{THIN}/>' for y in (c - r_out - 4, c + r_in - 4))
    mark = (f'<path d="M{c + r_out - 6},{c - 8} L{c + r_out + 22},{c} L{c + r_out - 6},{c + 8} Z" '
            f'fill="{C["alerte"]}" {THIN}/>')
    return _svg("Bague BNC (fiche mâle), vue de face", 360, 360, (
        f'<g id="bague">{ring}{knurl}{cut}{mark}</g>'
        f'<g id="isolant"><circle cx="{c}" cy="{c}" r="{r_in - 30}" fill="{C["isolant"]}" {OUT}/></g>'
        f'<g id="broche"><circle cx="{c}" cy="{c}" r="14" fill="{C["broche"]}" {THIN}/></g>'))


def build_bnc_studs_face() -> str:
    """Fût de l'embase femelle vue de face (360 × 360) : deux ergots en haut et en bas, sous la bague."""
    c = 180
    studs = "".join(f'<rect x="{c - 13}" y="{y}" width="26" height="34" rx="8" fill="{C["broche"]}" {THIN}/>'
                    for y in (c - 146, c + 112))
    return _svg("Embase BNC femelle, fût et ergots, vue de face", 360, 360, (
        f'<g id="fut"><circle cx="{c}" cy="{c}" r="118" fill="{C["metal-ombre"]}" {OUT}/></g>'
        f'<g id="ergots">{studs}</g>'))


# ------------------------------------------------------------------------------------ signaux, débits

def build_wave(stable: bool) -> str:
    """Signal numérique schématique : créneaux nets (stable) ou déformés avec coupures (dégradé)."""
    bits = [1, 0, 1, 1, 0, 1, 0, 0, 1, 0, 1, 1]
    step, x, d = 48, 12, ""
    y = {1: 36, 0: 120}
    prev = bits[0]
    d = f"M{x},{y[prev]}"
    for i, b in enumerate(bits):
        if b != prev:
            d += f" L{x},{y[b]}" if stable else f" Q{x + 14},{(y[b] + y[prev]) / 2 + (18 if i % 2 else -18)} {x + 24},{y[b] + (10 if b else -10)}"
        x += step
        d += f" L{x},{y[b]}" if stable else f" L{x - 6},{y[b] + (8 if i % 3 else -6)}"
        prev = b
    color = C["signal"] if stable else C["alerte"]
    extra = ""
    if not stable:
        extra = "".join(f'<rect x="{gx}" y="20" width="34" height="120" fill="#FFFFFF" fill-opacity="0.92"/>'
                        f'<path d="M{gx + 6},62 L{gx + 28},96 M{gx + 28},62 L{gx + 6},96" stroke="{C["alerte"]}" '
                        f'stroke-width="7" stroke-linecap="round"/>' for gx in (190, 430))
    return _svg("Signal numérique " + ("stable" if stable else "dégradé, avec coupures"), 600, 160, (
        f'<g id="onde"><rect x="2" y="2" width="596" height="156" rx="18" fill="#FFFFFF" {OUT}/>'
        f'<path d="{d}" fill="none" stroke="{C["contour"]}" stroke-width="14" stroke-linejoin="round" '
        f'stroke-linecap="round"/><path d="{d}" fill="none" stroke="{color}" stroke-width="7" '
        f'stroke-linejoin="round" stroke-linecap="round"/>{extra}</g>'))


def build_icon_video() -> str:
    return _svg("Icône vidéo", 160, 140, (
        f'<g id="icone"><rect x="10" y="14" width="140" height="96" rx="14" fill="{C["signal"]}" {OUT}/>'
        f'<path d="M66,40 L102,62 L66,84 Z" fill="#FFFFFF" {THIN}/>'
        f'<rect x="60" y="112" width="40" height="18" rx="4" fill="{C["corps-appareil"]}" {THIN}/></g>'))


def build_icon_audio() -> str:
    bars = [30, 62, 92, 50, 80, 40, 66, 28]
    return _svg("Icône audio", 160, 140, (
        f'<g id="icone"><rect x="10" y="14" width="140" height="112" rx="14" fill="{C["audio"]}" {OUT}/>'
        + "".join(f'<rect x="{26 + i * 15}" y="{70 - h / 2}" width="9" height="{h}" rx="4" fill="#FFFFFF"/>'
                  for i, h in enumerate(bars)) + "</g>"))


RATES = {"debit-hd": ("HD-SDI", 1.485), "debit-3g": ("3G-SDI", 2.97), "debit-12g": ("12G-SDI", 11.88)}


def build_rate_bar(key: str) -> str:
    """Barre de débit proportionnelle (12G-SDI = 700 px), ancrée à gauche, 760 × 90."""
    name, gbps = RATES[key]
    length = max(40, round(700 * gbps / 11.88))
    value = f"{gbps:.3f}".rstrip("0").rstrip(".").replace(".", ",")
    inside = length >= 560  # valeur écrite dans la barre (blanc) si elle est longue, sinon à sa droite
    label = _text(length - 12 if inside else length + 24, 56, f"{value} Gbit/s", 32, "end" if inside else "start",
                  fill="#FFFFFF" if inside else None)
    return _svg(f"{name} : {value} Gbit/s", 760, 90, (
        f'<g id="barre"><rect x="8" y="14" width="{length}" height="62" rx="16" fill="{C["signal"]}" {OUT}/></g>'
        f'<g id="valeur">{label}</g>'))


def build_tag_75() -> str:
    return _svg("Étiquette 75 Ω", 240, 120, (
        f'<g id="etiquette"><path d="M40,10 L226,10 L226,110 L40,110 L8,60 Z" fill="{C["jaune"]}" {OUT}/>'
        f'<circle cx="42" cy="60" r="10" fill="#FFFFFF" {THIN}/>{_text(140, 78, "75 Ω", 48)}</g>'))


# ---------------------------------------------------------------------------------------- catalogue

# clé → (identifiant de catalogue, nom, description, constructeur, largeur, hauteur, ancrage)
ASSETS: dict[str, tuple] = {
    "regie-camera": ("ast_137000000001", "Caméra de plateau", "Caméra générique, sortie SDI en BNC.",
                     build_camera, 380, 320, (0.5, 0.5)),
    "regie-melangeur": ("ast_137000000002", "Mélangeur vidéo", "Bus PGM/PVW, T-bar, entrées SDI en BNC.",
                        build_mixer, 460, 270, (0.5, 0.5)),
    "regie-moniteur": ("ast_137000000003", "Moniteur de contrôle", "Écran noir, entrée SDI.",
                       build_monitor, 400, 320, (0.5, 0.5)),
    "mire": ("ast_137000000004", "Mire de barres", "Image retrouvée sur le moniteur.", build_test_card, 316, 190,
             (0.5, 0.5)),
    "cable-sdi-droit": ("ast_137000000005", "Câble SDI droit", "Coaxial noir, fiche BNC à chaque bout.",
                        build_straight_cable, 760, 80, (0.5, 0.5)),
    "paquet": ("ast_137000000006", "Paquet numérique", "Petit paquet de données qui voyage dans le câble.",
               build_packet, 72, 72, (0.5, 0.5)),
    **{key: (f"ast_1370000000{10 + i}", f"Coax longitudinal — {COAX_LONG[key][0]}",
             "Calque de la coupe longitudinale (repère commun 1000 × 240).",
             (lambda k=key: build_coax_layer(k)), 1000, 240, (0.5, 0.5)) for i, key in enumerate(COAX_LONG)},
    "coax-transversale": ("ast_137000000014", "Coax — coupe transversale légendée",
                          "Âme, diélectrique, feuille, tresse, gaine ; proportions type 1694A.",
                          build_coax_cross_section, 1000, 560, (0.5, 0.5)),
    "bnc-etats": ("ast_137000000020", "BNC — débranché / inséré / verrouillé",
                  "Planche de profil : fente en J et ergot dans les trois états.", build_bnc_states, 900, 840,
                  (0.5, 0.5)),
    "bnc-bague-face": ("ast_137000000021", "BNC — bague vue de face", "Bague de la fiche mâle, 2 entrées de fente.",
                       build_bnc_ring_face, 360, 360, (0.5, 0.5)),
    "bnc-ergots-face": ("ast_137000000022", "BNC — fût et ergots vue de face", "Embase femelle, 2 ergots.",
                        build_bnc_studs_face, 360, 360, (0.5, 0.5)),
    "onde-stable": ("ast_137000000030", "Signal numérique stable", "Créneaux nets.", lambda: build_wave(True),
                    600, 160, (0.5, 0.5)),
    "onde-degradee": ("ast_137000000031", "Signal numérique dégradé", "Créneaux déformés et coupures.",
                      lambda: build_wave(False), 600, 160, (0.5, 0.5)),
    "icone-video": ("ast_137000000032", "Icône vidéo", "Piste vidéo.", build_icon_video, 160, 140, (0.5, 0.5)),
    "icone-audio": ("ast_137000000033", "Icône audio", "Piste audio intégrée.", build_icon_audio, 160, 140,
                    (0.5, 0.5)),
    **{key: (f"ast_1370000000{40 + i}", f"Débit {RATES[key][0]}", "Barre proportionnelle au débit nominal.",
             (lambda k=key: build_rate_bar(k)), 760, 90, (0.0, 0.5)) for i, key in enumerate(RATES)},
    "etiquette-75": ("ast_137000000050", "Étiquette 75 Ω", "Impédance du câble et des connecteurs.",
                     build_tag_75, 240, 120, (0.5, 0.5)),
}


def rel_path(key: str) -> str:
    return f"animation-assets/{key}/v1/{key}.svg"


def outputs() -> dict[str, str]:
    """Chemin relatif (au dossier ``resource/``) → contenu SVG de la version 1."""
    return {rel_path(key): spec[3]() for key, spec in ASSETS.items()}


def build_catalog(resource: Path) -> dict:
    assets = []
    for key, (asset_id, name, description, builder, width, height, anchor) in ASSETS.items():
        rel = rel_path(key)
        content = (resource / rel).read_text(encoding="utf-8")
        parts = sorted(set(_ids(content)))
        manifest = {"format": "svg", "width": width, "height": height, "anchor": {"x": anchor[0], "y": anchor[1]},
                    "parts": parts, "files": [{"role": "base", "path": rel, "sha256": _sha(resource / rel)}]}
        assets.append({"id": asset_id, "name": name, "kind": "prop", "description": description, "is_active": True,
                       "character": None, "location": None,
                       "versions": [{"version": 1, "manifest": manifest, "sha256": _manifest_sha(manifest),
                                     "notes": "Épisode #137 : schéma SDI/BNC v1."}]})
    return {"schema": "lody.animation-assets/1", "assets": assets}


def _ids(svg: str) -> list[str]:
    return re.findall(r'<g id="([^"]+)"', svg)


BOARD = [  # planche de contrôle : (clé, x, y, largeur affichée)
    ("regie-camera", 30, 70, 300), ("cable-sdi-droit", 350, 140, 420), ("regie-melangeur", 790, 60, 330),
    ("regie-moniteur", 1150, 60, 300), ("mire", 1190, 340, 220),
    ("coax-transversale", 30, 470, 640), ("bnc-etats", 700, 430, 560), ("bnc-bague-face", 1290, 520, 180),
    ("bnc-ergots-face", 1290, 760, 180),
    ("onde-stable", 30, 1010, 330), ("onde-degradee", 380, 1010, 330), ("icone-video", 730, 1010, 110),
    ("icone-audio", 860, 1010, 110), ("etiquette-75", 990, 1010, 160), ("paquet", 1180, 1020, 60),
    ("debit-hd", 30, 1180, 420), ("debit-3g", 30, 1260, 420), ("debit-12g", 30, 1340, 420),
]


def build_board(resource: Path = ROOT / "resource") -> str:
    """Planche SVG de tous les schémas (contrôle humain) ; la coupe longitudinale est montée en place."""
    body = ""
    for key, x, y, width in BOARD:
        _, name, _, _, w, h, _ = ASSETS[key]
        inner = (resource / rel_path(key)).read_text(encoding="utf-8")
        inner = re.sub(r"^<svg [^>]*>", "", inner.strip()).removesuffix("</svg>")
        inner = re.sub(r"<title>.*?</title>", "", inner)
        height = round(width * h / w)
        body += (f'<svg x="{x}" y="{y}" width="{width}" height="{height}" viewBox="0 0 {w} {h}">{inner}</svg>'
                 f'{_text(x, y - 12, name, 18, "start", 600)}')
    coax = "".join(re.sub(r"<title>.*?</title>", "", re.sub(r"^<svg [^>]*>", "", (resource / rel_path(k))
                   .read_text(encoding="utf-8").strip()).removesuffix("</svg>")) for k in COAX_LONG)
    body += (f'<svg x="520" y="1190" width="900" height="216" viewBox="0 0 1000 240">{coax}</svg>'
             f'{_text(520, 1196, "Coax longitudinal (4 calques superposés)", 18, "start", 600)}')
    return _svg("Épisode #137 — planche de contrôle des schémas SDI / BNC", 1560, 1440,
                f'<rect width="1560" height="1440" fill="#F4F6FA"/>{body}')


def write_all(resource: Path = ROOT / "resource") -> list[Path]:
    written = []
    for rel, content in outputs().items():
        path = resource / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    catalog = resource / "animation-assets" / "sdi-catalogue.json"
    catalog.write_text(json.dumps(build_catalog(resource), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written.append(catalog)
    board = ROOT / "docs" / "animation" / "voltix-137" / "planche-schemas.svg"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text(build_board(resource), encoding="utf-8")
    written.append(board)
    return written


if __name__ == "__main__":
    for item in write_all():
        print(item.relative_to(ROOT))
    sys.exit(0)
