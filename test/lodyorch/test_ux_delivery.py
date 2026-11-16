"""#221 — superviseur : voie `ux-safe` de la fusion automatique et relais du journal du livreur
(mails « EN LIGNE — prêt à tester », « Déploiement échoué / retour arrière »). Faux GitHub, aucun réseau."""

import json

from test.lodyorch.test_auto_merge import AFTER, AUTO, audit, automerge, comments, merges, poll, ready

UX_FILES = [{"filename": "webui/lody/view_home.py", "status": "modified", "patch": "@@\n+st.caption('Projets')"},
            {"filename": "webui/lody/styles.css", "status": "modified", "patch": "@@\n+.lody-row { gap: 4px; }"},
            {"filename": "test/lody/test_home.py", "status": "modified", "patch": "@@\n+def test_x(): pass"}]
DATA_FILES = [{"filename": "webui/lody/db.py", "status": "modified", "patch": "@@\n+x = 1"},
              {"filename": "test/lody/test_db.py", "status": "modified", "patch": "@@\n+def test_x(): pass"}]


def test_ux_safe_mode_off_by_default_keeps_merge_auto_required(orch):
    assert orch.mod.UX_SAFE_AUTO is False  # lodyorch.env livré : LQ_UX_SAFE_AUTO=0
    ready(orch, files=UX_FILES, labels=(AUTO, "priority:P0"))
    poll(orch)
    (row,) = audit(orch)
    assert not row["eligible"] and "autorisation-merge-auto" in row["refusals"]
    assert "classification-ux-safe" not in row["refusals"] and merges(orch) == []


def test_ux_safe_pr_is_simulated_without_merge_auto_in_dry_run(orch, monkeypatch):
    monkeypatch.setattr(orch.mod, "UX_SAFE_AUTO", True)
    ready(orch, files=UX_FILES, labels=(AUTO, "priority:P0"))
    poll(orch)
    (row,) = audit(orch)
    assert row["eligible"] == 1 and row["mode"] == "dry-run"
    assert "classification-ux-safe" in row["matrix"]
    assert len(comments(orch, 1, "**Simulation**")) == 1 and merges(orch) == []
    poll(orch)
    assert len(comments(orch, 1, "**Simulation**")) == 1  # idempotent


def test_non_ux_safe_pr_is_refused_with_reasons_and_never_merged(orch, monkeypatch):
    monkeypatch.setattr(orch.mod, "UX_SAFE_AUTO", True)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    ready(orch, files=DATA_FILES, labels=(AUTO, "priority:P0"), created_at=AFTER)
    poll(orch)
    row = audit(orch)[-1]
    assert not row["eligible"] and "classification-ux-safe" in row["refusals"]
    assert merges(orch) == []


def test_ux_safe_pr_opened_after_activation_is_merged_in_on_mode(orch, monkeypatch):
    monkeypatch.setattr(orch.mod, "UX_SAFE_AUTO", True)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    num = ready(orch, files=UX_FILES, labels=(AUTO, "priority:P0"), created_at=AFTER)
    lines = poll(orch)
    assert [m["pr"] for m in merges(orch)] == [num]
    assert lines and "pas encore en ligne" in lines[0]
    (c,) = comments(orch, 1, "**CI verte — fusionné**")
    assert "Pas encore en ligne" in c["body"]


def test_ux_safe_pr_opened_before_activation_is_not_merged(orch, monkeypatch):
    monkeypatch.setattr(orch.mod, "UX_SAFE_AUTO", True)
    automerge(orch, "on", ceiling="on", monkeypatch=monkeypatch)
    ready(orch, files=UX_FILES, labels=(AUTO, "priority:P0"), created_at="2000-01-01T00:00:00Z")
    poll(orch)
    assert merges(orch) == [] and "ux-safe-apres-activation" in audit(orch)[-1]["refusals"]


# --- relais du journal du livreur ----------------------------------------------------------------

def journal(tmp_path, *entries, partial=""):
    path = tmp_path / "ux-deliveries.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        fh.write(partial)
    return path


def entry(state, sha="c" * 40, tickets=(195,), **extra):
    e = {"id": sha[:12], "at": "2026-11-13T17:00:00+00:00", "state": state, "target_sha": sha,
         "served_sha": "d" * 40, "tickets": list(tickets), "detail": f"détail {state}",
         "site_url": "https://video.example.invalid"}
    e.update(extra)
    return e


def mails(orch):
    return orch.con.execute("SELECT * FROM notifications ORDER BY created_at, key").fetchall()


def setup_relay(orch, monkeypatch, path):
    monkeypatch.setattr(orch.mod, "UX_DELIVERY_JOURNAL", str(path))
    monkeypatch.setattr(orch.mod, "NOTIFY_SMTP_ENV", str(orch.tmp / "absent-smtp"))  # file : oui, envoi : non


def test_delivery_states_become_unambiguous_mails_and_ticket_comments_once(orch, monkeypatch):
    orch.issue(195, [AUTO])
    path = journal(orch.tmp, entry("deploying"),
                   entry("online", proof={"release": "cccccccccccc (validée)", "health": "ok"}),
                   partial='{"id": "incomplet"')
    setup_relay(orch, monkeypatch, path)
    subjects = orch.mod.watch_deliveries(orch.con)
    assert [s.split(" — #")[0] for s in subjects] == ["[Lody Video Factory] Déploiement en cours",
                                                      "[Lody Video Factory] EN LIGNE — prêt à tester"]
    online = [m for m in mails(orch) if "EN LIGNE" in m["subject"]][0]
    assert "#195" in online["subject"] and "cccccccccc" in online["subject"]
    for part in ("Commit : cccccccccccc", "Site : https://video.example.invalid", "Preuve :", "release : cccccccccccc",
                 "https://github.com/ReC82/lody-video-factory/issues/195", "2026-11-13T17:00:00"):
        assert part in online["body"], part
    (c,) = comments(orch, 195, "EN LIGNE — prêt à tester")
    assert c["body"].startswith("<!-- lodyorch -->")
    assert comments(orch, 195, "Déploiement en cours") == []  # état intermédiaire : mail seulement
    # Idempotent : rien de neuf → rien ; la ligne incomplète est relue une fois terminée.
    assert orch.mod.watch_deliveries(orch.con) == []
    with path.open("a", encoding="utf-8") as fh:
        fh.write(', "state": "online"}\n')
    assert orch.mod.watch_deliveries(orch.con) == []  # entrée sans cible valide : ignorée
    assert len(mails(orch)) == 2


def test_failed_delivery_and_rollback_alert(orch, monkeypatch):
    orch.issue(212, [AUTO])
    path = journal(orch.tmp, entry("deploying", sha="e" * 40, tickets=(212,)),
                   entry("rolled_back", sha="e" * 40, tickets=(212,)))
    setup_relay(orch, monkeypatch, path)
    orch.mod.watch_deliveries(orch.con)
    assert any("Déploiement échoué / retour arrière" in m["subject"] for m in mails(orch))
    assert len(comments(orch, 212, "Déploiement échoué / retour arrière")) == 1
    assert not any("EN LIGNE" in m["subject"] for m in mails(orch))


def test_truncated_journal_is_reread_without_duplicates(orch, monkeypatch):
    path = journal(orch.tmp, entry("refused", tickets=()))
    setup_relay(orch, monkeypatch, path)
    assert len(orch.mod.watch_deliveries(orch.con)) == 1
    path.write_text(json.dumps(entry("refused", tickets=())) + "\n", encoding="utf-8")
    orch.mod.ctl_set(orch.con, "ux_delivery.offset", 10 ** 6)  # journal remplacé, plus court
    assert orch.mod.watch_deliveries(orch.con) == []
    assert len(mails(orch)) == 1


def test_no_journal_configured_does_nothing(orch):
    assert orch.mod.UX_DELIVERY_JOURNAL == ""
    assert orch.mod.watch_deliveries(orch.con) == []


def test_waiting_mail_never_says_simply_done(orch):
    assert orch.mod.STATE_TEXT["DONE"] != "terminé"
    assert "PR" in orch.mod.STATE_TEXT["WAITING_OWNER"]
    t = {"status": "WAITING_OWNER", "ci_state": "merged"}
    assert orch.mod.report_state(t, "OK").startswith("CI verte — fusionné")
    assert "pas en ligne" in orch.mod.report_state({"status": "WAITING_OWNER", "ci_state": "success"}, "OK")
