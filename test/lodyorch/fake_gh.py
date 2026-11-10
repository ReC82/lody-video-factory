"""Faux `gh api` pour les tests LODYORCH : tickets et labels lus/écrits dans un fichier JSON.

État : {"issues": {"<n>": {"number", "title", "state", "labels": [..], "body", "pull_request"?}},
        "comments": [...], "calls": [[...argv...]]}. Aucun réseau."""

import json
import os
import re
import sys
import urllib.parse

STATE = os.environ["FAKE_GH_STATE"]


def load():
    with open(STATE, encoding="utf-8") as fh:
        return json.load(fh)


def save(data):
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def issue_json(it):
    out = dict(it)
    out["labels"] = [{"name": n} for n in it.get("labels", [])]
    return out


def main(argv):
    data = load()
    data.setdefault("calls", []).append(argv)
    save(data)
    if argv[:2] == ["auth", "status"]:
        return 0
    if not argv or argv[0] != "api":
        return 1
    args = argv[1:]
    method = "GET"
    if args[0] == "-X":
        method, args = args[1], args[2:]
    path, fields = args[0], args[1:]
    if data.get("offline"):
        return 1
    base = path.split("?")[0]
    query = urllib.parse.parse_qs(path.split("?")[1]) if "?" in path else {}
    m = re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)/labels(?:/(.+))?", base)
    if m:
        it = data["issues"][m.group(1)]
        if method == "POST":
            for i in range(0, len(fields), 2):
                name = fields[i + 1].split("=", 1)[1]
                if name not in it["labels"]:
                    it["labels"].append(name)
        elif method == "DELETE":
            name = urllib.parse.unquote(m.group(2))
            it["labels"] = [x for x in it["labels"] if x != name]
        save(data)
        print(json.dumps([{"name": n} for n in it["labels"]]))
        return 0
    m = re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)", base)
    if m:
        it = data["issues"].get(m.group(1))
        if not it:
            return 1
        print(json.dumps(issue_json(it)))
        return 0
    if base.endswith("/issues/comments"):
        page = int(query.get("page", ["1"])[0])
        print(json.dumps(data.get("comments", []) if page == 1 else []))
        return 0
    if base.endswith("/issues"):
        page = int(query.get("page", ["1"])[0])
        items = [issue_json(it) for it in data["issues"].values() if it.get("state", "open") == "open"]
        print(json.dumps(items if page == 1 else []))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
