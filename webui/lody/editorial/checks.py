"""Contrôles éditoriaux avant génération (#167) — niveau novice, script ↔ storyboard, texte parlé (TTS).

Trois familles de contrôles sur un document ``lody.preprod/1`` déjà validé (``preprod.validate_preprod``) :

1. **Niveau novice** (``brief``) : objectif central, connaissances supposées, UNE notion nouvelle, première
   occurrence de chaque mot nouveau expliquée, mots techniques non prévus (« dépasse le niveau novice »),
   exemples concrets, appel à l'action final sans technologie non enseignée.
2. **Script ↔ storyboard** : chaque scène a une description VISUELLE concrète ; « tu vois ce câble » exige un
   câble dans le visuel ; scènes de 5 à 7 s ; durée fondée sur le débit de parole, pas sur un nombre d'écrans.
3. **Texte parlé** : sigles et symboles sensibles pour la synthèse vocale, forme phonétique affichée à
   l'écran, pauses entre « 3G » et « SDI », voix ≠ sous-titres, consignes de production dans la voix.

Heuristiques volontairement simples et explicables (aucune analyse linguistique complète, aucun réseau) :
une alerte « à revoir » est une question posée à l'auteur, pas une erreur certaine. Rien n'est jamais
corrigé : chaque alerte porte une ``suggestion`` que l'auteur applique — ou écarte — lui-même.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any

from lody.editorial.preprod import brief_settings
from lody.generation import script_guard

BLOQUANT, A_REVOIR, INFO = "bloquant", "à revoir", "info"
LEVELS = (BLOQUANT, A_REVOIR, INFO)

# Vocabulaire technique de la vidéo et du broadcast qu'un novice ne connaît pas a priori. Liste éditable :
# un projet ajoute les siens par ``brief.extra_jargon`` ; un terme déclaré connu (``assumed_knowledge``) ou
# nouveau (``new_terms``) n'est jamais signalé. Les sigles (au moins deux majuscules : SDI, 3G-SDI, 4K…)
# sont détectés automatiquement en plus de cette liste.
JARGON = (
    "broadcast", "broadcasting", "régie", "mélangeur", "mélangeur vidéo", "grille de routage", "matrice",
    "routeur vidéo", "genlock", "tally", "timecode", "time code", "multiview", "prévisualisation", "preview",
    "trame", "impédance", "impédance caractéristique", "coaxial", "câble coaxial", "BNC", "SDI", "HD-SDI",
    "3G-SDI", "6G-SDI", "12G-SDI", "HDMI", "débit", "bande passante", "convertisseur", "synchroniseur",
    "signal de référence", "fibre optique", "audio embarqué", "son embarqué", "latence", "entrelacé",
    "progressif", "cadence", "conducteur central", "blindage", "diélectrique", "baïonnette", "terminaison",
    "atténuation", "codec", "compression", "interface numérique série", "Serial Digital Interface",
    "multiplexage", "désembarqueur", "embarqueur", "resynchroniser", "verrouillage de phase", "4K", "8K",
    "UHD", "HDR", "SMPTE", "NDI", "IP", "XLR", "Gbit/s", "Mbit/s",
)
# Sigles du quotidien, jamais signalés comme jargon.
COMMON_ACRONYMS = ("tv", "ok", "usb", "pc", "wifi", "wi-fi", "dvd", "gps", "sms")

EXPLAIN_MARKERS = ("c'est", "c'est-a-dire", "autrement dit", "veut dire", "s'appelle", "on appelle",
                   "appele", "signifie", "sert a", "servent a", "permet", "qui ", ":", "(", " — ", " comme ",
                   "designe", "imagine", "pareil", "un peu comme")
DEICTIC_CUE = re.compile(r"\b(?:vois|voyez|regarde|regardez|voici|voila|ici|observe|observez|la-bas)\b")
DEMONSTRATIVE = re.compile(
    r"\b(?:ce|cet|cette|ces)\s+(?:(?:petit|petite|petits|petites|grand|grande|gros|grosse|meme|drole de)\s+)?"
    r"([a-z][a-z-]{2,})")
NOT_VISUAL_NOUNS = {"qui", "que", "quoi", "dont", "sont", "est", "soir", "matin", "fois", "moment", "episode",
                    "video", "videos", "sujet", "question", "idee", "histoire", "jour", "semaine", "point"}
SYMBOLS = {"Ω": "« ohms »", "%": "« pour cent »", "&": "« et »", "/": "« sur » ou « par »", "→": "une phrase",
           "+": "« plus »", "=": "« égale »", "°": "« degrés »", "≠": "« différent de »", "<": "« moins que »",
           ">": "« plus que »", "#": "« dièse »", "@": "« arobase »", "€": "« euros »", "$": "« dollars »"}
UNIT = re.compile(r"\d+(?:[.,]\d+)?\s*(?:Gbit/s|Mbit/s|Gb/s|Mb/s|GHz|MHz|kHz|Hz|fps|dB)\b", re.IGNORECASE)
ACRONYM = re.compile(r"(?:\d+[A-Z]{1,2}|[A-Z0-9]*[A-Z][A-Z0-9]*)(?:-[A-Z0-9]+)*")
TOKEN = re.compile(r"[\w-]+")
PAUSE_SPLIT = (re.compile(r"\b(?:HD|\d+\s?G)\s*[,;:–—]\s*SDI\b"),
               re.compile(r"\b(?:hache de|\w+ g|\d+ g)\s*[,;–—]\s*(?:esse?|ess|sdi)\b"))
CAPTION_MAX = 84  # deux lignes de 42 caractères, norme usuelle des sous-titres
VAGUE_VISUAL_WORDS = 4
SILENT_GAP_S = 3.0
OVERFLOW_TOLERANCE_S = 0.2


@dataclass(frozen=True)
class Finding:
    """Une alerte, rattachée si possible à une scène et à un champ précis du document."""

    level: str
    code: str
    message: str
    scene: str | None = None
    path: str = ""
    suggestion: str = ""


@dataclass
class SceneTiming:
    id: str
    words: int
    speech_s: float
    planned_s: float
    measured: bool


@dataclass
class Report:
    verdict: str
    findings: list[Finding]
    timings: list[SceneTiming]
    speech_total_s: float
    planned_total_s: float
    new_terms: list[str]
    settings: dict[str, Any] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {level: sum(1 for f in self.findings if f.level == level) for level in LEVELS}

    def for_scene(self, scene_id: str) -> list[Finding]:
        return [f for f in self.findings if f.scene == scene_id]

    def as_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "counts": self.counts(), "speech_total_s": self.speech_total_s,
                "planned_total_s": self.planned_total_s, "new_terms": self.new_terms,
                "settings": {**self.settings, "scene_seconds": list(self.settings.get("scene_seconds", ())),
                             "target_seconds": list(self.settings["target_seconds"])
                             if self.settings.get("target_seconds") else None},
                "timings": [asdict(t) for t in self.timings], "findings": [asdict(f) for f in self.findings]}


# -- normalisation ---------------------------------------------------------------------------------------
def norm(text: str) -> str:
    """Minuscules, sans accents, apostrophes droites, espaces simples : base de toutes les comparaisons."""
    text = unicodedata.normalize("NFKD", str(text or "")).replace("’", "'").replace("‘", "'")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def _term_pattern(term_norm: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])" + re.escape(term_norm) + r"(?:s|x)?(?![\w-])")


def _contains(text_norm: str, term_norm: str) -> bool:
    return bool(term_norm) and bool(_term_pattern(term_norm).search(text_norm))


def _sentences(text_norm: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+", text_norm) if s.strip()]


def _words(text: str) -> int:
    return len(TOKEN.findall(str(text or "").replace("'", " ").replace("’", " ")))


def _is_acronym(token: str) -> bool:
    """Sigle sensible : au moins deux majuscules (SDI, HD-SDI) ou chiffre + lettre(s) (3G, 4K), hors sigles du
    quotidien."""
    if norm(token) in COMMON_ACRONYMS:
        return False
    return bool(ACRONYM.fullmatch(token) and sum(ch.isupper() for ch in token) >= 2) or bool(
        re.fullmatch(r"\d+[A-Za-z]{1,2}", token))


def _plain(text_norm: str) -> str:
    return " ".join(re.sub(r"[^\w\s'-]", " ", text_norm).split())


class _Context:
    """Données préparées une fois : formes normalisées, prononciations, termes couverts."""

    def __init__(self, document: dict[str, Any]):
        self.brief = document["brief"]
        self.scenes = document["scenes"]
        self.settings = brief_settings(self.brief)
        self.pronunciations = {str(k): str(v) for k, v in (self.brief.get("pronunciations") or {}).items()}
        self.new_terms = [t for t in self.brief.get("new_terms") or [] if str(t.get("term", "")).strip()]
        self.new_forms = {norm(t["term"]): [norm(t["term"]), *(norm(a) for a in t.get("aliases") or [] if a)]
                          for t in self.new_terms}
        known = [norm(t) for t in self.brief.get("assumed_knowledge") or [] if str(t).strip()]
        self.covered = {*known, *COMMON_ACRONYMS, *(f for forms in self.new_forms.values() for f in forms)}
        raw_terms = [*(self.brief.get("assumed_knowledge") or []), *(t["term"] for t in self.new_terms),
                     *(a for t in self.new_terms for a in t.get("aliases") or [])]
        self.covered_acronyms = {norm(t) for t in raw_terms if _is_acronym(str(t))}
        extra = [str(t) for t in self.brief.get("extra_jargon") or [] if str(t).strip()]
        self.display = {norm(t): t for t in (*JARGON, *extra)}
        self.jargon = sorted({norm(t) for t in (*JARGON, *(self.brief.get("extra_jargon") or [])) if str(t).strip()},
                             key=len, reverse=True)
        # Forme parlée → écrit : relit le texte parlé comme on l'écrirait (« èss di aï » → « sdi »).
        self.unphonetic = sorted(((norm(v), norm(k)) for k, v in self.pronunciations.items()
                                  if norm(v) and norm(v) != norm(k)), key=lambda kv: len(kv[0]), reverse=True)

    def reading(self, scene: dict[str, Any]) -> str:
        """Texte de la scène tel qu'on le LIT (sous-titres s'ils existent, sinon voix ramenée à l'écrit)."""
        if scene["captions"]:
            return norm(" ".join(scene["captions"]))
        return self.written(scene["spoken"])

    def written(self, spoken: str) -> str:
        text = norm(spoken)
        for spoken_form, written in self.unphonetic:
            text = re.sub(r"(?<![\w-])" + re.escape(spoken_form) + r"(?![\w-])", written, text)
        return text

    def is_covered(self, term_norm: str) -> bool:
        """Couvert si déclaré tel quel, si le terme fait partie d'une expression déclarée (« mélangeur » dans
        « mélangeur vidéo »), ou s'il PRÉCISE un terme technique déclaré (« impédance caractéristique » quand
        « impédance » est enseignée) — jamais parce qu'il contient un mot courant (« vidéo »)."""
        return any(c == term_norm or _contains(c, term_norm)
                   or (_contains(term_norm, c) and (c in self.jargon or c in self.covered_acronyms))
                   for c in self.covered)


# -- 1. niveau novice ----------------------------------------------------------------------------------------
def _found_terms(ctx: _Context, scene: dict[str, Any]) -> dict[str, str]:
    """Mots techniques d'une scène → champ où ils apparaissent (``captions``/``spoken``/``displayed``)."""
    found: dict[str, str] = {}
    for fieldname, text_norm in (("spoken" if not scene["captions"] else "captions", ctx.reading(scene)),
                                 ("displayed", norm(" ".join(scene["displayed"])))):
        taken: list[tuple[int, int]] = []
        for term in ctx.jargon:  # du plus long au plus court : « impédance caractéristique » avant « impédance »
            for match in _term_pattern(term).finditer(text_norm):
                if any(match.start() < end and start < match.end() for start, end in taken):
                    continue
                taken.append(match.span())
                found.setdefault(term, fieldname)
    for fieldname, texts in (("captions", scene["captions"]), ("displayed", scene["displayed"]),
                             ("spoken", [scene["spoken"]])):
        for text in texts:
            for token in TOKEN.findall(text):
                token = token.strip("-")
                if _is_acronym(token):
                    term = norm(token)
                    if not any(_contains(existing, term) for existing in found):
                        found.setdefault(term, fieldname)
                        ctx.display.setdefault(term, token)
    return found


def _check_brief(ctx: _Context, out: list[Finding]) -> None:
    brief = ctx.brief
    if not str(brief.get("objective", "")).strip():
        out.append(Finding(BLOQUANT, "objectif-absent", "Objectif central absent : en une phrase, que doit retenir "
                           "le spectateur ?", path="brief.objective",
                           suggestion="Ex. : « Comprendre que le SDI est le câble vidéo des pros, comme le HDMI à "
                           "la maison. »"))
    if "audience" not in brief:
        out.append(Finding(INFO, "public-implicite", "Niveau du public non précisé : contrôle au niveau « novice ».",
                           path="brief.audience"))
    if not brief.get("assumed_knowledge"):
        out.append(Finding(A_REVOIR, "connaissances-absentes", "Connaissances supposées du spectateur non "
                           "déclarées : impossible de savoir quels mots il connaît déjà.",
                           path="brief.assumed_knowledge", suggestion="Ex. : « télé », « box », « HDMI »."))
    if not ctx.new_terms:
        out.append(Finding(A_REVOIR, "notion-absente", "Aucune notion nouvelle déclarée : quel mot l'épisode "
                           "apprend-il ?", path="brief.new_terms"))
    elif ctx.settings["audience"] == "novice" and len(ctx.new_terms) > 1:
        extras = ", ".join(f"« {t['term']} »" for t in ctx.new_terms[1:])
        out.append(Finding(A_REVOIR, "plusieurs-notions", f"{len(ctx.new_terms)} notions nouvelles pour un public "
                           f"novice : une seule par épisode (« {ctx.new_terms[0]['term']} »).",
                           path="brief.new_terms[1]",
                           suggestion=f"Réserver {extras} aux épisodes suivants de la série."))
    for index, term in enumerate(ctx.new_terms):
        if not str(term.get("explanation", "")).strip():
            out.append(Finding(A_REVOIR, "explication-absente", f"« {term['term']} » : explication simple non "
                               "déclarée dans le brief.", path=f"brief.new_terms[{index}].explanation"))
    if not brief.get("examples"):
        out.append(Finding(A_REVOIR, "exemple-absent", "Aucun exemple concret déclaré (objet du quotidien, "
                           "situation vécue).", path="brief.examples"))
    cta = str(brief.get("cta", "")).strip()
    if not cta:
        out.append(Finding(A_REVOIR, "cta-absent", "Appel à l'action final absent : comment l'épisode ouvre-t-il "
                           "le suivant ?", path="brief.cta",
                           suggestion="Une question de curiosité, sans nommer une technologie pas encore "
                           "expliquée."))
        return
    pseudo = {"captions": [], "displayed": [], "spoken": cta}
    untaught = sorted(ctx.display.get(t, t) for t in _found_terms(ctx, pseudo) if not ctx.is_covered(t))
    if untaught:
        out.append(Finding(A_REVOIR, "cta-non-enseigne", "L'appel à l'action expose un terme non enseigné : "
                           + ", ".join(f"« {t} »" for t in untaught) + ".", path="brief.cta",
                           suggestion="Teaser par une question concrète (« Et comment le présentateur sait "
                           "quelle caméra le filme ? ») plutôt que par le nom technique."))
    last = ctx.scenes[-1]
    if SequenceMatcher(None, _plain(norm(cta)), _plain(ctx.reading(last))).find_longest_match().size < min(
            20, len(_plain(norm(cta)))):
        out.append(Finding(A_REVOIR, "cta-hors-scene", "L'appel à l'action déclaré n'apparaît pas dans la "
                           "dernière scène.", scene=last["id"], path=f"scenes[{len(ctx.scenes) - 1}].spoken"))


def _check_vocabulary(ctx: _Context, out: list[Finding]) -> None:
    first_seen: dict[str, tuple[int, str]] = {}
    scenes_of: dict[str, list[str]] = {}
    for index, scene in enumerate(ctx.scenes):
        for term, fieldname in _found_terms(ctx, scene).items():
            first_seen.setdefault(term, (index, fieldname))
            scenes_of.setdefault(term, []).append(scene["id"])
    novice = ctx.settings["audience"] == "novice"
    for term, (index, fieldname) in first_seen.items():
        if ctx.is_covered(term):
            continue
        others = [s for s in scenes_of[term] if s != ctx.scenes[index]["id"]]
        where = f" (aussi : {', '.join(others)})" if others else ""
        out.append(Finding(
            A_REVOIR if novice else INFO, "hors-niveau",
            f"Terme technique non prévu par le brief : « {ctx.display.get(term, term)} »{where}"
            + (" — dépasse le niveau novice." if novice else "."),
            scene=ctx.scenes[index]["id"], path=f"scenes[{index}].{fieldname}",
            suggestion="Le supprimer, le remplacer par un mot du quotidien, ou le déclarer comme notion de "
                       "l'épisode (une seule) ; sinon le réserver à un épisode suivant."))

    for t_index, term in enumerate(ctx.new_terms):
        forms = ctx.new_forms[norm(term["term"])]
        located, shown_early = None, False
        for index, scene in enumerate(ctx.scenes):
            reading, displayed = ctx.reading(scene), norm(" ".join(scene["displayed"]))
            sentences = _sentences(reading)
            hit = next((i for i, s in enumerate(sentences) if any(_contains(s, f) for f in forms)), None)
            if hit is not None:
                located = (index, sentences, hit)
                break
            if not shown_early and any(_contains(displayed, f) for f in forms):
                shown_early = True
                out.append(Finding(A_REVOIR, "affiche-avant-dit", f"« {term['term']} » apparaît à l'écran avant "
                                   "d'être dit et expliqué.", scene=scene["id"], path=f"scenes[{index}].displayed",
                                   suggestion="Le faire apparaître au moment où la voix l'explique."))
        if located is None:
            out.append(Finding(A_REVOIR, "notion-jamais-dite", f"Notion déclarée mais jamais prononcée : "
                               f"« {term['term']} ».", path=f"brief.new_terms[{t_index}]"))
            continue
        index, sentences, hit = located
        window = sentences[hit:hit + 2]
        if not any(marker in s for s in window for marker in EXPLAIN_MARKERS):
            out.append(Finding(A_REVOIR, "premiere-occurrence", f"Première occurrence de « {term['term']} » sans "
                               "explication visible dans la phrase ou la suivante.", scene=ctx.scenes[index]["id"],
                               path=f"scenes[{index}].spoken",
                               suggestion=f"Expliquer dans la même phrase : « {term['term']}, c'est… » "
                               + (f"(brief : {term['explanation']})" if term.get("explanation") else "")))


# -- 2. script ↔ storyboard ----------------------------------------------------------------------------------
def _check_scenes(ctx: _Context, out: list[Finding]) -> list[SceneTiming]:
    low, high = ctx.settings["scene_seconds"]
    wps = ctx.settings["words_per_second"]
    timings: list[SceneTiming] = []
    for index, scene in enumerate(ctx.scenes):
        sid, base = scene["id"], f"scenes[{index}]"
        visual = norm(scene["visual"])
        if not visual:
            out.append(Finding(BLOQUANT, "visuel-absent", "Description visuelle absente : que voit-on pendant "
                               "cette phrase ?", scene=sid, path=f"{base}.visual"))
        elif _words(visual) < VAGUE_VISUAL_WORDS:
            out.append(Finding(A_REVOIR, "visuel-vague", "Description visuelle trop vague pour être dessinée.",
                               scene=sid, path=f"{base}.visual",
                               suggestion="Nommer l'objet montré, son état et ce qui bouge."))
        if visual:
            seen_text = visual + " " + norm(" ".join(scene["displayed"]))
            for sentence in _sentences(ctx.reading(scene)):
                if not DEICTIC_CUE.search(sentence):
                    continue
                for noun in DEMONSTRATIVE.findall(sentence):
                    singular = noun[:-1] if noun.endswith(("s", "x")) and len(noun) > 4 else noun
                    if singular in NOT_VISUAL_NOUNS:
                        continue
                    stem = singular[:max(4, len(singular) - 1)]
                    if stem not in seen_text:
                        out.append(Finding(A_REVOIR, "montre-sans-visuel", f"La voix montre « {noun} » mais la "
                                           "description visuelle ne le contient pas.", scene=sid,
                                           path=f"{base}.visual",
                                           suggestion=f"Montrer réellement « {noun} » à l'écran, ou changer la "
                                           "phrase."))
        spoken_text = scene["spoken"] or " ".join(scene["captions"])
        words = _words(spoken_text)
        measured = "audio_s" in scene
        speech = float(scene["audio_s"]) if measured else round(words / wps, 2)
        planned = float(scene.get("duration_s") or round(max(speech + 0.5, 0.5), 2))
        timings.append(SceneTiming(sid, words, speech, planned, measured))
        if words and speech > planned + OVERFLOW_TOLERANCE_S:
            out.append(Finding(BLOQUANT, "voix-deborde", f"La voix ({speech:.1f} s) ne tient pas dans la scène "
                               f"({planned:.1f} s).", scene=sid, path=f"{base}.duration_s",
                               suggestion="Raccourcir la phrase ou allonger la scène."))
        if planned < low - 0.05:
            out.append(Finding(A_REVOIR, "scene-courte", f"Scène de {planned:.1f} s (cible {low:g}–{high:g} s).",
                               scene=sid, path=f"{base}.duration_s",
                               suggestion="La fusionner avec sa voisine ou y ajouter une idée visuelle."))
        elif planned > high + 0.05:
            out.append(Finding(A_REVOIR, "scene-longue", f"Scène de {planned:.1f} s (cible {low:g}–{high:g} s).",
                               scene=sid, path=f"{base}.duration_s",
                               suggestion="La scinder : une idée visuelle nouvelle toutes les 5 à 7 s."))
        if words and planned - speech > SILENT_GAP_S:
            out.append(Finding(A_REVOIR, "ecran-sans-voix", f"{planned - speech:.1f} s d'écran sans parole : la "
                               "durée doit suivre la voix, pas un nombre d'écrans.", scene=sid,
                               path=f"{base}.duration_s"))
        elif not words:
            out.append(Finding(INFO, "scene-muette", "Scène sans parole : vérifier qu'elle est voulue.", scene=sid,
                               path=f"{base}.spoken"))
    target = ctx.settings["target_seconds"]
    speech_total = sum(t.speech_s for t in timings)
    if target and not target[0] <= speech_total <= target[1]:
        out.append(Finding(A_REVOIR, "duree-totale", f"Parole totale estimée à {speech_total:.1f} s, hors de la "
                           f"cible {target[0]:g}–{target[1]:g} s.", path="brief.target_seconds",
                           suggestion="Ajuster le texte, pas le nombre de scènes."))
    return timings


# -- 3. texte parlé (TTS) ------------------------------------------------------------------------------------
def _check_speech(ctx: _Context, out: list[Finding]) -> None:
    declared_seen: set[str] = set()
    undeclared_seen: set[str] = set()
    declared = {norm(k): v for k, v in ctx.pronunciations.items()}
    phonetic_forms = [(norm(v), k) for k, v in ctx.pronunciations.items() if len(norm(v)) >= 4
                      and norm(v) != norm(k)]
    for index, scene in enumerate(ctx.scenes):
        sid, base, spoken = scene["id"], f"scenes[{index}]", scene["spoken"]
        for reason in _metadata_issues(spoken):
            out.append(Finding(BLOQUANT, "consigne-dans-voix", f"Le texte parlé contient {reason} : ce n'est pas "
                               "un texte à prononcer.", scene=sid, path=f"{base}.spoken"))
        for token in TOKEN.findall(spoken):
            token = token.strip("-")
            if not _is_acronym(token):
                continue
            key = norm(token)
            if key in declared:
                if key not in declared_seen:
                    declared_seen.add(key)
                    out.append(Finding(INFO, "prononciation-declaree", f"« {token} » sera lu « {declared[key]} » "
                                       "si le lexique est appliqué : écoute à valider avant rendu.", scene=sid,
                                       path=f"{base}.spoken"))
            elif key not in undeclared_seen:
                undeclared_seen.add(key)
                out.append(Finding(A_REVOIR, "tts-sigle", f"Sigle « {token} » dans le texte parlé sans prononciation "
                                   "déclarée : la synthèse risque de l'épeler ou de l'écorcher.", scene=sid,
                                   path=f"{base}.spoken",
                                   suggestion="Déclarer sa forme parlée dans brief.pronunciations et l'écouter "
                                   "(lexique #162)."))
        for symbol, reading in SYMBOLS.items():
            if symbol in spoken and not UNIT.search(spoken):
                out.append(Finding(A_REVOIR, "tts-symbole", f"Symbole « {symbol} » dans le texte parlé.", scene=sid,
                                   path=f"{base}.spoken", suggestion=f"L'écrire en toutes lettres : {reading}."))
        if UNIT.search(spoken):
            out.append(Finding(A_REVOIR, "tts-unite", f"Valeur avec unité abrégée « {UNIT.search(spoken).group(0)} » "
                               "dans le texte parlé.", scene=sid, path=f"{base}.spoken",
                               suggestion="L'écrire comme on la dit (« douze gigabits par seconde ») — ou la "
                               "retirer si elle dépasse le niveau du public."))
        if PAUSE_SPLIT[0].search(spoken) or PAUSE_SPLIT[1].search(norm(spoken)):
            out.append(Finding(A_REVOIR, "tts-pause", "Ponctuation entre « HD/3G/12G » et « SDI » : la voix marquera "
                               "une pause artificielle.", scene=sid, path=f"{base}.spoken",
                               suggestion="Écrire le groupe d'un seul tenant, sans virgule."))
        for fieldname in ("captions", "displayed"):
            for t_index, text in enumerate(scene[fieldname]):
                text_norm = norm(text)
                for form, written in phonetic_forms:
                    if _contains(text_norm, form):
                        out.append(Finding(BLOQUANT, "phonetique-affichee", f"Forme phonétique « {form} » affichée "
                                           f"à l'écran : l'écran doit montrer « {written} ».", scene=sid,
                                           path=f"{base}.{fieldname}[{t_index}]"))
                if fieldname == "captions" and len(text) > CAPTION_MAX:
                    out.append(Finding(A_REVOIR, "sous-titre-long", f"Sous-titre de {len(text)} caractères "
                                       f"(> {CAPTION_MAX}, soit deux lignes).", scene=sid,
                                       path=f"{base}.captions[{t_index}]", suggestion="Le couper en deux."))
        if spoken.strip() and scene["captions"]:
            said, shown = _plain(ctx.written(spoken)), _plain(norm(" ".join(scene["captions"])))
            if SequenceMatcher(None, said, shown).ratio() < 0.8:
                out.append(Finding(A_REVOIR, "voix-sous-titres", "La voix et les sous-titres ne disent pas la même "
                                   "chose.", scene=sid, path=f"{base}.captions",
                                   suggestion="Les sous-titres reprennent la voix mot pour mot, à l'écriture "
                                   "exacte près (sigles)."))


def _metadata_issues(text: str) -> list[str]:
    """Motifs de consignes de production du garde-fou #76 (``generation.script_guard``), sans ``Production``."""
    issues = []
    if script_guard._EPISODE_TITLE.search(text):
        issues.append("un titre d'épisode")
    if script_guard._SCENE_MARKER.search(text):
        issues.append("un numéro de scène")
    if script_guard._DURATION.search(text):
        issues.append("une durée de production")
    if script_guard._FORMAT_CODE.search(text):
        issues.append("un format vidéo")
    if script_guard._technical_phrase_hit(text.lower()):
        issues.append("une consigne de production")
    return issues


# -- API -----------------------------------------------------------------------------------------------------
def check_document(document: dict[str, Any]) -> Report:
    """Contrôle un document ``lody.preprod/1`` VALIDÉ. Ne modifie jamais ``document``.

    Verdict : « à corriger » si une alerte bloquante, « à relire » s'il reste des alertes « à revoir »,
    « prêt » sinon. Les alertes sont triées : brief d'abord, puis scène par scène, dans l'ordre du document."""
    ctx = _Context(document)
    findings: list[Finding] = []
    _check_brief(ctx, findings)
    _check_vocabulary(ctx, findings)
    timings = _check_scenes(ctx, findings)
    _check_speech(ctx, findings)
    order = {scene["id"]: index for index, scene in enumerate(ctx.scenes)}
    findings.sort(key=lambda f: (-1 if f.scene is None else order[f.scene], LEVELS.index(f.level)))
    levels = {f.level for f in findings}
    verdict = "à corriger" if BLOQUANT in levels else "à relire" if A_REVOIR in levels else "prêt"
    return Report(verdict, findings, timings, round(sum(t.speech_s for t in timings), 2),
                  round(sum(t.planned_s for t in timings), 2), [t["term"] for t in ctx.new_terms], ctx.settings)


def codes(findings: Iterable[Finding]) -> set[str]:
    return {f.code for f in findings}
