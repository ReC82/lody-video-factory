"""#221 — classification `ux-safe` par le code et voie d'autorisation de la politique de fusion.
Aucun réseau : faits GitHub factices, dépôt git temporaire."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ORCH_DIR = Path(__file__).resolve().parents[2] / "deploy" / "lodyorch"
if str(ORCH_DIR) not in sys.path:
    sys.path.insert(0, str(ORCH_DIR))

import ux_safe  # noqa: E402


def load_policy():
    spec = importlib.util.spec_from_file_location("merge_policy_ux", ORCH_DIR / "merge_policy.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses : le module doit être enregistré avant exécution
    spec.loader.exec_module(mod)
    return mod


def f(name, *added, status="modified", removed=0):
    patch = "@@ -1 +1 @@\n" + "".join(f"-old{i}\n" for i in range(removed)) + "".join(f"+{line}\n" for line in added)
    return {"filename": name, "status": status, "patch": patch}


UI_CHANGE = [f("webui/lody/view_home.py", "from lody.project_list import filter_projects",
               "from lody import catalog", "import re", "_RX = re.compile(r'[*_]')",
               "st.markdown('<div class=\"lody-row\">', unsafe_allow_html=True)"),
             f("webui/lody/styles.css", ".lody-row { display: flex; gap: .5rem; }"),
             f("test/lody/test_project_list.py", "def test_filter():", "    assert True")]


def test_ui_css_and_tests_are_ux_safe():
    v = ux_safe.classify(UI_CHANGE)
    assert v.eligible, v.reasons
    assert v.summary().startswith("ux-safe : 3 fichier(s)")


@pytest.mark.parametrize("files, reason", [
    ([f("webui/lody/db.py", "x = 1")], "hors liste blanche"),
    ([f("deploy/lodyorch/ux_safe.py", "UI_FILES = ('*',)")], "hors liste blanche"),
    ([f("scripts/lody-deploy.sh", "echo")], "hors liste blanche"),
    ([f("Dockerfile.lody", "RUN true")], "hors liste blanche"),
    ([f("webui/lody/view_production.py", "x = 1")], "hors liste blanche"),
    ([f("webui/lody/view_settings.py", "x = 1")], "hors liste blanche"),
    ([f("webui/lody/app.py", "x = 1")], "hors liste blanche"),
    ([f("webui/lody/view_home.py", "x = 1", status="removed")], "suppression"),
    ([{"filename": "webui/lody/view_home.py", "status": "renamed", "previous_filename": "webui/lody/old.py",
       "patch": "@@\n+x\n"}], "renommage"),
    ([{"filename": "webui/lody/styles.css", "status": "modified", "patch": None}], "diff illisible"),
    ([f("webui/lody/view_home.py", "from lody.generation import run")], "import non autorisé"),
    ([f("webui/lody/view_home.py", "from lody import db")], "import non autorisé (lody.db)"),
    ([f("webui/lody/view_home.py", "import subprocess")], "import non autorisé"),
    ([f("webui/lody/view_home.py", "con.execute('DELETE FROM projects')")], "sql-ou-base"),
    ([f("webui/lody/view_home.py", "db.delete_project(pid)")], "sql-ou-base"),
    ([f("webui/lody/view_home.py", "key = st.secrets['openai']")], "secrets-auth"),
    ([f("webui/lody/view_home.py", "os.environ['X'] = '1'")], "systeme-ou-reseau"),
    ([f("webui/lody/view_home.py", "open('/data/x', 'w').write('')")], "fichiers"),
    ([f("webui/lody/view_home.py", "remove_character(cid)")], "suppression"),
    ([f("webui/lody/view_home.py", "eval(code)")], "code-dynamique"),
    ([f("webui/lody/view_characters.py", "voice_picker.render()")], "traitement-payant"),
    ([f("webui/lody/view_project.py", "generation.start(pid)")], "traitement-payant"),
    ([f("webui/lody/view_home.py", "st.markdown('<script>x()</script>', unsafe_allow_html=True)")], "html-actif"),
    ([f("webui/lody/styles.css", "@import url(javascript:alert(1));")], "html-actif"),
    ([f("test/lody/test_x.py", "def test(): pass")], "aucun fichier d'interface"),
    ([f("docs/x.md", "texte")], "aucun fichier d'interface"),
    ([f("webui/lody/view_home.py", "TOKEN = 'ghp_" + "a" * 30 + "'")], "secret"),
    ([], "aucun fichier"),
])
def test_anything_not_proven_ui_only_is_refused(files, reason):
    v = ux_safe.classify(files)
    assert not v.eligible
    assert any(reason in r for r in v.reasons), v.reasons


def test_size_bounds():
    big = [f("webui/lody/styles.css", *[f".c{i} {{}}" for i in range(ux_safe.MAX_CHANGED_LINES + 1)])]
    assert "lignes modifiées" in " ".join(ux_safe.classify(big).reasons)
    many = [f("webui/lody/styles.css", "a{}")] + [f(f"test/lody/test_{i}.py", "x = 1") for i in range(ux_safe.MAX_FILES)]
    assert "fichiers (max" in " ".join(ux_safe.classify(many).reasons)


def test_comment_mentioning_a_forbidden_word_does_not_count():
    assert ux_safe.classify([f("webui/lody/view_home.py", "x = 1  # pas de generation ici")]).eligible


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_git_files_reads_a_real_range(tmp_path):
    repo = tmp_path / "r"
    (repo / "webui" / "lody").mkdir(parents=True)
    git(tmp_path, "init", "-q", str(repo))
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    (repo / "webui/lody/styles.css").write_text("a {}\n")
    (repo / "webui/lody/db.py").write_text("x = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "webui/lody/styles.css").write_text("a {}\n.b { color: red; }\n")
    git(repo, "commit", "-qam", "css (#1)")
    ok = git(repo, "rev-parse", "HEAD")
    assert ux_safe.classify(ux_safe.git_files(base, ok, str(repo))).eligible
    assert ux_safe.main(["diff", base, ok, "--repo", str(repo)]) == 0
    (repo / "webui/lody/db.py").unlink()
    git(repo, "commit", "-qam", "suppression")
    files = ux_safe.git_files(base, "HEAD", str(repo))
    assert {"filename": "webui/lody/db.py", "status": "removed"} in files
    assert ux_safe.main(["diff", base, "HEAD", "--repo", str(repo), "--json"]) == 1


# --- politique de fusion : voie `ux-safe` -------------------------------------------------------

SHA = "b" * 40


def facts(mp, files, labels=("agent:auto",), events=(), created="2026-11-14T10:00:00Z", **extra):
    pr = dict(number=91, state="open", merged=False, draft=False, mergeable=True, mergeable_state="clean",
              user={"login": mp.TRUSTED_ACTORS[0]}, body="Closes #1", created_at=created,
              head={"sha": SHA, "ref": "agent/1-ux", "repo": {"full_name": mp.REPO}}, base={"ref": "main"},
              additions=10, deletions=1, changed_files=len(files))
    runs = [dict(name=n, status="completed", conclusion="success", started_at="x")
            for n in ("Detect code changes", "CI result")]
    out = {"pr": pr, "files": files, "commits": [{"sha": SHA, "parents": [{}]}], "reviews": [], "check_runs": runs,
           "ticket": {"state": "open", "labels": [{"name": n} for n in labels]}, "ticket_events": list(events),
           "base_compare": {"ahead_by": 0, "files": []}}
    out.update(extra)
    return out


def refused(decision):
    return {c.code for c in decision.refusals}


def test_ux_safe_pr_is_authorized_without_merge_auto_only_when_enabled():
    mp = load_policy()
    assert "autorisation-merge-auto" in refused(mp.evaluate(facts(mp, UI_CHANGE)))
    d = mp.evaluate(facts(mp, UI_CHANGE, ux_safe_auto=True))
    assert d.eligible, d.refusals
    assert any(c.code == "classification-ux-safe" and c.ok for c in d.checks)


def test_non_ux_safe_pr_still_needs_merge_auto():
    mp = load_policy()
    files = [f("webui/lody/view_production.py", "x = 1"), f("test/lody/test_x.py", "x = 1")]
    d = mp.evaluate(facts(mp, files, ux_safe_auto=True))
    assert {"classification-ux-safe", "autorisation-merge-auto"} <= refused(d)


def test_ux_safe_path_is_not_retroactive_and_vetoes_still_win():
    mp = load_policy()
    old = mp.evaluate(facts(mp, UI_CHANGE, ux_safe_auto=True, created="2026-11-01T00:00:00Z",
                            authorized_after="2026-11-10T00:00:00Z"))
    assert refused(old) == {"ux-safe-apres-activation"}
    new = mp.evaluate(facts(mp, UI_CHANGE, ux_safe_auto=True, authorized_after="2026-11-10T00:00:00Z"))
    assert new.eligible, new.refusals
    veto = mp.evaluate(facts(mp, UI_CHANGE, labels=("agent:auto", "merge:manual"), ux_safe_auto=True))
    assert "aucun-veto" in refused(veto)
    red = facts(mp, UI_CHANGE, ux_safe_auto=True)
    red["check_runs"][1]["conclusion"] = "failure"
    assert "ci" in refused(mp.evaluate(red))


def test_merge_auto_label_path_is_unchanged_by_ux_mode():
    mp = load_policy()
    ev = [{"event": "labeled", "label": {"name": "merge:auto"}, "actor": {"login": mp.TRUSTED_ACTORS[0]},
           "created_at": "2026-11-12T00:00:00Z"}]
    files = [f("webui/lody/locations.py", "x = 1"), f("test/lody/test_x.py", "x = 1")]
    d = mp.evaluate(facts(mp, files, labels=("agent:auto", "merge:auto"), events=ev, ux_safe_auto=True))
    assert d.eligible, d.refusals
    assert not any(c.code == "classification-ux-safe" for c in d.checks)
