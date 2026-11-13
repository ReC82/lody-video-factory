"""Protection de `main` (#200) : seuls des checks toujours présents sont exigés ; à blanc par défaut."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("branch_protection", ROOT / "deploy" / "lodyorch" / "branch_protection.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)


def test_only_stable_aggregate_is_required():
    assert bp.choose_contexts({"Detect code changes", "Python 3.11 tests", "Documentation checks"}) == []
    assert bp.choose_contexts({"Detect code changes", "CI result"}) == ["CI result"]
    assert bp.choose_contexts(set(), ["X"]) == ["X"]
    payload = bp.protection_payload(["CI result"])
    assert payload["required_status_checks"] == {"strict": False, "contexts": ["CI result"]}
    assert payload["enforce_admins"] is True and payload["allow_force_pushes"] is False


def fake_gh(monkeypatch, names, calls):
    def gh(*args, payload=None):
        calls.append(args)
        if "check-runs" in args[-1]:
            return 0, json.dumps({"check_runs": [{"name": n} for n in names]}), ""
        return 1, "", "Branch not protected (HTTP 404)"
    monkeypatch.setattr(bp, "gh", gh)


def test_dry_run_never_writes_and_refuses_without_stable_check(monkeypatch, capsys):
    calls = []
    fake_gh(monkeypatch, ["Detect code changes", "Python 3.11 tests"], calls)
    assert bp.main([]) == 2 and "attendre la fusion de #198" in capsys.readouterr().out
    fake_gh(monkeypatch, ["Detect code changes", "CI result"], calls)
    assert bp.main([]) == 0
    assert "À blanc" in capsys.readouterr().out
    assert calls and all(a[1] == "GET" for a in calls)
