"""Voltix : rig SVG, accessoires et catalogue importable (#136).

Vérifie que les fichiers livrés sont reproductibles (générateur), que les calques exigés par le ticket et les
pivots existent dans les deux vues, que chaque expression, visème et geste du rig s'applique sans erreur aux
deux vues, que les SVG ne contiennent rien d'exécutable ni de distant, et que le catalogue s'importe et se
fige avec le contrat #119. Aucun réseau, aucun rendu, aucun fournisseur payant.
"""

import hashlib
import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lody.animation.assets import AnimationAssetRepository, manifest_sha256, validate_manifest
from lody.animation.freeze import freeze_document, remap_assets
from lody.characters import CharacterRepository
from lody.projects import ProjectRepository

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "resource"
V1 = RESOURCE / "animation-assets" / "voltix" / "v1"
CATALOG = RESOURCE / "animation-assets" / "voltix-catalogue.json"
VIEWS = ("face", "trois-quarts")
REQUIRED_LAYERS = ("body", "eyes", "pupils", "lids", "eyebrows", "mouth-visemes", "arm-left", "arm-right",
                   "hand-left", "hand-right", "springs-left", "springs-right", "shoes", "toolbelt", "accessories")
COLLEAGUES = ("tom", "danaé", "danae")


def _module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "animation" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rig_gen = _module("voltix_rig")
rig_pose = _module("rig_pose")


@pytest.fixture(scope="module")
def rig():
    return json.loads((V1 / "voltix-rig.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _svg(view):
    return (V1 / f"voltix-{view}.svg").read_text(encoding="utf-8")


def _ids(root):
    return {el.get("id"): el for el in root.iter() if el.get("id")}


def test_committed_files_match_the_generator(catalog):
    """Les SVG v1 et le catalogue sont exactement ceux du générateur : aucune dérive silencieuse."""
    for rel, content in rig_gen.outputs().items():
        assert (RESOURCE / rel).read_text(encoding="utf-8") == content, rel
    assert rig_gen.build_catalog(RESOURCE) == catalog


@pytest.mark.parametrize("view", VIEWS)
def test_views_have_named_layers_and_pivots(view, rig):
    ids = _ids(ET.fromstring(_svg(view)))
    for layer in REQUIRED_LAYERS:
        assert layer in ids, layer
    assert set(rig["parts"]) <= set(ids)
    for group in ("arm-left", "arm-right", "forearm-left", "forearm-right", "hand-left", "hand-right",
                  "springs-left", "springs-right", "upper", "puppet", "pupil-left", "pupil-right"):
        assert ids[group].get("data-pivot"), group
    for group, variants in rig["variants"].items():
        assert ids[group].get("data-variants") is not None
        declared = [child.get("data-variant") for child in ids[group] if child.get("data-variant")]
        assert declared == variants
        visible = [child for child in ids[group] if child.get("data-variant") and child.get("display") != "none"]
        assert len(visible) == 1, f"{group} : une seule variante visible au repos"
    for slot in rig["slots"]:
        assert ids[slot].get("data-slot") is not None


def test_visemes_cover_french_mouth_shapes(rig):
    shapes = {spec.get("mouth", name) for name, spec in rig["visemes"].items() if name != "silence"}
    assert 5 <= len(shapes) <= 8
    assert shapes <= set(rig["variants"]["mouth-visemes"])
    assert rig["visemes"]["silence"]["mouth"] in rig["variants"]["mouth-visemes"]


@pytest.mark.parametrize("view", VIEWS)
def test_every_expression_viseme_and_gesture_key_poses_both_views(view, rig):
    _, props = rig_pose._load_catalog(RESOURCE)
    for expression in rig["expressions"]:
        rig_pose.apply_pose(_svg(view), rig, rig_pose.pose_for(rig, expression))
    for name in rig["variants"]["mouth-visemes"]:
        rig_pose.apply_pose(_svg(view), rig, rig_pose.merge(rig["rest"], {"select": {"mouth-visemes": name}}))
    for gesture, spec in rig["gestures"].items():
        frames = [key["frame"] for key in spec["keys"]]
        assert frames == sorted(frames) and frames[0] == 0 and frames[-1] == spec["frames"]
        for index in range(len(spec["keys"])):
            rig_pose.apply_pose(_svg(view), rig, rig_pose.pose_for(rig, "neutre", gesture, index), props=props)


def test_pose_moves_the_right_groups(rig):
    _, props = rig_pose._load_catalog(RESOURCE)
    posed = _ids(rig_pose.apply_pose(_svg("face"), rig, rig_pose.pose_for(rig, "surprise", "tenir-cable"),
                                     props=props))
    assert posed["arm-left"].get("transform").startswith("rotate(")
    assert posed["eyebrows-surprise"].get("display") is None
    assert posed["eyebrows-neutre"].get("display") == "none"
    assert len(list(posed["slot-belly"])) == 1  # câble accroché, asset séparé
    bounce = _ids(rig_pose.apply_pose(_svg("face"), rig, rig_pose.pose_for(rig, None, "rebond", 1)))
    assert "scale(1 0.8" in bounce["springs-left"].get("transform")  # ressorts comprimés, sol fixe
    with pytest.raises(rig_pose.RigError):
        rig_pose.apply_pose(_svg("face"), rig, rig_pose.merge(rig["rest"], {"select": {"lids": "inconnu"}}))
    with pytest.raises(rig_pose.RigError):
        rig_pose.merge({"script": "alert(1)"})


def test_svgs_are_inert_and_local():
    for path in sorted((RESOURCE / "animation-assets").rglob("*.svg")):
        text = path.read_text(encoding="utf-8")
        root = ET.fromstring(text)
        for el in root.iter():
            assert not el.tag.endswith(("script", "foreignObject", "image", "use")), path
            for attr, value in el.attrib.items():
                assert not attr.lower().startswith("on"), (path, attr)
                assert "http" not in value or attr == "xmlns", (path, attr)
        lowered = text.casefold()
        assert not any(name in lowered for name in COLLEAGUES), path


def test_catalog_manifests_files_and_hashes(catalog, rig):
    assert catalog["schema"] == "lody.animation-assets/1"
    voltix = catalog["assets"][0]
    assert voltix["kind"] == "character" and voltix["character"] == "Voltix"
    for asset in catalog["assets"]:
        for version in asset["versions"]:
            manifest = validate_manifest(version["manifest"])
            assert version["sha256"] == manifest_sha256(manifest)
            for item in manifest["files"]:
                data = (RESOURCE / item["path"]).read_bytes()
                assert hashlib.sha256(data).hexdigest() == item["sha256"], item["path"]
    manifest = voltix["versions"][0]["manifest"]
    assert set(manifest["expressions"]) == set(rig["expressions"])
    assert set(manifest["gestures"]) == set(rig["gestures"])
    assert {"saluer", "montrer", "tenir-cable", "rebond"} <= set(manifest["gestures"])
    assert {"sourire", "surprise", "confusion", "concentration"} <= set(manifest["expressions"])


def test_catalog_imports_and_freezes_a_voltix_storyboard(tmp_path, catalog):
    db_path = tmp_path / "lody.sqlite3"
    project = ProjectRepository(db_path).create(name="Projet Voltix")
    CharacterRepository(db_path).create(project.id, name="Voltix")
    repo = AnimationAssetRepository(db_path, clock=lambda: "2026-11-11T00:00:00+00:00")
    id_map, warnings = repo.import_catalog(project.id, catalog)
    assert warnings == [] and len(id_map) == 5
    storyboard = {
        "schema": "lody.animation/1", "canvas": {"fps": 30, "width": 1080, "height": 1920},
        "scenes": [{
            "id": "accueil", "duration_frames": 60,
            "intent": {"narration": "Salut, moi c'est Voltix !"},
            "render": {
                "layers": [{"id": "personnages", "objects": [{
                    "id": "voltix", "type": "asset", "asset": {"id": "ast_136000000001", "version": "latest"},
                    "position": [{"frame": 0, "x": 540, "y": 1700}],
                    "expressions": [{"frame": 0, "name": "sourire"}],
                    "gestures": [{"start": 0, "end": 28, "name": "rebond"}, {"start": 30, "end": 60,
                                                                               "name": "saluer"}],
                    "lipsync": True}, {
                    "id": "cable", "type": "asset", "asset": {"id": "ast_136000000002", "version": "latest"},
                    "position": [{"frame": 0, "x": 540, "y": 1100}]}]}],
                "speech": {"speaker": "voltix", "cues": [{"text": "Salut", "start": 0, "end": 12}]},
                "fallback": {"type": "still", "frame": 30},
            },
        }],
    }
    frozen = freeze_document(repo, project.id, remap_assets(storyboard, id_map))
    assert [item["version"] for item in frozen["frozen"]["assets"]] == [1, 1]
