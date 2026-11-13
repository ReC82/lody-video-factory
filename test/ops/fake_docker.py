#!/usr/bin/env python3
"""Faux ``docker`` pour les tests #209 : images, tags, couches, conteneurs (actifs/arrêtés), santé, sonde.

Deux usages :

- en Python : ``FakeDocker(state)`` est un *runner* ``(args, input_text=None) -> CompletedProcess`` ;
- en exécutable (tests du script shell) : ``FAKE_DOCKER_STATE=<fichier.json> fake_docker.py <args>``,
  l'état est relu et réécrit à chaque appel.

Toute commande destructrice large (``prune``, ``-f``/``--force`` sur ``image rm``) est ENREGISTRÉE puis
refusée : les tests vérifient qu'elle n'apparaît jamais.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from typing import Any, Optional

BUILD_LABELS = {"LODY_GIT_SHA": "org.opencontainers.image.revision", "LODY_SOURCE_HASH": "lody.source-hash",
                "LODY_BASE_IMAGE": "lody.base-image"}
COMPONENTS = {"Dockerfile.lody": "ui", "Dockerfile.lody-2d": "2d", "Dockerfile.lody-auth": "auth"}


def image(id_: str, tags: list[str], created: str = "2026-10-01T10:00:00Z", size: int = 1_000_000_000,
          layers: Optional[list[str]] = None, labels: Optional[dict[str, str]] = None,
          unique: Optional[int] = None) -> dict[str, Any]:
    return {"Id": id_, "RepoTags": list(tags), "RepoDigests": [], "Created": created, "Size": size,
            "Config": {"Labels": dict(labels or {})}, "RootFS": {"Layers": list(layers or [f"layer-{id_}"])},
            "unique": unique}


def container(name: str, image_id: str, ref: str, status: str = "running", health: Optional[str] = "healthy"):
    state: dict[str, Any] = {"Status": status}
    if health:
        state["Health"] = {"Status": health}
    return {"Id": f"c-{name}", "Name": f"/{name}", "Image": image_id, "Config": {"Image": ref}, "State": state}


def empty_state() -> dict[str, Any]:
    return {"images": {}, "containers": {}, "probe": {"state": "idle"}, "probe_rc": 0, "db_version": 10,
            "health": "healthy", "calls": [], "rm_errors": {}, "fail": {}}


def _result(args, code=0, out="", err=""):
    return subprocess.CompletedProcess(list(args), code, out, err)


class FakeDocker:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = state or empty_state()

    # ----------------------------------------------------------------- helpers
    @property
    def calls(self) -> list[list[str]]:
        return self.state["calls"]

    def resolve(self, ref: str) -> Optional[str]:
        images = self.state["images"]
        if ref in images:
            return ref
        for identifier, data in images.items():
            if ref in data["RepoTags"] or identifier.split(":")[-1].startswith(ref.split(":")[-1]) and \
                    ref.startswith("sha256:"):
                return identifier
        return None

    def find_container(self, name: str) -> Optional[dict[str, Any]]:
        for data in self.state["containers"].values():
            if data["Name"].lstrip("/") == name or data["Id"] == name:
                return data
        return None

    # ----------------------------------------------------------------- dispatcher
    def __call__(self, args, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        args = list(args)
        assert args[0] == "docker", args
        rest = args[1:]
        self.calls.append(rest)
        key = " ".join(rest[:2])
        if key in self.state.get("fail", {}):
            return _result(args, 1, "", self.state["fail"][key])
        if "prune" in rest:
            return _result(args, 99, "", "prune interdit dans les tests")
        if rest[:2] == ["image", "ls"]:
            return _result(args, 0, "\n".join(self.state["images"]) + "\n")
        if rest[:2] == ["image", "inspect"]:
            return self.image_inspect(args, rest[2:])
        if rest[:2] == ["ps", "-aq"]:
            return _result(args, 0, "\n".join(c["Id"] for c in self.state["containers"].values()) + "\n")
        if rest[:2] == ["container", "inspect"]:
            return self.container_inspect(args, rest[2:])
        if rest[:2] == ["system", "df"]:
            rows = [{"ID": i.split(":")[-1][:12], "UniqueSize": f"{d['unique'] / 1e6:.1f}MB"}
                    for i, d in self.state["images"].items() if d.get("unique") is not None]
            return _result(args, 0, json.dumps({"Images": rows}))
        if rest[:2] == ["image", "rm"]:
            return self.image_rm(args, rest[2:])
        if rest[0] == "exec":
            return self.exec(args, rest[1:], input_text)
        if rest[0] == "build":
            return self.build(args, rest[1:])
        if rest[0] == "compose":
            return self.compose(args, rest[1:])
        return _result(args, 2, "", f"commande non simulée : {' '.join(rest)}")

    def image_inspect(self, args, rest):
        fmt = None
        if rest and rest[0] == "--format":
            fmt, rest = rest[1], rest[2:]
        found = []
        for ref in rest:
            identifier = self.resolve(ref)
            if identifier is None:
                return _result(args, 1, "[]", f"Error: No such image: {ref}")
            found.append(self.state["images"][identifier])
        if fmt:
            match = re.search(r'index \.Config\.Labels "([^"]+)"', fmt)
            if match:
                return _result(args, 0, found[0]["Config"]["Labels"].get(match.group(1), "") + "\n")
            return _result(args, 0, found[0]["Id"] + "\n")
        public = [{k: v for k, v in data.items() if k != "unique"} for data in found]
        return _result(args, 0, json.dumps(public))

    def container_inspect(self, args, rest):
        if rest and rest[0] == "--format":
            fmt, name = rest[1], rest[2]
            data = self.find_container(name)
            if data is None:
                return _result(args, 1, "", f"Error: No such container: {name}")
            if "Health" in fmt and data["State"].get("Health"):
                return _result(args, 0, data["State"]["Health"]["Status"] + "\n")
            return _result(args, 0, data["State"]["Status"] + "\n")
        found = []
        for name in rest:
            data = self.find_container(name)
            if data is None:
                return _result(args, 1, "[]", f"Error: No such container: {name}")
            found.append(data)
        return _result(args, 0, json.dumps(found))

    def image_rm(self, args, rest):
        if any(flag in rest for flag in ("-f", "--force")):
            return _result(args, 99, "", "docker image rm -f interdit")
        ref = rest[0]
        if ref in self.state["rm_errors"]:
            return _result(args, 1, "", self.state["rm_errors"][ref])
        identifier = self.resolve(ref)
        if identifier is None:
            return _result(args, 1, "", f"Error response from daemon: No such image: {ref}")
        data = self.state["images"][identifier]
        users = [c for c in self.state["containers"].values() if c["Image"] == identifier]
        if users and (ref == identifier or data["RepoTags"] == [ref]):
            return _result(args, 1, "", f"conflict: unable to remove repository reference {ref} "
                                        f"(must force) - container {users[0]['Id']} is using its referenced image")
        if ref in data["RepoTags"]:
            data["RepoTags"].remove(ref)
            if data["RepoTags"]:
                return _result(args, 0, f"Untagged: {ref}\n")
        del self.state["images"][identifier]
        return _result(args, 0, f"Untagged: {ref}\nDeleted: {identifier}\n")

    def exec(self, args, rest, input_text):
        command = " ".join(rest)
        if rest[-1] == "-":  # sonde d'activité poussée sur l'entrée standard
            return _result(args, self.state["probe_rc"], json.dumps(self.state["probe"]) + "\n")
        if "user_version" in command:
            return _result(args, 0, f"{self.state['db_version']}\n")
        if "backup" in command:
            return _result(args, 0, "SQLite format 3 (fausse sauvegarde)")
        return _result(args, 2, "", "exec non simulé")

    def build(self, args, rest):
        tag = rest[rest.index("-t") + 1]
        dockerfile = rest[rest.index("-f") + 1]
        build_args = dict(rest[i + 1].split("=", 1) for i, value in enumerate(rest) if value == "--build-arg")
        labels = {BUILD_LABELS[k]: v for k, v in build_args.items() if k in BUILD_LABELS}
        labels["lody.component"] = COMPONENTS.get(dockerfile, "?")
        base_layers = ["layer-python"]
        base = build_args.get("LODY_BASE_IMAGE")
        if base and self.resolve(base):
            base_layers = self.state["images"][self.resolve(base)]["RootFS"]["Layers"]
        identifier = "sha256:" + hashlib.sha256(json.dumps([tag, dockerfile, build_args]).encode()).hexdigest()
        self.state["images"][identifier] = image(identifier, [tag], created="2026-11-13T09:00:00Z",
                                                 layers=base_layers + [f"layer-{identifier[7:15]}"], labels=labels,
                                                 unique=100_000_000)
        return _result(args, 0, f"built {tag}\n")

    def compose(self, args, rest):
        tag = os.environ.get("LODY_IMAGE_TAG", "local")
        auth_tag = os.environ.get("LODY_AUTH_IMAGE_TAG") or tag
        ui_ref = f"lody-video-factory-2d:{tag}" if "docker-compose.lody-2d.yml" in rest else f"lody-video-factory:{tag}"
        auth_ref = f"lody-auth:{auth_tag}"
        for name, ref in (("lody-video-factory-ui", ui_ref), ("lody-video-factory-auth", auth_ref)):
            identifier = self.resolve(ref)
            if identifier is None:
                return _result(args, 1, "", f"image {ref} absente")
            current = self.find_container(name)
            if current is not None and current["Image"] == identifier:
                current["recreated"] = False
                continue
            self.state["containers"][f"c-{name}"] = dict(
                container(name, identifier, ref, health=self.state["health"]), recreated=True)
        return _result(args, 0, "")


def main() -> int:
    path = os.environ["FAKE_DOCKER_STATE"]
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
    stdin = None if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    result = FakeDocker(state)(["docker", *sys.argv[1:]], input_text=stdin)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
