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
    payload = None
    if fields[:2] == ["--input", "-"]:
        payload, fields = json.load(sys.stdin), fields[2:]
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
    data["requests"] = data.get("requests", 0) + 1
    save(data)
    m = re.fullmatch(r"repos/[^/]+/[^/]+/pulls/(\d+)/merge", base)
    if m and method == "PUT":
        # Fusion (#200) : `sha` attendu obligatoire ; 409 si la tête a bougé, 405 si non fusionnable.
        pr = data.get("pulls", {}).get(m.group(1))
        values = dict(f.split("=", 1) for f in fields[1::2])
        data.setdefault("merges", []).append({"pr": int(m.group(1)), **values})
        if data.get("merge_error"):
            save(data)
            print(f"gh: {data['merge_error']}", file=sys.stderr)
            return 1
        if not pr or pr.get("merged") or pr.get("state") != "open":
            save(data)
            print("gh: Pull Request is not mergeable (HTTP 405)", file=sys.stderr)
            return 1
        if values.get("sha") != pr["head"]["sha"]:
            save(data)
            print("gh: Head branch was modified. Review and try the merge again. (HTTP 409)", file=sys.stderr)
            return 1
        pr.update(merged=True, state="closed", merge_commit_sha="m" * 40)
        if data.get("merge_closes_issue"):  # comme GitHub : « Closes #N » ferme le ticket à la fusion
            for num in re.findall(r"(?i)closes\s+#(\d+)", pr.get("body") or ""):
                if num in data["issues"]:
                    data["issues"][num]["state"] = "closed"
        save(data)
        print(json.dumps({"sha": "m" * 40, "merged": True, "message": "Pull Request successfully merged"}))
        return 0
    m = re.fullmatch(r"repos/[^/]+/[^/]+/pulls/(\d+)/(files|commits|reviews)", base)
    if m:
        pr = data.get("pulls", {}).get(m.group(1)) or {}
        page = int(query.get("page", ["1"])[0])
        print(json.dumps(pr.get(m.group(2), []) if page == 1 else []))
        return 0
    m = re.fullmatch(r"repos/[^/]+/[^/]+/compare/([^.]+)\.\.\.(.+)", base)
    if m:
        print(json.dumps(data.get("compare", {}).get(m.group(1), {"ahead_by": 0, "files": []})))
        return 0
    m = re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)/events", base)
    if m:
        page = int(query.get("page", ["1"])[0])
        print(json.dumps(data.get("events", {}).get(m.group(1), []) if page == 1 else []))
        return 0
    m = re.fullmatch(r"repos/[^/]+/[^/]+/pulls/(\d+)", base)
    if m:
        pr = data.get("pulls", {}).get(m.group(1))
        if not pr:
            return 1
        print(json.dumps(pr))
        return 0
    if base.endswith("/pulls"):
        head = query.get("head", [""])[0].split(":", 1)[-1]
        print(json.dumps([p for p in data.get("pulls", {}).values() if p["head"].get("ref") == head
                          and p.get("state") == "open"]))
        return 0
    m = re.fullmatch(r"repos/[^/]+/[^/]+/commits/([^/]+)/check-runs", base)
    if m:
        print(json.dumps({"check_runs": data.get("checks", {}).get(m.group(1), [])}))
        return 0
    m = re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)/comments", base)
    if m:
        if data.get("comments_offline"):
            return 1
        mine = [c for c in data.get("comments", []) if c.get("issue") == int(m.group(1))]
        if method == "POST":
            if data.get("comments_post_offline"):
                return 1
            body = payload["body"] if payload else dict(f.split("=", 1) for f in fields[1::2])["body"]
            cid = len(data["comments"]) + 1
            comment = {"issue": int(m.group(1)), "body": body, "id": cid, "issue_url": f"x/issues/{m.group(1)}",
                       "html_url": f"https://github.com/x/issues/{m.group(1)}#issuecomment-{cid}",
                       "author_association": "OWNER", "created_at": "2026-11-10T00:00:00Z"}
            data.setdefault("comments", []).append(comment)
            save(data)
            print(json.dumps(comment))
            return 0
        page = int(query.get("page", ["1"])[0])
        print(json.dumps(mine if page == 1 else []))
        return 0
    m = re.fullmatch(r"repos/[^/]+/[^/]+/labels(?:/(.+))?", base)
    if m:
        repo_labels = data.setdefault("repo_labels", {})
        values = dict(f.split("=", 1) for f in fields[1::2])
        if method in ("POST", "PATCH"):
            name = values.get("name") or urllib.parse.unquote(m.group(1))
            repo_labels[name] = {"name": name, "color": values["color"], "description": values["description"]}
            save(data)
            return 0
        page = int(query.get("page", ["1"])[0])
        print(json.dumps(list(repo_labels.values()) if page == 1 else []))
        return 0
    m = re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)", base)
    if m:
        it = data["issues"].get(m.group(1))
        if not it:
            return 1
        if method == "PATCH":
            it.update(dict(f.split("=", 1) for f in fields[1::2]))
            save(data)
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
