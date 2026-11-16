"""Préproduction de l'épisode Voltix « Le Genlock » (#141) : livrables documentaires vérifiés.

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
DOC = DOCS / "voltix-genlock.md"
TEMPLATE = DOCS / "voltix-genlock" / "template.json"
STORYBOARD = DOCS / "voltix-genlock" / "storyboard.json"
VOLTIX_EXPRESSIONS = {"concentration", "confusion", "neutre", "sourire", "surprise"}  # rig #136 v1
VOLTIX_GESTURES = {"cligner", "montrer", "rebond", "saluer", "tenir-cable"}
VOLTIX_PLACEHOLDER = "ast_136000000001"
EPISODE_PREFIX = "ast_141"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _standard_script():
    text = DOC.read_text(encoding="utf-8")
    block = text.split("<!-- script-standard:debut -->")[1].split("<!-- script-standard:fin -->")[0]
    return [line.split("|")[3].strip() for line in block.splitlines() if re.match(r"^\| \d+ \|", line)]


def _words(line):
    return len(re.findall(r"[\wÀ-ÿ'’-]+", line))


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


def test_each_line_fits_its_scene_at_fast_pace():
    wpm = brief.WORDS_PER_MINUTE["rapide"]
    for scene in _load(STORYBOARD)["scenes"]:
        spoken = _words(scene["intent"]["narration"]) * 60 / wpm
        assert spoken <= scene["duration_frames"] / 30, scene["id"]


def test_script_is_tts_safe():
    for line in _standard_script():
        assert not re.search(r"\d", line), line
        assert not re.search(r"\b[A-Z]{2,}\b", line), line


def test_voltix_uses_only_rig_136_names_and_refs_are_provisional():
    document = _load(STORYBOARD)
    for scene in document["scenes"]:
        for layer in scene["render"]["layers"]:
            for obj in layer["objects"]:
                if obj.get("asset", {}).get("id") != VOLTIX_PLACEHOLDER:
                    continue
                assert {e["name"] for e in obj.get("expressions", [])} <= VOLTIX_EXPRESSIONS
                assert {g["name"] for g in obj.get("gestures", [])} <= VOLTIX_GESTURES
    refs = [ref for _, ref in iter_asset_refs(document)]
    # Identifiants provisoires : à remapper puis figer à l'import, jamais figés ici.
    assert all(ref["version"] == "latest" for ref in refs)
    assert all(ref["id"] == VOLTIX_PLACEHOLDER or ref["id"].startswith(EPISODE_PREFIX) for ref in refs)
    assert "frozen" not in document


def test_accuracy_criteria_are_stated():
    text = DOC.read_text(encoding="utf-8")
    script = " ".join(_standard_script())
    # Genlock ≠ timecode, pas obligatoire partout, synchroniseurs, la référence ne porte pas l'image.
    assert "pas un timecode" in script
    assert "pas obligatoire pour tous les directs" in script
    assert "resynchroniser des sources qui ne sont pas verrouillées" in script
    assert "ne transporte pas l'image" in script
    # Aucune affirmation qu'une source non verrouillée ne peut jamais être commutée.
    assert not re.search(r"impossible|jamais", script, re.IGNORECASE)
    assert "docs.ptzoptics.com" in text


def test_short_version_targets_45_seconds():
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## 4. Version courte")[1].split("## 5.")[0]
    lines = [row.split("|")[3].strip() for row in section.splitlines() if re.match(r"^\| \d+ \|", row)]
    seconds = sum(_words(line) for line in lines) * 60 / brief.WORDS_PER_MINUTE["rapide"]
    assert 30 <= seconds <= 45
    for line in lines:
        assert not re.search(r"\d", line), line
