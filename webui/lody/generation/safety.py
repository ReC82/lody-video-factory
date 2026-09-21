"""Nettoyage des messages et validation des chemins : rien de secret n'est affiché ni stocké."""

from __future__ import annotations

import re
from pathlib import Path

from lody.generation.models import ErrorKind
from lody.secrets_guard import _SECRET_VALUE_PATTERNS

MASK = "[masqué]"
MAX_MESSAGE = 300

_AUTH_HEADER = re.compile(r"(?i)\b(x-api-key|authorization|api[_-]?key|token|secret)\b\s*[:=]\s*\S+")


def sanitize(text: object, limit: int = MAX_MESSAGE) -> str:
    """Retire tout motif de secret et borne la taille. À appliquer à tout texte externe."""
    cleaned = str(text or "").replace("\r", " ")
    cleaned = _AUTH_HEADER.sub(lambda match: f"{match.group(1)}: {MASK}", cleaned)
    for pattern in _SECRET_VALUE_PATTERNS:
        cleaned = pattern.sub(MASK, cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


_STAGE_LABELS = {
    "preflight": "les vérifications préalables",
    "script": "l’écriture du script",
    "terms": "la préparation des scènes",
    "audio": "la voix",
    "materials": "les images",
    "video": "le montage",
    "pipeline": "le traitement",
}

_RULES: tuple[tuple[re.Pattern[str], ErrorKind, str], ...] = (
    (re.compile(r"api[_ ]?key is not set|requires an? .*api key|missing .*key|no api key", re.I), ErrorKind.KEY_MISSING,
     "Une clé d’API nécessaire à cette génération n’est pas configurée côté serveur."),
    (re.compile(r"quota|insufficient|billing|credit|rate.?limit|too many requests|\b429\b|exceeded", re.I), ErrorKind.QUOTA,
     "Le fournisseur signale un quota ou un crédit insuffisant."),
    (re.compile(r"unauthori[sz]ed|invalid api key|incorrect api key|forbidden|\b40[13]\b", re.I), ErrorKind.AUTH,
     "Le fournisseur refuse la clé d’API configurée."),
    (re.compile(r"time.?out|timed out", re.I), ErrorKind.TIMEOUT,
     "Le fournisseur a mis trop de temps à répondre."),
)


def classify_engine_error(stage: str, raw: object) -> tuple[ErrorKind, str]:
    """(nature, message lisible) d'un échec rapporté par le moteur. Le texte brut n'est pas montré."""
    text = str(raw or "")
    where = _STAGE_LABELS.get(stage, "la génération")
    for pattern, kind, message in _RULES:
        if pattern.search(text):
            return kind, f"{message} (échec pendant {where})."
    return ErrorKind.PROVIDER, f"Un fournisseur a renvoyé une erreur pendant {where}."


_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{5,79}$")
ALLOWED_VIDEO_SUFFIXES = (".mp4", ".webm", ".mov")


def is_safe_task_id(value: object) -> bool:
    return isinstance(value, str) and bool(_TASK_ID.fullmatch(value))


def resolve_within(root: Path | str, relative: str, *, prefix: str = "", suffixes: tuple[str, ...] = ()) -> Path:
    """Chemin réel de ``relative`` sous ``root``, ou ``ValueError``.

    Refuse : chemins absolus, ``..``, octets nuls, backslash, liens symboliques sortant de ``root``,
    fichiers hors ``prefix`` (dossier de la production), extensions non prévues, non-fichiers.
    """
    if not isinstance(relative, str) or not relative or "\x00" in relative or "\\" in relative:
        raise ValueError("chemin invalide")
    if relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
        raise ValueError("chemin absolu refusé")
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("chemin non normalisé")
    if prefix and not relative.startswith(prefix.rstrip("/") + "/"):
        raise ValueError("fichier étranger à la production")
    base = Path(root).resolve()
    target = (base / relative).resolve()
    if base != target and base not in target.parents:
        raise ValueError("chemin hors du stockage")
    if prefix:
        allowed = (base / prefix.rstrip("/")).resolve()
        if allowed != target and allowed not in target.parents:
            raise ValueError("fichier étranger à la production")
    if suffixes and target.suffix.lower() not in suffixes:
        raise ValueError("type de fichier non autorisé")
    if not target.is_file():
        raise ValueError("fichier introuvable")
    return target
