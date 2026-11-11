"""Épisode #137, V2 éditoriale (#160) : nouveaux schémas, script TTS-safe, storyboard et musique de test V2.

Vérifie que la V2 corrige les retours du propriétaire sans toucher à la V1 : prononciations sûres (aucun
sigle brut envoyé à la synthèse, pas de pause entre « 3G » et « SDI »), affirmations techniques prudentes,
schémas reproductibles et cohérents (liaison réellement branchée, coaxial coté donnant ≈ 75 Ω), textes qui
tiennent dans le cadre 9:16, storyboard valide et figeable avec les trois catalogues. Aucun réseau, aucun
rendu, aucun fournisseur payant.
"""

import importlib.util
import json
import math
import re
import sys
import wave
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from lody.animation.assets import AnimationAssetRepository, manifest_sha256, validate_manifest
from lody.animation.contract import validate_document
from lody.animation.freeze import freeze_document, remap_assets
from lody.characters import CharacterRepository
from lody.projects import ProjectRepository

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = ROOT / "resource"
DOCS = ROOT / "docs" / "animation" / "voltix-137-v2"
SVG_NS = "{http://www.w3.org/2000/svg}"


def _module(name):
    scripts = str(ROOT / "scripts" / "animation")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "animation" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _module("sdi_160_assets")
gen_v1 = _module("sdi_137_assets")
episode = _module("episode_voltix_160")
musique = _module("musique_v2")


@pytest.fixture(scope="module")
def catalog():
    return json.loads((RESOURCE / "animation-assets" / gen.CATALOG_NAME).read_text(encoding="utf-8"))


def _svg(key):
    return ET.fromstring((RESOURCE / gen.rel_path(key)).read_text(encoding="utf-8"))


def _groups(root):
    return {el.get("id"): el for el in root.iter(f"{SVG_NS}g") if el.get("id")}


def _all_spoken():
    return " ".join(spoken for lines in episode.LINES.values() for _, spoken in lines)


# -- schémas -----------------------------------------------------------------------------------------------------
def test_committed_files_match_the_generator(catalog):
    for rel, content in gen.outputs().items():
        assert (RESOURCE / rel).read_text(encoding="utf-8") == content, rel
    assert catalog == gen.build_catalog(RESOURCE)
    assert (DOCS / "planche-schemas-v2.svg").read_text(encoding="utf-8") == gen.build_board(RESOURCE)


def test_v1_assets_are_untouched_and_ids_do_not_collide(catalog):
    """V2 réversible : les schémas V1 restent ceux de leur générateur, aucun identifiant n'est partagé."""
    for rel, content in gen_v1.outputs().items():
        assert (RESOURCE / rel).read_text(encoding="utf-8") == content, rel
    assert not set(gen.ASSETS) & set(gen_v1.ASSETS)
    v1_ids = {spec[0] for spec in gen_v1.ASSETS.values()}
    assert not {a["id"] for a in catalog["assets"]} & v1_ids


def test_style_matches_voltix():
    for key in gen.ASSETS:
        text = (RESOURCE / gen.rel_path(key)).read_text(encoding="utf-8")
        assert "#2A1F14" in text, key
        assert not re.search(r'(stroke|fill)="#000(000)?"', text), key
        assert "Gradient" not in text and "<image" not in text, key


def test_catalog_manifests_and_hashes(catalog):
    assert catalog["schema"] == "lody.animation-assets/1"
    ids = [asset["id"] for asset in catalog["assets"]]
    assert len(ids) == len(set(ids)) == len(gen.ASSETS)
    for asset in catalog["assets"]:
        version = asset["versions"][0]
        manifest = validate_manifest(version["manifest"])
        assert version["sha256"] == manifest_sha256(manifest)


def test_connected_link_has_no_floating_cable():
    """Le câble de la conclusion part EXACTEMENT de l'embase SDI OUT de la caméra et arrive sur l'entrée du
    moniteur (positions lues dans les SVG V1) ; une fiche BNC est montée sur chacune."""
    camera = ET.fromstring(gen_v1.build_camera())
    monitor = ET.fromstring(gen_v1.build_monitor())

    def port(root, group):
        circle = _groups(root)[group].find(f"{SVG_NS}circle")
        return float(circle.get("cx")), float(circle.get("cy"))

    cam_port, mon_port = port(camera, "sortie-sdi"), port(monitor, "entree-sdi")
    assert gen.CAM_PORT == (gen.CAM_AT[0] + cam_port[0], gen.CAM_AT[1] + cam_port[1])
    assert gen.MON_PORT == (gen.MON_AT[0] + mon_port[0], gen.MON_AT[1] + mon_port[1])
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", gen.LINK_PATH)]
    assert tuple(numbers[:2]) == gen.CAM_PORT and tuple(numbers[-2:]) == gen.MON_PORT
    groups = _groups(_svg("liaison-connectee"))
    assert {"camera", "moniteur", "coaxial", "fiches-bnc"} <= set(groups)
    plugs = [(float(c.get("cx")), float(c.get("cy"))) for c in groups["fiches-bnc"].iter(f"{SVG_NS}circle")]
    assert gen.CAM_PORT in plugs and gen.MON_PORT in plugs


def test_coax_dimensions_give_75_ohms():
    """Z0 = (60 / √εr) · ln(D / d) : avec les proportions dessinées (D/d = 4,6, celles de la V1) et un
    diélectrique en polyéthylène expansé (εr ≈ 1,5), on retrouve bien ≈ 75 Ω."""
    ratio = gen.COAX_D / gen.COAX_SMALL_D
    assert 60 / math.sqrt(1.5) * math.log(ratio) == pytest.approx(75, abs=2)
    circles = sorted(float(c.get("r")) for c in _groups(_svg("coax-cotes"))["coupe"].iter(f"{SVG_NS}circle"))
    assert circles[1] / circles[0] == pytest.approx(ratio)  # isolant / âme
    text = "".join(_svg("coax-cotes").itertext())
    assert "75 Ω" in text and "isolant" in text


def test_serial_bits_and_equipment_assets():
    assert set(gen.BITS) == {"0", "1"}
    assert "un bit après l’autre" in "".join(_svg("bits-serie").itertext())
    assert "SDI → HDMI" in "".join(_svg("convertisseur").itertext())
    assert {"entree-sdi", "sortie-hdmi"} <= set(_groups(_svg("convertisseur")))
    assert "entree-sdi" in _groups(_svg("enregistreur"))
    assert "HDMI" in "".join(_svg("salon-hdmi").itertext())


# -- script et prononciations ------------------------------------------------------------------------------------
def test_spoken_text_is_tts_safe():
    for lines in episode.LINES.values():
        for display, spoken in lines:
            assert 0 < len(display) <= 60
            assert not re.search(r"[A-Z]{2,}|\d|Ω|=|/|→", spoken), spoken
    spoken = _all_spoken()
    for rejected in episode.REJECTED_SPOKEN:
        assert rejected not in spoken, rejected
    # HD / 3G / 12G liés à SDI, sans virgule ni pause interne.
    for term in ("HD-SDI", "3G-SDI", "12G-SDI"):
        assert episode.p(term) in spoken and "," not in episode.p(term)
    assert episode.p("SDI") == "èss di aï"


def test_every_displayed_term_has_its_spoken_form():
    for lines in episode.LINES.values():
        for display, spoken in lines:
            for term in ("12G-SDI", "3G-SDI", "HD-SDI", "Serial Digital Interface", "HDMI", "BNC", "broadcast"):
                if term in display:
                    assert episode.p(term) in spoken, (term, display)
            if re.search(r"\bSDI\b", re.sub(r"\w+-SDI", "", display)):
                assert episode.p("SDI") in spoken, display
            if "75 ohms" in display:
                assert episode.p("75 Ω") in spoken, display


def test_pronunciation_check_reports_gaps():
    expected = [ph for _, ph in episode.PRONUNCIATIONS.values()]
    assert episode.check_pronunciations(lambda texts: list(expected)) == []
    wrong = list(expected)
    wrong[0] = "lə- zdˈi"
    gaps = episode.check_pronunciations(lambda texts: wrong)
    assert len(gaps) == 1 and gaps[0].startswith("SDI")


def test_script_covers_the_owner_corrections_with_careful_wording():
    display = " ".join(d for lines in episode.LINES.values() for d, _ in lines)
    assert list(episode.LINES)[0] == "accroche" and "HDMI" in episode.LINES["accroche"][0][0]
    assert "Tu vois ce câble" not in display  # accroche V1 refusée
    assert "Serial Digital Interface" in display and "numérique série" in display
    assert "broadcast" in display and "diffusion pro" in display
    for word in ("grille de routage", "moniteur", "enregistreur", "mélangeur", "convertisseur"):
        assert word in display
    assert "peut aussi intégrer le son" in display  # possible, jamais promis
    assert "le son peut aussi voyager avec l'image" not in display
    assert "impédance caractéristique" in display and "Pas une résistance" in display
    assert "pas des résolutions" in display
    assert "câble et connecteurs" in display.lower()


def test_onscreen_texts_fit_the_9_16_frame():
    """Les styles du moteur Remotion sont sans retour à la ligne : largeur estimée (DejaVu Sans gras) bornée."""
    em = {"title": 88, "subtitle": 54, "label": 40}
    padding = {"title": 0, "subtitle": 60, "label": 50}
    document = episode.build_storyboard(episode.estimated_layout())
    for scene in document["scenes"]:
        for layer in scene["render"]["layers"]:
            for obj in layer["objects"]:
                if obj["type"] != "text":
                    continue
                style, content = obj["text"]["style"], obj["text"]["content"]
                scale = max(kf["value"] for kf in obj.get("scale", [{"value": 1.0}]))
                width = (len(content) * 0.64 * em[style] + padding[style]) * scale
                x = obj["position"][0]["x"]
                assert x - width / 2 >= 0 and x + width / 2 <= episode.WIDTH, (scene["id"], content, round(width))


# -- storyboard --------------------------------------------------------------------------------------------------
def test_storyboard_is_valid_and_bounded():
    document = episode.build_storyboard(episode.estimated_layout())
    assert validate_document(document) == document
    assert [s["id"] for s in document["scenes"]] == list(episode.LINES) == list(episode.BUILDERS)
    seconds = sum(s["duration_frames"] for s in document["scenes"]) / episode.FPS
    assert 50 <= seconds <= 75  # estimation sans voix ; la durée réelle est bornée au rendu (55–65 s)
    for scene in document["scenes"]:
        assert all(c["end"] <= scene["duration_frames"] for c in scene["render"]["speech"]["cues"])
        puppets = [o for layer in scene["render"]["layers"] for o in layer["objects"] if o["id"] == "voltix"]
        assert len(puppets) == 1 and puppets[0]["lipsync"] is True


def test_fit_tails_respects_the_target():
    target = int(episode.TARGET_SECONDS * episode.FPS)
    fitted = episode.fit_tails(target + 20, episode.TAIL)
    assert sum(episode.TAIL.values()) - sum(fitted.values()) == 20
    assert episode.fit_tails(target - 5, episode.TAIL) == episode.TAIL


def test_storyboard_freezes_against_the_three_catalogs(tmp_path):
    db_path = tmp_path / "lody.sqlite3"
    project = ProjectRepository(db_path).create(name="Projet Voltix SDI V2")
    CharacterRepository(db_path).create(project.id, name="Voltix")
    repo = AnimationAssetRepository(db_path, clock=lambda: "2026-11-11T00:00:00+00:00")
    id_map = {}
    catalogs = episode.load_catalogs()
    assert len(catalogs) == 3
    for data in catalogs:
        mapping, warnings = repo.import_catalog(project.id, data)
        assert warnings == []
        id_map.update(mapping)
    frozen = freeze_document(repo, project.id, remap_assets(episode.build_storyboard(episode.estimated_layout()),
                                                              id_map))
    used = {item["name"] for item in frozen["frozen"]["assets"]}
    assert {"Voltix — marionnette", "Liaison SDI connectée", "Salon : box → HDMI → télé",
            "Coaxial coté (d, D)"} <= used


# -- musique -----------------------------------------------------------------------------------------------------
def test_music_v2_is_deterministic_and_never_longer_than_asked(tmp_path):
    first = musique.render(6.0)
    assert len(first) == 6 * musique.SAMPLE_RATE
    assert np.array_equal(first, musique.render(6.0))
    assert np.max(np.abs(first)) == pytest.approx(0.6, abs=1e-6)
    assert abs(first[-1]) < 1e-3  # fondu de sortie
    path = tmp_path / "m.wav"
    musique.write(path, 3.5)
    with wave.open(str(path), "rb") as handle:
        assert handle.getnframes() == int(3.5 * musique.SAMPLE_RATE) and handle.getnchannels() == 2
    assert musique.BPM > 100  # plus rythmée que la musique de test V1 (100 BPM)
