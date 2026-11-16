"""Préproduction de l'épisode Voltix « Le mélangeur vidéo » (#142) : livrables documentaires vérifiés.

Le preset doit être accepté tel quel par les templates de production (#110), le storyboard par le
contrat ``lody.animation/1`` (#119), et la narration du storyboard doit être exactement le script
standard du document. Lecture de fichiers uniquement : aucune base, aucun réseau, aucun appel payant.
"""

import json
import re
from pathlib import Path

from lody import brief
from lody.animation.contract import duration_seconds, iter_asset_refs, validate_document
from lody.templates import validate_fields

DOCS = Path(__file__).resolve().parents[2] / "docs" / "episodes"
DOC = DOCS / "voltix-melangeur.md"
TEMPLATE = DOCS / "voltix-melangeur" / "template.json"
STORYBOARD = DOCS / "voltix-melangeur" / "storyboard.json"
VOLTIX_EXPRESSIONS = {"concentration", "confusion", "neutre", "sourire", "surprise"}  # rig #136 v1
VOLTIX_GESTURES = {"cligner", "montrer", "rebond", "saluer", "tenir-cable"}
VOLTIX_PLACEHOLDER = "ast_136000000001"
EPISODE_PREFIX = "ast_142"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _standard_script():
    text = DOC.read_text(encoding="utf-8")
    block = text.split("<!-- script-standard:debut -->")[1].split("<!-- script-standard:fin -->")[0]
    return [line.split("|")[3].strip() for line in block.splitlines() if re.match(r"^\| \d+ \|", line)]


def _objects(document):
    for scene in document["scenes"]:
        for layer in scene["render"]["layers"]:
            for obj in layer["objects"]:
                yield scene, obj


def test_template_is_a_valid_production_template():
    raw = _load(TEMPLATE)
    clean = validate_fields(raw)
    # Rien n'est perdu ni réécrit à la validation : le preset est déjà au format canonique.
    assert clean["payload"] == raw["payload"]
    assert 45 <= clean["payload"]["duration_min"] <= clean["payload"]["duration_max"] <= 65
    # Aucune voix ni réglage permanent du projet dans le preset d'épisode.
    assert "voice_id" not in json.dumps(raw)


def test_storyboard_is_valid_and_vertical_full_hd():
    document = validate_document(_load(STORYBOARD))
    assert document["canvas"] == {"fps": 30, "width": 1080, "height": 1920}
    assert 45 <= duration_seconds(document) <= 65
    for scene in document["scenes"]:
        assert 5 <= scene["duration_frames"] / 30 <= 7, scene["id"]


def test_storyboard_narration_is_the_documented_script():
    scenes = _load(STORYBOARD)["scenes"]
    assert [scene["intent"]["narration"] for scene in scenes] == _standard_script()


def test_narration_fits_the_fast_pace_and_is_tts_safe():
    script = _standard_script()
    words = sum(len(line.split()) for line in script)
    seconds = words * 60 / brief.WORDS_PER_MINUTE["rapide"]
    assert seconds <= duration_seconds(_load(STORYBOARD))
    for line in script:
        assert not re.search(r"\d", line), line
        assert not re.search(r"\b[A-Z]{2,}\b", line), line


def test_assets_are_provisional_and_voltix_uses_rig_136_names():
    document = _load(STORYBOARD)
    for _, obj in _objects(document):
        if obj.get("asset", {}).get("id") != VOLTIX_PLACEHOLDER:
            continue
        assert {e["name"] for e in obj.get("expressions", [])} <= VOLTIX_EXPRESSIONS
        assert {g["name"] for g in obj.get("gestures", [])} <= VOLTIX_GESTURES
    refs = [ref for _, ref in iter_asset_refs(document)]
    assert all(ref["id"] == VOLTIX_PLACEHOLDER or ref["id"].startswith(EPISODE_PREFIX) for ref in refs)
    # Identifiants provisoires : à remapper puis figer à l'import, jamais figés ici.
    assert all(ref["version"] == "latest" for ref in refs)
    assert "frozen" not in document


def test_preview_and_program_swap_on_transition():
    scene = next(s for s in _load(STORYBOARD)["scenes"] if s["id"] == "bascule")
    texts = {obj["id"]: obj for _, obj in _objects({"scenes": [scene]}) if obj["type"] == "text"}

    def visible_at_end(obj):
        return obj["opacity"][-1]["value"] == 1

    assert visible_at_end(texts["pgm-apres"]) and texts["pgm-apres"]["text"]["content"] == "Caméra 2"
    assert visible_at_end(texts["pvw-apres"]) and texts["pvw-apres"]["text"]["content"] == "Caméra 1"
    assert not visible_at_end(texts["pgm-avant"]) and not visible_at_end(texts["pvw-avant"])


def test_accuracy_criteria_are_stated():
    text = DOC.read_text(encoding="utf-8")
    script = " ".join(_standard_script())
    assert "multivue" in script and "réellement diffusée" in script
    assert "Selon le modèle" in script and "Sur beaucoup de mélangeurs" in script
    assert "sélectionne et combine" in script
    # La sortie programme ne part pas « directement à l'antenne ».
    assert "diffusion ou l'enregistrement" in script
    assert "ATEMMiniManual.pdf" in text
