"""« Reprendre l'audio » : refaire UNIQUEMENT la voix d'une production terminée (#92, direction vocale par
réplique), images déjà générées réutilisées, jamais régénérées. Nouvelle version, l'originale conservée."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from lody import settings
from lody.generation import reprise_audio as ra
from lody.generation.models import ProductionStatus as S
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS

TASK = "c89df30f-7edc-4319-8a65-d54c5466f860"
PARAMS = {
    "video_subject": "Épisode 0", "video_aspect": "9:16", "video_fit_mode": "cover",
    "video_concat_mode": "sequential", "video_transition_mode": None, "video_clip_duration": 6,
    "video_clip_speed": 1.0, "match_materials_to_script": True, "video_count": 1,
    "video_source": "openai_image", "video_language": "fr-FR", "n_threads": 2,
    "subtitle_enabled": True, "subtitle_display_mode": "sentence", "font_name": "BeVietnamPro-Bold.ttf",
    "bgm_type": "",
}
SPOKEN = "[matter-of-fact] Voyageur, j'ai une quête urgente pour toi ! [hesitates] Enfin, c'est ce que je dis."
CLEAN = "Voyageur, j'ai une quête urgente pour toi ! Enfin, c'est ce que je dis."


def _task(tmp_path: Path, *, clip_count: int = 10) -> Path:
    folder = tmp_path / "storage" / "tasks" / TASK
    folder.mkdir(parents=True)
    (folder / "script.json").write_bytes(json.dumps({"script": CLEAN, "params": PARAMS}).encode())
    for i in range(clip_count):
        name = f"openai-image-clip{i:02d}"
        (folder / f"{name}.png").write_bytes(f"PNG-{i}".encode() * 50)
        (folder / f"{name}.png.mp4").write_bytes(f"CLIP-{i}".encode() * 200)
    return folder


class FakeVoice:
    """Remplace voice.elevenlabs_tts + voice.get_audio_duration : écrit un audio.mp3, tente le réseau si demandé."""

    def __init__(self, *, use_wrong_host=False, duration=30.0, succeed=True):
        self.calls, self.use_wrong_host, self.duration, self.succeed = [], use_wrong_host, duration, succeed

    def elevenlabs_tts(self, *, text, voice_id, voice_file, model_id, voice_settings_override, subtitle_text):
        self.calls.append({"text": text, "voice_id": voice_id, "voice_file": voice_file, "model_id": model_id,
                           "voice_settings_override": voice_settings_override, "subtitle_text": subtitle_text})
        if self.use_wrong_host:
            # simule un code réutilisé qui tenterait, par erreur, de joindre un autre fournisseur —
            # exercé à travers le VRAI garde réseau (_allow_network_only_for), pas une connexion réelle.
            with ra._allow_network_only_for((".elevenlabs.io",)):
                socket.create_connection(("evil.example.com", 443), timeout=1)
        if not self.succeed:
            return None
        Path(voice_file).write_bytes(b"FAKE-MP3" * 500)
        return SimpleNamespace(subs=[subtitle_text], offset=[(0, 1)])

    def get_audio_duration(self, _path):
        return self.duration


class FakeSubtitle:
    def __init__(self):
        self.calls = []

    def __call__(self, task_id, params, text, sub_maker, audio_file):
        self.calls.append({"task_id": task_id, "params": params, "text": text, "audio_file": audio_file})
        path = Path(audio_file).parent / "subtitle.srt"
        path.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n", encoding="utf-8")
        return str(path)


class FakeVideo:
    def __init__(self, *, combine_ok=True, final_ok=True):
        self.combine_calls, self.video_calls = [], []
        self.combine_ok, self.final_ok = combine_ok, final_ok

    def combine_videos(self, **kwargs):
        self.combine_calls.append(kwargs)
        if self.combine_ok:
            Path(kwargs["combined_video_path"]).write_bytes(b"COMBINED" * 500)

    def generate_video(self, **kwargs):
        self.video_calls.append(kwargs)
        if self.final_ok:
            Path(kwargs["output_file"]).write_bytes(b"FINAL-RENDER" * 500)
        return True


def _task_dir_for(tmp_path):
    def inner(task_id):
        d = tmp_path / "storage" / "tasks" / task_id
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    return inner


def _reprise(folder, tmp_path, *, voice=None, subtitle=None, video=None, voice_id="goccsFDjQ0kcbRoOsQ2r",
            model_id="eleven_v3", voice_settings=None):
    voice = voice or FakeVoice()
    subtitle = subtitle or FakeSubtitle()
    video = video or FakeVideo()
    voice_settings = voice_settings or {"stability": 0.3, "similarity_boost": 0.75, "style": 0.35,
                                        "use_speaker_boost": True, "speed": 1.0}
    report = ra.reprise(
        folder, spoken_text=SPOKEN, subtitle_text=CLEAN, voice_id=voice_id, model_id=model_id,
        voice_settings=voice_settings, elevenlabs_tts=voice.elevenlabs_tts,
        get_audio_duration=voice.get_audio_duration, generate_subtitle=subtitle,
        combine_videos=video.combine_videos, generate_video=video.generate_video,
        make_video_params=lambda params: SimpleNamespace(**params), task_dir_for=_task_dir_for(tmp_path),
    )
    return report, voice, subtitle, video


def test_reprise_regenerates_only_the_voice_and_reuses_every_existing_clip(tmp_path):
    folder = _task(tmp_path)
    report, voice, subtitle, video = _reprise(folder, tmp_path)

    # un seul appel fournisseur, la voix — jamais les images
    assert report["provider_calls"] == 1
    assert len(voice.calls) == 1
    sent = voice.calls[0]
    assert sent["text"] == SPOKEN and sent["subtitle_text"] == CLEAN  # balises envoyées, jamais aux sous-titres
    assert sent["model_id"] == "eleven_v3"

    # les dix clips sont copiés (jamais régénérés) dans le nouveau dossier de tâche, ordre préservé
    new_dir = tmp_path / "storage" / "tasks" / report["new_task_id"]
    copied = sorted(p.name for p in new_dir.glob("openai-image-*.png.mp4"))
    original = sorted(p.name for p in folder.glob("openai-image-*.png.mp4"))
    assert copied == original and len(copied) == 10
    assert report["reused_clip_count"] == 10

    # sous-titres construits à partir du texte PROPRE, jamais des balises
    assert subtitle.calls[0]["text"] == CLEAN
    assert "[matter-of-fact]" not in (new_dir / "subtitle.srt").read_text(encoding="utf-8")

    # montage : match_materials_to_script propagé depuis les params d'origine (#92)
    combine = video.combine_calls[0]
    assert combine["match_materials_to_script"] is True
    assert len(combine["video_paths"]) == 10
    assert combine["audio_file"] == str(new_dir / "audio.mp3")

    # rendu final produit, référence cohérente
    assert (new_dir / "final-1.mp4").is_file()
    assert report["video_ref"] == f"tasks/{report['new_task_id']}/final-1.mp4"
    assert {a["kind"] for a in report["assets"]} == {"audio", "subtitle", "clip"}
    assert sum(1 for a in report["assets"] if a["kind"] == "clip") == 10

    # l'original n'a jamais été modifié
    assert (folder / "openai-image-clip00.png.mp4").read_bytes() == b"CLIP-0" * 200


def test_the_network_guard_rejects_any_host_other_than_elevenlabs(tmp_path):
    """Défense en profondeur : même du code réutilisé qui tenterait de joindre un autre fournisseur
    (ex. images) pendant la reprise est bloqué AVANT toute tentative réelle de connexion."""
    folder = _task(tmp_path)
    voice = FakeVoice(use_wrong_host=True)
    with pytest.raises(RuntimeError, match="hôte non autorisé"):
        _reprise(folder, tmp_path, voice=voice)
    assert voice.calls  # l'appel a bien été tenté : c'est la connexion interne qui a été bloquée


def test_missing_clips_are_reported_and_nothing_is_called(tmp_path):
    folder = _task(tmp_path, clip_count=0)
    voice = FakeVoice()
    with pytest.raises(ra.ReprisError, match="Aucune image"):
        _reprise(folder, tmp_path, voice=voice)
    assert voice.calls == []


def test_missing_script_json_is_reported(tmp_path):
    folder = tmp_path / "storage" / "tasks" / TASK
    folder.mkdir(parents=True)
    with pytest.raises(ra.ReprisError, match="script.json"):
        _reprise(folder, tmp_path)


def test_voice_synthesis_failure_is_reported_and_nothing_downstream_runs(tmp_path):
    folder = _task(tmp_path)
    voice = FakeVoice(succeed=False)
    subtitle = FakeSubtitle()
    with pytest.raises(ra.ReprisError, match="synthèse vocale"):
        _reprise(folder, tmp_path, voice=voice, subtitle=subtitle)
    assert subtitle.calls == []


def test_zero_duration_audio_is_rejected(tmp_path):
    folder = _task(tmp_path)
    voice = FakeVoice(duration=0.0)
    with pytest.raises(ra.ReprisError, match="Durée audio"):
        _reprise(folder, tmp_path, voice=voice)


def test_combine_failure_is_reported(tmp_path):
    folder = _task(tmp_path)
    video = FakeVideo(combine_ok=False)
    with pytest.raises(ra.ReprisError, match="montage"):
        _reprise(folder, tmp_path, video=video)


def test_final_render_failure_is_reported(tmp_path):
    folder = _task(tmp_path)
    video = FakeVideo(final_ok=False)
    with pytest.raises(ra.ReprisError, match="rendu final"):
        _reprise(folder, tmp_path, video=video)


# -- enregistrement dans Lody : NOUVELLE VERSION, l'originale conservée --------------------------------------------
@pytest.fixture
def registered(lody_env):
    projects = ProjectRepository(settings.db_path())
    projects.seed_defaults(SEED_PROJECTS)
    project = next(p for p in projects.list_projects() if p.name == "LodyCrypto")
    repo = ProductionRepository(settings.db_path())
    production = repo.create(
        project_id=project.id, subject="Épisode 0", provider="moneyprinterturbo", status=S.TERMINEE,
        external_task_id=TASK, video_ref=f"tasks/{TASK}/final-1.mp4", script=CLEAN,
        storyboard=[{"index": 1, "narration": CLEAN}], assets=[{"kind": "audio", "ref": f"tasks/{TASK}/audio.mp3"}],
    )
    return repo, production


def _fake_report(new_task_id="51b9ab1b-aaaa-bbbb-cccc-000000000000"):
    return {
        "kind": ra.REPORT_KIND, "source_task_id": TASK, "new_task_id": new_task_id,
        "created_at": "2026-10-04T10:00:00+00:00", "provider_calls": 1, "reused_clip_count": 10,
        "voice_direction": {
            "voice_id": "goccsFDjQ0kcbRoOsQ2r", "model_id": "eleven_v3",
            "voice_settings": {"stability": 0.3, "similarity_boost": 0.75, "style": 0.35,
                              "use_speaker_boost": True, "speed": 1.0},
            "spoken_text_sent": SPOKEN, "subtitle_text": CLEAN,
        },
        "audio_duration_s": 29.4, "video_ref": f"tasks/{new_task_id}/final-1.mp4",
        "assets": [{"kind": "audio", "ref": f"tasks/{new_task_id}/audio.mp3"},
                  {"kind": "subtitle", "ref": f"tasks/{new_task_id}/subtitle.srt"},
                  {"kind": "clip", "ref": f"tasks/{new_task_id}/openai-image-clip00.png.mp4"}],
    }


def test_register_creates_a_new_version_and_keeps_the_original_untouched(registered):
    repo, production = registered
    report = _fake_report()
    before = repo.get(production.id)

    result = ra.register(production.id, report)

    original_again = repo.get(production.id)
    assert original_again == before  # jamais modifiée
    new = repo.get(result["production"])
    assert new.parent_production_id == production.id
    assert new.root_production_id == production.root_production_id
    assert new.version == before.version + 1
    assert new.status == S.TERMINEE
    assert new.script == CLEAN and new.storyboard == before.storyboard  # script/storyboard conservés
    assert new.video_ref == report["video_ref"]
    assert new.external_task_id == report["new_task_id"]
    assert new.trace["voice_direction"]["applied"] is True
    assert new.trace["voice_direction"]["model_id"] == "eleven_v3"
    assert new.trace["voice_direction"]["spoken_text_sent"] == SPOKEN
    assert len(repo.list_for_project(production.project_id)) == 2  # originale + nouvelle version


def test_register_refuses_a_foreign_or_unfinished_report(registered):
    repo, production = registered
    with pytest.raises(ra.ReprisError, match="ne correspond pas"):
        ra.register(production.id, {**_fake_report(), "source_task_id": "autre-tache"})
    with pytest.raises(ra.ReprisError, match="ne correspond pas"):
        ra.register(production.id, {**_fake_report(), "kind": "autre"})
    repo.update(production.id, status="ECHEC")
    with pytest.raises(ra.ReprisError, match="terminée"):
        ra.register(production.id, _fake_report())


def test_reprise_tool_never_reads_config_or_hardcodes_a_provider_other_than_elevenlabs():
    source = Path(ra.__file__).read_text(encoding="utf-8")
    for forbidden in ("config.toml", "openai_api_key", "pexels", "pixabay"):
        assert forbidden not in source, forbidden


def test_reprise_script_targets_both_containers_correctly():
    script = (Path(ra.__file__).resolve().parents[3] / "scripts" / "lody-reprise-audio.sh").read_text(encoding="utf-8")
    assert "PYTHONPATH=/MoneyPrinterTurbo/webui" in script
    assert "PYTHONPATH=/tmp/lody-reprise:/MoneyPrinterTurbo" in script
    assert 'register --production "$PROD"' in script and "set -eu" in script
    for forbidden in ("curl", "wget"):
        assert forbidden not in script
