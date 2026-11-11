"""Épisode #137 « Voltix explique le câble SDI » : storyboard 9 scènes et rendu par la VRAIE chaîne 2D.

Même chaîne que la démo #150 (``demo_voltix_150.py``, environnement DEV isolé : base SQLite et dossier de
données temporaires) :

    projet Animation 2D + fiche « Voltix » → import des catalogues Voltix (#136) et schémas SDI (#137)
    → storyboard lody.animation/1 validé puis gelé (#119) → AnimationConnector.preflight/submit (#120)
    → worker run_job → moteur de scène Remotion (#150) → mixage voix + musique → out.mp4

Voix PROVISOIRE : Piper local et gratuit (``fr_FR-upmc-medium``), jamais ElevenLabs ni OpenAI : la fiche
Voltix n'a pas de voix validée (#153) et aucun budget n'est accordé. Musique : ``musique_test.py`` (générée,
aucune piste tierce). Aucun appel payant, aucune donnée de production.

Le texte AFFICHÉ (sous-titres) et le texte PARLÉ (sigles épelés pour la synthèse) sont séparés dans ``LINES``.

Usage :
    python scripts/animation/episode_voltix_137.py --out /tmp/voltix-137 \\
        --piper-model /chemin/fr_FR-upmc-medium.onnx [--tts "uvx --from piper-tts piper"]
    python scripts/animation/episode_voltix_137.py --storyboard-only --out /tmp/sb   # sans voix ni rendu
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT), str(Path(__file__).resolve().parent)]

import demo_voltix_150 as base  # noqa: E402  (voix Piper, mise en page, écriture WAV : mêmes règles que #150)
from lody.animation.contract import validate_document  # noqa: E402

FPS, WIDTH, HEIGHT = 30, 1080, 1920
VOLTIX, CABLE, BNC_M, BNC_F = base.VOLTIX, base.CABLE, base.BNC_M, base.BNC_F
SDI_CATALOG = ROOT / "resource" / "animation-assets" / "sdi-catalogue.json"
VOLTIX_CATALOG = ROOT / "resource" / "animation-assets" / "voltix-catalogue.json"
LEAD, GAP = 10, 6  # images avant la première réplique d'une scène, entre deux répliques

# Identifiants des schémas #137 dans sdi-catalogue.json (remplacés à l'import).
A = {
    "camera": "ast_137000000001", "melangeur": "ast_137000000002", "moniteur": "ast_137000000003",
    "mire": "ast_137000000004", "cable": "ast_137000000005", "paquet": "ast_137000000006",
    "coax-ame": "ast_137000000010", "coax-isolant": "ast_137000000011", "coax-blindage": "ast_137000000012",
    "coax-gaine": "ast_137000000013", "coax-x": "ast_137000000014", "bague": "ast_137000000021",
    "ergots": "ast_137000000022", "onde-ok": "ast_137000000030", "onde-ko": "ast_137000000031",
    "video": "ast_137000000032", "audio": "ast_137000000033", "hd": "ast_137000000040",
    "3g": "ast_137000000041", "12g": "ast_137000000042", "75": "ast_137000000050",
}

# Scène → répliques (texte affiché, texte parlé). Script du brief #137, découpé au chrono des 9 scènes.
LINES: dict[str, list[tuple[str, str]]] = {
    "accroche": [("Tu vois ce câble ?", "Tu vois ce câble ?"),
                 ("En régie, il peut relier une caméra à un mélangeur vidéo.",
                  "En régie, il peut relier une caméra à un mélangeur vidéo.")],
    "liaison": [("C'est une liaison SDI :", "C'est une liaison, èsse dé i :"),
                ("de la vidéo numérique professionnelle.", "de la vidéo numérique professionnelle.")],
    "coupe": [("Regarde à l'intérieur.", "Regarde à l'intérieur !"),
              ("Ce câble coaxial possède un conducteur central, un isolant…",
               "Ce câble coaxial possède un conducteur central, un isolant,"),
              ("… et un blindage, protégés par une gaine.", "et un blindage, protégés par une gaine.")],
    "bnc": [("Et cette fiche métallique ? C'est un connecteur BNC.",
             "Et cette fiche métallique ? C'est un connecteur, bé ène cé."),
            ("On l'insère, on le tourne, et il se verrouille !", "On l'insère, on le tourne, et il se verrouille !")],
    "distinction": [("Attention : le BNC, c'est le connecteur.", "Attention : le bé ène cé, c'est le connecteur."),
                    ("Le SDI, c'est le signal.", "Le èsse dé i, c'est le signal.")],
    "audio": [("Et le son peut aussi voyager avec l'image.", "Et le son peut aussi voyager avec l'image.")],
    "debits": [("Tu entendras parler de HD-SDI, de 3G-SDI et de 12G-SDI.",
                "Tu entendras parler de hache dé, èsse dé i, de trois G, èsse dé i, et de douze G, èsse dé i."),
               ("Selon le format, on transporte plus de données…", "Selon le format, on transporte plus de données,"),
               ("… par exemple de la 4K à 60 images par seconde en 12G.",
                "par exemple de la quatre K à soixante images par seconde, en douze G.")],
    "limites": [("Mais attention : ce n'est pas magique !", "Mais attention : ce n'est pas magique !"),
                ("La qualité du câble, de ses connecteurs…", "La qualité du câble, de ses connecteurs,"),
                ("… et sa longueur comptent énormément.", "et sa longueur, comptent énormément.")],
    "conclusion": [("Voilà : le signal SDI, un bon coaxial et des BNC adaptés.",
                    "Voilà : le signal èsse dé i, un bon coaxial, et des bé ène cé adaptés."),
                   ("Et la régie retrouve son image !", "Et la régie retrouve son image !")],
}
# Fins de scène (images) : réglées pour que la durée totale reste dans 55–65 s avec la voix provisoire mesurée.
TAIL = {"accroche": 8, "liaison": 10, "coupe": 14, "bnc": 18, "distinction": 12, "audio": 14, "debits": 14,
        "limites": 14, "conclusion": 32}
PURPOSE = {
    "accroche": ("Accroche : SDI, c'est quoi ?", "enjoué"), "liaison": ("Liaison SDI caméra → mélangeur", "clair"),
    "coupe": ("Couches du coaxial", "pédagogique"), "bnc": ("Verrouillage BNC à baïonnette", "concentré"),
    "distinction": ("SDI = signal, BNC = connecteur", "net"), "audio": ("Audio intégré possible", "léger"),
    "debits": ("HD / 3G / 12G-SDI", "rythmé"), "limites": ("Câble et connecteurs adaptés", "prudent"),
    "conclusion": ("Récapitulatif et image retrouvée", "victorieux"),
}
MIN_SECONDS, MAX_SECONDS, TARGET_SECONDS = 55.0, 65.0, 64.5  # durée imposée par le brief, avec une marge
MIN_TAIL = 6  # images minimales après la dernière réplique d'une scène
WORDS_PER_SECOND = 3.1  # débit de la voix provisoire mesuré au rendu ; sert seulement à l'estimation sans voix


# -- outils de storyboard (mêmes conventions que #150) -----------------------------------------------------------
k, cam, text = base.k, base.cam, base.text


def asset(obj_id: str, key: str, position: list, **extra) -> dict:
    asset_id = A.get(key, key)
    return base.asset(obj_id, asset_id, position, **extra)


def voltix(x: int, scale: float | list, expressions: list, gestures: list, y: int = 1490) -> dict:
    scales = scale if isinstance(scale, list) else [k(0, value=scale)]
    return asset("voltix", VOLTIX, [k(0, x=x, y=y)], scale=scales, expressions=expressions,
                 gestures=[g for g in gestures if g["end"] > g["start"]], lipsync=True)


def g(name: str, start: int, end: int) -> dict:
    return {"start": max(0, start), "end": end, "name": name}


def pop(start: int, value: float = 1.0) -> dict:
    """Apparition « ressort » : échelle de 40 % à ``value`` en 12 images."""
    return {"start": start, "scale": [k(start, value=round(value * 0.4, 3)), k(start + 12, value=value)],
            "easing": "spring"}


def fade_in(start: int, frames: int = 10, end: int | None = None) -> dict:
    out = {"start": start, "opacity": [k(start, value=0), k(start + frames, value=1)]}
    if end is not None:
        out["end"] = end
    return out


def packets(prefix: str, x0: int, x1: int, y: int, first: int, last: int, every: int = 14,
            travel: int = 24, scale: float = 0.9) -> list[dict]:
    """Paquets numériques qui traversent le câble de ``x0`` à ``x1`` entre les images ``first`` et ``last``."""
    out, n = [], 0
    for start in range(first, max(first + 1, last - travel), every):
        out.append(asset(f"{prefix}-{n}", "paquet", [k(start, x=x0, y=y), k(start + travel, x=x1, y=y)],
                         start=start, end=start + travel, scale=[k(0, value=scale)], easing="linear"))
        n += 1
    return out


def scene(scene_id: str, s: dict, layers: list, camera: list, fallback: int, **extra) -> dict:
    purpose, mood = PURPOSE[scene_id]
    render = {"layers": [layer for layer in layers if layer["objects"]], "camera": {"keyframes": camera},
              "speech": {"speaker": "voltix", "cues": s["cues"]},
              "fallback": {"type": "still", "frame": min(fallback, s["duration"] - 1)}, **extra}
    return {"id": scene_id, "duration_frames": s["duration"],
            "intent": {"narration": " ".join(t for t, _ in LINES[scene_id]), "purpose": purpose, "mood": mood},
            "render": render}


def L(layer_id: str, objects: list) -> dict:  # noqa: N802 - calque
    return {"id": layer_id, "objects": objects}


# -- les 9 scènes ------------------------------------------------------------------------------------------------
def scene_accroche(s: dict) -> dict:
    d, (c1, c2) = s["duration"], s["cues"]
    hidden_cable = asset("cable-sdi", CABLE, [k(0, x=-400, y=-400)], opacity=[k(0, value=0)])  # pour « tenir-cable »
    objects = [
        asset("camera", "camera", [k(0, x=-260, y=600), k(c2["start"] + 6, x=-260, y=600),
                                    k(c2["start"] + 22, x=210, y=560)], scale=[k(0, value=0.9)], easing="ease_out"),
        asset("melangeur", "melangeur", [k(0, x=1400, y=600), k(c2["start"] + 26, x=1400, y=600),
                                          k(c2["start"] + 42, x=850, y=570)], scale=[k(0, value=0.82)],
              easing="ease_out"),
        asset("cable", "cable", [k(0, x=540, y=700)], scale=[k(0, value=0.62)], **fade_in(c2["start"] + 44)),
    ]
    title = text("titre", "SDI : c'est quoi ?", "title", [k(0, x=540, y=260)], **pop(c1["start"]))
    shrink = [k(0, value=1.1), k(c2["start"], value=1.1), k(c2["start"] + 14, value=0.78)]
    puppet = voltix(540, shrink, [k(0, name="sourire"), k(c2["start"], name="neutre")],
                    [g("rebond", 0, 28), g("tenir-cable", 28, c1["end"] + 6),
                     g("montrer", c2["start"] + 4, min(d, c2["end"]))])
    return scene("accroche", s, [L("regie", objects), L("textes", [title]), L("accessoires", [hidden_cable]),
                                 L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.08), cam(c2["start"], 540, 960, 1.0)], c2["end"] - 4,
                 transition_in={"type": "fade", "frames": 8})


def scene_liaison(s: dict) -> dict:
    d, (c1, c2) = s["duration"], s["cues"]
    regie = [asset("camera", "camera", [k(0, x=190, y=600)], scale=[k(0, value=0.78)]),
             asset("cable", "cable", [k(0, x=540, y=700)], scale=[k(0, value=0.62)]),
             asset("melangeur", "melangeur", [k(0, x=880, y=610)], scale=[k(0, value=0.74)])]
    flow = packets("paquet", 330, 760, 700, c1["start"] + 6, d - 4)
    texts = [text("liaison", "liaison SDI", "label", [k(0, x=540, y=800)], **pop(c1["start"] + 8)),
             text("signal", "Signal vidéo numérique", "subtitle", [k(0, x=540, y=330)], **fade_in(c2["start"]))]
    puppet = voltix(860, 0.66, [k(0, name="concentration"), k(c2["start"], name="sourire")],
                    [g("montrer", c1["start"], min(d, c2["end"]))])
    return scene("liaison", s, [L("regie", regie), L("flux", flow), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(d, 540, 900, 1.05)], c1["start"] + 40,
                 transition_in={"type": "slide_left", "frames": 10})


# Coupe longitudinale : 4 calques empilés à l'échelle 0,9 ; centre x = 540 → bord gauche du repère x = 90.
COAX_SCALE, COAX_Y = 0.9, 560
COAX_SPREAD = {"coax-ame": 90, "coax-isolant": 60, "coax-blindage": 30, "coax-gaine": 0}


def scene_coupe(s: dict) -> dict:
    d, (c1, c2, c3) = s["duration"], s["cues"]
    spread_at = c1["end"]
    layers = [asset(key, key, [k(0, x=-600, y=COAX_Y), k(12, x=540, y=COAX_Y), k(spread_at, x=540, y=COAX_Y),
                               k(spread_at + 14, x=540 + round(COAX_SPREAD[key] * COAX_SCALE), y=COAX_Y)],
                    scale=[k(0, value=COAX_SCALE)], easing="ease_out") for key in COAX_SPREAD]
    # Étiquettes au rythme des mots : centre de la partie visible de chaque couche après séparation.
    span2, span3 = c2["end"] - c2["start"], c3["end"] - c3["start"]
    labels = [("l-ame", "Conducteur", 900, 440, c2["start"] + span2 * 45 // 100),
              ("l-isolant", "Isolant", 750, 680, c2["start"] + span2 * 85 // 100),
              ("l-blindage", "Blindage", 570, 440, c3["start"] + span3 * 15 // 100),
              ("l-gaine", "Gaine", 290, 680, c3["start"] + span3 * 75 // 100)]
    texts = [text(i, c, "label", [k(0, x=x, y=y)], **pop(f)) for i, c, x, y, f in labels]
    section = asset("coax-x", "coax-x", [k(0, x=300, y=1010)], scale=[k(0, value=0.52)], **fade_in(c3["start"] + 6))
    puppet = voltix(880, 0.6, [k(0, name="surprise"), k(c2["start"], name="concentration"),
                               k(c3["end"], name="sourire")],
                    [g("montrer", c2["start"], c3["end"] - 2)])
    return scene("coupe", s, [L("schema", layers), L("coupe-transversale", [section]), L("etiquettes", texts),
                              L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(c1["end"], 540, 900, 1.04), cam(d, 540, 960, 1.0)],
                 c3["end"] + 6, transition_in={"type": "slide_left", "frames": 10})


def scene_bnc(s: dict) -> dict:
    d, (c1, c2) = s["duration"], s["cues"]
    span = c2["end"] - c2["start"]
    push, turn, lock = c2["start"] + 4, c2["start"] + span * 40 // 100, c2["start"] + span * 75 // 100
    male = asset("bnc-male", BNC_M, [k(0, x=-420, y=480), k(c1["start"] + 16, x=240, y=480),
                                     k(push, x=240, y=480), k(push + 10, x=410, y=480)],
                 scale=[k(0, value=1.2)], easing="ease_out")
    femelle = asset("bnc-femelle", BNC_F, [k(0, x=1500, y=480), k(c1["start"] + 10, x=690, y=480)],
                    scale=[k(0, value=1.2)], easing="ease_out")
    # Vue de face : la bague (fiche mâle) tourne d'un quart de tour et masque les deux ergots de l'embase.
    ergots = asset("ergots", "ergots", [k(0, x=300, y=960)], scale=[k(0, value=0.8)], **fade_in(push))
    bague = asset("bague", "bague", [k(0, x=300, y=960)], scale=[k(0, value=0.8)],
                  rotation=[k(turn, value=0), k(lock, value=90)], easing="ease_in_out", **fade_in(push))
    texts = [text("l-male", "BNC mâle", "label", [k(0, x=240, y=660)], **fade_in(c1["start"] + 16)),
             text("l-femelle", "BNC femelle", "label", [k(0, x=760, y=660)], **fade_in(c1["start"] + 10)),
             text("etapes", "Insérer · tourner · verrouiller", "subtitle", [k(0, x=540, y=240)], **fade_in(push)),
             text("vue-face", "vue de face", "label", [k(0, x=300, y=1150)], **fade_in(push + 6)),
             text("quart", "↻ ¼ de tour", "label", [k(0, x=300, y=760)], **fade_in(turn, end=lock + 16)),
             text("clic", "Clic !", "title", [k(0, x=560, y=360)], **pop(lock))]
    puppet = voltix(860, 0.62, [k(0, name="neutre"), k(c2["start"], name="concentration"),
                                k(lock, name="surprise"), k(lock + 12, name="sourire")],
                    [g("montrer", c1["start"], turn), g("rebond", lock, min(d, lock + 28))])
    return scene("bnc", s, [L("connecteurs", [femelle, male]), L("vue-face", [ergots, bague]),
                            L("etiquettes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(push + 10, 540, 960, 1.03), cam(d, 540, 960, 1.0)], lock + 14,
                 transition_in={"type": "slide_left", "frames": 10})


def scene_distinction(s: dict) -> dict:
    d, (c1, c2) = s["duration"], s["cues"]
    objects = [asset("bnc-male", BNC_M, [k(0, x=780, y=620)], **pop(c1["start"] + 8, 1.0)),
               asset("onde", "onde-ok", [k(0, x=280, y=620)], **pop(c2["start"] + 2, 0.78))]
    texts = [text("bnc", "BNC = connecteur", "label", [k(0, x=790, y=440)], **fade_in(c1["start"] + 8)),
             text("sdi", "SDI = signal", "label", [k(0, x=270, y=440)], **fade_in(c2["start"] + 2))]
    puppet = voltix(540, 0.7, [k(0, name="concentration"), k(c2["start"], name="sourire")],
                    [g("montrer", c1["start"], c1["end"]), g("montrer", c2["start"], min(d, c2["end"] + 4))])
    return scene("distinction", s, [L("schemas", objects), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0)], c2["end"], transition_in={"type": "fade", "frames": 8})


def scene_audio(s: dict) -> dict:
    d, (c1,) = s["duration"], s["cues"]
    join = c1["start"] + 22
    objects = [asset("cable", "cable", [k(0, x=540, y=720)], scale=[k(0, value=1.0)]),
               asset("video", "video", [k(0, x=260, y=470), k(join - 12, x=260, y=470), k(join + 8, x=470, y=720)],
                     scale=[k(join - 12, value=1.0), k(join + 8, value=0.45)], easing="ease_in_out",
                     **fade_in(c1["start"])),
               asset("audio", "audio", [k(0, x=820, y=470), k(join - 4, x=820, y=470), k(join + 16, x=610, y=720)],
                     scale=[k(join - 4, value=1.0), k(join + 16, value=0.45)], easing="ease_in_out",
                     **fade_in(c1["start"] + 12))]
    flow = packets("paquet", 220, 860, 720, join + 18, d, every=12)
    texts = [text("audio-integre", "Audio intégré possible", "subtitle", [k(0, x=540, y=260)], **fade_in(c1["start"] + 6))]
    puppet = voltix(860, 0.6, [k(0, name="sourire")], [g("montrer", c1["start"], min(d, c1["end"]))])
    return scene("audio", s, [L("canal", objects), L("flux", flow), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0)], join + 24, transition_in={"type": "slide_left", "frames": 10})


def scene_debits(s: dict) -> dict:
    d, (c1, c2, c3) = s["duration"], s["cues"]
    span = c1["end"] - c1["start"]
    t = [c1["start"] + span * p // 100 for p in (20, 50, 78)]
    bars, labels = [], []
    for i, (key, name, y) in enumerate((("hd", "HD-SDI", 470), ("3g", "3G-SDI", 640), ("12g", "12G-SDI", 810))):
        bars.append(asset(f"barre-{key}", key, [k(0, x=90, y=y)],
                          start=t[i], scale=[k(t[i], value=0.05), k(t[i] + 16, value=1.0)], easing="ease_out"))
        labels.append(text(f"l-{key}", name, "label", [k(0, x=190, y=y - 80)], **fade_in(t[i])))
    texts = [text("titre", "HD / 3G / 12G", "title", [k(0, x=540, y=240)], **pop(c1["start"])),
             text("exemple", "ex. 4K à 60 images/s en 12G", "subtitle", [k(0, x=540, y=1000)], **fade_in(c3["start"])),
             text("selon", "selon le format", "label", [k(0, x=760, y=640)], **fade_in(c2["start"]))]
    puppet = voltix(880, 0.58, [k(0, name="concentration"), k(c3["start"], name="sourire")],
                    [g("montrer", c1["start"], c1["end"]), g("rebond", c3["start"], min(d, c3["start"] + 28))])
    return scene("debits", s, [L("barres", bars), L("etiquettes", labels + texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(d, 540, 960, 1.02)], c3["start"] + 20,
                 transition_in={"type": "fade", "frames": 8})


def scene_limites(s: dict) -> dict:
    d, (c1, c2, c3) = s["duration"], s["cues"]
    good, bad = c2["start"], c3["start"]
    objects = [asset("cable-ok", "cable", [k(0, x=430, y=430)], scale=[k(0, value=0.95)], **fade_in(good)),
               asset("onde-ok", "onde-ok", [k(0, x=420, y=560)], scale=[k(0, value=0.66)], **fade_in(good + 6)),
               asset("cable-ko", "cable", [k(0, x=430, y=790)], scale=[k(0, value=0.95)], **fade_in(bad)),
               asset("onde-ko", "onde-ko", [k(0, x=420, y=920)], scale=[k(0, value=0.66)], **fade_in(bad + 6)),
               asset("tag-75", "75", [k(0, x=900, y=400)], **pop(c3["end"] - 24, 0.8))]
    texts = [text("pas-magique", "Pas magique !", "title", [k(0, x=540, y=600)], start=c1["start"],
                  end=c1["end"] + 4, scale=[k(c1["start"], value=0.4), k(c1["start"] + 12, value=1.0)],
                  rotation=[k(c1["start"], value=-6)], easing="spring"),
             text("ok", "✓ adapté", "label", [k(0, x=200, y=330)], **fade_in(good)),
             text("ko", "✗ inadapté ou trop long", "label", [k(0, x=330, y=690)], **fade_in(bad)),
             text("titre", "Câble et connecteurs adaptés", "subtitle", [k(0, x=540, y=200)], **fade_in(good))]
    puppet = voltix(880, 0.58, [k(0, name="confusion"), k(c2["start"], name="concentration")],
                    [g("montrer", c3["end"] - 24, min(d, c3["end"] + 12))])
    return scene("limites", s, [L("trajets", objects), L("textes", texts), L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0)], c3["end"] - 6, transition_in={"type": "slide_left", "frames": 10})


def scene_conclusion(s: dict) -> dict:
    d, (c1, c2) = s["duration"], s["cues"]
    image = c2["start"] + 10
    # Écran du moniteur : centre (200, 135) de l'asset 400 × 320 → décalage (0, −25) × 0,75 sous le centre.
    regie = [asset("camera", "camera", [k(0, x=190, y=560)], scale=[k(0, value=0.75)]),
             asset("cable", "cable", [k(0, x=540, y=660)], scale=[k(0, value=0.5)]),
             asset("moniteur", "moniteur", [k(0, x=880, y=560)], scale=[k(0, value=0.75)]),
             asset("mire", "mire", [k(0, x=880, y=541)], scale=[k(0, value=0.75)], **fade_in(image, 8))]
    flow = packets("paquet", 370, 710, 660, c1["start"], image + 8, every=10, travel=18, scale=0.7)
    texts = [text("recap", "SDI + coaxial + BNC", "subtitle", [k(0, x=540, y=260)], **pop(c1["start"] + 10))]
    puppet = voltix(540, 0.8, [k(0, name="sourire"), k(image, name="surprise"), k(image + 10, name="sourire")],
                    [g("montrer", c1["start"], c1["end"]), g("rebond", image, image + 28),
                     g("saluer", image + 30, min(d, image + 60))], y=1500)
    return scene("conclusion", s, [L("regie", regie), L("flux", flow), L("textes", texts),
                                   L("personnages", [puppet])],
                 [cam(0, 540, 960, 1.0), cam(d, 540, 940, 1.04)], image + 20,
                 transition_in={"type": "fade", "frames": 8}, transition_out={"type": "fade", "frames": 12})


BUILDERS = {"accroche": scene_accroche, "liaison": scene_liaison, "coupe": scene_coupe, "bnc": scene_bnc,
            "distinction": scene_distinction, "audio": scene_audio, "debits": scene_debits,
            "limites": scene_limites, "conclusion": scene_conclusion}


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


def fit_tails(total_frames: int, tails: dict[str, int]) -> dict[str, int]:
    """Raccourcit les fins de scène (la plus longue d'abord, jamais sous ``MIN_TAIL``) pour ramener la durée
    totale sous ``TARGET_SECONDS``. La voix provisoire varie de quelques dixièmes d'une synthèse à l'autre."""
    tails, excess = dict(tails), total_frames - int(TARGET_SECONDS * FPS)
    while excess > 0:
        longest = max(tails, key=lambda sid: tails[sid])
        if tails[longest] <= MIN_TAIL:
            break
        tails[longest] -= 1
        excess -= 1
    return tails


def fit_layout(clips: dict, rate: int):
    """Mise en page #150 (repères d'après les durées réelles de la voix), fins de scène ajustées à la borne."""
    base.LINES, base.LEAD, base.GAP, base.TAIL = LINES, LEAD, GAP, TAIL
    layout, voice = base.layout(clips, rate)
    total = sum(s["duration"] for s in layout.values())
    base.TAIL = fit_tails(total, TAIL)
    if base.TAIL != TAIL:
        layout, voice = base.layout(clips, rate)
        total = sum(s["duration"] for s in layout.values())
    if not MIN_SECONDS * FPS <= total <= MAX_SECONDS * FPS:
        raise SystemExit(f"durée {total / FPS:.2f} s hors de la borne {MIN_SECONDS}–{MAX_SECONDS} s : "
                         "raccourcir le script ou ajuster le débit de la voix")
    return layout, voice


def build_storyboard(layout: dict[str, dict]) -> dict:
    """Storyboard ``lody.animation/1`` (non figé, identifiants du catalogue) d'après les repères de parole."""
    document = {"schema": "lody.animation/1", "canvas": {"fps": FPS, "width": WIDTH, "height": HEIGHT},
                "scenes": [BUILDERS[scene_id](layout[scene_id]) for scene_id in LINES]}
    return validate_document(document)


def load_catalogs() -> tuple[dict, dict]:
    return (json.loads(VOLTIX_CATALOG.read_text(encoding="utf-8")),
            json.loads(SDI_CATALOG.read_text(encoding="utf-8")))


# -- exécution ---------------------------------------------------------------------------------------------------
def setup_project(db: Path):
    """Projet DEV Animation 2D, fiche Voltix, catalogues importés. Renvoie (projet, dépôt, fiche, id_map)."""
    from lody.animation import modes
    from lody.animation.assets import AnimationAssetRepository
    from lody.characters import CharacterRepository
    from lody.projects import ProjectRepository

    projects, assets = ProjectRepository(db), AnimationAssetRepository(db)
    project = projects.create(name="Voltix — épisode SDI (#137)", settings={modes.SETTINGS_KEY: modes.ANIMATION})
    character = CharacterRepository(db).create(project.id, name="Voltix")
    id_map, warnings = {}, []
    for catalog in load_catalogs():
        mapping, notes = assets.import_catalog(project.id, catalog)
        id_map.update(mapping)
        warnings += notes
    return project, assets, character, id_map, warnings


def run(out: Path, model: Path, tts: list[str], renderer: str, keep: Path | None = None) -> dict:
    import numpy as np  # noqa: F401  (dépendance de demo_voltix_150)

    from lody.animation import audio, modes
    from lody.animation import worker as w
    from lody.animation.freeze import freeze_document, remap_assets
    from lody.generation.animation_connector import AnimationConnector
    from lody.generation.service import build_request

    import musique_test

    out.mkdir(parents=True, exist_ok=True)
    work = keep or Path(tempfile.mkdtemp(prefix="lody-137-"))
    data_dir, db = work / "data", work / "lody.sqlite3"
    shutil.copytree(ROOT / "resource" / "animation-assets", data_dir / "animation-assets", dirs_exist_ok=True)
    project, assets, character, id_map, warnings = setup_project(db)
    plan = audio.voice_plan(character)

    # 1. Voix provisoire, une synthèse par réplique ; repères de parole d'après les durées réelles.
    started = time.monotonic()
    clips, rate = {}, 22050
    for scene_id, lines in LINES.items():
        clips[scene_id] = []
        for i, (_, spoken) in enumerate(lines):
            clip, rate = base.synthesize(tts, model, spoken, work / f"tts-{scene_id}-{i}.wav")
            clips[scene_id].append(clip)
    layout, voice = fit_layout(clips, rate)
    tts_seconds = round(time.monotonic() - started, 1)

    # 2. Storyboard, validation, gel.
    storyboard = freeze_document(assets, project.id, remap_assets(build_storyboard(layout), id_map))
    total_seconds = sum(s["duration_frames"] for s in storyboard["scenes"]) / FPS

    # 3. Bande son : voix provisoire + musique de test générée (coupée à la durée exacte par le worker).
    sound = work / "sound"
    sound.mkdir(exist_ok=True)
    base.write_wav(sound / audio.VOICE_FILE, voice, rate)
    musique_test.write(sound / audio.MUSIC_FILE, total_seconds + 1)
    (sound / audio.INFO_FILE).write_text(json.dumps({
        "voice": {"provisional": True, "source": "Piper fr_FR-upmc-medium (locuteur pierre), synthèse locale",
                  "license": "CC BY-SA 4.0 (modèle)", "note": plan["message"]},
        "music": {"provisional": True, "source": "scripts/animation/musique_test.py (générée)",
                  "license": "aucune piste tierce"}}, ensure_ascii=False), encoding="utf-8")

    # 4. Fournisseur Animation 2D (#120) : préflight, tâche, worker avec le moteur Remotion.
    def launch(directory: Path) -> None:
        for name in (audio.VOICE_FILE, audio.MUSIC_FILE, audio.INFO_FILE):
            shutil.copyfile(sound / name, directory / name)
        w.run_job(directory, w.CommandRenderer(shlex.split(renderer)))

    connector = AnimationConnector(root=data_dir, asset_repo=assets, launcher=launch, renderer=renderer)
    request = build_request(project, "Voltix explique le câble SDI").with_updates(
        render_mode=modes.ANIMATION, animation={"project_id": project.id, "storyboard": storyboard})
    preflight = connector.preflight(request)
    blocking = [i.message for i in preflight.items if i.state.name not in ("READY", "NOT_NEEDED", "DISABLED")]
    if blocking:
        raise SystemExit("préflight bloquant : " + " | ".join(blocking))
    render_started = time.monotonic()
    task = connector.submit(request, "episode-137-voltix-sdi")
    directory = w.find_job_dir(data_dir, task.task_id)
    state = w.read_json(directory / "state.json") or {}
    if state.get("state") != "done":
        log = (directory / "worker.log").read_text(encoding="utf-8") if (directory / "worker.log").is_file() else ""
        raise SystemExit(f"rendu en échec : {state.get('error')}\n{log}")
    render_seconds = round(time.monotonic() - render_started, 1)

    for name in ("out.mp4", "manifest.json", "subtitles.srt"):
        shutil.copyfile(directory / name, out / ("voltix-137.mp4" if name == "out.mp4" else name))
    (out / "storyboard.json").write_text(json.dumps(storyboard, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {"task": task.task_id, "job_dir": str(directory), "tts_seconds": tts_seconds,
               "render_seconds": render_seconds, "duration_seconds": round(total_seconds, 2), "voice_plan": plan,
               "catalog_warnings": warnings,
               "scenes": {sid: {"offset": layout[sid]["offset"], "duration": layout[sid]["duration"],
                                "cues": layout[sid]["cues"]} for sid in layout}}
    (out / "episode.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--piper-model", type=Path)
    parser.add_argument("--tts", default="uvx --from piper-tts piper")
    parser.add_argument("--renderer", default=f"node {ROOT / 'renderers/remotion/bin/lody-remotion.mjs'}")
    parser.add_argument("--work", type=Path, default=None, help="dossier de travail conservé (sinon temporaire)")
    parser.add_argument("--storyboard-only", action="store_true", help="storyboard estimé, sans voix ni rendu")
    args = parser.parse_args()
    if args.storyboard_only:
        args.out.mkdir(parents=True, exist_ok=True)
        document = build_storyboard(estimated_layout())
        (args.out / "storyboard-estime.json").write_text(json.dumps(document, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
        print(sum(s["duration_frames"] for s in document["scenes"]) / FPS, "s (estimation)")
        return 0
    if not args.piper_model:
        parser.error("--piper-model est requis pour le rendu (ou --storyboard-only)")
    summary = run(args.out, args.piper_model, shlex.split(args.tts), args.renderer, args.work)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
