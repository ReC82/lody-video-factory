#!/usr/bin/env python3
"""#209 : « Lody est-il en train de produire quelque chose ? » — sonde en LECTURE SEULE.

Conçue pour tourner DANS le conteneur d'interface (le rendu 2D y est un sous-processus) sans y être
installée : le script hôte la pousse sur l'entrée standard.

    docker exec -i lody-video-factory-ui python3 - < scripts/lody_activity_probe.py

Elle regarde deux choses, sans rien modifier :

1. la base SQLite (ouverte en ``mode=ro``) : productions ``CONFIRMEE`` / ``EN_FILE`` / ``EN_COURS`` ;
2. les processus visibles (``/proc``) : moteur Remotion, Chrome headless, ffmpeg.

Sortie : un objet JSON sur stdout, et le code de retour

- ``0`` — ``idle`` : rien d'actif démontré ;
- ``1`` — ``active`` : au moins un rendu ou une production en cours ;
- ``2`` — ``unknown`` : impossible de conclure (base illisible, production « en cours » figée depuis
  longtemps…). Les appelants traitent ``unknown`` comme ``active`` (fail closed).

Bibliothèque standard uniquement (Python 3.9+), aucune dépendance au paquet ``lody``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ACTIVE_STATUSES = ("CONFIRMEE", "EN_FILE", "EN_COURS")
# Fragments de ligne de commande qui signalent un rendu en cours dans le conteneur.
PROCESS_MARKERS = ("lody-remotion", "remotion", "chrome-headless", "headless_shell", "ffmpeg")
# Une production « en cours » sans mise à jour depuis plus longtemps est suspecte : on ne conclut pas.
STALE_HOURS = 6.0


def _parse_time(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def check_database(db_path: Path, now: datetime, stale_hours: float = STALE_HOURS) -> tuple[list, list, list]:
    """(actives, figées, erreurs) d'après la table ``productions``."""
    active: list[str] = []
    stale: list[str] = []
    errors: list[str] = []
    if not db_path.exists():
        return active, stale, errors  # installation neuve : aucune production
    marks = ",".join("?" for _ in ACTIVE_STATUSES)
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            rows = connection.execute(
                f"SELECT id, status, updated_at FROM productions WHERE status IN ({marks})", ACTIVE_STATUSES
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as error:
        errors.append(f"base illisible : {error}")
        return active, stale, errors
    for production_id, status, updated_at in rows:
        moment = _parse_time(updated_at)
        label = f"production {production_id} ({status}, mise à jour {updated_at})"
        if moment is not None and (now - moment).total_seconds() > stale_hours * 3600:
            stale.append(label)
        else:
            active.append(label)
    return active, stale, errors


def check_processes(proc_root: Path, self_pid: int) -> list[str]:
    """Processus de rendu visibles sous ``proc_root`` (hors la sonde elle-même)."""
    found: list[str] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == self_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue  # processus terminé entre-temps, ou illisible
        command = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        if any(marker in command for marker in PROCESS_MARKERS):
            found.append(f"pid {entry.name} : {command[:160]}")
    return found


def probe(db_path: Path, proc_root: Path = Path("/proc"), now: datetime | None = None,
          self_pid: int | None = None, stale_hours: float = STALE_HOURS) -> dict:
    now = now or datetime.now(timezone.utc)
    active, stale, errors = check_database(db_path, now, stale_hours)
    processes = check_processes(proc_root, os.getpid() if self_pid is None else self_pid)
    if active or processes:
        state = "active"
    elif stale or errors:
        state = "unknown"
    else:
        state = "idle"
    return {"state": state, "productions": active, "stale_productions": stale, "processes": processes,
            "errors": errors, "database": str(db_path), "checked_at": now.isoformat(timespec="seconds")}


EXIT_CODES = {"idle": 0, "active": 1, "unknown": 2}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    data_dir = Path(os.environ.get("LODY_DATA_DIR", "/data"))
    db_path = Path(argv[0]) if argv else data_dir / "lody.sqlite3"
    result = probe(db_path)
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_CODES[result["state"]]


if __name__ == "__main__":
    sys.exit(main())
