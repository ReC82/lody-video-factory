"""Épisode #137 — V3 « du HDMI familier au SDI » : schémas, script, storyboards gelés et kit d'échantillons.

Vérifie que les schémas V3 sont reproductibles et justes (VGA à 15 broches, DisplayPort à un seul coin
chanfreiné), que le catalogue V3 s'importe avec ceux de Voltix (#136) et des schémas v1, que le script respecte
la directive V3 (sujets exclus, texte dit sûr pour la synthèse, repères de 60 caractères au plus), que les
storyboards livrés sont valides, gelés et cohérents entre eux (les 2 échantillons = les 2 premières scènes de
l'épisode, mêmes textes : le cache de voix les resservira) et que le coût des échantillons reste dans le cadre
du 13/11 (2 appels). Aucun réseau, aucun rendu, aucun fournisseur payant.
"""

import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lody.animation import recette
from lody.animation.assets import AnimationAssetRepository, validate_manifest
from lody.animation.contract import validate_document
from lody.characters import CharacterRepository
from lody.projects import ProjectRepository

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "resource"
DOCS = ROOT / "docs" / "animation" / "voltix-137" / "v3"
SVG_NS = "{http://www.w3.org/2000/svg}"


def _module(name):
    scripts = str(ROOT / "scripts" / "animation")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "animation" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _module("sdi_137_v3_assets")
episode = _module("episode_voltix_137_v3")


def _load(name):
    return json.loads((DOCS / name).read_text(encoding="utf-8"))


def _svg(key):
    return ET.fromstring((RESOURCE / gen.rel_path(key)).read_text(encoding="utf-8"))


def _group(root, group_id):
    return next(el for el in root.iter(f"{SVG_NS}g") if el.get("id") == group_id)


def test_committed_svgs_and_catalog_match_the_generator():
    for rel, content in gen.outputs().items():
        assert (RESOURCE / rel).read_text(encoding="utf-8") == content, rel
    committed = json.loads((RESOURCE / "animation-assets" / "sdi-v3-catalogue.json").read_text(encoding="utf-8"))
    assert committed == gen.build_catalog(RESOURCE)


def test_catalog_manifests_are_valid_and_ids_match_the_storyboard_builder():
    catalog = gen.build_catalog(RESOURCE)
    ids = {asset["id"] for asset in catalog["assets"]}
    assert len(ids) == len(gen.ASSETS)
    for asset in catalog["assets"]:
        validate_manifest(asset["versions"][0]["manifest"])
    for key in ("box-tv", "televiseur", "cable-plat", "pc", "plateau", "image", "cable-antenne", "antenne"):
        assert episode.A[key] in ids, key


def test_connectors_are_drawn_correctly():
    pins = _group(_svg("fiche-vga-face"), "broches").findall(f"{SVG_NS}circle")
    assert len(pins) == 15  # D-sub 15 : 3 rangées de 5
    assert len({pin.get("cy") for pin in pins}) == 3
    assert len(_group(_svg("fiche-vga-face"), "vis").findall(f"{SVG_NS}circle")) == 2

    def shell_points(key):
        path = _group(_svg(key), "coque").find(f"{SVG_NS}path").get("d")
        return re.findall(r"[MLHV]", path)

    # HDMI : 6 sommets (deux coins inférieurs chanfreinés) ; DisplayPort : 5 sommets (un seul coin chanfreiné).
    assert len(shell_points("fiche-hdmi-face")) == 6
    assert len(shell_points("fiche-dp-face")) == 5


def test_catalogs_import_together_without_warning(tmp_path):
    db = tmp_path / "lody.sqlite3"
    project = ProjectRepository(db).create(name="V3")
    CharacterRepository(db).create(project.id, name="Voltix")  # fiche à voix ElevenLabs en DEV (§ 4.3 de #187)
    repo = AnimationAssetRepository(db)
    for path in (episode.v1.VOLTIX_CATALOG, episode.v1.SDI_CATALOG, episode.V3_CATALOG):
        _, warnings = repo.import_catalog(project.id, json.loads(path.read_text(encoding="utf-8")))
        assert warnings == []


def test_script_follows_the_v3_directive():
    spoken = " ".join(" ".join(lines) for lines in episode.LINES.values())
    for word in ("HDMI", "DisplayPort", "VGA", "SDI", "coaxial", "soixante-quinze ohms", "dix caméras"):
        assert word in spoken
    for banned in ("3G", "12G", "matrice", "régie", "routage", "2110", "débit", "4K", "BNC"):
        assert banned.lower() not in spoken.lower(), banned
    assert not re.search(r"\d|Ω", spoken), "texte dit sûr pour la synthèse : nombres en toutes lettres"
    assert list(episode.LINES) == list(episode.PLAN) == list(episode.BUILDERS)
    plans = "".join(episode.PLAN[sid][0] for sid in episode.PLAN)
    assert all(str(n) in plans for n in range(1, 11))  # les 10 plans de la directive sont couverts


@pytest.mark.parametrize("sentence", [line for lines in episode.LINES.values() for line in lines])
def test_split_keeps_the_spoken_text_and_the_cue_limit(sentence):
    pieces = episode.split_sentence(sentence)
    assert " ".join(pieces) == sentence
    assert all(0 < len(piece) <= episode.MAX_CUE for piece in pieces)


def test_estimated_layout_respects_the_target_duration():
    layout = episode.estimated_layout()
    seconds = sum(s["duration"] for s in layout.values()) / episode.FPS
    assert episode.MIN_SECONDS <= seconds <= episode.MAX_SECONDS
    for s in layout.values():
        assert s["cues"][-1]["end"] < s["duration"]
        assert all(a["end"] <= b["start"] for a, b in zip(s["cues"], s["cues"][1:]))


def test_committed_storyboards_are_frozen_valid_and_consistent():
    full, samples = _load("storyboard-v3.json"), _load("echantillons-v3.json")
    for document in (full, samples):
        validate_document(document)
        assert document["frozen"]["assets"]
    assert [s["id"] for s in full["scenes"]] == list(episode.LINES)
    assert [s["id"] for s in samples["scenes"]] == list(episode.SAMPLE_SCENES)
    regenerated = episode.estimated_layout()
    for scene in full["scenes"]:
        texts = [cue["text"] for cue in scene["render"]["speech"]["cues"]]
        assert texts == [cue["text"] for cue in regenerated[scene["id"]]["cues"]]
    # Échantillons = mêmes textes que les 2 premières scènes de l'épisode : même empreinte de voix (#164).
    for sample, scene in zip(samples["scenes"], full["scenes"]):
        assert ([c["text"] for c in sample["render"]["speech"]["cues"]]
                == [c["text"] for c in scene["render"]["speech"]["cues"]])
        assert sample["duration_frames"] >= scene["duration_frames"]  # marge pour une voix lente


def test_samples_stay_within_the_owner_budget():
    estimate = recette.estimate([_load("echantillons-v3.json")], episode.DEFAULT_CAP)
    assert estimate["calls"] == 2  # cadre du 13/11 : au plus 2 petits échantillons
    assert estimate["characters"] == episode.characters(episode.SAMPLE_SCENES)
    assert estimate["within_cap"]
    summary = _load("episode-v3.json")
    assert summary["full_after_samples"]["characters_to_pay"] == episode.characters() - estimate["characters"]
    assert summary["full_after_samples"]["calls"] == len(episode.LINES) - 2
