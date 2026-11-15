"""Schémas V3 de l'épisode #137 « du HDMI familier au SDI » — SVG vectoriels éditables, style Voltix.

La directive éditoriale V3 (#137, 11/11/2026) part du connu : HDMI à la maison (box → téléviseur),
DisplayPort au bureau (PC → moniteur), VGA d'autrefois (image analogique), puis le SDI du plateau de
télévision, et compare le coaxial SDI au câble d'antenne domestique. Ce module ajoute les décors et
connecteurs qui manquaient aux schémas v1 (``sdi_137_assets.py``), qui restent réutilisés tels quels
(caméra, moniteur de contrôle, câble SDI à fiches BNC, icônes vidéo / audio, étiquette 75 Ω).

Même style que Voltix (#136) : palette ``voltix_rig.P``, contour ``#2A1F14`` de 6 px, aplats. Chaque asset
est séparé, en calques nommés ``<g id>``.

Exactitude des connecteurs (vue de face, sans marque ni copie d'une photo commerciale) :

- HDMI type A : face trapézoïdale, coins inférieurs chanfreinés des DEUX côtés, languette intérieure
  portant les contacts (19 broches, non dessinées une à une) ;
- DisplayPort : face rectangulaire avec UN SEUL coin chanfreiné (détrompeur asymétrique) ;
- VGA (D-sub 15 « HD15 ») : coque trapézoïdale bleue, 15 broches en 3 rangées de 5, deux vis de
  fixation ; c'est l'interface ANALOGIQUE de l'épisode ;
- câble d'antenne TV : coaxial blanc et fiche « IEC » (prise TV européenne) — même famille physique que
  le coaxial SDI, souvent 75 Ω lui aussi : l'épisode ne prétend PAS que l'impédance les distingue.

Usage :
    python scripts/animation/sdi_137_v3_assets.py   # écrit les SVG + resource/animation-assets/sdi-v3-catalogue.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sdi_137_assets import C, OUT, THIN, _text  # noqa: E402
from voltix_rig import ROOT, _manifest_sha, _sha, _svg  # noqa: E402

V3 = {
    **C,
    "mur": "#DCE7F2", "sol": "#A9B8C8", "bois": "#C98A4B", "bois-ombre": "#A86E36", "ciel": "#8FD0F2",
    "herbe": "#5DBB63", "herbe-fonce": "#3E9A4A", "soleil": "#F7C325", "toit": "#D2553A", "vga": "#3D6FD1",
    "blanc-cable": "#F2F2EE", "plastique": "#2E3136",
}


# --------------------------------------------------------------------------------------------- salon
def build_box() -> str:
    """Box TV / décodeur générique, voyant allumé, sortie HDMI à l'arrière (étiquetée)."""
    return _svg("Box TV (sortie HDMI)", 360, 150, (
        f'<g id="boitier"><rect x="20" y="30" width="320" height="86" rx="16" fill="{V3["plastique"]}" {OUT}/>'
        f'<rect x="40" y="112" width="40" height="16" rx="5" fill="{V3["panneau"]}" {THIN}/>'
        f'<rect x="280" y="112" width="40" height="16" rx="5" fill="{V3["panneau"]}" {THIN}/></g>'
        f'<g id="facade"><circle cx="300" cy="72" r="9" fill="{V3["vert"]}" {THIN}/>'
        f'<rect x="54" y="62" width="150" height="20" rx="6" fill="{V3["ecran"]}"/></g>'
        f'<g id="sortie-hdmi"><path d="M228,58 h40 v14 l-6,8 h-28 l-6,-8 Z" fill="{V3["metal"]}" {THIN}/>'
        f'{_text(248, 104, "HDMI", 15, fill=V3["isolant"])}</g>'))


def build_tv() -> str:
    """Téléviseur à écran plat sur pied ; écran noir (l'asset « image-paysage » s'y superpose).
    Écran utile : 480 × 260, centre (280, 170) du repère 560 × 400."""
    return _svg("Téléviseur (entrée HDMI)", 560, 400, (
        f'<g id="pied"><rect x="250" y="320" width="60" height="40" fill="{V3["panneau"]}" {OUT}/>'
        f'<rect x="170" y="352" width="220" height="26" rx="10" fill="{V3["plastique"]}" {OUT}/></g>'
        f'<g id="cadre"><rect x="20" y="20" width="520" height="300" rx="18" fill="{V3["plastique"]}" {OUT}/></g>'
        f'<g id="ecran"><rect x="40" y="40" width="480" height="260" rx="6" fill="{V3["ecran"]}"/></g>'
        f'<g id="entree-hdmi"><path d="M500,300 h26 v10 l-4,6 h-18 l-4,-6 Z" fill="{V3["metal"]}" {THIN}/></g>'))


def build_flat_cable() -> str:
    """Câble domestique (HDMI ou DisplayPort selon l'étiquette de la scène) : gaine noire, deux fiches plates
    vues de profil — surmoulage plastique et coque métallique. Repère 600 × 80."""
    def plug(x: float, direction: int) -> str:
        mold_x = x if direction > 0 else x - 70
        shell_x = mold_x + 70 if direction > 0 else mold_x - 34
        return (f'<rect x="{mold_x}" y="18" width="70" height="44" rx="10" fill="{V3["panneau"]}" {THIN}/>'
                f'<rect x="{shell_x}" y="26" width="34" height="28" rx="3" fill="{V3["metal"]}" {THIN}/>'
                f'<line x1="{shell_x + 6}" y1="40" x2="{shell_x + 28}" y2="40" stroke="{V3["metal-ombre"]}" '
                f'stroke-width="4" stroke-linecap="round"/>')
    return _svg("Câble domestique HDMI / DisplayPort", 600, 80, (
        f'<g id="gaine"><rect x="104" y="30" width="392" height="20" rx="9" fill="{V3["gaine"]}" {OUT}/>'
        f'<line x1="118" y1="35" x2="482" y2="35" stroke="{V3["gaine-reflet"]}" stroke-width="4" '
        f'stroke-linecap="round"/></g>'
        f'<g id="fiches">{plug(104, -1)}{plug(496, 1)}</g>'))


def build_hdmi_face() -> str:
    """Fiche HDMI type A vue de face : trapèze, deux coins inférieurs chanfreinés, languette de contacts."""
    return _svg("Fiche HDMI (vue de face)", 220, 140, (
        f'<g id="coque"><path d="M20,30 H200 V82 L178,112 H42 L20,82 Z" fill="{V3["metal"]}" {OUT}/></g>'
        f'<g id="interieur"><path d="M38,46 H182 V78 L166,98 H54 L38,78 Z" fill="{V3["plastique"]}"/>'
        f'<rect x="58" y="60" width="104" height="14" rx="3" fill="{V3["broche"]}" {THIN}/></g>'))


def build_dp_face() -> str:
    """Fiche DisplayPort vue de face : rectangle, UN seul coin chanfreiné (détrompeur asymétrique)."""
    return _svg("Fiche DisplayPort (vue de face)", 220, 140, (
        f'<g id="coque"><path d="M24,28 H196 V112 H50 L24,86 Z" fill="{V3["metal"]}" {OUT}/></g>'
        f'<g id="interieur"><path d="M40,44 H180 V96 H58 L40,78 Z" fill="{V3["plastique"]}"/>'
        f'<rect x="62" y="62" width="104" height="14" rx="3" fill="{V3["broche"]}" {THIN}/></g>'))


def build_vga_face() -> str:
    """Fiche VGA (D-sub 15) vue de face : coque trapézoïdale bleue, 3 rangées de 5 broches, 2 vis."""
    pins = "".join(
        f'<circle cx="{x0 + i * 26}" cy="{y}" r="5" fill="{V3["broche"]}" stroke="{V3["contour"]}" stroke-width="2"/>'
        for y, x0 in ((58, 88), (80, 75), (102, 88)) for i in range(5))
    screws = "".join(f'<circle cx="{x}" cy="80" r="13" fill="{V3["metal"]}" {THIN}/>'
                     f'<line x1="{x - 7}" y1="80" x2="{x + 7}" y2="80" stroke="{V3["contour"]}" stroke-width="3"/>'
                     for x in (26, 254))
    return _svg("Fiche VGA analogique (vue de face)", 280, 160, (
        f'<g id="plaque"><rect x="8" y="30" width="264" height="100" rx="12" fill="{V3["vga"]}" {OUT}/></g>'
        f'<g id="vis">{screws}</g>'
        f'<g id="coque"><path d="M46,42 H234 L218,118 H62 Z" fill="{V3["metal"]}" {THIN}/>'
        f'<path d="M54,48 H226 L212,112 H68 Z" fill="{V3["plastique"]}"/></g>'
        f'<g id="broches">{pins}</g>'))


# --------------------------------------------------------------------------------------------- bureau
def build_pc() -> str:
    """Tour d'ordinateur générique, sortie DisplayPort étiquetée « DP »."""
    vents = "".join(f'<line x1="56" y1="{y}" x2="144" y2="{y}" stroke="{V3["panneau"]}" stroke-width="5" '
                    f'stroke-linecap="round"/>' for y in (230, 248, 266, 284))
    return _svg("Ordinateur (sortie DisplayPort)", 200, 360, (
        f'<g id="boitier"><rect x="30" y="20" width="140" height="320" rx="14" fill="{V3["corps-appareil"]}" {OUT}/>'
        f'{vents}</g>'
        f'<g id="facade"><circle cx="100" cy="70" r="14" fill="{V3["metal"]}" {THIN}/>'
        f'<circle cx="100" cy="70" r="5" fill="{V3["signal"]}"/></g>'
        f'<g id="sortie-dp"><path d="M78,140 h44 v18 h-36 l-8,-8 Z" fill="{V3["metal"]}" {THIN}/>'
        f'{_text(100, 184, "DP", 18, fill=V3["isolant"])}</g>'))


def build_desk_monitor() -> str:
    """Moniteur d'ordinateur, bords fins, pied central. Écran utile 408 × 236, centre (240, 154) du 480 × 400."""
    return _svg("Moniteur d'ordinateur (entrée DisplayPort)", 480, 400, (
        f'<g id="pied"><rect x="220" y="290" width="40" height="70" fill="{V3["corps-appareil"]}" {OUT}/>'
        f'<rect x="150" y="352" width="180" height="24" rx="10" fill="{V3["panneau"]}" {OUT}/></g>'
        f'<g id="cadre"><rect x="20" y="20" width="440" height="270" rx="14" fill="{V3["panneau"]}" {OUT}/></g>'
        f'<g id="ecran"><rect x="36" y="36" width="408" height="236" rx="4" fill="{V3["ecran"]}"/></g>'))


def build_picture() -> str:
    """Image affichée (paysage simple, ciel, soleil, collines, maison), 316 × 190 comme la mire v1 :
    même image sur le téléviseur, le moniteur de bureau et le moniteur de plateau."""
    return _svg("Image affichée (paysage)", 316, 190, (
        f'<g id="ciel"><rect x="0" y="0" width="316" height="190" fill="{V3["ciel"]}"/>'
        f'<circle cx="252" cy="52" r="26" fill="{V3["soleil"]}"/></g>'
        f'<g id="collines"><path d="M0,130 Q80,78 170,124 T316,112 V190 H0 Z" fill="{V3["herbe"]}"/>'
        f'<path d="M0,160 Q110,128 220,156 T316,150 V190 H0 Z" fill="{V3["herbe-fonce"]}"/></g>'
        f'<g id="maison"><rect x="70" y="106" width="44" height="34" fill="{V3["isolant"]}"/>'
        f'<path d="M64,108 L92,84 L120,108 Z" fill="{V3["toit"]}"/>'
        f'<rect x="86" y="120" width="12" height="20" fill="{V3["bois-ombre"]}"/></g>'))


# --------------------------------------------------------------------------------------------- plateau
def build_set() -> str:
    """Décor de plateau TV clair et lisible : mur, sol, voyant « ON AIR », bureau de présentation."""
    return _svg("Plateau de télévision", 1080, 760, (
        f'<g id="mur"><rect x="0" y="0" width="1080" height="520" fill="{V3["mur"]}"/>'
        + "".join(f'<rect x="{x}" y="90" width="150" height="300" rx="10" fill="#C9D8E8"/>' for x in (120, 465, 810))
        + "</g>"
        f'<g id="sol"><rect x="0" y="520" width="1080" height="240" fill="{V3["sol"]}"/>'
        f'<line x1="0" y1="520" x2="1080" y2="520" stroke="{V3["contour"]}" stroke-width="6"/></g>'
        f'<g id="on-air"><rect x="440" y="24" width="200" height="54" rx="12" fill="{V3["rouge"]}" {OUT}/>'
        f'{_text(540, 62, "ON AIR", 30, fill="#FFFFFF")}</g>'
        f'<g id="bureau"><path d="M330,470 H750 L720,640 H360 Z" fill="{V3["bois"]}" {OUT}/>'
        f'<rect x="310" y="440" width="460" height="40" rx="10" fill="{V3["bois-ombre"]}" {OUT}/></g>'))


def build_light() -> str:
    """Projecteur de plateau sur pied, volets ouverts."""
    return _svg("Projecteur de plateau", 220, 360, (
        f'<g id="pied"><path d="M110,170 L50,350 M110,170 L170,350 M110,170 L110,350" fill="none" '
        f'stroke="{V3["contour"]}" stroke-width="12" stroke-linecap="round"/>'
        f'<path d="M110,170 L50,350 M110,170 L170,350 M110,170 L110,350" fill="none" '
        f'stroke="{V3["corps-appareil"]}" stroke-width="5" stroke-linecap="round"/></g>'
        f'<g id="tete"><rect x="54" y="60" width="112" height="100" rx="14" fill="{V3["plastique"]}" {OUT}/>'
        f'<circle cx="110" cy="110" r="34" fill="{V3["soleil"]}" {THIN}/></g>'
        f'<g id="volets"><path d="M54,66 L20,40 V180 L54,154 Z" fill="{V3["panneau"]}" {THIN}/>'
        f'<path d="M166,66 L200,40 V180 L166,154 Z" fill="{V3["panneau"]}" {THIN}/></g>'))


# --------------------------------------------------------------------------------------------- antenne
def build_antenna_cable() -> str:
    """Câble coaxial d'antenne domestique : gaine blanche, fiches « IEC » (prise TV) mâle et femelle."""
    male = (f'<rect x="46" y="18" width="62" height="44" rx="10" fill="{V3["blanc-cable"]}" {THIN}/>'
            f'<rect x="12" y="27" width="36" height="26" rx="4" fill="{V3["metal"]}" {THIN}/>'
            f'<line x1="2" y1="40" x2="14" y2="40" stroke="{V3["broche"]}" stroke-width="5" stroke-linecap="round"/>')
    female = (f'<rect x="532" y="18" width="62" height="44" rx="10" fill="{V3["blanc-cable"]}" {THIN}/>'
              f'<rect x="592" y="25" width="34" height="30" rx="4" fill="{V3["metal"]}" {THIN}/>'
              f'<circle cx="610" cy="40" r="6" fill="{V3["contour"]}"/>')
    return _svg("Câble d'antenne TV (coaxial, fiches IEC)", 640, 80, (
        f'<g id="gaine"><rect x="104" y="30" width="432" height="20" rx="9" fill="{V3["blanc-cable"]}" {OUT}/></g>'
        f'<g id="fiches">{male}{female}</g>'))


def build_antenna() -> str:
    """Antenne râteau (Yagi) de toit, mât et éléments ; descente vers le câble d'antenne."""
    elements = "".join(f'<line x1="{x}" y1="{60 - h}" x2="{x}" y2="{60 + h}" stroke="{V3["metal-ombre"]}" '
                       f'stroke-width="8" stroke-linecap="round"/>' for x, h in ((40, 44), (80, 38), (120, 34),
                                                                                  (160, 30), (200, 26), (240, 22)))
    return _svg("Antenne TV râteau", 280, 240, (
        f'<g id="mat"><rect x="128" y="60" width="16" height="170" rx="6" fill="{V3["metal"]}" {THIN}/></g>'
        f'<g id="elements"><line x1="30" y1="60" x2="250" y2="60" stroke="{V3["contour"]}" stroke-width="10" '
        f'stroke-linecap="round"/>{elements}</g>'))


ASSETS = {  # clé → (identifiant, nom, description, fabrique, largeur, hauteur)
    "box-tv": ("ast_137000000100", "Box TV", "Décodeur, sortie HDMI étiquetée.", build_box, 360, 150),
    "televiseur": ("ast_137000000101", "Téléviseur", "Écran utile 480 × 260 centré en (280, 170).", build_tv,
                   560, 400),
    "cable-plat": ("ast_137000000102", "Câble domestique HDMI / DP", "Gaine noire, fiches plates de profil.",
                   build_flat_cable, 600, 80),
    "fiche-hdmi-face": ("ast_137000000103", "Fiche HDMI — vue de face", "Trapèze, deux coins chanfreinés.",
                        build_hdmi_face, 220, 140),
    "pc-tour": ("ast_137000000104", "Ordinateur", "Tour, sortie DisplayPort « DP ».", build_pc, 200, 360),
    "moniteur-bureau": ("ast_137000000105", "Moniteur d'ordinateur", "Écran utile 408 × 236 centré en (240, 154).",
                        build_desk_monitor, 480, 400),
    "fiche-dp-face": ("ast_137000000106", "Fiche DisplayPort — vue de face", "Un seul coin chanfreiné.",
                      build_dp_face, 220, 140),
    "fiche-vga-face": ("ast_137000000107", "Fiche VGA — vue de face", "D-sub 15, 3 × 5 broches, 2 vis ; analogique.",
                       build_vga_face, 280, 160),
    "plateau-decor": ("ast_137000000108", "Plateau TV", "Mur, sol, voyant ON AIR, bureau.", build_set, 1080, 760),
    "projecteur": ("ast_137000000109", "Projecteur de plateau", "Sur pied, volets ouverts.", build_light, 220, 360),
    "image-paysage": ("ast_137000000110", "Image affichée", "Paysage 316 × 190, superposable aux écrans.",
                      build_picture, 316, 190),
    "cable-antenne": ("ast_137000000111", "Câble d'antenne TV", "Coaxial blanc, fiches IEC.", build_antenna_cable,
                      640, 80),
    "antenne-rateau": ("ast_137000000112", "Antenne TV râteau", "Antenne de toit.", build_antenna, 280, 240),
}


def rel_path(key: str) -> str:
    return f"animation-assets/{key}/v1/{key}.svg"


def outputs() -> dict[str, str]:
    return {rel_path(key): spec[3]() for key, spec in ASSETS.items()}


def _ids(svg: str) -> list[str]:
    return re.findall(r'<g id="([^"]+)"', svg)


def build_catalog(resource: Path) -> dict:
    assets = []
    for key, (asset_id, name, description, _builder, width, height) in ASSETS.items():
        rel = rel_path(key)
        content = (resource / rel).read_text(encoding="utf-8")
        manifest = {"format": "svg", "width": width, "height": height, "anchor": {"x": 0.5, "y": 0.5},
                    "parts": sorted(set(_ids(content))),
                    "files": [{"role": "base", "path": rel, "sha256": _sha(resource / rel)}]}
        assets.append({"id": asset_id, "name": name, "kind": "prop", "description": description, "is_active": True,
                       "character": None, "location": None,
                       "versions": [{"version": 1, "manifest": manifest, "sha256": _manifest_sha(manifest),
                                     "notes": "Épisode #137 V3 : du HDMI familier au SDI."}]})
    return {"schema": "lody.animation-assets/1", "assets": assets}


BOARD = [  # planche de contrôle V3 : (clé, x, y, largeur affichée)
    ("box-tv", 30, 70, 220), ("cable-plat", 270, 110, 300), ("televiseur", 590, 50, 300),
    ("image-paysage", 920, 70, 200), ("fiche-hdmi-face", 1150, 70, 170),
    ("pc-tour", 30, 330, 110), ("moniteur-bureau", 170, 330, 280), ("fiche-dp-face", 480, 350, 170),
    ("fiche-vga-face", 700, 380, 200), ("antenne-rateau", 940, 330, 200), ("cable-antenne", 1160, 380, 300),
    ("plateau-decor", 30, 620, 560), ("projecteur", 620, 640, 150),
]


def build_board(resource: Path = ROOT / "resource") -> str:
    body = ""
    for key, x, y, width in BOARD:
        _, name, _, _, w, h = ASSETS[key]
        inner = (resource / rel_path(key)).read_text(encoding="utf-8")
        inner = re.sub(r"<title>.*?</title>", "", re.sub(r"^<svg [^>]*>", "", inner.strip()).removesuffix("</svg>"))
        body += (f'<svg x="{x}" y="{y}" width="{width}" height="{round(width * h / w)}" viewBox="0 0 {w} {h}">'
                 f'{inner}</svg>{_text(x, y - 12, name, 18, "start", 600)}')
    return _svg("Épisode #137 V3 — planche de contrôle (HDMI, DisplayPort, VGA, plateau, antenne)", 1500, 1060,
                f'<rect width="1500" height="1060" fill="#F4F6FA"/>{body}')


def write_all(resource: Path = ROOT / "resource") -> list[Path]:
    written = []
    for rel, content in outputs().items():
        path = resource / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    catalog = resource / "animation-assets" / "sdi-v3-catalogue.json"
    catalog.write_text(json.dumps(build_catalog(resource), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    written.append(catalog)
    board = ROOT / "docs" / "animation" / "voltix-137" / "v3" / "planche-v3.svg"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text(build_board(resource), encoding="utf-8")
    written.append(board)
    return written


if __name__ == "__main__":
    for item in write_all():
        print(item.relative_to(ROOT))
    sys.exit(0)
