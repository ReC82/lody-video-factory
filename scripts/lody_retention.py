#!/usr/bin/env python3
"""#209 : rétention des images Docker de Lody — simulation par défaut, suppression explicite et prudente.

    scripts/lody-image-retention.sh                 # SIMULATION (défaut) : plan détaillé, rien n'est supprimé
    scripts/lody-image-retention.sh --apply         # suppression des seules images candidates
    scripts/lody-image-retention.sh report          # disque, images, worktrees, alertes 75/85 %
    scripts/lody-image-retention.sh inventory > f   # inventaire JSON (lecture seule), rejouable avec --inventory-file

Périmètre : dépôts ``lody-video-factory``, ``lody-video-factory-2d``, ``lody-auth`` (et images sans tag
portant le label ``lody.component``). Les images MoneyPrinterTurbo et celles des autres projets sont
seulement INVENTORIÉES, jamais supprimées.

Une image est CONSERVÉE si au moins une raison s'applique :

- utilisée par un conteneur, actif OU arrêté (comparaison par identifiant d'image, pas par tag) ;
- release en service ou l'un des ``--min-rollbacks`` (≥ 2) derniers retours arrière VALIDÉS du journal
  ``releases.jsonl`` (voir ``lody_release.py``) ;
- base d'une image conservée (ses couches forment un préfixe de celles de l'image conservée) ;
- tag protégé (``local``, ``latest``), ou tag d'un dépôt hors périmètre (ex. ``ghcr.io/…``) ;
- parmi les ``--keep-recent`` plus récentes de son dépôt, ou plus jeune que ``--min-age-days``.

Fail closed : sans release courante, avec moins de 2 retours arrière validés, si le conteneur en service
ne correspond pas au journal, si l'inventaire est incomplet, si un déploiement tient le verrou ou si un
rendu est actif (ou indéterminé), ``--apply`` est REFUSÉ (code 3) et seul le plan est affiché.

Suppression : ``docker image rm <dépôt:tag>`` tag par tag, SANS ``-f`` (Docker refuse lui-même de
retirer une image utilisée par un conteneur). Jamais de ``prune``. Le gain est ESTIMÉ (borne basse :
couches exclusives selon ``docker system df -v`` ; borne haute : somme des tailles) puis MESURÉ (``df``
avant/après) séparément. Chaque exécution est journalisée dans ``<ops>/retention.jsonl``.

Bibliothèque standard uniquement (Python 3.9+) : tourne avec le Python de l'hôte.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lody_release  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = Path(__file__).resolve().parent / "lody_activity_probe.py"

LODY_REPOSITORIES = ("lody-video-factory", "lody-video-factory-2d", "lody-auth")
# Inventoriés pour information, JAMAIS supprimés : moteur partagé, décision du propriétaire requise.
ENGINE_PATTERN = re.compile(r"moneyprinterturbo", re.I)
PROTECTED_TAGS = ("local", "latest")
UI_CONTAINER = "lody-video-factory-ui"
COMPONENT_LABEL = "lody.component"
JOURNAL_FILE = "retention.jsonl"
LOCK_FILE = "deploy.lock"
WARN_PERCENT = 75
CRITICAL_PERCENT = 85

EXIT_OK, EXIT_PARTIAL, EXIT_INVENTORY, EXIT_REFUSED = 0, 1, 2, 3

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def run_command(args: Sequence[str], input_text: Optional[str] = None) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(list(args), capture_output=True, text=True, check=False, input=input_text)


class InventoryError(RuntimeError):
    """L'inventaire Docker est incomplet : on ne décide rien."""


# --------------------------------------------------------------------------- modèle

@dataclass
class Image:
    id: str
    tags: list[str] = field(default_factory=list)
    created: str = ""
    size: int = 0
    labels: dict[str, str] = field(default_factory=dict)
    layers: list[str] = field(default_factory=list)
    unique_size: Optional[int] = None

    def repositories(self) -> set[str]:
        return {tag.rsplit(":", 1)[0] for tag in self.tags}

    def tag_names(self) -> set[str]:
        return {tag.rsplit(":", 1)[1] for tag in self.tags if ":" in tag}


@dataclass
class Container:
    name: str
    image_id: str
    state: str
    image_ref: str = ""


@dataclass
class Inventory:
    images: list[Image]
    containers: list[Container]
    collected_at: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"images": [asdict(i) for i in self.images], "containers": [asdict(c) for c in self.containers],
                "collected_at": self.collected_at, "warnings": self.warnings}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Inventory":
        return cls(images=[Image(**i) for i in data.get("images", [])],
                   containers=[Container(**c) for c in data.get("containers", [])],
                   collected_at=data.get("collected_at", ""), warnings=list(data.get("warnings", [])))


@dataclass
class Policy:
    repositories: tuple[str, ...] = LODY_REPOSITORIES
    keep_recent: int = 3
    min_age_days: float = 7.0
    min_rollbacks: int = 2
    protected_tags: tuple[str, ...] = PROTECTED_TAGS
    ui_container: str = UI_CONTAINER

    def __post_init__(self) -> None:
        if self.min_rollbacks < 2:
            raise ValueError("au moins deux retours arrière validés doivent être conservés (#209)")
        if self.keep_recent < 1:
            raise ValueError("--keep-recent doit valoir au moins 1")


# --------------------------------------------------------------------------- inventaire réel

_UNITS = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12, "PB": 10**15}


def parse_human_size(value: Any) -> Optional[int]:
    """« 1.23GB » → octets (unités décimales de Docker). ``None`` si illisible."""
    if isinstance(value, (int, float)):
        return int(value)
    match = re.fullmatch(r"\s*([\d.]+)\s*([kKMGTP]?i?B)\s*", str(value or ""))
    if not match:
        return None
    unit = match.group(2).upper().replace("I", "")
    return int(float(match.group(1)) * _UNITS.get(unit, 1))


def _json_or_fail(result: "subprocess.CompletedProcess[str]", what: str) -> Any:
    if result.returncode != 0:
        raise InventoryError(f"{what} : {result.stderr.strip() or 'échec'}")
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError as error:
        raise InventoryError(f"{what} : sortie illisible ({error})") from error


def collect_inventory(runner: Runner = run_command) -> Inventory:
    """Toutes les images et TOUS les conteneurs (y compris arrêtés). Lecture seule."""
    listing = runner(["docker", "image", "ls", "-aq", "--no-trunc"])
    if listing.returncode != 0:
        raise InventoryError(f"docker image ls : {listing.stderr.strip() or 'échec'}")
    ids = sorted(set(listing.stdout.split()))
    images: list[Image] = []
    if ids:
        for raw in _json_or_fail(runner(["docker", "image", "inspect", *ids]), "docker image inspect") or []:
            config = raw.get("Config") or {}
            images.append(Image(
                id=raw["Id"], tags=[t for t in raw.get("RepoTags") or [] if t != "<none>:<none>"],
                created=raw.get("Created", ""), size=int(raw.get("Size") or 0),
                labels=dict(config.get("Labels") or {}), layers=list((raw.get("RootFS") or {}).get("Layers") or [])))
        if len(images) != len(ids):
            raise InventoryError(f"{len(ids)} images listées, {len(images)} inspectées")
    listing = runner(["docker", "ps", "-aq", "--no-trunc"])
    if listing.returncode != 0:
        raise InventoryError(f"docker ps -a : {listing.stderr.strip() or 'échec'}")
    container_ids = listing.stdout.split()
    containers: list[Container] = []
    if container_ids:
        for raw in _json_or_fail(runner(["docker", "container", "inspect", *container_ids]),
                                 "docker container inspect") or []:
            containers.append(Container(name=str(raw.get("Name", "")).lstrip("/"), image_id=raw.get("Image", ""),
                                        state=(raw.get("State") or {}).get("Status", "?"),
                                        image_ref=(raw.get("Config") or {}).get("Image", "")))
        if len(containers) != len(container_ids):
            raise InventoryError(f"{len(container_ids)} conteneurs listés, {len(containers)} inspectés")
    inventory = Inventory(images=images, containers=containers, collected_at=_now().isoformat(timespec="seconds"))
    _add_unique_sizes(inventory, runner)
    return inventory


def _add_unique_sizes(inventory: Inventory, runner: Runner) -> None:
    """Taille des couches EXCLUSIVES de chaque image, selon Docker. Facultatif (borne basse du gain)."""
    result = runner(["docker", "system", "df", "-v", "--format", "{{json .}}"])
    try:
        data = json.loads(result.stdout) if result.returncode == 0 else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        inventory.warnings.append("docker system df -v indisponible : borne basse du gain inconnue")
        return
    unique: dict[str, int] = {}
    for row in data.get("Images") or []:
        size = parse_human_size(row.get("UniqueSize"))
        identifier = str(row.get("ID", ""))
        if size is None or not identifier:
            continue
        for image in inventory.images:
            if image.id == identifier or image.id.split(":")[-1].startswith(identifier.split(":")[-1]):
                unique[image.id] = size
    for image in inventory.images:
        image.unique_size = unique.get(image.id)


# --------------------------------------------------------------------------- plan

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _created(image: Image) -> datetime:
    text = re.sub(r"(\.\d{6})\d+", r"\1", image.created.replace("Z", "+00:00"))
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return datetime.max.replace(tzinfo=timezone.utc)  # date illisible = « récente » : on garde
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def in_scope(image: Image, policy: Policy) -> bool:
    if image.tags:
        return bool(image.repositories() & set(policy.repositories))
    return bool(image.labels.get(COMPONENT_LABEL))


def build_plan(inventory: Inventory, releases: list[dict[str, Any]], policy: Policy,
               now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or _now()
    by_id = {image.id: image for image in inventory.images}
    scoped = [image for image in inventory.images if in_scope(image, policy)]
    keep: dict[str, list[str]] = {image.id: [] for image in scoped}
    blocked: list[str] = []

    def protect(image_id: str, reason: str) -> None:
        if image_id in keep:
            keep[image_id].append(reason)

    for container in inventory.containers:
        protect(container.image_id, f"conteneur {container.name} ({container.state})")
        if container.image_id not in by_id:
            blocked.append(f"conteneur {container.name} : image {container.image_id[:19]} absente de l'inventaire")

    current = lody_release.current_release(releases)
    rollbacks = lody_release.validated_rollbacks(releases)
    if current is None:
        blocked.append("aucune release validée dans le journal (phase A : livrer avec scripts/lody-deploy.sh)")
    else:
        for image_id in lody_release.image_ids(current):
            protect(image_id, f"release en service {current['tag']}")
            if image_id not in by_id:
                blocked.append(f"release en service {current['tag']} : image {image_id[:19]} introuvable")
    if len(rollbacks) < policy.min_rollbacks:
        blocked.append(f"{len(rollbacks)} retour(s) arrière validé(s) dans le journal, {policy.min_rollbacks} exigés")
    for record in rollbacks[:policy.min_rollbacks]:
        for image_id in lody_release.image_ids(record):
            protect(image_id, f"retour arrière validé {record['tag']}")
    if current is not None:
        expected = lody_release.image_ids(current)
        for container in inventory.containers:
            if container.name == policy.ui_container and container.state == "running" \
                    and container.image_id not in expected:
                blocked.append(f"{container.name} tourne sur {container.image_id[:19]}, absente de la release "
                               f"{current['tag']} du journal (livraison manuelle ?)")

    for image in scoped:
        foreign = sorted(r for r in image.repositories() if r not in policy.repositories)
        if foreign:
            protect(image.id, "aussi étiquetée hors périmètre : " + ", ".join(foreign))
        protected = sorted(image.tag_names() & set(policy.protected_tags))
        if protected:
            protect(image.id, "tag protégé " + ", ".join(protected))
        age = now - _created(image)
        if age < timedelta(days=policy.min_age_days):
            protect(image.id, f"récente ({max(age.days, 0)} j < {policy.min_age_days:g} j)")

    for repository in policy.repositories:
        members = [i for i in scoped if repository in i.repositories()]
        for image in sorted(members, key=_created, reverse=True)[:policy.keep_recent]:
            protect(image.id, f"parmi les {policy.keep_recent} plus récentes de {repository}")

    # Bases : une image dont les couches sont un préfixe de celles d'une image conservée (point fixe).
    changed = True
    while changed:
        changed = False
        survivors = [i for i in inventory.images if i.id not in keep or keep[i.id]]
        for image in scoped:
            if keep[image.id] or not image.layers:
                continue
            for other in survivors:
                if other.id != image.id and len(other.layers) > len(image.layers) \
                        and other.layers[:len(image.layers)] == image.layers:
                    label = ", ".join(other.tags[:1]) or other.id[:19]
                    keep[image.id].append(f"base de l'image conservée {label}")
                    changed = True
                    break

    candidates = [image for image in scoped if not keep[image.id]]
    kept = [image for image in scoped if keep[image.id]]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "policy": asdict(policy),
        "blocked": blocked,
        "warnings": list(inventory.warnings),
        "current": current["tag"] if current else None,
        "rollbacks": [r["tag"] for r in rollbacks[:policy.min_rollbacks]],
        "candidates": [_describe(i) for i in sorted(candidates, key=_created)],
        "kept": [dict(_describe(i), reasons=keep[i.id]) for i in sorted(kept, key=_created, reverse=True)],
        "estimate": estimate(inventory, {i.id for i in candidates}),
        "summary": summarize(inventory, policy),
    }


def _describe(image: Image) -> dict[str, Any]:
    return {"id": image.id, "tags": image.tags, "created": image.created, "size": image.size,
            "unique_size": image.unique_size, "component": image.labels.get(COMPONENT_LABEL),
            "revision": image.labels.get("org.opencontainers.image.revision")}


def estimate(inventory: Inventory, removed: set[str]) -> dict[str, Any]:
    """Gain ESTIMÉ (pas mesuré) : bornes, et nombre de couches qui ne serviraient plus à personne."""
    targets = [i for i in inventory.images if i.id in removed]
    remaining_layers = {layer for i in inventory.images if i.id not in removed for layer in i.layers}
    freed_layers = {layer for i in targets for layer in i.layers} - remaining_layers
    unique = [i.unique_size for i in targets]
    return {
        "images": len(targets),
        "tags": sum(len(i.tags) for i in targets),
        "lower_bound_bytes": sum(u for u in unique if u is not None),
        "lower_bound_complete": all(u is not None for u in unique),
        "upper_bound_bytes": sum(i.size for i in targets),
        "freed_layers": len(freed_layers),
    }


def summarize(inventory: Inventory, policy: Policy) -> dict[str, Any]:
    """Tags ≠ images : nombre d'étiquettes et d'images UNIQUES par dépôt, Lody, moteur et autres."""
    repos: dict[str, dict[str, Any]] = {}
    for image in inventory.images:
        for repository in image.repositories() or {"<sans tag>"}:
            entry = repos.setdefault(repository, {"tags": 0, "images": set(), "size": 0})
            entry["tags"] += sum(1 for t in image.tags if t.rsplit(":", 1)[0] == repository)
            if image.id not in entry["images"]:
                entry["images"].add(image.id)
                entry["size"] += image.size
    used = {c.image_id for c in inventory.containers}

    def group(name: str) -> str:
        if name in policy.repositories:
            return "lody"
        if ENGINE_PATTERN.search(name):
            return "moteur (décision propriétaire requise)"
        return "autres projets (jamais touchés)"

    result: dict[str, Any] = {}
    for repository, entry in sorted(repos.items()):
        result[repository] = {"group": group(repository), "tags": entry["tags"], "images": len(entry["images"]),
                              "size_bytes": entry["size"],
                              "used_by_containers": len(entry["images"] & used)}
    unlabeled_dangling = [i for i in inventory.images if not i.tags and not i.labels.get(COMPONENT_LABEL)]
    result["<sans tag>"] = dict(result.get("<sans tag>", {}), group="non attribuables (hors périmètre)",
                                images=len(unlabeled_dangling))
    return result


# --------------------------------------------------------------------------- application

def apply_plan(plan: dict[str, Any], runner: Runner) -> list[dict[str, Any]]:
    """Retire les candidats tag par tag, SANS -f. Idempotent : une image déjà absente compte comme faite."""
    actions = []
    for candidate in plan["candidates"]:
        references = candidate["tags"] or [candidate["id"]]
        for reference in references:
            result = runner(["docker", "image", "rm", reference])
            stderr = (result.stderr or "").strip()
            if result.returncode == 0:
                outcome = "supprimé"
            elif "No such image" in stderr or "No such object" in stderr:
                outcome = "déjà absent"
            else:
                outcome = "refusé"
            actions.append({"ref": reference, "image": candidate["id"], "outcome": outcome,
                            "detail": stderr[:300]})
    return actions


def disk_usage(path: Path) -> dict[str, Any]:
    for candidate in (path, Path("/")):
        try:
            usage = shutil.disk_usage(candidate)
        except OSError:
            continue
        return {"path": str(candidate), "total": usage.total, "used": usage.used, "free": usage.free,
                "percent": round(usage.used * 100 / usage.total, 1) if usage.total else 0.0}
    return {"path": str(path), "total": 0, "used": 0, "free": 0, "percent": 0.0}


def alert_level(percent: float, warn: float = WARN_PERCENT, critical: float = CRITICAL_PERCENT) -> str:
    if percent >= critical:
        return "critique"
    if percent >= warn:
        return "alerte"
    return "ok"


# --------------------------------------------------------------------------- garde-fous d'exécution

class DeployLock:
    """Verrou de déploiement partagé avec scripts/lody-deploy.sh (flock sur <ops>/deploy.lock)."""

    def __init__(self, ops_dir: Path) -> None:
        self.path = ops_dir / LOCK_FILE
        self.handle = None

    def acquire(self) -> bool:
        if os.environ.get("LODY_DEPLOY_LOCK_HELD") == "1":
            return True  # le script de livraison, qui nous appelle, tient déjà le verrou
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EACCES):
                self.handle.close()
                self.handle = None
                return False
            raise
        return True

    def release(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def activity(runner: Runner, container: str) -> dict[str, Any]:
    """Sonde d'activité DANS le conteneur d'interface. Conteneur arrêté = aucun rendu possible."""
    state = runner(["docker", "container", "inspect", "--format", "{{.State.Status}}", container])
    if state.returncode != 0:
        return {"state": "idle", "note": f"conteneur {container} absent : aucun rendu possible"}
    if state.stdout.strip() != "running":
        return {"state": "idle", "note": f"conteneur {container} {state.stdout.strip()} : aucun rendu possible"}
    result = runner(["docker", "exec", "-i", container, "python3", "-"],
                    input_text=PROBE_SCRIPT.read_text(encoding="utf-8"))
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"state": "unknown", "note": f"sonde illisible (code {result.returncode}) : {result.stderr[:200]}"}
    return data if data.get("state") in ("idle", "active", "unknown") else {"state": "unknown", "note": "sonde"}


# --------------------------------------------------------------------------- présentation

def human(size: Optional[int]) -> str:
    if size is None:
        return "?"
    value = float(size)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(value) < 1000 or unit == "To":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.2f} {unit}"
        value /= 1000
    return f"{value:.2f} To"


def render_plan(plan: dict[str, Any], mode: str) -> str:
    lines = [f"== Rétention des images Lody — {mode} — {plan['generated_at']}"]
    lines.append(f"Release en service : {plan['current'] or 'INCONNUE'} ; retours arrière protégés : "
                 f"{', '.join(plan['rollbacks']) or 'aucun'}")
    lines.append("")
    lines.append("Inventaire (étiquettes ≠ images uniques) :")
    for repository, entry in plan["summary"].items():
        if repository == "<sans tag>":
            lines.append(f"  {'<sans tag>':34} {entry.get('images', 0):3} image(s) — {entry['group']}")
            continue
        lines.append(f"  {repository[:34]:34} {entry['tags']:3} tag(s) {entry['images']:3} image(s) "
                     f"{human(entry['size_bytes']):>10}  utilisées: {entry['used_by_containers']}  [{entry['group']}]")
    lines.append("")
    lines.append(f"Conservées ({len(plan['kept'])}) :")
    for image in plan["kept"]:
        lines.append(f"  KEEP {image['id'][7:19]} {', '.join(image['tags']) or '<sans tag>'}")
        for reason in image["reasons"]:
            lines.append(f"         - {reason}")
    lines.append("")
    lines.append(f"Candidates à la suppression ({len(plan['candidates'])}) :")
    for image in plan["candidates"]:
        lines.append(f"  DEL  {image['id'][7:19]} {', '.join(image['tags']) or '<sans tag>'} "
                     f"(créée {image['created'][:10]}, {human(image['size'])}, exclusive {human(image['unique_size'])})")
    est = plan["estimate"]
    lines.append("")
    lines.append(f"Gain ESTIMÉ : au moins {human(est['lower_bound_bytes'])}"
                 f"{'' if est['lower_bound_complete'] else ' (borne basse partielle)'}, au plus "
                 f"{human(est['upper_bound_bytes'])} ; {est['images']} image(s), {est['tags']} tag(s), "
                 f"{est['freed_layers']} couche(s) libérée(s). Le gain RÉEL se mesure après --apply (df).")
    for warning in plan["warnings"]:
        lines.append(f"Avertissement : {warning}")
    if plan["blocked"]:
        lines.append("")
        lines.append("ALERTE — suppression REFUSÉE (fail closed) :")
        lines.extend(f"  - {reason}" for reason in plan["blocked"])
    return "\n".join(lines)


def journal(ops_dir: Path, entry: dict[str, Any]) -> None:
    ops_dir.mkdir(parents=True, exist_ok=True)
    with (ops_dir / JOURNAL_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def directory_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


# --------------------------------------------------------------------------- CLI

def _policy(args: argparse.Namespace) -> Policy:
    return Policy(keep_recent=args.keep_recent, min_age_days=args.min_age_days, min_rollbacks=args.min_rollbacks,
                  ui_container=args.container)


def _inventory(args: argparse.Namespace, runner: Runner) -> Inventory:
    if args.inventory_file:
        return Inventory.from_json(json.loads(Path(args.inventory_file).read_text(encoding="utf-8")))
    return collect_inventory(runner)


def command_plan(args: argparse.Namespace, runner: Runner, out=None) -> int:
    out = out or sys.stdout
    ops_dir = args.ops_dir or lody_release.default_ops_dir()
    policy = _policy(args)
    try:
        releases = lody_release.load_releases(ops_dir)
        inventory = _inventory(args, runner)
    except (InventoryError, ValueError, OSError) as error:
        print(f"ERREUR inventaire : {error} — rien n'est supprimé", file=sys.stderr)
        journal(ops_dir, {"at": _now().isoformat(timespec="seconds"), "mode": "erreur", "error": str(error)})
        return EXIT_INVENTORY
    plan = build_plan(inventory, releases, policy)
    mode = "APPLICATION" if args.apply else "SIMULATION (rien n'est supprimé)"
    entry: dict[str, Any] = {"at": plan["generated_at"], "mode": "apply" if args.apply else "dry-run",
                             "current": plan["current"], "blocked": plan["blocked"],
                             "candidates": [c["id"] for c in plan["candidates"]], "estimate": plan["estimate"]}
    if not args.apply:
        print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else render_plan(plan, mode), file=out)
        journal(ops_dir, entry)
        return EXIT_OK

    refusals = list(plan["blocked"])
    if args.inventory_file:
        refusals.append("--apply impossible sur un inventaire hors ligne (--inventory-file)")
    lock = DeployLock(ops_dir)
    locked = not refusals and lock.acquire()
    if not refusals and not locked:
        refusals.append("verrou de déploiement occupé : une livraison est en cours")
    try:
        if not refusals:
            probe = activity(runner, policy.ui_container)
            entry["activity"] = probe
            if probe.get("state") != "idle":
                refusals.append(f"activité Lody {probe.get('state')} : "
                                + "; ".join(probe.get("productions", []) + probe.get("processes", [])
                                            + probe.get("stale_productions", []) + probe.get("errors", [])
                                            + [probe.get("note", "")]).strip("; "))
        if not refusals:
            # Second inventaire juste avant d'agir : seules les images candidates DEUX fois sont retirées.
            try:
                fresh = build_plan(collect_inventory(runner), lody_release.load_releases(ops_dir), policy)
            except (InventoryError, ValueError) as error:
                refusals.append(f"second inventaire impossible : {error}")
            else:
                again = {c["id"] for c in fresh["candidates"]}
                refusals.extend(fresh["blocked"])
                plan["candidates"] = [c for c in plan["candidates"] if c["id"] in again]
        if refusals:
            plan["blocked"] = refusals
            print(render_plan(plan, "APPLICATION REFUSÉE — simulation seulement"), file=out)
            entry.update(mode="apply-refused", blocked=refusals)
            journal(ops_dir, entry)
            return EXIT_REFUSED
        before = disk_usage(Path(args.disk_path))
        actions = apply_plan(plan, runner)
        after = disk_usage(Path(args.disk_path))
    finally:
        lock.release()
    measured = before["used"] - after["used"]
    print(render_plan(plan, mode), file=out)
    print("", file=out)
    for action in actions:
        print(f"  {action['outcome']:12} {action['ref']}" + (f" — {action['detail']}" if action["outcome"] == "refusé"
                                                             else ""), file=out)
    print(f"\nGain MESURÉ (df {before['path']}) : {human(measured)} — disque {before['percent']} % → "
          f"{after['percent']} % (d'autres projets écrivent aussi sur ce disque : mesure indicative)", file=out)
    entry.update(actions=actions, disk_before=before, disk_after=after, measured_bytes=measured)
    journal(ops_dir, entry)
    return EXIT_PARTIAL if any(a["outcome"] == "refusé" for a in actions) else EXIT_OK


def command_report(args: argparse.Namespace, runner: Runner, out=None) -> int:
    out = out or sys.stdout
    ops_dir = args.ops_dir or lody_release.default_ops_dir()
    disk = disk_usage(Path(args.disk_path))
    level = alert_level(disk["percent"], args.warn, args.critical)
    report: dict[str, Any] = {"at": _now().isoformat(timespec="seconds"), "disk": disk, "level": level}
    lines = [f"Disque {disk['path']} : {disk['percent']} % utilisé ({human(disk['free'])} libres) — niveau {level}"]
    try:
        plan = build_plan(_inventory(args, runner), lody_release.load_releases(ops_dir), _policy(args))
    except (InventoryError, ValueError, OSError) as error:
        plan = None
        lines.append(f"Images : inventaire impossible ({error})")
    if plan is not None:
        lody = {k: v for k, v in plan["summary"].items() if v.get("group") == "lody"}
        lines.append("Images Lody : " + ", ".join(f"{k} {v['tags']} tag(s)/{v['images']} image(s)"
                                                  for k, v in lody.items()))
        est = plan["estimate"]
        lines.append(f"Candidates : {len(plan['candidates'])} image(s), gain estimé "
                     f"{human(est['lower_bound_bytes'])} – {human(est['upper_bound_bytes'])}"
                     + (" — suppression bloquée : " + "; ".join(plan["blocked"]) if plan["blocked"] else ""))
        report["plan"] = {"candidates": len(plan["candidates"]), "estimate": est, "blocked": plan["blocked"]}
    if args.worktrees_dir:
        sizes = {}
        root = Path(args.worktrees_dir)
        for entry in sorted(root.iterdir()) if root.is_dir() else []:
            if entry.is_dir():
                sizes[entry.name] = directory_size(entry)
        report["worktrees"] = sizes
        lines.append(f"Worktrees ({root}) : {len(sizes)}, {human(sum(sizes.values()))} — nettoyage : "
                     "python3 deploy/lodyorch/worktree_gc.py (simulation)")
    if level != "ok":
        lines.append(f"ALERTE disque {level} (seuils {args.warn:g}/{args.critical:g} %) — suggestions : "
                     "1) scripts/lody-image-retention.sh (plan) puis --apply si non bloqué ; "
                     "2) worktree_gc.py ; 3) proposition BuildKit/moteur dans docs/lody-retention.md "
                     "(décision propriétaire).")
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else "\n".join(lines), file=out)
    journal(ops_dir, dict(report, mode="report"))
    if args.alert_exit and level != "ok":
        return 10 if level == "alerte" else 11
    return EXIT_OK


def main(argv: Optional[list[str]] = None, runner: Runner = run_command) -> int:
    parser = argparse.ArgumentParser(description="Rétention des images Docker Lody (#209)")
    parser.add_argument("command", nargs="?", default="plan", choices=("plan", "report", "inventory"))
    parser.add_argument("--apply", action="store_true", help="supprime réellement (sinon : simulation)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ops-dir", type=Path, default=None)
    parser.add_argument("--inventory-file", default=None, help="rejoue un inventaire JSON (simulation)")
    parser.add_argument("--keep-recent", type=int, default=3)
    parser.add_argument("--min-age-days", type=float, default=7.0)
    parser.add_argument("--min-rollbacks", type=int, default=2)
    parser.add_argument("--container", default=UI_CONTAINER)
    parser.add_argument("--disk-path", default="/var/lib/docker")
    parser.add_argument("--worktrees-dir", default=None)
    parser.add_argument("--warn", type=float, default=WARN_PERCENT)
    parser.add_argument("--critical", type=float, default=CRITICAL_PERCENT)
    parser.add_argument("--alert-exit", action="store_true", help="report : code 10/11 si alerte/critique")
    args = parser.parse_args(argv)
    try:
        _policy(args)
    except ValueError as error:
        parser.error(str(error))
    if args.command == "inventory":
        try:
            print(json.dumps(collect_inventory(runner).to_json(), ensure_ascii=False, indent=2))
        except InventoryError as error:
            print(f"ERREUR inventaire : {error}", file=sys.stderr)
            return EXIT_INVENTORY
        return EXIT_OK
    if args.command == "report":
        return command_report(args, runner)
    return command_plan(args, runner)


if __name__ == "__main__":
    sys.exit(main())
