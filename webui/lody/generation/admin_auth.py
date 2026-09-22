"""Vérifie une session administrateur auprès du service ``lody-auth``.

Jamais de secret ici : ce module ne connaît que le jeton de session (un identifiant opaque, jamais un mot de
passe ni une clé de fournisseur), reçu via ``st.context.cookies`` — Streamlit ne peut pas poser lui-même le
cookie de session : la connexion se fait sur une page séparée servie par ``lody-auth`` (hors Streamlit), qui
pose le cookie puis redirige ici. Revérifié auprès du service à **chaque affichage ET avant chaque écriture
sensible**, jamais mis en cache dans ``st.session_state`` : sinon l'expiration de session ne s'appliquerait pas
tant que l'onglet reste ouvert.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import quote

import streamlit as st

SESSION_COOKIE = "lody_admin_session"


@dataclass(frozen=True)
class AdminSession:
    username: str


def verify_url() -> str:
    """Interne au réseau Docker : jamais proxifié publiquement par nginx (voir docs/lody-auth.md)."""
    return os.environ.get("LODY_AUTH_VERIFY_URL", "http://lody-auth:8602/verify")


def public_base_url() -> str:
    """Chemin relatif : fonctionne quel que soit le domaine public (pas de domaine codé en dur)."""
    return os.environ.get("LODY_AUTH_PUBLIC_URL", "/lody-auth")


def login_url(next_path: str) -> str:
    return f"{public_base_url()}/login?next={quote(next_path, safe='')}"


def logout_url() -> str:
    return f"{public_base_url()}/logout"


def current_session(*, timeout: float = 3.0) -> AdminSession | None:
    """Session admin valide pour CET appel, ou ``None``. N'importe quelle panne (service injoignable, jeton
    absent/expiré, réponse inattendue) se traduit par ``None`` — jamais une exception qui laisserait deviner un
    état intermédiaire, et jamais un accès accordé par défaut."""
    token = st.context.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    request = urllib.request.Request(verify_url(), headers={"X-Lody-Session-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — hôte interne fixe
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or not payload.get("valid"):
        return None
    username = payload.get("username")
    return AdminSession(username=str(username)) if isinstance(username, str) and username else None
