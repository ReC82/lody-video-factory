"""Nora et Sam : rigs SVG des personnages adultes du laboratoire et catalogue importable (#173).

Vérifie que les fichiers livrés sont reproductibles (générateur), que les calques, pivots, variantes et points
d'accroche existent dans les deux vues, que chaque expression, visème et clé de geste s'applique aux deux vues
(sans ressorts : pose générique), que les SVG sont inertes et locaux, que les deux silhouettes diffèrent, que
Voltix n'a pas bougé et que le catalogue s'importe et se fige avec le contrat #119. Aucun réseau, aucun rendu,
aucun fournisseur payant.
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
CATALOG = RESOURCE / "animation-assets" / "labo-personnages-catalogue.json"
KEYS = ("nora", "sam")
VIEWS = ("face", "trois-quarts")
REQUIRED_LAYERS = ("body", "head", "eyes", "pupils", "lids", "eyebrows", "mouth-visemes", "arm-left", "arm-right",
                   "forearm-left", "forearm-right", "hand-left", "hand-right", "legs-left", "legs-right", "shoes",
                   "accessories", "upper", "puppet", "shadow")
PIVOTS = ("head", "arm-left", "arm-right", "forearm-left", "forearm-right", "hand-left", "hand-right", "upper",
          "puppet", "pupil-left", "pupil-right", "legs-left", "legs-right")
COLLEAGUES = ("tom", "danaé", "danae")


def _module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "animation" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


humain = _module("humain_rig")
rig_pose = _module("rig_pose")
voltix_gen = _module("voltix_rig")


def _rig(key):
    return json.loads((RESOURCE / f"animation-assets/{key}/v1/{key}-rig.json").read_text(encoding="utf-8"))


def _svg(key, view):
    return (RESOURCE / f"animation-assets/{key}/v1/{key}-{view}.svg").read_text(encoding="utf-8")


def _ids(root):
    return {el.get("id"): el for el in root.iter() if el.get("id")}


@pytest.fixture(scope="module")
def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def props():
    return rig_pose._load_catalog(RESOURCE)[1]


def test_committed_files_match_the_generator(catalog):
    """SVG, rigs et catalogue v1 sont exactement ceux du générateur : aucune dérive silencieuse."""
    for rel, content in humain.outputs().items():
        assert (RESOURCE / rel).read_text(encoding="utf-8") == content, rel
    assert humain.build_catalog(RESOURCE) == catalog


def test_voltix_is_unchanged():
    """Le ticket impose de conserver Voltix : ses fichiers v1 restent ceux de son générateur."""
    for rel, content in voltix_gen.outputs().items():
        assert (RESOURCE / rel).read_text(encoding="utf-8") == content, rel


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("view", VIEWS)
def test_views_have_named_layers_pivots_variants_and_slots(key, view):
    rig = _rig(key)
    ids = _ids(ET.fromstring(_svg(key, view)))
    for layer in REQUIRED_LAYERS:
        assert layer in ids, layer
    assert set(rig["parts"]) <= set(ids)
    for group in PIVOTS:
        assert ids[group].get("data-pivot"), group
    for group, variants in rig["variants"].items():
        assert ids[group].get("data-variants") is not None
        declared = [child.get("data-variant") for child in ids[group] if child.get("data-variant")]
        assert declared == variants
        visible = [child for child in ids[group] if child.get("data-variant") and child.get("display") != "none"]
        assert len(visible) == 1, f"{group} : une seule variante visible au repos"
    for slot in rig["slots"]:
        assert ids[slot].get("data-slot") is not None


@pytest.mark.parametrize("key", KEYS)
def test_rig_speaks_the_same_language_as_voltix(key):
    """Mêmes noms de visèmes, paupières, mains et expressions : le moteur (lipsync, clignement) les pilote."""
    rig, voltix = _rig(key), json.loads(
        (RESOURCE / "animation-assets/voltix/v1/voltix-rig.json").read_text(encoding="utf-8"))
    assert rig["schema"] == "lody.rig/1"
    for group in ("eyebrows", "lids", "mouth-visemes", "hand-left", "hand-right"):
        assert rig["variants"][group] == voltix["variants"][group]
    assert set(rig["visemes"]) == set(voltix["visemes"])
    assert set(rig["expressions"]) == {"neutre", "sourire", "surprise", "confusion", "concentration"}
    assert {"saluer", "montrer", "tenir-cable", "cligner"} <= set(rig["gestures"])
    for spec in rig["gestures"].values():
        frames = [item["frame"] for item in spec["keys"]]
        assert frames == sorted(frames) and frames[0] == 0 and frames[-1] == spec["frames"]
        for item in spec["keys"]:
            assert not {"lift", "air"} & set(item["pose"]), "pas de ressorts chez un humain"
    for name in rig["rest"]["rotate"]:
        assert name in rig["parts"]


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("view", VIEWS)
def test_every_expression_viseme_and_gesture_key_poses_both_views(key, view, props):
    rig, svg = _rig(key), _svg(key, view)
    for expression in rig["expressions"]:
        rig_pose.apply_pose(svg, rig, rig_pose.pose_for(rig, expression))
    for name in rig["variants"]["mouth-visemes"]:
        rig_pose.apply_pose(svg, rig, rig_pose.merge(rig["rest"], {"select": {"mouth-visemes": name}}))
    for gesture, spec in rig["gestures"].items():
        for index in range(len(spec["keys"])):
            rig_pose.apply_pose(svg, rig, rig_pose.pose_for(rig, "neutre", gesture, index), props=props)


@pytest.mark.parametrize("key", KEYS)
def test_pose_moves_the_right_groups_without_springs(key, props):
    rig = _rig(key)
    posed = _ids(rig_pose.apply_pose(_svg(key, "face"), rig, rig_pose.pose_for(rig, "surprise", "tenir-cable"),
                                     props=props))
    assert posed["arm-left"].get("transform").startswith("rotate(")
    assert posed["eyebrows-surprise"].get("display") is None
    assert posed["eyebrows-neutre"].get("display") == "none"
    assert len(list(posed["slot-chest"])) == 1  # câble accroché, asset séparé
    # Respiration du moteur (squash) : le haut du corps s'écrase, les jambes ne bougent pas.
    breathing = _ids(rig_pose.apply_pose(_svg(key, "face"), rig, rig_pose.merge(rig["rest"], {"squash": 0.012})))
    assert "scale(1.012 0.988)" in breathing["upper"].get("transform")
    assert breathing["legs-left"].get("transform") is None
    tilt = _ids(rig_pose.apply_pose(_svg(key, "face"), rig, rig_pose.pose_for(rig, None, "pencher-tete")))
    assert tilt["head"].get("transform").startswith("rotate(8 ")
    with pytest.raises(rig_pose.RigError):
        rig_pose.apply_pose(_svg(key, "face"), rig, rig_pose.merge(rig["rest"], {"select": {"lids": "inconnu"}}))


def test_svgs_are_inert_local_and_fictional():
    for key in KEYS:
        for path in sorted((RESOURCE / "animation-assets" / key).rglob("*.svg")):
            text = path.read_text(encoding="utf-8")
            for el in ET.fromstring(text).iter():
                assert not el.tag.endswith(("script", "foreignObject", "image", "use")), path
                for attr, value in el.attrib.items():
                    assert not attr.lower().startswith("on"), (path, attr)
                    assert "http" not in value or attr == "xmlns", (path, attr)
            assert not any(name in text.casefold() for name in COLLEAGUES), path


def test_silhouettes_are_distinct():
    nora, sam = humain.CHARACTERS["nora"], humain.CHARACTERS["sam"]
    assert sam["shoulder_half"] - nora["shoulder_half"] >= 20  # Sam trapu, Nora élancée
    assert nora["palette"]["tenue"] != sam["palette"]["tenue"]
    assert sam["palette"]["gilet"].upper() == "#F2802A"  # gilet orange
    nora_ids, sam_ids = _ids(ET.fromstring(_svg("nora", "face"))), _ids(ET.fromstring(_svg("sam", "face")))
    assert {"headset", "beltpack", "badge"} <= set(nora_ids) and "vest" not in nora_ids
    assert {"vest", "beard"} <= set(sam_ids) and "headset" not in sam_ids


def test_catalog_manifests_files_and_hashes(catalog):
    assert catalog["schema"] == "lody.animation-assets/1"
    assert [asset["character"] for asset in catalog["assets"]] == ["Nora", "Sam"]
    for asset in catalog["assets"]:
        assert asset["kind"] == "character"
        version = asset["versions"][0]
        manifest = validate_manifest(version["manifest"])
        assert version["sha256"] == manifest_sha256(manifest)
        for item in manifest["files"]:
            data = (RESOURCE / item["path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == item["sha256"], item["path"]
        key = asset["character"].lower()
        assert set(manifest["gestures"]) == set(_rig(key)["gestures"])
        assert {"base", "trois-quarts", "rig"} == {item["role"] for item in manifest["files"]}


def test_catalog_imports_and_freezes_a_two_character_storyboard(tmp_path, catalog):
    db_path = tmp_path / "lody.sqlite3"
    project = ProjectRepository(db_path).create(name="Labo")
    for name in ("Nora", "Sam"):
        CharacterRepository(db_path).create(project.id, name=name)
    repo = AnimationAssetRepository(db_path, clock=lambda: "2026-11-12T00:00:00+00:00")
    id_map, warnings = repo.import_catalog(project.id, catalog)
    assert warnings == [] and len(id_map) == 2

    def puppet(obj_id, asset_id, x, gesture):
        return {"id": obj_id, "type": "asset", "asset": {"id": asset_id, "version": "latest"},
                "position": [{"frame": 0, "x": x, "y": 1700}], "expressions": [{"frame": 0, "name": "sourire"}],
                "gestures": [{"start": 0, "end": 30, "name": gesture}], "lipsync": True}

    storyboard = {
        "schema": "lody.animation/1", "canvas": {"fps": 30, "width": 1080, "height": 1920},
        "scenes": [{
            "id": "accueil", "duration_frames": 60, "intent": {"narration": "Bonjour, moi c'est Nora."},
            "render": {
                "layers": [{"id": "personnages", "objects": [
                    puppet("nora", "ast_173000000001", 340, "saluer"),
                    puppet("sam", "ast_173000000002", 760, "montrer")]}],
                "speech": {"speaker": "nora", "cues": [{"text": "Bonjour", "start": 0, "end": 12}]},
                "fallback": {"type": "still", "frame": 30},
            },
        }],
    }
    frozen = freeze_document(repo, project.id, remap_assets(storyboard, id_map))
    assert [item["version"] for item in frozen["frozen"]["assets"]] == [1, 1]
