"""Épisode #137 — V3 « du HDMI familier au SDI » : script, storyboard 8 scènes (10 plans), échantillons voix.

Directive éditoriale V3 du propriétaire (#137, 11/11/2026), cadre d'exécution du 13/11/2026 :

- progression du connu vers l'inconnu : HDMI (box → téléviseur), DisplayPort (PC → moniteur), VGA d'autrefois
  (image analogique), puis SDI au studio ; pourquoi le SDI (distance avec un câble adapté, son avec l'image) ;
  coaxial comme l'antenne TV MAIS tous les coaxiaux ne conviennent pas ; liaison 75 ohms avec du matériel adapté
  (sans prétendre que 75 Ω distingue le SDI de l'antenne) ; teaser « dix caméras, vingt écrans » SANS matrice ;
- pas de régie, matrice, routage, 3G/12G, débits ni vidéo sur IP dans cet épisode ;
- voix finale ElevenLabs uniquement (voix enregistrée sur la fiche Voltix), jamais Piper : ce script ne
  synthétise RIEN. Il écrit des storyboards gelés que le propriétaire importe en DEV (Atelier 2D) :
  ``echantillons-v3.json`` (2 scènes = 2 appels ElevenLabs au plus, cadre du 13/11) puis, après accord,
  ``storyboard-v3.json`` (8 scènes) dont les 2 premières reprennent EXACTEMENT les textes des échantillons :
  le cache de voix (#164, empreinte voix + réglages + texte) les resservira sans nouvel appel.

Texte : le moteur envoie le texte des repères tel quel à ElevenLabs (le lexique #163 n'est pas fusionné) ;
le texte affiché, les sous-titres et le texte dit sont donc identiques, écrits pour être dits (« soixante-quinze
ohms » en toutes lettres ; le chiffre « 75 Ω » n'apparaît que sur les schémas).

Durées : sans voix réelle, les repères sont ESTIMÉS au débit ``--cps`` (caractères par seconde, prudent par
défaut pour qu'une voix un peu lente tienne dans sa scène : le moteur refuse une voix plus longue que sa scène).
Après l'écoute des échantillons, relancer avec le débit mesuré.

Usage :
    python scripts/animation/episode_voltix_137_v3.py --out docs/animation/voltix-137/v3 [--cps 13.5]
    python scripts/animation/episode_voltix_137_v3.py --out /tmp/v3 --render   # + animatique MUETTE (Remotion)
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT), str(Path(__file__).resolve().parent)]

import episode_voltix_137 as v1  # noqa: E402  (outils de storyboard, ids des schémas v1)
from lody.animation.contract import validate_document  # noqa: E402

FPS, WIDTH, HEIGHT = v1.FPS, v1.WIDTH, v1.HEIGHT
VOLTIX, BNC_M, BNC_F = v1.VOLTIX, v1.BNC_M, v1.BNC_F
V3_CATALOG = ROOT / "resource" / "animation-assets" / "sdi-v3-catalogue.json"
LEAD, GAP = 6, 8          # images avant la première réplique, entre deux répliques
DEFAULT_CPS = 15.0        # débit estimé d'une voix ElevenLabs FR naturelle : à remplacer par le débit MESURÉ
SAMPLE_CPS = 11.0         # échantillons : scènes larges, une voix lente y tient (le cache ignore la durée)
MIN_SECONDS, MAX_SECONDS = 55.0, 65.0
SAMPLE_SCENES = ("maison", "studio")  # les 2 échantillons autorisés : accroche HDMI → SDI
DEFAULT_CAP = 750         # plafond des 2 échantillons : pire cas (3 essais du moteur par appel) = 3 × 249

A = {**v1.A, **{key: f"ast_1370000001{n:02d}" for n, key in enumerate((
    "box-tv", "televiseur", "cable-plat", "fiche-hdmi", "pc", "moniteur-bureau", "fiche-dp", "fiche-vga",
    "plateau", "projecteur", "image", "cable-antenne", "antenne"))}}

# Scène → répliques (texte affiché = sous-titre = texte dit). Script V3 du propriétaire, découpé en 8 scènes.
LINES: dict[str, list[str]] = {
    "maison": ["Vous connaissez sûrement le câble HDMI qui relie votre box à votre télé.",
               "Ou le DisplayPort de votre écran d'ordinateur.",
               "Et avant, il y avait le VGA, pour l'image analogique."],
    "studio": ["Mais dans les studios télé, on utilise souvent une autre connexion : le SDI."],
    "distance": ["Pourquoi ?",
                 "D'abord, parce qu'on peut transporter l'image plus loin, avec un câble adapté."],
    "son": ["Et le SDI peut aussi transporter le son avec l'image, sans câble audio séparé."],
    "coaxial": ["Physiquement, il utilise souvent un câble coaxial, comme ceux des antennes de télé.",
                "Mais attention : tous les câbles coaxiaux ne conviennent pas !"],
    "ohms": ["Pour le SDI, on utilise une liaison à soixante-quinze ohms, avec du matériel adapté."],
    "conclusion": ["Voilà pourquoi on trouve tant de connexions SDI en vidéo professionnelle !"],
    "teaser": ["Mais maintenant, imaginez dix caméras et vingt écrans…",
               "Comment envoyer la bonne image au bon endroit ?",
               "Réponse dans la prochaine vidéo !"],
}
TAIL = {"maison": 10, "studio": 10, "distance": 12, "son": 10, "coaxial": 10, "ohms": 12, "conclusion": 14,
        "teaser": 24}
# Scène → (plans de la directive, intention, humeur, visuel concret)
PLAN = {
    "maison": ("1–2", "Du connu : HDMI, DisplayPort, VGA", "complice",
               "Salon : box → câble HDMI → téléviseur, l'image apparaît ; bureau : PC → DisplayPort → moniteur ; "
               "fiche VGA bleue, étiquetée « analogique »."),
    "studio": ("3", "Au studio : le SDI", "curieux",
               "Plateau TV clair (projecteur, caméra, voyant ON AIR) ; « SDI » apparaît sur le mot. Ni régie ni "
               "matrice."),
    "distance": ("4–5", "Pourquoi ? Plus loin, avec un câble adapté", "explicatif",
                 "Caméra → câble SDI → moniteur, l'image arrive ; trajet HDMI maison court, trajet SDI plus long, "
                 "« avec un câble adapté », aucun nombre de mètres."),
    "son": ("6", "Image et son dans le même câble", "léger",
            "Icônes image et son rejoignent le MÊME câble SDI ; « son intégré possible »."),
    "coaxial": ("7", "Coaxial, comme l'antenne… mais pas n'importe lequel", "prudent",
                "Antenne râteau + câble d'antenne blanc (fiche TV) au-dessus du câble SDI noir (fiches BNC) : même "
                "famille ; « pas les mêmes exigences »."),
    "ohms": ("8", "Une liaison 75 ohms, matériel adapté", "net",
             "Étiquette 75 Ω, puis câble, connecteurs et appareils adaptés, « de bout en bout ». Aucune "
             "comparaison d'impédance avec l'antenne."),
    "conclusion": ("9", "Voilà pourquoi le SDI en vidéo pro", "victorieux",
                   "La caméra envoie son image au moniteur de plateau ; Voltix rebondit."),
    "teaser": ("10", "Teaser : 10 caméras, 20 écrans", "intrigué",
               "10 caméras puis 20 écrans apparaissent, gros « ? », « Prochain épisode ! ». Aucune matrice."),
}

k, cam, text, g, pop, fade_in, packets, L = v1.k, v1.cam, v1.text, v1.g, v1.pop, v1.fade_in, v1.packets, v1.L


def asset(obj_id: str, key: str, position: list, **extra) -> dict:
    return v1.base.asset(obj_id, A.get(key, key), position, **extra)


def voltix(x: int, scale: float, expressions: list, gestures: list, y: int = 1500) -> dict:
    return asset("voltix", VOLTIX, [k(0, x=x, y=y)], scale=[k(0, value=scale)], expressions=expressions,
                 gestures=[gg for gg in gestures if gg["end"] > gg["start"]], lipsync=True)


def scene(scene_id: str, s: dict, layers: list, camera: list, fallback: int, **extra) -> dict:
    _, purpose, mood, _ = PLAN[scene_id]
    render = {"layers": [layer for layer in layers if layer["objects"]], "camera": {"keyframes": camera},
              "speech": {"speaker": "voltix", "cues": s["cues"]},
              "fallback": {"type": "still", "frame": max(0, min(fallback, s["duration"] - 1))}, **extra}
    return {"id": scene_id, "duration_frames": s["duration"],
            "intent": {"narration": " ".join(LINES[scene_id]), "purpose": purpose, "mood": mood},
            "render": render}


def at(cue: dict, percent: int) -> int:
    """Image située à ``percent`` % d'un repère (synchronisation au mot, estimée)."""
    return cue["start"] + (cue["end"] - cue["start"]) * percent // 100


def screen(obj_id: str, key: str, x: int, y: int, scale: float, start: int, end: int | None = None) -> dict:
    return asset(obj_id, "image", [k(0, x=x, y=y)], scale=[k(0, value=round(scale, 3))], **fade_in(start, 8, end))


# -- les 8 scènes ------------------------------------------------------------------------------------------------
def scene_maison(s: dict) -> dict:
    d, (c1, c2, c3) = s["duration"], s["phrases"]
    salon_end, plugged = c2["start"], at(c1, 45)
    salon = [asset("box", "box-tv", [k(0, x=250, y=880)], scale=[k(0, value=0.85)], end=salon_end),
             asset("tv", "televiseur", [k(0, x=700, y=640)], scale=[k(0, value=1.0)], end=salon_end),
             # Fiches posées sur les prises : sortie HDMI de la box (308, 871) → entrée du téléviseur (933, 748).
             asset("hdmi", "cable-plat", [k(0, x=620, y=810)], scale=[k(0, value=1.06)], rotation=[k(0, value=-11)],
                   end=salon_end, **fade_in(at(c1, 20), 8)),
             screen("image-tv", "image", 700, 610, 480 / 316, plugged + 4, salon_end)]
    bureau = [asset("pc", "pc", [k(0, x=200, y=760)], scale=[k(0, value=0.9)], **fade_in(c2["start"], 8)),
              asset("moniteur", "moniteur-bureau", [k(0, x=700, y=640)], **fade_in(c2["start"], 8)),
              asset("dp", "cable-plat", [k(0, x=360, y=726)], scale=[k(0, value=0.53)], rotation=[k(0, value=-2)],
                    **fade_in(c2["start"] + 6)),  # sortie DP du PC (200, 732) → arrière du moniteur
              screen("image-moniteur", "image", 700, 614, 408 / 316, at(c2, 50))]
    fiches = [asset("f-hdmi", "fiche-hdmi", [k(0, x=250, y=360)], end=salon_end, **pop(at(c1, 30), 0.9)),
              asset("f-dp", "fiche-dp", [k(0, x=250, y=360)], end=c3["start"], **pop(at(c2, 30), 0.9)),
              asset("f-vga", "fiche-vga", [k(0, x=540, y=330)], **pop(c3["start"] + 4, 1.1))]
    texts = [text("t-hdmi", "HDMI", "title", [k(0, x=620, y=300)], end=salon_end, **pop(at(c1, 30))),
             text("t-dp", "DisplayPort", "title", [k(0, x=640, y=300)], end=c3["start"], **pop(at(c2, 30))),
             text("t-vga", "VGA", "title", [k(0, x=540, y=180)], **pop(c3["start"] + 4)),
             text("t-analogique", "image analogique", "label", [k(0, x=540, y=1000)], **fade_in(at(c3, 60)))]
    puppet = voltix(880, 0.52, [k(0, name="sourire"), k(c3["start"], name="concentration"),
                                k(c3["end"], name="sourire")],
                    [g("saluer", 0, 26), g("montrer", at(c1, 30), c1["end"]), g("montrer", at(c2, 20), c2["end"]),
                     g("montrer", c3["start"], min(d, c3["end"]))])
    return scene("maison", s, [L("salon", salon), L("bureau", bureau), L("fiches", fiches), L("textes", texts),
                               L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.04), cam(c1["end"], 540, 960, 1.0), cam(d, 540, 940, 1.03)], at(c1, 80),
                 transition_in={"type": "fade", "frames": 8})


def scene_studio(s: dict) -> dict:
    d, (c1,) = s["duration"], s["phrases"]
    sdi = at(c1, 85)
    decor = [asset("plateau", "plateau", [k(0, x=540, y=720)]),
             asset("projecteur", "projecteur", [k(0, x=140, y=560)], scale=[k(0, value=0.8)]),
             asset("camera", "camera", [k(0, x=1400, y=900), k(at(c1, 30), x=760, y=900)], scale=[k(0, value=0.9)],
                   easing="ease_out")]
    texts = [text("sdi", "SDI", "title", [k(0, x=540, y=250)], **pop(sdi, 1.2)),
             text("studio", "studio de télévision", "label", [k(0, x=540, y=1150)], **fade_in(at(c1, 15)))]
    puppet = voltix(880, 0.5, [k(0, name="surprise"), k(sdi, name="sourire")],
                    [g("montrer", at(c1, 20), at(c1, 70)), g("rebond", sdi, min(d, sdi + 28))])
    return scene("studio", s, [L("plateau", decor), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 900, 1.08), cam(d, 540, 960, 1.0)], sdi + 10,
                 transition_in={"type": "fade", "frames": 10})


def scene_distance(s: dict) -> dict:
    d, (c1, c2) = s["duration"], s["phrases"]
    arrive = c2["start"] + 4
    chain = [asset("camera", "camera", [k(0, x=190, y=480)], scale=[k(0, value=0.7)]),
             asset("cable", "cable", [k(0, x=540, y=580)], scale=[k(0, value=0.48)]),
             asset("moniteur", "moniteur", [k(0, x=880, y=480)], scale=[k(0, value=0.7)]),
             screen("image", "image", 880, 463, 0.7, arrive)]
    flow = packets("paquet", 360, 720, 580, c1["start"], arrive + 4, every=10, travel=18, scale=0.6)
    long_at = at(c2, 45)
    compare = [asset("court", "cable-plat", [k(0, x=330, y=820)], scale=[k(0, value=0.55)], **fade_in(at(c2, 15))),
               asset("long", "cable", [k(0, x=540, y=990)], scale=[k(long_at, value=0.3), k(long_at + 18, value=1.25)],
                     start=long_at, easing="ease_out")]
    texts = [text("pourquoi", "Pourquoi ?", "title", [k(0, x=540, y=260)], end=c2["start"] + 6, **pop(c1["start"])),
             text("maison", "HDMI à la maison", "label", [k(0, x=330, y=740)], **fade_in(at(c2, 15))),
             text("plus-loin", "SDI : plus loin…", "label", [k(0, x=540, y=910)], **fade_in(long_at + 6)),
             text("adapte", "… avec un câble adapté", "subtitle", [k(0, x=540, y=260)], **fade_in(at(c2, 80)))]
    puppet = voltix(880, 0.5, [k(0, name="confusion"), k(c2["start"], name="concentration")],
                    [g("montrer", c2["start"], min(d, c2["end"]))])
    return scene("distance", s, [L("chaine", chain), L("flux", flow), L("comparaison", compare), L("textes", texts),
                                 L("personnages", [puppet])],
                 [cam(0, 540, 760, 1.08), cam(c2["start"], 540, 900, 1.0), cam(d, 540, 960, 1.0)], at(c2, 90),
                 transition_in={"type": "slide_left", "frames": 10})


def scene_son(s: dict) -> dict:
    d, (c1,) = s["duration"], s["phrases"]
    join = at(c1, 45)
    objects = [asset("cable", "cable", [k(0, x=540, y=720)]),
               asset("video", "video", [k(0, x=260, y=470), k(join - 12, x=260, y=470), k(join + 8, x=470, y=720)],
                     scale=[k(join - 12, value=1.0), k(join + 8, value=0.45)], easing="ease_in_out",
                     **fade_in(c1["start"])),
               asset("audio", "audio", [k(0, x=820, y=470), k(join - 4, x=820, y=470), k(join + 16, x=610, y=720)],
                     scale=[k(join - 4, value=1.0), k(join + 16, value=0.45)], easing="ease_in_out",
                     **fade_in(at(c1, 25)))]
    flow = packets("paquet", 220, 860, 720, join + 18, d, every=12)
    texts = [text("meme", "Image + son, même câble", "subtitle", [k(0, x=540, y=260)], **fade_in(at(c1, 20))),
             text("possible", "son intégré possible", "label", [k(0, x=540, y=880)], **fade_in(join + 18)),
             text("sans", "✗ câble audio séparé", "label", [k(0, x=540, y=980)], **fade_in(at(c1, 85)))]
    puppet = voltix(880, 0.5, [k(0, name="sourire")], [g("montrer", c1["start"], min(d, c1["end"]))])
    return scene("son", s, [L("canal", objects), L("flux", flow), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0)], join + 24, transition_in={"type": "slide_left", "frames": 10})


def scene_coaxial(s: dict) -> dict:
    d, (c1, c2) = s["duration"], s["phrases"]
    antenne_at, warn = at(c1, 55), c2["start"]
    objects = [asset("sdi", "cable", [k(0, x=540, y=900)], scale=[k(0, value=0.85)], **fade_in(c1["start"] + 4)),
               asset("antenne", "antenne", [k(0, x=200, y=470)], **pop(antenne_at, 0.8)),
               asset("cable-antenne", "cable-antenne", [k(0, x=560, y=640)], scale=[k(0, value=0.8)],
                     **fade_in(antenne_at + 6))]
    texts = [text("l-sdi", "câble SDI (vidéo pro)", "label", [k(0, x=540, y=990)], **fade_in(c1["start"] + 10)),
             text("l-antenne", "câble d'antenne TV", "label", [k(0, x=620, y=560)], **fade_in(antenne_at + 6)),
             text("famille", "Même famille : le coaxial", "subtitle", [k(0, x=540, y=260)], end=warn,
                  **fade_in(at(c1, 70))),
             text("attention", "Pas n'importe lequel !", "subtitle", [k(0, x=540, y=260)], **pop(warn)),
             text("exigences", "pas les mêmes exigences", "label", [k(0, x=540, y=760)], **fade_in(at(c2, 50)))]
    puppet = voltix(880, 0.5, [k(0, name="neutre"), k(warn, name="confusion"), k(at(c2, 70), name="concentration")],
                    [g("montrer", c1["start"], c1["end"]), g("cligner", warn, warn + 10)])
    return scene("coaxial", s, [L("cables", objects), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(warn, 540, 960, 1.0), cam(d, 540, 900, 1.05)], at(c2, 80),
                 transition_in={"type": "fade", "frames": 8})


def scene_ohms(s: dict) -> dict:
    d, (c1,) = s["duration"], s["phrases"]
    tag, adapted = at(c1, 30), at(c1, 70)
    objects = [asset("tag-75", "75", [k(0, x=540, y=520)], **pop(tag, 1.5)),
               asset("cable", "cable", [k(0, x=540, y=800)], scale=[k(0, value=0.85)], **fade_in(c1["start"])),
               asset("bnc-male", BNC_M, [k(0, x=330, y=1010)], scale=[k(0, value=0.7)], **fade_in(adapted)),
               asset("bnc-femelle", BNC_F, [k(0, x=620, y=1010)], scale=[k(0, value=0.7)], **fade_in(adapted + 4))]
    texts = [text("liaison", "Liaison 75 Ω de bout en bout", "subtitle", [k(0, x=540, y=260)], **fade_in(tag + 6)),
             text("materiel", "matériel adapté", "label", [k(0, x=420, y=1150)],
                  **fade_in(adapted + 6))]
    puppet = voltix(880, 0.5, [k(0, name="concentration"), k(adapted, name="sourire")],
                    [g("montrer", tag, min(d, c1["end"]))])
    return scene("ohms", s, [L("liaison", objects), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(tag + 12, 540, 900, 1.05), cam(d, 540, 960, 1.0)], adapted + 10,
                 transition_in={"type": "slide_left", "frames": 10})


def scene_conclusion(s: dict) -> dict:
    d, (c1,) = s["duration"], s["phrases"]
    image = at(c1, 45)
    chain = [asset("plateau", "plateau", [k(0, x=540, y=720)], opacity=[k(0, value=0.45)]),
             asset("camera", "camera", [k(0, x=190, y=560)], scale=[k(0, value=0.75)]),
             asset("cable", "cable", [k(0, x=540, y=660)], scale=[k(0, value=0.5)]),
             asset("moniteur", "moniteur", [k(0, x=880, y=560)], scale=[k(0, value=0.75)]),
             screen("image", "image", 880, 541, 0.75, image)]
    flow = packets("paquet", 370, 710, 660, c1["start"], image + 8, every=10, travel=18, scale=0.7)
    texts = [text("pro", "SDI : la connexion vidéo pro", "subtitle", [k(0, x=540, y=260)],
                  **pop(at(c1, 60)))]
    puppet = voltix(540, 0.62, [k(0, name="sourire"), k(image, name="surprise"), k(image + 10, name="sourire")],
                    [g("montrer", c1["start"], image), g("rebond", image, min(d, image + 28))])
    return scene("conclusion", s, [L("chaine", chain), L("flux", flow), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(d, 540, 940, 1.04)], image + 20,
                 transition_in={"type": "fade", "frames": 8})


def scene_teaser(s: dict) -> dict:
    d, (c1, c2, c3) = s["duration"], s["phrases"]
    cams_at, screens_at = at(c1, 20), at(c1, 60)
    cameras = [asset(f"cam-{i}", "camera", [k(0, x=140 + (i % 5) * 200, y=330 + (i // 5) * 150)],
                     **pop(cams_at + i * 2, 0.28)) for i in range(10)]
    screens = [asset(f"ecran-{i}", "moniteur", [k(0, x=140 + (i % 5) * 200, y=720 + (i // 5) * 110)],
                     **pop(screens_at + i, 0.22)) for i in range(20)]
    texts = [text("dix", "10 caméras", "label", [k(0, x=540, y=200)], **fade_in(cams_at, end=c3["start"])),
             text("vingt", "20 écrans", "label", [k(0, x=540, y=600)], **fade_in(screens_at, end=c2["start"])),
             text("question", "?", "title", [k(0, x=540, y=600)], **pop(c2["start"], 1.6)),
             text("bon-ecran", "La bonne image au bon endroit ?", "subtitle", [k(0, x=540, y=1150)],
                  **fade_in(at(c2, 30))),
             text("suite", "Prochain épisode !", "title", [k(0, x=540, y=200)], **pop(c3["start"]))]
    puppet = voltix(880, 0.5, [k(0, name="surprise"), k(c2["start"], name="confusion"), k(c3["start"], name="sourire")],
                    [g("montrer", c1["start"], c1["end"]), g("saluer", at(c3, 50), min(d, at(c3, 50) + 40))])
    return scene("teaser", s, [L("cameras", cameras), L("ecrans", screens), L("textes", texts),
                               L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(d, 540, 960, 1.04)], c2["start"] + 20,
                 transition_in={"type": "fade", "frames": 8}, transition_out={"type": "fade", "frames": 12})


BUILDERS = {"maison": scene_maison, "studio": scene_studio, "distance": scene_distance, "son": scene_son,
            "coaxial": scene_coaxial, "ohms": scene_ohms, "conclusion": scene_conclusion, "teaser": scene_teaser}


# -- mise en page estimée, storyboard, coût ----------------------------------------------------------------------
MAX_CUE = 60  # longueur maximale d'un repère (contrat lody.animation/1) : une phrase longue en fait deux


def split_sentence(sentence: str) -> list[str]:
    """Phrase → repères de 60 caractères au plus, coupés après une virgule ou un deux-points si possible,
    sinon à l'espace la plus proche du milieu. Les repères rejoints par une espace redonnent la phrase : le
    texte envoyé à la voix (``voiceover`` joint les repères) est inchangé."""
    if len(sentence) <= MAX_CUE:
        return [sentence]
    middle = len(sentence) / 2
    spaces = [i for i, ch in enumerate(sentence) if ch == " " and i <= MAX_CUE and len(sentence) - i - 1 <= MAX_CUE]
    punctuated = [i for i in spaces if sentence[i - 1] in ",:"]
    cut = min(punctuated or spaces, key=lambda i: abs(i - middle))
    return [sentence[:cut], *split_sentence(sentence[cut + 1:])]


def estimated_layout(cps: float = DEFAULT_CPS) -> dict[str, dict]:
    """Repères de parole ESTIMÉS (caractères / débit) ; la voix ElevenLabs réelle les recale au rendu (#164).
    ``phrases`` : début et fin de chaque phrase du script (les animations se calent sur les phrases)."""
    scenes, offset = {}, 0
    for scene_id, lines in LINES.items():
        cues, phrases, frame = [], [], LEAD
        for line in lines:
            first = len(cues)
            for piece in split_sentence(line):
                frames = max(18, round((len(piece) + 1) / cps * FPS))
                cues.append({"text": piece, "start": frame, "end": frame + frames})
                frame += frames + 2
            frame += GAP - 2
            phrases.append({"start": cues[first]["start"], "end": cues[-1]["end"]})
        duration = frame - GAP + TAIL[scene_id]
        scenes[scene_id] = {"duration": duration, "cues": cues, "phrases": phrases, "offset": offset}
        offset += duration
    return scenes


def build_storyboard(layout: dict[str, dict], only: tuple[str, ...] | None = None) -> dict:
    ids = [sid for sid in LINES if only is None or sid in only]
    document = {"schema": "lody.animation/1", "canvas": {"fps": FPS, "width": WIDTH, "height": HEIGHT},
                "scenes": [BUILDERS[sid](layout[sid]) for sid in ids]}
    return validate_document(document)


def characters(scene_ids=None) -> int:
    """Caractères envoyés à ElevenLabs (texte des repères joints par une espace, comme ``voiceover``)."""
    return sum(len(" ".join(LINES[sid])) for sid in (scene_ids or LINES))


def setup_project(db: Path):
    """Projet temporaire Animation 2D, SANS fiche personnage (aucune voix) ; catalogues v1 + V3 importés."""
    from lody.animation import modes
    from lody.animation.assets import AnimationAssetRepository
    from lody.projects import ProjectRepository

    project = ProjectRepository(db).create(name="Voltix — épisode SDI V3 (#137)",
                                           settings={modes.SETTINGS_KEY: modes.ANIMATION})
    assets, id_map, warnings = AnimationAssetRepository(db), {}, []
    for path in (v1.VOLTIX_CATALOG, v1.SDI_CATALOG, V3_CATALOG):
        mapping, notes = assets.import_catalog(project.id, json.loads(path.read_text(encoding="utf-8")))
        id_map.update(mapping)
        warnings += notes
    return project, assets, id_map, warnings


def freeze(document: dict, db: Path) -> dict:
    from lody.animation.freeze import freeze_document, remap_assets

    project, assets, id_map, _ = setup_project(db)
    return freeze_document(assets, project.id, remap_assets(document, id_map))


def script_table(layout: dict[str, dict]) -> str:
    rows = ["| # | Scène | Plans | Début | Durée | Texte dit et affiché | Visuel |", "|---|---|---|---|---|---|---|"]
    for n, (sid, s) in enumerate(layout.items(), 1):
        plans, purpose, _, visual = PLAN[sid]
        rows.append(f"| {n} | {purpose} | {plans} | {s['offset'] / FPS:.1f} s | {s['duration'] / FPS:.1f} s | "
                    + " ".join(f"« {line} »" for line in LINES[sid]) + f" | {visual} |")
    return "\n".join(rows)


def write_outputs(out: Path, cps: float) -> dict:
    from lody.animation import recette

    out.mkdir(parents=True, exist_ok=True)
    layout = estimated_layout(cps)
    with tempfile.TemporaryDirectory(prefix="lody-137-v3-") as tmp:
        full = freeze(build_storyboard(layout), Path(tmp) / "a.sqlite3")
        samples = freeze(build_storyboard(estimated_layout(SAMPLE_CPS), SAMPLE_SCENES), Path(tmp) / "b.sqlite3")
    for name, document in (("storyboard-v3.json", full), ("echantillons-v3.json", samples)):
        (out / name).write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    total = sum(s["duration"] for s in layout.values()) / FPS
    summary = {
        "cps": cps, "duration_seconds": round(total, 2),
        "scenes": {sid: {"offset": s["offset"], "duration": s["duration"], "cues": s["cues"]}
                   for sid, s in layout.items()},
        "samples": recette.estimate([samples], DEFAULT_CAP),
        "full_after_samples": {"characters_total": characters(),
                               "characters_samples_reused": characters(SAMPLE_SCENES),
                               "characters_to_pay": characters() - characters(SAMPLE_SCENES),
                               "calls": len(LINES) - len(SAMPLE_SCENES)},
    }
    (out / "episode-v3.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "script-v3.md").write_text(script_table(layout) + "\n", encoding="utf-8")
    return summary


def render_animatic(out: Path, renderer: str) -> dict:
    """Animatique MUETTE (aucune voix, aucune musique) rendue par la vraie chaîne #120 / Remotion : contrôle
    visuel gratuit du découpage avant toute dépense. Ce n'est PAS le rendu final."""
    from lody.animation import modes
    from lody.animation import worker as w
    from lody.animation.freeze import freeze_document, remap_assets
    from lody.generation.animation_connector import AnimationConnector
    from lody.generation.service import build_request

    work = Path(tempfile.mkdtemp(prefix="lody-137-v3-render-"))
    data_dir = work / "data"
    shutil.copytree(ROOT / "resource" / "animation-assets", data_dir / "animation-assets", dirs_exist_ok=True)
    project, assets, id_map, _ = setup_project(work / "lody.sqlite3")
    storyboard = freeze_document(assets, project.id, remap_assets(build_storyboard(estimated_layout()), id_map))
    connector = AnimationConnector(root=data_dir, asset_repo=assets, renderer=renderer,
                                   launcher=lambda d: w.run_job(d, w.CommandRenderer(shlex.split(renderer))))
    request = build_request(project, "Voltix — du HDMI au SDI (animatique V3)").with_updates(
        render_mode=modes.ANIMATION, animation={"project_id": project.id, "storyboard": storyboard})
    task = connector.submit(request, "episode-137-v3-animatique")
    directory = w.find_job_dir(data_dir, task.task_id)
    state = w.read_json(directory / "state.json") or {}
    if state.get("state") != "done":
        raise SystemExit(f"rendu en échec : {state.get('error')}")
    shutil.copyfile(directory / "out.mp4", out / "animatique-v3-muette.mp4")
    return {"job_dir": str(directory)}


def captures(out: Path, layout: dict[str, dict]) -> list[Path]:
    """Une capture par scène (au milieu de sa dernière réplique) depuis l'animatique."""
    shots = []
    for n, (sid, s) in enumerate(layout.items(), 1):
        last = s["cues"][-1]
        second = (s["offset"] + (last["start"] + last["end"]) // 2) / FPS
        target = out / f"capture-{n}-{sid}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{second:.2f}", "-i",
                        str(out / "animatique-v3-muette.mp4"), "-frames:v", "1", "-vf", "scale=360:-1", str(target)],
                       check=True)
        shots.append(target)
    return shots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cps", type=float, default=DEFAULT_CPS, help="débit de la voix (caractères/seconde)")
    parser.add_argument("--render", action="store_true", help="rendre aussi l'animatique muette et les captures")
    parser.add_argument("--renderer", default=f"node {ROOT / 'renderers/remotion/bin/lody-remotion.mjs'}")
    args = parser.parse_args()
    summary = write_outputs(args.out, args.cps)
    if not MIN_SECONDS <= summary["duration_seconds"] <= MAX_SECONDS:
        raise SystemExit(f"durée estimée {summary['duration_seconds']} s hors de {MIN_SECONDS}–{MAX_SECONDS} s")
    if args.render:
        render_animatic(args.out, args.renderer)
        captures(args.out, estimated_layout(args.cps))
    print(json.dumps({key: summary[key] for key in ("duration_seconds", "samples", "full_after_samples")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
