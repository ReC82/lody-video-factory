"""Isolation des permissions des sessions Claude et du superviseur (#207), sans second compte GitHub.

Faux GitHub en mémoire, dossiers temporaires, aucun réseau, aucun secret réel. Couvre :
- politique : `merge:auto` valable seulement posé par un humain de confiance, jamais par `[bot]` ni
  via une application ; veto levé par un compte non autorisé = veto maintenu ; auteurs distincts ;
- broker : plafond root, kill switch, armement non rétroactif, SHA attendu, idempotence, demandes
  invalides, jamais root, jeton d'agent 0640 aux permissions réduites ;
- superviseur : environnement des sessions sans jeton, `on` plafonné sans broker, fusion par le broker ;
- installation : utilisateur dédié, secrets jamais écrasés ni copiés, unités durcies ;
- garde : les sessions ne touchent ni aux secrets du broker, ni à l'identité GitHub, ni aux protections.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

import pytest

from test.lodyorch.conftest import ORCH_DIR, instance_env

SHA = "a" * 40
OWNER = "ReC82"
BOT = "lody-agent-rec82[bot]"
AFTER = "2999-01-01T00:00:00Z"
BEFORE = "2000-01-01T00:00:00Z"
CI_OK = [{"name": n, "status": "completed", "conclusion": "success", "started_at": "2026-11-12T10:00:00Z"}
         for n in ("Detect code changes", "CI result")]
SAFE_FILES = [{"filename": "webui/lody/view_home.py", "status": "modified", "patch": "@@\n+x = 1"},
              {"filename": "test/lody/test_home.py", "status": "modified", "patch": "@@\n+def test_x(): pass"}]


def load_module(name, path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses : le module doit être enregistré
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mp(monkeypatch):
    monkeypatch.setenv("LQ_REPO", "ReC82/lody-video-factory")
    monkeypatch.setenv("LQ_MERGE_TRUSTED", OWNER)
    monkeypatch.setenv("LQ_MERGE_PR_AUTHORS", f"{OWNER},{BOT}")
    if str(ORCH_DIR) not in sys.path:
        sys.path.insert(0, str(ORCH_DIR))
    return load_module("merge_policy_207", ORCH_DIR / "merge_policy.py")


def facts(author=BOT, label_actor=OWNER, label_at=AFTER, via_app=None, extra_events=(), pr_events=(), labels=None):
    ev = {"event": "labeled", "label": {"name": "merge:auto"}, "actor": {"login": label_actor}, "created_at": label_at}
    if via_app:
        ev["performed_via_github_app"] = {"slug": via_app}
    pr = {"number": 91, "state": "open", "merged": False, "draft": False, "mergeable": True, "mergeable_state": "clean",
          "user": {"login": author}, "body": "Closes #1", "additions": 3, "deletions": 1, "changed_files": 2,
          "head": {"sha": SHA, "ref": "agent/1-correctif", "repo": {"full_name": "ReC82/lody-video-factory"}},
          "base": {"ref": "main"}}
    return {"pr": pr, "files": SAFE_FILES, "commits": [{"sha": SHA, "parents": [{}]}], "reviews": [],
            "check_runs": CI_OK, "ticket": {"state": "open", "labels": labels or ["agent:auto", "merge:auto"]},
            "ticket_events": [ev, *extra_events], "pr_events": list(pr_events),
            "base_compare": {"ahead_by": 0, "files": []}}


def refused(decision):
    return {c.code for c in decision.refusals}


# --- politique (merge_policy.py) ------------------------------------------------------------------

def test_pr_by_the_app_authorized_by_the_human_owner_is_eligible(mp):
    d = mp.evaluate(facts())
    assert d.eligible, mp.render(d)
    assert mp.POLICY_VERSION >= 3


def test_label_posed_by_the_session_app_is_never_an_authorization(mp, monkeypatch):
    assert "autorisation-merge-auto" in refused(mp.evaluate(facts(label_actor=BOT)))
    # Même si quelqu'un ajoute le bot aux comptes de confiance : un [bot] n'autorise jamais.
    monkeypatch.setattr(mp, "TRUSTED_ACTORS", (OWNER, BOT))
    assert "autorisation-merge-auto" in refused(mp.evaluate(facts(label_actor=BOT)))


def test_label_posed_as_the_owner_through_an_app_is_refused(mp):
    d = mp.evaluate(facts(via_app="lody-agent-rec82"))
    assert "autorisation-merge-auto" in refused(d)
    assert "via l'application" in {c.code: c.detail for c in d.checks}["autorisation-merge-auto"]


def test_pr_author_list_does_not_grant_authorization(mp):
    assert "auteur-de-confiance" in refused(mp.evaluate(facts(author="inconnu")))
    assert BOT in mp.PR_AUTHORS and BOT not in mp.TRUSTED_ACTORS


def test_veto_removed_by_the_session_stays_a_veto(mp):
    veto = [{"event": "labeled", "label": {"name": "merge:manual"}, "actor": {"login": OWNER}, "created_at": AFTER},
            {"event": "unlabeled", "label": {"name": "merge:manual"}, "actor": {"login": BOT}, "created_at": AFTER}]
    assert "veto-leve-par-compte-autorise" in refused(mp.evaluate(facts(extra_events=veto)))
    assert "veto-leve-par-compte-autorise" in refused(mp.evaluate(facts(pr_events=veto)))
    lifted_by_owner = veto[:1] + [dict(veto[1], actor={"login": OWNER})]
    assert mp.evaluate(facts(extra_events=lifted_by_owner)).eligible


# --- broker (merge_broker.py) ---------------------------------------------------------------------

class FakeGitHub:
    def __init__(self, f):
        self.f = f
        self.calls = []
        self.merge_status = 200

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        f, base = self.f, "repos/ReC82/lody-video-factory/"
        if method == "PUT" and path.endswith("/merge"):
            if self.merge_status != 200:
                return self.merge_status, {"message": "Head branch was modified"}
            assert body["sha"] == SHA
            return 200, {"merged": True, "sha": "m" * 40}
        p = path.split("?")[0]
        m = re.search(r"[?&]page=(\d+)", path)
        page = int(m.group(1)) if m else 1
        table = {base + "pulls/91": f["pr"], base + "pulls/91/files": f["files"], base + "pulls/91/commits": f["commits"],
                 base + "pulls/91/reviews": f["reviews"], base + f"commits/{SHA}/check-runs": {"check_runs": f["check_runs"]},
                 base + "issues/1": f["ticket"], base + "issues/1/events": f["ticket_events"],
                 base + "issues/91/events": f["pr_events"], base + f"compare/{SHA}...main": f["base_compare"]}
        if p not in table:
            return 404, {"message": "Not Found"}
        data = table[p]
        if isinstance(data, list) and page > 1:
            data = []
        if isinstance(data, dict) and "check_runs" in data and page > 1:
            data = {"check_runs": []}
        return 200, data

    def merges(self):
        return [c for c in self.calls if c[0] == "PUT"]


@pytest.fixture
def broker_env(tmp_path, monkeypatch):
    for key in ("LQ_REPO", "LQ_MERGE_TRUSTED", "LQ_MERGE_PR_AUTHORS"):
        monkeypatch.delenv(key, raising=False)
    base = tmp_path / "broker"
    for d in ("requests", "results", "state", "agent"):
        (base / d).mkdir(parents=True)
    conf = tmp_path / "merge-broker.env"

    def write(ceiling="on", mtime=None, **extra):
        lines = [f"LQ_AUTO_MERGE={ceiling}", "LQ_REPO=ReC82/lody-video-factory", f"LQ_MERGE_TRUSTED={OWNER}",
                 f"LQ_MERGE_PR_AUTHORS={OWNER},{BOT}", f"LQ_BROKER_DIR={base}",
                 f"LQ_BROKER_TOKEN_FILE={tmp_path / 'merge-token'}", f"LQ_AGENT_TOKEN_FILE={base / 'agent' / 'token'}"]
        lines += [f"{k}={v}" for k, v in extra.items()]
        conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if mtime is not None:
            os.utime(conf, (mtime, mtime))
        return conf

    mod = load_module("merge_broker_207", ORCH_DIR / "merge_broker.py")
    return mod, base, write


def request(base, pr=91, sha=SHA, nonce="0123456789abcdef", **fields):
    name = f"{pr}-{sha}-{nonce}.json"
    (base / "requests" / name).write_text(json.dumps(dict({"pr": pr, "head_sha": sha, "mode": "on"}, **fields)))
    return name


def result(base, name):
    return json.loads((base / "results" / name).read_text())


def test_broker_merges_only_with_its_own_full_revalidation(broker_env):
    mod, base, write = broker_env
    gh = FakeGitHub(facts())
    broker = mod.Broker(write("on", mtime=time.time() - 3600), api=gh)
    name = request(base)
    assert broker.process() == ["PR #91 @ aaaaaaaaaa : merged"]
    assert result(base, name)["state"] == "merged" and len(gh.merges()) == 1
    assert not list((base / "requests").iterdir())
    # Idempotence : une nouvelle demande pour le même SHA ne déclenche jamais de seconde requête.
    again = request(base, nonce="fedcba9876543210")
    broker.process()
    assert result(base, again)["state"] == "merged" and len(gh.merges()) == 1


@pytest.mark.parametrize("ceiling", ["off", "dry-run"])
def test_root_ceiling_is_a_hard_kill_switch(broker_env, ceiling):
    mod, base, write = broker_env
    gh = FakeGitHub(facts())
    name = request(base)
    mod.Broker(write(ceiling), api=gh).process()
    res = result(base, name)
    assert res["state"] == "refused" and res["refusals"][0]["code"] == "mode"
    assert gh.calls == []  # aucun appel GitHub en mode coupé


def test_request_mode_can_only_lower_the_ceiling(broker_env):
    mod, base, write = broker_env
    gh = FakeGitHub(facts())
    name = request(base, mode="dry-run")
    mod.Broker(write("on"), api=gh).process()
    assert result(base, name)["state"] == "refused" and not gh.merges()


def test_authorization_older_than_root_activation_is_refused(broker_env):
    mod, base, write = broker_env
    gh = FakeGitHub(facts(label_at=BEFORE))
    name = request(base, authorized_after="")  # une demande forgée sans date ne recule pas l'armement
    mod.Broker(write("on", mtime=time.time()), api=gh).process()
    res = result(base, name)
    assert res["state"] == "refused" and "autorisation-apres-activation" in {r["code"] for r in res["refusals"]}
    assert not gh.merges()


def test_forged_request_with_bot_label_is_refused(broker_env):
    mod, base, write = broker_env
    gh = FakeGitHub(facts(label_actor=BOT))
    name = request(base)
    mod.Broker(write("on", mtime=time.time() - 3600), api=gh).process()
    assert "autorisation-merge-auto" in {r["code"] for r in result(base, name)["refusals"]}
    assert not gh.merges()


def test_head_moved_since_the_request_is_refused(broker_env):
    mod, base, write = broker_env
    gh = FakeGitHub(facts())
    name = request(base, sha="b" * 40)
    mod.Broker(write("on", mtime=time.time() - 3600), api=gh).process()
    assert result(base, name)["state"] == "refused" and not gh.merges()


def test_github_refusal_is_journaled_and_never_retried(broker_env):
    mod, base, write = broker_env
    gh = FakeGitHub(facts())
    gh.merge_status = 409
    broker = mod.Broker(write("on", mtime=time.time() - 3600), api=gh)
    first = request(base)
    broker.process()
    assert result(base, first)["state"] == "failed" and result(base, first)["status"] == 409
    second = request(base, nonce="fedcba9876543210")
    broker.process()
    assert result(base, second)["state"] == "failed" and len(gh.merges()) == 1


def test_invalid_requests_are_discarded_without_github_calls(broker_env, tmp_path):
    mod, base, write = broker_env
    gh = FakeGitHub(facts())
    (base / "requests" / "nimporte.json").write_text("{}")
    (base / "requests" / f"91-{SHA}-0123456789abcdef.json").write_text("pas du json")
    (base / "requests" / "dossier").mkdir()
    (tmp_path / "cible").write_text("x")
    (base / "requests" / f"92-{SHA}-0123456789abcdef.json").symlink_to(tmp_path / "cible")
    lines = mod.Broker(write("on"), api=gh).process()
    assert len(lines) == 4 and all("ignorée" in x for x in lines)
    assert gh.calls == [] and not list((base / "requests").iterdir())
    assert (tmp_path / "cible").exists()


def test_broker_refuses_to_run_as_root(broker_env, monkeypatch):
    mod, _, _ = broker_env
    monkeypatch.setattr(mod.os, "geteuid", lambda: 0)
    assert mod.main(["tick"]) == 2


def test_broker_policy_ignores_the_caller_environment(broker_env, monkeypatch):
    """Les acteurs de confiance viennent de la configuration root, pas de l'environnement appelant."""
    mod, base, write = broker_env
    monkeypatch.setenv("LQ_MERGE_TRUSTED", BOT)
    broker = mod.Broker(write("on"), api=FakeGitHub(facts()))
    assert broker.mp.TRUSTED_ACTORS == (OWNER,)


@pytest.mark.skipif(not shutil.which("openssl"), reason="openssl requis pour signer le JWT")
def test_agent_token_is_minted_with_reduced_permissions_and_private_mode(broker_env, tmp_path):
    mod, base, write = broker_env
    key = tmp_path / "app-key.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    seen = {}

    def api(method, path, body=None):
        seen.update(method=method, path=path, body=body)
        return 201, {"token": "ghs_" + "x" * 36, "expires_at": "2026-11-13T12:00:00Z",
                     "permissions": {"contents": "write", "pull_requests": "write", "issues": "write", "metadata": "read"}}

    broker = mod.Broker(write("dry-run", LQ_APP_ID="123", LQ_APP_INSTALLATION_ID="456", LQ_APP_KEY_FILE=str(key)), api=api)
    jwt = broker.jwt()
    assert jwt.count(".") == 2
    msg = broker.mint()
    assert "ghs_" not in msg  # le jeton n'est jamais affiché
    assert seen["path"] == "app/installations/456/access_tokens"
    assert seen["body"]["repositories"] == ["lody-video-factory"]
    assert not set(seen["body"]["permissions"]) & set(mod.FORBIDDEN_AGENT_PERMISSIONS)
    token = base / "agent" / "token"
    assert token.read_text().startswith("ghs_") and (token.stat().st_mode & 0o777) == 0o640
    assert "conservé" in broker.mint()  # jeton récent : pas de nouvel appel


def test_too_broad_agent_token_is_never_written(broker_env, tmp_path, monkeypatch):
    mod, base, write = broker_env
    broker = mod.Broker(write("dry-run", LQ_APP_ID="1", LQ_APP_INSTALLATION_ID="2"),
                        api=lambda *a: (201, {"token": "ghs_" + "y" * 36, "permissions": {"administration": "write"}}))
    monkeypatch.setattr(broker, "jwt", lambda: "jwt")
    with pytest.raises(RuntimeError, match="trop large"):
        broker.mint()
    assert not (base / "agent" / "token").exists()


# --- superviseur (lodyorch.py) --------------------------------------------------------------------

def test_session_environment_carries_no_token_or_secret(orch):
    base = {"PATH": "/opt/lodyorch/bin:/usr/bin", "HOME": "/home/ubuntu", "GH_TOKEN": "ghp_x", "GITHUB_TOKEN": "x",
            "GH_CONFIG_DIR": "/tmp/gh", "LQ_REAL_GH": "/usr/bin/gh", "OPENAI_API_KEY": "sk-x", "SMTP_PASSWORD": "x",
            "AWS_SECRET_ACCESS_KEY": "x", "LQ_NOTIFY_SMTP_ENV": "/home/ubuntu/.config/plugadmin/smtp.env",
            "LQ_AGENT_TOKEN_FILE": "/var/lib/lodyorch-merge/agent/token", "CLAUDE_CODE_OAUTH_TOKEN": "keep",
            "SSH_AUTH_SOCK": "/tmp/ssh", "UV_PROJECT_ENVIRONMENT": "/home/ubuntu/lody-orch/venv"}
    env = orch.mod.session_env(base, LQ_ISSUE="7")
    for gone in ("GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "LQ_REAL_GH", "OPENAI_API_KEY", "SMTP_PASSWORD",
                 "AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK"):
        assert gone not in env, gone
    for kept in ("PATH", "HOME", "LQ_NOTIFY_SMTP_ENV", "LQ_AGENT_TOKEN_FILE", "UV_PROJECT_ENVIRONMENT"):
        assert env[kept] == base[kept]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep" and env["LQ_ISSUE"] == "7"


def test_launched_session_does_not_inherit_github_token(orch, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "z" * 36)
    seen = {}
    real = orch.mod.subprocess.Popen

    def spy(cmd, *a, **kw):
        if "-p" in cmd:  # le lancement de la session Claude (pas les autres sous-processus)
            seen["env"] = kw.get("env")
        return real(cmd, *a, **kw)

    monkeypatch.setattr(orch.mod.subprocess, "Popen", spy)
    orch.issue(1, ["agent:auto", "priority:P0"])
    assert orch.cycle() == 1
    assert seen["env"] is not None and "GH_TOKEN" not in seen["env"] and seen["env"]["LQ_ISSUE"] == "1"


def test_on_is_capped_to_dry_run_without_broker(orch, monkeypatch):
    import argparse
    monkeypatch.setattr(orch.mod, "AUTO_MERGE_CEILING", "on")
    monkeypatch.setattr(orch.mod, "MERGE_BROKER", "")
    monkeypatch.setattr(orch.mod, "MERGE_DIRECT", False)
    orch.mod.cmd_automerge(orch.con, argparse.Namespace(mode="on", limit=5))
    mode, why = orch.mod.auto_merge_mode(orch.con)
    assert mode == "dry-run" and "broker" in why
    monkeypatch.setattr(orch.mod, "MERGE_BROKER", "/var/lib/lodyorch-merge")
    assert orch.mod.auto_merge_mode(orch.con)[0] == "on"


def test_instance_config_routes_merges_through_the_broker():
    env = instance_env()
    assert env["LQ_MERGE_BROKER"] == "/var/lib/lodyorch-merge"
    assert env["LQ_AUTO_MERGE"] == "dry-run" and "LQ_MERGE_DIRECT" not in env
    assert BOT in env["LQ_MERGE_PR_AUTHORS"].split(",") and "LQ_MERGE_TRUSTED" not in env


def test_supervisor_merge_request_round_trip_with_the_broker(orch, tmp_path, broker_env, monkeypatch):
    mod, base, write = broker_env
    monkeypatch.setattr(orch.mod, "MERGE_BROKER", str(base))
    policy = orch.mod.merge_policy()
    decision = policy.Decision(pr=91, head_sha=SHA, ticket=1, eligible=True, dry_run=False)
    # Pas de réponse dans le délai : intention laissée ouverte (None), jamais rejouée par le superviseur.
    assert orch.mod.broker_merge(policy, decision, 1, "3000-01-01T00:00:00Z", wait=0) is None
    gh = FakeGitHub(facts())
    mod.Broker(write("on", mtime=time.time() - 3600), api=gh).process()
    name = next((base / "results").iterdir()).name
    assert result(base, name)["state"] == "refused"  # label antérieur à authorized_after (demande)
    # Demande normale : réponse « merged » lue par le superviseur.
    import threading
    t = threading.Timer(0.5, lambda: mod.Broker(write("on", mtime=time.time() - 3600), api=FakeGitHub(facts())).process())
    t.start()
    assert orch.mod.broker_merge(policy, decision, 1, "", wait=10) == "m" * 40
    t.join()


def test_broker_refusal_becomes_a_merge_error(orch, broker_env, monkeypatch):
    mod, base, write = broker_env
    monkeypatch.setattr(orch.mod, "MERGE_BROKER", str(base))
    policy = orch.mod.merge_policy()
    decision = policy.Decision(pr=91, head_sha=SHA, ticket=1, eligible=True, dry_run=False)
    import threading
    t = threading.Timer(0.5, lambda: mod.Broker(write("off"), api=FakeGitHub(facts())).process())
    t.start()
    with pytest.raises(policy.MergeError, match="refus du broker"):
        orch.mod.broker_merge(policy, decision, 1, "", wait=10)
    t.join()


def test_isolation_checks_flag_privileged_leftovers(orch, tmp_path, monkeypatch):
    monkeypatch.setattr(orch.mod, "MERGE_BROKER", str(tmp_path))
    (tmp_path / "requests").mkdir()
    secret = tmp_path / "merge-token"
    secret.write_text("x")
    sock = tmp_path / "docker.sock"
    sock.write_text("")
    status = tmp_path / "status"
    status.write_text("Name:\tpython\nNoNewPrivs:\t1\n")
    token = tmp_path / "token"
    token.write_text("ghs_" + "t" * 36)
    fake_gh = tmp_path / "gh"
    fake_gh.write_text("#!/bin/sh\nexit 0\n")  # identifiant gh encore enregistré
    fake_gh.chmod(0o755)
    env = {"GH_TOKEN": "x", "LQ_AGENT_TOKEN_FILE": str(token), "PATH": os.environ.get("PATH", "")}
    rows = {label: (ok, detail) for ok, label, detail in orch.mod.isolation_checks(
        env, sockets=(str(sock),), secrets=(str(secret),), proc_status=str(status), real_gh=str(fake_gh))}
    failed = {label for label, (ok, _) in rows.items() if ok is False}
    assert any("jeton GitHub dans l'environnement" in x for x in failed)
    assert any("socket" in x for x in failed)
    assert any("secrets du broker" in x for x in failed)
    assert any("identifiant gh" in x for x in failed)
    assert not any("NoNewPrivileges" in x for x in failed) and not any("jeton d'agent" in x for x in failed)
    for _, detail in rows.values():
        assert "ghs_" not in detail and "x" * 3 not in detail


# --- installation et unités -----------------------------------------------------------------------

def directives(unit):
    out = {}
    for line in unit.splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out.setdefault(k, []).append(v)
    return out


def test_broker_unit_is_a_dedicated_hardened_user():
    d = directives((ORCH_DIR / "systemd" / "lodyorch-merge.service").read_text(encoding="utf-8"))
    assert d["User"] == ["lodyorch-merge"] and d["NoNewPrivileges"] == ["yes"]
    assert d["ExecStart"] == ["/usr/bin/python3 /opt/lodyorch/merge_broker.py tick"]
    assert d["ProtectSystem"] == ["strict"] and d["ProtectHome"] == ["yes"] and d["CapabilityBoundingSet"] == [""]
    timer = (ORCH_DIR / "systemd" / "lodyorch-merge.timer").read_text(encoding="utf-8")
    assert "OnUnitInactiveSec=30s" in timer


def test_supervisor_unit_hides_broker_secrets_and_root_sockets():
    d = directives((ORCH_DIR / "systemd" / "lodyorch.service").read_text(encoding="utf-8"))
    hidden = " ".join(d["InaccessiblePaths"]).split()
    for path in ("-/etc/lodyorch", "-/var/lib/lodyorch-merge/state", "-/run/docker.sock", "-/var/snap/lxd"):
        assert path in hidden
    assert d["Environment"][1].startswith("PATH=/opt/lodyorch/bin:")
    assert "-/var/lib/lodyorch-merge/requests" in d["ReadWritePaths"][0].split()


def test_install_isolation_dry_run_never_writes_nor_reads_secrets(tmp_path):
    import getpass
    env = dict(os.environ, LODYORCH_USER=getpass.getuser())
    out = subprocess.run(["sh", str(ORCH_DIR / "install.sh"), "--isolation", "--dry-run"], env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    actions = [x for x in out.stdout.splitlines() if "[dry-run]" in x]
    assert any("useradd --system" in a and "lodyorch-merge" in a for a in actions) or "déjà présent" in out.stdout
    assert any("-m 0750 /etc/lodyorch" in a for a in actions)
    assert any("merge-broker.env" in a and "0640" in a for a in actions) or "conservé" in out.stdout
    assert any("/opt/lodyorch/bin/gh" in a for a in actions)
    assert any("git-credential-lodyorch" in a and "credential.https://github.com.helper" in a for a in actions)
    # Aucun secret n'est copié, affiché ni déposé par le script ; rien n'est démarré.
    assert not any(re.search(r"(install|cp)\b.*(merge-token|app-key\.pem)", a) for a in actions)
    assert not any(re.search(r"systemctl (enable|start|restart)", a) for a in actions)
    script = (ORCH_DIR / "install.sh").read_text(encoding="utf-8")
    assert "cat \"$BROKER_ETC" not in script and "gh auth token" not in script


def test_broker_config_template_has_no_secret_and_dry_run_ceiling():
    text = (ORCH_DIR / "merge-broker.env").read_text(encoding="utf-8")
    assert "LQ_AUTO_MERGE=dry-run" in text and "LQ_MERGE_TRUSTED=ReC82" in text
    assert not re.search(r"gh[pousr]_[A-Za-z0-9]{10,}|github_pat_|BEGIN [A-Z ]*PRIVATE KEY", text)


@pytest.mark.skipif(sys.platform == "win32", reason="wrappers shell POSIX")
def test_gh_wrapper_uses_the_agent_token_and_never_an_inherited_one(tmp_path):
    token = tmp_path / "token"
    token.write_text("ghs_agent\n")
    real = tmp_path / "realgh"
    real.write_text('#!/bin/sh\necho "GH_TOKEN=$GH_TOKEN GITHUB_TOKEN=${GITHUB_TOKEN:-none} args=$*"\n')
    real.chmod(0o755)
    env = dict(os.environ, LQ_AGENT_TOKEN_FILE=str(token), LQ_REAL_GH=str(real), GH_TOKEN="ghp_owner",
               GITHUB_TOKEN="ghp_owner2")
    out = subprocess.run([str(ORCH_DIR / "bin" / "gh"), "pr", "list"], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "GH_TOKEN=ghs_agent GITHUB_TOKEN=none args=pr list"


@pytest.mark.skipif(sys.platform == "win32", reason="wrappers shell POSIX")
def test_git_credential_helper_answers_only_github_https_get(tmp_path):
    token = tmp_path / "token"
    token.write_text("ghs_agent\n")
    helper = str(ORCH_DIR / "bin" / "git-credential-lodyorch")
    env = dict(os.environ, LQ_AGENT_TOKEN_FILE=str(token))

    def ask(op, text):
        return subprocess.run([helper, op], input=text, env=env, capture_output=True, text=True).stdout

    assert ask("get", "protocol=https\nhost=github.com\n\n") == "username=x-access-token\npassword=ghs_agent\n"
    assert ask("get", "protocol=https\nhost=evil.example\n\n") == ""
    assert ask("store", "protocol=https\nhost=github.com\npassword=x\n\n") == ""


# --- crochet de garde -----------------------------------------------------------------------------

def guard(cmd=None, tool="Bash", **inp):
    call = {"tool_name": tool, "tool_input": dict(inp, command=cmd) if cmd is not None else inp}
    return subprocess.run([sys.executable, str(ORCH_DIR / "guard.py")], input=json.dumps(call),
                          capture_output=True, text=True).returncode


@pytest.mark.parametrize("cmd", [
    "cat /etc/lodyorch/merge-token",
    "ls /var/lib/lodyorch-merge/agent",
    "GH_TOKEN=abc gh api user",
    "export GH_CONFIG_DIR=/tmp/x; gh auth status",
    "LQ_REAL_GH=/tmp/gh gh pr list",
    "gh api -X POST repos/ReC82/lody-video-factory/rulesets --input r.json",
    "gh api -X DELETE repos/ReC82/lody-video-factory/branches/main/protection",
    "gh api -X PUT repos/ReC82/lody-video-factory/collaborators/x",
    "gh api repos/ReC82/lody-video-factory/installation",
    "gh issue edit 3 --add-label merge:auto",
    "gh api -X POST repos/ReC82/lody-video-factory/issues/3/labels -f labels[]=merge:auto",
])
def test_guard_blocks_privilege_escalation_attempts(cmd):
    assert guard(cmd) == 2, cmd


def test_guard_blocks_reading_broker_secrets_with_file_tools():
    assert guard(tool="Read", file_path="/etc/lodyorch/app-key.pem") == 2


@pytest.mark.parametrize("cmd", ["gh pr list --repo ReC82/lody-video-factory", "git push origin agent/207-x",
                                 "gh issue view 207 --comments"])
def test_guard_keeps_normal_session_work(cmd):
    assert guard(cmd) == 0, cmd



def test_main_ruleset_blocks_updates_for_everyone_but_repository_admins():
    """Protection indépendante de l'hôte : l'application des sessions ne peut ni fusionner ni pousser
    sur la branche par défaut ; seul le rôle administrateur (propriétaire, broker) contourne."""
    rs = json.loads((ORCH_DIR / "ruleset-main.json").read_text(encoding="utf-8"))
    assert rs["target"] == "branch" and rs["enforcement"] == "active"
    assert rs["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert {r["type"] for r in rs["rules"]} == {"update", "deletion", "non_fast_forward"}
    assert rs["bypass_actors"] == [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
