#!/usr/bin/env python3
"""#209 : manifeste des releases Lody — traçabilité, retour arrière vérifiable, base de la rétention.

Chaque livraison faite par ``scripts/lody-deploy.sh`` ajoute UNE ligne JSON à
``<ops>/releases.jsonl`` (journal en ajout seul, jamais réécrit) :

- le tag immuable (SHA Git court) et le SHA complet ;
- pour chaque composant (``ui``, ``2d``, ``auth``) la référence ``dépôt:tag`` ET l'identifiant d'image
  (``sha256:…``, adressé par le contenu : c'est lui qui fait foi, pas le tag) ;
- la provenance (Dockerfiles, image Node épinglée, ``package-lock.json`` de Remotion) ;
- la version du schéma SQLite du code livré, la version de la base AVANT la livraison et la sauvegarde
  prise juste avant ;
- ``validated`` : vrai seulement si les conteneurs sont devenus « healthy ».

Seule une release ``validated`` peut servir de retour arrière ; une image absente de ce journal (par
exemple les images d'octobre) n'est JAMAIS considérée comme un retour arrière sûr.

Sous-commandes : ``record``, ``list``, ``current``, ``rollback-check``, ``schema-version``.
Bibliothèque standard uniquement : tourne avec le Python de l'hôte.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
RELEASES_FILE = "releases.jsonl"
COMPONENTS = ("ui", "2d", "auth")
# Fichiers dont l'empreinte décrit la recette de build (provenance).
PROVENANCE_FILES = (
    "Dockerfile.lody", "Dockerfile.lody-2d", "Dockerfile.lody-auth", "requirements.lody.txt",
    "renderers/remotion/package-lock.json", "docker-compose.lody.yml", "docker-compose.lody-2d.yml",
)

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def default_ops_dir() -> Path:
    return Path(os.environ.get("LODY_OPS_DIR") or ROOT / ".lody-ops")


def run_command(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- lecture / écriture

def load_releases(ops_dir: Path) -> list[dict[str, Any]]:
    """Releases dans l'ordre d'écriture. Une ligne illisible lève une erreur : on ne devine pas."""
    path = ops_dir / RELEASES_FILE
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{number} illisible ({error}) — journal à vérifier à la main") from error
        if not isinstance(record, dict) or not record.get("tag"):
            raise ValueError(f"{path}:{number} : enregistrement sans tag")
        records.append(record)
    return records


def append_release(ops_dir: Path, record: dict[str, Any]) -> None:
    ops_dir.mkdir(parents=True, exist_ok=True)
    with (ops_dir / RELEASES_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def current_release(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Dernière release VALIDÉE (livraison ou retour arrière) : c'est ce qui tourne."""
    for record in reversed(records):
        if record.get("validated"):
            return record
    return None


def validated_rollbacks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Livraisons validées autres que la courante, la plus récente d'abord, une par tag."""
    current = current_release(records)
    seen = {current["tag"]} if current else set()
    result = []
    for record in reversed(records):
        if record.get("kind", "deploy") != "deploy" or not record.get("validated"):
            continue
        if record["tag"] in seen:
            continue
        seen.add(record["tag"])
        result.append(record)
    return result


def image_ids(record: dict[str, Any]) -> set[str]:
    return {image["id"] for image in (record.get("images") or {}).values() if image.get("id")}


# --------------------------------------------------------------------------- provenance

def schema_version(repo: Path = ROOT) -> int | None:
    """Version de schéma SQLite déclarée par le code (dernier numéro de ``MIGRATIONS``)."""
    try:
        source = (repo / "webui/lody/db.py").read_text(encoding="utf-8")
    except OSError:
        return None
    block = re.search(r"^MIGRATIONS\b[^=]*=\s*\((.*?)^\)", source, re.S | re.M)
    if not block:
        return None
    numbers = [int(n) for n in re.findall(r"^\s*\((\d+),", block.group(1), re.M)]
    return max(numbers) if numbers else None


def file_digest(path: Path) -> str | None:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def provenance(repo: Path = ROOT) -> dict[str, Any]:
    files = {name: file_digest(repo / name) for name in PROVENANCE_FILES}
    node_image = None
    try:
        match = re.search(r"^ARG NODE_IMAGE=(\S+)", (repo / "Dockerfile.lody-2d").read_text(encoding="utf-8"), re.M)
        node_image = match.group(1) if match else None
    except OSError:
        pass
    return {"files": {k: v for k, v in files.items() if v}, "node_image": node_image}


def inspect_image(reference: str, runner: Runner) -> dict[str, Any] | None:
    result = runner(["docker", "image", "inspect", reference])
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return data[0] if data else None


# --------------------------------------------------------------------------- record

def build_record(*, tag: str, git_sha: str, mode: str, images: dict[str, str], validated: bool,
                 runner: Runner, kind: str = "deploy", db_version: int | None = None,
                 backup: str | None = None, health: str = "", repo: Path = ROOT,
                 note: str = "") -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for component, reference in images.items():
        if component not in COMPONENTS:
            raise ValueError(f"composant inconnu : {component}")
        info = inspect_image(reference, runner)
        if info is None:
            raise ValueError(f"image introuvable : {reference} — rien n'est enregistré")
        labels = (info.get("Config") or {}).get("Labels") or {}
        resolved[component] = {
            "ref": reference, "id": info.get("Id"), "repo_digests": info.get("RepoDigests") or [],
            "created": info.get("Created"), "revision": labels.get("org.opencontainers.image.revision"),
            "base_image": labels.get("lody.base-image"), "source_hash": labels.get("lody.source-hash"),
        }
    return {
        "kind": kind, "tag": tag, "git_sha": git_sha, "mode": mode, "images": resolved,
        "validated": bool(validated), "health": health, "schema_version": schema_version(repo),
        "db_version_before": db_version, "backup": backup, "provenance": provenance(repo),
        "recorded_at": now_iso(), "operator": _operator(), "note": note,
    }


def _operator() -> str:
    try:
        return os.environ.get("SUDO_USER") or getpass.getuser()
    except Exception:  # noqa: BLE001 - utilisateur sans entrée passwd : sans importance
        return "?"


# --------------------------------------------------------------------------- retour arrière

def rollback_check(records: list[dict[str, Any]], tag: str, db_version: int | None, runner: Runner,
                   allow_newer_schema: bool = False) -> dict[str, Any]:
    """Peut-on revenir à ``tag`` ? Réponse argumentée ; ``ok`` faux = refus."""
    problems: list[str] = []
    warnings: list[str] = []
    candidates = [r for r in records if r["tag"] == tag and r.get("kind", "deploy") == "deploy"]
    validated = [r for r in candidates if r.get("validated")]
    if not validated:
        reason = "jamais validée (santé non atteinte)" if candidates else "absente du journal des releases"
        return {"ok": False, "tag": tag, "problems": [f"release {tag} {reason} : pas un retour arrière sûr"],
                "warnings": [], "env": {}}
    target = validated[-1]
    for component, image in (target.get("images") or {}).items():
        info = inspect_image(image["ref"], runner)
        if info is None:
            problems.append(f"{component} : image {image['ref']} absente (supprimée ?)")
        elif info.get("Id") != image.get("id"):
            problems.append(f"{component} : {image['ref']} pointe vers {info.get('Id')} au lieu de "
                            f"{image.get('id')} (tag réutilisé) — retour arrière non reproductible")
    target_schema = target.get("schema_version")
    restore = _restore_candidate(records, target)
    if db_version is None:
        problems.append("version de la base inconnue : impossible de vérifier la compatibilité du schéma")
    elif target_schema is None:
        problems.append("version de schéma de la release inconnue")
    elif db_version > target_schema:
        message = (f"base au schéma v{db_version}, release prévue pour v{target_schema} : l'ancienne version "
                   "ignore les colonnes/tables ajoutées (migrations Lody additives), mais les données saisies "
                   "depuis n'y seront pas toutes visibles")
        if restore:
            message += f" ; sauvegarde cohérente avec cette release : {restore}"
        (warnings if allow_newer_schema else problems).append(
            message + ("" if allow_newer_schema else " — relancer avec --allow-newer-schema après lecture"))
    images = target.get("images") or {}
    env = {"LODY_IMAGE_TAG": tag}
    if "auth" in images:
        env["LODY_AUTH_IMAGE_TAG"] = images["auth"]["ref"].rsplit(":", 1)[1]
    return {"ok": not problems, "tag": tag, "mode": target.get("mode", "classic"), "problems": problems,
            "images": {k: v["ref"] for k, v in images.items()}, "git_sha": target.get("git_sha"),
            "warnings": warnings, "env": env, "restore_backup": restore, "schema_version": target_schema}


def _restore_candidate(records: list[dict[str, Any]], target: dict[str, Any]) -> str | None:
    """Sauvegarde prise juste avant la livraison qui a SUIVI ``target`` (état de la base sous ``target``)."""
    index = max(i for i, r in enumerate(records) if r is target)
    for record in records[index + 1:]:
        if record.get("backup"):
            return record["backup"]
    return None


# --------------------------------------------------------------------------- CLI

def _parse_images(values: list[str]) -> dict[str, str]:
    images = {}
    for value in values:
        component, _, reference = value.partition("=")
        if not reference or ":" not in reference:
            raise SystemExit(f"--image attend composant=dépôt:tag, reçu {value!r}")
        images[component] = reference
    return images


def main(argv: list[str] | None = None, runner: Runner = run_command) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ops-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="ajoute une release au journal")
    rec.add_argument("--tag", required=True)
    rec.add_argument("--git-sha", required=True)
    rec.add_argument("--mode", choices=("classic", "2d"), required=True)
    rec.add_argument("--image", action="append", default=[], help="composant=dépôt:tag (ui, 2d, auth)")
    rec.add_argument("--validated", choices=("yes", "no"), required=True)
    rec.add_argument("--kind", choices=("deploy", "rollback"), default="deploy")
    rec.add_argument("--db-version", type=int, default=None)
    rec.add_argument("--backup", default=None)
    rec.add_argument("--health", default="")
    rec.add_argument("--note", default="")
    sub.add_parser("list", help="affiche le journal")
    sub.add_parser("current", help="tag de la release en service")
    chk = sub.add_parser("rollback-check", help="vérifie qu'un retour arrière est sûr")
    chk.add_argument("--tag", required=True)
    chk.add_argument("--db-version", type=int, default=None)
    chk.add_argument("--allow-newer-schema", action="store_true")
    sub.add_parser("schema-version", help="version de schéma SQLite du code")
    args = parser.parse_args(argv)
    ops_dir = args.ops_dir or default_ops_dir()

    if args.command == "schema-version":
        version = schema_version()
        print(version if version is not None else "")
        return 0 if version is not None else 1
    records = load_releases(ops_dir)
    if args.command == "record":
        try:
            record = build_record(tag=args.tag, git_sha=args.git_sha, mode=args.mode,
                                  images=_parse_images(args.image), validated=args.validated == "yes",
                                  runner=runner, kind=args.kind, db_version=args.db_version,
                                  backup=args.backup, health=args.health, note=args.note)
        except ValueError as error:
            print(f"ERREUR : {error}", file=sys.stderr)
            return 1
        append_release(ops_dir, record)
        print(json.dumps({"recorded": record["tag"], "validated": record["validated"]}, ensure_ascii=False))
        return 0
    if args.command == "list":
        current = current_release(records)
        for record in records:
            mark = "*" if record is current else " "
            images = ", ".join(f"{k}={v.get('id', '?')[:19]}" for k, v in (record.get("images") or {}).items())
            print(f"{mark} {record.get('recorded_at', '?')}  {record.get('kind', 'deploy'):8} {record['tag']:14} "
                  f"{'validée' if record.get('validated') else 'NON validée':11} schéma v{record.get('schema_version')}"
                  f"  {images}")
        if not records:
            print("(aucune release enregistrée — phase A pas encore faite sur cet hôte)")
        return 0
    if args.command == "current":
        current = current_release(records)
        if not current:
            return 1
        print(current["tag"])
        return 0
    result = rollback_check(records, args.tag, args.db_version, runner, args.allow_newer_schema)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
