"""Applique les clés en attente (``secrets.toml``) dans ``config.toml`` : forme, moteur, PUIS fournisseur —
avec retour arrière automatique complet si l'une de ces trois vérifications échoue.

Conçu pour tourner **hors de tout conteneur**, sur l'hôte, déclenché par un ``systemd.path`` qui surveille
``secrets.reload`` (voir ``deploy/systemd/``) — jamais par scrutation périodique. Un seul champ modifié à la fois
en pratique, mais le pipeline traite un lot par robustesse (plusieurs enregistrements rapprochés avant que le
service n'ait eu le temps de tourner).

Déroulé :

1. valider la FORME de chaque valeur en attente (jamais son exactitude auprès du fournisseur) ;
2. copier ``config.toml`` tel quel (vérifié par empreinte) AVANT toute modification ;
3. patcher chirurgicalement les champs valides (``toml_patch``, une seule écriture atomique) ;
4. redémarrer UNIQUEMENT le conteneur du moteur, attendre sa santé (ceci prouve que le PROCESSUS redémarre,
   pas que la clé fonctionne : une clé expirée ou inventée ne fait pas planter le démarrage) ;
5. moteur sain → appel de vérification minimal et **non générateur** auprès du fournisseur de chaque clé
   modifiée (liste de modèles OpenAI, compte ElevenLabs) :
     - accepté (2xx) → *ok* ;
     - refusé (401/403) → retour arrière automatique, comme un moteur pas sain (voir ci-dessous) ;
     - fournisseur injoignable ou délai dépassé → *unverified* : la clé reste appliquée (le moteur, lui, est
       sain), mais annoncée comme non confirmée plutôt que « Configurée » — jamais confondue avec un refus ;
6. moteur pas sain, OU clé refusée par le fournisseur → restaurer la sauvegarde vérifiée, redémarrer à nouveau,
   revérifier la santé (jamais une seconde vérification auprès du fournisseur : la valeur restaurée est celle
   qui fonctionnait avant) :
     - sain → *rolled_back* (ou *rejected* pour le ou les champs directement refusés) ;
     - toujours pas sain → *critical*, aucune boucle, intervention manuelle décrite dans le message.

Aucune valeur de secret n'entre JAMAIS dans un message de statut, une exception journalisée, ou la sortie
standard : au-delà de la validation de forme (qui ne fait que constater si la valeur est vide/mal formée, sans
la répéter), la valeur ne sert plus qu'à être écrite dans ``config.toml`` et posée dans un en-tête HTTP
(``Authorization``/``xi-api-key``, jamais dans une URL) pour l'appel de vérification — jamais dans un texte lu
ensuite par un humain. Les redirections HTTP ne sont jamais suivies pendant cet appel (voir ``_NO_REDIRECT``) :
un en-tête d'authentification ne doit jamais être renvoyé à un hôte différent de celui prévu.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import socket
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import settings
from lody.generation import engine_facts, secrets_store
from lody.generation.secrets_fields import FIELDS, ShapeError, validate
from lody.generation.toml_patch import PatchError, patch as toml_patch_one

RestartFn = Callable[[], None]
HealthFn = Callable[[], bool]


@dataclass(frozen=True)
class ProviderCheck:
    """Résultat d'une vérification auprès d'un fournisseur. ``detail`` est TOUJOURS un message générique fixe :
    jamais un extrait de réponse, d'URL ou de valeur de clé (voir la doc du module)."""

    outcome: str  # "valid" | "rejected" | "timeout" | "unavailable"
    detail: str = ""


VerifyFn = Callable[[str, str, dict[str, Any]], ProviderCheck]

# Champs qui portent une clé qu'on peut vérifier auprès d'un fournisseur. ``app.llm_provider`` n'en a pas :
# le faire passer à « openai » est vérifié via la clé app.openai_api_key actuellement active (voir
# _fields_to_verify), sinon rien ne garantirait qu'elle fonctionne.
_PROVIDER_CHECK_FIELDS = frozenset({"app.openai_api_key", "app.openai_image_api_keys", "elevenlabs.api_key"})

_CRITICAL_NO_BACKUP = "Sauvegarde introuvable ou non vérifiable avant modification : rien n’a été changé."
_CRITICAL_NO_RESTORE_BACKUP = (
    "Le moteur ne répond plus et la sauvegarde de sécurité est introuvable ou corrompue : "
    "restauration automatique impossible. Intervention manuelle nécessaire sur l’hôte."
)
_CRITICAL_STILL_DOWN = (
    "Le moteur ne répond plus après une tentative de restauration automatique. "
    "Intervention manuelle nécessaire : vérifie config.toml et les journaux du conteneur sur l’hôte."
)
_ROLLED_BACK_ENGINE = (
    "Le moteur n’est pas revenu sain après le redémarrage : l’ancienne configuration a été restaurée automatiquement."
)
_ROLLED_BACK_COMPANION = (
    "Une autre clé du même enregistrement a été refusée par son fournisseur : "
    "l’ancienne configuration a été restaurée automatiquement."
)
_REJECTED = (
    "Le fournisseur a refusé cette clé (authentification refusée) : "
    "l’ancienne configuration a été restaurée automatiquement."
)
_UNVERIFIED_TIMEOUT = (
    "Le moteur a redémarré normalement, mais la vérification auprès du fournisseur a dépassé le délai : "
    "la clé est appliquée mais non confirmée."
)
_UNVERIFIED_UNAVAILABLE = (
    "Le moteur a redémarré normalement, mais le fournisseur n’a pas pu être joint pour confirmer la clé : "
    "elle est appliquée mais non confirmée."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write_text(target: Path, content: str, mode: int = 0o600) -> None:
    """Même méthode que ``save_config()`` du moteur : fichier temporaire + remplacement atomique, avec repli sur
    une écriture en place si le point de montage refuse le remplacement (bind mount monofichier, ``EBUSY``)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".config-", suffix=".toml.tmp", dir=target.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        try:
            os.replace(temp, target)
        except OSError as error:
            if error.errno != 16:  # EBUSY
                raise
            with target.open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        if temp.exists():
            temp.unlink()


def _write_status(status_path: Path, data: dict[str, Any]) -> None:
    payload = {**data, "at": _now()}
    status_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".status-", suffix=".json.tmp", dir=status_path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temp, 0o640)  # lu par Lody via le groupe partagé lody-secrets : jamais de secret dedans
        os.replace(temp, status_path)
    finally:
        if temp.exists():
            temp.unlink()


def _backup(config_path: Path, backup_path: Path) -> str | None:
    """Copie ``config.toml`` tel quel, vérifiée par empreinte. Retourne l'empreinte, ou ``None`` si la
    vérification échoue (rien n'est alors modifié en aval)."""
    try:
        data = config_path.read_bytes()
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_bytes(data)
        os.chmod(backup_path, 0o600)
        return _sha256_bytes(data) if _sha256_bytes(backup_path.read_bytes()) == _sha256_bytes(data) else None
    except OSError:
        return None


def _restore(backup_path: Path, backup_hash: str, config_path: Path) -> bool:
    try:
        data = backup_path.read_bytes()
        if _sha256_bytes(data) != backup_hash:
            return False
        _atomic_write_text(config_path, data.decode("utf-8"))
        return True
    except OSError:
        return False


def _current_value(config: dict[str, Any], field_path: str) -> str | None:
    spec = FIELDS[field_path]
    section = config.get(spec.section)
    if not isinstance(section, dict):
        return None
    value = section.get(spec.key)
    if spec.kind == "list1":
        return value[0] if isinstance(value, list) and value and isinstance(value[0], str) and value[0].strip() else None
    return value if isinstance(value, str) and value.strip() else None


def _fields_to_verify(to_apply: dict[str, str], patched_config: dict[str, Any]) -> dict[str, str]:
    """Champs à vérifier auprès de leur fournisseur une fois le moteur sain. Chaque clé modifiée, PLUS la clé
    OpenAI actuellement active si ``app.llm_provider`` vient de passer à « openai » sans que cette clé ait
    elle-même été modifiée dans le même lot (sinon rien ne garantirait qu'elle fonctionne)."""
    to_verify = {f: v for f, v in to_apply.items() if f in _PROVIDER_CHECK_FIELDS}
    if "app.llm_provider" in to_apply and "app.openai_api_key" not in to_verify:
        current = _current_value(patched_config, "app.openai_api_key")
        if current:
            to_verify["app.openai_api_key"] = current
    return to_verify


def _rollback(*, config_path: Path, backup_path: Path, backup_hash: str, status_path: Path, report_path: Path,
             restart_engine: RestartFn, wait_healthy: HealthFn, to_apply: dict[str, str],
             results: dict[str, dict[str, str]], secrets_path: Path, pending: dict[str, str], overall_message: str,
             rejected: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Restaure la sauvegarde vérifiée, redémarre, revérifie la santé. Partagé par « moteur pas sain » et
    « clé refusée par le fournisseur » : dans les deux cas, on revient à la dernière configuration qui
    fonctionnait, sans revérifier cette ancienne clé auprès du fournisseur (elle fonctionnait déjà avant)."""
    _write_status(status_path, {"state": "restoring", "processing": list(to_apply), "fields": results})
    restored = _restore(backup_path, backup_hash, config_path)
    if not restored:
        for field_path in to_apply:
            results[field_path] = {"state": "critical", "message": _CRITICAL_NO_RESTORE_BACKUP}
        final = {"state": "critical", "message": _CRITICAL_NO_RESTORE_BACKUP, "fields": results}
        _write_status(status_path, final)
        return final

    healthy_again = False
    try:
        restart_engine()
        healthy_again = wait_healthy()
    except Exception:  # noqa: BLE001 — jamais journalisé avec le détail : voir la doc du module
        healthy_again = False

    if not healthy_again:
        for field_path in to_apply:
            results[field_path] = {"state": "critical", "message": _CRITICAL_STILL_DOWN}
        secrets_store.clear_pending(list(pending), secrets_path=secrets_path)
        final = {"state": "critical", "message": _CRITICAL_STILL_DOWN, "fields": results}
        _write_status(status_path, final)
        return final

    try:
        engine_facts.write_report(config_path, report_path)
    except Exception:  # noqa: BLE001 — le rapport sera simplement régénéré au prochain cycle
        pass
    for field_path in to_apply:
        if field_path in rejected:
            results[field_path] = {"state": "rejected", "message": _REJECTED}
        else:
            results[field_path] = {"state": "rolled_back", "message": overall_message}
    secrets_store.clear_pending(list(pending), secrets_path=secrets_path)
    final = {"state": "rejected" if rejected else "rolled_back", "message": overall_message, "fields": results}
    _write_status(status_path, final)
    return final


def apply(*, config_path: Path, secrets_path: Path, status_path: Path, backup_path: Path, report_path: Path,
          restart_engine: RestartFn, wait_healthy: HealthFn, verify_key: VerifyFn) -> dict[str, Any]:
    """Point d'entrée. Toujours sûr à appeler : sans rien en attente, ne touche rien."""
    pending = secrets_store.read_pending(secrets_path)
    if not pending:
        return {"state": "noop"}

    results: dict[str, dict[str, str]] = {}
    to_apply: dict[str, str] = {}
    for field_path, value in pending.items():
        try:
            to_apply[field_path] = validate(field_path, value)
        except ShapeError as error:
            results[field_path] = {"state": "invalid", "message": str(error)}

    _write_status(status_path, {"state": "applying", "processing": list(to_apply), "fields": results})

    if not to_apply:
        secrets_store.clear_pending(list(pending), secrets_path=secrets_path)
        final = {"state": "error", "fields": results}
        _write_status(status_path, final)
        return final

    backup_hash = _backup(config_path, backup_path)
    if backup_hash is None:
        final = {"state": "critical", "message": _CRITICAL_NO_BACKUP,
                 "fields": {**results, **{f: {"state": "critical", "message": _CRITICAL_NO_BACKUP} for f in to_apply}}}
        _write_status(status_path, final)
        return final

    try:
        text = config_path.read_text(encoding="utf-8")
        for field_path, value in to_apply.items():
            spec = FIELDS[field_path]
            text = toml_patch_one(text, section=spec.section, key=spec.key, value=value, kind=spec.kind)
    except (PatchError, OSError) as error:
        message = f"Écriture refusée : {type(error).__name__}." if isinstance(error, OSError) else str(error)
        for field_path in to_apply:
            results[field_path] = {"state": "invalid", "message": message}
        secrets_store.clear_pending(list(pending), secrets_path=secrets_path)
        final = {"state": "error", "fields": results}
        _write_status(status_path, final)
        return final

    _atomic_write_text(config_path, text)
    _write_status(status_path, {"state": "restarting", "processing": list(to_apply), "fields": results})

    healthy = False
    try:
        restart_engine()
        healthy = wait_healthy()
    except Exception:  # noqa: BLE001 — jamais journalisé avec le détail : voir la doc du module
        healthy = False

    if not healthy:
        return _rollback(config_path=config_path, backup_path=backup_path, backup_hash=backup_hash,
                         status_path=status_path, report_path=report_path, restart_engine=restart_engine,
                         wait_healthy=wait_healthy, to_apply=to_apply, results=results, secrets_path=secrets_path,
                         pending=pending, overall_message=_ROLLED_BACK_ENGINE)

    # Le PROCESSUS du moteur redémarre : ça ne prouve pas que la clé fonctionne (une clé expirée ne fait pas
    # planter le démarrage). Vérification minimale, non génératrice, auprès du fournisseur.
    try:
        patched_config = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        patched_config = {}
    to_verify = _fields_to_verify(to_apply, patched_config)
    _write_status(status_path, {"state": "verifying", "processing": list(to_apply), "fields": results})
    checks: dict[str, ProviderCheck] = {}
    for field_path, value in to_verify.items():
        try:
            checks[field_path] = verify_key(field_path, value, patched_config)
        except Exception:  # noqa: BLE001 — jamais de détail journalisé : voir la doc du module
            checks[field_path] = ProviderCheck("unavailable")

    rejected = frozenset(field_path for field_path, check in checks.items() if check.outcome == "rejected")
    if rejected:
        directly_rejected = rejected & set(to_apply)
        overall_message = _REJECTED if directly_rejected else _ROLLED_BACK_COMPANION
        return _rollback(config_path=config_path, backup_path=backup_path, backup_hash=backup_hash,
                         status_path=status_path, report_path=report_path, restart_engine=restart_engine,
                         wait_healthy=wait_healthy, to_apply=to_apply, results=results, secrets_path=secrets_path,
                         pending=pending, overall_message=overall_message, rejected=directly_rejected)

    try:
        engine_facts.write_report(config_path, report_path)
    except Exception:  # noqa: BLE001 — le rapport sera simplement régénéré au prochain cycle
        pass
    for field_path in to_apply:
        # Vérification directe si ce champ porte une clé ; sinon (app.llm_provider) hérite du résultat de la
        # clé « compagnon » vérifiée à sa place, pour rester honnête sur ce qui a vraiment été confirmé.
        check = checks.get(field_path) or (checks.get("app.openai_api_key") if field_path == "app.llm_provider" else None)
        if check is None or check.outcome == "valid":
            results[field_path] = {"state": "ok", "message": ""}
        elif check.outcome == "timeout":
            results[field_path] = {"state": "unverified", "message": _UNVERIFIED_TIMEOUT}
        else:  # "unavailable"
            results[field_path] = {"state": "unverified", "message": _UNVERIFIED_UNAVAILABLE}
    secrets_store.clear_pending(list(pending), secrets_path=secrets_path)
    overall = "ready" if all(r["state"] == "ok" for r in results.values()) else "ready_unverified"
    final = {"state": overall, "fields": results}
    _write_status(status_path, final)
    return final


# -- câblage réel (CLI, exécuté sur l'hôte par le service systemd) -----------------------------------------------
def _real_restart(container: str, docker_cmd: list[str]) -> RestartFn:
    def restart() -> None:
        subprocess.run([*docker_cmd, "restart", container], check=True, capture_output=True, timeout=60)
    return restart


def _real_wait_healthy(url: str, timeout_s: float, interval_s: float) -> HealthFn:
    def wait() -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 — URL fixe, locale
                    if 200 <= response.status < 500:
                        return True
            except OSError:
                pass
            time.sleep(interval_s)
        return False
    return wait


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Un en-tête d'authentification ne doit JAMAIS être renvoyé à un hôte différent de celui prévu : toute
    redirection (3xx) est traitée comme un échec plutôt que suivie."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N803, D102 — signature imposée
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


def _http_check(url: str, headers: dict[str, str], timeout_s: float) -> ProviderCheck:
    """Un seul GET, en-têtes uniquement (jamais la clé dans l'URL), aucune redirection suivie, timeout court.
    Le détail retourné est toujours un texte fixe : jamais un extrait de réponse ni d'exception."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _NO_REDIRECT_OPENER.open(request, timeout=timeout_s) as response:
            if 200 <= response.status < 300:
                return ProviderCheck("valid")
            return ProviderCheck("unavailable", f"réponse inattendue ({response.status})")
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return ProviderCheck("rejected", f"authentification refusée ({error.code})")
        return ProviderCheck("unavailable", f"réponse inattendue ({error.code})")
    except (TimeoutError, socket.timeout):
        return ProviderCheck("timeout", "délai dépassé")
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            return ProviderCheck("timeout", "délai dépassé")
        return ProviderCheck("unavailable", "fournisseur injoignable")
    except OSError:
        return ProviderCheck("unavailable", "fournisseur injoignable")


def _real_verify_key(timeout_s: float) -> VerifyFn:
    """Vérification minimale et gratuite/non génératrice pour chaque fournisseur :

    * OpenAI (script et images) : ``GET {base_url}/models`` avec ``Authorization: Bearer <clé>`` — liste les
      modèles disponibles, ne génère rien. Respecte une éventuelle base URL compatible personnalisée.
    * ElevenLabs : ``GET https://api.elevenlabs.io/v1/user`` avec ``xi-api-key: <clé>`` — endpoint officiel
      d'information de compte, ne génère aucun audio.
    """
    def verify(field_path: str, value: str, config: dict[str, Any]) -> ProviderCheck:
        app = config.get("app") if isinstance(config.get("app"), dict) else {}
        if field_path == "app.openai_api_key":
            base = str(app.get("openai_base_url") or "").strip() or "https://api.openai.com/v1"
            return _http_check(f"{base.rstrip('/')}/models", {"Authorization": f"Bearer {value}"}, timeout_s)
        if field_path == "app.openai_image_api_keys":
            base = str(app.get("openai_image_base_url") or "").strip() or "https://api.openai.com/v1"
            return _http_check(f"{base.rstrip('/')}/models", {"Authorization": f"Bearer {value}"}, timeout_s)
        if field_path == "elevenlabs.api_key":
            return _http_check("https://api.elevenlabs.io/v1/user", {"xi-api-key": value}, timeout_s)
        return ProviderCheck("valid")
    return verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Applique les clés en attente dans config.toml (hôte uniquement).")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--secrets-dir", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    # Le nom du conteneur n'est jamais codé en dur ici (voir test_generation_connector.py) : le script hôte
    # scripts/lody-apply-secrets.sh fournit toujours la valeur par défaut.
    parser.add_argument("--container", default=os.environ.get("LODY_ENGINE_CONTAINER", ""), required=False)
    parser.add_argument("--docker", default=os.environ.get("LODY_DOCKER_BIN", "docker"),
                        help="commande docker, ex. 'docker' ou 'sudo -n docker' (analysée avec shlex)")
    parser.add_argument("--health-url", default=os.environ.get("LODY_ENGINE_HEALTH_URL", "http://127.0.0.1:8080/docs"))
    parser.add_argument("--health-timeout", type=float, default=60.0)
    parser.add_argument("--health-interval", type=float, default=2.0)
    parser.add_argument("--provider-timeout", type=float, default=8.0,
                        help="délai court pour l'appel de vérification auprès du fournisseur (voir la doc du module)")
    args = parser.parse_args(argv)
    if not args.container:
        parser.error("--container est requis (ou LODY_ENGINE_CONTAINER) : nom du conteneur du moteur.")

    secrets_dir = args.secrets_dir or settings.secrets_dir()
    config_path = args.config or settings.config_path()
    result = apply(
        config_path=config_path, secrets_path=secrets_dir / "secrets.toml",
        status_path=secrets_dir / "secrets-status.json", backup_path=config_path.with_name("config.toml.rollback"),
        report_path=args.report or settings.engine_report_path(),
        restart_engine=_real_restart(args.container, shlex.split(args.docker)),
        wait_healthy=_real_wait_healthy(args.health_url, args.health_timeout, args.health_interval),
        verify_key=_real_verify_key(args.provider_timeout),
    )
    print(f"état final : {result['state']}")  # jamais de valeur de secret : voir la doc du module
    return 0 if result["state"] in ("noop", "ready", "ready_unverified", "rolled_back") else 1


if __name__ == "__main__":
    raise SystemExit(main())
