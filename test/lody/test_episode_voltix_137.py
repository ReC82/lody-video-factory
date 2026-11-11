"""Épisode #137 « Voltix explique le câble SDI » : schémas SVG, catalogue, storyboard et preset.

Vérifie que les schémas livrés sont reproductibles (générateur), techniquement cohérents (ordre des couches
du coaxial, débits proportionnels, trois états BNC), que le catalogue s'importe avec celui de Voltix (#136),
que le storyboard des 9 scènes est valide et se fige (#119), que le texte parlé est sûr pour la synthèse et
que le preset suit le vrai format des templates (#110). Aucun réseau, aucun rendu, aucun fournisseur payant.
"""

import hashlib
import importlib.util
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lody import templates
from lody.animation.assets import AnimationAssetRepository, manifest_sha256, validate_manifest
from lody.animation.contract import validate_document
from lody.animation.freeze import freeze_document, remap_assets
from lody.characters import CharacterRepository
from lody.projects import ProjectRepository

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "resource"
DOCS = ROOT / "docs" / "animation" / "voltix-137"
SVG_NS = "{http://www.w3.org/2000/svg}"


def _module(name):
    scripts = str(ROOT / "scripts" / "animation")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "animation" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _module("sdi_137_assets")
episode = _module("episode_voltix_137")


@pytest.fixture(scope="module")
def catalog():
    return json.loads((RESOURCE / "animation-assets" / "sdi-catalogue.json").read_text(encoding="utf-8"))


def _svg(key):
    return ET.fromstring((RESOURCE / gen.rel_path(key)).read_text(encoding="utf-8"))


def _groups(root):
    return {el.get("id"): el for el in root.iter(f"{SVG_NS}g") if el.get("id")}


def test_committed_files_match_the_generator(catalog):
    for rel, content in gen.outputs().items():
        assert (RESOURCE / rel).read_text(encoding="utf-8") == content, rel
    assert catalog == gen.build_catalog(RESOURCE)
    assert (DOCS / "planche-schemas.svg").read_text(encoding="utf-8") == gen.build_board(RESOURCE)


def test_style_matches_voltix():
    """Contour de Voltix, jamais de noir pur, aucun dégradé ni image aplatie (la mire, contenu d'écran, n'a
    pas de contour)."""
    for key in gen.ASSETS:
        text = (RESOURCE / gen.rel_path(key)).read_text(encoding="utf-8")
        assert key == "mire" or "#2A1F14" in text, key
        assert not re.search(r'(stroke|fill)="#000(000)?"', text), key
        assert "Gradient" not in text and "<image" not in text, key


def test_coax_layers_are_ordered_and_stack_into_a_stepped_cut():
    """Âme < diélectrique < blindage < gaine, en épaisseur ; plus la couche est interne, plus elle dépasse."""
    names = list(gen.COAX_LONG)
    assert names == ["coax-ame", "coax-isolant", "coax-blindage", "coax-gaine"]
    ends = [gen.COAX_LONG[n][1] for n in names]
    halves = [gen.COAX_LONG[n][2] for n in names]
    assert ends == sorted(ends, reverse=True) and halves == sorted(halves)
    blindage = _groups(_svg("coax-blindage"))
    assert {"feuille", "tresse"} <= set(blindage)
    # Le storyboard empile les calques de l'âme (au fond) à la gaine (devant) et les écarte dans cet ordre.
    assert list(episode.COAX_SPREAD) == names
    assert [episode.COAX_SPREAD[n] for n in names] == sorted(episode.COAX_SPREAD.values(), reverse=True)


def test_cross_section_rings_and_legends():
    root = _svg("coax-transversale")
    groups = _groups(root)
    radii = [float(groups[i].find(f"{SVG_NS}circle").get("r")) for i in
             ("ame", "dielectrique", "feuille", "tresse", "gaine")]
    assert radii == sorted(radii)
    # Proportions d'un coaxial SDI type 1694A : âme / diélectrique ≈ 1,0 / 4,6 mm.
    assert 0.18 < radii[0] / radii[1] < 0.26
    legend = "".join(groups["legendes"].itertext())
    for label in ("Gaine", "tresse", "feuille", "Diélectrique", "Âme en cuivre"):
        assert label in legend


def test_bnc_board_shows_three_states_with_j_slot_and_stud():
    root = _svg("bnc-etats")
    groups = _groups(root)
    assert {"etat-debranche", "etat-insere", "etat-verrouille"} <= set(groups)
    text = "".join(root.itertext())
    for word in ("Débranché", "Inséré", "Verrouillé", "¼ de tour", "fente en J", "ergot"):
        assert word in text
    for word in ("RJ45", "XLR"):
        assert word not in text
    assert {"bague", "broche"} <= set(_groups(_svg("bnc-bague-face")))
    assert "ergots" in _groups(_svg("bnc-ergots-face"))


def test_rate_bars_are_proportional_to_nominal_sdi_rates():
    widths = {}
    for key in gen.RATES:
        bar = _groups(_svg(key))["barre"].find(f"{SVG_NS}rect")
        widths[key] = float(bar.get("width"))
    assert gen.RATES["debit-12g"][1] == 11.88 and gen.RATES["debit-3g"][1] == 2.97
    assert widths["debit-12g"] == 700
    assert widths["debit-3g"] == pytest.approx(700 * 2.97 / 11.88, abs=1)
    assert widths["debit-hd"] == pytest.approx(700 * 1.485 / 11.88, abs=1)


def test_catalog_manifests_and_hashes(catalog):
    assert catalog["schema"] == "lody.animation-assets/1"
    ids = [asset["id"] for asset in catalog["assets"]]
    assert len(ids) == len(set(ids)) == len(gen.ASSETS)
    assert set(episode.A.values()) <= set(ids)
    for asset in catalog["assets"]:
        assert asset["kind"] == "prop"
        version = asset["versions"][0]
        manifest = validate_manifest(version["manifest"])
        assert version["sha256"] == manifest_sha256(manifest)
        for item in manifest["files"]:
            assert hashlib.sha256((RESOURCE / item["path"]).read_bytes()).hexdigest() == item["sha256"]


def test_script_separates_displayed_and_spoken_text():
    assert list(episode.LINES) == ["accroche", "liaison", "coupe", "bnc", "distinction", "audio", "debits",
                                   "limites", "conclusion"]
    for lines in episode.LINES.values():
        for display, spoken in lines:
            assert 0 < len(display) <= 60
            # Le texte parlé ne contient ni sigle à épeler, ni chiffre, ni symbole à prononcer.
            assert not re.search(r"SDI|BNC|HD|\d|Ω|=|/", spoken), spoken
    spoken = " ".join(s for lines in episode.LINES.values() for _, s in lines)
    assert "èsse dé i" in spoken and "bé ène cé" in spoken and "soixante images par seconde" in spoken


def test_storyboard_is_valid_and_bounded():
    layout = episode.estimated_layout()
    document = episode.build_storyboard(layout)
    assert validate_document(document) == document
    assert [s["id"] for s in document["scenes"]] == list(episode.LINES)
    seconds = sum(s["duration_frames"] for s in document["scenes"]) / episode.FPS
    assert 50 <= seconds <= 75  # estimation sans voix ; la durée réelle est mesurée au rendu
    for scene in document["scenes"]:
        cues = scene["render"]["speech"]["cues"]
        assert all(c["end"] <= scene["duration_frames"] for c in cues)
        puppets = [o for layer in scene["render"]["layers"] for o in layer["objects"] if o["id"] == "voltix"]
        assert len(puppets) == 1 and puppets[0]["lipsync"] is True


def test_scene_tails_are_trimmed_to_respect_the_duration_bound():
    tails = dict(episode.TAIL)
    target = int(episode.TARGET_SECONDS * episode.FPS)
    fitted = episode.fit_tails(target + 30, tails)
    assert sum(tails.values()) - sum(fitted.values()) == 30
    assert min(fitted.values()) >= episode.MIN_TAIL and fitted["conclusion"] < tails["conclusion"]
    assert episode.fit_tails(target - 10, tails) == tails
    floor = episode.fit_tails(target + 10_000, tails)
    assert set(floor.values()) == {episode.MIN_TAIL}


def test_storyboard_freezes_against_both_catalogs(tmp_path):
    db_path = tmp_path / "lody.sqlite3"
    project = ProjectRepository(db_path).create(name="Projet Voltix SDI")
    CharacterRepository(db_path).create(project.id, name="Voltix")
    repo = AnimationAssetRepository(db_path, clock=lambda: "2026-11-11T00:00:00+00:00")
    id_map = {}
    for data in episode.load_catalogs():
        mapping, warnings = repo.import_catalog(project.id, data)
        assert warnings == []
        id_map.update(mapping)
    frozen = freeze_document(repo, project.id, remap_assets(episode.build_storyboard(episode.estimated_layout()),
                                                              id_map))
    used = {item["name"] for item in frozen["frozen"]["assets"]}
    assert "Voltix — marionnette" in used and "Coax — coupe transversale légendée" in used
    assert all(item["version"] == 1 for item in frozen["frozen"]["assets"])


def test_preset_uses_the_real_template_format():
    preset = json.loads((DOCS / "preset-production.json").read_text(encoding="utf-8"))
    clean = templates.validate_fields(preset["template"])
    assert clean["payload"]["duration_min"] == 55 and clean["payload"]["duration_max"] == 65
    assert clean["payload"]["narration_pace"] == "rapide"
    assert preset["project"]["render_mode"] == "animation"
    assert (ROOT / preset["storyboard"]).is_file()
    for path in preset["project"]["catalogs"]:
        assert (ROOT / path).is_file()
