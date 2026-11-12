"""Préflight du projet expérimental du laboratoire (#174) : « Comment l'image d'une caméra apparaît-elle sur un
moniteur ? », dialogue Nora / Sam.

Vérifie, avant toute génération : livrables reproductibles, preset accepté par les templates (#110), storyboard
accepté par le contrat ``lody.animation/1`` (#119) avec 30 à 45 s et des scènes de 5 à 7 s, une réplique par
scène et des locuteurs alternés, texte oral TTS-safe et distinct du texte affiché, rythme de parole tenable,
schéma caméra → câble → écran complet, gestes et expressions limités au rig #173, aucun personnage inventé, puis
import des catalogues et gel du storyboard. Aucun réseau, aucune voix, aucun fournisseur payant.
"""

import copy
import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lody import brief
from lody.animation.assets import AnimationAssetRepository, manifest_sha256, validate_manifest
from lody.animation.contract import duration_seconds, iter_asset_refs, validate_document
from lody.animation.freeze import freeze_document, remap_assets
from lody.characters import CharacterRepository
from lody.projects import ProjectRepository
from lody.templates import validate_fields

ROOT = Path(__file__).resolve().parents[2]
EPISODE = ROOT / "docs" / "episodes" / "labo-camera-moniteur"
DOC = ROOT / "docs" / "episodes" / "labo-camera-moniteur.md"
RESOURCE = ROOT / "resource"
CATALOG = RESOURCE / "animation-assets" / "labo-174-catalogue.json"
PEOPLE_CATALOG = RESOURCE / "animation-assets" / "labo-personnages-catalogue.json"  # livré par #173
NORA, SAM = "ast_173000000001", "ast_173000000002"
# Noms déclarés par le rig v1 de Nora et Sam (#173, docs/lody-personnages-labo.md).
RIG_173_EXPRESSIONS = {"concentration", "confusion", "neutre", "sourire", "surprise"}
RIG_173_GESTURES = {"cligner", "expliquer", "montrer", "pencher-tete", "saluer", "tenir-cable"}
# Indépendant de SDI et des épisodes publiés : aucune norme, aucun connecteur, aucune marque.
FORBIDDEN_TERMS = ("sdi", "hdmi", "bnc", "coax", "ohm", "voltix", "tally", "genlock")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _module():
    spec = importlib.util.spec_from_file_location("labo_174", ROOT / "scripts" / "animation" / "labo_174.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc_dialogue():
    """Tableau du document : (personnage, texte oral, textes affichés)."""
    block = DOC.read_text(encoding="utf-8").split("<!-- dialogue:debut -->")[1].split("<!-- dialogue:fin -->")[0]
    rows = [line.split("|")[1:-1] for line in block.splitlines() if re.match(r"^\| \d+ \|", line)]
    return [(cells[1].strip(), cells[3].strip(),
             [cell.strip().strip("`") for cell in cells[4:6] if cell.strip() != "—"]) for cells in rows]


def _objects(scene):
    for layer in scene["render"]["layers"]:
        yield from layer["objects"]


def _texts(scene):
    return [obj["text"]["content"] for obj in _objects(scene) if obj["type"] == "text"]


@pytest.fixture(scope="module")
def storyboard():
    return _load(EPISODE / "storyboard.json")


# -- livrables ---------------------------------------------------------------------------------------------------
def test_deliverables_are_reproducible_from_the_generator():
    for path, content in _module().outputs().items():
        assert path.read_text(encoding="utf-8") == content, f"{path} : relancer scripts/animation/labo_174.py"


def test_template_is_a_valid_production_template():
    raw = _load(EPISODE / "template.json")
    clean = validate_fields(raw)
    assert clean["payload"] == raw["payload"]
    assert (clean["payload"]["duration_min"], clean["payload"]["duration_max"]) == (30, 45)
    assert "voice_id" not in json.dumps(raw)


def test_lab_catalog_is_importable_and_its_svgs_are_inert():
    catalog = _load(CATALOG)
    assert catalog["schema"] == "lody.animation-assets/1"
    for asset in catalog["assets"]:
        assert asset["kind"] in ("prop", "overlay") and asset["character"] is None  # aucun personnage ici
        version = asset["versions"][0]
        manifest = validate_manifest(version["manifest"])
        assert manifest_sha256(manifest) == version["sha256"]
        svg_path = RESOURCE / manifest["files"][0]["path"]
        text = svg_path.read_text(encoding="utf-8")
        root = ET.fromstring(text)
        assert root.get("viewBox") == f"0 0 {manifest['width']} {manifest['height']}"
        ids = {node.get("id") for node in root.iter() if node.get("id")}
        assert set(manifest["parts"]) <= ids, svg_path
        lowered = text.replace('xmlns="http://www.w3.org/2000/svg"', "").lower()
        for needle in ("<script", "href", "url(", "http", "onload", "<image", "<text"):
            assert needle not in lowered, (svg_path, needle)


# -- storyboard --------------------------------------------------------------------------------------------------
def test_storyboard_is_valid_vertical_and_timed(storyboard):
    document = validate_document(storyboard)
    assert document["canvas"] == {"fps": 30, "width": 1080, "height": 1920}
    assert 30 <= duration_seconds(document) <= 45
    assert 5 <= len(document["scenes"]) <= 9
    for scene in document["scenes"]:
        assert 5 <= scene["duration_frames"] / 30 <= 7, scene["id"]


def test_one_line_per_scene_alternating_between_nora_and_sam(storyboard):
    speakers = [scene["render"]["speech"]["speaker"] for scene in storyboard["scenes"]]
    assert set(speakers) == {"nora", "sam"}
    assert all(a != b for a, b in zip(speakers, speakers[1:]))
    for scene in storyboard["scenes"]:
        speaker = scene["render"]["speech"]["speaker"]
        puppets = {obj["id"]: obj for obj in _objects(scene) if obj["id"] in ("nora", "sam")}
        assert set(puppets) == {"nora", "sam"}, scene["id"]
        # Seul le personnage qui parle bouge les lèvres.
        assert {key for key, obj in puppets.items() if obj["lipsync"]} == {speaker}, scene["id"]
        # Les sous-titres reprennent exactement la réplique.
        assert " ".join(cue["text"] for cue in scene["render"]["speech"]["cues"]) == scene["intent"]["narration"]


def test_storyboard_matches_the_documented_dialogue(storyboard):
    documented = _doc_dialogue()
    assert len(documented) == len(storyboard["scenes"])
    for (speaker, oral, shown), scene in zip(documented, storyboard["scenes"]):
        assert speaker.lower() == scene["render"]["speech"]["speaker"]
        assert oral == scene["intent"]["narration"]
        assert shown == [text for text in _texts(scene) if text not in ("Caméra", "Câble", "Écran")]


def test_spoken_and_displayed_texts_are_separate(storyboard):
    for scene in storyboard["scenes"]:
        oral = scene["intent"]["narration"]
        titles = [obj["text"]["content"] for obj in _objects(scene)
                  if obj["type"] == "text" and obj["text"]["style"] == "title"]
        assert len(titles) == 1, scene["id"]
        assert len(titles[0].split()) <= 6, scene["id"]  # lisible d'un coup d'œil
        for shown in _texts(scene):
            assert shown not in oral and oral not in shown, scene["id"]


def test_spoken_text_is_tts_safe_and_fits_each_scene(storyboard):
    words_per_second = brief.WORDS_PER_MINUTE["normal"] / 60
    for scene in storyboard["scenes"]:
        oral = scene["intent"]["narration"]
        assert not re.search(r"\d", oral), oral
        assert not re.search(r"\b[A-Z]{2,}\b", oral), oral
        assert "→" not in oral
        speaking = len(oral.split()) / words_per_second
        # Marge : 8 images avant et 14 après la réplique (découpage des sous-titres).
        assert speaking <= (scene["duration_frames"] - 22) / 30, (scene["id"], speaking)


def test_single_central_notion_and_independent_from_sdi(storyboard):
    whole = json.dumps(storyboard, ensure_ascii=False).lower() + _load(EPISODE / "template.json").__repr__().lower()
    for term in FORBIDDEN_TERMS:
        assert not re.search(rf"\b{term}", whole), term
    narration = " ".join(scene["intent"]["narration"] for scene in storyboard["scenes"]).lower()
    for step in ("capte", "transporte", "affiche"):
        assert step in narration
    assert "lumière en signal électrique" in narration and "décode le signal" in narration


def test_camera_cable_screen_diagram_is_built_left_to_right(storyboard):
    module = _module()
    final = storyboard["scenes"][-1]
    by_id = {obj["id"]: obj for obj in _objects(final)}
    chain = {"sujet": module.SUJET, "camera": module.CAMERA, "cable": module.CABLE, "moniteur": module.MONITEUR}
    assert {key: by_id[key]["asset"]["id"] for key in chain} == chain
    xs = [by_id[key]["position"][0]["x"] for key in chain]
    assert xs == sorted(xs)
    assert {"Caméra", "Câble", "Écran"} <= set(_texts(final))
    # Le câble n'existe pas avant que Sam en parle ; l'image n'est sur l'écran qu'une fois expliquée.
    first = {obj["id"] for obj in _objects(storyboard["scenes"][0])}
    assert "cable" not in first and "image-ecran" not in first
    shown_on_screen = [scene["id"] for scene in storyboard["scenes"]
                       if any(obj["id"] == "image-ecran" for obj in _objects(scene))]
    assert shown_on_screen == ["afficher", "recap"]
    # Le signal circule dans le sens caméra → écran.
    for scene in storyboard["scenes"]:
        for obj in _objects(scene):
            if obj["id"] == "signal-cable":
                assert obj["position"][0]["x"] < obj["position"][-1]["x"]


def test_only_nora_and_sam_with_rig_173_names(storyboard):
    lab_ids = {asset["id"] for asset in _load(CATALOG)["assets"]}
    for scene in storyboard["scenes"]:
        for obj in _objects(scene):
            if obj["type"] != "asset":
                continue
            asset_id = obj["asset"]["id"]
            assert asset_id in lab_ids | {NORA, SAM}, asset_id  # aucun personnage inventé
            if asset_id in (NORA, SAM):
                assert {e["name"] for e in obj["expressions"]} <= RIG_173_EXPRESSIONS
                assert {g["name"] for g in obj["gestures"]} <= RIG_173_GESTURES
            else:
                assert "expressions" not in obj and "gestures" not in obj and "lipsync" not in obj
    assert all(ref["version"] == "latest" for _, ref in iter_asset_refs(storyboard))
    assert "frozen" not in storyboard


# -- import et gel -----------------------------------------------------------------------------------------------
def _stub_people_catalog():
    """Catalogue minimal reprenant les identifiants et noms déclarés du rig #173, si celui-ci n'est pas livré."""
    def manifest(key):
        return {"format": "svg", "width": 600, "height": 900, "anchor": {"x": 0.5, "y": 0.98},
                "parts": ["puppet", "mouth-visemes"], "expressions": sorted(RIG_173_EXPRESSIONS),
                "gestures": sorted(RIG_173_GESTURES),
                "files": [{"role": "base", "path": f"animation-assets/{key}/v1/{key}-face.svg", "sha256": "0" * 64}]}
    return {"schema": "lody.animation-assets/1", "assets": [
        {"id": asset_id, "name": f"{name} — marionnette", "kind": "character", "description": "", "is_active": True,
         "character": name, "location": None,
         "versions": [{"version": 1, "manifest": manifest(name.lower()), "sha256": manifest_sha256(manifest(name.lower())),
                       "notes": ""}]}
        for asset_id, name in ((NORA, "Nora"), (SAM, "Sam"))]}


def _freeze(tmp_path, people_catalog, storyboard):
    db_path = tmp_path / "lody.sqlite3"
    project = ProjectRepository(db_path).create(name="Labo caméra moniteur")
    for name in ("Nora", "Sam"):
        CharacterRepository(db_path).create(project.id, name=name)
    repo = AnimationAssetRepository(db_path, clock=lambda: "2026-11-12T00:00:00+00:00")
    id_map, warnings = repo.import_catalog(project.id, people_catalog)
    assert warnings == []
    lab_map, lab_warnings = repo.import_catalog(project.id, _load(CATALOG))
    assert lab_warnings == []
    id_map.update(lab_map)
    return freeze_document(repo, project.id, remap_assets(copy.deepcopy(storyboard), id_map)), id_map


def test_storyboard_freezes_against_the_rig_173_declaration(tmp_path, storyboard):
    frozen, id_map = _freeze(tmp_path, _stub_people_catalog(), storyboard)
    validate_document(frozen, require_pinned=True)
    assert len(frozen["frozen"]["assets"]) == 7
    assert {ref["id"] for _, ref in iter_asset_refs(frozen)} == set(id_map.values())


@pytest.mark.skipif(not PEOPLE_CATALOG.exists(), reason="catalogue Nora/Sam (#173, PR #180) pas encore fusionné")
def test_storyboard_freezes_with_the_delivered_nora_and_sam(tmp_path, storyboard):
    frozen, _ = _freeze(tmp_path, _load(PEOPLE_CATALOG), storyboard)
    validate_document(frozen, require_pinned=True)


def test_freeze_refuses_a_gesture_outside_the_rig(tmp_path, storyboard):
    broken = copy.deepcopy(storyboard)
    for obj in _objects(broken["scenes"][0]):
        if obj["id"] == "sam":
            obj["gestures"][0]["name"] = "danser"
    with pytest.raises(Exception, match="danser|geste"):
        _freeze(tmp_path, _stub_people_catalog(), broken)
