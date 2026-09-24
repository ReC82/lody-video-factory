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

Architecture — LE CONTENEUR LODY NE VOIT JAMAIS LA CLÉ, DÉLIBÉRÉMENT, PAS SEULEMENT PAR MANQUE DE DROITS :

Deux familles de fonctions, jamais mélangées :

* **côté hôte, hors du conteneur Lody** (``fetch_page``/``fetch_all``/``build_report``/``write_report``/
  ``main`` — exécutées par ``scripts/lody-elevenlabs-voices-report.sh`` ou par ``apply_secrets.py`` juste
  après une rotation de clé réussie) : lisent ``config.toml`` et appellent l'API ElevenLabs avec la clé,
  gardée dans une variable locale le temps du seul appel HTTP sortant, jamais journalisée, jamais écrite
  ailleurs que dans l'en-tête ``xi-api-key`` de CETTE requête. Écrivent un **rapport assaini** (voice_id,
  nom, catégorie, labels, URL d'aperçu validée) dans ``./engine-report`` — le même dossier, déjà monté en
  lecture seule dans le conteneur Lody, qui porte ``engine-capabilities.json``. Aucun changement de
  ``docker-compose.lody.yml`` : ``config.toml`` n'est PAS monté dans ``lody-video-factory-ui`` pour ce
  chantier, et ne doit pas l'être.

* **côté conteneur Lody** (``resolve_catalog``/``search_voices``, utilisées par l'interface) : lisent
  UNIQUEMENT ce rapport assaini. Ce module ne contient AUCUN code qui ouvrirait ``config.toml`` depuis ce
  chemin — impossible d'y lire la clé même par erreur, même transitoirement. Si le rapport manque ou est
  invalide (script hôte jamais lancé, ou pas encore après le premier déploiement), l'interface l'affiche
  clairement et retombe entièrement sur la saisie manuelle du ``voice_id`` : rien n'est jamais bloqué.

``VoiceInfo``/``CatalogResult`` n'ont aucun champ secret par construction : impossible d'y stocker une clé
par erreur (mêmes principes que ``EngineFacts``).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
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
REPORT_VERSION = 1
# Un catalogue de voix change rarement ; au-delà de ce délai, le rapport est affiché quand même
# (mieux qu'un blocage) mais signalé comme périmé, avec un rappel de la commande de régénération.
REPORT_MAX_AGE_SECONDS = 30 * 24 * 3600

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
        # Revalide l'URL d'aperçu ICI (pas seulement à la lecture de l'API/du rapport) : ``to_dict`` est le
        # point de passage obligé avant toute écriture sur disque (``build_report``) — aucun VoiceInfo construit
        # autrement ne peut donc faire fuiter une URL non sûre dans un fichier (défense en profondeur).
        return {"voice_id": self.voice_id, "name": self.name, "category": self.category,
                "labels": dict(self.labels), "preview_url": _safe_preview_url(self.preview_url)}

    @staticmethod
    def from_dict(data: dict[str, Any]) -> VoiceInfo:
        return VoiceInfo(
            voice_id=str(data.get("voice_id", "")), name=str(data.get("name", "")),
            category=str(data.get("category", "")),
            labels={str(k): str(v) for k, v in dict(data.get("labels") or {}).items()},
            preview_url=_safe_preview_url(data.get("preview_url")),
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


def _safe_preview_url(value: Any) -> str:
    """Ne conserve l'URL d'aperçu que si elle est « sûre » : HTTPS uniquement — jamais ``javascript:``,
    ``data:``, ``http:`` en clair, ou une valeur qui n'est même pas une URL. Appliqué aussi bien à une
    réponse ElevenLabs qu'à un rapport relu (défense en profondeur, coût nul)."""
    text = str(value or "").strip()
    return text if text.lower().startswith("https://") else ""


def _voice_from_api(item: dict[str, Any]) -> VoiceInfo | None:
    voice_id, name = item.get("voice_id"), item.get("name")
    if not voice_id or not name:
        return None
    labels = item.get("labels")
    return VoiceInfo(
        voice_id=str(voice_id), name=str(name), category=str(item.get("category") or ""),
        labels={str(k): str(v) for k, v in labels.items()} if isinstance(labels, dict) else {},
        preview_url=_safe_preview_url(item.get("preview_url")),
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
    """Filtre côté client (catalogue déjà résolu, aucun appel réseau), insensible à la casse. Cherche au
    minimum dans le nom, le ``voice_id`` complet, et les labels disponibles (clés et valeurs : langue, âge,
    genre, accent…) — ticket #59. Un texte partiel suffit (sous-chaîne, pas un préfixe)."""
    needle = " ".join(query.split()).casefold()
    if not needle:
        return list(voices)

    def haystacks(voice: VoiceInfo) -> list[str]:
        return [voice.name, voice.voice_id, *voice.labels.keys(), *voice.labels.values()]

    return [voice for voice in voices if any(needle in str(item).casefold() for item in haystacks(voice))]


# -- résultat exposé à l'interface (ne connaît jamais la clé) -------------------------------------------------------
@dataclass
class CatalogResult:
    voices: tuple[VoiceInfo, ...] = ()
    source: str = "none"  # "report" | "none"
    generated_at: str = ""  # quand l'HÔTE a produit ce rapport (jamais quand Lody l'a relu — voir voice_picker.py)
    stale: bool = False   # rapport plus vieux que REPORT_MAX_AGE_SECONDS : affiché quand même, avec un avertissement
    empty: bool = False   # rapport valide et récent, mais 0 voix sur le compte (distinct d'un rapport absent/cassé)
    error: str | None = None  # message sûr, jamais la clé ni une réponse brute ; None si empty/valide
    problems: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.voices)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_NO_REPORT_MESSAGE = (
    "Aucun catalogue de voix ElevenLabs n'a encore été généré côté hôte. La saisie manuelle de "
    "l'identifiant de voix reste disponible ci-dessous. Demande à l'administrateur d'exécuter "
    "scripts/lody-elevenlabs-voices-report.sh pour l'activer."
)
_INVALID_REPORT_MESSAGE = (
    "Le catalogue de voix ElevenLabs préparé côté hôte est illisible ou dans un format inattendu. "
    "La saisie manuelle de l'identifiant de voix reste disponible ci-dessous."
)


# -- résolution : LIT UNIQUEMENT le rapport assaini produit côté hôte — jamais config.toml, jamais la clé -----------
def resolve_catalog(*, report_path: Path | None = None) -> CatalogResult:
    """Ne lève JAMAIS. N'ouvre jamais ``config.toml`` : voir le docstring du module. Relit le fichier à chaque
    appel (pas de cache Python/Streamlit à invalider) : le bouton « Actualiser » de l'interface n'a donc qu'à
    déclencher un nouveau rendu (n'importe quel ``st.button`` le fait déjà) pour voir un fichier remplacé
    entre-temps par le script hôte — voir ``voice_picker.py``."""
    path = report_path or settings.voice_catalog_report_path()
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CatalogResult(error=_NO_REPORT_MESSAGE)
    except OSError:
        return CatalogResult(error=_INVALID_REPORT_MESSAGE)
    try:
        data = json.loads(raw_text)
        if data.get("version") != REPORT_VERSION or not isinstance(data.get("voices"), list):
            raise ValueError("format")
    except (ValueError, json.JSONDecodeError, TypeError, AttributeError):
        return CatalogResult(error=_INVALID_REPORT_MESSAGE)

    voices = tuple(v for v in (VoiceInfo.from_dict(item) for item in data["voices"] if isinstance(item, dict))
                   if v.voice_id and v.name)
    generated_at = str(data.get("generated_at", ""))
    stale = _report_age_seconds(generated_at) > REPORT_MAX_AGE_SECONDS
    return CatalogResult(voices=voices, source="report", generated_at=generated_at, stale=stale,
                         empty=not voices, error=None)


def _report_age_seconds(generated_at: str) -> float:
    try:
        moment = datetime.fromisoformat(generated_at)
    except ValueError:
        return float("inf")  # date absente/illisible : traité comme périmé plutôt qu'une confiance non fondée
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - moment).total_seconds()


# -- rapport côté hôte (même mécanisme que engine_facts.py, CLI incluse) -------------------------------------------
def build_report(config_path: Path) -> dict[str, Any]:
    """À exécuter par quelqu'un qui peut lire ``config.toml`` — l'hôte (script CLI ci-dessous) ou
    ``apply_secrets.py``, JAMAIS le conteneur Lody. Fait UN appel réel à ElevenLabs : n'exécuter que lorsque
    c'est effectivement souhaité (voir docs/lody-elevenlabs-voices.md). Ne modifie jamais ``config.toml``
    (ouvert en lecture seule, ``"rb"``, jamais réécrit)."""
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    api_key = str((raw.get("elevenlabs") or {}).get("api_key") or "").strip()
    voices = fetch_all(api_key) if api_key else []
    return {"version": REPORT_VERSION, "generated_at": _now_iso(),
            "voices": [voice.to_dict() for voice in voices]}


def _write_atomic(target: Path, payload: dict[str, Any]) -> None:
    """Écriture atomique (fichier temporaire + ``os.replace``, même méthode que ``engine_facts.py``) : un
    lecteur ne voit jamais un fichier à moitié écrit. Permissions ``0o644`` : LECTURE seule pour le groupe et
    le reste du monde (dont le conteneur Lody, uid différent de celui de l'hôte — pas de groupe partagé ici,
    comme ``engine-capabilities.json`` déjà mounté ``:ro`` dans le conteneur) ; ÉCRITURE seulement pour le
    propriétaire (l'hôte). Sans risque : ce fichier ne contient jamais de secret (voir ``VoiceInfo``)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False, suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp = Path(handle.name)
    os.chmod(temp, 0o644)
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
