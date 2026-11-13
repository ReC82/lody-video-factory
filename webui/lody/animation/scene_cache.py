"""Cache de scène du rendu 2D (#121) : une scène inchangée n'est jamais rendue deux fois, d'une tâche à l'autre.

Une scène rendue est rangée dans le cache de SON projet (``<données>/animation/<projet>/.scene-cache/``) sous la
clé = SHA-256 de ses **dépendances**, et seulement d'elles :

- la scène telle que le moteur la lit, **sans** ``id`` ni ``intent`` (l'intention narrative n'est jamais
  interprétée par le moteur : la retoucher ne refait rien) ;
- le canevas (fps, largeur, hauteur) et l'échelle de rendu (prévisualisation basse résolution ≠ rendu final) ;
- l'empreinte des fichiers d'assets que la scène utilise, et leurs informations de placement ;
- la **tranche de voix** jouée pendant la scène (échantillons PCM de sa fenêtre) : retoucher la voix d'une
  autre scène, ou déplacer la scène, ne l'invalide pas ;
- les **repères de parole recalés** sur la voix réellement jouée (``speech_cues``, #185) quand il y en a :
  la bouche (visèmes) et les sous-titres de la scène en dépendent. Mêmes textes mais nouveaux repères, ou
  nouvelle voix, = scène à refaire ; une scène réutilisée n'affiche jamais une bouche calée sur l'ancienne voix.
  Sans repères recalés (rendu V1, voix du storyboard), l'entrée est absente : les clés existantes ne changent pas ;
- l'identité du moteur de scène (commande, révision déclarée, version annoncée par le moteur).

Une modification n'invalide donc que les sorties qui en dépendent. Seule une scène **réellement animée** entre
au cache : un repli hybride (image fixe) n'y entre jamais, il serait sinon resservi comme une animation.

Sûreté : le cache est par projet (isolation identique aux dossiers de tâche) ; chaque entrée porte l'empreinte
de son MP4, vérifiée avant toute réutilisation (fichier altéré = entrée ignorée et supprimée) ; la taille du
cache est plafonnée (les entrées les moins récemment utilisées partent en premier).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import wave
from pathlib import Path
from typing import Any

from lody.animation.contract import canonical_json

CACHE_SCHEMA = "lody.animation-scene-cache/1"
CACHE_DIR = ".scene-cache"
MAX_CACHE_BYTES_ENV = "LODY_ANIMATION_CACHE_MAX_MB"
DEFAULT_MAX_CACHE_BYTES = 2 * 1024**3       # 2 Gio par projet (≈ 1 000 scènes de 5 s en 1080×1920)
_ENGINE_KEYS = ("id", "intent")              # champs que le moteur de scène n'interprète pas


def engine_view(scene: dict[str, Any]) -> dict[str, Any]:
    """La scène telle que le moteur de scène la lit : sans identifiant ni intention narrative."""
    return {key: value for key, value in scene.items() if key not in _ENGINE_KEYS}


def used_asset_keys(scene: dict[str, Any]) -> list[str]:
    """``id@version`` des assets dont dépend la scène (objets + repli), triés."""
    used: set[str] = set()
    render = scene.get("render") or {}
    for layer in render.get("layers") or []:
        for obj in layer.get("objects") or []:
            if obj.get("type") == "asset":
                used.add(f"{obj['asset']['id']}@{obj['asset']['version']}")
    fallback = render.get("fallback") or {}
    if fallback.get("type") == "asset":
        used.add(f"{fallback['asset']['id']}@{fallback['asset']['version']}")
    return sorted(used)


def voice_slice_digest(voice: Path, fps: int, offset_frames: int, frames: int) -> dict[str, Any] | None:
    """Empreinte des échantillons de voix joués pendant la scène, ou ``None`` sans voix.

    WAV PCM lisible : seule la fenêtre ``[offset, offset + frames[`` compte (plus les paramètres du flux).
    Format illisible par ``wave`` : empreinte du fichier entier + décalage (prudent : toute retouche invalide).
    """
    if not voice.is_file():
        return None
    try:
        with wave.open(str(voice), "rb") as source:
            rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
            start = int(round(offset_frames * rate / fps))
            count = int(round(frames * rate / fps))
            total = source.getnframes()
            digest = hashlib.sha256(f"{rate}:{channels}:{width}:{count}".encode())
            if start < total:
                source.setpos(start)
                digest.update(source.readframes(min(count, total - start)))
            return {"window": [offset_frames, frames], "sha256": digest.hexdigest()}
    except (wave.Error, EOFError, OSError):
        digest = hashlib.sha256()
        with voice.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        return {"window": [offset_frames, frames], "file_sha256": digest.hexdigest()}


def normalized_speech_cues(cues: Any) -> list[dict[str, Any]] | None:
    """Repères de parole d'UNE scène, réduits à ce que le moteur de scène lit (début, fin, texte, visèmes).

    ``None`` ou liste vide = aucun repère recalé (la scène garde ceux du storyboard, déjà dans ``scene``).
    Les champs inconnus sont conservés (prudence : un champ nouveau lu par le moteur doit invalider le cache).
    """
    if not cues:
        return None
    if not isinstance(cues, list) or not all(isinstance(cue, dict) for cue in cues):
        raise ValueError("repères de parole invalides : liste d'objets attendue")
    return [dict(cue) for cue in cues]


def scene_dependencies(job: dict[str, Any], index: int, scene: dict[str, Any], voice: Path | None,
                       renderer_identity: str, scale: float = 1.0, *,
                       speech_cues: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Tout ce dont dépend l'image rendue d'une scène — et rien d'autre (voir l'en-tête du module).

    ``speech_cues`` (#185) : repères recalés sur la voix réelle de CETTE scène (``voiceover.timed_cues`` de la
    PR #169), tels que transmis au moteur de scène. Absents ou vides : la clé est identique à celle d'avant #185.
    """
    storyboard = job["storyboard"]
    fps = storyboard["canvas"]["fps"]
    offset = sum(s["duration_frames"] for s in storyboard["scenes"][: index - 1])
    assets = job.get("assets") or {}
    info = job.get("asset_info") or {}
    used = used_asset_keys(scene)
    dependencies = {
        "schema": CACHE_SCHEMA,
        "canvas": storyboard["canvas"],
        "scale": round(float(scale), 3),
        "scene": engine_view(scene),
        "assets": {key: {role: entry["sha256"] for role, entry in (assets.get(key) or {}).items()} for key in used},
        "asset_info": {key: info[key] for key in used if key in info},
        "voice": voice_slice_digest(voice, fps, offset, scene["duration_frames"]) if voice is not None else None,
        "renderer": renderer_identity,
    }
    timed = normalized_speech_cues(speech_cues)
    if timed is not None:  # absente sinon : aucune clé existante (V1, cache déjà rempli) n'est invalidée
        dependencies["speech_timing"] = timed
    return dependencies


def dependency_key(dependencies: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dependencies).encode("utf-8")).hexdigest()


def max_cache_bytes() -> int:
    try:
        value = int(os.environ.get(MAX_CACHE_BYTES_ENV, "") or 0)
    except ValueError:
        value = 0
    return value * 1024**2 if value > 0 else DEFAULT_MAX_CACHE_BYTES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _place(source: Path, target: Path) -> None:
    """Lien physique si possible (aucune copie), sinon copie ; publication atomique."""
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        os.link(source, temporary)
    except OSError:
        shutil.copyfile(source, temporary)
    os.replace(temporary, target)


class SceneCache:
    """Cache d'un projet : ``<clé>.mp4`` + ``<clé>.json`` (trace d'origine, empreinte, durée de rendu)."""

    def __init__(self, project_dir: Path, max_bytes: int | None = None):
        self.root = Path(project_dir) / CACHE_DIR
        self.max_bytes = max_bytes if max_bytes is not None else max_cache_bytes()

    def _paths(self, key: str) -> tuple[Path, Path]:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("clé de cache invalide")
        return self.root / f"{key}.mp4", self.root / f"{key}.json"

    def lookup(self, key: str, target: Path) -> dict[str, Any] | None:
        """Place la scène en cache dans ``target`` et renvoie sa trace, ou ``None`` (absente ou altérée)."""
        video, meta_path = self._paths(key)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict) or meta.get("schema") != CACHE_SCHEMA or not video.is_file():
            return None
        if _sha256(video) != meta.get("sha256"):
            video.unlink(missing_ok=True)   # altérée : jamais resservie
            meta_path.unlink(missing_ok=True)
            return None
        _place(video, target)
        now = time.time()
        try:
            os.utime(meta_path, (now, now))  # utilisée récemment : dernière à partir au plafonnement
        except OSError:
            pass
        return meta

    def store(self, key: str, source: Path, trace: dict[str, Any]) -> None:
        """Range une scène ANIMÉE (jamais un repli) ; une erreur d'écriture n'empêche jamais le rendu."""
        if trace.get("status") != "animated":
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            video, meta_path = self._paths(key)
            _place(source, video)
            meta = {"schema": CACHE_SCHEMA, "sha256": trace["sha256"], "render_ms": trace.get("render_ms", 0),
                    "duration_seconds": trace.get("duration_seconds"), "frames": trace.get("frames"),
                    "origin": {"task": Path(source).parents[1].name, "scene": trace.get("id"),
                               "index": trace.get("index")}, "stored_at": time.time()}
            temporary = meta_path.with_name(f".{meta_path.name}.{os.getpid()}.tmp")
            temporary.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, meta_path)
            self.prune()
        except OSError:
            return

    def prune(self) -> int:
        """Plafonne la taille du cache (moins récemment utilisées d'abord). Renvoie le nombre d'entrées retirées."""
        if not self.root.is_dir():
            return 0
        entries = []
        for meta_path in self.root.glob("*.json"):
            video = meta_path.with_suffix(".mp4")
            try:
                entries.append((meta_path.stat().st_mtime, meta_path, video,
                                video.stat().st_size if video.is_file() else 0))
            except OSError:
                continue
        total = sum(size for *_, size in entries)
        removed = 0
        for _, meta_path, video, size in sorted(entries, key=lambda item: item[0]):
            if total <= self.max_bytes:
                break
            video.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            total -= size
            removed += 1
        return removed
