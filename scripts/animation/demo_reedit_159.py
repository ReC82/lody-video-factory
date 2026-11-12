"""Démonstration #159 : nouvelle version de la vidéo Voltix #150 ne modifiant QUE la scène 2, par la vraie chaîne.

Chaîne exécutée (environnement DEV isolé : base SQLite et dossier de données temporaires, aucun config.toml lu) :

    démo Voltix #150 (projet, catalogue, voix provisoire Piper, musique de test, storyboard gelé)
    → ProductionService.prepare/confirm → worker + moteur Remotion → V1 terminée
    → create_v2 → scene_edit.edit_scene (scène 2 : geste et expression, comme l'écran « Modifier cette scène »)
    → prepare_animation_v2 (regel) → plan de réédition → [aperçu ½] → confirm → V2 terminée
    → contrôles : V2 ne rend que la scène 2, ``scenes_reused == 2``, ``render_ms_saved > 0``, V1 intacte.

Aucun appel payant (voix Piper locale, jamais ElevenLabs), aucune donnée de production.

Usage :
    python scripts/animation/demo_reedit_159.py --out /tmp/voltix-159 \\
        --piper-model /chemin/fr_FR-upmc-medium.onnx [--preview]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT), str(Path(__file__).resolve().parent)]

import demo_voltix_150 as demo  # noqa: E402
import musique_test  # noqa: E402

from lody.animation import audio, modes, reedit, scene_edit  # noqa: E402
from lody.animation import worker as w  # noqa: E402
from lody.animation.assets import AnimationAssetRepository  # noqa: E402
from lody.animation.contract import validate_document  # noqa: E402
from lody.animation.freeze import remap_assets  # noqa: E402
from lody.characters import CharacterRepository  # noqa: E402
from lody.generation import reedit_v2  # noqa: E402
from lody.generation.animation_connector import AnimationConnector  # noqa: E402
from lody.generation.costing import PriceBook  # noqa: E402
from lody.generation.models import ProductionStatus  # noqa: E402
from lody.generation.service import ProductionService  # noqa: E402
from lody.generation.store import ProductionRepository  # noqa: E402
from lody.projects import ProjectRepository  # noqa: E402

PROVIDER = "animation_2d"
SCENE = "coaxial"  # scène 2


class Inline:
    """Exécuteur synchrone : la production tourne dans ce processus (démonstration)."""

    def submit(self, fn, *args):
        return fn(*args)


def edit_of_scene_2(document: dict, assets: AnimationAssetRepository, project_id: str) -> dict:
    """Ce que ferait le propriétaire dans « Modifier cette scène » : autre geste, autre expression pour Voltix."""
    summary = next(item for item in scene_edit.summarize(document) if item.id == SCENE)
    voltix = summary.characters[0]
    manifest = assets.get_version(project_id, voltix.asset_id, voltix.version).manifest
    gestures = [name for name in manifest.get("gestures") or [] if name != voltix.gestures[0][2]]
    expressions = [name for name in manifest.get("expressions") or [] if name != voltix.expressions[0][1]]
    changes = {"gestures": {(voltix.key, 0): "saluer" if "saluer" in gestures else gestures[0]},
               "expressions": {(voltix.key, 0): "surprise" if "surprise" in expressions else expressions[0]}}
    return scene_edit.edit_scene(document, SCENE, changes), changes


def run(out: Path, model: Path, tts: list[str], renderer: str, preview: bool, keep: Path | None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    work = keep or Path(tempfile.mkdtemp(prefix="lody-159-"))
    data_dir, db = work / "data", work / "lody.sqlite3"
    work.mkdir(parents=True, exist_ok=True)
    config = work / "lody-demo-vide.toml"  # configuration vide : aucun fichier réel n'est lu
    config.write_text("", encoding="utf-8")
    os.environ.update({"LODY_DATA_DIR": str(data_dir), "LODY_CONFIG_PATH": str(config)})
    # Démo DEV d'évaluation (#154/#156) : la licence Remotion n'est pas décidée, rien n'est publié.
    os.environ.setdefault("LODY_REMOTION_EVALUATION", "1")
    shutil.copytree(ROOT / "resource" / "animation-assets", data_dir / "animation-assets", dirs_exist_ok=True)

    projects, assets = ProjectRepository(db), AnimationAssetRepository(db)
    project = projects.create(name="Voltix — réédition (#159)", settings={modes.SETTINGS_KEY: modes.ANIMATION})
    voltix_character = CharacterRepository(db).create(project.id, name="Voltix")
    voice_plan = audio.voice_plan(voltix_character)
    catalog = json.loads((ROOT / "resource/animation-assets/voltix-catalogue.json").read_text(encoding="utf-8"))
    id_map, _ = assets.import_catalog(project.id, catalog)
    coax = assets.create(project.id, kind="prop", name="Câble coaxial 75 Ω — coupe")
    assets.add_version(project.id, coax.id, demo.coax_manifest(data_dir), notes="Schéma technique #150")

    # Voix provisoire locale (Piper), comme la démo #150, et musique de test générée.
    clips, rate = {}, 22050
    for scene_id, lines in demo.LINES.items():
        clips[scene_id] = []
        for i, (_, spoken) in enumerate(lines):
            clip, rate = demo.synthesize(tts, model, spoken, work / f"tts-{scene_id}-{i}.wav")
            clips[scene_id].append(clip)
    scenes, voice = demo.layout(clips, rate)
    builders = {"accueil": demo.scene_accueil, "coaxial": demo.scene_coaxial, "bnc": demo.scene_bnc}
    raw = {"schema": "lody.animation/1", "canvas": {"fps": demo.FPS, "width": demo.WIDTH, "height": demo.HEIGHT},
           "scenes": [builders[sid](scenes[sid], coax.id) for sid in demo.LINES]}
    validate_document(raw)
    raw = remap_assets(raw, {**id_map, coax.id: coax.id})
    sound = work / "sound"
    sound.mkdir(exist_ok=True)
    demo.write_wav(sound / audio.VOICE_FILE, voice, rate)
    total = sum(s["duration_frames"] for s in raw["scenes"]) / demo.FPS
    musique_test.write(sound / audio.MUSIC_FILE, total + 1)
    (sound / audio.INFO_FILE).write_text(json.dumps({
        "voice": {"provisional": True, "source": "Piper fr_FR-upmc-medium (locuteur pierre), synthèse locale",
                  "license": "CC BY-SA 4.0 (modèle)", "note": voice_plan["message"]},
        "music": {"provisional": True, "source": "scripts/animation/musique_test.py (générée)",
                  "license": "aucune piste tierce"}}, ensure_ascii=False), encoding="utf-8")

    timings: dict[str, float] = {}

    def launch(directory: Path) -> None:
        # Même bande son pour V1, aperçu et V2 (la voix n'est pas encore branchée au fournisseur : #164).
        for name in (audio.VOICE_FILE, audio.MUSIC_FILE, audio.INFO_FILE):
            shutil.copyfile(sound / name, directory / name)
        started = time.monotonic()
        w.run_job(directory, w.CommandRenderer(shlex.split(renderer)))
        timings[directory.name] = round(time.monotonic() - started, 1)

    connector = AnimationConnector(root=data_dir, asset_repo=assets, launcher=launch, renderer=renderer)
    service = ProductionService(ProductionRepository(db), {PROVIDER: connector}, Inline(),
                                price_book=lambda: PriceBook("EUR", {}), animation_asset_repo=assets)

    # V1
    draft = service.prepare(project, "Voltix explique le câble SDI", provider_id=PROVIDER, animation=raw)
    service.confirm(draft.id)
    v1 = service.refresh(draft.id)
    if v1.status is not ProductionStatus.TERMINEE:
        raise SystemExit(f"V1 en échec : {v1.error_message}")
    v1_video = service.resolve_video(v1)
    v1_sha = w.sha256_file(v1_video)

    # V2 : uniquement la scène 2, par les fonctions de l'écran « Nouvelle version ».
    v2_draft = service.create_v2(v1.id)
    edited, changes = edit_of_scene_2(v2_draft.animation, assets, project.id)
    v2_draft = service.prepare_animation_v2(v2_draft.id, edited)
    plan = reedit_v2.plan_for(service, v2_draft)
    result: dict = {"plan": reedit.describe(plan), "changes": {f"{k}": {str(a): b for a, b in v.items()}
                                                                 for k, v in changes.items()}}
    if preview:
        reedit_v2.start_preview(service, v2_draft)
        state = reedit_v2.preview_state(service, v2_draft)
        result["preview"] = {"state": state.state, "error": state.error}
        if state.video:
            shutil.copyfile(state.video, out / "voltix-159-apercu.mp4")
            manifest = w.read_json(state.video.parent / "manifest.json") or {}
            result["preview"].update(width=manifest.get("width"), height=manifest.get("height"),
                                     audit=manifest.get("audit"))
    service.confirm(v2_draft.id)
    v2 = service.refresh(v2_draft.id)
    if v2.status is not ProductionStatus.TERMINEE:
        raise SystemExit(f"V2 en échec : {v2.error_message}")
    data = reedit_v2.comparison(service, v2)

    v1_after = service.repo.get(v1.id)
    checks = {
        "v2_scenes_reused": data["scenes_reused"], "v2_scenes_rendered": data["scenes_rendered"],
        "v2_render_ms_saved": data["render_ms_saved"],
        "v2_rendered_scenes": data["audit"].get("rendered_scenes"),
        "v1_video_unchanged": w.sha256_file(v1_video) == v1_sha,
        "v1_storyboard_unchanged": v1_after.animation == v1.animation and v1_after.status is ProductionStatus.TERMINEE,
        "qa": data["qa"], "qa_problems": [item["message"] for item in data["qa_checks"]
                                          if item.get("level") in ("warning", "error")],
    }
    ok = (data["scenes_reused"] == 2 and data["render_ms_saved"] > 0 and data["audit"].get("rendered_scenes") == [2]
          and checks["v1_video_unchanged"] and checks["v1_storyboard_unchanged"])
    shutil.copyfile(v1_video, out / "voltix-159-v1.mp4")
    shutil.copyfile(service.resolve_video(v2), out / "voltix-159-v2.mp4")
    for name, production in (("v1", v1), ("v2", v2)):
        manifest = reedit_v2.manifest_of(service, production) or {}
        (out / f"manifest-{name}.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "storyboard-v2.json").write_text(json.dumps(copy.deepcopy(v2.animation), ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    result.update(ok=ok, checks=checks, timings_s=timings, work=str(work))
    (out / "demo.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--piper-model", type=Path, required=True)
    parser.add_argument("--tts", default="uvx --from piper-tts piper")
    parser.add_argument("--renderer", default=f"node {ROOT / 'renderers/remotion/bin/lody-remotion.mjs'}")
    parser.add_argument("--preview", action="store_true", help="lancer aussi l'aperçu rapide ½ de la V2")
    parser.add_argument("--work", type=Path, default=None, help="dossier de travail conservé (sinon temporaire)")
    args = parser.parse_args()
    result = run(args.out, args.piper_model, shlex.split(args.tts), args.renderer, args.preview, args.work)
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
