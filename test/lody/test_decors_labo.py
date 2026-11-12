"""Quatre décors 2D réutilisables du laboratoire (#175).

Vérifie que les fichiers versionnés sont ceux du générateur, que chaque décor est en 9:16 et composé de
couches séparables identiques au fichier complet, que la bande des sous-titres ne contient que le sol uni,
que les emplacements et points d'intérêt de la fiche de scène sont cohérents avec la géométrie, que les SVG
sont inertes (ni script, ni texte, ni ressource distante, ni marque) et que le catalogue s'importe et se fige
avec le contrat #119 (décor en calque de fond et en repli). Aucun réseau, aucun rendu, aucun fournisseur payant.
"""

import hashlib
import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lody.animation.assets import AnimationAssetRepository, manifest_sha256, validate_manifest
from lody.animation.freeze import freeze_document, remap_assets
from lody.projects import ProjectRepository

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "resource"
CATALOG = RESOURCE / "animation-assets" / "labo-decors-catalogue.json"
SLUGS = ("decor-salon", "decor-bureau", "decor-plateau", "decor-regie")
NS = "{http://www.w3.org/2000/svg}"
BRANDS = ("sony", "samsung", "lg", "philips", "panasonic", "blackmagic", "atem", "grass", "canon", "nikon",
          "dell", "hp", "lenovo", "apple", "freebox", "livebox", "orange", "bbox", "sfr", "free")


def _module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "animation" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _module("decors_labo")


@pytest.fixture(scope="module")
def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _base(slug):
    return RESOURCE / "animation-assets" / slug / "v1"


def _svg(slug, layer=None):
    name = f"{slug}.svg" if layer is None else f"{slug}-{layer}.svg"
    return ET.fromstring((_base(slug) / name).read_text(encoding="utf-8"))


def _scene(slug):
    return json.loads((_base(slug) / f"{slug}-scene.json").read_text(encoding="utf-8"))


def _overlap(a, b):
    """a : (x0, y0, x1, y1) ; b : zone {x, y, width, height}."""
    return a[0] < b["x"] + b["width"] and a[2] > b["x"] and a[1] < b["y"] + b["height"] and a[3] > b["y"]


def test_committed_files_match_the_generator(catalog):
    files = gen.outputs()
    for rel, content in files.items():
        assert (RESOURCE / rel).read_text(encoding="utf-8") == content, rel
    assert gen.build_catalog(files) == catalog
    assert {spec["slug"] for spec in gen.specs()} == set(SLUGS)


@pytest.mark.parametrize("slug", SLUGS)
def test_decor_is_vertical_and_made_of_composable_layers(slug):
    root = _svg(slug)
    assert (root.get("width"), root.get("height"), root.get("viewBox")) == ("1080", "1920", "0 0 1080 1920")
    groups = [child for child in root if child.tag == f"{NS}g"]
    assert [g.get("id") for g in groups] == ["fond", "objets", "avant"]
    for group in groups:
        layer = _svg(slug, group.get("id"))
        assert layer.get("viewBox") == root.get("viewBox")
        [alone] = [child for child in layer if child.tag == f"{NS}g"]
        alone.tail = group.tail = None
        assert ET.tostring(alone) == ET.tostring(group), group.get("id")
    # Fond opaque couvrant tout le canevas : le décor remplace le fond par défaut du moteur.
    covered = [gen.element_bbox(el) for el in groups[0] if el.tag == f"{NS}rect"]
    assert min(b[0] for b in covered) <= 0 and max(b[2] for b in covered) >= 1080
    assert min(b[1] for b in covered) <= 0 and max(b[3] for b in covered) >= 1920


@pytest.mark.parametrize("slug", SLUGS)
def test_subtitle_band_contains_only_the_plain_floor(slug):
    scene = _scene(slug)
    band = scene["zones"]["subtitles"]
    assert band["y"] >= 1920 * 0.7 and band["y"] + band["height"] <= 1920
    assert band["y"] <= 1920 * 0.8 < band["y"] + band["height"]  # le moteur pose les sous-titres à 80 %
    root = _svg(slug)
    for layer in ("objets", "avant"):
        group = next(g for g in root if g.get("id") == layer)
        for el in group.iter():
            box = gen.element_bbox(el)
            assert box is None or not _overlap(box, band), (layer, el.attrib)


@pytest.mark.parametrize("slug", SLUGS)
def test_scene_sheet_places_and_hotspots(slug):
    scene = _scene(slug)
    assert scene["schema"] == "lody.decor/1" and scene["decor"] == slug and scene["version"] == 1
    assert scene["order"] == ["fond", "objets", "personnages", "avant"]
    assert [item["file"] for item in scene["layers"]] == [f"{slug}-{n}.svg" for n in ("fond", "objets", "avant")]
    band = scene["zones"]["subtitles"]
    assert len(scene["places"]) >= 3
    xs = [place["x"] for place in scene["places"]]
    assert len(set(xs)) == len(xs)
    for place in scene["places"]:
        assert 0 < place["x"] < 1080
        assert scene["floor_y"] < place["y"] <= band["y"], "pieds sur le sol, au-dessus des sous-titres"
        assert 0 < place["y"] - place["height"], "la tête reste dans l'image"
    root = _svg(slug)
    ids = {el.get("id"): el for el in root.iter() if el.get("id")}
    seen = set()
    for spot in scene["hotspots"]:
        assert spot["id"] not in seen and spot["label"]
        seen.add(spot["id"])
        group = ids[spot["id"]]
        assert group in list(ids[spot["layer"]])
        x, y, w, h = spot["bbox"]
        assert w > 0 and h > 0 and 0 <= x and x + w <= 1080 and 0 <= y and y + h <= 1920
        assert not _overlap((x, y, x + w, y + h), band), spot["id"]
        assert x <= spot["point"][0] <= x + w and y <= spot["point"][1] <= y + h
        assert spot["bbox"] == gen.clip(gen.group_bbox(group))  # boîte = géométrie réelle du SVG
    assert seen == {child.get("id") for layer in ("objets", "avant") for child in ids[layer]}


def test_each_decor_shows_the_objects_the_ticket_asks_for():
    expected = {
        "decor-salon": {"tv", "box", "cable-hdmi", "prise-hdmi-tv", "prise-hdmi-box"},
        "decor-bureau": {"tour-pc", "ecran-displayport", "ecran-vga", "cable-displayport", "cable-vga",
                         "sortie-dp-pc", "sortie-vga-pc"},
        "decor-plateau": {"camera", "trepied", "tally", "sortie-sdi-camera", "projecteur-gauche"},
        "decor-regie": {"mur-moniteurs", "programme-pgm", "apercu-pvw", "melangeur", "levier-t-bar"},
    }
    for slug, ids in expected.items():
        assert ids <= {spot["id"] for spot in _scene(slug)["hotspots"]}, slug


@pytest.mark.parametrize("slug", SLUGS)
def test_svgs_are_inert_schematic_and_unbranded(slug):
    for path in sorted(_base(slug).glob("*.svg")):
        text = path.read_text(encoding="utf-8")
        for el in ET.fromstring(text).iter():
            tag = el.tag.removeprefix(NS)
            assert tag in {"svg", "title", "g", "rect", "circle", "ellipse", "line", "polygon", "polyline"}, (path, tag)
            for attr, value in el.attrib.items():
                assert not attr.lower().startswith("on") and attr != "transform", (path, attr)
                assert "http" not in value and "url(" not in value, (path, attr)
        words = set("".join(ch if ch.isalnum() else " " for ch in text.casefold()).split())
        assert not words & set(BRANDS), path


def test_catalog_manifests_files_and_hashes(catalog):
    assert catalog["schema"] == "lody.animation-assets/1"
    assert [asset["kind"] for asset in catalog["assets"]] == ["background"] * 4
    for asset, slug in zip(catalog["assets"], SLUGS):
        [version] = asset["versions"]
        manifest = validate_manifest(version["manifest"])
        assert version["sha256"] == manifest_sha256(manifest)
        assert (manifest["width"], manifest["height"]) == (1080, 1920)
        assert {item["role"] for item in manifest["files"]} == {"base", "fond", "objets", "avant", "scene"}
        for item in manifest["files"]:
            assert item["path"].startswith(f"animation-assets/{slug}/v1/")
            data = (RESOURCE / item["path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == item["sha256"], item["path"]
        assert {spot["id"] for spot in _scene(slug)["hotspots"]} <= set(manifest["parts"])


def test_catalog_imports_and_freezes_a_storyboard_using_a_decor(tmp_path, catalog):
    db_path = tmp_path / "lody.sqlite3"
    project = ProjectRepository(db_path).create(name="Labo décors")
    repo = AnimationAssetRepository(db_path, clock=lambda: "2026-11-12T00:00:00+00:00")
    id_map, warnings = repo.import_catalog(project.id, catalog)
    assert warnings == [] and len(id_map) == 4
    assert repo.import_catalog(project.id, catalog)[0].keys() == id_map.keys()  # réimport : pas d'erreur
    salon = _scene("decor-salon")
    tv = next(spot for spot in salon["hotspots"] if spot["id"] == "tv")
    storyboard = {
        "schema": "lody.animation/1", "canvas": {"fps": 30, "width": 1080, "height": 1920},
        "scenes": [{
            "id": "salon", "duration_frames": 60,
            "intent": {"narration": "La box envoie l'image au téléviseur par le câble HDMI."},
            "render": {
                "layers": [
                    {"id": "decor", "objects": [{
                        "id": "salon", "type": "asset", "asset": {"id": "ast_175000000001", "version": "latest"},
                        "position": [{"frame": 0, "x": 540, "y": 960}]}]},
                    {"id": "textes", "objects": [{
                        "id": "etiquette-tv", "type": "text", "text": {"content": "Téléviseur", "style": "label"},
                        "position": [{"frame": 0, "x": tv["point"][0], "y": tv["bbox"][1] - 40}]}]},
                ],
                "camera": {"keyframes": [
                    {"frame": 0, "x": 540, "y": 960, "zoom": 1.0, "rotation": 0},
                    {"frame": 60, "x": tv["point"][0], "y": tv["point"][1], "zoom": 1.4, "rotation": 0}]},
                "fallback": {"type": "asset", "asset": {"id": "ast_175000000001", "version": "latest"}},
            },
        }],
    }
    frozen = freeze_document(repo, project.id, remap_assets(storyboard, id_map))
    assert [(item["kind"], item["version"]) for item in frozen["frozen"]["assets"]] == [("background", 1)]
