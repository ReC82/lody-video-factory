"""Applique les clés en attente (``secrets.toml``) dans ``config.toml``, avec retour arrière automatique.

Conçu pour tourner **hors de tout conteneur**, sur l'hôte, déclenché par un ``systemd.path`` qui surveille
``secrets.reload`` (voir ``deploy/systemd/``) — jamais par scrutation périodique. Un seul champ modifié à la fois
en pratique, mais le pipeline traite un lot par robustesse (plusieurs enregistrements rapprochés avant que le
service n'ait eu le temps de tourner).

Déroulé :

1. valider la FORME de chaque valeur en attente (jamais son exactitude auprès du fournisseur) ;
2. copier ``config.toml`` tel quel (vérifié par empreinte) AVANT toute modification ;
3. patcher chirurgicalement les champs valides (``toml_patch``, une seule écriture atomique) ;
4. redémarrer UNIQUEMENT le conteneur du moteur, attendre sa santé ;
5. sain → régénérer le rapport de capacités, statut *ready* ;
   pas sain → restaurer la copie, redémarrer à nouveau, vérifier la santé ;
     restauré et sain → statut *rolled_back* (l'ancienne configuration a été remise en place) ;
     toujours pas sain → statut *critical*, aucune boucle, intervention manuelle décrite dans le message.

Aucune valeur de secret n'entre JAMAIS dans un message de statut, une exception journalisée, ou la sortie
standard : au-delà de l'étape 1 (qui ne fait que constater si la valeur est vide/mal formée, sans la répéter),
seules les valeurs elles-mêmes transitent — jamais leur texte — jusqu'à l'écriture dans ``config.toml``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lody import settings
from lody.generation import engine_facts, secrets_store
from lody.generation.secrets_fields import FIELDS, ShapeError, validate
from lody.generation.toml_patch import PatchError, patch as toml_patch_one

RestartFn = Callable[[], None]
HealthFn = Callable[[], bool]

_CRITICAL_NO_BACKUP = ("Sauvegarde introuvable ou non vérifiable avant modification : rien n’a été changé.")
_CRITICAL_NO_RESTORE_BACKUP = (
    "Le moteur ne répond plus et la sauvegarde de sécurité est introuvable ou corrompue : "
    "restauration automatique impossible. Intervention manuelle nécessaire sur l’hôte."
)
_CRITICAL_STILL_DOWN = (
    "Le moteur ne répond plus après une tentative de restauration automatique. "
    "Intervention manuelle nécessaire : vérifie config.toml et les journaux du conteneur sur l’hôte."
)
_ROLLED_BACK = "La nouvelle valeur a été refusée par le moteur : l’ancienne configuration a été restaurée automatiquement."


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
        os.chmod(temp, 0o644)  # lu par Lody : jamais de secret dans ce fichier
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


def apply(*, config_path: Path, secrets_path: Path, status_path: Path, backup_path: Path, report_path: Path,
          restart_engine: RestartFn, wait_healthy: HealthFn) -> dict[str, Any]:
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

    if healthy:
        try:
            engine_facts.write_report(config_path, report_path)
        except Exception:  # noqa: BLE001 — le rapport sera simplement régénéré au prochain cycle
            pass
        for field_path in to_apply:
            results[field_path] = {"state": "ok", "message": ""}
        secrets_store.clear_pending(list(pending), secrets_path=secrets_path)
        final = {"state": "ready" if all(r.get("state") == "ok" for r in results.values()) else "error",
                 "fields": results}
        _write_status(status_path, final)
        return final

    # Le moteur n'est pas revenu sain : retour arrière automatique complet.
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
    except Exception:  # noqa: BLE001
        healthy_again = False

    if healthy_again:
        try:
            engine_facts.write_report(config_path, report_path)
        except Exception:  # noqa: BLE001
            pass
        for field_path in to_apply:
            results[field_path] = {"state": "rolled_back", "message": _ROLLED_BACK}
        secrets_store.clear_pending(list(pending), secrets_path=secrets_path)
        final = {"state": "rolled_back", "message": _ROLLED_BACK, "fields": results}
        _write_status(status_path, final)
        return final

    for field_path in to_apply:
        results[field_path] = {"state": "critical", "message": _CRITICAL_STILL_DOWN}
    secrets_store.clear_pending(list(pending), secrets_path=secrets_path)
    final = {"state": "critical", "message": _CRITICAL_STILL_DOWN, "fields": results}
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
    )
    print(f"état final : {result['state']}")  # jamais de valeur de secret : voir la doc du module
    return 0 if result["state"] in ("noop", "ready", "rolled_back") else 1


if __name__ == "__main__":
    raise SystemExit(main())
