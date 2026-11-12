"""Réédition ciblée et contrôle qualité du rendu 2D (#121).

Aucun réseau, aucun fournisseur payant, aucun vrai config.toml : moteur de scène factice (mire ffmpeg),
ffmpeg du système ou d'imageio-ffmpeg, base et dossier de données temporaires.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from lody.animation import modes, qa, reedit, scene_cache
from lody.animation import worker as w
from lody.animation.freeze import freeze_document
from test.lody.test_animation_provider import FFMPEG, FPS, FakeRenderer, World, needs_ffmpeg

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _job(world: World, project, storyboard, key: str, **animation):
    request = world.request(project, storyboard)
    if animation:
        request = request.with_updates(animation={**request.animation, **animation})
    task = world.connector.submit(request, key)
    return w.find_job_dir(world.data, task.task_id)


def _edited(world: World, project, storyboard, edit) -> dict:
    """V2 : copie modifiée du storyboard figé, regelée dans le catalogue du projet."""
    document = copy.deepcopy(storyboard)
    edit(document)
    return freeze_document(world.assets, project.id, document)


def _manifest(directory: Path) -> dict:
    return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))


def _voice(path: Path, seconds: float, *, louder_after: float | None = None, rate: int = 16000) -> None:
    """WAV PCM 16 bits mono ; ``louder_after`` change les échantillons à partir de cet instant."""
    frames = bytearray()
    for i in range(int(seconds * rate)):
        value = 3000 if (louder_after is not None and i >= louder_after * rate) else 1000
        frames += int(value if i % 40 < 20 else -value).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(rate)
        sink.writeframes(bytes(frames))


class BlackRenderer(FakeRenderer):
    """Rend la scène ``black`` en noir complet (écran noir à détecter)."""

    def __init__(self, black: int):
        super().__init__()
        self.black = black

    def render(self, scene_input, output, timeout, tick):
        data = json.loads(Path(scene_input).read_text(encoding="utf-8"))
        if data["index"] != self.black:
            return super().render(scene_input, output, timeout, tick)
        canvas = data["canvas"]
        seconds = data["scene"]["duration_frames"] / canvas["fps"]
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"color=c=black:s={canvas['width']}x{canvas['height']}:r={canvas['fps']}", "-t",
                        f"{seconds:.3f}", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                        str(output)], check=True, capture_output=True, timeout=60)
        self.rendered.append(data["index"])


# -- cache de scène : une V2 ne refait que ce qui a changé --------------------------------------------------------
@needs_ffmpeg
def test_v2_only_renders_the_modified_scene_and_reports_the_time_saved(world):
    project = world.project("Réédition")
    v1 = world.storyboard(project.id)
    first = _job(world, project, v1, "prd_v1-run")
    renderer = FakeRenderer()
    assert w.run_job(first, renderer, ffmpeg=FFMPEG)["state"] == "done"
    assert renderer.rendered == [1, 2]
    assert _manifest(first)["audit"]["scenes_rendered"] == 2

    def new_gesture(document):
        document["scenes"][1]["render"]["layers"][1]["objects"][0]["gestures"][0]["end"] = 10

    second = _job(world, project, _edited(world, project, v1, new_gesture), "prd_v2-run")
    again = FakeRenderer()
    assert w.run_job(second, again, ffmpeg=FFMPEG)["state"] == "done"
    assert again.rendered == [2]  # la scène 1, inchangée, vient du cache du projet
    manifest = _manifest(second)
    audit = manifest["audit"]
    assert audit["scenes_reused"] == 1 and audit["reused_scenes"] == [1] and audit["rendered_scenes"] == [2]
    assert audit["render_ms_saved"] == _manifest(first)["scenes"][0]["render_ms"] > 0  # temps MESURÉ en V1
    assert audit["external_calls"] == 0 and audit["external_cost"] == 0.0
    assert set(audit["steps_ms"]) == {"scenes_ms", "soundtrack_ms", "assembly_ms", "qa_ms"}
    assert manifest["scenes"][0]["cache"] == "hit" and manifest["scenes"][0]["reused_from"]["task"] == first.name
    assert manifest["scenes"][0]["sha256"] == _manifest(first)["scenes"][0]["sha256"]  # même fichier, octet pour octet
    assert abs(manifest["duration_seconds"] - 2.0) < 0.15


@needs_ffmpeg
def test_editing_only_the_narrative_intent_renders_nothing(world):
    project = world.project("Intention")
    v1 = world.storyboard(project.id)
    assert w.run_job(_job(world, project, v1, "prd_a-run"), FakeRenderer(), ffmpeg=FFMPEG)["state"] == "done"

    def retitle(document):
        document["scenes"][0]["intent"]["narration"] = "Une autre intention, jamais lue par le moteur."
        document["scenes"][0]["id"] = "accueil-v2"

    renderer = FakeRenderer()
    second = _job(world, project, _edited(world, project, v1, retitle), "prd_b-run")
    assert w.run_job(second, renderer, ffmpeg=FFMPEG)["state"] == "done"
    assert renderer.rendered == [] and _manifest(second)["audit"]["scenes_reused"] == 2


@needs_ffmpeg
def test_a_fallback_is_never_cached_and_an_altered_entry_is_never_served(world):
    project = world.project("Repli", modes.HYBRID)
    v1 = world.storyboard(project.id)
    first = _job(world, project, v1, "prd_c-run")
    assert w.run_job(first, FakeRenderer(fail={2}), ffmpeg=FFMPEG)["state"] == "done"
    cache_dir = first.parent / scene_cache.CACHE_DIR
    assert len(list(cache_dir.glob("*.mp4"))) == 1  # seule la scène animée est rangée

    entry = next(cache_dir.glob("*.mp4"))
    entry.write_bytes(b"altere")
    renderer = FakeRenderer()
    second = _job(world, project, v1, "prd_d-run")
    assert w.run_job(second, renderer, ffmpeg=FFMPEG)["state"] == "done"
    assert renderer.rendered == [1, 2]  # entrée altérée ignorée, repli de la V1 jamais resservi
    assert all(s["status"] == "animated" for s in _manifest(second)["scenes"])


@needs_ffmpeg
def test_cache_is_bypassed_when_the_scene_engine_changes_or_on_request(world):
    project = world.project("Moteur")
    v1 = world.storyboard(project.id)
    old = FakeRenderer()
    old.identity = "moteur v1"
    assert w.run_job(_job(world, project, v1, "prd_e-run"), old, ffmpeg=FFMPEG)["state"] == "done"
    new = FakeRenderer()
    new.identity = "moteur v2"
    assert w.run_job(_job(world, project, v1, "prd_f-run"), new, ffmpeg=FFMPEG)["state"] == "done"
    assert new.rendered == [1, 2]
    fresh = FakeRenderer()
    fresh.identity = "moteur v2"
    assert w.run_job(_job(world, project, v1, "prd_g-run"), fresh, ffmpeg=FFMPEG, use_cache=False)["state"] == "done"
    assert fresh.rendered == [1, 2]


@needs_ffmpeg
def test_a_voice_change_only_invalidates_the_scene_where_it_is_heard(world):
    project = world.project("Voix")
    v1 = world.storyboard(project.id)
    first = _job(world, project, v1, "prd_h-run")
    _voice(first / "voice.wav", 2.0)
    assert w.run_job(first, FakeRenderer(), ffmpeg=FFMPEG)["state"] == "done"
    assert _manifest(first)["qa"]["status"] == "ok"

    second = _job(world, project, v1, "prd_i-run")
    _voice(second / "voice.wav", 2.0, louder_after=1.2)  # retouche dans la fenêtre de la scène 2 (1 s → 2 s)
    renderer = FakeRenderer()
    assert w.run_job(second, renderer, ffmpeg=FFMPEG)["state"] == "done"
    assert renderer.rendered == [2]


def test_voice_slice_digest_depends_only_on_the_scene_window(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    _voice(a, 2.0)
    _voice(b, 2.0, louder_after=1.5)
    assert scene_cache.voice_slice_digest(a, FPS, 0, FPS) == scene_cache.voice_slice_digest(b, FPS, 0, FPS)
    assert scene_cache.voice_slice_digest(a, FPS, FPS, FPS) != scene_cache.voice_slice_digest(b, FPS, FPS, FPS)
    odd = tmp_path / "odd.wav"
    odd.write_bytes(b"pas un wav")
    assert "file_sha256" in scene_cache.voice_slice_digest(odd, FPS, 0, FPS)
    assert scene_cache.voice_slice_digest(tmp_path / "absent.wav", FPS, 0, FPS) is None


def test_cache_is_capped_least_recently_used_first(tmp_path):
    cache = scene_cache.SceneCache(tmp_path / "projet", max_bytes=10)
    source = tmp_path / "projet" / "tache" / "scenes" / "01.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"123456")
    keys = ["a" * 64, "b" * 64]
    for key in keys:
        cache.store(key, source, {"status": "animated", "sha256": scene_cache._sha256(source), "render_ms": 5})
    assert not (cache.root / f"{keys[0]}.mp4").exists() and (cache.root / f"{keys[1]}.mp4").exists()
    cache.store("c" * 64, source, {"status": "fallback", "sha256": "x"})
    assert not (cache.root / f"{'c' * 64}.json").exists()
    with pytest.raises(ValueError):
        cache.lookup("../../etc", tmp_path / "x.mp4")


def test_command_engine_identity_includes_its_announced_version(monkeypatch):
    script = "import sys; print('moteur-1.2' if sys.argv[1:] == ['--version'] else '')"
    renderer = w.CommandRenderer([sys.executable, "-c", script])
    monkeypatch.setenv(w.RENDERER_REVISION_ENV, "image-2026-11")
    identity = renderer.identity()
    assert "moteur-1.2" in identity and "image-2026-11" in identity
    assert w.renderer_identity(renderer) == identity and w.renderer_identity(None) == ""


def test_remotion_adapter_announces_its_source_digest():
    adapter = ROOT / "renderers" / "remotion" / "bin" / "lody-remotion.mjs"
    node = __import__("shutil").which("node")
    if not node:
        pytest.skip("node indisponible")
    done = subprocess.run([node, str(adapter), "--version"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.startswith("lody-remotion ")


def test_remotion_adapter_keeps_docker_prepare_and_cache_version_modes():
    """#183 : la fusion du worker Docker #156 conserve --prepare (image) et --version (cache de scène #121)."""
    adapter = ROOT / "renderers" / "remotion" / "bin" / "lody-remotion.mjs"
    source = adapter.read_text(encoding="utf-8")
    assert "args.prepare" in source and "'--version'" in source and "process.chdir(ROOT)" in source
    node = __import__("shutil").which("node")
    if not node:
        pytest.skip("node indisponible")
    done = subprocess.run([node, str(adapter)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 2 and "--prepare" in done.stderr and "--version" in done.stderr


# -- prévisualisation basse résolution ------------------------------------------------------------------------------
@needs_ffmpeg
def test_low_resolution_preview_is_scaled_and_cached_apart_from_the_final_render(world):
    project = world.project("Aperçu")
    v1 = world.storyboard(project.id)
    preview = _job(world, project, v1, "prd_j-preview", preview_scale=0.5)
    assert json.loads((preview / "job.json").read_text())["preview"] == {"scale": 0.5}
    renderer = FakeRenderer()
    assert w.run_job(preview, renderer, ffmpeg=FFMPEG)["state"] == "done"
    scene_input = json.loads((preview / "scenes" / "01-input.json").read_text())
    assert scene_input["render"] == {"scale": 0.5}
    manifest = _manifest(preview)
    assert manifest["preview"] is True and (manifest["width"], manifest["height"]) == (160, 284)
    final = FakeRenderer()
    assert w.run_job(_job(world, project, v1, "prd_j-run"), final, ffmpeg=FFMPEG)["state"] == "done"
    assert final.rendered == [1, 2]  # un aperçu n'est jamais resservi comme rendu final
    assert w.preview_scale({"preview": {"scale": 0.4}}) == 0.5 and w.preview_scale({"preview": {"scale": 3}}) == 1.0


# -- contrôle qualité du MP4 ------------------------------------------------------------------------------------------
@needs_ffmpeg
def test_qa_flags_a_black_scene_and_a_cut_voice(world):
    project = world.project("QA")
    directory = _job(world, project, world.storyboard(project.id), "prd_k-run")
    _voice(directory / "voice.wav", 3.0)  # 1 s de voix de trop pour une vidéo de 2 s
    assert w.run_job(directory, BlackRenderer(black=2), ffmpeg=FFMPEG)["state"] == "done"
    report = json.loads((directory / "qa.json").read_text())
    found = {item["id"]: item for item in report["checks"]}
    assert report["status"] == "error"
    assert found["black_screen"]["scene"] == 2 and found["voice_cut"]["level"] == "error"
    assert "audio_duration" not in found  # la bande son finit exactement avec l'image
    manifest = _manifest(directory)
    assert manifest["qa"]["status"] == "error"
    assert any(warning.startswith("Contrôle qualité : Écran noir") for warning in manifest["warnings"])


@needs_ffmpeg
def test_a_clean_render_passes_qa_without_warnings(world):
    project = world.project("Propre")
    directory = _job(world, project, world.storyboard(project.id), "prd_l-run")
    assert w.run_job(directory, FakeRenderer(), ffmpeg=FFMPEG)["state"] == "done"
    manifest = _manifest(directory)
    assert manifest["qa"]["status"] == "ok" and manifest["warnings"] == []


def _doc(scenes: list[dict], width: int = 1080, height: int = 1920) -> dict:
    return {"schema": "lody.animation/1", "canvas": {"fps": 30, "width": width, "height": height}, "scenes": scenes}


def _scene(scene_id: str, objects: list[dict], cues: list[dict] | None = None, **render) -> dict:
    data = {"layers": [{"id": "l", "objects": objects}], "fallback": {"type": "still", "frame": 0}, **render}
    if cues:
        data["speech"] = {"cues": cues}
    return {"id": scene_id, "duration_frames": 150, "intent": {}, "render": data}


def _hero(version: int = 1, x: int = 540, lipsync: bool = False) -> dict:
    obj = {"id": "heros", "type": "asset", "asset": {"id": "ast_000000000121", "version": version},
           "position": [{"frame": 0, "x": x, "y": 1400}]}
    return {**obj, "lipsync": True} if lipsync else obj


def test_static_qa_finds_cut_subtitles_overflowing_text_and_continuity_breaks():
    long_cue = {"text": " ".join(["Voltix explique le câble coaxial et sa fiche BNC"] * 3), "start": 0, "end": 140}
    fast = {"text": "Un texte beaucoup trop long pour être lu si vite", "start": 141, "end": 149}
    title = {"id": "titre", "type": "text", "text": {"content": "Un titre beaucoup trop long pour l'écran",
                                                       "style": "title"}, "position": [{"frame": 0, "x": 540, "y": 300}]}
    entering = {**title, "id": "entree", "position": [{"frame": 0, "x": -2000, "y": 300}]}
    doc = _doc([_scene("a", [_hero(1, x=100, lipsync=True), title, entering], [long_cue, fast]),
                _scene("b", [_hero(2, x=900)], [{"text": "Une réplique bien trop longue pour une seule seconde",
                                                  "start": 0, "end": 30}])])
    found = {}
    for item in qa.static_checks(doc, has_voice=False):
        found.setdefault(item["id"], []).append(item)
    assert found["subtitle_cut"][0]["scene"] == 1
    assert found["subtitle_short"][0]["scene"] == 1 and 2 in [i["scene"] for i in found["subtitle_fast"]]
    assert [i["data"]["object"] for i in found["text_overflow"]] == ["titre"]  # l'entrée hors champ est voulue
    assert found["continuity_version"][0]["level"] == "warning"
    assert found["continuity_jump"][0]["scene"] == 2
    assert found["sync_no_voice"][0]["scene"] == 1
    assert qa.static_checks(_doc([_scene("ok", [_hero()], [{"text": "Bonjour !", "start": 0, "end": 30}])])) == []


def test_wrap_lines_estimates_caption_lines():
    assert qa.wrap_lines("Bonjour", 60, 894) == 1
    assert qa.wrap_lines("mot " * 40, 60, 894) >= 6
    assert qa.wrap_lines("x" * 100, 60, 894) == 5  # un mot plus long qu'une ligne est coupé


# -- plan de réédition ---------------------------------------------------------------------------------------------
def _story() -> dict:
    gesture = {**_hero(lipsync=True), "gestures": [{"start": 0, "end": 30, "name": "saluer"}]}
    return _doc([
        _scene("accueil", [gesture], [{"text": "Salut, moi c'est Voltix !", "start": 10, "end": 60}]),
        _scene("cable", [_hero()], [{"text": "Voici le câble SDI.", "start": 10, "end": 60}]),
        _scene("bnc", [_hero()], [{"text": "Et la fiche BNC.", "start": 10, "end": 60}]),
    ])


def test_plan_explains_what_a_v2_redoes_and_what_it_keeps():
    before = _story()
    after = copy.deepcopy(before)
    after["scenes"][0]["render"]["layers"][0]["objects"][0]["gestures"][0]["name"] = "montrer"
    after["scenes"][1]["intent"] = {"narration": "nouvelle intention"}
    after["scenes"][2]["render"]["speech"]["cues"][0]["text"] = "Et enfin la fiche BNC."
    after["scenes"].append(_scene("fin", [_hero()]))
    manifest = {"scenes": [{"id": "cable", "render_ms": 40000}]}
    result = reedit.plan(before, after, previous_manifest=manifest)
    by_id = {entry["id"]: entry for entry in result["scenes"]}
    assert by_id["accueil"]["action"] == "render" and by_id["accueil"]["reason"] == "geste"
    assert by_id["cable"]["action"] == "reuse" and by_id["cable"]["voice"] == "keep"
    assert by_id["bnc"]["action"] == "render" and by_id["bnc"]["voice"] == "regenerate"
    assert by_id["fin"]["reason"] == "nouvelle scène"
    summary = result["summary"]
    assert summary["scenes_reused"] == 1 and summary["render_seconds_saved"] == 40
    assert summary["render_seconds_saved_measured"] is True
    assert summary["voice_to_regenerate"] == [3] and "accord" in summary["cost_note"]
    text = reedit.describe(result)
    assert "1 scène(s) réutilisée(s), 3 à refaire" in text and "voix à régénérer" in text


def test_plan_handles_reorder_removal_canvas_and_voice_shift():
    before = _story()
    moved = copy.deepcopy(before)
    moved["scenes"] = [moved["scenes"][1], moved["scenes"][0]]
    result = reedit.plan(before, moved)
    assert all(entry["action"] == "reuse" for entry in result["scenes"])
    assert result["removed"] == [{"index": 3, "id": "bnc"}]
    with_voice = reedit.plan(before, moved, has_voice=True)
    assert all(entry.get("voice_shift") for entry in with_voice["scenes"])  # fenêtre de voix décalée
    longer = copy.deepcopy(before)
    longer["scenes"][0]["duration_frames"] = 180
    shifted = {e["id"]: e for e in reedit.plan(before, longer, has_voice=True)["scenes"]}
    assert shifted["accueil"]["reason"] == "durée" and shifted["cable"]["voice_shift"] is True
    assert {e["action"] for e in reedit.plan(before, longer)["scenes"][1:]} == {"reuse"}
    wide = copy.deepcopy(before)
    wide["canvas"]["width"] = 720
    assert reedit.plan(before, wide)["canvas_changed"] is True


def test_plan_and_cache_agree_on_what_is_reusable():
    before = _story()
    after = copy.deepcopy(before)
    after["scenes"][1]["intent"] = {"narration": "x"}
    after["scenes"][2]["render"]["camera"] = {"keyframes": [{"frame": 0, "x": 540, "y": 960, "zoom": 1.2,
                                                             "rotation": 0}]}
    job_before = {"storyboard": before, "assets": {}, "asset_info": {}}
    job_after = {"storyboard": after, "assets": {}, "asset_info": {}}
    plan = {entry["id"]: entry["action"] for entry in reedit.plan(before, after)["scenes"]}
    for index, (old, new) in enumerate(zip(before["scenes"], after["scenes"]), 1):
        same = scene_cache.dependency_key(scene_cache.scene_dependencies(job_before, index, old, None, "m")) == \
            scene_cache.dependency_key(scene_cache.scene_dependencies(job_after, index, new, None, "m"))
        assert same == (plan[new["id"]] == "reuse"), new["id"]


# -- outil en ligne de commande -------------------------------------------------------------------------------------
def test_reedit_cli_prints_a_plan(tmp_path):
    before, after = _story(), _story()
    after["scenes"][0]["render"]["layers"][0]["objects"][0]["gestures"][0]["name"] = "montrer"
    (tmp_path / "v1.json").write_text(json.dumps(before), encoding="utf-8")
    (tmp_path / "v2.json").write_text(json.dumps(after), encoding="utf-8")
    done = subprocess.run([sys.executable, str(ROOT / "scripts/animation/reedit.py"), "plan", "--before",
                           str(tmp_path / "v1.json"), "--after", str(tmp_path / "v2.json")],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert "2 scène(s) réutilisée(s), 1 à refaire" in done.stdout and "accueil : geste" in done.stdout
