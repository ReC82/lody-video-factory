"""Démonstration #150 : premier MP4 de Voltix parlant, produit par la VRAIE chaîne du rendu 2D.

Chaîne exécutée (environnement DEV isolé, base SQLite et dossier de données temporaires) :

    projet + fiche « Voltix » → import du catalogue Voltix (#136) + schéma coaxial → storyboard lody.animation/1
    → gel (freeze_document, #119) → AnimationConnector.preflight/submit (#120) → worker run_job (#120)
    → moteur de scène Remotion renderers/remotion (#150), une scène à la fois → mixage voix + musique → out.mp4

Voix : la fiche Voltix n'a pas de voix choisie (aucune ``external_voice_id``) → voix PROVISOIRE locale et
gratuite (Piper, modèle français ``fr_FR-upmc-medium``, locuteur « pierre », CC BY-SA 4.0), jamais ElevenLabs.
Musique : générée par ``musique_test.py`` (aucune piste tierce). Aucun appel payant, aucune donnée de production.

Usage :
    python scripts/animation/demo_voltix_150.py --out /tmp/voltix-150 \\
        --piper-model /chemin/fr_FR-upmc-medium.onnx [--tts "uvx --from piper-tts piper"]
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT)]

from lody.animation import audio, modes  # noqa: E402
from lody.animation import worker as w  # noqa: E402
from lody.animation.assets import AnimationAssetRepository, validate_manifest  # noqa: E402
from lody.animation.contract import validate_document  # noqa: E402
from lody.animation.freeze import freeze_document, remap_assets  # noqa: E402
from lody.characters import CharacterRepository  # noqa: E402
from lody.generation.animation_connector import AnimationConnector  # noqa: E402
from lody.generation.service import build_request  # noqa: E402
from lody.projects import ProjectRepository  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import musique_test  # noqa: E402

FPS, WIDTH, HEIGHT = 30, 1080, 1920
VOLTIX, CABLE, BNC_M, BNC_F = "ast_136000000001", "ast_136000000002", "ast_136000000003", "ast_136000000004"
LEAD, GAP = 14, 8  # images avant la première réplique d'une scène, entre deux répliques

# (texte affiché en sous-titre, texte lu par la synthèse) : sigles épelés phonétiquement (TTS-safe).
LINES = {
    "accueil": [("Salut, moi c'est Voltix !", "Salut, moi c'est Voltix !"),
                ("Aujourd'hui : le câble SDI.", "Aujourd'hui : le câble, èsse dé i.")],
    "coaxial": [("Le signal vidéo voyage dans un câble coaxial…", "Le signal vidéo voyage dans un câble coaxial,"),
                ("… d'une impédance de 75 ohms.", "d'une impédance de soixante-quinze ohms.")],
    "bnc": [("Au bout : une fiche BNC.", "Au bout : une fiche, bé ène cé."),
            ("On pousse, on tourne d'un quart de tour…", "On pousse, on tourne d'un quart de tour,"),
            ("Clic ! C'est verrouillé.", "Clic ! C'est verrouillé !")],
}
TAIL = {"accueil": 16, "coaxial": 14, "bnc": 40}


# -- voix ------------------------------------------------------------------------------------------------------
def synthesize(tts: list[str], model: Path, text: str, target: Path) -> tuple[np.ndarray, int]:
    source = target.with_suffix(".txt")
    source.write_text(text + "\n", encoding="utf-8")
    subprocess.run([*tts, "-m", str(model), "-s", "1", "--length-scale", "0.92", "--sentence-silence", "0",
                    "-i", str(source), "-f", str(target)], check=True, capture_output=True, timeout=300)
    with wave.open(str(target), "rb") as handle:
        rate = handle.getframerate()
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2").astype(np.float32) / 32768
        if handle.getnchannels() > 1:
            data = data.reshape(-1, handle.getnchannels()).mean(axis=1)
    # Rognage des silences de bord (seuil -45 dB) : les repères de parole collent à la voix.
    loud = np.flatnonzero(np.abs(data) > 0.0056)
    if loud.size:
        data = data[max(0, loud[0] - int(0.02 * rate)): loud[-1] + int(0.05 * rate)]
    return data, rate


def layout(clips: dict[str, list[np.ndarray]], rate: int) -> tuple[dict[str, dict], np.ndarray]:
    """Durée de chaque scène et repères de parole (en images) d'après les durées réelles de la voix."""
    scenes, track, offset = {}, [], 0
    for scene_id, items in clips.items():
        cues, frame = [], LEAD
        for (display, _), clip in zip(LINES[scene_id], items):
            frames = int(np.ceil(len(clip) / rate * FPS))
            cues.append({"text": display, "start": frame, "end": frame + frames})
            frame += frames + GAP
        duration = frame - GAP + TAIL[scene_id]
        scenes[scene_id] = {"duration": duration, "cues": cues, "offset": offset}
        offset += duration
    total = np.zeros(int(np.ceil(offset / FPS * rate)) + rate, dtype=np.float32)
    for scene_id, items in clips.items():
        for cue, clip in zip(scenes[scene_id]["cues"], items):
            start = int(round((scenes[scene_id]["offset"] + cue["start"]) / FPS * rate))
            total[start:start + len(clip)] += clip
    track.append(total[: int(np.ceil(offset / FPS * rate))])
    return scenes, track[0]


def write_wav(path: Path, data: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(data, -1, 1) * 32767).astype("<i2").tobytes())


# -- storyboard --------------------------------------------------------------------------------------------------
def k(frame: int, **values) -> dict:
    return {"frame": frame, **values}


def cam(frame: int, x: int, y: int, zoom: float) -> dict:
    return {"frame": frame, "x": x, "y": y, "zoom": zoom, "rotation": 0}


def asset(obj_id: str, asset_id: str, position: list, **extra) -> dict:
    return {"id": obj_id, "type": "asset", "asset": {"id": asset_id, "version": "latest"}, "position": position,
            **extra}


def text(obj_id: str, content: str, style: str, position: list, **extra) -> dict:
    return {"id": obj_id, "type": "text", "text": {"content": content, "style": style}, "position": position,
            **extra}


def scene_accueil(s: dict, coax_id: str) -> dict:
    d, (c1, c2) = s["duration"], s["cues"]
    voltix = asset("voltix", VOLTIX, [k(0, x=540, y=1480)], scale=[k(0, value=1.22)],
                   expressions=[k(0, name="sourire")],
                   gestures=[{"start": 0, "end": 28, "name": "rebond"},
                             {"start": 30, "end": 60, "name": "saluer"},
                             {"start": max(62, c2["start"]), "end": min(d, c2["end"] + 4), "name": "montrer"}],
                   lipsync=True)
    title = text("titre", "Le câble SDI", "title", [k(0, x=540, y=250)], start=c2["start"] - 4,
                 scale=[k(c2["start"] - 4, value=0.4), k(c2["start"] + 10, value=1.0)], easing="spring")
    sub = text("sous-titre", "BNC · coaxial 75 Ω", "subtitle", [k(0, x=540, y=380)], start=c2["start"] + 8,
               opacity=[k(c2["start"] + 8, value=0), k(c2["start"] + 20, value=1)])
    return {"id": "accueil", "duration_frames": d,
            "intent": {"narration": " ".join(t for t, _ in LINES["accueil"]), "purpose": "Accroche",
                       "mood": "enjoué"},
            "render": {"layers": [{"id": "textes", "objects": [title, sub]},
                                  {"id": "personnages", "objects": [voltix]}],
                       "camera": {"keyframes": [cam(0, 540, 960, 1.0), cam(d, 540, 930, 1.06)]},
                       "speech": {"speaker": "voltix", "cues": s["cues"]},
                       "transition_in": {"type": "fade", "frames": 10},
                       "fallback": {"type": "still", "frame": c1["start"] + 10}}}


def scene_coaxial(s: dict, coax_id: str) -> dict:
    d, (c3, c4) = s["duration"], s["cues"]
    step = max(6, (c3["end"] - c3["start"]) // 5)
    t = [c3["start"] + i * step for i in range(4)]
    # Étiquettes alternées au-dessus / au-dessous du câble, centrées sur leur couche et sans chevauchement
    # (coupe à l'échelle 1,05 centrée en x = 540 : gaine ≈ 277, tresse ≈ 575, isolant ≈ 773, âme ≈ 935).
    labels = [("l-ame", "Âme en cuivre", 860, 430, t[0]), ("l-isolant", "Isolant", 860, 800, t[1]),
              ("l-tresse", "Tresse (blindage)", 480, 800, t[2]), ("l-gaine", "Gaine", 277, 430, t[3])]
    texts = [text(i, c, "label", [k(0, x=x, y=y)], start=f,
                  scale=[k(f, value=0.5), k(f + 8, value=1.0)], easing="spring") for i, c, x, y, f in labels]
    texts.append(text("ohms", "75 Ω", "title", [k(0, x=300, y=1040)], start=c4["start"] + 6,
                      scale=[k(c4["start"] + 6, value=0.3), k(c4["start"] + 20, value=1.15)], easing="spring"))
    coax = asset("coax", coax_id, [k(0, x=-520, y=610), k(16, x=540, y=610)], scale=[k(0, value=1.05)],
                 easing="ease_out")
    voltix = asset("voltix", VOLTIX, [k(0, x=800, y=1490)], scale=[k(0, value=0.8)],
                   expressions=[k(0, name="neutre"), k(c4["start"], name="sourire")],
                   gestures=[{"start": c3["start"], "end": c4["start"] - 2, "name": "montrer"},
                             {"start": c4["start"], "end": min(d, c4["start"] + 28), "name": "rebond"}],
                   lipsync=True)
    return {"id": "coaxial", "duration_frames": d,
            "intent": {"narration": " ".join(t for t, _ in LINES["coaxial"]), "purpose": "Expliquer le câble"},
            "render": {"layers": [{"id": "schema", "objects": [coax]}, {"id": "etiquettes", "objects": texts},
                                  {"id": "personnages", "objects": [voltix]}],
                       "camera": {"keyframes": [cam(0, 540, 960, 1.0), cam(c3["end"], 540, 960, 1.03),
                                                cam(d, 540, 960, 1.0)]},
                       "speech": {"speaker": "voltix", "cues": s["cues"]},
                       "transition_in": {"type": "slide_left", "frames": 12},
                       "fallback": {"type": "still", "frame": c4["start"] + 20}}}


def scene_bnc(s: dict, coax_id: str) -> dict:
    d, (c5, c6, c7) = s["duration"], s["cues"]
    push, turn = c6["start"] + 4, c6["start"] + (c6["end"] - c6["start"]) // 2
    male = asset("bnc-male", BNC_M, [k(0, x=-420, y=600), k(c5["start"] + 18, x=250, y=600),
                                     k(push, x=250, y=600), k(push + 10, x=420, y=600)],
                 scale=[k(0, value=1.3)], easing="ease_out")
    femelle = asset("bnc-femelle", BNC_F, [k(0, x=1500, y=600), k(c5["start"] + 12, x=680, y=600)],
                    scale=[k(0, value=1.3)], easing="ease_out")
    labels = [text("l-male", "BNC mâle", "label", [k(0, x=250, y=790)], start=c5["start"] + 18),
              text("l-femelle", "BNC femelle · 75 Ω", "label", [k(0, x=760, y=790)], start=c5["start"] + 12),
              text("quart", "↻ ¼ de tour", "subtitle", [k(0, x=300, y=1000)], start=turn, end=c7["start"] + 4,
                   rotation=[k(turn, value=-20), k(turn + 14, value=0)], easing="spring"),
              text("clic", "Clic !", "title", [k(0, x=560, y=400)], start=c7["start"],
                   scale=[k(c7["start"], value=0.3), k(c7["start"] + 12, value=1.3)],
                   rotation=[k(c7["start"], value=-8)], easing="spring")]
    voltix = asset("voltix", VOLTIX, [k(0, x=810, y=1490)], scale=[k(0, value=0.8)],
                   expressions=[k(0, name="neutre"), k(c6["start"], name="concentration"), k(c7["start"], name="surprise"),
                                k(c7["start"] + 14, name="sourire")],
                   gestures=[{"start": c5["start"], "end": c6["end"], "name": "montrer"},
                             {"start": c7["start"], "end": c7["start"] + 28, "name": "rebond"},
                             {"start": c7["start"] + 30, "end": min(d, c7["start"] + 60), "name": "saluer"}],
                   lipsync=True)
    return {"id": "bnc", "duration_frames": d,
            "intent": {"narration": " ".join(t for t, _ in LINES["bnc"]), "purpose": "Montrer le verrouillage BNC"},
            "render": {"layers": [{"id": "connecteurs", "objects": [femelle, male]},
                                  {"id": "etiquettes", "objects": labels},
                                  {"id": "personnages", "objects": [voltix]}],
                       "camera": {"keyframes": [cam(0, 540, 960, 1.0), cam(push + 10, 540, 960, 1.04),
                                                cam(c7["end"], 540, 960, 1.0)]},
                       "speech": {"speaker": "voltix", "cues": s["cues"]},
                       "transition_in": {"type": "slide_left", "frames": 12},
                       "transition_out": {"type": "fade", "frames": 12},
                       "fallback": {"type": "still", "frame": c7["start"] + 10}}}


def coax_manifest(data_dir: Path) -> dict:
    path = "animation-assets/coax-coupe/v1/coax-coupe.svg"
    return validate_manifest({"format": "svg", "width": 900, "height": 300, "anchor": {"x": 0.5, "y": 0.5},
                              "files": [{"role": "base", "path": path,
                                         "sha256": w.sha256_file(data_dir / path)}]})


# -- exécution ---------------------------------------------------------------------------------------------------
def run(out: Path, model: Path, tts: list[str], renderer: str, keep: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    work = keep or Path(tempfile.mkdtemp(prefix="lody-150-"))
    data_dir, db = work / "data", work / "lody.sqlite3"
    shutil.copytree(ROOT / "resource" / "animation-assets", data_dir / "animation-assets", dirs_exist_ok=True)

    projects, assets = ProjectRepository(db), AnimationAssetRepository(db)
    project = projects.create(name="Voltix — démo SDI (#150)", settings={modes.SETTINGS_KEY: modes.ANIMATION})
    voltix = CharacterRepository(db).create(project.id, name="Voltix")
    plan = audio.voice_plan(voltix)
    catalog = json.loads((ROOT / "resource/animation-assets/voltix-catalogue.json").read_text(encoding="utf-8"))
    id_map, warnings = assets.import_catalog(project.id, catalog)
    coax = assets.create(project.id, kind="prop", name="Câble coaxial 75 Ω — coupe")
    assets.add_version(project.id, coax.id, coax_manifest(data_dir), notes="Schéma technique #150")

    # 1. Voix provisoire (une synthèse par réplique) et mise en page des repères de parole.
    started = time.monotonic()
    clips, rate = {}, 22050
    for scene_id, lines in LINES.items():
        clips[scene_id] = []
        for i, (_, spoken) in enumerate(lines):
            clip, rate = synthesize(tts, model, spoken, work / f"tts-{scene_id}-{i}.wav")
            clips[scene_id].append(clip)
    scenes, voice = layout(clips, rate)
    tts_seconds = round(time.monotonic() - started, 1)

    # 2. Storyboard, validation, gel.
    builders = {"accueil": scene_accueil, "coaxial": scene_coaxial, "bnc": scene_bnc}
    raw = {"schema": "lody.animation/1", "canvas": {"fps": FPS, "width": WIDTH, "height": HEIGHT},
           "scenes": [builders[sid](scenes[sid], coax.id) for sid in LINES]}
    validate_document(raw)
    storyboard = freeze_document(assets, project.id, remap_assets(raw, {**id_map, coax.id: coax.id}))
    total_seconds = sum(s["duration_frames"] for s in storyboard["scenes"]) / FPS

    # 3. Bande son : voix provisoire + musique de test générée.
    sound = work / "sound"
    sound.mkdir(exist_ok=True)
    write_wav(sound / audio.VOICE_FILE, voice, rate)
    musique_test.write(sound / audio.MUSIC_FILE, total_seconds + 1)
    (sound / audio.INFO_FILE).write_text(json.dumps({
        "voice": {"provisional": True, "source": "Piper fr_FR-upmc-medium (locuteur pierre), synthèse locale",
                  "license": "CC BY-SA 4.0 (modèle)", "note": plan["message"]},
        "music": {"provisional": True, "source": "scripts/animation/musique_test.py (générée)",
                  "license": "aucune piste tierce"}}, ensure_ascii=False), encoding="utf-8")

    # 4. Fournisseur Animation 2D (#120) : préflight, dépôt de la tâche, worker avec le moteur Remotion.
    def launch(directory: Path) -> None:
        for name in (audio.VOICE_FILE, audio.MUSIC_FILE, audio.INFO_FILE):
            shutil.copyfile(sound / name, directory / name)
        w.run_job(directory, w.CommandRenderer(shlex.split(renderer)))

    connector = AnimationConnector(root=data_dir, asset_repo=assets, launcher=launch, renderer=renderer)
    request = build_request(project, "Voltix explique le câble SDI").with_updates(
        render_mode=modes.ANIMATION, animation={"project_id": project.id, "storyboard": storyboard})
    preflight = connector.preflight(request)
    blocking = [item.message for item in preflight.items if item.state.name not in ("READY", "NOT_NEEDED", "DISABLED")]
    if blocking:
        raise SystemExit("préflight bloquant : " + " | ".join(blocking))
    render_started = time.monotonic()
    task = connector.submit(request, "demo-150-voltix")
    directory = w.find_job_dir(data_dir, task.task_id)
    state = w.read_json(directory / "state.json") or {}
    if state.get("state") != "done":
        log = (directory / "worker.log").read_text(encoding="utf-8") if (directory / "worker.log").is_file() else ""
        raise SystemExit(f"rendu en échec : {state.get('error')}\n{log}")
    render_seconds = round(time.monotonic() - render_started, 1)

    for name in ("out.mp4", "manifest.json", "subtitles.srt", "job.json"):
        shutil.copyfile(directory / name, out / ("voltix-150.mp4" if name == "out.mp4" else name))
    (out / "storyboard.json").write_text(json.dumps(storyboard, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {"task": task.task_id, "job_dir": str(directory), "tts_seconds": tts_seconds,
               "render_seconds": render_seconds, "duration_seconds": total_seconds, "voice_plan": plan,
               "catalog_warnings": warnings,
               "cues": {sid: scenes[sid]["cues"] for sid in scenes}}
    (out / "demo.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--piper-model", type=Path, required=True)
    parser.add_argument("--tts", default="uvx --from piper-tts piper")
    parser.add_argument("--renderer", default=f"node {ROOT / 'renderers/remotion/bin/lody-remotion.mjs'}")
    parser.add_argument("--work", type=Path, default=None, help="dossier de travail conservé (sinon temporaire)")
    args = parser.parse_args()
    summary = run(args.out, args.piper_model, shlex.split(args.tts), args.renderer, args.work)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
