"""Sous-titres séparés (SRT / VTT) à partir du SRT DÉJÀ produit par le moteur : aucun appel, aucun coût.

Les minutages sont ceux du SRT que le moteur a incrusté dans la vidéo : ils correspondent donc à la vidéo finale. Ce module
ne fait que : lire, corriger la typographie française (aucune espace après une apostrophe d'élision), vérifier la
cohérence avec la durée réelle, et écrire SRT (UTF-8) et VTT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lody.generation import typography

TOLERANCE_MS = 120  # un sous-titre peut dépasser la durée d'une fraction d'image sans conséquence
MAX_BYTES = 1_000_000


@dataclass(frozen=True)
class Cue:
    index: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class SubtitleCheck:
    """Résultat des contrôles ; ``ok`` est faux dès qu'une anomalie reste après correction automatique."""

    ok: bool
    issues: tuple[str, ...]
    fixes: tuple[str, ...]
    last_end_ms: int
    duration_ms: int | None


def parse_srt(content: str) -> list[Cue]:
    """Lit un SRT (BOM, CRLF, virgule ou point décimal tolérés). Les blocs sans minutage valide sont ignorés."""
    text = content[:MAX_BYTES].lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n{2,}", text.strip()):
        lines = block.split("\n")
        timing_at = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_at is None:
            continue
        match = re.match(r"^\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})", lines[timing_at])
        if not match:
            continue
        start = ((int(match.group(1)) * 60 + int(match.group(2))) * 60 + int(match.group(3))) * 1000 + int(match.group(4).ljust(3, "0")[:3])
        end = ((int(match.group(5)) * 60 + int(match.group(6))) * 60 + int(match.group(7))) * 1000 + int(match.group(8).ljust(3, "0")[:3])
        body = "\n".join(line.strip() for line in lines[timing_at + 1:] if line.strip())
        cues.append(Cue(len(cues) + 1, start, end, body))
    return cues


def normalize_cues(cues: list[Cue], language: str) -> list[Cue]:
    """Typographie française sur le texte seulement (jamais sur les minutages) ; renumérote."""
    return [Cue(i, cue.start_ms, cue.end_ms, typography.normalize_for_language(cue.text, language)) for i, cue in enumerate(cues, 1)]


def fit_to_duration(cues: list[Cue], duration_ms: int | None) -> tuple[list[Cue], list[str]]:
    """Garantit des minutages cohérents avec la vidéo : ordonnés, sans chevauchement, aucun après la dernière image."""
    fixes: list[str] = []
    result: list[Cue] = []
    previous_end = 0
    for cue in sorted(cues, key=lambda item: (item.start_ms, item.end_ms)):
        if not cue.text.strip():
            fixes.append(f"sous-titre vide n°{cue.index} retiré")
            continue
        start, end = max(cue.start_ms, previous_end), cue.end_ms
        if start != cue.start_ms:
            fixes.append(f"chevauchement corrigé au sous-titre n°{cue.index}")
        if duration_ms is not None and start >= duration_ms:
            fixes.append(f"sous-titre n°{cue.index} commençant après la fin de la vidéo retiré")
            continue
        if duration_ms is not None and end > duration_ms:
            end = duration_ms
            fixes.append(f"fin du sous-titre n°{cue.index} ramenée à la durée de la vidéo")
        if end <= start:
            fixes.append(f"sous-titre n°{cue.index} sans durée retiré")
            continue
        result.append(Cue(len(result) + 1, start, end, cue.text))
        previous_end = end
    return result, fixes


def check(cues: list[Cue], duration_ms: int | None, fixes: tuple[str, ...] = ()) -> SubtitleCheck:
    """Anomalies restantes (après ``fit_to_duration``) : liste vide = sous-titres exploitables tels quels."""
    issues: list[str] = []
    if not cues:
        issues.append("aucun sous-titre")
    last = cues[-1].end_ms if cues else 0
    previous_end = 0
    for cue in cues:
        if cue.start_ms < previous_end:
            issues.append(f"chevauchement au sous-titre n°{cue.index}")
        if cue.end_ms <= cue.start_ms:
            issues.append(f"durée nulle au sous-titre n°{cue.index}")
        if typography.normalize_french_text(cue.text) != cue.text:  # une élision suivie d'une espace subsiste
            issues.append(f"espace après une apostrophe au sous-titre n°{cue.index}")
        previous_end = max(previous_end, cue.end_ms)
    if duration_ms is not None and last > duration_ms + TOLERANCE_MS:
        issues.append("un sous-titre dépasse la durée de la vidéo")
    return SubtitleCheck(not issues, tuple(issues), tuple(fixes), last, duration_ms)


def _stamp(ms: int, separator: str) -> str:
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def to_srt(cues: list[Cue]) -> str:
    return "".join(f"{cue.index}\n{_stamp(cue.start_ms, ',')} --> {_stamp(cue.end_ms, ',')}\n{cue.text}\n\n" for cue in cues)


def to_vtt(cues: list[Cue]) -> str:
    """WebVTT : en-tête ``WEBVTT``, point décimal, sans numéros ; le texte est échappé pour les caractères réservés."""
    def escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;")

    body = "".join(f"{_stamp(cue.start_ms, '.')} --> {_stamp(cue.end_ms, '.')}\n{escape(cue.text)}\n\n" for cue in cues)
    return "WEBVTT\nKind: captions\nLanguage: fr\n\n" + body if body else "WEBVTT\n\n"


def plain_text(cues: list[Cue]) -> str:
    return "\n".join(cue.text.replace("\n", " ") for cue in cues)


def build(content: str, language: str, duration_ms: int | None) -> tuple[list[Cue], SubtitleCheck]:
    """Chaîne complète : lecture → typographie → cohérence avec la durée → contrôle."""
    cues, fixes = fit_to_duration(normalize_cues(parse_srt(content), language), duration_ms)
    return cues, check(cues, duration_ms, tuple(fixes))
