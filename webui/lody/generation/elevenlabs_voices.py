"""Catalogue recherchable des voix ElevenLabs accessibles au compte configuré (ticket #55).

Vérifié auprès de la documentation officielle ElevenLabs (v2, janvier 2026) :

* endpoint : ``GET https://api.elevenlabs.io/v2/voices`` ;
* authentification : en-tête ``xi-api-key: <clé>`` (jamais un paramètre d'URL — ne fuit donc jamais dans
  un journal d'accès ou un historique de navigateur) ;
* pagination par curseur : ``page_size`` (défaut 10, max 100) + ``next_page_token`` ; la réponse porte
  ``voices``, ``has_more``, ``next_page_token``, ``total_count`` ;
* recherche : paramètre ``search`` (nom, description, labels, catégorie) — utilisé ici pour la recherche
  serveur ; une recherche client (``search_voices``) est aussi fournie pour filtrer un catalogue déjà en
  cache sans nouvel appel ;
* chaque voix porte au moins ``voice_id``, ``name`` ; selon les comptes/voix, aussi ``category``, ``labels``
  (paire clé/valeur libre : langue, accent, genre, âge, cas d'usage…), ``preview_url`` (URL publique d'un
  extrait audio, ne nécessite PAS la clé API pour être lue — voir ``VoiceInfo.preview_url``) ;
* erreurs documentées : 422 (paramètres invalides) ; 401/403 constatés en pratique pour une clé absente,
  invalide, ou un compte sans droit sur ce catalogue (IP non autorisée notamment) ; 429 en cas de
  dépassement de débit. Aucune limite de débit précise n'est publiée pour cet endpoint : ce module respecte
  l'en-tête ``Retry-After`` s'il est présent, sinon affiche un message générique.

Modèle de sécurité — LA CLÉ N'EST JAMAIS ENVOYÉE AU NAVIGATEUR NI ÉCRITE NULLE PART :

* ``fetch_all``/``fetch_page`` reçoivent la clé en paramètre et l'utilisent uniquement dans l'en-tête HTTP
  sortant de CETTE requête ; elle n'est jamais journalisée (aucun ``logger``/``print`` de ``headers`` ou
  ``api_key`` dans ce module) ni conservée au-delà de l'appel.
* ``resolve_catalog`` (utilisée par l'interface) ne lit JAMAIS la clé elle-même : comme
  ``engine_facts.resolve()``, elle tente d'abord de lire ``config.toml`` directement (si le conteneur en a
  le droit), extrait la clé dans une variable locale le temps du seul appel HTTP, puis l'oublie. Si
  ``config.toml`` n'est pas lisible (cas actuel du déploiement — voir docs/lody-secrets.md), elle retombe
  sur un **rapport assaini** produit côté hôte (``write_report``/CLI ci-dessous, même mécanisme que
  ``engine_facts.py`` pour le rapport de capacités) : ce fichier ne contient QUE des métadonnées publiques
  de voix (voice_id, nom, catégorie, labels, preview_url) — jamais la clé.
* ``VoiceInfo``/``CatalogResult`` n'ont aucun champ secret par construction : impossible d'y stocker une clé
  par erreur (mêmes principes que ``EngineFacts``).

Le catalogue est entièrement optionnel et son indisponibilité ne bloque jamais rien : ``resolve_catalog``
ne lève jamais, elle renvoie un ``CatalogResult`` avec ``error`` renseigné et ``voices == ()`` — la saisie
manuelle du ``voice_id`` reste alors le seul chemin, exactement comme avant ce ticket.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import settings

logger = logging.getLogger("lody.elevenlabs_voices")

API_BASE = "https://api.elevenlabs.io/v2/voices"
DEFAULT_PAGE_SIZE = 100
MAX_PAGES = 20  # garde-fou : 2000 voix en pagination complète largement au-delà d'un usage réel
DEFAULT_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 5_000_000
CACHE_TTL_SECONDS = 900  # 15 minutes : raisonnable pour un catalogue qui change rarement
REPORT_VERSION = 1

Transport = Callable[[str, dict[str, str], dict[str, str], float], tuple[int, bytes, dict[str, str]]]


# -- modèle -----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class VoiceInfo:
    """Une voix, uniquement des métadonnées publiques — jamais de clé, jamais d'identifiant interne à Lody."""

    voice_id: str
    name: str
    category: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    preview_url: str = ""  # URL publique ElevenLabs : lisible par le navigateur SANS la clé API

    def to_dict(self) -> dict[str, Any]:
        return {"voice_id": self.voice_id, "name": self.name, "category": self.category,
                "labels": dict(self.labels), "preview_url": self.preview_url}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> VoiceInfo:
        return VoiceInfo(
            voice_id=str(data.get("voice_id", "")), name=str(data.get("name", "")),
            category=str(data.get("category", "")),
            labels={str(k): str(v) for k, v in dict(data.get("labels") or {}).items()},
            preview_url=str(data.get("preview_url", "")),
        )


# -- erreurs ----------------------------------------------------------------------------------------------------
class VoiceCatalogError(Exception):
    """Erreur de base : message toujours sûr à afficher (jamais la clé, jamais une réponse brute)."""


class VoiceCatalogAuthError(VoiceCatalogError):
    """401 (clé absente/invalide) ou 403 (compte/IP sans droit sur ce catalogue)."""

    def __init__(self, status: int):
        self.status = status
        message = "Clé ElevenLabs invalide ou absente." if status == 401 else \
            "Accès refusé au catalogue de voix (compte ou IP non autorisés)."
        super().__init__(message)


class VoiceCatalogRateLimited(VoiceCatalogError):
    """429 : au-delà de la limite de débit. ``retry_after`` en secondes si l'en-tête est présent."""

    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__("Trop de requêtes vers ElevenLabs : réessaie dans un instant.")


class VoiceCatalogUnavailable(VoiceCatalogError):
    """Timeout, erreur réseau, 5xx, ou réponse incompréhensible."""


# -- transport HTTP (urllib, comme mpt_connector.py : aucune dépendance supplémentaire) ---------------------------
def urllib_transport(url: str, params: dict[str, str], headers: dict[str, str],
                     timeout: float) -> tuple[int, bytes, dict[str, str]]:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    request = urllib.request.Request(f"{url}?{query}" if query else url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (URL fixe, officielle)
            return response.status, response.read(MAX_RESPONSE_BYTES), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read(MAX_RESPONSE_BYTES), dict(error.headers or {})


# -- appel et pagination ------------------------------------------------------------------------------------------
def fetch_page(api_key: str, *, page_size: int = DEFAULT_PAGE_SIZE, next_page_token: str | None = None,
              search: str = "", timeout: float = DEFAULT_TIMEOUT,
              transport: Transport = urllib_transport) -> tuple[list[VoiceInfo], str | None, bool]:
    """Une page. Ne journalise ni ne renvoie jamais ``api_key`` au-delà de cet appel."""
    params = {"page_size": str(max(1, min(page_size, 100)))}
    if next_page_token:
        params["next_page_token"] = next_page_token
    if search:
        params["search"] = search
    headers = {"xi-api-key": api_key, "Accept": "application/json"}
    try:
        status, body, response_headers = transport(API_BASE, params, headers, timeout)
    except TimeoutError as error:
        raise VoiceCatalogUnavailable("Le catalogue ElevenLabs n'a pas répondu à temps.") from error
    except (urllib.error.URLError, OSError) as error:
        raise VoiceCatalogUnavailable("Impossible de joindre ElevenLabs (réseau indisponible).") from error

    if status == 401 or status == 403:
        raise VoiceCatalogAuthError(status)
    if status == 429:
        raise VoiceCatalogRateLimited(_parse_retry_after(response_headers))
    if status >= 500:
        raise VoiceCatalogUnavailable(f"ElevenLabs a répondu une erreur serveur ({status}).")
    if status != 200:
        raise VoiceCatalogUnavailable(f"Réponse inattendue d'ElevenLabs ({status}).")

    try:
        data = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise VoiceCatalogUnavailable("Réponse d'ElevenLabs illisible.") from error
    if not isinstance(data, dict):
        raise VoiceCatalogUnavailable("Réponse d'ElevenLabs inattendue.")

    raw_voices = data.get("voices")
    voices = [voice for voice in (_voice_from_api(item) for item in raw_voices or [] if isinstance(item, dict))
             if voice is not None]  # une voix sans voice_id/name est ignorée (donnée incomplète), pas fatale
    return voices, data.get("next_page_token") or None, bool(data.get("has_more"))


def _parse_retry_after(response_headers: dict[str, str]) -> float | None:
    raw = response_headers.get("Retry-After") or response_headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _voice_from_api(item: dict[str, Any]) -> VoiceInfo | None:
    voice_id, name = item.get("voice_id"), item.get("name")
    if not voice_id or not name:
        return None
    labels = item.get("labels")
    return VoiceInfo(
        voice_id=str(voice_id), name=str(name), category=str(item.get("category") or ""),
        labels={str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {},
        preview_url=str(item.get("preview_url") or ""),
    )


def fetch_all(api_key: str, *, page_size: int = DEFAULT_PAGE_SIZE, max_pages: int = MAX_PAGES,
             timeout: float = DEFAULT_TIMEOUT, transport: Transport = urllib_transport) -> list[VoiceInfo]:
    """Toutes les pages (jusqu'à ``max_pages``, garde-fou). Lève une des erreurs ci-dessus, ne renvoie jamais
    une liste partielle en cas d'échec en cours de pagination (tout ou rien, comme un catalogue cohérent)."""
    voices: list[VoiceInfo] = []
    token: str | None = None
    for _ in range(max_pages):
        page, token, has_more = fetch_page(api_key, page_size=page_size, next_page_token=token,
                                           timeout=timeout, transport=transport)
        voices.extend(page)
        if not has_more or not token:
            break
    return voices


# -- recherche côté client (catalogue déjà en cache, aucun appel réseau) ------------------------------------------
def search_voices(voices: Iterable[VoiceInfo], query: str) -> list[VoiceInfo]:
    """Filtre par nom (insensible à la casse/accents grossièrement), pour la recherche mobile instantanée."""
    needle = " ".join(query.split()).casefold()
    if not needle:
        return list(voices)
    return [voice for voice in voices if needle in voice.name.casefold()]


# -- cache TTL, générique (ne connaît jamais la clé) ---------------------------------------------------------------
@dataclass
class CatalogResult:
    voices: tuple[VoiceInfo, ...] = ()
    source: str = "none"  # "live" | "report" | "none"
    fetched_at: str = ""
    stale: bool = False
    error: str | None = None  # message sûr, jamais la clé ni une réponse brute
    problems: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.voices)


class CatalogCache:
    """Cache mémoire d'un seul catalogue (une seule configuration ElevenLabs par déploiement Lody, comme le
    reste de la plateforme — voir ``docs/lody-secrets.md``) : ne voit jamais la clé, seulement un ``fetch_fn``
    déjà lié à elle par l'appelant."""

    def __init__(self, ttl_seconds: float = CACHE_TTL_SECONDS, clock: Callable[[], float] = time.time):
        self._ttl = ttl_seconds
        self._clock = clock
        self._cached: CatalogResult | None = None
        self._cached_at: float = 0.0

    def get(self, fetch_fn: Callable[[], list[VoiceInfo]], *, force_refresh: bool = False) -> CatalogResult:
        now = self._clock()
        if not force_refresh and self._cached is not None and (now - self._cached_at) < self._ttl:
            return self._cached
        try:
            voices = fetch_fn()
        except VoiceCatalogError as error:
            if self._cached is not None:  # un catalogue périmé reste préférable à rien du tout
                return CatalogResult(self._cached.voices, "live", self._cached.fetched_at, True, str(error))
            return CatalogResult(error=str(error))
        result = CatalogResult(tuple(voices), "live", _now_iso(), False, None)
        self._cached, self._cached_at = result, now
        return result

    def clear(self) -> None:
        self._cached, self._cached_at = None, 0.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_GLOBAL_CACHE = CatalogCache()


# -- résolution : lecture directe si possible, sinon rapport assaini (même schéma que engine_facts.resolve) --------
def resolve_catalog(*, config_path: Path | None = None, report_path: Path | None = None,
                    force_refresh: bool = False, cache: CatalogCache | None = None) -> CatalogResult:
    """Ne lève JAMAIS. Ne lit/garde jamais la clé au-delà de l'appel HTTP transitoire ci-dessous."""
    cache = cache if cache is not None else _GLOBAL_CACHE
    config = config_path or settings.config_path()
    api_key = ""
    try:
        with config.open("rb") as handle:
            raw = tomllib.load(handle)
        api_key = str((raw.get("elevenlabs") or {}).get("api_key") or "").strip()
    except (OSError, tomllib.TOMLDecodeError):
        api_key = ""  # config.toml illisible ou absente pour ce conteneur : voir le rapport ci-dessous

    if api_key:
        return cache.get(lambda: fetch_all(api_key), force_refresh=force_refresh)

    return _from_report(report_path or settings.voice_catalog_report_path(), force_refresh)


def _from_report(report_path: Path, force_refresh: bool) -> CatalogResult:
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
        if data.get("version") != REPORT_VERSION or not isinstance(data.get("voices"), list):
            raise ValueError("format")
    except FileNotFoundError:
        return CatalogResult(error="Aucune clé ElevenLabs lisible et aucun catalogue préparé côté hôte. "
                              "Utilise la saisie manuelle de l'identifiant de voix, ou demande à l'administrateur "
                              "d'exécuter scripts/lody-elevenlabs-voices-report.sh.")
    except (OSError, ValueError, json.JSONDecodeError, TypeError):
        return CatalogResult(error="Catalogue de voix ElevenLabs illisible côté serveur.")
    voices = tuple(v for v in (VoiceInfo.from_dict(item) for item in data["voices"] if isinstance(item, dict))
                   if v.voice_id and v.name)
    return CatalogResult(voices, "report", str(data.get("generated_at", "")), False, None)


# -- rapport côté hôte (même mécanisme que engine_facts.py, CLI incluse) -------------------------------------------
def build_report(config_path: Path) -> dict[str, Any]:
    """À exécuter par quelqu'un qui peut lire ``config.toml`` (l'hôte, jamais le conteneur Lody). Fait UN appel
    réel à ElevenLabs : n'exécuter que lorsque c'est effectivement souhaité (voir docs/lody-generation.md)."""
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    api_key = str((raw.get("elevenlabs") or {}).get("api_key") or "").strip()
    voices = fetch_all(api_key) if api_key else []
    return {"version": REPORT_VERSION, "generated_at": _now_iso(),
            "voices": [voice.to_dict() for voice in voices]}


def _write_atomic(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False, suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp = Path(handle.name)
    os.chmod(temp, 0o644)  # lisible par le conteneur : ce rapport ne contient aucun secret
    os.replace(temp, target)


def write_report(config_path: Path, out_path: Path) -> dict[str, Any]:
    report = build_report(config_path)
    _write_atomic(out_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Génère le catalogue de voix ElevenLabs assaini (aucun secret) pour Lody. "
                    "Fait un appel réel à l'API ElevenLabs : à exécuter côté hôte, quand c'est souhaité.")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--out", type=Path, default=Path("engine-report/elevenlabs-voices.json"))
    args = parser.parse_args(argv)
    try:
        report = write_report(args.config, args.out)
    except (OSError, tomllib.TOMLDecodeError) as error:
        print(f"Impossible de lire {args.config} : {type(error).__name__}")
        return 1
    except VoiceCatalogError as error:
        print(f"Échec de récupération du catalogue : {error}")
        return 1
    print(f"Rapport écrit : {args.out} ({len(report['voices'])} voix)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
