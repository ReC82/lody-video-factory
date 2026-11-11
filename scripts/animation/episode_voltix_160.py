"""Épisode #137 « Voltix explique le câble SDI » — V2 éditoriale (#160) : script, storyboard 9:16 et rendu.

Réécriture après visionnage de la V1 par le propriétaire (dynamisme, voix mécanique, exactitude). La V1
(``episode_voltix_137.py``, ``docs/animation/voltix-137``) reste INCHANGÉE : cette V2 est réversible.

Un seul concept central : « le SDI, c'est quoi et pourquoi en vidéo professionnelle ? ». Fil :
HDMI de la maison → définition (Serial Digital Interface) → broadcast → chaîne indicative d'équipements →
audio intégré possible → signal / coaxial / BNC → 75 Ω → HD / 3G / 12G → récapitulatif connecté.

Trois textes séparés : AFFICHÉ (sous-titres), PARLÉ (envoyé à la synthèse, sans sigle ni chiffre) et la table
``PRONUNCIATIONS`` (sigle → forme parlée → phonèmes attendus du phonémiseur Piper/espeak). Les prononciations
se contrôlent AVANT tout rendu : ``--check-pronunciations``.

Même chaîne réelle que #137 / #150 (DEV isolé, base SQLite et données temporaires) : catalogues Voltix (#136),
SDI V1 (#137) et SDI V2 (#160) → storyboard lody.animation/1 validé, gelé (#119) → AnimationConnector (#120)
→ worker → Remotion (#150) → mixage. Voix PROVISOIRE Piper, musique de test V2 générée : aucun appel payant.

Usage :
    python scripts/animation/episode_voltix_160.py --check-pronunciations --piper-model /chemin/fr_FR-upmc-medium.onnx
    python scripts/animation/episode_voltix_160.py --storyboard-only --out /tmp/sb
    python scripts/animation/episode_voltix_160.py --out /tmp/voltix-137-v2 --piper-model /chemin/fr_FR-upmc-medium.onnx
"""

from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT), str(Path(__file__).resolve().parent)]

import demo_voltix_150 as base  # noqa: E402
import episode_voltix_137 as v1  # noqa: E402
from lody.animation.contract import validate_document  # noqa: E402

FPS, WIDTH, HEIGHT = v1.FPS, v1.WIDTH, v1.HEIGHT
VOLTIX, BNC_M, BNC_F = base.VOLTIX, base.BNC_M, base.BNC_F
SDI_V2_CATALOG = ROOT / "resource" / "animation-assets" / "sdi-v2-catalogue.json"
LEAD, GAP = 4, 3  # images avant la première réplique d'une scène, entre deux répliques (V1 : 10 et 6)
LENGTH_SCALE = 0.84  # débit Piper plus vif que la V1 (0,92)

# Schémas : V1 (#137, réutilisés tels quels) + V2 (#160, sdi-v2-catalogue.json).
A = {**v1.A,
     "salon": "ast_160000000001", "bits": "ast_160000000002", "plateau": "ast_160000000010",
     "car": "ast_160000000011", "direct": "ast_160000000012", "enregistreur": "ast_160000000020",
     "grille": "ast_160000000021", "convertisseur": "ast_160000000022", "fleche": "ast_160000000023",
     "coax-cotes": "ast_160000000030", "liaison": "ast_160000000040"}

# Sigle ou terme affiché → (forme parlée envoyée à Piper, phonèmes attendus du phonémiseur espeak de Piper).
# Les phonèmes ont été relevés le 11/11/2026 avec fr_FR-upmc-medium ; « --check-pronunciations » les revérifie.
PRONUNCIATIONS: dict[str, tuple[str, str]] = {
    "SDI": ("èss di aï", "ˈɛs dˈiʲ ˈaj"),
    "Serial Digital Interface": ("sirieul didjiteul innteurfeïss", "siʁjˈœl didʒitˈœl intœʁfˈɛjs"),
    "HDMI": ("hache dé èm i", "ˈaʃ dˈe ˈɛm ˈi"),
    "BNC": ("bé ène cé", "bˈe ˈɛn sˈe"),
    "HD-SDI": ("hache dé èss di aï", "ˈaʃ dˈe ˈɛs dˈiʲ ˈaj"),
    "3G-SDI": ("trois gé èss di aï", "tʁwˈa ʒˈe ˈɛs dˈiʲ ˈaj"),
    "12G-SDI": ("douze gé èss di aï", "dˈuz ʒˈe ˈɛs dˈiʲ ˈaj"),
    "broadcast": ("brodcaste", "bʁɔdkˈast"),
    "75 Ω": ("soixante-quinze ohms", "swasˈɑ̃tkˈɛ̃z ˈɔm"),
}
# Formes REFUSÉES (relevées sur la V1 ou au contrôle) : « SDI » brut → /zdi/, « HDMI » brut → /dmi/,
# « broadcast » brut → anglais /bɹɔːdkast/ ; « èsse dé i » (V1) = épellation française refusée par le propriétaire ;
# les virgules entre « HD/3G/12G » et « SDI » (V1) créaient des pauses : groupes liés, sans ponctuation.
REJECTED_SPOKEN = ("èsse dé i", "SDI", "HDMI", "BNC", "broadcast", "G, ")


def p(term: str) -> str:
    """Forme parlée d'un sigle ou d'un terme."""
    return PRONUNCIATIONS[term][0]


# Scène → répliques (texte AFFICHÉ, texte PARLÉ). Phrases courtes, une idée par réplique.
LINES: dict[str, list[tuple[str, str]]] = {
    "accroche": [
        ("Ta box et ta télé ? Reliées en HDMI.", f"Ta box et ta télé ? Reliées en {p('HDMI')}."),
        ("Et chez les pros de la vidéo ?", "Et chez les pros de la vidéo ?")],
    "definition": [
        ("Du SDI : Serial Digital Interface.", f"Du {p('SDI')} : {p('Serial Digital Interface')}."),
        ("Interface numérique série :", "Interface numérique série :"),
        ("des bits, un par un.", "des bits, un par un.")],
    "broadcast": [
        ("Le standard du broadcast :", f"Le standard du {p('broadcast')} :"),
        ("production et diffusion pro.", "production et diffusion pro !"),
        ("Comme le HDMI : de la vidéo numérique…", f"Comme le {p('HDMI')} : de la vidéo numérique,"),
        ("… mais câbles longs et prises verrouillées.", "mais câbles longs, et prises verrouillées.")],
    "chaine": [
        ("Caméra, grille de routage, moniteur, enregistreur…",
         "Caméra, grille de routage, moniteur, enregistreur,"),
        ("… mélangeur, qui choisit les sources…", "mélangeur, qui choisit les sources,"),
        ("… ou convertisseur, pour finir en HDMI.", f"ou convertisseur, pour finir en {p('HDMI')}.")],
    "audio": [
        ("Ce même signal peut aussi intégrer le son.", "Ce même signal peut aussi intégrer le son.")],
    "couches": [
        ("Le SDI, c'est le signal.", f"Le {p('SDI')}, c'est le signal."),
        ("Ici, il passe dans un coaxial…", "Ici, il passe dans un coaxial,"),
        ("… branché en BNC, verrouillé d'un quart de tour.",
         f"branché en {p('BNC')}, verrouillé d'un quart de tour.")],
    "ohms": [
        ("Pas n'importe quel coaxial : du 75 ohms.", f"Pas n'importe quel coaxial : du {p('75 Ω')}."),
        ("Pas une résistance : son impédance caractéristique,",
         "Pas une résistance : son impédance caractéristique,"),
        ("fixée par ses diamètres et son isolant.", "fixée par ses diamètres et son isolant.")],
    "debits": [
        ("HD-SDI, 3G-SDI, 12G-SDI :", f"{p('HD-SDI')}, {p('3G-SDI')}, {p('12G-SDI')} :"),
        ("des débits, pas des résolutions.", "des débits, pas des résolutions."),
        ("Plus ça monte, plus câble et connecteurs doivent suivre.",
         "Plus ça monte, plus câble et connecteurs doivent suivre.")],
    "conclusion": [
        ("Bref : le SDI, le signal de la vidéo pro.", f"Bref : le {p('SDI')}, le signal de la vidéo pro."),
        ("Dans un coaxial 75 ohms, branché en BNC…", f"Dans un coaxial {p('75 Ω')}, branché en {p('BNC')},"),
        ("… et l'image arrive !", "et l'image arrive !")],
}
# Fins de scène (images) : courtes, la scène suivante enchaîne (V1 : 8 à 32).
TAIL = {"accroche": 4, "definition": 6, "broadcast": 6, "chaine": 6, "audio": 8, "couches": 6, "ohms": 6,
        "debits": 6, "conclusion": 40}
MIN_TAILS = {"conclusion": 36}  # la mire et le salut restent visibles avant le fondu final (12 images)
PURPOSE = {
    "accroche": ("Accroche : HDMI à la maison, et chez les pros ?", "curieux"),
    "definition": ("SDI = Serial Digital Interface", "clair"),
    "broadcast": ("Broadcast : production et diffusion pro ; pont avec le HDMI", "enjoué"),
    "chaine": ("Chaîne indicative d'équipements", "rythmé"),
    "audio": ("Audio intégré possible", "léger"),
    "couches": ("Signal SDI / câble coaxial / connecteur BNC", "net"),
    "ohms": ("75 Ω = impédance caractéristique", "pédagogique"),
    "debits": ("HD / 3G / 12G = débits", "rythmé"),
    "conclusion": ("Récapitulatif sur une liaison réellement branchée", "victorieux"),
}
MIN_SECONDS, MAX_SECONDS, TARGET_SECONDS = 55.0, 65.0, 64.5
WORDS_PER_SECOND = 3.3  # estimation sans voix (Piper, length-scale 0,86)

k, cam, text = base.k, base.cam, base.text
g, pop, fade_in, L = v1.g, v1.pop, v1.fade_in, v1.L


def asset(obj_id: str, key: str, position: list, **extra) -> dict:
    return base.asset(obj_id, A.get(key, key), position, **extra)


def voltix(x: int, scale: float | list, expressions: list, gestures: list, y: int = 1490) -> dict:
    scales = scale if isinstance(scale, list) else [k(0, value=scale)]
    return asset("voltix", VOLTIX, [k(0, x=x, y=y)], scale=scales, expressions=expressions,
                 gestures=[gg for gg in gestures if gg["end"] > gg["start"]], lipsync=True)


def at(cue: dict, percent: int) -> int:
    """Image située à ``percent`` % d'une réplique (synchronise une apparition sur un mot)."""
    return cue["start"] + (cue["end"] - cue["start"]) * percent // 100


def scene(scene_id: str, s: dict, layers: list, camera: list, fallback: int, **extra) -> dict:
    purpose, mood = PURPOSE[scene_id]
    render = {"layers": [layer for layer in layers if layer["objects"]], "camera": {"keyframes": camera},
              "speech": {"speaker": "voltix", "cues": s["cues"]},
              "fallback": {"type": "still", "frame": max(0, min(fallback, s["duration"] - 1))}, **extra}
    return {"id": scene_id, "duration_frames": s["duration"],
            "intent": {"narration": " ".join(t for t, _ in LINES[scene_id]), "purpose": purpose, "mood": mood},
            "render": render}


# -- les 9 scènes ------------------------------------------------------------------------------------------------
def scene_accroche(s: dict) -> dict:
    """Ce qui est DIT est ce qui est MONTRÉ : la box, le câble HDMI et la télé, branchés ; puis la caméra pro."""
    d, (c1, c2) = s["duration"], s["cues"]
    salon = asset("salon", "salon", [k(0, x=540, y=600), k(c2["start"], x=540, y=600),
                                     k(c2["start"] + 14, x=330, y=520)],
                  scale=[k(0, value=0.45), k(10, value=1.05), k(c2["start"], value=1.05),
                         k(c2["start"] + 14, value=0.62)], easing="spring")
    pro = asset("camera-pro", "camera", [k(0, x=1300, y=560), k(c2["start"] + 6, x=1300, y=560),
                                         k(c2["start"] + 20, x=820, y=560)], scale=[k(0, value=0.9)],
                easing="ease_out")
    texts = [text("maison", "À la maison", "label", [k(0, x=330, y=330)], **fade_in(c2["start"] + 10)),
             text("pros", "Chez les pros ?", "label", [k(0, x=820, y=330)], **fade_in(c2["start"] + 20)),
             text("titre", "HDMI… et chez les pros ?", "subtitle", [k(0, x=540, y=180)], **pop(c1["start"]))]
    puppet = voltix(540, [k(0, value=0.78)], [k(0, name="sourire"), k(c2["start"], name="surprise"),
                                               k(c2["end"] - 6, name="concentration")],
                    [g("rebond", 0, 20), g("montrer", 20, c1["end"]), g("montrer", c2["start"] + 16, d)])
    return scene("accroche", s, [L("objets", [salon, pro]), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.06), cam(c1["end"], 540, 960, 1.0)], c2["end"] - 4)


def scene_definition(s: dict) -> dict:
    d, (c1, c2, c3) = s["duration"], s["cues"]
    texts = [text("sigle", "SDI", "title", [k(0, x=540, y=260)], scale=[k(0, value=0.4), k(10, value=1.6)],
                  easing="spring"),
             text("anglais", "Serial Digital Interface", "subtitle", [k(0, x=540, y=430)], **pop(at(c1, 40))),
             text("francais", "interface numérique série", "label", [k(0, x=540, y=560)], **fade_in(c2["start"]))]
    bits = asset("bits", "bits", [k(0, x=-500, y=760), k(c3["start"], x=-500, y=760), k(c3["start"] + 24, x=540, y=760),
                                  k(d, x=620, y=760)], scale=[k(0, value=1.0)], easing="ease_out")
    puppet = voltix(540, 0.66, [k(0, name="sourire"), k(c2["start"], name="concentration")],
                    [g("rebond", 0, 18), g("montrer", c3["start"], d)])
    return scene("definition", s, [L("schema", [bits]), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0)], at(c3, 90), transition_in={"type": "slide_left", "frames": 8})


def scene_broadcast(s: dict) -> dict:
    d, (c1, c2, c4, c5) = s["duration"], s["cues"]
    c3 = {"start": at(c1, 60), "end": c2["end"]}  # les lieux apparaissent pendant « broadcast : production… »
    places = [("plateau", "plateau", 200, at(c3, 5), "Plateau télé"), ("car", "car", 540, at(c3, 40), "Car régie"),
              ("direct", "direct", 880, at(c3, 72), "Direct")]
    objects, labels = [], []
    for obj_id, key, x, start, name in places:
        objects.append(asset(obj_id, key, [k(0, x=x, y=560)], end=c4["start"], **pop(start, 1.0)))
        labels.append(text(f"l-{obj_id}", name, "label", [k(0, x=x, y=730)], **fade_in(start + 4, end=c4["start"])))
    compare = [text("hdmi", "HDMI : vidéo numérique", "label", [k(0, x=540, y=440)], **fade_in(c4["start"])),
               text("sdi", "SDI : câbles longs, prises verrouillées", "label", [k(0, x=540, y=580)],
                    scale=[k(0, value=0.9)], **fade_in(at(c5, 20))),
               text("pas-pareil", "Pas les mêmes prises !", "subtitle", [k(0, x=540, y=740)], **fade_in(at(c5, 70)))]
    titles = [text("broadcast", "Broadcast", "title", [k(0, x=540, y=200)], **pop(c1["start"])),
              text("def", "production + diffusion pro", "label", [k(0, x=540, y=340)],
                   **fade_in(c2["start"], end=c4["start"]))]
    puppet = voltix(540, 0.62, [k(0, name="sourire"), k(c4["start"], name="concentration"),
                                k(c5["end"] - 4, name="sourire")],
                    [g("montrer", c3["start"], c3["end"]), g("rebond", c5["start"], c5["start"] + 24)])
    return scene("broadcast", s, [L("lieux", objects), L("textes", titles + labels + compare),
                                  L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(c3["end"], 540, 960, 1.03), cam(d, 540, 960, 1.0)], at(c3, 95),
                 transition_in={"type": "fade", "frames": 6})


# Chaîne indicative : caméra → grille → 4 destinations empilées à droite (liens = flèches symboliques).
CHAIN_DEST = [("moniteur", "moniteur", 330, 0.5), ("enregistreur", "enregistreur", 540, 0.62),
              ("melangeur", "melangeur", 750, 0.46), ("convertisseur", "convertisseur", 950, 0.62)]
GRID_AT, CAMERA_AT = (500, 640), (150, 640)


def _arrow_to(obj_id: str, start_xy: tuple, end_xy: tuple, start: int) -> dict:
    """Flèche (220 px) posée au milieu du segment, tournée vers la destination."""
    (x0, y0), (x1, y1) = start_xy, end_xy
    angle = math.degrees(math.atan2(y1 - y0, x1 - x0))
    length = math.hypot(x1 - x0, y1 - y0)
    return asset(obj_id, "fleche", [k(0, x=round((x0 + x1) / 2), y=round((y0 + y1) / 2))],
                 rotation=[k(0, value=round(angle, 1))], start=start,
                 scale=[k(start, value=0.1), k(start + 8, value=round(min(0.8, length / 220 * 0.7), 2))],
                 easing="ease_out")


def scene_chaine(s: dict) -> dict:
    c1, c3, c4 = s["cues"]
    objects = [asset("camera", "camera", [k(0, x=CAMERA_AT[0], y=CAMERA_AT[1])], scale=[k(0, value=0.55)],
                     **fade_in(0, 6)),
               asset("grille", "grille", [k(0, x=GRID_AT[0], y=GRID_AT[1])], **pop(at(c1, 18), 0.6)),
               _arrow_to("f-grille", (CAMERA_AT[0] + 90, CAMERA_AT[1]), (GRID_AT[0] - 120, GRID_AT[1]), at(c1, 14))]
    starts = [at(c1, 55), at(c1, 80), at(c3, 5), at(c4, 15)]  # au mot près : moniteur, enregistreur, …
    for (obj_id, key, y, scale), start in zip(CHAIN_DEST, starts):
        objects.append(asset(obj_id, key, [k(0, x=880, y=y)], **pop(start, scale)))
        objects.append(_arrow_to(f"f-{obj_id}", (GRID_AT[0] + 125, GRID_AT[1]), (880 - 120, y), start - 4))
    labels = [text("l-grille", "grille de routage", "label", [k(0, x=GRID_AT[0], y=GRID_AT[1] + 130)],
                   scale=[k(0, value=0.8)], **fade_in(at(c1, 25))),
              text("l-melangeur", "mélangeur : choisit les sources", "label", [k(0, x=540, y=1090)],
                   **fade_in(at(c3, 30), end=c4["start"] + 4)),
              text("l-hdmi", "convertisseur → HDMI", "label", [k(0, x=540, y=1090)], **fade_in(at(c4, 40)))]
    title = text("titre", "Exemple de chaîne", "subtitle", [k(0, x=540, y=150)], **fade_in(c1["start"]))
    note = text("note", "(indicative, pas un câblage type)", "label", [k(0, x=540, y=240)],
                scale=[k(0, value=0.75)], **fade_in(c1["start"] + 8))
    puppet = voltix(540, 0.52, [k(0, name="concentration"), k(c4["end"], name="sourire")],
                    [g("montrer", c1["start"], c3["end"])], y=1540)
    return scene("chaine", s, [L("equipements", objects), L("textes", [title, note] + labels),
                               L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0)], at(c4, 90), transition_in={"type": "slide_left", "frames": 8})


def scene_audio(s: dict) -> dict:
    d, (c1,) = s["duration"], s["cues"]
    join = at(c1, 55)
    objects = [asset("cable", "cable", [k(0, x=540, y=700)], scale=[k(0, value=1.0)]),
               asset("video", "video", [k(0, x=300, y=460), k(join - 10, x=300, y=460), k(join + 8, x=470, y=700)],
                     scale=[k(join - 10, value=1.0), k(join + 8, value=0.45)], easing="ease_in_out", **fade_in(0, 6)),
               asset("audio", "audio", [k(0, x=780, y=460), k(join - 4, x=780, y=460), k(join + 14, x=610, y=700)],
                     scale=[k(join - 4, value=1.0), k(join + 14, value=0.45)], easing="ease_in_out",
                     **fade_in(at(c1, 60)))]
    flow = v1.packets("paquet", 220, 860, 700, join + 14, d, every=10, travel=20)
    texts = [text("titre", "Vidéo + son intégré", "subtitle", [k(0, x=540, y=240)], **fade_in(c1["start"] + 4)),
             text("possible", "possible, pas obligatoire", "label", [k(0, x=540, y=880)], **fade_in(join + 10))]
    puppet = voltix(540, 0.6, [k(0, name="sourire")], [g("montrer", c1["start"], d)])
    return scene("audio", s, [L("canal", objects), L("flux", flow), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0)], join + 20, transition_in={"type": "fade", "frames": 6})


def scene_couches(s: dict) -> dict:
    """Hiérarchie claire : le SIGNAL (SDI) voyage DANS un SUPPORT (coaxial), branché par un CONNECTEUR (BNC)."""
    d, (c1, c2, c3) = s["duration"], s["cues"]
    onde = asset("onde", "onde-ok", [k(0, x=540, y=330)], **pop(at(c1, 40), 0.85))
    coax = [asset(key, key, [k(0, x=540, y=620)], scale=[k(0, value=0.7)], **fade_in(c2["start"] + 2 * i, 8))
            for i, key in enumerate(("coax-gaine", "coax-blindage", "coax-isolant", "coax-ame"))]
    push, lock = at(c3, 45), at(c3, 85)
    male = asset("bnc-male", BNC_M, [k(0, x=-300, y=930), k(at(c3, 25), x=250, y=930), k(push, x=250, y=930),
                                     k(push + 10, x=400, y=930)], scale=[k(0, value=1.0)], easing="ease_out",
                 start=c3["start"])
    femelle = asset("bnc-femelle", BNC_F, [k(0, x=1400, y=930), k(at(c3, 25), x=700, y=930)],
                    scale=[k(0, value=1.0)], easing="ease_out", start=c3["start"])
    texts = [text("l-signal", "SDI = le signal", "label", [k(0, x=540, y=200)], **fade_in(at(c1, 40))),
             text("l-coax", "coaxial = le câble (ici)", "label", [k(0, x=540, y=480)], **fade_in(at(c2, 50))),
             text("l-bnc", "BNC = le connecteur", "label", [k(0, x=540, y=1090)], **fade_in(at(c3, 15))),
             text("quart", "↻ ¼ de tour : clic !", "label", [k(0, x=820, y=790)], **pop(lock, 0.9))]
    puppet = voltix(880, 0.5, [k(0, name="concentration"), k(lock, name="sourire")],
                    [g("montrer", c1["start"], c2["end"]), g("rebond", lock, min(d, lock + 24))], y=1560)
    return scene("couches", s, [L("signal", [onde]), L("cable", coax), L("connecteurs", [femelle, male]),
                                L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0)], lock + 6, transition_in={"type": "slide_left", "frames": 8})


def scene_ohms(s: dict) -> dict:
    d, (c1, c2, c3) = s["duration"], s["cues"]
    coupe = asset("coax-cotes", "coax-cotes", [k(0, x=540, y=620)], **pop(c1["start"] + 4, 0.95))
    tag = asset("tag-75", "75", [k(0, x=540, y=1040)], **pop(at(c3, 60), 0.75))
    texts = [text("titre", "75 Ω", "title", [k(0, x=540, y=200)], **pop(at(c1, 60))),
             text("pas-r", "pas une résistance", "label", [k(0, x=540, y=320)], **fade_in(c2["start"])),
             text("z", "impédance caractéristique", "subtitle", [k(0, x=540, y=930)],
                  **fade_in(at(c2, 40), end=at(c3, 55))),
             text("prevus", "câble + connecteurs en 75 Ω", "label", [k(0, x=540, y=920)], **fade_in(at(c3, 55), 8))]
    puppet = voltix(880, 0.5, [k(0, name="concentration"), k(at(c3, 60), name="sourire")],
                    [g("montrer", c3["start"], c3["end"])], y=1560)
    return scene("ohms", s, [L("schema", [coupe, tag]), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(c3["start"], 540, 900, 1.05), cam(d, 540, 960, 1.0)],
                 at(c3, 80), transition_in={"type": "fade", "frames": 6})


def scene_debits(s: dict) -> dict:
    c1, c2, c3 = s["cues"]
    t = [at(c1, 5), at(c1, 38), at(c1, 70)]
    bars, labels = [], []
    for i, (key, name, y) in enumerate((("hd", "HD-SDI", 420), ("3g", "3G-SDI", 590), ("12g", "12G-SDI", 760))):
        bars.append(asset(f"barre-{key}", key, [k(0, x=90, y=y)], start=t[i],
                          scale=[k(t[i], value=0.05), k(t[i] + 12, value=1.0)], easing="ease_out"))
        labels.append(text(f"l-{key}", name, "label", [k(0, x=200, y=y - 80)], **fade_in(t[i])))
    texts = [text("titre", "Des débits, pas des résolutions", "subtitle", [k(0, x=540, y=200)],
                  scale=[k(0, value=0.95)], **fade_in(c2["start"])),
             text("cable", "câble + connecteurs prévus pour ce débit", "label", [k(0, x=540, y=960)],
                  scale=[k(0, value=0.85)], **fade_in(at(c3, 30))),
             text("etiquette", "« 75 Ω » seul ne suffit pas", "label", [k(0, x=540, y=1060)],
                  **fade_in(at(c3, 75)))]
    puppet = voltix(540, 0.52, [k(0, name="concentration"), k(c3["start"], name="confusion")],
                    [g("montrer", c2["start"], c2["end"])], y=1560)
    return scene("debits", s, [L("barres", bars), L("textes", labels + texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0)], at(c3, 90), transition_in={"type": "slide_left", "frames": 8})


# Liaison connectée (1000 × 470, centre (500, 235)) posée en (540, 600) à l'échelle 0,95.
LINK_AT, LINK_SCALE = (540, 600), 0.95


def link_point(x: float, y: float) -> tuple[int, int]:
    """Point de l'asset ``liaison-connectee`` → coordonnées écran."""
    return (round(LINK_AT[0] + (x - 500) * LINK_SCALE), round(LINK_AT[1] + (y - 235) * LINK_SCALE))


def scene_conclusion(s: dict) -> dict:
    d, (c1, c2, c3) = s["duration"], s["cues"]
    image = c3["start"] + 6
    screen = link_point(800, 135)  # écran du moniteur : centre (200, 135) de l'asset moniteur, posé en x = 600
    objects = [asset("liaison", "liaison", [k(0, x=LINK_AT[0], y=LINK_AT[1])], scale=[k(0, value=LINK_SCALE)],
                     **fade_in(0, 8)),
               asset("mire", "mire", [k(0, x=screen[0], y=screen[1])], scale=[k(0, value=LINK_SCALE)],
                     **fade_in(image, 8))]
    cable_mid, cam_port, mon_port = link_point(620, 420), link_point(296, 182), link_point(952, 244)
    texts = [text("l-signal", "① signal SDI", "label", [k(0, x=300, y=330)], **fade_in(at(c1, 40))),
             text("l-coax", "② coaxial 75 Ω", "label", [k(0, x=cable_mid[0], y=cable_mid[1] + 70)],
                  **fade_in(at(c2, 25))),
             text("l-bnc-1", "③ BNC", "label", [k(0, x=cam_port[0] + 160, y=cam_port[1] - 20)], **fade_in(at(c2, 70))),
             text("l-bnc-2", "③ BNC", "label", [k(0, x=mon_port[0] - 20, y=mon_port[1] + 90)],
                  **fade_in(at(c2, 70))),
             text("titre", "SDI : le signal de la vidéo pro", "subtitle", [k(0, x=540, y=180)], scale=[k(0, value=0.95)],
                  **fade_in(c1["start"]))]
    puppet = voltix(540, 0.7, [k(0, name="sourire"), k(image, name="surprise"), k(image + 10, name="sourire")],
                    [g("montrer", c1["start"], c1["end"]), g("rebond", image, image + 26),
                     g("saluer", image + 28, min(d, image + 58))], y=1520)
    return scene("conclusion", s, [L("liaison", objects), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(d, 540, 940, 1.03)], image + 16,
                 transition_in={"type": "fade", "frames": 6}, transition_out={"type": "fade", "frames": 12})


BUILDERS = {"accroche": scene_accroche, "definition": scene_definition, "broadcast": scene_broadcast,
            "chaine": scene_chaine, "audio": scene_audio, "couches": scene_couches, "ohms": scene_ohms,
            "debits": scene_debits, "conclusion": scene_conclusion}


# -- mise en page ------------------------------------------------------------------------------------------------
def estimated_layout() -> dict[str, dict]:
    """Repères de parole ESTIMÉS (mots / débit) : storyboard vérifiable sans synthèse vocale."""
    scenes, offset = {}, 0
    for scene_id, lines in LINES.items():
        cues, frame = [], LEAD
        for display, spoken in lines:
            frames = max(18, round(len(spoken.split()) / WORDS_PER_SECOND * FPS))
            cues.append({"text": display, "start": frame, "end": frame + frames})
            frame += frames + GAP
        duration = frame - GAP + TAIL[scene_id]
        scenes[scene_id] = {"duration": duration, "cues": cues, "offset": offset}
        offset += duration
    return scenes


def fit_tails(total_frames: int, tails: dict[str, int], min_tail: int = 4) -> dict[str, int]:
    """Raccourcit les fins de scène (la plus longue d'abord, jamais sous ``min_tail``) pour ramener la durée
    totale sous ``TARGET_SECONDS`` (la durée de la voix Piper varie de quelques dixièmes d'une synthèse à l'autre)."""
    tails, excess = dict(tails), total_frames - int(TARGET_SECONDS * FPS)
    while excess > 0:
        room = {sid: tails[sid] - MIN_TAILS.get(sid, min_tail) for sid in tails}
        longest = max(room, key=lambda sid: room[sid])
        if room[longest] <= 0:
            break
        tails[longest] -= 1
        excess -= 1
    return tails


def fit_layout(clips: dict, rate: int):
    """Mise en page #150 d'après les durées réelles de la voix ; fins de scène raccourcies si besoin."""
    base.LINES, base.LEAD, base.GAP, base.TAIL = LINES, LEAD, GAP, TAIL
    layout, voice = base.layout(clips, rate)
    total = sum(sc["duration"] for sc in layout.values())
    tails = fit_tails(total, TAIL)
    if tails != TAIL:
        base.TAIL = tails
        layout, voice = base.layout(clips, rate)
        total = sum(sc["duration"] for sc in layout.values())
    if not MIN_SECONDS * FPS <= total <= MAX_SECONDS * FPS:
        raise SystemExit(f"durée {total / FPS:.2f} s hors de la borne {MIN_SECONDS}–{MAX_SECONDS} s : "
                         "raccourcir le script ou ajuster le débit de la voix")
    return layout, voice


def build_storyboard(layout: dict[str, dict]) -> dict:
    document = {"schema": "lody.animation/1", "canvas": {"fps": FPS, "width": WIDTH, "height": HEIGHT},
                "scenes": [BUILDERS[scene_id](layout[scene_id]) for scene_id in LINES]}
    return validate_document(document)


_load_v1_catalogs = v1.load_catalogs  # Voltix (#136) + SDI V1 (#137), avant substitution par ``run``


def load_catalogs() -> list[dict]:
    return [*_load_v1_catalogs(), json.loads(SDI_V2_CATALOG.read_text(encoding="utf-8"))]


# -- prononciations ----------------------------------------------------------------------------------------------
PHONEMIZE = ("from piper import PiperVoice; import sys, json; v = PiperVoice.load(sys.argv[1]); "
             "print(json.dumps([' '.join(''.join(x) for x in v.phonemize(t)) for t in sys.argv[2:]], "
             "ensure_ascii=False))")


def check_pronunciations(phonemize) -> list[str]:
    """Compare les phonèmes produits pour chaque forme parlée à ceux attendus. ``phonemize`` reçoit une liste de
    textes et renvoie la liste des phonèmes. Renvoie les écarts (liste vide = conforme)."""
    terms = list(PRONUNCIATIONS)
    got = phonemize([PRONUNCIATIONS[t][0] for t in terms])
    return [f"{t} : « {PRONUNCIATIONS[t][0]} » → {ph} (attendu {PRONUNCIATIONS[t][1]})"
            for t, ph in zip(terms, got) if ph.strip() != PRONUNCIATIONS[t][1]]


def piper_phonemizer(model: Path, python: list[str]):
    def run(texts: list[str]) -> list[str]:
        out = subprocess.run([*python, "-c", PHONEMIZE, str(model), *texts], check=True, capture_output=True,
                             text=True, timeout=600)
        return json.loads(out.stdout.strip().splitlines()[-1])
    return run


# -- exécution ---------------------------------------------------------------------------------------------------
def synthesize_v2(tts: list[str], model: Path, spoken: str, target: Path):
    """Synthèse Piper de ``demo_voltix_150.synthesize`` avec le débit V2 (``LENGTH_SCALE`` au lieu de 0,92)."""
    import wave

    import numpy as np

    source = target.with_suffix(".txt")
    source.write_text(spoken + "\n", encoding="utf-8")
    subprocess.run([*tts, "-m", str(model), "-s", "1", "--length-scale", str(LENGTH_SCALE), "--sentence-silence",
                    "0", "-i", str(source), "-f", str(target)], check=True, capture_output=True, timeout=300)
    with wave.open(str(target), "rb") as handle:
        rate = handle.getframerate()
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(np.float32) / 32768
        if handle.getnchannels() > 1:
            data = data.reshape(-1, handle.getnchannels()).mean(axis=1)
    loud = np.flatnonzero(np.abs(data) > 0.0056)  # rognage des silences de bord (−45 dB), comme #150
    if loud.size:
        data = data[max(0, loud[0] - int(0.02 * rate)): loud[-1] + int(0.05 * rate)]
    return data, rate


def run(out: Path, model: Path, tts: list[str], renderer: str, keep: Path | None = None) -> dict:
    """Rendu complet : identique à la V1 (``episode_voltix_137.run``) avec le script, les catalogues, le débit
    de voix et la musique V2."""
    import musique_v2

    synthesize = base.synthesize

    patches = {"LINES": LINES, "TAIL": TAIL, "BUILDERS": BUILDERS, "fit_layout": fit_layout,
               "build_storyboard": build_storyboard, "load_catalogs": load_catalogs}
    saved = {name: getattr(v1, name) for name in patches}
    saved_music = v1.__dict__.get("musique_test")
    try:
        for name, value in patches.items():
            setattr(v1, name, value)
        base.synthesize = synthesize_v2
        sys.modules["musique_test"] = musique_v2  # musique de test V2 (même interface ``write``)
        summary = v1.run(out, model, tts, renderer, keep or Path(tempfile.mkdtemp(prefix="lody-160-")))
    finally:
        for name, value in saved.items():
            setattr(v1, name, value)
        base.synthesize = synthesize
        if saved_music is not None:
            sys.modules["musique_test"] = saved_music
        else:
            sys.modules.pop("musique_test", None)
    mp4 = out / "voltix-137.mp4"
    if mp4.exists():
        mp4.replace(out / "voltix-137-v2.mp4")
    # ``episode_voltix_137.run`` décrit la musique V1 dans le manifeste : on y inscrit la musique réellement mixée.
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("audio", {}).setdefault("music", {})["source"] = "scripts/animation/musique_v2.py (générée)"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    summary["voice"] = {"engine": "Piper (provisoire)", "length_scale": LENGTH_SCALE}
    summary["music"] = "scripts/animation/musique_v2.py (générée, provisoire)"
    (out / "episode.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--piper-model", type=Path)
    parser.add_argument("--tts", default="uvx --from piper-tts piper")
    parser.add_argument("--python", default="uvx --from piper-tts python", help="Python avec piper-tts (phonèmes)")
    parser.add_argument("--renderer", default=f"node {ROOT / 'renderers/remotion/bin/lody-remotion.mjs'}")
    parser.add_argument("--work", type=Path, default=None)
    parser.add_argument("--storyboard-only", action="store_true")
    parser.add_argument("--check-pronunciations", action="store_true")
    args = parser.parse_args()
    if args.check_pronunciations:
        if not args.piper_model:
            parser.error("--piper-model est requis")
        gaps = check_pronunciations(piper_phonemizer(args.piper_model, shlex.split(args.python)))
        print("\n".join(gaps) or f"{len(PRONUNCIATIONS)} prononciations conformes")
        return 1 if gaps else 0
    if not args.out:
        parser.error("--out est requis")
    if args.storyboard_only:
        args.out.mkdir(parents=True, exist_ok=True)
        document = build_storyboard(estimated_layout())
        (args.out / "storyboard-estime.json").write_text(json.dumps(document, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
        print(sum(sc["duration_frames"] for sc in document["scenes"]) / FPS, "s (estimation)")
        return 0
    if not args.piper_model:
        parser.error("--piper-model est requis pour le rendu (ou --storyboard-only)")
    summary = run(args.out, args.piper_model, shlex.split(args.tts), args.renderer, args.work)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
