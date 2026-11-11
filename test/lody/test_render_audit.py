"""Audit de reproductibilité, de cache et de coût du rendu 2D (#168) : lecture seule, aucun appel payant."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from lody.animation import render_audit as ra
from lody.animation import worker
from lody.generation.costing import PriceBook

ROOT = Path(__file__).resolve().parents[2]
DEMO_150 = ROOT / "docs" / "animation" / "voltix-150"


def _storyboard() -> dict:
    return json.loads((DEMO_150 / "storyboard.json").read_text(encoding="utf-8"))


def _set_text(board: dict, scene: int, old: str, new: str) -> None:
    for layer in board["scenes"][scene]["render"]["layers"]:
        for obj in layer["objects"]:
            if obj["type"] == "text" and obj["text"]["content"] == old:
                obj["text"]["content"] = new
                return
    raise AssertionError(old)


def _job(tmp_path: Path, *, state: str = "done") -> Path:
    directory = tmp_path / "data" / "animation" / "prj1" / "task1"
    (directory / "scenes").mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"schema": worker.JOB_SCHEMA, "storyboard": _storyboard()}))
    (directory / "state.json").write_text(json.dumps({"state": state}))
    (directory / "out.mp4").write_bytes(b"video")
    manifest = {
        "mode": "animation_2d", "fps": 30, "frames": 600, "expected_seconds": 20.0, "duration_seconds": 20.0,
        "sha256": ra._sha256(directory / "out.mp4"), "size_bytes": 5, "render_ms": 150_000,
        "storyboard_sha256": "x", "fallback_scenes": [],
        "scenes": [
            {"index": 1, "id": "a", "status": "animated", "frames": 300, "expected_seconds": 10.0,
             "duration_seconds": 10.0, "render_ms": 130_000, "sha256": "s1"},
            {"index": 2, "id": "b", "status": "animated", "frames": 300, "expected_seconds": 10.0,
             "duration_seconds": 10.5, "render_ms": 5, "sha256": "s2", "cache": "hit", "render_ms_saved": 40_000},
        ],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


# -- rapport --------------------------------------------------------------------------------------------------
def test_audit_demo_150_is_reproducible_and_measured():
    report = ra.audit_render(DEMO_150)
    assert report["integrity"]["mp4_matches_manifest"] is True
    assert report["integrity"]["storyboard_frozen_sha256"] == report["integrity"]["storyboard_sha256"]
    stages = {s["stage"]: s for s in report["stages"]}
    assert stages["remotion"]["wall_seconds"] == pytest.approx(118.031)
    assert stages["assembly"]["wall_seconds"] == pytest.approx(21.415)
    assert stages["voice"]["wall_seconds"] is None and stages["voice"]["source"] == "non instrumenté"
    assert report["cache"] == {"scenes": 3, "reused": 0, "rendered": 3, "render_seconds_saved": 0.0, "fallbacks": 0}
    assert all(c["status"] in ("ok", "unknown") for c in report["checks"])
    # voix Piper locale : aucune dépense ; estimation ElevenLabs sans tarif : jamais inventée
    assert report["spend"]["known"] == []
    assert report["spend"]["estimated"][0]["status"] == "unpriced"
    assert report["spend"]["estimated"][0]["amount"] is None


def test_audit_job_dir_cache_spend_and_thresholds(tmp_path):
    directory = _job(tmp_path)
    (directory / "audio.json").write_text(json.dumps({
        "voice": {"provider": "elevenlabs", "cost": {"amount": "0.42", "currency": "EUR"}},
        "music": {"source": "scripts/animation/musique_test.py (générée)"}}))
    (directory / "worker.log").write_text("2026-11-12T10:00:00Z rendu démarré : 2 scène(s)\n"
                                          "2026-11-12T10:02:10Z bande son : voix\n"
                                          "2026-11-12T10:02:30Z rendu terminé : 20 s, 0 repli(s)\n")
    book = PriceBook(currency="EUR", rates={("voice", "elevenlabs"): Decimal("0.30")})
    report = ra.audit_render(directory, book=book)
    assert report["cache"]["reused"] == 1 and report["cache"]["render_seconds_saved"] == 40.0
    stages = {s["stage"]: s for s in report["stages"]}
    assert stages["remotion"]["wall_seconds"] == 130.0  # la scène réutilisée ne compte pas
    known = {k["component"]: k for k in report["spend"]["known"]}
    assert known["voice"]["amount"] == "0.42" and known["music"]["amount"] == "0"
    estimate = report["spend"]["estimated"][0]
    assert estimate["status"] == "priced"
    assert Decimal(estimate["amount"]) == (Decimal(estimate["characters"]) / 1000 * Decimal("0.30")).quantize(
        Decimal("0.0001"))
    checks = {c["check"]: c for c in report["checks"]}
    assert checks["scene_1_realtime_factor"]["status"] == "warn"   # 13 s de rendu par seconde
    assert checks["scene_2_duration_drift"]["status"] == "warn"    # 0,5 s d'écart
    assert "scene_2_realtime_factor" not in checks                 # réutilisée : pas de rendu à juger
    assert any("Seuil dépassé" in r for r in report["recommendations"])
    assert report["integrity"]["mp4_matches_manifest"] is True


def test_audit_detects_altered_mp4_and_reads_metrics(tmp_path):
    directory = _job(tmp_path)
    (directory / "out.mp4").write_bytes(b"retouche")
    recorder = ra.StageRecorder(directory / ra.METRICS_FILE)
    with recorder.stage("voice"):
        pass
    report = ra.audit_render(directory)
    assert report["integrity"]["mp4_matches_manifest"] is False
    assert any("ne correspond plus" in r for r in report["recommendations"])
    voice = next(s for s in report["stages"] if s["stage"] == "voice")
    assert voice["source"] == "mesuré (metrics.json)" and voice["max_rss_bytes"] > 0


def test_audit_requires_manifest(tmp_path):
    with pytest.raises(FileNotFoundError):
        ra.audit_render(tmp_path)


def test_html_is_escaped_and_self_contained(tmp_path):
    directory = _job(tmp_path)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["scenes"][0]["id"] = "<script>alert(1)</script>"
    (directory / "manifest.json").write_text(json.dumps(manifest))
    page = ra.to_html(ra.audit_render(directory, previous_storyboard=_storyboard()))
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "http" not in page  # aucune ressource externe
    assert "Invalidation" in page


# -- invalidation ---------------------------------------------------------------------------------------------
def test_screen_text_change_does_not_touch_voice():
    old = _storyboard()
    new = copy.deepcopy(old)
    _set_text(new, 1, "Isolant", "Isolant (diélectrique)")
    result = ra.invalidation(old, new)
    assert result["voice_changed"] is False
    assert [r["render_by_dependency"] for r in result["scenes"]] == [False, True, False]
    assert [r["render_current"] for r in result["scenes"]] == [False, True, False]
    assert result["voice_chars_current"] == result["voice_chars_by_scene"] == 0
    assert result["scenes"][1]["changes"] == ["screen_text"]


def test_one_line_of_dialogue_resynthesises_everything_today():
    old = _storyboard()
    new = copy.deepcopy(old)
    new["scenes"][2]["render"]["speech"]["cues"][0]["text"] += " Facile"
    _set_text(new, 1, "Isolant", "Isolant (diélectrique)")
    result = ra.invalidation(old, new)
    assert result["voice_changed"] is True
    assert result["render_current"] == 3 and result["render_by_dependency"] == 2
    assert result["voice_chars_by_scene"] == len(ra.spoken_text(new["scenes"][2]))
    assert result["voice_chars_current"] == ra.narration_chars(new)
    assert any("seul le texte d'écran change" in f for f in result["findings"])
    assert any("Scène 1" in f and "inutilement" in f for f in result["findings"])


def test_intent_only_change_is_a_useless_invalidation():
    old = _storyboard()
    new = copy.deepcopy(old)
    new["scenes"][0]["intent"]["purpose"] = "Accroche courte"
    result = ra.invalidation(old, new)
    assert result["scenes"][0]["changes"] == ["intent"]
    assert result["render_current"] == 1 and result["render_by_dependency"] == 0


def test_scenes_matched_by_id_and_new_scene():
    old = _storyboard()
    new = copy.deepcopy(old)
    new["scenes"] = [new["scenes"][1], new["scenes"][0], new["scenes"][2]]  # permutation
    extra = copy.deepcopy(new["scenes"][2])
    extra["id"] = "bonus"
    new["scenes"].append(extra)
    rows = ra.invalidation(old, new)["scenes"]
    assert [r["changes"] for r in rows[:3]] == [[], [], []]
    assert rows[3]["changes"] == ["new"] and rows[3]["voice_resynth_by_scene"] is True


# -- mesures --------------------------------------------------------------------------------------------------
def test_measure_records_wall_cpu_rss_and_exit_code(tmp_path):
    recorder = ra.StageRecorder(tmp_path / "m" / "metrics.json")
    code = ra.measure([sys.executable, "-c", "sum(range(10**5))"], recorder, "mix")
    assert code == 0
    data = json.loads((tmp_path / "m" / "metrics.json").read_text())
    entry = data["stages"]["mix"]
    assert entry["exit_code"] == 0 and entry["wall_seconds"] >= 0 and entry["max_rss_bytes"] > 0
    # un second enregistreur complète le même fichier
    with ra.StageRecorder(tmp_path / "m" / "metrics.json").stage("voice"):
        pass
    assert set(json.loads((tmp_path / "m" / "metrics.json").read_text())["stages"]) == {"mix", "voice"}


# -- disque et nettoyage --------------------------------------------------------------------------------------
_TEMP = ("scenes/01.render.mp4", "scenes/01.render.mp4.log", "scenes/02-still.png", "scenes/concat.txt",
         "scenes/01-input.json", "out.partial.mp4", ".state.json.123.tmp", ".audio.wav.tmp.wav")
_KEEP = ("out.mp4", "manifest.json", "job.json", "state.json", "scenes/01.mp4", "scenes/01.json",
         "assets/ast_1/base.png", "voice.wav", "audio.wav", "worker.log", "subtitles.srt")


def _fill(directory: Path) -> None:
    for name in _TEMP + _KEEP:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"0123456789")


def test_clean_plan_only_lists_regenerable_intermediates(tmp_path):
    directory = _job(tmp_path)
    _fill(directory)
    plan = ra.clean_plan(directory)
    assert plan["skip"] == ""
    assert sorted(plan["files"]) == sorted(_TEMP)
    assert plan["bytes"] == 10 * len(_TEMP)


@pytest.mark.parametrize("state", ["running", "queued", "interrupted", ""])
def test_clean_never_touches_unfinished_tasks(tmp_path, state):
    directory = _job(tmp_path, state=state)
    _fill(directory)
    plan = ra.clean_plan(directory)
    assert plan["files"] == [] and "non terminée" in plan["skip"]


def test_clean_skips_task_held_by_a_worker(tmp_path):
    directory = _job(tmp_path)
    _fill(directory)
    with worker.file_lock(directory / "job.lock", blocking=False) as held:
        assert held
        assert ra.clean_plan(directory)["skip"] == "un worker tient encore la tâche"


def test_clean_ignores_symlinks_out_of_task(tmp_path):
    directory = _job(tmp_path)
    outside = tmp_path / "precieux.mp4"
    outside.write_bytes(b"x")
    (directory / "scenes" / "03.render.mp4").symlink_to(outside)
    assert ra.clean_plan(directory)["files"] == []


def test_clean_is_dry_run_by_default_then_applies(tmp_path):
    directory = _job(tmp_path)
    _fill(directory)
    dry = ra.clean(tmp_path / "data")
    assert dry["applied"] is False and dry["bytes"] == 10 * len(_TEMP)
    assert all((directory / name).exists() for name in _TEMP)
    done = ra.clean(tmp_path / "data", apply=True)
    assert done["applied"] is True
    assert not any((directory / name).exists() for name in _TEMP)
    assert all((directory / name).exists() for name in _KEEP)
    assert ra.clean(tmp_path / "data")["bytes"] == 0


def test_disk_status_threshold(tmp_path):
    assert ra.disk_status(tmp_path, min_free=0)["status"] == "ok"
    assert ra.disk_status(tmp_path, min_free=1 << 62)["status"] == "warn"
    assert ra.disk_status(tmp_path / "absent")["status"] == "unknown"


# -- CLI ------------------------------------------------------------------------------------------------------
def _cli():
    spec = importlib.util.spec_from_file_location("audit_render_cli", ROOT / "scripts/animation/audit_render.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_report_writes_json_and_html(tmp_path, capsys):
    out = tmp_path / "audit"
    assert _cli().main(["report", str(DEMO_150), "--out", str(out)]) == 0
    data = json.loads((out / "audit.json").read_text(encoding="utf-8"))
    assert data["schema"] == ra.AUDIT_SCHEMA
    assert (out / "audit.html").read_text(encoding="utf-8").startswith("<!doctype html>")


def test_cli_diff_and_clean(tmp_path, capsys):
    old = tmp_path / "old.json"
    old.write_text(json.dumps(_storyboard()))
    assert _cli().main(["diff", str(old), str(old)]) == 0
    assert json.loads(capsys.readouterr().out)["render_current"] == 0
    assert _cli().main(["clean", str(tmp_path / "vide")]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == []
