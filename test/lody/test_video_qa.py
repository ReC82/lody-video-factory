"""Contrôle qualité automatique des MP4 Remotion (#166).

Aucun réseau, aucun fournisseur payant, aucune génération : le pilote Voltix #150 VERSIONNÉ
(``docs/animation/voltix-150``) et des vidéos synthétiques produites par ffmpeg (mire, noir, silence).
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lody.animation import video_qa as qa
from lody.animation import worker as w
from lody.generation.animation_connector import qa_warnings
from test.lody.test_animation_provider import FFMPEG, FakeRenderer, World, _submit, needs_ffmpeg

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "docs" / "animation" / "voltix-150"
CANVAS = {"fps": 30, "width": 1080, "height": 1920}
HERO, PROP = "ast_hero@1", "ast_prop@1"
INFO = {HERO: {"kind": "character", "width": 600, "height": 900, "anchor": {"x": 0.5, "y": 0.98}, "slug": "heros"},
        PROP: {"kind": "prop", "width": 400, "height": 200, "anchor": {"x": 0.5, "y": 0.5}, "slug": "fiche-bnc"}}


def _ids(findings: list[dict]) -> list[str]:
    return [f["id"] for f in findings]


def _asset(obj_id: str, ref: str, x: float, y: float, **extra) -> dict:
    asset_id, version = ref.split("@")
    return {"id": obj_id, "type": "asset", "asset": {"id": asset_id, "version": int(version)},
            "position": [{"frame": 0, "x": x, "y": y}], **extra}


def _text(obj_id: str, content: str, x: float, y: float, style: str = "label") -> dict:
    return {"id": obj_id, "type": "text", "text": {"content": content, "style": style},
            "position": [{"frame": 0, "x": x, "y": y}]}


def _storyboard(objects: list[dict], cues: list[dict] | None = None, speaker: str = "heros",
                frames: int = 90) -> dict:
    return {"schema": "lody.animation/1", "canvas": dict(CANVAS),
            "frozen": {"assets": [{"id": "ast_prop", "version": 1, "name": "Fiche BNC mâle"},
                                  {"id": "ast_hero", "version": 1, "name": "Héros — marionnette"}]},
            "scenes": [{"id": "s1", "duration_frames": frames, "intent": {"narration": "test"},
                        "render": {"layers": [{"id": "l", "objects": objects}],
                                   "speech": {"speaker": speaker, "cues": cues or []},
                                   "fallback": {"type": "still", "frame": 0}}}]}


# -- mise en page estimée ---------------------------------------------------------------------------------------
def test_well_placed_scene_has_no_layout_finding():
    board = _storyboard([_asset("prise", PROP, 540, 600), _text("etiquette", "Fiche", 540, 800),
                         _asset("heros", HERO, 540, 1450, scale=[{"frame": 0, "value": 0.6}])],
                        [{"text": "Voici la fiche BNC.", "start": 10, "end": 60}])
    assert qa.layout_checks(board, INFO) == []


def test_label_past_the_edge_is_reported_as_clipped_and_near_the_edge_as_unsafe():
    board = _storyboard([_text("coupee", "Tresse (blindage)", 1000, 600), _text("bord", "Isolant", 950, 900)])
    found = qa.layout_checks(board, INFO)
    clipped = [f for f in found if f["id"] == "text_clipped"]
    assert len(clipped) == 1 and "droit" in clipped[0]["message"] and clipped[0]["data"]["object"] == "coupee"
    assert [f["data"]["object"] for f in found if f["id"] == "text_unsafe"] == ["bord"]


def test_entering_object_is_not_reported_as_clipped_while_it_slides_in():
    slide = _asset("prise", PROP, 540, 600)
    slide["position"] = [{"frame": 0, "x": -400, "y": 600}, {"frame": 80, "x": 540, "y": 600}]
    assert "asset_clipped" not in _ids(qa.layout_checks(_storyboard([slide], frames=100), INFO))
    still = _asset("prise", PROP, 1050, 600)
    assert "asset_clipped" in _ids(qa.layout_checks(_storyboard([still]), INFO))


def test_schema_hidden_by_the_character_is_reported():
    board = _storyboard([_asset("prise", PROP, 540, 1100), _asset("heros", HERO, 540, 1500)])
    hidden = [f for f in qa.layout_checks(board, INFO) if f["id"] == "hidden_by_character"]
    assert hidden and hidden[0]["data"] == {"object": "prise", "character": "heros", "covered": 1.0}
    assert "Fiche BNC mâle" in hidden[0]["message"] and "Héros" in hidden[0]["message"]


def test_subtitle_on_the_feet_and_outside_the_safe_area():
    feet = _storyboard([_asset("heros", HERO, 540, 1600)], [{"text": "Bonjour", "start": 0, "end": 30}])
    assert "subtitle_on_character" in _ids(qa.layout_checks(feet, INFO))
    long_line = "Une réplique beaucoup trop longue qui tiendra sur trois lignes au moins en bas de l'image"
    board = _storyboard([_asset("heros", HERO, 540, 1100, scale=[{"frame": 0, "value": 0.5}])],
                        [{"text": long_line, "start": 0, "end": 80}])
    unsafe = [f for f in qa.layout_checks(board, INFO) if f["id"] == "subtitle_unsafe"]
    assert unsafe and unsafe[0]["data"]["lines"] >= 3


def test_object_cited_in_a_line_but_absent_from_the_image():
    late = _asset("prise", PROP, 540, 600, start=60)
    board = _storyboard([late, _asset("heros", HERO, 540, 1450, scale=[{"frame": 0, "value": 0.6}])],
                        [{"text": "Regarde la fiche BNC !", "start": 0, "end": 40},
                         {"text": "La voilà, la fiche BNC.", "start": 62, "end": 88}])
    missing = [f for f in qa.layout_checks(board, INFO) if f["id"] == "cited_object_missing"]
    assert len(missing) == 1 and missing[0]["data"]["words"] == ["bnc", "fiche"] and missing[0]["at"] < 1.5
    shown_as_text = _storyboard([_text("titre", "La fiche BNC", 540, 300, "title")],
                                [{"text": "Regarde la fiche BNC !", "start": 0, "end": 40}], speaker="")
    assert "cited_object_missing" not in _ids(qa.layout_checks(shown_as_text, INFO))


def test_speaker_offscreen_and_unknown_geometry():
    board = _storyboard([_asset("heros", HERO, 2400, 1450)], [{"text": "Bonjour", "start": 0, "end": 30}])
    assert "speaker_offscreen" in _ids(qa.layout_checks(board, INFO))
    unknown = qa.layout_checks(_storyboard([_asset("heros", HERO, 540, 1450)]), {})
    assert _ids(unknown) == ["geometry_unknown"]


def test_interpolation_matches_the_remotion_engine():
    keys = [{"frame": 0, "x": 0}, {"frame": 10, "x": 100}]
    assert qa.sample(keys, 5, "x", "linear") == 50
    assert qa.sample(keys, 5, "x", "ease_out") == pytest.approx(87.5)
    assert qa.sample(keys, -3, "x") == 0 and qa.sample(keys, 30, "x") == 100
    assert qa.ease("spring", 1) == 1 and qa.ease("spring", 0) == 0


# -- synchronisation --------------------------------------------------------------------------------------------
def test_srt_must_match_the_storyboard_cues():
    board = _storyboard([], [{"text": "Bonjour", "start": 15, "end": 45}])
    good = "1\n00:00:00,500 --> 00:00:01,500\nBonjour\n"
    assert qa.srt_checks(board, good) == []
    shifted = qa.srt_checks(board, good.replace("00:00:00,500", "00:00:00,900"))
    assert _ids(shifted) == ["srt_mismatch"] and shifted[0]["data"]["shift_ms"] == 400
    assert _ids(qa.srt_checks(board, "")) == ["srt_mismatch"]
    assert _ids(qa.srt_checks(board, None)) == ["srt_missing"]


def test_music_alone_too_long_between_lines_and_after_the_last_one():
    cues = [{"scene": 1, "text": "a", "start": 0.5, "end": 2.0}, {"scene": 1, "text": "b", "start": 7.0, "end": 8.0}]
    found = qa.music_checks(cues, 12.0, has_music=True)
    assert _ids(found) == ["music_gap", "music_tail"] and found[1]["data"]["seconds"] == 4.0
    assert qa.music_checks(cues, 12.0, has_music=False) == []
    assert qa.music_checks(cues, 9.0, has_music=True) == [f for f in found if f["id"] == "music_gap"]


def test_audio_gap_speech_missing_and_unsubtitled_voice():
    window = qa.AUDIO_WINDOW
    cues = [{"scene": 1, "text": "première", "start": 0.0, "end": 1.0},
            {"scene": 1, "text": "deuxième", "start": 3.0, "end": 4.0}]
    mix = [-20.0] * 20 + [-80.0] * 15 + [-20.0] * 25        # 1,5 s de silence complet à 2,0 s
    voice = [-20.0] * 10 + [-90.0] * 8 + [-25.0] * 12 + [-90.0] * 30   # parle de 1,8 à 3 s, muette pendant « deuxième »
    found = qa.audio_checks(mix, voice, cues, 60 * window)
    assert _ids(found) == ["audio_gap", "speech_missing", "speech_unsubtitled"]
    assert found[0]["at"] == 2.0 and found[1]["scene"] == 1 and "deuxième" in found[1]["message"]


def test_speech_is_estimated_from_the_mix_when_the_voice_file_is_absent():
    cues = [{"scene": 1, "text": "parlée", "start": 0.0, "end": 1.0},
            {"scene": 1, "text": "muette", "start": 2.0, "end": 3.0}]
    mix = [-18.0] * 10 + [-33.0] * 10 + [-33.0] * 10 + [-33.0] * 10   # musique seule pendant « muette »
    found = qa.audio_checks(mix, None, cues, 4.0)
    assert _ids(found) == ["speech_missing"] and "muette" in found[0]["message"]


# -- contrôles techniques ---------------------------------------------------------------------------------------
def test_technical_checks_on_format_frames_audio_and_manifest():
    board = _storyboard([], frames=90)
    ok = {"duration": 3.0, "video": {"codec": "h264", "width": 1080, "height": 1920, "fps": 30.0, "pix_fmt": "yuv420p",
                                     "frames": 89, "duration": 3.0},
          "audio": {"codec": "aac", "sample_rate": 48000, "channels": 2, "duration": 3.0}}
    assert qa.technical_checks(ok, board, {"sha256": "abc"}, "abc") == []
    wide = copy.deepcopy(ok)
    wide["video"].update(width=1920, height=1080, frames=60)
    wide["audio"] = None
    found = qa.technical_checks(wide, board, {"sha256": "abc", "fallback_scenes": [1]}, "def")
    assert _ids(found) == ["aspect", "resolution", "frames", "no_audio", "manifest_sha", "fallback"]
    assert {f["level"] for f in found if f["id"] in ("aspect", "no_audio")} == {"error"}


def test_qa_warning_is_only_shown_when_something_needs_a_look():
    assert qa_warnings({"qa": {"status": "done", "error": 0, "warning": 0, "info": 3}}) == ()
    assert qa_warnings({"qa": {"status": "unavailable"}}) == ()
    text = qa_warnings({"qa": {"status": "done", "error": 1, "warning": 2, "report": "qa/index.html"}})[0]
    assert "1 erreur(s), 2 point(s) à vérifier" in text and "qa/index.html" in text


# -- vidéos réelles (ffmpeg local) ------------------------------------------------------------------------------
@needs_ffmpeg
def test_versioned_voltix_pilot_report(tmp_path):
    """Pilote #150 versionné : aucune erreur technique, le sous-titre posé sur les pieds de Voltix est repéré."""
    report = qa.run(PILOT, tmp_path / "qa", ffmpeg=FFMPEG)
    assert report["schema"] == qa.QA_SCHEMA and report["summary"]["error"] == 0
    assert report["probe"]["video"]["width"] == 1080 and report["probe"]["video"]["height"] == 1920
    assert report["inputs"] == {"manifest": True, "subtitles": True, "voice": False, "music": False,
                                "asset_geometry": True}
    found = {(f["id"], f.get("scene")) for f in report["findings"]}
    assert ("subtitle_on_character", 1) in found
    assert not {i for i, _ in found} & {"black", "borders", "dark_frame", "empty_frame", "srt_mismatch", "audio_gap",
                                        "speech_missing", "asset_clipped", "manifest_sha", "duration"}
    page = (tmp_path / "qa" / "index.html").read_text(encoding="utf-8")
    assert len(re.findall(r'src="data:image/jpeg;base64,', page)) == 9      # 3 scènes × début/milieu/fin
    assert "voltix-150.mp4" in page and "Clic ! C&#x27;est verrouillé." in page
    assert "ne garantit <b>pas</b> la justesse pédagogique" in page
    assert json.loads((tmp_path / "qa" / "qa.json").read_text(encoding="utf-8"))["findings"] == report["findings"]
    assert not (PILOT / "qa").exists()  # le dossier versionné n'est pas modifié


def _video(path: Path, source: str, seconds: float, audio: str | None) -> Path:
    separator = ":" if "=" in source else "="
    command = [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
               f"{source}{separator}size=270x480:rate=30:duration={seconds}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"{audio}:duration={seconds}", "-c:a", "aac", "-shortest"]
    subprocess.run([*command, "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


@needs_ffmpeg
def test_black_silent_video_is_reported(tmp_path):
    folder = tmp_path / "rendu"
    folder.mkdir()
    _video(folder / "noir.mp4", "color=c=black", 3, "anullsrc=r=48000:cl=stereo")
    board = _storyboard([], [{"text": "Bonjour", "start": 15, "end": 45}])
    board["canvas"].update(width=270, height=480)
    (folder / "storyboard.json").write_text(json.dumps(board), encoding="utf-8")
    report = qa.run(folder, ffmpeg=FFMPEG)
    ids = _ids(report["findings"])
    assert {"black", "dark_frame", "audio_gap", "srt_missing"} <= set(ids)
    assert report["summary"]["error"] >= 1 and (folder / "qa" / "index.html").is_file()


@needs_ffmpeg
def test_probe_without_ffprobe_reads_the_ffmpeg_header(tmp_path):
    video = _video(tmp_path / "mire.mp4", "testsrc2", 1, "sine=frequency=440")
    info = qa.probe(FFMPEG, None, video)
    assert info["video"]["width"] == 270 and info["video"]["height"] == 480 and info["video"]["fps"] == 30
    assert info["audio"]["codec"] == "aac" and abs(info["duration"] - 1.0) < 0.1
    pilot = qa.probe(FFMPEG, None, PILOT / "voltix-150.mp4")  # « yuv420p(tv, …, progressive) »
    assert pilot["video"] == {"codec": "h264", "pix_fmt": "yuv420p", "width": 1080, "height": 1920, "fps": 30.0,
                              "frames": None, "duration": None}
    assert qa.technical_checks(pilot, json.loads((PILOT / "storyboard.json").read_text(encoding="utf-8")), None,
                               None) == []


@needs_ffmpeg
def test_cli_writes_the_report_and_returns_an_error_code(tmp_path):
    done = subprocess.run([sys.executable, str(ROOT / "scripts/animation/qa_video.py"), str(PILOT), "--out",
                           str(tmp_path / "rapport"), "--ffmpeg", FFMPEG], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    assert "recouvre le bas de Voltix" in done.stdout and (tmp_path / "rapport" / "index.html").is_file()


@needs_ffmpeg
def test_worker_runs_the_qa_after_each_render_and_the_project_can_download_it(tmp_path):
    world = World(tmp_path)
    project = world.project("Contrôle")
    task, directory = _submit(world, project, world.storyboard(project.id))
    assert w.run_job(directory, FakeRenderer(), ffmpeg=FFMPEG)["state"] == "done"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["qa"]["status"] == "done" and manifest["qa"]["report"] == "qa/index.html"
    assert manifest["qa"]["error"] >= 1  # la mire du faux moteur est muette : « aucune piste audio »
    report = json.loads((directory / "qa" / "qa.json").read_text(encoding="utf-8"))
    assert "no_audio" in _ids(report["findings"])
    snapshot = world.connector.poll(task)
    ref = next(a["ref"] for a in snapshot.result.assets if a["kind"] == "qa_report")
    assert world.connector.resolve_asset(task, ref, suffixes=(".html",)).is_file()
    assert any("Contrôle qualité automatique" in warning for warning in snapshot.warnings)
    log = (directory / "worker.log").read_text(encoding="utf-8")
    assert "contrôle qualité" in log


@needs_ffmpeg
def test_a_failing_qa_never_fails_the_render(tmp_path, monkeypatch):
    world = World(tmp_path)
    project = world.project("Contrôle en échec")
    _, directory = _submit(world, project, world.storyboard(project.id))

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(qa, "run", broken)
    assert w.run_job(directory, FakeRenderer(), ffmpeg=FFMPEG)["state"] == "done"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["qa"] == {"schema": qa.QA_SCHEMA, "status": "unavailable"}


@needs_ffmpeg
def test_production_exposes_the_qa_report_of_a_finished_animated_render(tmp_path):
    from lody.generation.animation_connector import AnimationConnector
    from lody.generation.models import ProductionStatus
    from lody.generation.service import ProductionService
    from lody.generation.store import ProductionRepository
    from test.lody.fakes import SyncExecutor
    from test.lody.test_generation_service import FULL_BOOK, SUBJECT

    world = World(tmp_path)
    project = world.project("Production contrôlée")
    renderer = FakeRenderer()
    connector = AnimationConnector(root=world.data, asset_repo=world.assets, renderer=f"{sys.executable} -c pass",
                                   ffmpeg=FFMPEG, launcher=lambda d: w.run_job(d, renderer, ffmpeg=FFMPEG))
    productions = ProductionService(ProductionRepository(world.db), {"animation_2d": connector}, SyncExecutor(),
                                    price_book=lambda: FULL_BOOK, animation_asset_repo=world.assets)
    raw = world.storyboard(project.id, "q")
    raw.pop("frozen")
    draft = productions.prepare(project, SUBJECT, provider_id="animation_2d", animation=raw)
    finished = productions.refresh(productions.confirm(draft.id).id)
    assert finished.status is ProductionStatus.TERMINEE
    report = next(a for a in finished.assets if a["kind"] == "qa_report")
    path = productions.resolve_report(finished, report["ref"])
    assert path.name == "index.html" and "Contrôle qualité automatique" in path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        productions.resolve_report(finished, finished.video_ref)  # seuls les rapports HTML passent par ici
    assert any(warning.startswith("Contrôle qualité automatique") for warning in finished.warnings)
