"""Schémas V2 de l'épisode « Voltix explique le câble SDI » (#160, V2 éditoriale de #137) — SVG éditables.

S'ajoutent aux 23 schémas V1 (``sdi_137_assets.py``, inchangés) ; même style que Voltix (#136) : palette,
contour ``#2A1F14`` de 6 px, aplats, calques ``<g id>`` nommés, aucune image aplatie ni dégradé.

Ce que la V2 corrige (retours du propriétaire sur la V1, ticket #160) :

- ``salon-hdmi`` : objet réellement montré par l'accroche (box → câble HDMI → télé, branchés) ;
- ``bits-serie`` : les « 01 » sont expliqués : des bits envoyés l'un après l'autre (« série ») ;
- ``lieu-*`` : le broadcast n'est pas réduit à une régie (plateau, car régie, direct) ;
- ``enregistreur``, ``grille-routage``, ``convertisseur`` + ``fleche-signal`` : une chaîne INDICATIVE
  d'équipements, pas un câblage universel caméra → mélangeur ;
- ``coax-cotes`` : 75 Ω = impédance caractéristique fixée par la géométrie (d, D) et l'isolant, pas une
  résistance ;
- ``liaison-connectee`` : caméra, câble et moniteur PHYSIQUEMENT reliés (fiches BNC sur les embases, câble
  continu), pour la conclusion « signal SDI → coaxial 75 Ω → connecteurs BNC ».

Usage :
    python scripts/animation/sdi_160_assets.py   # écrit les SVG v1 + resource/animation-assets/sdi-v2-catalogue.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sdi_137_assets as v1  # noqa: E402
from voltix_rig import ROOT, _manifest_sha, _sha, _svg  # noqa: E402

C, OUT, THIN, _text, _bnc_port = v1.C, v1.OUT, v1.THIN, v1._text, v1._bnc_port
C = {**C, "bois": "#B07A4A", "mur": "#F4F6FA", "camion": "#E9EDF2", "led": "#3DBE6A"}


def _inner(svg: str) -> str:
    """Contenu d'un SVG sans sa balise racine ni son titre (pour l'imbriquer dans un schéma composé)."""
    inner = re.sub(r"^<svg [^>]*>", "", svg.strip()).removesuffix("</svg>")
    return re.sub(r"<title>.*?</title>", "", inner)


def _hdmi_plug(x: float, y: float, direction: int) -> str:
    """Fiche HDMI de profil, vue de dessus : coque trapézoïdale, embout métallique ; ``direction`` = sens de
    l'embout (+1 vers la droite)."""
    tip = x + direction * 30
    return (f'<rect x="{min(x, x - direction * 46)}" y="{y - 16}" width="46" height="32" rx="6" '
            f'fill="{C["panneau"]}" {THIN}/>'
            f'<rect x="{min(x, tip)}" y="{y - 11}" width="30" height="22" rx="2" fill="{C["metal"]}" {THIN}/>')


# ------------------------------------------------------------------------------------------- accroche

def build_salon_hdmi() -> str:
    """Salon : box → câble HDMI → téléviseur, branchés (fiches dans les prises), 900 × 460."""
    screen = ('<rect x="470" y="60" width="380" height="226" rx="8" fill="#7FC8F0"/>'
              '<path d="M470,230 L560,170 L640,220 L720,150 L850,240 L850,286 L470,286 Z" fill="#5BAF6A"/>'
              '<circle cx="790" cy="110" r="26" fill="#FFE48A"/>')
    cable = (f'<path d="M226,350 C300,350 300,420 400,420 C470,420 440,330 446,300" fill="none" '
             f'stroke="{C["contour"]}" stroke-width="16" stroke-linecap="round"/>'
             f'<path d="M226,350 C300,350 300,420 400,420 C470,420 440,330 446,300" fill="none" '
             f'stroke="{C["panneau"]}" stroke-width="8" stroke-linecap="round"/>')
    return _svg("Salon : box, câble HDMI et télé branchés", 900, 460, (
        f'<g id="meuble"><rect x="10" y="380" width="880" height="60" rx="8" fill="{C["bois"]}" {OUT}/></g>'
        f'<g id="tele"><rect x="450" y="40" width="420" height="266" rx="16" fill="{C["panneau"]}" {OUT}/>'
        f'{screen}<rect x="630" y="306" width="60" height="44" fill="{C["corps-appareil"]}" {OUT}/>'
        f'<rect x="570" y="346" width="180" height="30" rx="8" fill="{C["panneau"]}" {OUT}/></g>'
        f'<g id="box"><rect x="30" y="310" width="190" height="70" rx="10" fill="{C["corps-appareil"]}" {OUT}/>'
        f'<circle cx="62" cy="345" r="7" fill="{C["led"]}"/>{_text(130, 354, "BOX", 24, fill=C["isolant"])}</g>'
        f'<g id="cable-hdmi">{cable}{_hdmi_plug(226, 350, -1)}'
        f'<g transform="rotate(-90 446 300)">{_hdmi_plug(446, 300, 1)}</g></g>'
        f'<g id="etiquette-hdmi"><rect x="262" y="236" width="130" height="48" rx="12" fill="#FFFFFF" {THIN}/>'
        f'{_text(327, 270, "HDMI", 28)}</g>'))


# ------------------------------------------------------------------------------------------ définition

BITS = "0110100111010010"


def build_bits_serie() -> str:
    """Bits envoyés l'un après l'autre sur UN fil (sens de lecture → ), 960 × 170 : explique « série »."""
    cells = "".join(
        f'<rect x="{40 + i * 52}" y="40" width="44" height="60" rx="8" '
        f'fill="{C["signal"] if bit == "1" else "#FFFFFF"}" {THIN}/>'
        f'{_text(62 + i * 52, 83, bit, 30, fill="#FFFFFF" if bit == "1" else C["contour"])}'
        for i, bit in enumerate(BITS))
    return _svg("Bits envoyés en série, l'un après l'autre", 960, 170, (
        f'<g id="fil"><line x1="20" y1="70" x2="900" y2="70" stroke="{C["contour"]}" stroke-width="6"/></g>'
        f'<g id="bits">{cells}</g>'
        f'<g id="sens"><path d="M880,130 L930,130 M912,116 L932,130 L912,144" fill="none" stroke="{C["contour"]}" '
        f'stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>'
        + _text(860, 142, "un bit après l’autre", 26, "end", 600) + "</g>"))


# ---------------------------------------------------------------------------------------- broadcast

def build_lieu_plateau() -> str:
    """Plateau de télévision : projecteur, décor, caméra sur pied, 260 × 240."""
    return _svg("Lieu : plateau télé", 260, 240, (
        f'<g id="decor"><rect x="20" y="40" width="220" height="150" rx="12" fill="{C["mur"]}" {OUT}/>'
        f'<rect x="20" y="170" width="220" height="40" rx="8" fill="{C["bois"]}" {OUT}/></g>'
        f'<g id="projecteur"><path d="M60,20 L100,20 L110,60 L50,60 Z" fill="{C["panneau"]}" {THIN}/>'
        f'<path d="M58,60 L102,60 L150,170 L20,170 Z" fill="#FFE48A" opacity="0.7"/></g>'
        f'<g id="camera"><rect x="160" y="110" width="64" height="42" rx="8" fill="{C["corps-appareil"]}" {THIN}/>'
        f'<rect x="140" y="120" width="24" height="24" rx="4" fill="{C["panneau"]}" {THIN}/>'
        f'<path d="M192,152 L172,196 M192,152 L212,196" stroke="{C["contour"]}" stroke-width="6"/></g>'))


def build_lieu_car() -> str:
    """Car régie (camion de production mobile), 260 × 240."""
    return _svg("Lieu : car régie", 260, 240, (
        f'<g id="camion"><rect x="14" y="70" width="170" height="110" rx="10" fill="{C["camion"]}" {OUT}/>'
        f'<path d="M184,100 L224,100 L248,140 L248,180 L184,180 Z" fill="{C["signal"]}" {OUT}/>'
        f'<rect x="196" y="110" width="30" height="26" rx="4" fill="#BFE6F7" {THIN}/>'
        + "".join(f'<rect x="{34 + i * 36}" y="96" width="26" height="20" rx="3" fill="{C["ecran"]}"/>'
                  for i in range(4))
        + f'<circle cx="60" cy="186" r="22" fill="{C["panneau"]}" {OUT}/>'
        f'<circle cx="206" cy="186" r="22" fill="{C["panneau"]}" {OUT}/></g>'
        f'<g id="antenne"><path d="M90,70 L90,40" stroke="{C["contour"]}" stroke-width="6"/>'
        f'<path d="M66,24 Q90,48 114,24" fill="{C["metal"]}" {THIN}/></g>'))


def build_lieu_direct() -> str:
    """Événement en direct (scène, public), 260 × 240."""
    heads = "".join(f'<circle cx="{30 + i * 40}" cy="206" r="16" fill="{C["corps-appareil"]}" {THIN}/>'
                    for i in range(6))
    return _svg("Lieu : événement en direct", 260, 240, (
        f'<g id="scene"><rect x="20" y="40" width="220" height="130" rx="10" fill="{C["panneau"]}" {OUT}/>'
        f'<rect x="20" y="150" width="220" height="30" fill="{C["bois"]}" {OUT}/>'
        f'<circle cx="130" cy="98" r="26" fill="{C["jaune"]}" {THIN}/></g>'
        f'<g id="direct"><rect x="150" y="14" width="96" height="40" rx="10" fill="{C["rouge"]}" {THIN}/>'
        f'<circle cx="170" cy="34" r="7" fill="#FFFFFF"/>{_text(212, 44, "LIVE", 22, fill="#FFFFFF")}</g>'
        f'<g id="public">{heads}</g>'))


# ------------------------------------------------------------------------------------------ équipements

def build_recorder() -> str:
    """Enregistreur vidéo générique (rack), entrée SDI en BNC, 340 × 170."""
    return _svg("Enregistreur (entrée SDI)", 340, 170, (
        f'<g id="boitier"><rect x="10" y="20" width="320" height="130" rx="12" fill="{C["corps-appareil"]}" {OUT}/>'
        f'<rect x="30" y="40" width="130" height="70" rx="6" fill="{C["ecran"]}"/>'
        f'<circle cx="200" cy="74" r="18" fill="{C["rouge"]}" {THIN}/>'
        f'{_text(200, 128, "REC", 20, fill=C["isolant"])}</g>'
        f'<g id="entree-sdi">{_bnc_port(286, 74, 15)}{_text(286, 128, "SDI IN", 15, fill=C["isolant"])}</g>'))


def build_router() -> str:
    """Grille de routage (matrice) : entrées en lignes, sorties en colonnes, points de croisement, 400 × 260."""
    dots = "".join(f'<circle cx="{150 + c * 52}" cy="{70 + r * 44}" r="9" '
                   f'fill="{C["signal"] if c == r or (c, r) == (3, 0) else C["metal"]}" {THIN}/>'
                   for r in range(4) for c in range(4))
    lines = "".join(f'<line x1="120" y1="{70 + r * 44}" x2="330" y2="{70 + r * 44}" stroke="{C["metal-ombre"]}" '
                    f'stroke-width="3"/>' for r in range(4))
    lines += "".join(f'<line x1="{150 + c * 52}" y1="46" x2="{150 + c * 52}" y2="220" '
                     f'stroke="{C["metal-ombre"]}" stroke-width="3"/>' for c in range(4))
    return _svg("Grille de routage (matrice d'aiguillage)", 400, 260, (
        f'<g id="boitier"><rect x="10" y="20" width="380" height="226" rx="14" fill="{C["corps-appareil"]}" {OUT}/></g>'
        f'<g id="entrees">' + "".join(_bnc_port(60, 70 + r * 44, 12) for r in range(4)) + "</g>"
        f'<g id="matrice">{lines}{dots}</g>'))


def build_converter() -> str:
    """Convertisseur SDI → HDMI : entrée BNC à gauche, sortie HDMI à droite, 320 × 160."""
    return _svg("Convertisseur SDI → HDMI", 320, 160, (
        f'<g id="boitier"><rect x="40" y="30" width="240" height="100" rx="14" fill="{C["corps-appareil"]}" {OUT}/>'
        f'{_text(160, 90, "SDI → HDMI", 26, fill="#FFFFFF")}</g>'
        f'<g id="entree-sdi">{_bnc_port(40, 80, 14)}</g>'
        f'<g id="sortie-hdmi"><rect x="266" y="66" width="36" height="28" rx="4" fill="{C["panneau"]}" {THIN}/>'
        f'<rect x="272" y="74" width="24" height="12" rx="2" fill="{C["metal"]}"/></g>'))


def build_signal_arrow() -> str:
    """Flèche « le signal va vers » (liaison symbolique d'une chaîne indicative), 220 × 60."""
    return _svg("Flèche de signal", 220, 60, (
        f'<g id="fleche"><path d="M10,22 L160,22 L160,8 L210,30 L160,52 L160,38 L10,38 Z" '
        f'fill="{C["signal"]}" {THIN}/></g>'))


# ------------------------------------------------------------------------------------------- 75 ohms

COAX_D, COAX_SMALL_D = 4.6, 1.0  # mm, coaxial SDI type « 1694A » (diélectrique, âme) — mêmes proportions que V1


def build_coax_dimensions() -> str:
    """Coupe transversale cotée : diamètre de l'âme ``d`` et du diélectrique ``D`` ; l'impédance
    caractéristique dépend de leur rapport et de l'isolant, 760 × 560."""
    cx, cy, scale = 280, 280, 70  # 70 px par mm
    big, small = COAX_D * scale / 2, COAX_SMALL_D * scale / 2
    rings = (f'<circle cx="{cx}" cy="{cy}" r="{big + 52}" fill="{C["gaine"]}" {OUT}/>'
             f'<circle cx="{cx}" cy="{cy}" r="{big + 22}" fill="{C["tresse"]}" {THIN}/>'
             f'<circle cx="{cx}" cy="{cy}" r="{big}" fill="{C["isolant"]}" {OUT}/>'
             f'<circle cx="{cx}" cy="{cy}" r="{small}" fill="{C["cuivre"]}" {OUT}/>')

    def cote(y: float, half: float, label: str) -> str:
        return (f'<path d="M{cx - half},{y} L{cx + half},{y}" stroke="{C["alerte"]}" stroke-width="5"/>'
                f'<path d="M{cx - half + 14},{y - 9} L{cx - half},{y} L{cx - half + 14},{y + 9} '
                f'M{cx + half - 14},{y - 9} L{cx + half},{y} L{cx + half - 14},{y + 9}" fill="none" '
                f'stroke="{C["alerte"]}" stroke-width="5" stroke-linecap="round"/>'
                f'{_text(cx + half + 20, y + 10, label, 34, "start", fill=C["alerte"])}')
    return _svg("Coaxial 75 Ω : impédance fixée par d, D et l'isolant", 760, 560, (
        f'<g id="coupe">{rings}</g>'
        f'<g id="cote-petit-d">{cote(cy, small, "")}{_text(cx, cy - 46, "d", 40, fill=C["alerte"])}</g>'
        f'<g id="cote-grand-d"><path d="M{cx - big},{cy} L{cx - big},{cy + big + 96} M{cx + big},{cy} '
        f'L{cx + big},{cy + big + 96}" stroke="{C["alerte"]}" stroke-width="3" stroke-dasharray="10 8"/>'
        f'{cote(cy + big + 82, big, "D")}</g>'
        f'<g id="legende">{_text(610, 160, "âme : d", 30)}{_text(610, 210, "isolant : D", 30)}'
        f'{_text(610, 310, "d, D et isolant", 26, weight=600)}{_text(610, 372, "→ 75 Ω", 44)}</g>'))


# ---------------------------------------------------------------------------------------- conclusion

# Embases sur les assets V1 : caméra (380 × 320) sortie en (296, 182) ; moniteur (400 × 320) entrée en (352, 244).
CAM_AT, MON_AT = (0, 0), (600, 0)
CAM_PORT = (CAM_AT[0] + 296, CAM_AT[1] + 182)
MON_PORT = (MON_AT[0] + 352, MON_AT[1] + 244)
LINK_PATH = (f"M{CAM_PORT[0]},{CAM_PORT[1]} C{CAM_PORT[0] + 40},{CAM_PORT[1] + 210} "
             f"{MON_PORT[0] + 40},{MON_PORT[1] + 150} {MON_PORT[0]},{MON_PORT[1]}")


def _mounted_plug(x: float, y: float) -> str:
    """Fiche BNC verrouillée sur son embase, vue de face : bague moletée par-dessus le fût."""
    return (f'<circle cx="{x}" cy="{y}" r="24" fill="{C["metal"]}" {THIN}/>'
            f'<circle cx="{x}" cy="{y}" r="15" fill="{C["metal-ombre"]}" {THIN}/>'
            f'<circle cx="{x}" cy="{y}" r="5" fill="{C["contour"]}"/>')


def build_connected_link() -> str:
    """Caméra → câble coaxial continu → moniteur : fiches BNC verrouillées SUR les embases, 1000 × 470.
    Les extrémités du câble sont exactement les embases des assets V1 (pas de câble flottant)."""
    camera = _inner(v1.build_camera())
    monitor = _inner(v1.build_monitor())
    cable = (f'<path d="{LINK_PATH}" fill="none" stroke="{C["contour"]}" stroke-width="22" stroke-linecap="round"/>'
             f'<path d="{LINK_PATH}" fill="none" stroke="{C["gaine"]}" stroke-width="12" stroke-linecap="round"/>')
    return _svg("Liaison SDI connectée : caméra → coaxial 75 Ω → moniteur, fiches BNC", 1000, 470, (
        f'<g id="camera"><svg x="{CAM_AT[0]}" y="{CAM_AT[1]}" width="380" height="320" viewBox="0 0 380 320">'
        f'{camera}</svg></g>'
        f'<g id="moniteur"><svg x="{MON_AT[0]}" y="{MON_AT[1]}" width="400" height="320" viewBox="0 0 400 320">'
        f'{monitor}</svg></g>'
        f'<g id="coaxial">{cable}</g>'
        f'<g id="fiches-bnc">{_mounted_plug(*CAM_PORT)}{_mounted_plug(*MON_PORT)}</g>'))


# ---------------------------------------------------------------------------------------- catalogue

# clé → (identifiant de catalogue, nom, description, constructeur, largeur, hauteur, ancrage)
ASSETS: dict[str, tuple] = {
    "salon-hdmi": ("ast_160000000001", "Salon : box → HDMI → télé", "Accroche : objets branchés, câble HDMI visible.",
                   build_salon_hdmi, 900, 460, (0.5, 0.5)),
    "bits-serie": ("ast_160000000002", "Bits en série", "Les « 01 » expliqués : un bit après l'autre.",
                   build_bits_serie, 960, 170, (0.5, 0.5)),
    "lieu-plateau": ("ast_160000000010", "Lieu : plateau télé", "Broadcast : production en studio.",
                     build_lieu_plateau, 260, 240, (0.5, 0.5)),
    "lieu-car": ("ast_160000000011", "Lieu : car régie", "Broadcast : production mobile.", build_lieu_car, 260, 240,
                 (0.5, 0.5)),
    "lieu-direct": ("ast_160000000012", "Lieu : direct", "Broadcast : événement en direct.", build_lieu_direct,
                    260, 240, (0.5, 0.5)),
    "enregistreur": ("ast_160000000020", "Enregistreur", "Enregistreur vidéo, entrée SDI en BNC.", build_recorder,
                     340, 170, (0.5, 0.5)),
    "grille-routage": ("ast_160000000021", "Grille de routage", "Matrice qui aiguille les sources vers les sorties.",
                       build_router, 400, 260, (0.5, 0.5)),
    "convertisseur": ("ast_160000000022", "Convertisseur SDI → HDMI", "Entrée BNC, sortie HDMI.", build_converter,
                      320, 160, (0.5, 0.5)),
    "fleche-signal": ("ast_160000000023", "Flèche de signal", "Liaison symbolique d'une chaîne indicative.",
                      build_signal_arrow, 220, 60, (0.5, 0.5)),
    "coax-cotes": ("ast_160000000030", "Coaxial coté (d, D)", "75 Ω = impédance caractéristique (géométrie + isolant).",
                   build_coax_dimensions, 760, 560, (0.5, 0.5)),
    "liaison-connectee": ("ast_160000000040", "Liaison SDI connectée", "Caméra → coaxial → moniteur, BNC verrouillés.",
                          build_connected_link, 1000, 470, (0.5, 0.5)),
}
CATALOG_NAME = "sdi-v2-catalogue.json"


def rel_path(key: str) -> str:
    return f"animation-assets/{key}/v1/{key}.svg"


def outputs() -> dict[str, str]:
    """Chemin relatif (au dossier ``resource/``) → contenu SVG de la version 1."""
    return {rel_path(key): spec[3]() for key, spec in ASSETS.items()}


def build_catalog(resource: Path) -> dict:
    assets = []
    for key, (asset_id, name, description, _builder, width, height, anchor) in ASSETS.items():
        rel = rel_path(key)
        content = (resource / rel).read_text(encoding="utf-8")
        parts = sorted(set(re.findall(r'<g id="([^"]+)"', content)))
        manifest = {"format": "svg", "width": width, "height": height, "anchor": {"x": anchor[0], "y": anchor[1]},
                    "parts": parts, "files": [{"role": "base", "path": rel, "sha256": _sha(resource / rel)}]}
        assets.append({"id": asset_id, "name": name, "kind": "prop", "description": description, "is_active": True,
                       "character": None, "location": None,
                       "versions": [{"version": 1, "manifest": manifest, "sha256": _manifest_sha(manifest),
                                     "notes": "Épisode #137, V2 éditoriale (#160) : schéma v1."}]})
    return {"schema": "lody.animation-assets/1", "assets": assets}


BOARD = [  # planche de contrôle : (clé, x, y, largeur affichée)
    ("salon-hdmi", 30, 60, 560), ("bits-serie", 620, 80, 900), ("lieu-plateau", 620, 330, 220),
    ("lieu-car", 870, 330, 220), ("lieu-direct", 1120, 330, 220),
    ("enregistreur", 30, 420, 280), ("grille-routage", 340, 420, 250), ("convertisseur", 30, 660, 280),
    ("fleche-signal", 340, 700, 200), ("coax-cotes", 640, 640, 520),
    ("liaison-connectee", 30, 1060, 900),
]


def build_board(resource: Path = ROOT / "resource") -> str:
    body = ""
    for key, x, y, width in BOARD:
        _, name, _, _, w, h, _ = ASSETS[key]
        inner = _inner((resource / rel_path(key)).read_text(encoding="utf-8"))
        height = round(width * h / w)
        body += (f'<svg x="{x}" y="{y}" width="{width}" height="{height}" viewBox="0 0 {w} {h}">{inner}</svg>'
                 f'{_text(x, y - 12, name, 18, "start", 600)}')
    return _svg("Épisode #137 V2 (#160) — planche de contrôle des nouveaux schémas", 1560, 1520,
                f'<rect width="1560" height="1520" fill="#F4F6FA"/>{body}')


DOCS = ROOT / "docs" / "animation" / "voltix-137-v2"


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
    board = DOCS / "planche-schemas-v2.svg"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text(build_board(resource), encoding="utf-8")
    written.append(board)
    return written


if __name__ == "__main__":
    for item in write_all():
        print(item.relative_to(ROOT))
    sys.exit(0)
