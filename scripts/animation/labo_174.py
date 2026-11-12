"""Projet expérimental du laboratoire (#174) : « Comment l'image d'une caméra apparaît-elle sur un moniteur ? ».

Source unique et reproductible de tous les livrables du projet :

- assets SVG réutilisables (caméra, câble vidéo, moniteur, sujet filmé, onde de signal) et leur catalogue
  ``lody.animation-assets/1`` importable (identifiants provisoires ``ast_1740000000NN``) ;
- storyboard ``lody.animation/1`` : dialogue Nora / Sam réplique par réplique, 7 scènes de 5 à 7 s (43 s),
  schéma caméra → câble → écran construit progressivement ;
- preset de production importable (template #110).

Nora et Sam sont les marionnettes du laboratoire #173 (identifiants ``ast_173000000001`` et
``ast_173000000002``, catalogue ``labo-personnages-catalogue.json``) : ce script ne crée aucun personnage.
Aucun réseau, aucune voix, aucun fournisseur payant.

Usage : ``python scripts/animation/labo_174.py`` (réécrit les fichiers) ; ``--check`` (échoue s'ils diffèrent).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "resource"
EPISODE = ROOT / "docs" / "episodes" / "labo-camera-moniteur"
FPS, WIDTH, HEIGHT = 30, 1080, 1920

NORA, SAM = "ast_173000000001", "ast_173000000002"  # catalogue #173 (PR #180)
CAMERA, CABLE, MONITEUR, SUJET, SIGNAL = (f"ast_17400000000{n}" for n in range(1, 6))

P = {"contour": "#2A1F14", "corps": "#3C4148", "corps-clair": "#5A616B", "objectif": "#1E2228",
     "verre": "#4FA3C7", "reflet": "#BFE6F2", "cable": "#2B2F36", "fiche": "#C9CED4", "ecran": "#151A22",
     "pied": "#5A616B", "pot": "#D9774A", "terre": "#6B4A32", "feuille": "#43A047", "feuille-ombre": "#2E7D32",
     "fleur": "#F2C230", "coeur": "#E8562A", "signal": "#2BB3C0", "voyant": "#E53935"}
STROKE = f'stroke="{P["contour"]}" stroke-width="6" stroke-linejoin="round"'


# -- assets SVG -------------------------------------------------------------------------------------------------
def _svg(title: str, width: int, height: int, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" '
            f'height="{height}"><title>{title}</title>{body}</svg>\n')


def build_camera() -> str:
    """Caméra vidéo de profil, objectif vers la gauche (vers le sujet), prise de sortie à droite."""
    return _svg("Caméra vidéo", 320, 220, (
        f'<g id="corps"><rect x="96" y="56" width="176" height="112" rx="16" fill="{P["corps"]}" {STROKE}/>'
        f'<rect x="120" y="76" width="96" height="14" rx="7" fill="{P["corps-clair"]}"/></g>'
        f'<g id="viseur"><rect x="196" y="22" width="72" height="36" rx="8" fill="{P["corps-clair"]}" {STROKE}/></g>'
        f'<g id="objectif"><path d="M96,78 L44,62 L44,162 L96,146 Z" fill="{P["objectif"]}" {STROKE}/>'
        f'<ellipse cx="40" cy="112" rx="18" ry="52" fill="{P["verre"]}" {STROKE}/>'
        f'<ellipse cx="34" cy="96" rx="5" ry="14" fill="{P["reflet"]}"/></g>'
        f'<g id="voyant"><circle cx="252" cy="80" r="8" fill="{P["voyant"]}" {STROKE.replace("6", "3")}/></g>'
        f'<g id="sortie"><rect x="272" y="120" width="34" height="24" rx="4" fill="{P["fiche"]}" {STROKE}/></g>'
        f'<g id="poignee"><rect x="132" y="168" width="104" height="30" rx="10" fill="{P["corps-clair"]}" '
        f'{STROKE}/></g>'))


def build_cable() -> str:
    """Câble vidéo droit, générique (sans norme), une fiche à chaque extrémité."""
    return _svg("Câble vidéo", 400, 80, (
        f'<g id="gaine"><path d="M48,40 C140,10 260,70 352,40" fill="none" stroke="{P["contour"]}" '
        f'stroke-width="22" stroke-linecap="round"/><path d="M48,40 C140,10 260,70 352,40" fill="none" '
        f'stroke="{P["cable"]}" stroke-width="12" stroke-linecap="round"/></g>'
        f'<g id="fiche-gauche"><rect x="8" y="26" width="44" height="28" rx="6" fill="{P["fiche"]}" {STROKE}/></g>'
        f'<g id="fiche-droite"><rect x="348" y="26" width="44" height="28" rx="6" fill="{P["fiche"]}" {STROKE}/>'
        f'</g>'))


def build_moniteur() -> str:
    """Moniteur vidéo éteint (écran sombre) sur pied ; l'image s'y superpose avec l'asset « sujet »."""
    return _svg("Moniteur vidéo", 340, 300, (
        f'<g id="pied"><rect x="150" y="214" width="40" height="50" fill="{P["pied"]}" {STROKE}/>'
        f'<rect x="96" y="258" width="148" height="26" rx="10" fill="{P["pied"]}" {STROKE}/></g>'
        f'<g id="cadre"><rect x="10" y="12" width="320" height="210" rx="16" fill="{P["corps"]}" {STROKE}/></g>'
        f'<g id="ecran"><rect x="28" y="30" width="284" height="164" rx="6" fill="{P["ecran"]}"/></g>'
        f'<g id="entree"><rect x="2" y="150" width="20" height="28" rx="4" fill="{P["fiche"]}" {STROKE}/></g>'))


def build_sujet() -> str:
    """Sujet filmé : une plante en pot, reconnaissable aussi en petit sur l'écran du moniteur."""
    leaves = "".join(
        f'<path d="M100,150 Q{x1},{y1} {x2},{y2} Q{x3},{y3} 100,150 Z" fill="{fill}" {STROKE}/>'
        for x1, y1, x2, y2, x3, y3, fill in (
            (40, 130, 30, 70, 80, 100, P["feuille"]), (160, 130, 170, 70, 120, 100, P["feuille"]),
            (70, 120, 84, 52, 110, 96, P["feuille-ombre"]), (130, 120, 120, 56, 92, 100, P["feuille-ombre"])))
    return _svg("Plante en pot", 200, 260, (
        f'<g id="feuilles">{leaves}<line x1="100" y1="150" x2="100" y2="58" stroke="{P["contour"]}" '
        f'stroke-width="6"/></g>'
        f'<g id="fleur"><circle cx="100" cy="48" r="26" fill="{P["fleur"]}" {STROKE}/>'
        f'<circle cx="100" cy="48" r="10" fill="{P["coeur"]}"/></g>'
        f'<g id="pot"><path d="M52,160 L148,160 L136,248 L64,248 Z" fill="{P["pot"]}" {STROKE}/>'
        f'<rect x="44" y="150" width="112" height="24" rx="6" fill="{P["pot"]}" {STROKE}/>'
        f'<ellipse cx="100" cy="156" rx="46" ry="5" fill="{P["terre"]}"/></g>'))


def build_signal() -> str:
    """Onde de signal électrique avec flèche de sens : ce que la caméra envoie dans le câble."""
    return _svg("Signal électrique", 160, 80, (
        f'<g id="onde"><path d="M10,40 Q25,10 40,40 T70,40 T100,40 T130,40" fill="none" stroke="{P["contour"]}" '
        f'stroke-width="14" stroke-linecap="round"/><path d="M10,40 Q25,10 40,40 T70,40 T100,40 T130,40" '
        f'fill="none" stroke="{P["signal"]}" stroke-width="7" stroke-linecap="round"/></g>'
        f'<g id="fleche"><path d="M126,22 L154,40 L126,58 Z" fill="{P["signal"]}" {STROKE}/></g>'))


ASSET_FILES = {
    "animation-assets/labo-camera/v1/labo-camera.svg": build_camera,
    "animation-assets/labo-cable/v1/labo-cable.svg": build_cable,
    "animation-assets/labo-moniteur/v1/labo-moniteur.svg": build_moniteur,
    "animation-assets/labo-sujet/v1/labo-sujet.svg": build_sujet,
    "animation-assets/labo-signal/v1/labo-signal.svg": build_signal,
}
CATALOG_PATH = "animation-assets/labo-174-catalogue.json"


def _manifest_sha(manifest: dict) -> str:
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_catalog(svgs: dict[str, str]) -> dict:
    def prop(rel: str, width: int, height: int, parts: list[str]) -> dict:
        return {"format": "svg", "width": width, "height": height, "anchor": {"x": 0.5, "y": 0.5}, "parts": parts,
                "files": [{"role": "base", "path": rel,
                           "sha256": hashlib.sha256(svgs[rel].encode("utf-8")).hexdigest()}]}

    entries = [
        (CAMERA, "Caméra vidéo", "prop", "Caméra de profil, objectif à gauche, prise de sortie à droite (#174).",
         prop("animation-assets/labo-camera/v1/labo-camera.svg", 320, 220,
              ["corps", "viseur", "objectif", "voyant", "sortie", "poignee"])),
        (CABLE, "Câble vidéo", "prop", "Câble vidéo générique, une fiche à chaque bout, sans norme précise (#174).",
         prop("animation-assets/labo-cable/v1/labo-cable.svg", 400, 80, ["gaine", "fiche-gauche", "fiche-droite"])),
        (MONITEUR, "Moniteur vidéo", "prop", "Moniteur éteint sur pied ; superposer « Plante en pot » pour "
         "l'image affichée (centre de l'écran 30 px au-dessus de l'ancrage, à l'échelle 1) (#174).",
         prop("animation-assets/labo-moniteur/v1/labo-moniteur.svg", 340, 300, ["pied", "cadre", "ecran", "entree"])),
        (SUJET, "Plante en pot", "prop", "Sujet filmé, réutilisé en petit comme image sur l'écran (#174).",
         prop("animation-assets/labo-sujet/v1/labo-sujet.svg", 200, 260, ["feuilles", "fleur", "pot"])),
        (SIGNAL, "Signal électrique", "overlay", "Onde avec flèche de sens, à faire glisser le long du câble (#174).",
         prop("animation-assets/labo-signal/v1/labo-signal.svg", 160, 80, ["onde", "fleche"])),
    ]
    return {"schema": "lody.animation-assets/1", "assets": [
        {"id": asset_id, "name": name, "kind": kind, "description": description, "is_active": True,
         "character": None, "location": None,
         "versions": [{"version": 1, "manifest": manifest, "sha256": _manifest_sha(manifest),
                       "notes": "Version 1 (laboratoire #174)."}]}
        for asset_id, name, kind, description, manifest in entries]}


# -- dialogue ---------------------------------------------------------------------------------------------------
# (identifiant, locuteur, durée en s, texte oral (TTS-safe), texte affiché (titre, sous-titre), intention).
# Titre ≤ 16 caractères et sous-titre ≤ 28 : le moteur ne passe pas à la ligne (88 px et 54 px en 1080 de large).
DIALOGUE = [
    ("question", "sam", 6, "Nora, comment l'image de cette caméra arrive-t-elle sur l'écran, là-bas ?",
     ("Comment ?", "caméra → écran"), "Accrocher : poser la question du novice devant le matériel."),
    ("capter", "nora", 6, "Dans la caméra, un capteur transforme la lumière en signal électrique.",
     ("1. Capter", "lumière → signal"), "Étape un : la caméra convertit la lumière en signal électrique."),
    ("cable", "sam", 5, "Et ce signal file dans le câble jusqu'à l'écran ?",
     ("2. Transporter ?", None), "Faire apparaître le câble : le lien entre la caméra et l'écran."),
    ("transporter", "nora", 7, "Oui. Le câble ne transporte pas une photo : il transporte ce signal, image après "
     "image.", ("2. Transporter", "le signal voyage"), "Étape deux : le câble porte un signal, pas une image."),
    ("inverse", "sam", 6, "Et l'écran, alors, il fait le chemin inverse ?",
     ("3. Afficher ?", None), "Annoncer l'étape trois par la question de Sam."),
    ("afficher", "nora", 7, "Oui : le moniteur décode le signal et allume ses points lumineux pour recomposer "
     "l'image.", ("3. Afficher", "signal → image"), "Étape trois : l'écran recompose l'image à partir du signal."),
    ("recap", "sam", 6, "Donc la caméra capte, le câble transporte, et l'écran affiche !",
     ("En résumé", "capte → transporte → affiche"), "Récapituler la chaîne complète en une phrase."),
]
MOODS = {"question": "curieux", "recap": "enjoué"}

# Gestes et expressions (noms du rig #173) : (expressions [(image, nom)], gestes [(début, fin, nom)]).
ACTING = {
    "question": {"sam": ([(0, "confusion")], [(18, 42, "montrer"), (120, 126, "cligner")]),
                 "nora": ([(0, "neutre"), (110, "sourire")], [(60, 66, "cligner")])},
    "capter": {"nora": ([(0, "concentration")], [(14, 44, "expliquer"), (90, 114, "montrer")]),
               "sam": ([(0, "neutre")], [(96, 102, "cligner")])},
    "cable": {"sam": ([(0, "confusion")], [(12, 36, "montrer")]),
              "nora": ([(0, "sourire")], [(70, 90, "pencher-tete")])},
    "transporter": {"nora": ([(0, "concentration")], [(12, 42, "expliquer"), (110, 140, "expliquer")]),
                    "sam": ([(0, "neutre"), (150, "surprise")], [(60, 66, "cligner")])},
    "inverse": {"sam": ([(0, "confusion")], [(10, 30, "pencher-tete"), (70, 94, "montrer")]),
                "nora": ([(0, "sourire")], [(120, 126, "cligner")])},
    "afficher": {"nora": ([(0, "concentration")], [(16, 40, "montrer"), (100, 130, "expliquer")]),
                 "sam": ([(0, "surprise"), (140, "sourire")], [(80, 86, "cligner")])},
    "recap": {"sam": ([(0, "sourire")], [(10, 34, "montrer"), (80, 110, "expliquer")]),
              "nora": ([(0, "sourire")], [(130, 160, "saluer")])},
}

# Mise en page 1080 × 1920 : titre en haut, schéma sur une ligne, personnages en bas.
ROW_Y = 620
LAYOUT = {"sujet": (100, ROW_Y + 10), "camera": (330, ROW_Y), "cable": (600, ROW_Y + 40),
          "moniteur": (880, ROW_Y), "nora": (300, 1790), "sam": (790, 1790)}
LABELS = {"camera": "Caméra", "cable": "Câble", "moniteur": "Écran"}
SCENE_ORDER = [scene_id for scene_id, *_ in DIALOGUE]


def _at(frame: int, x: int, y: int) -> dict:
    return {"frame": frame, "x": x, "y": y}


def _asset(obj_id: str, asset_id: str, x: int, y: int, scale: float, start: int = 0, **extra) -> dict:
    obj = {"id": obj_id, "type": "asset", "asset": {"id": asset_id, "version": "latest"},
           "position": [_at(0, x, y)], "scale": [{"frame": 0, "value": scale}]}
    if start:
        obj["opacity"] = [{"frame": start, "value": 0}, {"frame": start + 12, "value": 1}]
    obj.update(extra)
    return obj


def _text(obj_id: str, content: str, style: str, x: int, y: int, start: int = 0) -> dict:
    obj = {"id": obj_id, "type": "text", "text": {"content": content, "style": style}, "position": [_at(0, x, y)]}
    if start:
        obj["opacity"] = [{"frame": start, "value": 0}, {"frame": start + 10, "value": 1}]
    return obj


def cues(text: str, frames: int, lead: int = 8, tail: int = 14, max_chars: int = 30) -> list[dict]:
    """Découpe la réplique en sous-titres (≤ ``max_chars``) répartis au prorata du nombre de caractères."""
    chunks, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = word
        else:
            current = candidate
    chunks.append(current)
    span, total = frames - lead - tail, sum(len(chunk) for chunk in chunks)
    out, start = [], lead
    for index, chunk in enumerate(chunks):
        end = frames - tail if index == len(chunks) - 1 else start + round(span * len(chunk) / total)
        out.append({"text": chunk, "start": start, "end": end})
        start = end
    return out


def _schema_objects(index: int, frames: int) -> list[dict]:
    """Schéma caméra → câble → écran, construit étape par étape (index de la scène dans le dialogue)."""
    (sx, sy), (cx, cy), (kx, ky), (mx, my) = (LAYOUT[k] for k in ("sujet", "camera", "cable", "moniteur"))
    objects = [_asset("sujet", SUJET, sx, sy, 0.8), _asset("camera", CAMERA, cx, cy, 0.95),
               _asset("moniteur", MONITEUR, mx, my, 0.95)]
    if index >= 2:  # le câble apparaît quand Sam en parle
        objects.insert(2, _asset("cable", CABLE, kx, ky, 0.6, start=20 if index == 2 else 0))
    if index == 1:  # la caméra émet un signal
        objects.append(_asset("signal-camera", SIGNAL, cx + 170, cy - 80, 0.6, start=60))
    if index in (3, 6):  # le signal parcourt le câble, en boucle visuelle de gauche à droite
        signal = _asset("signal-cable", SIGNAL, kx - 90, ky - 60, 0.6)
        signal["position"] = [_at(10, kx - 90, ky - 60), _at(frames - 20, kx + 90, ky - 60)]
        signal["easing"] = "ease_in_out"
        objects.append(signal)
    if index >= 5:  # l'image recomposée sur l'écran
        objects.append(_asset("image-ecran", SUJET, mx, my - 28, 0.55, start=40 if index == 5 else 0))
    labels = ["camera", "moniteur"] + (["cable"] if index >= 2 else [])
    objects += [_text(f"etiquette-{key}", LABELS[key], "caption", LAYOUT[key][0], ROW_Y + 180,
                      start=20 if key == "cable" and index == 2 else 0) for key in labels]
    return objects


def _puppet(key: str, scene_id: str, speaking: bool) -> dict:
    asset_id = NORA if key == "nora" else SAM
    expressions, gestures = ACTING[scene_id][key]
    return _asset(key, asset_id, *LAYOUT[key], 1.15,
                  expressions=[{"frame": frame, "name": name} for frame, name in expressions],
                  gestures=[{"start": start, "end": end, "name": name} for start, end, name in gestures],
                  lipsync=speaking)


def build_storyboard() -> dict:
    scenes = []
    for index, (scene_id, speaker, seconds, oral, shown, purpose) in enumerate(DIALOGUE):
        frames = seconds * FPS
        layers = [
            {"id": "schema", "objects": _schema_objects(index, frames)},
            {"id": "personnages", "objects": [_puppet("nora", scene_id, speaker == "nora"),
                                              _puppet("sam", scene_id, speaker == "sam")]},
            {"id": "texte-affiche", "objects": [_text("titre", shown[0], "title", WIDTH // 2, 170, start=6)]
             + ([_text("sous-titre", shown[1], "subtitle", WIDTH // 2, 300, start=12)] if shown[1] else [])},
        ]
        render = {"layers": layers, "speech": {"speaker": speaker, "cues": cues(oral, frames)},
                  "transition_in": {"type": "fade", "frames": 10} if index == 0 else {"type": "cut", "frames": 0},
                  "transition_out": ({"type": "fade", "frames": 12} if index == len(DIALOGUE) - 1
                                     else {"type": "cut", "frames": 0}),
                  "fallback": {"type": "still", "frame": frames // 2}}
        scenes.append({"id": scene_id, "duration_frames": frames,
                       "intent": {"narration": oral, "purpose": purpose, "mood": MOODS.get(scene_id, "pédagogique"),
                                  "notes": f"Réplique de {speaker.capitalize()}. Texte affiché : "
                                  + " / ".join(f"« {text} »" for text in shown if text) + "."},
                       "render": render})
    return {"schema": "lody.animation/1", "canvas": {"fps": FPS, "width": WIDTH, "height": HEIGHT}, "scenes": scenes}


def build_template() -> dict:
    return {
        "name": "Labo — De la caméra à l'écran",
        "description": "Projet expérimental du laboratoire (#174) : dialogue Nora / Sam, « Comment l'image d'une "
                       "caméra apparaît-elle sur un moniteur ? ». Voir docs/episodes/labo-camera-moniteur.md.",
        "payload": {
            "duration_min": 30,
            "duration_max": 45,
            "narration_pace": "normal",
            "scenes_per_minute_min": 8,
            "scenes_per_minute_max": 12,
            "structure": [
                "Question de Sam devant la caméra et l'écran",
                "Capter : la caméra transforme la lumière en signal électrique",
                "Le câble apparaît entre la caméra et l'écran",
                "Transporter : le câble porte un signal, pas une photo",
                "Question : l'écran fait-il le chemin inverse ?",
                "Afficher : le moniteur décode le signal et recompose l'image",
                "Récapitulatif : capter, transporter, afficher",
            ],
            "standing_instructions": "Projet de laboratoire, indépendant des épisodes publiés. Dialogue entre deux "
                                     "personnages fictifs adultes, Nora (explique) et Sam (pose les questions du "
                                     "novice), une réplique par scène. Une seule notion centrale : la caméra "
                                     "capte la lumière et la change en signal électrique, le câble transporte ce "
                                     "signal, l'écran le décode pour recomposer l'image. Aucun nom de norme, de "
                                     "connecteur ni de marque. Texte oral TTS-safe : aucun chiffre ni sigle.",
            "visual_rules": "Illustration vectorielle plate, contours brun foncé, 9:16. Schéma sur une ligne : "
                            "sujet, caméra, câble, écran, de gauche à droite ; le signal circule dans le même sens. "
                            "Texte affiché court et distinct du texte oral : étape numérotée en haut, étiquettes "
                            "sous le matériel. Nora à gauche, Sam à droite, sans changer leurs palettes. Une "
                            "action visuelle toutes les 5 à 7 secondes.",
            "visual_avoid": [
                "Personnes réelles ou collègues reconnaissables",
                "Logos ou marques de fabricants",
                "Noms de normes ou de connecteurs",
                "Texte en anglais à l'écran",
                "Nouveau personnage non prévu par le catalogue",
            ],
            "hook_notes": "Question de Sam dès la première seconde, en montrant l'écran éteint.",
            "subtitle_notes": "Sous-titres français en phrases entières, découpés par réplique ; le titre affiché "
                              "n'est jamais la copie du texte oral.",
            "music_notes": "Pas de musique imposée pour l'animatic ; musique libre de droits discrète en option.",
        },
    }


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def outputs() -> dict[Path, str]:
    """Chemin absolu → contenu attendu de chaque livrable."""
    svgs = {rel: build() for rel, build in ASSET_FILES.items()}
    out: dict[Path, str] = {RESOURCE / rel: content for rel, content in svgs.items()}
    out[RESOURCE / CATALOG_PATH] = _json(build_catalog(svgs))
    out[EPISODE / "storyboard.json"] = _json(build_storyboard())
    out[EPISODE / "template.json"] = _json(build_template())
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="vérifie sans écrire")
    args = parser.parse_args()
    stale = [path for path, content in outputs().items()
             if not path.exists() or path.read_text(encoding="utf-8") != content]
    if args.check:
        for path in stale:
            print(f"différent : {path.relative_to(ROOT)}")
        return 1 if stale else 0
    for path, content in outputs().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(f"{len(outputs())} fichiers écrits ({len(stale)} modifiés).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
