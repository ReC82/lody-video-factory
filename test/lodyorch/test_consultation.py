"""#139 (livré dans la PR du #127) : consulter l'état depuis la console ne préempte jamais la file,
et GitHub est la file par défaut (aucun `lodyorch add` nécessaire).

Faux `gh` et faux `claude` (conftest) : aucun réseau, aucun appel au modèle."""

import json
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from test.lodyorch.conftest import ORCH_DIR

AUTO = "agent:auto"
BLOCKED = "agent:blocked"


def hook(orch, state, prompt=None):
    """Le crochet de la console tel que Claude Code l'appelle : JSON du message sur l'entrée standard."""
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": prompt}) if prompt is not None else ""
    return subprocess.run([sys.executable, str(ORCH_DIR / "lodyorch.py"), "human", state], input=payload,
                          capture_output=True, text=True, timeout=30)


# -- classement des messages -------------------------------------------------------------------

@pytest.mark.parametrize("prompt", [
    "lodyorch status", "  lodyorch status  ", "status", "Statut ?", "lodyorch queue", "lodyorch log 127",
    "lodyorch sessions", "lodyorch notifications", "lodyorch pulse", "!lodyorch status", "État de la file",
    "Où en est-on ?", "où en es-tu", "on en est où ?", "Où en est le ticket #127 ?", "Quel est l'état de la file ?",
    "quel est le statut ?", "le point stp", "fais le point", "Quoi de neuf ?", "what's the status?",
    "Bonjour, où en est-on ? merci", "Status update", "qu'est-ce que fait la file maintenant ?",
])
def test_status_questions_are_consultations(orch, prompt):
    assert orch.mod.is_consultation(prompt), prompt


@pytest.mark.parametrize("prompt", [
    "", "lodyorch status puis lance #120", "lodyorch pause", "lodyorch stop", "lodyorch add 120",
    "corrige le bug du statut", "Arrête la session en cours", "Où en est-on ? Et mets #120 en pause",
    "status: fixe le label de #128", "Peux-tu relire la PR #140 et la corriger ?", "pause",
    "écris un ticket pour l'état de la file", "x" * 400,
])
def test_real_requests_are_not_consultations(orch, prompt):
    assert not orch.mod.is_consultation(prompt), prompt


@pytest.mark.parametrize("body", ["Merci !", "👍", "Super, merci beaucoup", "ok", "Bien reçu.", "Bravo 🎉",
                                  "LGTM? non : parfait", "Où en est-on ?", "merci pour le rapport"])
def test_acknowledgements_do_not_call_for_work(orch, body):
    if body.startswith("LGTM"):
        assert not orch.mod.is_acknowledgement(body)
    else:
        assert orch.mod.is_acknowledgement(body), body


@pytest.mark.parametrize("body", ["Merci, mais corrige le test qui échoue", "ok vas-y, lance la suite",
                                  "1. Oui, prends l'option A", "Le bouton ne marche pas sur mobile", "Non.",
                                  "merci " * 60])
def test_answers_and_corrections_still_resume_the_ticket(orch, body):
    assert not orch.mod.is_acknowledgement(body), body


# -- crochets de la console --------------------------------------------------------------------

def test_status_consultation_does_not_hold_the_queue_nor_restart_the_quiet_delay(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    out = hook(orch, "busy", "lodyorch status")
    assert out.returncode == 0 and "lecture seule" in out.stdout and "sans pause" in out.stdout
    assert orch.mod.human_state(orch.con)[0] == "idle"
    assert hook(orch, "idle").returncode == 0
    assert orch.mod.human_state(orch.con)[0] == "idle"  # le Stop d'une consultation ne lance aucun délai
    assert orch.mod.start_blocker(orch.con) is None or "Priorité humaine" not in orch.mod.start_blocker(orch.con)[0]
    assert orch.mod.ctl_get(orch.con, "human_consult_at")


def test_consultation_after_a_real_exchange_keeps_the_original_quiet_deadline(orch):
    hook(orch, "busy", "corrige le label de #128")
    hook(orch, "idle")
    state, at, until = orch.mod.human_state(orch.con)
    assert state == "quiet"
    time.sleep(1.1)
    hook(orch, "busy", "où en est-on ?")
    hook(orch, "idle")
    assert orch.mod.human_state(orch.con) == (state, at, until)  # délai de calme NON relancé


def test_real_request_still_takes_priority(orch):
    out = hook(orch, "busy", "Mets #120 en pause, je veux d'abord relire la PR")
    assert out.returncode == 0 and "lecture seule" not in out.stdout
    assert orch.mod.human_state(orch.con)[0] == "busy"
    hook(orch, "idle")
    assert orch.mod.human_state(orch.con)[0] == "quiet"


def test_unreadable_hook_input_is_treated_as_a_real_request(orch):
    for payload in ("", "pas du json", json.dumps({"prompt": 42})):
        orch.mod.ctl_set(orch.con, "human_state", "")
        out = subprocess.run([sys.executable, str(ORCH_DIR / "lodyorch.py"), "human", "busy"], input=payload,
                             capture_output=True, text=True, timeout=30)
        assert out.returncode == 0
        assert orch.mod.human_state(orch.con)[0] == "busy", payload


def test_ten_status_requests_during_a_session_cost_nothing(orch, monkeypatch):
    """Test obligatoire du #139 : 10 « lodyorch status » pendant une tâche → 0 pause, 0 session de plus,
    0 délai de relance ; le ticket avance jusqu'à sa livraison."""
    orch.issue(1, [AUTO, "priority:P0"])
    orch.mod.ctl_set(orch.con, "cfg.yield_grace_seconds", 30)
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "8")
    orch.sync()
    yields = []

    def owner():
        time.sleep(1)
        for i in range(10):
            hook(orch, "busy", "lodyorch status" if i % 2 else "Où en est-on ?")
            yields.append(bool(list(orch.mod.LOG_DIR.glob("*.yield"))))
            hook(orch, "idle")
            time.sleep(0.3)

    th = threading.Thread(target=owner)
    th.start()
    assert orch.cycle() == 1
    th.join()
    assert len(yields) == 10 and not any(yields)  # jamais de rangement demandé
    (s,) = orch.con.execute("SELECT outcome FROM sessions").fetchall()
    assert s["outcome"] == "COMPLETED"
    assert len(orch.claude_calls()) == 1
    assert orch.mod.task(orch.con, 1)["status"] == "WAITING_OWNER"
    assert orch.mod.human_state(orch.con)[0] == "idle"
    assert not orch.con.execute("SELECT 1 FROM events WHERE kind='yield'").fetchone()


# -- GitHub = file par défaut ------------------------------------------------------------------

def test_agent_auto_ticket_is_dispatched_without_add(orch):
    orch.issue(5, [AUTO, "priority:P2"])
    orch.issue(6, [AUTO, "priority:P1"], body="Dépend de #5")
    orch.issue(7, ["priority:P0"])  # sans agent:auto : jamais pris
    assert orch.cycle() == 5  # #6 attend sa dépendance ; #7 n'entre pas dans la file
    assert orch.status(7) is None
    assert not orch.con.execute("SELECT 1 FROM events WHERE kind='control'").fetchone()  # aucun add


def test_github_priority_change_applies_to_a_ticket_already_queued(orch):
    orch.issue(1, [AUTO, "priority:P2"])
    orch.issue(2, [AUTO, "priority:P1"])
    orch.sync()
    assert orch.next_issue() == 2
    orch.edit_labels(1, add=["priority:P0"], remove=["priority:P2"])
    orch.sync()
    assert orch.mod.task(orch.con, 1)["priority"] == 0 and orch.next_issue() == 1


def test_local_only_block_is_mirrored_on_github_then_lifted_by_removing_the_label(orch):
    """Incident du 10/11 : `skip` local, invisible sur GitHub, exigeait `lodyorch add` (#121, #128)."""
    orch.issue(3, [AUTO, "priority:P2"])
    orch.sync()
    orch.mod.update_task(orch.con, 3, status="BLOCKED", blockers="Hors du premier essai réel")
    orch.sync()
    assert BLOCKED in orch.labels(3) and orch.status(3) == "BLOCKED"
    orch.sync()  # idempotent : pas de nouvel événement
    assert len(orch.con.execute("SELECT 1 FROM events WHERE issue=3 AND message LIKE 'Blocage local%'").fetchall()) == 1
    orch.edit_labels(3, remove=[BLOCKED])
    orch.sync()
    assert orch.status(3) == "READY" and orch.next_issue() == 3


def test_skip_posts_the_block_label_and_add_removes_it_keeping_github_priority(orch):
    orch.issue(4, [AUTO, "priority:P1"])
    orch.sync()
    orch.mod.cmd_skip(orch.con, SimpleNamespace(issue=4, reason=""))
    assert BLOCKED in orch.labels(4) and orch.status(4) == "BLOCKED"
    orch.mod.cmd_add(orch.con, SimpleNamespace(issue=4, prio=None, deps=None, note=None))
    assert BLOCKED not in orch.labels(4) and orch.status(4) == "READY"
    assert orch.mod.task(orch.con, 4)["priority"] == 10  # priorité GitHub conservée (P1), pas 50
    orch.sync()
    assert orch.status(4) == "READY"


def test_add_of_an_unknown_ticket_takes_its_github_priority(orch):
    orch.issue(8, [AUTO, "priority:P0"])
    orch.mod.cmd_add(orch.con, SimpleNamespace(issue=8, prio=None, deps=None, note=None))
    t = orch.mod.task(orch.con, 8)
    assert t["priority"] == 0 and t["title"] == "Ticket 8"


def test_thank_you_comment_does_not_wake_claude(orch):
    orch.issue(1, [AUTO, "priority:P0"])
    assert orch.cycle() == 1
    orch.mod.update_task(orch.con, 1, gh_since="2026-11-10T00:00:00Z", ci_state="success")
    data = orch.gh()
    base = {"issue": 1, "issue_url": "x/issues/1", "author_association": "OWNER", "created_at": "2026-11-10T09:00:00Z"}
    data["comments"] += [dict(base, id=500, body="Merci, bien reçu 👍", html_url="u500")]
    orch.set_gh(data)
    orch.sync()
    assert orch.status(1) == "WAITING_OWNER"
    assert orch.con.execute("SELECT 1 FROM events WHERE kind='reply_ignored'").fetchone()
    data = orch.gh()
    data["comments"] += [dict(base, id=501, body="Le bouton ne marche pas : corrige-le", html_url="u501")]
    orch.set_gh(data)
    orch.sync()
    assert orch.status(1) == "READY"  # une vraie demande relance toujours le ticket


def test_new_ticket_is_discovered_during_a_long_session(orch, monkeypatch):
    orch.issue(1, [AUTO, "priority:P0"])
    orch.mod.SYNC_EVERY_SECONDS = 2
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "8")
    orch.sync()
    threading.Timer(2, lambda: orch.issue(9, [AUTO, "priority:P1"])).start()
    assert orch.cycle() == 1
    assert orch.status(9) == "READY"
    assert orch.con.execute("SELECT 1 FROM events WHERE issue=9 AND kind='discovered'").fetchone()
