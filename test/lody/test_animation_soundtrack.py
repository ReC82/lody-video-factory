"""Bande son et entrée du moteur de scène du rendu 2D (#150).

Aucun réseau, aucun fournisseur payant : voix et musique sont des sinus générés par ffmpeg ; le moteur de scène
est le faux de ``test_animation_provider`` (mire ffmpeg). L'adaptateur Remotion réel est testé côté Node
(``renderers/remotion/test``) et par la démonstration ``scripts/animation/demo_voltix_150.py``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from lody.animation import audio
from lody.animation import worker as w
from test.lody.test_animation_provider import FFMPEG, FPS, FakeRenderer, World, _submit, needs_ffmpeg


def _tone(path: Path, seconds: float, frequency: int, volume: float = 0.5) -> Path:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={frequency}:sample_rate=22050:duration={seconds}", "-af", f"volume={volume}",
                    "-c:a", "pcm_s16le", str(path)], check=True, capture_output=True, timeout=60)
    return path


def _duration(path: Path) -> float:
    return w.media_duration(FFMPEG, path) or 0.0


# -- règles de mixage -------------------------------------------------------------------------------------------
def test_music_sits_twenty_percent_below_the_voice():
    assert audio.music_gain_db(-20.0, -10.0) == pytest.approx(-23.98, abs=0.01)  # -20 - 13.98 - (-10)
    assert audio.music_gain_db(None, -10.0) == -10.0  # musique seule : fond à -20 dB
    assert audio.music_gain_db(-20.0, None) == 0.0


def test_mix_command_ducks_the_music_under_the_voice_and_fades_out_at_the_exact_end(tmp_path):
    command = audio.mix_command("ffmpeg", 12.5, tmp_path / "v.wav", tmp_path / "m.wav", tmp_path / "o.wav", -14)
    graph = command[command.index("-filter_complex") + 1]
    assert "sidechaincompress" in graph and "volume=-14dB" in graph
    assert "afade=t=out:st=11.000:d=1.5" in graph and command[command.index("-t") + 1] == "12.500"
    assert "-stream_loop" in command  # musique trop courte : bouclée
    with pytest.raises(audio.SoundtrackError):
        audio.mix_command("ffmpeg", 1, None, None, tmp_path / "o.wav")


@needs_ffmpeg
def test_soundtrack_has_the_exact_video_duration_and_quiet_music(tmp_path):
    _tone(tmp_path / audio.VOICE_FILE, 2.0, 440)
    _tone(tmp_path / audio.MUSIC_FILE, 1.0, 220)  # plus courte que la vidéo : bouclée
    summary = audio.prepare_soundtrack(FFMPEG, tmp_path, 3.2)
    assert summary and "ducking" in summary
    mix = tmp_path / audio.MIX_FILE
    assert _duration(mix) == pytest.approx(3.2, abs=0.05)
    # Après la voix, il ne reste que la musique, nettement plus basse que la voix.
    voice_db = audio.mean_volume(FFMPEG, tmp_path / audio.VOICE_FILE)
    tail = tmp_path / "tail.wav"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", "2.2", "-t", "0.4", "-i", str(mix), str(tail)],
                   check=True, capture_output=True, timeout=60)
    assert audio.mean_volume(FFMPEG, tail) < voice_db - 10


@needs_ffmpeg
def test_no_voice_and_no_music_keeps_a_provided_mix(tmp_path):
    assert audio.prepare_soundtrack(FFMPEG, tmp_path, 2.0) is None
    assert audio.describe(tmp_path) == {"voice": None, "music": None, "music_level": None, "mixed": False}


def test_describe_reports_declared_provenance_and_defaults_to_provisional(tmp_path):
    (tmp_path / audio.VOICE_FILE).write_bytes(b"x")
    (tmp_path / audio.MUSIC_FILE).write_bytes(b"x")
    (tmp_path / audio.INFO_FILE).write_text(json.dumps({"voice": {"source": "Piper", "provisional": True,
                                                                  "secret": "ignoré"}}), encoding="utf-8")
    info = audio.describe(tmp_path)
    assert info["voice"] == {"provisional": True, "source": "Piper"}
    assert info["music"] == {"provisional": True} and info["music_level"] == audio.MUSIC_LEVEL


# -- voix de la fiche personnage --------------------------------------------------------------------------------
def test_voice_plan_reuses_the_character_voice_and_never_invents_one():
    chosen = SimpleNamespace(name="Voltix", voice_provider="elevenlabs", external_voice_id="voix-fiche",
                             voice_name="Voix choisie")
    plan = audio.voice_plan(chosen)
    assert plan["status"] == "character_voice" and plan["voice_id"] == "voix-fiche" and plan["needs_cost_approval"]
    missing = audio.voice_plan(SimpleNamespace(name="Voltix", voice_provider="", external_voice_id=""))
    assert missing["status"] == "provisional" and missing["voice_id"] == "" and "provisoire" in missing["message"]
    assert audio.voice_plan(SimpleNamespace(name="X", voice_provider="openai", external_voice_id="v"))["status"] == \
        "provisional"


# -- worker : entrée de scène et assemblage avec la bande son ---------------------------------------------------
@needs_ffmpeg
def test_worker_gives_the_engine_asset_info_and_the_voice_offset_then_mixes_the_soundtrack(tmp_path):
    world = World(tmp_path)
    project = world.project("Bande son")
    task, directory = _submit(world, project, world.storyboard(project.id))
    job = json.loads((directory / "job.json").read_text(encoding="utf-8"))
    info = next(iter(job["asset_info"].values()))
    assert {"kind", "width", "height", "anchor", "slug"} <= set(info)
    _tone(directory / audio.VOICE_FILE, 1.5, 440)
    _tone(directory / audio.MUSIC_FILE, 0.5, 220)
    state = w.run_job(directory, FakeRenderer(), ffmpeg=FFMPEG)
    assert state["state"] == "done"
    second = json.loads((directory / "scenes" / "02-input.json").read_text(encoding="utf-8"))
    assert second["voice"] == {"path": str(directory / audio.VOICE_FILE), "offset_frames": FPS}
    assert set(second["asset_info"]) == set(second["assets"])
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio"]["mixed"] and manifest["audio"]["voice"]["provisional"]
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", str(directory / "out.mp4")], capture_output=True,
                           text=True, timeout=60).stderr
    assert "Audio: aac" in probe
    assert _duration(directory / "out.mp4") == pytest.approx(manifest["expected_seconds"], abs=0.1)


@needs_ffmpeg
def test_a_changed_voice_re_renders_the_scenes(tmp_path):
    world = World(tmp_path)
    project = world.project("Voix changée")
    _task, directory = _submit(world, project, world.storyboard(project.id))
    renderer = FakeRenderer()
    assert w.run_job(directory, renderer, ffmpeg=FFMPEG)["state"] == "done"
    assert renderer.rendered == [1, 2]
    for name in ("state.json", "out.mp4", "manifest.json"):
        (directory / name).unlink()
    assert w.run_job(directory, renderer, ffmpeg=FFMPEG)["state"] == "done"
    assert renderer.rendered == [1, 2], "même scène, même voix : rien n'est refait"
    for name in ("state.json", "out.mp4", "manifest.json"):
        (directory / name).unlink()
    _tone(directory / audio.VOICE_FILE, 1.0, 330)
    assert w.run_job(directory, renderer, ffmpeg=FFMPEG)["state"] == "done"
    assert renderer.rendered == [1, 2, 1, 2], "nouvelle voix : la bouche doit être recalculée"
