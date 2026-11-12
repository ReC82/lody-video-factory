"""Recette contrôlée du rendu 2D parlé (#187) : estimation du coût ElevenLabs et verdict PASS/FAIL, sans appel.

Deux usages, entièrement locaux (aucun réseau, aucune clé, aucune écriture) :

- ``estimate`` : caractères qu'ElevenLabs facturerait pour une suite de storyboards V1 → V2 → V3 (seuls les
  textes parlés jamais synthétisés sont payés, comme dans ``voiceover.billable_characters``), le pire cas (le
  moteur relance jusqu'à ``ENGINE_ATTEMPTS`` fois un appel en échec, et une réponse 200 est facturée même si
  l'audio se révèle illisible) et la comparaison au plafond accordé par le propriétaire ;
- ``verify`` : lit les dossiers de rendu des trois versions (``manifest.json``, ``voice-timing.json``,
  ``subtitles.srt``, ``voice.wav``) et rend un verdict par critère du protocole (``docs/lody-recette-dev-2d.md``).

    python -m lody.animation.recette estimate v1.json [v2.json v3.json] [--cap 1000] [--json]
    python -m lody.animation.recette verify --dirs <V1> <V2> <V3> [--json]
    python -m lody.animation.recette verify --productions <prd_V1> <prd_V2> <prd_V3> [--json]

L'estimation compte des CARACTÈRES (unité de facturation ElevenLabs) : la facture réelle se lit sur le tableau de
bord ElevenLabs, jamais ici.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ENGINE_ATTEMPTS = 3          # ``app/services/voice.py::elevenlabs_tts`` : 3 essais sur 5xx / exception
DEFAULT_CAP = 1000           # plafond proposé pour toute la recette, en caractères (voir la doc)
TIMING_FILE = "voice-timing.json"
VOICE_FILE = "voice.wav"


# -- estimation ------------------------------------------------------------------------------------------------
def spoken_scenes(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
    """Scènes parlées : texte envoyé à la voix (repères joints par une espace, comme ``SceneVoice.spoken``)."""
    found = []
    for index, scene in enumerate(storyboard.get("scenes") or [], 1):
        speech = (scene.get("render") or {}).get("speech") or {}
        cues = [str(cue.get("text", "")) for cue in speech.get("cues") or []]
        if cues:
            text = " ".join(cues)
            found.append({"index": index, "id": str(scene.get("id", "")), "speaker": str(speech.get("speaker", "")),
                          "text": text, "characters": len(text)})
    return found


def estimate(storyboards: list[dict[str, Any]], cap: int = DEFAULT_CAP) -> dict[str, Any]:
    """Caractères facturables par version, cache de voix vide au départ et même voix pour toutes les versions."""
    paid: set[tuple[str, str]] = set()
    steps = []
    for number, storyboard in enumerate(storyboards, 1):
        billed = []
        for scene in spoken_scenes(storyboard):
            key = (scene["speaker"], scene["text"])
            if key not in paid:
                paid.add(key)
                billed.append(scene)
        steps.append({"version": f"V{number}", "calls": len(billed), "characters": sum(s["characters"] for s in billed),
                      "scenes": [s["index"] for s in billed], "texts": [s["text"] for s in billed]})
    total = sum(step["characters"] for step in steps)
    worst = total * ENGINE_ATTEMPTS
    return {"steps": steps, "calls": sum(step["calls"] for step in steps), "characters": total,
            "worst_case_characters": worst, "cap": cap, "within_cap": worst <= cap}


# -- vérification ------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Verdict:
    criterion: str
    ok: bool
    detail: str


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _job(directory: Path) -> dict[str, Any]:
    manifest = _read(directory / "manifest.json")
    timing = _read(directory / TIMING_FILE).get("scenes") or {}
    scenes = {int(t.get("index", 0)): t for t in manifest.get("scenes") or [] if isinstance(t, dict)}
    seconds = None
    try:
        with wave.open(str(directory / VOICE_FILE)) as handle:
            seconds = handle.getnframes() / handle.getframerate()
    except (OSError, wave.Error, EOFError):
        pass
    try:
        srt = (directory / "subtitles.srt").read_text(encoding="utf-8")
    except OSError:
        srt = ""
    state = _read(directory / "state.json").get("state", "")
    return {"manifest": manifest, "timing": timing if isinstance(timing, dict) else {}, "scenes": scenes,
            "voice_seconds": seconds, "srt": srt, "state": state, "job": _read(directory / "job.json")}


def _key(job: dict[str, Any], index: int) -> str:
    entry = job["timing"].get(str(index))
    return str(entry.get("key", "")) if isinstance(entry, dict) else ""


def _cue_texts(job: dict[str, Any], index: int) -> list[str]:
    entry = job["timing"].get(str(index))
    return [str(c.get("text", "")) for c in (entry.get("cues") or [])] if isinstance(entry, dict) else []


def _cache(job: dict[str, Any], index: int) -> str:
    return str(job["scenes"].get(index, {}).get("cache", "absente"))


def _version_checks(label: str, job: dict[str, Any]) -> list[Verdict]:
    manifest = job["manifest"]
    voice = (manifest.get("audio") or {}).get("voice") or {}
    fps = int(manifest.get("fps") or 0)
    expected = float(manifest.get("expected_seconds") or 0)
    spoken = [scene["index"] for scene in spoken_scenes((job["job"].get("storyboard") or {}))]
    checks = [
        Verdict(f"{label} : rendu terminé", job["state"] == "done" and bool(manifest),
                f"état « {job['state'] or 'inconnu'} »"),
        Verdict(f"{label} : voix ElevenLabs réelle", voice.get("source") == "ElevenLabs" and not voice.get("provisional", True),
                f"source « {voice.get('source', 'aucune')} »"),
        Verdict(f"{label} : piste voix de la durée de la vidéo",
                job["voice_seconds"] is not None and abs(job["voice_seconds"] - expected) < 0.05,
                f"voix {job['voice_seconds'] if job['voice_seconds'] is not None else 'absente'} s, vidéo {expected} s"),
        Verdict(f"{label} : repères recalés pour chaque scène parlée",
                bool(spoken) and all(_cue_texts(job, i) for i in spoken), f"scènes parlées {spoken}"),
        Verdict(f"{label} : sous-titres présents", all(t in job["srt"] for i in spoken for t in _cue_texts(job, i)),
                "chaque réplique recalée apparaît dans subtitles.srt"),
        Verdict(f"{label} : contrôle qualité sans erreur", (manifest.get("qa") or {}).get("status") != "error",
                f"statut « {(manifest.get('qa') or {}).get('status', 'inconnu')} »"),
    ]
    if fps:
        bounds = all(0 <= c.get("start", -1) < c.get("end", -1) <= int(t.get("frames", 0))
                     for i, t in job["scenes"].items()
                     for c in (job["timing"].get(str(i), {}).get("cues") or []))
        checks.append(Verdict(f"{label} : bouche (visèmes) dans la scène", bounds,
                              "chaque repère recalé tient dans sa scène"))
    return checks


def verify(v1: Path, v2: Path, v3: Path, changed: int = 2, visual: int = 1) -> list[Verdict]:
    """Critères du protocole : V2 = réplique de la scène ``changed`` modifiée, V3 = visuel de la scène ``visual``."""
    one, two, three = _job(Path(v1)), _job(Path(v2)), _job(Path(v3))
    verdicts = [*_version_checks("V1", one), *_version_checks("V2", two), *_version_checks("V3", three)]
    others = [i for i in one["scenes"] if i != changed]
    verdicts += [
        Verdict(f"V2 : voix de la scène {changed} re-synthétisée (nouvelle empreinte)",
                bool(_key(two, changed)) and _key(two, changed) != _key(one, changed),
                "empreinte texte+voix différente de la V1"),
        Verdict(f"V2 : réplique de la scène {changed} modifiée", _cue_texts(two, changed) != _cue_texts(one, changed),
                " / ".join(_cue_texts(two, changed))[:120]),
        Verdict("V2 : voix des autres scènes inchangée (aucun appel)",
                all(_key(two, i) == _key(one, i) for i in others), f"scènes {others}"),
        Verdict(f"V2 : seule la scène {changed} est recalculée",
                _cache(two, changed) == "miss" and all(_cache(two, i) == "hit" for i in others),
                ", ".join(f"scène {i} : {_cache(two, i)}" for i in sorted(two["scenes"]))),
        Verdict("V3 : aucune resynthèse (toutes les voix déjà payées en V2)",
                bool(three["timing"]) and all(_key(three, int(i)) == _key(two, int(i)) for i in three["timing"])
                and set(three["timing"]) == set(two["timing"]), "empreintes de voix identiques à la V2"),
        Verdict(f"V3 : seule la scène {visual} est recalculée",
                _cache(three, visual) == "miss" and all(_cache(three, i) == "hit" for i in three["scenes"] if i != visual),
                ", ".join(f"scène {i} : {_cache(three, i)}" for i in sorted(three["scenes"]))),
    ]
    return verdicts


def _directories_of(productions: list[str]) -> list[Path]:
    from lody import settings
    from lody.animation import worker
    from lody.generation.store import ProductionRepository

    repo = ProductionRepository(settings.db_path())
    found = []
    for production_id in productions:
        task = repo.get(production_id).external_task_id
        directory = worker.find_job_dir(settings.data_dir(), task) if task else None
        if directory is None:
            raise SystemExit(f"{production_id} : aucun dossier de rendu.")
        found.append(directory)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lody.animation.recette", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    est = sub.add_parser("estimate", help="caractères ElevenLabs facturables (aucun appel)")
    est.add_argument("storyboards", nargs="+", type=Path)
    est.add_argument("--cap", type=int, default=DEFAULT_CAP)
    est.add_argument("--json", action="store_true")
    ver = sub.add_parser("verify", help="verdict PASS/FAIL des dossiers de rendu V1, V2, V3")
    source = ver.add_mutually_exclusive_group(required=True)
    source.add_argument("--dirs", nargs=3, type=Path)
    source.add_argument("--productions", nargs=3)
    ver.add_argument("--changed-scene", type=int, default=2)
    ver.add_argument("--visual-scene", type=int, default=1)
    ver.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "estimate":
        documents = [json.loads(path.read_text(encoding="utf-8")) for path in args.storyboards]
        result = estimate(documents, args.cap)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=1))
        else:
            for step in result["steps"]:
                print(f"{step['version']} : {step['calls']} appel(s), {step['characters']} caractère(s) {step['texts']}")
            print(f"Total estimé : {result['characters']} caractères ; pire cas ×{ENGINE_ATTEMPTS} : "
                  f"{result['worst_case_characters']} ; plafond {result['cap']} : "
                  + ("RESPECTÉ" if result["within_cap"] else "DÉPASSÉ"))
        return 0 if result["within_cap"] else 1

    directories = args.dirs or _directories_of(args.productions)
    verdicts = verify(*directories, changed=args.changed_scene, visual=args.visual_scene)
    if args.json:
        print(json.dumps([asdict(v) for v in verdicts], ensure_ascii=False, indent=1))
    else:
        for verdict in verdicts:
            print(f"[{'PASS' if verdict.ok else 'FAIL'}] {verdict.criterion} — {verdict.detail}")
    return 0 if all(v.ok for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
