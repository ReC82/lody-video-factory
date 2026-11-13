"""Faux `claude` pour les tests LODYORCH : aucun appel au modèle, aucun réseau.

- `auth status --json` : {"loggedIn": FAKE_CLAUDE_AUTH != "0"} ;
- `-p …` : note l'appel (ticket, arguments) dans FAKE_CLAUDE_CALLS, écrit des lignes stream-json,
  puis le rapport de fin dans LQ_STATUS_FILE selon FAKE_CLAUDE_MODE :
  `deliver` (WAITING_OWNER + PR), `blocked`, `crash` (aucun résultat, code 1), `secret` (fuite simulée
  d'un jeton dans la sortie, qui doit être masquée dans le journal). FAKE_CLAUDE_SLEEP : durée
  simulée de la session avant son rapport (essais de reprise). FAKE_CLAUDE_EXTRA : JSON fusionné
  dans le rapport de fin. FAKE_CLAUDE_PLAN (#215) : liste JSON déclarée par la vraie commande
  `lodyorch phase --plan …` avant tout le reste ; mode `limit` : limite fournisseur (429)."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ORCH = Path(__file__).resolve().parents[2] / "deploy" / "lodyorch" / "lodyorch.py"


def main(argv):
    if argv[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": os.environ.get("FAKE_CLAUDE_AUTH", "1") != "0", "email": "x@example.invalid"}))
        return 0
    issue = os.environ.get("LQ_ISSUE", "?")
    with open(os.environ["FAKE_CLAUDE_CALLS"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"issue": issue, "argv": argv[2:]}) + "\n")
    mode = os.environ.get("FAKE_CLAUDE_MODE", "deliver")
    print(json.dumps({"type": "system", "subtype": "init", "mcp_servers": []}), flush=True)
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "uv run pytest -q", "description": "tests"}}]}}),
        flush=True)
    if mode == "secret":
        print(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "token ghp_" + "A" * 36 + " api_key=\"zzzzzzzzzzzz\""}]}}),
            flush=True)
    plan = json.loads(os.environ.get("FAKE_CLAUDE_PLAN") or "[]")
    if plan:
        subprocess.run([sys.executable, str(ORCH), "phase", "--plan", *plan], check=True,
                       stdout=subprocess.DEVNULL)
    if mode == "crash":
        return 1
    if mode == "limit":
        print(json.dumps({"type": "result", "result": "usage limit reached", "is_error": True,
                          "api_error_status": 429}), flush=True)
        return 1
    time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))
    status = {"deliver": "WAITING_OWNER", "secret": "WAITING_OWNER", "blocked": "BLOCKED"}[mode]
    report = {"status": status, "summary": f"fictif #{issue}", "branch": f"agent/{issue}-fictif",
              "pr": "" if os.environ.get("FAKE_CLAUDE_NO_PR") else f"https://github.com/ReC82/lody-video-factory/pull/9{issue}",
              "commit_sha": "abc1234",
              "tests": "pytest ok", "questions": ["Q1 ?"] if status == "BLOCKED" else [],
              "blockers": "décision nécessaire" if status == "BLOCKED" else ""}
    report.update(json.loads(os.environ.get("FAKE_CLAUDE_EXTRA") or "{}"))
    with open(os.environ["LQ_STATUS_FILE"], "w", encoding="utf-8") as fh:
        json.dump(report, fh)
    print(json.dumps({"type": "result", "result": "fini", "is_error": False, "total_cost_usd": 0.01}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
