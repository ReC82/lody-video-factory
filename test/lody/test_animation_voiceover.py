"""Voix off ElevenLabs du rendu 2D (#164) : fiche personnage → texte oral → synthèse en cache → piste calée.

Aucun réseau, aucun appel payant, aucun vrai config.toml : le « synthétiseur » est un faux qui fabrique un WAV
local (silence de tête + son + silence de queue) avec ffmpeg et compte ses appels.
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from lody.animation import audio, voiceover
from lody.animation import worker as w
from lody.characters import CharacterRepository
from lody.generation.costing import estimate_cost
from lody.generation.models import Capability, CapabilityState, ErrorKind, ProviderError
from lody.generation.service import request_of
from test.lody.test_animation_provider import FFMPEG, FPS, FakeRenderer, World
from test.lody.test_generation_service import FULL_BOOK

pytestmark = pytest.mark.skipif(not FFMPEG, reason="ffmpeg indisponible")
VOICE_ID = "fakeVoiceId0001"


def _wav_bytes(tmp: Path, seconds: float, lead: float = 0.1, tail: float = 0.1) -> bytes:
    target = tmp / f"fake-{seconds}-{lead}-{tail}.wav"
    tone = max(0.05, seconds - lead - tail)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                    "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=22050", "-filter_complex",
                    f"[0:a]atrim=0:{lead}[a];[1:a]atrim=0:{tone},volume=0.6[b];[0:a]atrim=0:{tail}[c];"
                    "[a][b][c]concat=n=3:v=0:a=1[out]", "-map", "[out]", "-c:a", "pcm_s16le", str(target)],
                   check=True, capture_output=True, timeout=60)
    return target.read_bytes()


class FakeSynth:
    """Faux ElevenLabs : même signature que ``generate_voice_preview`` (texte, voice_id, réglages)."""

    def __init__(self, tmp: Path, seconds: float = 0.6, fail_on: int | None = None, garbage: bool = False):
        self.tmp, self.seconds, self.fail_on, self.garbage = tmp, seconds, fail_on, garbage
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, text: str, voice_id: str, settings: dict) -> bytes:
        self.calls.append((text, voice_id, dict(settings)))
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise ProviderError(ErrorKind.PROVIDER, "quota ElevenLabs dépassé (simulé)")
        if self.garbage:
            return b"pas un audio"
        return _wav_bytes(self.tmp, self.seconds)


class VoicedWorld(World):
    def __init__(self, tmp_path: Path, synth: FakeSynth):
        super().__init__(tmp_path)
        self.characters = CharacterRepository(self.db)
        self.synth = synth
        self.connector._character_repo = self.characters
        self.connector._synthesize = synth

    def voiced_project(self, voice: bool = True, tag: str = "v"):
        project = self.project(f"Voltix {tag}")
        storyboard = self.storyboard(project.id, tag)
        hero_id = storyboard["scenes"][0]["render"]["layers"][1]["objects"][0]["asset"]["id"]
        fields = {"voice_provider": "elevenlabs", "voice_name": "Voix test", "external_voice_id": VOICE_ID} if voice else {}
        character = self.characters.create(project.id, name="Voltix", **fields)
        if voice:
            self.characters.set_voice_direction(project.id, character.id, {"stability": 0.4, "speed": 1.05})
        self.assets.update(project.id, hero_id, character_id=character.id)
        return project, storyboard


@pytest.fixture
def synth(tmp_path):
    return FakeSynth(tmp_path)


@pytest.fixture
def vworld(tmp_path, synth):
    return VoicedWorld(tmp_path, synth)


# -- logique pure ------------------------------------------------------------------------------------------------
def test_voice_spec_reads_the_character_card_and_never_invents_a_voice():
    card = SimpleNamespace(name="Voltix", voice_provider="ElevenLabs", external_voice_id=" abc ", voice_name="V",
                           voice_direction={"stability": 0.3, "inconnu": 1})
    spec = voiceover.voice_spec(card)
    assert spec.voice_id == "abc" and spec.settings_dict == {"model_id": voiceover.DEFAULT_MODEL, "stability": 0.3}
    assert voiceover.voice_spec(SimpleNamespace(name="x", voice_provider="piper", external_voice_id="abc")) is None
    assert voiceover.voice_spec(SimpleNamespace(name="x", voice_provider="elevenlabs", external_voice_id="")) is None


def test_fingerprint_is_the_voice_casting_formula():
    import hashlib

    payload = {"v": 1, "provider": "elevenlabs", "voice_id": "v", "settings": {"a": 1, "b": 2}, "text": "t"}
    expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
    assert voiceover.fingerprint("v", {"b": 2, "a": 1}, "t") == expected
    assert voiceover.fingerprint("v", {"a": 1, "b": 2}, "t2") != expected


def test_render_text_is_unchanged_without_lexicon_module(monkeypatch):
    lexicon = {"entries": [{"written": "SDI", "spoken": "èss-dé-i"}]}
    assert voiceover.render_text("  Le  SDI ", None) == ("Le SDI", "Le SDI")
    monkeypatch.setitem(sys.modules, "lody.pronunciation", None)  # PR #163 absente : import impossible
    assert voiceover.render_text("Le SDI", lexicon) == ("Le SDI", "Le SDI")


def _fake_lexicon_module(monkeypatch):
    module = types.ModuleType("lody.pronunciation")

    def render_snapshot(text, frozen):
        spoken = text
        for entry in frozen["entries"]:
            spoken = spoken.replace(entry["written"], entry["spoken"])
        return SimpleNamespace(spoken=spoken, subtitle=text, applied=())

    module.render_snapshot = render_snapshot
    monkeypatch.setitem(sys.modules, "lody.pronunciation", module)


def test_parse_speech_span_trims_head_and_tail_silences():
    log = ("silence_start: 0\nsilence_end: 0.25 | silence_duration: 0.25\n"
           "silence_start: 0.9\nsilence_end: 1.0 | silence_duration: 0.1\n"
           "silence_start: 1.6\n")
    assert voiceover.parse_speech_span(log, 2.0) == (0.25, 1.6)
    assert voiceover.parse_speech_span("", 1.5) == (0.0, 1.5)
    assert voiceover.parse_speech_span("silence_start: 0\nsilence_end: 1.5\n", 1.5) == (0.0, 1.5)  # tout silence


def test_time_cues_follow_the_real_speech_and_stay_inside_the_scene():
    spec = voiceover.VoiceSpec("v", (), "Voltix")
    scene = voiceover.SceneVoice(1, "s", spec, (voiceover.CueText("Le SDI", "Le èss-dé-i"),
                                                voiceover.CueText("ok", "ok")), 48, 2)
    cues = voiceover.time_cues(scene, 24, 0.5, (0.1, 1.1))
    assert cues[0]["start"] == 14 and cues[-1]["end"] <= 48
    assert all(c["end"] > c["start"] for c in cues) and cues[0]["end"] <= cues[1]["start"]
    assert cues[0]["text"] == "Le SDI" and cues[0]["spoken"] == "Le èss-dé-i"
    assert cues[0]["end"] - cues[0]["start"] > cues[1]["end"] - cues[1]["start"]  # plus long à dire


# -- préflight et coût -------------------------------------------------------------------------------------------
def test_preflight_and_cost_announce_the_character_voice(vworld):
    project, storyboard = vworld.voiced_project()
    request = vworld.request(project, storyboard)
    voice = vworld.connector.preflight(request).get(Capability.VOICE)
    assert voice.state is CapabilityState.READY and "Voltix" in voice.configured and "ElevenLabs" in voice.configured
    units = vworld.connector.plan_units(request)
    spoken = sum(len(c["text"]) for s in storyboard["scenes"] for c in s["render"]["speech"]["cues"])
    assert units.characters == (spoken, spoken) and units.voice_provider == "elevenlabs"
    line = next(line for line in estimate_cost(request, units, FULL_BOOK).lines if line.component == "voice")
    assert line.provider == "elevenlabs" and line.quantity == (spoken, spoken)
    assert vworld.synth.calls == []  # préflight et estimation : aucun appel payant


def test_character_without_voice_blocks_instead_of_a_substitute_voice(vworld):
    project, storyboard = vworld.voiced_project(voice=False)
    request = vworld.request(project, storyboard)
    report = vworld.connector.preflight(request)
    voice = report.get(Capability.VOICE)
    assert voice.state is CapabilityState.NOT_CONFIGURED and voice.fix == "project" and "Voltix" in voice.message
    assert not report.ready
    with pytest.raises(ProviderError) as error:
        vworld.connector.submit(request, "prd_000000000001-run")
    assert error.value.stage == "voice" and vworld.synth.calls == []
    assert not (vworld.data / "animation" / project.id).exists() or not any(
        (vworld.data / "animation" / project.id).glob("anim-*"))


def test_unlinked_speaker_keeps_the_announced_silent_behaviour(vworld):
    project = vworld.project("Sans fiche")
    storyboard = vworld.storyboard(project.id)
    request = vworld.request(project, storyboard)
    assert vworld.connector.preflight(request).get(Capability.VOICE).state is CapabilityState.DISABLED
    assert vworld.connector.plan_units(request).characters == (0, 0)


# -- chaîne complète ---------------------------------------------------------------------------------------------
def test_submit_synthesizes_once_per_scene_and_builds_a_timed_track(vworld, synth):
    project, storyboard = vworld.voiced_project()
    task = vworld.connector.submit(vworld.request(project, storyboard), "prd_000000000001-run")
    directory = w.find_job_dir(vworld.data, task.task_id)
    assert len(synth.calls) == 2 and {call[1] for call in synth.calls} == {VOICE_ID}
    assert synth.calls[0][2] == {"model_id": voiceover.DEFAULT_MODEL, "speed": 1.05, "stability": 0.4}
    with wave.open(str(directory / audio.VOICE_FILE)) as handle:
        seconds = handle.getnframes() / handle.getframerate()
    assert abs(seconds - 2 * FPS / FPS) < 0.01  # durée exacte de la vidéo
    timing = voiceover.read_timing(directory)
    assert set(timing) == {"1", "2"}
    first = timing["1"]
    assert first["character"] == "Voltix" and first["voice_id"] == VOICE_ID and first["audio_seconds"] == pytest.approx(0.6, abs=0.02)
    cue = first["cues"][0]
    assert 0 <= cue["start"] < cue["end"] <= FPS and cue["text"] == storyboard["scenes"][0]["render"]["speech"]["cues"][0]["text"]
    info = json.loads((directory / audio.INFO_FILE).read_text(encoding="utf-8"))
    assert info["voice"]["source"] == "ElevenLabs" and info["voice"]["provisional"] is False
    assert "voice-scenes" not in {p.name for p in directory.iterdir()}

    # Reprise / second rendu : tout est en cache, aucun nouvel appel, coût 0.
    request = vworld.request(project, storyboard)
    assert vworld.connector.plan_units(request).characters == (0, 0)
    vworld.connector.submit(request, "prd_000000000002-run")
    assert len(synth.calls) == 2


def test_worker_uses_timed_cues_for_mouth_and_subtitles(vworld):
    project, storyboard = vworld.voiced_project()
    task = vworld.connector.submit(vworld.request(project, storyboard), "prd_000000000001-run")
    directory = w.find_job_dir(vworld.data, task.task_id)
    state = w.run_job(directory, FakeRenderer(), ffmpeg=FFMPEG)
    assert state["state"] == "done", state
    timing = voiceover.read_timing(directory)
    scene_input = json.loads((directory / "scenes" / "01-input.json").read_text(encoding="utf-8"))
    assert scene_input["scene"]["render"]["speech"]["cues"] == timing["1"]["cues"]
    assert scene_input["scene"]["render"]["speech"]["speaker"] == "heros"
    assert scene_input["voice"]["offset_frames"] == 0
    srt = (directory / "subtitles.srt").read_text(encoding="utf-8")
    start_ms = round(timing["2"]["cues"][0]["start"] * 1000 / FPS) + 1000
    assert f"00:00:0{start_ms // 1000},{start_ms % 1000:03d}" in srt
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["audio"]["voice"]["source"] == "ElevenLabs" and manifest["audio"]["voice"]["provisional"] is False


def test_api_error_fails_clearly_and_keeps_what_was_paid(tmp_path):
    synth = FakeSynth(tmp_path, fail_on=2)
    vworld = VoicedWorld(tmp_path, synth)
    project, storyboard = vworld.voiced_project()
    with pytest.raises(ProviderError) as error:
        vworld.connector.submit(vworld.request(project, storyboard), "prd_000000000001-run")
    assert error.value.stage == "voice" and "scène 2" in error.value.message and "quota" in error.value.message
    assert w.find_job_dir(vworld.data, vworld.connector.task_id_for("prd_000000000001-run")) is None
    assert not list((vworld.data / "animation" / project.id).glob(".*staging"))
    # Nouvel essai : seule la scène 2 est payée (la 1 est en cache).
    synth.fail_on = None
    vworld.connector.submit(vworld.request(project, storyboard), "prd_000000000001-run")
    assert len(synth.calls) == 3 and synth.calls[2][0] == synth.calls[1][0]


def test_unreadable_audio_is_never_cached(tmp_path):
    synth = FakeSynth(tmp_path, garbage=True)
    vworld = VoicedWorld(tmp_path, synth)
    project, storyboard = vworld.voiced_project()
    with pytest.raises(ProviderError) as error:
        vworld.connector.submit(vworld.request(project, storyboard), "prd_000000000001-run")
    assert "illisible" in error.value.message
    plan = vworld.connector.voice_plan(vworld.request(project, storyboard))
    assert not any(vworld.connector.voice_cache().has(scene.key) for scene in plan.scenes)


def test_voice_longer_than_its_scene_is_refused_not_truncated(tmp_path):
    synth = FakeSynth(tmp_path, seconds=1.6)
    vworld = VoicedWorld(tmp_path, synth)
    project, storyboard = vworld.voiced_project()
    with pytest.raises(ProviderError) as error:
        vworld.connector.submit(vworld.request(project, storyboard), "prd_000000000001-run")
    assert "dure 1.6" in error.value.message and "scène 1" in error.value.message


def test_pronunciation_lexicon_changes_the_voice_text_not_the_subtitles(vworld, synth, monkeypatch):
    _fake_lexicon_module(monkeypatch)
    project, storyboard = vworld.voiced_project()
    written = storyboard["scenes"][0]["render"]["speech"]["cues"][0]["text"]  # « Bonjour »
    request = vworld.request(project, storyboard)
    request = request.with_updates(animation={**request.animation, "pronunciation": {
        "entries": [{"written": written, "spoken": "Bon-djour"}], "pending": []}})
    task = vworld.connector.submit(request, "prd_000000000001-run")
    assert synth.calls[0][0] == "Bon-djour"
    cue = voiceover.read_timing(w.find_job_dir(vworld.data, task.task_id))["1"]["cues"][0]
    assert cue["text"] == written and cue["spoken"] == "Bon-djour"


def test_request_of_passes_the_frozen_lexicon_to_the_2d_provider():
    lexicon = {"entries": [{"written": "SDI", "spoken": "èss-dé-i"}], "pending": []}
    production = SimpleNamespace(params={"request": {"subject": "Le câble SDI"}}, animation={"schema": "x"}, project_id="prj_1",
                                 snapshot={"pronunciation": lexicon})
    assert request_of(production).animation["pronunciation"] == lexicon
    production.snapshot = {}
    assert "pronunciation" not in request_of(production).animation


def test_too_long_scene_text_is_refused_before_any_paid_call(tmp_path):
    synth = FakeSynth(tmp_path)
    spec = voiceover.VoiceSpec("v", (), "Voltix")
    plan = voiceover.VoicePlan(scenes=[
        voiceover.SceneVoice(1, "a", spec, (voiceover.CueText("ok", "ok"),), 24, 0),
        voiceover.SceneVoice(2, "b", spec, (voiceover.CueText("x" * 2001, "x" * 2001),), 24, 0)])
    with pytest.raises(voiceover.VoiceoverError) as error:
        voiceover.synthesize_missing(plan, voiceover.VoiceCache(tmp_path / "cache"), synth, FFMPEG)
    assert error.value.scene == 2 and synth.calls == []
