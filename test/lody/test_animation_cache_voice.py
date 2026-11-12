"""Contrat de cache voix ↔ scène (#185) : une scène réutilisée n'a jamais la bouche calée sur l'ancienne voix.

Prépare la réconciliation de la PR #169 (voix ElevenLabs, repères ``voiceover.timed_cues`` recalés) avec le cache
de scène de #157 (``scene_cache.scene_dependencies``). Tests purs (aucun moteur, aucun réseau, aucun fournisseur
payant) ; les tests d'intégration du worker ne s'activent qu'une fois ``lody.animation.voiceover`` (#169) présent.
"""

from __future__ import annotations

import copy
import json
import wave
from pathlib import Path

import pytest

from lody.animation import scene_cache

FPS = 30
SCENE_FRAMES = 60  # 2 s par scène


def _voice(path: Path, windows: dict[int, int], scenes: int = 3, rate: int = 16000) -> Path:
    """WAV PCM 16 bits mono de ``scenes`` scènes ; ``windows[i]`` = amplitude de la voix de la scène ``i``."""
    frames = bytearray()
    per_scene = SCENE_FRAMES * rate // FPS
    for index in range(1, scenes + 1):
        value = windows.get(index, 1000)
        for i in range(per_scene):
            frames += int(value if i % 40 < 20 else -value).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(rate)
        sink.writeframes(bytes(frames))
    return path


def _scene(scene_id: str, speaker: str, text: str, *, x: int = 540) -> dict:
    hero = {"id": speaker, "type": "asset", "asset": {"id": "ast_000000000185", "version": 1}, "lipsync": True,
            "position": [{"frame": 0, "x": x, "y": 1400}]}
    return {"id": scene_id, "duration_frames": SCENE_FRAMES, "intent": {},
            "render": {"layers": [{"id": "l", "objects": [hero]}], "fallback": {"type": "still", "frame": 0},
                       "speech": {"speaker": speaker, "cues": [{"text": text, "start": 5, "end": 50}]}}}


def _job() -> dict:
    """Trois scènes, deux locuteurs sur des scènes distinctes (Voltix : 1 et 2, Ampère : 3)."""
    storyboard = {"schema": "lody.animation/1", "canvas": {"fps": FPS, "width": 1080, "height": 1920}, "scenes": [
        _scene("accueil", "voltix", "Salut, moi c'est Voltix !"),
        _scene("cable", "voltix", "Voici le câble SDI."),
        _scene("bnc", "ampere", "Et la fiche BNC."),
    ]}
    return {"storyboard": storyboard, "assets": {}, "asset_info": {}}


def _timed(text: str, start: int = 8, end: int = 47, **extra) -> list[dict]:
    """Repères recalés tels que ``voiceover.timed_cues`` (#169) les rend : start/end/text/spoken."""
    return [{"start": start, "end": end, "text": text, "spoken": text, **extra}]


def _keys(job: dict, voice: Path | None, cues: dict[int, list[dict] | None]) -> list[str]:
    return [scene_cache.dependency_key(scene_cache.scene_dependencies(
        job, index, scene, voice, "moteur@1", speech_cues=cues.get(index)))
        for index, scene in enumerate(job["storyboard"]["scenes"], 1)]


def _cues(job: dict) -> dict[int, list[dict]]:
    return {i: _timed(s["render"]["speech"]["cues"][0]["text"]) for i, s in enumerate(job["storyboard"]["scenes"], 1)}


# -- rétrocompatibilité : V1 et caches existants -----------------------------------------------------------------
def test_without_timed_cues_the_key_is_exactly_the_one_before_185(tmp_path):
    job, voice = _job(), _voice(tmp_path / "voice.wav", {})
    scene = job["storyboard"]["scenes"][0]
    legacy = scene_cache.scene_dependencies(job, 1, scene, voice, "moteur@1")
    assert "speech_timing" not in legacy  # aucune clé déjà en cache n'est invalidée par #185
    for absent in (None, []):
        assert scene_cache.scene_dependencies(job, 1, scene, voice, "moteur@1", speech_cues=absent) == legacy
    assert "speech_timing" in scene_cache.scene_dependencies(job, 1, scene, voice, "moteur@1",
                                                             speech_cues=_timed("Salut"))


def test_invalid_timed_cues_are_refused_rather_than_silently_ignored(tmp_path):
    job = _job()
    with pytest.raises(ValueError):
        scene_cache.scene_dependencies(job, 1, job["storyboard"]["scenes"][0], None, "m", speech_cues=["texte"])


# -- scénarios du ticket #185 -------------------------------------------------------------------------------------
def test_unchanged_voice_and_cues_hit_the_cache(tmp_path):
    job = _job()
    first = _keys(job, _voice(tmp_path / "a.wav", {}), _cues(job))
    again = _keys(copy.deepcopy(job), _voice(tmp_path / "b.wav", {}), _cues(job))  # même voix, autre tâche
    assert first == again


def test_a_new_line_text_only_invalidates_its_scene(tmp_path):
    job = _job()
    before = _keys(job, _voice(tmp_path / "a.wav", {}), _cues(job))
    edited = copy.deepcopy(job)
    edited["storyboard"]["scenes"][1]["render"]["speech"]["cues"][0]["text"] = "Voici le câble 3G-SDI."
    # La voix de la scène 2 est re-synthétisée : nouveaux échantillons dans SA fenêtre seulement.
    after = _keys(edited, _voice(tmp_path / "b.wav", {2: 2500}), _cues(edited))
    assert [b == a for b, a in zip(before, after)] == [True, False, True]


def test_same_texts_but_new_timed_cues_invalidate_the_scene(tmp_path):
    job, voice = _job(), _voice(tmp_path / "voice.wav", {})
    base = _cues(job)
    before = _keys(job, voice, base)
    shifted = {**base, 2: _timed("Voici le câble SDI.", start=12, end=55)}  # parole recalée autrement
    assert [b == a for b, a in zip(before, _keys(job, voice, shifted))] == [True, False, True]
    visemes = {**base, 3: _timed("Et la fiche BNC.", visemes=[{"frame": 9, "shape": "O"}])}
    assert [b == a for b, a in zip(before, _keys(job, voice, visemes))] == [True, True, False]
    # Repères recalés présents puis absents (retour à la voix du storyboard) : scène à refaire aussi.
    dropped = {**base, 1: None}
    assert [b == a for b, a in zip(before, _keys(job, voice, dropped))] == [False, True, True]


def test_a_new_voice_with_identical_cues_invalidates_only_where_it_is_heard(tmp_path):
    job = _job()
    before = _keys(job, _voice(tmp_path / "a.wav", {}), _cues(job))
    after = _keys(job, _voice(tmp_path / "b.wav", {3: 4000}), _cues(job))  # autre voix pour Ampère seulement
    assert [b == a for b, a in zip(before, after)] == [True, True, False]


def test_a_visual_only_edit_keeps_the_voice_dependencies_untouched(tmp_path):
    job, voice = _job(), _voice(tmp_path / "voice.wav", {})
    edited = copy.deepcopy(job)
    edited["storyboard"]["scenes"][0]["render"]["layers"][0]["objects"][0]["position"][0]["x"] = 300
    old = scene_cache.scene_dependencies(job, 1, job["storyboard"]["scenes"][0], voice, "m",
                                         speech_cues=_cues(job)[1])
    new = scene_cache.scene_dependencies(edited, 1, edited["storyboard"]["scenes"][0], voice, "m",
                                         speech_cues=_cues(edited)[1])
    # Voix pré-générée conservée : mêmes échantillons, mêmes repères ; seule l'image est à refaire.
    assert new["voice"] == old["voice"] and new["speech_timing"] == old["speech_timing"]
    assert scene_cache.dependency_key(new) != scene_cache.dependency_key(old)
    assert _keys(edited, voice, _cues(edited))[1:] == _keys(job, voice, _cues(job))[1:]


def test_several_speakers_on_distinct_scenes_are_invalidated_independently(tmp_path):
    job, voice = _job(), _voice(tmp_path / "voice.wav", {})
    before = _keys(job, voice, _cues(job))
    recast = copy.deepcopy(job)
    recast["storyboard"]["scenes"][2]["render"]["speech"]["speaker"] = "voltix"  # Ampère → Voltix en scène 3
    assert [b == a for b, a in zip(before, _keys(recast, voice, _cues(recast)))] == [True, True, False]


# -- intégration worker (actives après la fusion de #169 : module ``voiceover``) ----------------------------------
def _write_timing(voiceover, directory: Path, scenes: dict[int, list[dict]]) -> None:
    timing = {"schema": voiceover.TIMING_SCHEMA, "fps": FPS, "provider": "elevenlabs",
              "scenes": {str(index): {"cues": cues} for index, cues in scenes.items()}}
    (directory / voiceover.TIMING_FILE).write_text(json.dumps(timing), encoding="utf-8")


def test_worker_cache_key_includes_the_recalibrated_cues(tmp_path):
    """Le worker réconcilié doit passer ``voiceover.timed_cues`` à ``scene_cache.scene_dependencies``."""
    voiceover = pytest.importorskip("lody.animation.voiceover", reason="voix ElevenLabs (#169) pas encore fusionnée")
    from test.lody.test_animation_provider import FFMPEG, FakeRenderer, World
    from test.lody.test_animation_reedit import _job as submit
    from test.lody.test_animation_reedit import _voice as tone

    if not FFMPEG:
        pytest.skip("ffmpeg absent")
    from lody.animation import worker as w

    world = World(tmp_path)
    project = world.project("Cache voix")
    v1 = world.storyboard(project.id)
    cue = v1["scenes"][1]["render"]["speech"]["cues"][0]

    first = submit(world, project, v1, "prd_185a-run")
    tone(first / "voice.wav", 2.0)
    _write_timing(voiceover, first, {2: [{**cue, "spoken": cue["text"]}]})
    assert w.run_job(first, FakeRenderer(), ffmpeg=FFMPEG)["state"] == "done"

    same = submit(world, project, v1, "prd_185b-run")
    tone(same / "voice.wav", 2.0)
    _write_timing(voiceover, same, {2: [{**cue, "spoken": cue["text"]}]})
    renderer = FakeRenderer()
    assert w.run_job(same, renderer, ffmpeg=FFMPEG)["state"] == "done"
    assert renderer.rendered == []  # voix et repères inchangés : tout vient du cache

    moved = submit(world, project, v1, "prd_185c-run")
    tone(moved / "voice.wav", 2.0)  # mêmes échantillons, même texte…
    _write_timing(voiceover, moved, {2: [{**cue, "start": cue["start"] + 3, "spoken": cue["text"]}]})  # …autre recalage
    renderer = FakeRenderer()
    assert w.run_job(moved, renderer, ffmpeg=FFMPEG)["state"] == "done"
    assert renderer.rendered == [2]  # jamais une bouche calée sur l'ancien minutage
