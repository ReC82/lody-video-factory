"""Préflight du worker 2D Remotion (#154) : environnement, isolement du pilote DEV et licence Remotion.

Gratuit, sans réseau, sans exécuter de rendu ni lire un secret : il vérifie la présence et la version des
outils (Node, moteur ``renderers/remotion``, Chrome headless, bundle préconstruit, ffmpeg/ffprobe), les
plafonds CPU/mémoire du conteneur, la place disque, l'absence de secrets visibles (noms de variables
seulement, jamais leurs valeurs) et l'état de la **licence Remotion**.

Licence Remotion — jamais supposée gratuite. ``LODY_REMOTION_LICENSE`` porte la décision ÉCRITE du
propriétaire :

- vide (défaut) : décision en attente → le rendu est refusé, sauf évaluation explicite d'un pilote DEV
  (``LODY_REMOTION_EVALUATION=1``, posé par ``docker-compose.lody-dev-2d.yml``) ;
- ``free:<motif>`` : le propriétaire a vérifié qu'il entre dans un cas d'usage gratuit de la licence Remotion ;
- ``company:<référence>`` : licence entreprise acquise (référence de commande ou de contrat, pas de clé).

Usage : ``python -m lody.animation.preflight [--json]`` (code de sortie 0 = aucun point bloquant).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lody import settings
from lody.animation import worker as animation_worker

LICENSE_ENV = "LODY_REMOTION_LICENSE"
EVALUATION_ENV = "LODY_REMOTION_EVALUATION"
IMAGE_ENV = "LODY_2D_WORKER_IMAGE"
LICENSE_URL = "https://www.remotion.dev/license"
LICENSE_KINDS = {"free": "usage gratuit vérifié par le propriétaire", "company": "licence entreprise"}

MIN_NODE_MAJOR = 18
MIN_MEMORY = 1200 * 1024 ** 2     # pic mesuré ≈ 1,5 Go en 1080×1920 (#150) : en dessous, échec probable
MIN_DISK = 2 * 1024 ** 3
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.IGNORECASE)
# Variables propres au pilote, dont le nom contient un mot sensible sans porter de secret.
SAFE_NAMES = frozenset({LICENSE_ENV})

OK, WARNING, ERROR, DECISION = "ok", "warning", "error", "decision"
BLOCKING = frozenset({ERROR, DECISION})

# Usage prévu, rapporté tel quel au propriétaire pour sa décision (rien n'est supposé sur son éligibilité).
PLANNED_USE = (
    "Rendu automatisé côté serveur de vidéos 2D (Remotion 4, @remotion/renderer), une scène à la fois, "
    "pour les productions de Lody Video Factory. Pilote DEV : évaluation, aucune vidéo publiée.")
CONDITIONS = (
    "La licence Remotion n'est gratuite que pour certains cas (particuliers, très petites structures, "
    "organisations à but non lucratif, selon les termes en vigueur) ; au-delà, une licence entreprise est "
    "exigée, avec une offre spécifique pour le rendu automatisé. Conditions et plafonds à relire sur "
    f"{LICENSE_URL} au moment de la décision.")


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    state: str          # ok | warning | error | decision
    message: str
    detail: str = ""

    @property
    def blocking(self) -> bool:
        return self.state in BLOCKING


# -- licence ---------------------------------------------------------------------------------------------------
def license_check(env: dict[str, str] | None = None) -> Check:
    env = os.environ if env is None else env
    raw = (env.get(LICENSE_ENV) or "").strip()
    evaluation = (env.get(EVALUATION_ENV) or "").strip() == "1"
    if not raw:
        if evaluation:
            return Check("license", "Licence Remotion", WARNING,
                         "Licence Remotion non décidée : évaluation DEV uniquement, aucune vidéo à publier.",
                         f"Usage prévu : {PLANNED_USE} {CONDITIONS}")
        return Check("license", "Licence Remotion", DECISION,
                     "Licence Remotion à décider par le propriétaire avant tout rendu.",
                     f"Usage prévu : {PLANNED_USE} {CONDITIONS} Renseigner {LICENSE_ENV} "
                     f"(free:<motif> ou company:<référence>), ou {EVALUATION_ENV}=1 pour un pilote DEV.")
    kind, _, reference = raw.partition(":")
    if kind not in LICENSE_KINDS or not reference.strip():
        return Check("license", "Licence Remotion", ERROR,
                     f"{LICENSE_ENV} illisible : « free:<motif> » ou « company:<référence> » attendu.")
    return Check("license", "Licence Remotion", OK,
                 f"Licence Remotion : {LICENSE_KINDS[kind]} ({reference.strip()[:60]}).")


def license_allows_render(env: dict[str, str] | None = None) -> tuple[bool, str]:
    """(rendu autorisé, message) — utilisé par le préflight du fournisseur Animation 2D."""
    check = license_check(env)
    return not check.blocking, check.message


# -- environnement ---------------------------------------------------------------------------------------------
def _renderer_root(command: list[str]) -> Path | None:
    for part in command:
        path = Path(part)
        if path.suffix in (".mjs", ".js") and path.parent.name == "bin":
            return path.parent.parent
    return None


def _run(command: list[str]) -> str | None:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False,
                              env=animation_worker.minimal_env())
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _package_version(root: Path, name: str) -> str | None:
    try:
        return json.loads((root / "node_modules" / name / "package.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return None


def _cgroup_limits(base: Path = Path("/sys/fs/cgroup")) -> tuple[float | None, int | None]:
    cpus = memory = None
    try:
        quota, period = (base / "cpu.max").read_text().split()[:2]
        if quota != "max":
            cpus = int(quota) / int(period)
    except (OSError, ValueError):
        pass
    try:
        raw = (base / "memory.max").read_text().strip()
        if raw != "max":
            memory = int(raw)
    except (OSError, ValueError):
        pass
    return cpus, memory


def _host_memory() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def engine_checks(command: list[str] | None = None, env: dict[str, str] | None = None) -> list[Check]:
    env = os.environ if env is None else env
    in_image = (env.get(IMAGE_ENV) or "") == "1"
    command = animation_worker.renderer_command() if command is None else command
    checks: list[Check] = []
    if not command:
        return [Check("renderer", "Moteur de scène", ERROR,
                      f"{animation_worker.RENDERER_ENV} absente : moteur de rendu 2D non configuré.")]
    executable = shutil.which(command[0]) or (command[0] if Path(command[0]).is_file() else None)
    if not executable:
        return [Check("renderer", "Moteur de scène", ERROR, f"Exécutable « {Path(command[0]).name} » introuvable.")]
    root = _renderer_root(command)
    if root is None or not (root / "package.json").is_file():
        checks.append(Check("renderer", "Moteur de scène", WARNING,
                            "Moteur de scène externe (hors renderers/remotion) : contrôles Remotion ignorés."))
        return checks
    checks.append(Check("renderer", "Moteur de scène", OK, f"Moteur renderers/remotion ({root})."))
    if Path(executable).name.startswith("node"):
        version = _run([executable, "--version"]) or ""
        match = re.match(r"v(\d+)\.", version)
        if not match:
            checks.append(Check("node", "Node.js", ERROR, "Node.js ne répond pas."))
        elif int(match.group(1)) < MIN_NODE_MAJOR:
            checks.append(Check("node", "Node.js", ERROR, f"Node.js {version} : {MIN_NODE_MAJOR} ou plus requis."))
        else:
            checks.append(Check("node", "Node.js", OK, f"Node.js {version}."))
    remotion = _package_version(root, "@remotion/renderer")
    if not remotion:
        checks.append(Check("modules", "Dépendances npm", ERROR,
                            "Dépendances du moteur absentes : lancer « npm ci » dans renderers/remotion."))
        return checks
    checks.append(Check("modules", "Dépendances npm", OK, f"Remotion {remotion} (versions du package-lock)."))
    browsers = sorted((root / "node_modules" / ".remotion" / "chrome-headless-shell").glob("*/*/chrome-headless-shell"))
    if browsers:
        checks.append(Check("browser", "Chrome headless", OK, "Chrome headless shell installé."))
    else:
        checks.append(Check("browser", "Chrome headless", ERROR if in_image else WARNING,
                            "Chrome headless absent : il serait téléchargé au premier rendu (~100 Mo)."))
    bundles = sorted((root / ".bundle").glob("*/index.html"))
    if bundles:
        checks.append(Check("bundle", "Bundle Remotion", OK, "Bundle webpack préconstruit."))
    else:
        checks.append(Check("bundle", "Bundle Remotion", ERROR if in_image else WARNING,
                            "Bundle absent : construit au premier rendu (pic mémoire supplémentaire)."))
    return checks


def tool_checks() -> list[Check]:
    ffmpeg = animation_worker.ffmpeg_exe()
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        return [Check("ffmpeg", "ffmpeg", ERROR, "ffmpeg introuvable : assemblage et mixage impossibles.")]
    return [Check("ffmpeg", "ffmpeg / ffprobe", OK if ffprobe else WARNING,
                  "ffmpeg et ffprobe disponibles." if ffprobe else "ffprobe absent : durées non mesurées.")]


def resource_checks(data_dir: Path | None = None, cgroup: Path = Path("/sys/fs/cgroup")) -> list[Check]:
    cpus, memory = _cgroup_limits(cgroup)
    host_cpus, host_memory = os.cpu_count() or 1, _host_memory()
    available = memory if memory is not None else host_memory
    cpu_text = f"{cpus:g} CPU (plafond)" if cpus else f"{host_cpus} CPU (sans plafond)"
    mem_text = (f"{available / 1024 ** 3:.1f} Go" + (" (plafond)" if memory is not None else " (hôte, sans plafond)")
                if available else "mémoire inconnue")
    checks = [Check("resources", "CPU / mémoire",
                    WARNING if available and available < MIN_MEMORY else OK,
                    f"{cpu_text}, {mem_text}." + (" Moins de 1,2 Go : rendu 1080×1920 risqué (pic ≈ 1,5 Go)."
                                                  if available and available < MIN_MEMORY else ""))]
    target = data_dir or settings.data_dir()
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        free = shutil.disk_usage(target).free
    except OSError:
        free = None
    if free is None:
        checks.append(Check("disk", "Disque", WARNING, "Espace disque impossible à vérifier."))
    else:
        checks.append(Check("disk", "Disque", WARNING if free < MIN_DISK else OK,
                            f"{free / 1024 ** 3:.1f} Go libres sur le dossier de données."))
    return checks


def isolation_checks(env: dict[str, str] | None = None, config: Path | None = None,
                     secrets_dir: Path = Path("/secrets")) -> list[Check]:
    """Le pilote DEV ne doit voir aucun secret : noms de variables sensibles, config.toml, dossier secrets."""
    env = os.environ if env is None else env
    names = sorted(name for name in env if SECRET_NAME.search(name) and name not in SAFE_NAMES)
    config = settings.config_path() if config is None else config
    problems = []
    if names:
        problems.append("variables sensibles visibles : " + ", ".join(names[:8]))
    if config.is_file():
        problems.append("config.toml visible")
    if secrets_dir.exists():
        problems.append(f"dossier {secrets_dir} monté")
    if problems:
        return [Check("isolation", "Isolement du pilote", WARNING,
                      "Le worker voit des secrets de l'application (il ne les transmet jamais au moteur) : "
                      + " ; ".join(problems) + ".")]
    return [Check("isolation", "Isolement du pilote", OK, "Aucun secret visible (variables, config.toml, secrets/).")]


def run_checks(env: dict[str, str] | None = None) -> list[Check]:
    return [*engine_checks(env=env), *tool_checks(), *resource_checks(), *isolation_checks(env), license_check(env)]


def summary(checks: list[Check]) -> dict[str, Any]:
    return {"ready": not any(check.blocking for check in checks), "checks": [asdict(check) for check in checks]}


ICONS = {OK: "OK ", WARNING: "AVERT.", ERROR: "ERREUR", DECISION: "DÉCISION"}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    checks = run_checks()
    if "--json" in argv:
        print(json.dumps(summary(checks), ensure_ascii=False, indent=1))
    else:
        for check in checks:
            print(f"[{ICONS[check.state]:<8}] {check.label} — {check.message}")
            if check.detail:
                print(f"           {check.detail}")
        print("Prêt pour un rendu 2D." if not any(c.blocking for c in checks) else "Point(s) bloquant(s) ci-dessus.")
    return 0 if not any(check.blocking for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
