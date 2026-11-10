"""Protocole de traitement LODYORCH (#117) : labels attendus, labels incohérents, prise en
charge publiée une seule fois, livraison sans PR refusée, PR ouvertes jamais sélectionnées."""

import json

from test.lodyorch.conftest import ORCH_DIR

AUTO = "agent:auto"
PROTOCOL = json.loads((ORCH_DIR / "labels.json").read_text(encoding="utf-8"))


class Args:
    def __init__(self, apply=False):
        self.apply = apply


def test_protocol_lists_every_label_of_the_ticket():
    assert set(PROTOCOL) == {"agent:auto", "agent:human", "agent:blocked", "agent:in-progress", "priority:P0",
                             "priority:P1", "priority:P2", "priority:P3", "status:needs-test"}
    for spec in PROTOCOL.values():
        assert len(spec["color"]) == 6 and spec["description"]


def test_labels_command_reports_then_creates_missing_labels_without_deleting(orch, capsys):
    data = orch.gh()
    data["repo_labels"] = {"agent:auto": {"name": "agent:auto", "color": "ededed", "description": ""},
                           "bug": {"name": "bug", "color": "d73a4a", "description": "x"}}
    orch.set_gh(data)
    assert orch.mod.cmd_labels(orch.con, Args()) == 1
    out = capsys.readouterr().out
    assert "agent:blocked — manquant" in out and "agent:auto — à mettre à jour" in out
    assert "labels --apply" in out
    assert set(orch.gh()["repo_labels"]) == {"agent:auto", "bug"}  # rien n'a changé
    assert orch.mod.cmd_labels(orch.con, Args(apply=True)) == 0
    labels = orch.gh()["repo_labels"]
    assert set(labels) == set(PROTOCOL) | {"bug"}
    assert labels["agent:auto"]["color"] == PROTOCOL["agent:auto"]["color"]
    assert orch.mod.cmd_labels(orch.con, Args()) == 0
    assert "Référentiel complet." in capsys.readouterr().out


def test_invalid_labels_are_reported_with_the_applied_rule(orch):
    orch.issue(1, [AUTO, "priority:P3", "priority:P0"])
    orch.issue(2, [AUTO, "priority:urgent"])
    orch.issue(3, [AUTO, "priority:P1", "agent:human"])
    orch.issue(4, [AUTO, "priority:P1", "agent:wip"])
    orch.issue(5, [AUTO, "priority:P1"])
    orch.issue(6, ["priority:nope"])  # hors file : ignoré, sans alerte
    orch.sync()
    issues = {n: orch.mod.task(orch.con, n)["label_issues"] for n in (1, 2, 3, 4, 5)}
    assert issues[1] == "plusieurs priorités (P0, P3) : P0 retenue"
    assert "priorité invalide « priority:urgent »" in issues[2] and "P2 appliquée" in issues[2]
    assert issues[3] == "agent:auto avec agent:human : exclu de la file"
    assert issues[4] == "label inconnu « agent:wip »"
    assert issues[5] == ""
    assert orch.mod.task(orch.con, 6) is None
    assert orch.mod.task(orch.con, 1)["priority"] == 0
    # Corrigé sur GitHub : l'alerte disparaît à la synchro suivante.
    orch.edit_labels(1, remove=["priority:P3"])
    orch.sync()
    assert orch.mod.task(orch.con, 1)["label_issues"] == ""
    events = [r["message"] for r in orch.con.execute("SELECT message FROM events WHERE kind='labels'")]
    assert len(events) == 4  # une alerte par ticket incohérent, pas une par synchro


def test_claim_comment_is_published_once_even_across_crashes(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.cycle(mode="crash")
    orch.mod.update_task(orch.con, 1, not_before="")
    orch.cycle(mode="deliver")
    comments = [c for c in orch.gh()["comments"] if c["issue"] == 1 and orch.mod.CLAIM_MARKER in c["body"]]
    assert len(comments) == 1
    assert orch.mod.CLAIM_MARKER in comments[0]["body"] and orch.mod.BOT_MARKER in comments[0]["body"]
    assert "Aucune fusion ni aucun déploiement automatique" in comments[0]["body"]


def test_claim_already_on_github_is_not_duplicated(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    data = orch.gh()
    data["comments"] = [{"issue": 1, "id": 1, "body": f"{orch.mod.BOT_MARKER}\n{orch.mod.CLAIM_MARKER}\nancien",
                         "issue_url": "x/issues/1", "author_association": "OWNER", "created_at": "2026-11-10T00:00:00Z"}]
    orch.set_gh(data)
    orch.cycle()
    assert len([c for c in orch.gh()["comments"] if c["issue"] == 1 and orch.mod.CLAIM_MARKER in c["body"]]) == 1
    assert orch.mod.task(orch.con, 1)["claimed"] == 1


def test_unreadable_comments_postpone_the_claim_without_blocking_the_work(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    data = orch.gh()
    data["comments_offline"] = True
    orch.set_gh(data)
    assert orch.cycle() == 1
    assert orch.mod.task(orch.con, 1)["claimed"] == 0
    assert orch.status(1) == "WAITING_OWNER"


def test_delivery_without_pr_is_not_a_delivery(orch, monkeypatch):
    orch.issue(1, [AUTO, "priority:P0"])
    monkeypatch.setenv("FAKE_CLAUDE_NO_PR", "1")
    orch.cycle()
    t = orch.mod.task(orch.con, 1)
    assert t["status"] == "RUNNING" and "sans PR" in t["next_action"]
    assert "status:needs-test" not in orch.labels(1)
    for _ in range(2):
        orch.cycle()
    t = orch.mod.task(orch.con, 1)
    assert t["status"] == "BLOCKED" and "sans PR" in t["blockers"]
    assert "agent:blocked" in orch.labels(1)


def test_open_pull_requests_are_never_tickets(orch):
    orch.issue(10, [AUTO, "priority:P0"], pull_request={"url": "x"}, title="PR de lodyorch")
    orch.issue(11, [AUTO, "priority:P3"])
    orch.sync()
    assert orch.mod.task(orch.con, 10) is None
    assert orch.cycle() == 11
    assert [c["issue"] for c in orch.claude_calls()] == ["11"]
