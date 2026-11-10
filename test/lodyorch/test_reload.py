"""« lodyorch reload » relit lodyorch.env (LQ_ENV_FILE) avant de ré-exécuter le code installé."""

import os

from test.lodyorch.conftest import ORCH_DIR, instance_env


def test_instance_points_reload_at_the_installed_env_file():
    assert instance_env()["LQ_ENV_FILE"] == "/opt/lodyorch/lodyorch.env"


def test_env_file_parser_matches_the_shell_format(orch):
    env = orch.mod.read_env_file(ORCH_DIR / "lodyorch.env")
    assert env == instance_env()
    assert env["LQ_REPORT_PROJECT"] == "Lody Video Factory"  # guillemets retirés comme par sh


def test_reexec_reloads_the_env_file_then_execs(orch, monkeypatch, tmp_path):
    env_file = tmp_path / "lodyorch.env"
    env_file.write_text('# commentaire\nLQ_RUN_REPORTS=1\nLQ_NOTIFY_PREFIX="[Lody Video Factory]"\n', encoding="utf-8")
    monkeypatch.setenv("LQ_ENV_FILE", str(env_file))
    monkeypatch.setenv("LQ_RUN_REPORTS", "0")
    seen = {}

    def fake_execv(exe, argv):
        seen.update(argv=argv, reports=os.environ["LQ_RUN_REPORTS"], prefix=os.environ["LQ_NOTIFY_PREFIX"])
    monkeypatch.setattr(orch.mod.os, "execv", fake_execv)
    orch.mod.reexec(orch.con)
    assert seen["argv"][-1] == "run"
    assert (seen["reports"], seen["prefix"]) == ("1", "[Lody Video Factory]")


def test_unreadable_env_file_keeps_current_values(orch, monkeypatch, tmp_path):
    monkeypatch.setenv("LQ_ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.setenv("LQ_RUN_REPORTS", "1")
    monkeypatch.setattr(orch.mod.os, "execv", lambda *a: None)
    orch.con = orch.mod.db()
    orch.mod.reexec(orch.con)
    assert os.environ["LQ_RUN_REPORTS"] == "1"
