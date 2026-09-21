"""« Réparer le rendu » : refaire seulement l'incrustation, sans appel fournisseur, original conservé."""

from __future__ import annotations

import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from lody import settings
from lody.generation import repair_render as rr
from lody.generation import typography
from lody.generation.models import ProductionStatus as S
from lody.generation.store import ProductionRepository
from lody.projects import ProjectRepository
from lody.seeds import SEED_PROJECTS

TASK = "55fbc4e6-d213-4dc5-8677-a253e73b8cd0"
SRT = "1\n00:00:00,000 --> 00:00:01,869\nSi tu découvres le Web3 gaming\n\n2\n00:00:04,984 --> 00:00:06,853\nmais l’  idée de base est simple\n"
PARAMS = {"video_language": "fr-FR", "subtitle_enabled": True, "subtitle_display_mode": "sentence",
          "font_name": "MicrosoftYaHeiBold.ttc", "font_size": 58, "bgm_type": "elevenlabs", "bgm_volume": 0.2}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task(tmp_path: Path, **overrides) -> Path:
    folder = tmp_path / "storage" / "tasks" / TASK
    folder.mkdir(parents=True)
    files = {"combined-1.mp4": b"combined-video" * 200, "audio.mp3": b"voice" * 300, "final-1.mp4": b"ORIGINAL-RENDER" * 500,
             "elevenlabs-bgm-1.mp3": b"music" * 300, "subtitle.srt": SRT.encode(),
             "script.json": json.dumps({"script": "x", "params": PARAMS}).encode()}
    files.update(overrides)
    for name, content in files.items():
        if content is not None:
            (folder / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    return folder


class FakeEngine:
    """Remplace generate_video du moteur : écrit un fichier, enregistre ce qu'il a reçu, tente le réseau si demandé."""

    def __init__(self, *, use_network=False, bgm_ok=True, write=True):
        self.calls, self.use_network, self.bgm_ok, self.write = [], use_network, bgm_ok, write

    def params(self, params):
        return SimpleNamespace(**params)

    def __call__(self, **kwargs):
        subtitle = Path(kwargs["subtitle_path"]).read_text(encoding="utf-8")
        self.calls.append({**kwargs, "subtitle_text": subtitle})
        if self.use_network:
            socket.create_connection(("api.openai.com", 443), timeout=1)   # un fournisseur payant serait joint ici
        if self.write:
            Path(kwargs["output_file"]).write_bytes(b"REPAIRED-RENDER" * 500)
        return self.bgm_ok


def _repair(folder, engine=None, **kwargs):
    engine = engine or FakeEngine()
    return rr.repair(folder, font="BeVietnamPro-Bold.ttf", engine_params=engine.params, generate_video=engine, **kwargs), engine


def test_repair_rebuilds_only_the_montage_from_existing_assets(tmp_path):
    folder = _task(tmp_path)
    before = {p.name: _sha(p) for p in folder.iterdir()}
    report, engine = _repair(folder)
    out = folder / "repair"
    call = engine.calls[0]
    # mêmes assets qu'à l'origine, sous-titres normalisés, musique déjà générée passée en « override » (aucun appel)
    assert Path(call["video_path"]).name == "combined-1.mp4" and Path(call["audio_path"]).name == "audio.mp3"
    assert Path(call["bgm_file_override"]).name == "elevenlabs-bgm-1.mp3"
    assert "mais l’idée de base est simple" in call["subtitle_text"] and "l’  idée" not in call["subtitle_text"]
    # mêmes paramètres, seule la police change
    assert call["params"].font_name == "BeVietnamPro-Bold.ttc".replace(".ttc", ".ttf")
    assert {k: v for k, v in vars(call["params"]).items() if k != "font_name"} == {k: v for k, v in PARAMS.items() if k != "font_name"}
    assert report["provider_calls"] == 0 and report["font"] == {"before": "MicrosoftYaHeiBold.ttc", "after": "BeVietnamPro-Bold.ttf"}
    assert report["subtitle_lines_normalized"] == 1 and report["kind"] == "technical_repair"
    assert set(report["reused_assets"]) == {"combined-1.mp4", "audio.mp3", "subtitle.srt", "elevenlabs-bgm-1.mp3"}
    # l'original est intact et archivé ; les entrées ne sont pas modifiées ; le rendu corrigé est distinct
    assert {p.name: _sha(p) for p in folder.iterdir() if p.is_file()} == before
    assert _sha(out / rr.ORIGINAL_COPY) == before["final-1.mp4"] and report["original"]["untouched"] is True
    assert (out / rr.REPAIRED).read_bytes().startswith(b"REPAIRED") and _sha(out / rr.REPAIRED) != before["final-1.mp4"]
    assert not list(out.glob("*.partial*")) and json.loads((out / rr.REPORT).read_text(encoding="utf-8"))["kind"] == "technical_repair"
    assert "l’  idée" in (folder / "subtitle.srt").read_text(encoding="utf-8")   # le SRT du moteur reste tel quel


def test_repair_is_idempotent_and_keeps_the_first_original_copy(tmp_path):
    folder = _task(tmp_path)
    first, _ = _repair(folder)
    second, _ = _repair(folder)
    assert first["original"]["sha256"] == second["original"]["sha256"] == _sha(folder / "final-1.mp4")
    assert _sha(folder / "repair" / rr.ORIGINAL_COPY) == first["original"]["sha256"]


def test_the_network_is_blocked_during_the_montage_and_restored_after(tmp_path):
    folder = _task(tmp_path)
    original = socket.create_connection
    with pytest.raises(RuntimeError, match="appel réseau interdit"):
        _repair(folder, FakeEngine(use_network=True))
    assert socket.create_connection is original and socket.getaddrinfo.__module__ == "socket"   # rétabli
    assert not (folder / "repair" / rr.REPAIRED).exists()                                          # rien de retenu
    assert (folder / "final-1.mp4").read_bytes().startswith(b"ORIGINAL")                          # original intact


@pytest.mark.parametrize("missing", ["combined-1.mp4", "audio.mp3", "subtitle.srt", "final-1.mp4"])
def test_missing_assets_are_reported_and_nothing_is_regenerated(tmp_path, missing):
    folder = _task(tmp_path, **{missing: None})
    engine = FakeEngine()
    with pytest.raises(rr.RepairError, match=missing):
        _repair(folder, engine)
    assert engine.calls == [] and not (folder / "repair").exists()


def test_failures_leave_no_repaired_file(tmp_path):
    folder = _task(tmp_path)
    with pytest.raises(rr.RepairError, match="musique"):
        _repair(folder, FakeEngine(bgm_ok=False))
    with pytest.raises(rr.RepairError, match="aucun fichier"):
        _repair(folder, FakeEngine(write=False))
    assert not (folder / "repair" / rr.REPAIRED).exists()


def test_a_tampered_archive_copy_or_a_missing_font_stops_the_repair(tmp_path):
    folder = _task(tmp_path)
    _repair(folder)
    (folder / "repair" / rr.ORIGINAL_COPY).write_bytes(b"autre chose")
    with pytest.raises(rr.RepairError, match="ne correspond pas"):
        _repair(folder)
    other = _task(tmp_path / "b")
    with pytest.raises(rr.RepairError, match="Police introuvable"):
        _repair(other, fonts_dir=tmp_path)


def test_a_production_without_burned_subtitles_is_not_repaired(tmp_path):
    folder = _task(tmp_path, **{"script.json": json.dumps({"params": {**PARAMS, "subtitle_enabled": False}})})
    with pytest.raises(rr.RepairError, match="pas de sous-titres"):
        _repair(folder)


# -- enregistrement dans Lody ----------------------------------------------------------------------------------------------------
@pytest.fixture
def registered(lody_env):
    projects = ProjectRepository(settings.db_path())
    projects.seed_defaults(SEED_PROJECTS)
    project = next(p for p in projects.list_projects() if p.name == "LodyCrypto")
    repo = ProductionRepository(settings.db_path())
    production = repo.create(project_id=project.id, subject="Explique la blockchain", provider="moneyprinterturbo",
                             status=S.TERMINEE, external_task_id=TASK, video_ref=f"tasks/{TASK}/final-1.mp4",
                             script="Un script.", assets=[{"kind": "audio", "ref": f"tasks/{TASK}/audio.mp3"}])
    storage = Path(settings.data_dir()).parent / "engine-storage"
    folder = storage / "tasks" / TASK
    folder.mkdir(parents=True)
    (folder / "final-1.mp4").write_bytes(b"ORIGINAL-RENDER" * 500)
    return repo, production, folder


def _fake_repair_in(folder: Path):
    for name, data in (("combined-1.mp4", b"c" * 400), ("audio.mp3", b"a" * 400), ("subtitle.srt", SRT.encode()),
                       ("elevenlabs-bgm-1.mp3", b"m" * 400), ("script.json", json.dumps({"params": PARAMS}).encode())):
        (folder / name).write_bytes(data)
    engine = FakeEngine()
    return rr.repair(folder, font="BeVietnamPro-Bold.ttf", engine_params=engine.params, generate_video=engine)


def test_register_adds_the_repaired_and_original_renders_without_touching_the_production(registered):
    repo, production, folder = registered
    report = _fake_repair_in(folder)
    before = repo.get(production.id)
    result = rr.register(production.id, report)
    after = repo.get(production.id)
    assert result["assets"] == ["audio", "repaired_video", "original_video"]
    refs = {a["kind"]: a["ref"] for a in after.assets}
    assert refs["repaired_video"] == f"tasks/{TASK}/repair/final-1-repaired.mp4"
    assert refs["original_video"] == f"tasks/{TASK}/repair/final-1.original.mp4"
    assert (after.status, after.video_ref, after.script, after.video_duration, after.version, after.external_task_id) == \
           (before.status, before.video_ref, before.script, before.video_duration, before.version, before.external_task_id)
    assert any("Rendu corrigé disponible" in w for w in after.warnings)
    assert len(repo.list_for_project(production.project_id)) == 1                    # aucune nouvelle production
    rr.register(production.id, report)                                               # idempotent
    again = repo.get(production.id)
    assert [a["kind"] for a in again.assets].count("repaired_video") == 1 and len(again.warnings) == len(after.warnings)


def test_register_refuses_foreign_reports_unfinished_productions_and_missing_files(registered):
    repo, production, folder = registered
    with pytest.raises(rr.RepairError, match="Fichier de réparation invalide"):
        rr.register(production.id, {"kind": "technical_repair", "task_id": TASK})       # rien n'a été produit
    report = _fake_repair_in(folder)
    with pytest.raises(rr.RepairError, match="ne correspond pas"):
        rr.register(production.id, {**report, "task_id": "autre-tache-123456"})
    with pytest.raises(rr.RepairError, match="ne correspond pas"):
        rr.register(production.id, {**report, "kind": "autre"})
    repo.update(production.id, status="ECHEC")
    with pytest.raises(rr.RepairError, match="terminée"):
        rr.register(production.id, report)
    with pytest.raises(LookupError):
        rr.register("prd_inconnue", report)


def test_repair_tool_is_stdlib_only_and_never_reads_config(monkeypatch):
    source = Path(rr.__file__).read_text(encoding="utf-8") + Path(typography.__file__).read_text(encoding="utf-8")
    for forbidden in ("requests", "urllib", "openai", "httpx", "config.toml", "api_key"):
        assert forbidden not in source, forbidden
