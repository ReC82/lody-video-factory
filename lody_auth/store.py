"""Logique d'authentification admin : hash de mot de passe, sessions, anti-force-brute, CSRF.

Aucune dépendance externe : ``hashlib.scrypt`` (bibliothèque standard) est une fonction de dérivation de clé à
coût mémoire, recommandée par l'OWASP quand bcrypt/argon2 ne sont pas disponibles. Rien n'est jamais persisté en
dehors du fichier d'identifiant (0640, groupe ``lody-secrets``, hors Git et hors SQLite — voir docs/lody-auth.md) :
les sessions et les compteurs anti-force-brute vivent en mémoire du processus, jamais sur disque. Un redémarrage
du service déconnecte tout le monde : accepté pour un déploiement mono-administrateur.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 64
SALT_BYTES = 16

SESSION_IDLE_SECONDS = 30 * 60           # expiration par inactivité
SESSION_ABSOLUTE_SECONDS = 12 * 60 * 60  # plafond absolu, même en cas d'activité continue

LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW_SECONDS = 15 * 60
LOCKOUT_BASE_SECONDS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# -- mot de passe ------------------------------------------------------------------------------------------------
def hash_password(password: str, *, salt: bytes | None = None) -> dict[str, Any]:
    salt = salt if salt is not None else os.urandom(SALT_BYTES)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_DKLEN)
    return {"algo": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": salt.hex(), "hash": digest.hex()}


def verify_password(password: str, record: dict[str, Any]) -> bool:
    """Comparaison en temps constant. Toute forme inattendue échoue sans lever d'exception."""
    if not isinstance(record, dict) or record.get("algo") != "scrypt":
        return False
    try:
        salt = bytes.fromhex(str(record["salt"]))
        expected = bytes.fromhex(str(record["hash"]))
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(record["n"]), r=int(record["r"]),
                                p=int(record["p"]), dklen=len(expected))
    except (KeyError, ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def _atomic_write_json(path: Path, payload: dict[str, Any], mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".auth-", suffix=".json.tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def set_admin_password(path: Path, username: str, password: str) -> None:
    """Écrit (ou remplace) l'identifiant administrateur. Jamais appelé depuis l'interface web : uniquement par
    la procédure d'activation initiale, lancée à la main par l'administrateur sur l'hôte (voir
    scripts/lody-auth-set-password.sh)."""
    record = {"username": username, "updated_at": _now_iso(), **hash_password(password)}
    _atomic_write_json(path, record)


def load_admin(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def check_credentials(path: Path, username: str, password: str) -> bool:
    """Vrai/faux. Ne distingue jamais « identifiant inconnu » de « mot de passe incorrect » (aucune énumération)."""
    record = load_admin(path)
    if record is None or not hmac.compare_digest(str(record.get("username", "")), username):
        # Temps constant même quand l'identifiant est inconnu : on vérifie quand même un hash factice.
        verify_password(password, {"algo": "scrypt", "salt": "00" * SALT_BYTES, "hash": "00" * SCRYPT_DKLEN,
                                   "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P})
        return False
    return verify_password(password, record)


# -- sessions --------------------------------------------------------------------------------------------------
@dataclass
class Session:
    username: str
    created_at: float
    last_seen_at: float


class SessionStore:
    def __init__(self, *, idle_seconds: int = SESSION_IDLE_SECONDS, absolute_seconds: int = SESSION_ABSOLUTE_SECONDS,
                clock: Callable[[], float] = time.time):
        self._sessions: dict[str, Session] = {}
        self._idle = idle_seconds
        self._absolute = absolute_seconds
        self._clock = clock

    def create(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        now = self._clock()
        self._sessions[token] = Session(username, now, now)
        return token

    def touch(self, token: str) -> Session | None:
        """Valide la session et rafraîchit son activité. ``None`` si absente ou expirée (et la retire alors)."""
        session = self._sessions.get(token)
        if session is None:
            return None
        now = self._clock()
        if now - session.last_seen_at > self._idle or now - session.created_at > self._absolute:
            self._sessions.pop(token, None)
            return None
        session.last_seen_at = now
        return session

    def revoke(self, token: str) -> None:
        self._sessions.pop(token, None)

    def revoke_all(self) -> None:
        self._sessions.clear()

    def __len__(self) -> int:
        return len(self._sessions)


# -- anti-force-brute --------------------------------------------------------------------------------------------
class LoginThrottle:
    """Verrouille une clé (IP) après plusieurs échecs rapprochés, avec recul exponentiel. En mémoire uniquement."""

    def __init__(self, *, threshold: int = LOCKOUT_THRESHOLD, window_seconds: int = LOCKOUT_WINDOW_SECONDS,
                base_seconds: int = LOCKOUT_BASE_SECONDS, clock: Callable[[], float] = time.time):
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._strikes: dict[str, int] = {}
        self._threshold = threshold
        self._window = window_seconds
        self._base = base_seconds
        self._clock = clock

    def locked_for(self, key: str) -> float:
        return max(0.0, self._locked_until.get(key, 0.0) - self._clock())

    def record_failure(self, key: str) -> None:
        now = self._clock()
        recent = [t for t in self._failures.get(key, []) if now - t < self._window]
        recent.append(now)
        if len(recent) >= self._threshold:
            strikes = self._strikes.get(key, 0) + 1
            self._strikes[key] = strikes
            self._locked_until[key] = now + self._base * (2 ** (strikes - 1))
            self._failures[key] = []
        else:
            self._failures[key] = recent

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)
        self._strikes.pop(key, None)


# -- CSRF (double soumission par cookie) -------------------------------------------------------------------------
def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def csrf_valid(cookie_value: str | None, form_value: str | None) -> bool:
    if not cookie_value or not form_value:
        return False
    return hmac.compare_digest(cookie_value, form_value)
