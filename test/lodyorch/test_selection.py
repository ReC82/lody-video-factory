"""Sélection des tickets LODYORCH (#116) : éligibilité agent:auto, priorités P0→P3, blocages,
dépendances, verrou visible agent:in-progress, PR exclues."""

AUTO = "agent:auto"


def test_only_agent_auto_issues_enter_the_queue(orch):
    orch.issue(1, [AUTO, "priority:P1"])
    orch.issue(2, ["priority:P0"])  # sans agent:auto : jamais enregistré
    orch.sync()
    assert orch.status(1) == "READY"
    assert orch.status(2) is None
    assert orch.next_issue() == 1


def test_priority_order_is_strict_then_stable_by_number(orch):
    orch.issue(30, [AUTO, "priority:P3"])
    orch.issue(12, [AUTO, "priority:P1"])
    orch.issue(11, [AUTO, "priority:P1"])
    orch.issue(40, [AUTO, "priority:P2"])
    orch.issue(50, [AUTO, "priority:P0"])
    orch.sync()
    order = [r["issue"] for r in orch.con.execute(
        "SELECT issue FROM tasks WHERE status='READY' ORDER BY priority, issue")]
    assert order == [50, 11, 12, 40, 30]
    assert orch.next_issue() == 50


def test_invalid_or_missing_priority_defaults_to_p2_and_two_labels_keep_the_highest(orch):
    orch.issue(1, [AUTO, "priority:high"])
    orch.issue(2, [AUTO])
    orch.issue(3, [AUTO, "priority:P3", "priority:P0"])
    orch.issue(4, [AUTO, "priority:P7"])
    orch.sync()
    prio = {n: orch.mod.task(orch.con, n)["priority"] // 10 for n in (1, 2, 3, 4)}
    assert prio == {1: 2, 2: 2, 3: 0, 4: 2}


def test_pull_requests_are_never_selected(orch):
    orch.issue(7, [AUTO, "priority:P0"], pull_request={"url": "x"})
    orch.issue(8, [AUTO, "priority:P2"])
    orch.sync()
    assert orch.status(7) is None
    assert orch.next_issue() == 8


def test_blocked_and_human_labels_are_never_processed(orch):
    orch.issue(1, [AUTO, "priority:P0", "agent:blocked"])
    orch.issue(2, [AUTO, "priority:P0", "agent:human"])
    orch.issue(3, [AUTO, "priority:P3"])
    orch.sync()
    assert orch.status(1) == "BLOCKED"
    assert orch.status(2) == "SUSPENDED"
    assert "agent:blocked" in orch.mod.task(orch.con, 1)["blockers"]
    assert orch.next_issue() == 3
    assert orch.cycle() == 3
    assert [c["issue"] for c in orch.claude_calls()] == ["3"]


def test_owner_labels_change_eligibility_both_ways(orch):
    orch.issue(1, [AUTO, "priority:P1"])
    orch.sync()
    orch.edit_labels(1, remove=[AUTO])
    orch.sync()
    assert orch.status(1) == "SUSPENDED"
    assert orch.next_issue() is None
    orch.edit_labels(1, add=[AUTO])
    orch.sync()
    assert orch.status(1) == "READY"
    orch.edit_labels(1, add=["agent:human"])
    orch.sync()
    assert orch.status(1) == "SUSPENDED"


def test_removing_agent_blocked_requeues_the_ticket(orch):
    orch.issue(5, [AUTO, "priority:P1"])
    assert orch.cycle(mode="blocked") == 5
    assert orch.status(5) == "BLOCKED"
    assert "agent:blocked" in orch.labels(5)
    orch.sync()
    assert orch.next_issue() is None  # bloqué : jamais repris tout seul
    orch.edit_labels(5, remove=["agent:blocked"])
    orch.sync()
    assert orch.status(5) == "READY"
    assert orch.next_issue() == 5


def test_depends_on_an_open_issue_waits_and_an_eligible_dependency_goes_first(orch):
    orch.issue(10, [AUTO, "priority:P0"], body="Depends on #11")
    orch.issue(11, [AUTO, "priority:P3"])
    orch.issue(12, [AUTO, "priority:P0"], body="Dépend de #99")  # dépendance hors file
    orch.issue(99, ["priority:P1"])
    orch.sync()
    assert orch.next_issue() == 11  # la dépendance traitable passe d'abord
    orch.cycle()
    assert orch.status(11) == "WAITING_OWNER"
    # PR de #11 pas encore fusionnée : #10 et #12 attendent (LQ_DEPS_DONE_STATES=DONE).
    assert orch.next_issue() is None
    orch.close(11)
    orch.close(99)
    orch.sync()
    assert orch.next_issue() == 10


def test_in_progress_label_set_by_someone_else_is_a_lock(orch):
    orch.issue(1, [AUTO, "priority:P0", "agent:in-progress"])  # pris par un autre agent
    orch.issue(2, [AUTO, "priority:P2"])
    orch.sync()
    assert orch.status(1) == "SUSPENDED"
    assert orch.next_issue() == 2
    orch.edit_labels(1, remove=["agent:in-progress"])
    orch.sync()
    assert orch.status(1) == "READY"


def test_own_in_progress_label_does_not_lock_us_out(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.sync()
    orch.mod.set_status_label(orch.con, 1, "RUNNING")
    orch.mod.update_task(orch.con, 1, status="RUNNING")
    assert "agent:in-progress" in orch.labels(1)
    orch.sync()
    assert orch.status(1) == "RUNNING"
    assert orch.next_issue() == 1


def test_closed_issue_leaves_the_queue(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.sync()
    orch.close(1)
    orch.sync()
    assert orch.status(1) == "DONE"
    assert orch.next_issue() is None
