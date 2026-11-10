"""Contrat de storyboard animé ``lody.animation/1`` (#119) : validation stricte, empreinte, maquette.

Couche pure : aucune base, aucun réseau, aucun rendu.
"""

import copy
import json
from pathlib import Path

import pytest

from lody.animation.contract import (
    SCHEMA,
    AnimationValidationError,
    canonical_json,
    document_sha256,
    duration_seconds,
    iter_asset_refs,
    total_frames,
    validate_document,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "animation" / "exemple-deux-scenes.json"
# Valeur au format d'une clé, construite à l'exécution (jamais écrite telle quelle dans le dépôt).
FAKE_KEY = "s" + "k-" + "Z" * 24


@pytest.fixture
def example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _errors(document, **kwargs):
    with pytest.raises(AnimationValidationError) as caught:
        validate_document(document, **kwargs)
    return caught.value.errors


def _mimo(document):
    return document["scenes"][0]["render"]["layers"][1]["objects"][0]


def test_example_two_scenes_is_valid(example):
    clean = validate_document(example)
    assert clean == example and clean is not example
    assert clean["schema"] == SCHEMA
    assert [scene["id"] for scene in clean["scenes"]] == ["accueil", "graphique"]
    assert total_frames(clean) == 330 and duration_seconds(clean) == 11.0
    # intention narrative et instructions techniques sont deux blocs distincts
    for scene in clean["scenes"]:
        assert set(scene) == {"id", "duration_frames", "intent", "render"}
        assert "narration" in scene["intent"] and "layers" in scene["render"]


def test_example_covers_every_element_of_the_ticket(example):
    """Durée, calques/objets, asset, position, trajectoire, échelle, expression, geste, texte, narration,
    timecodes, caméra, transitions et repli : chaque élément du ticket figure dans la maquette."""
    text = canonical_json(example)
    for key in ("duration_frames", "layers", "objects", '"asset"', "position", "easing", "scale", "rotation",
                "expressions", "gestures", '"text"', "narration", "cues", "camera", "transition_in",
                "transition_out", "fallback", "lipsync"):
        assert key in text, key
    fallbacks = {scene["render"]["fallback"]["type"] for scene in example["scenes"]}
    assert fallbacks == {"still", "asset"}


@pytest.mark.parametrize("locate", [
    lambda d: d,
    lambda d: d["canvas"],
    lambda d: d["scenes"][0],
    lambda d: d["scenes"][0]["intent"],
    lambda d: d["scenes"][0]["render"],
    lambda d: d["scenes"][0]["render"]["layers"][1],
    lambda d: d["scenes"][0]["render"]["layers"][1]["objects"][0],
    lambda d: d["scenes"][0]["render"]["camera"],
])
def test_unknown_keys_are_refused_everywhere(example, locate):
    """Aucun champ « code »/« script » ne peut être glissé : clé inconnue refusée à tous les niveaux."""
    locate(example)["onRender"] = "() => fetch('https://exemple.invalid')"
    assert any("onRender" in message for message in _errors(example).values())


def test_easing_is_an_enum_never_code(example):
    _mimo(example)["easing"] = "t => t * t"
    assert "scenes[0].render.layers[1].objects[0].easing" in _errors(example)


def test_schema_and_canvas(example):
    bad = copy.deepcopy(example)
    bad["schema"] = "lody.animation/2"
    assert "schema" in _errors(bad)
    bad = copy.deepcopy(example)
    bad["canvas"]["fps"] = 29
    assert "canvas.fps" in _errors(bad)
    bad = copy.deepcopy(example)
    bad["canvas"]["width"] = 1081
    assert "canvas.width" in _errors(bad)
    assert "$" in _errors([])


def test_keyframes_bounds_and_order(example):
    path = "scenes[0].render.layers[1].objects[0]"
    bad = copy.deepcopy(example)
    _mimo(bad)["position"] = [{"frame": 10, "x": 0, "y": 0}, {"frame": 10, "x": 1, "y": 1}]
    assert f"{path}.position[1].frame" in _errors(bad)
    bad = copy.deepcopy(example)
    _mimo(bad)["position"][1]["frame"] = 151  # durée de la scène = 150 images
    assert f"{path}.position[1].frame" in _errors(bad)
    bad = copy.deepcopy(example)
    _mimo(bad)["scale"][0]["value"] = float("nan")
    assert f"{path}.scale[0].value" in _errors(bad)
    bad = copy.deepcopy(example)
    _mimo(bad)["position"][0]["x"] = True
    assert f"{path}.position[0].x" in _errors(bad)


def test_type_specific_fields(example):
    bad = copy.deepcopy(example)
    bad["scenes"][0]["render"]["layers"][2]["objects"][0]["gestures"] = [{"start": 0, "end": 10, "name": "salut"}]
    assert "scenes[0].render.layers[2].objects[0].gestures" in _errors(bad)
    bad = copy.deepcopy(example)
    del _mimo(bad)["asset"]
    assert "scenes[0].render.layers[1].objects[0].asset" in _errors(bad)


def test_overlapping_gestures_and_speech_are_refused(example):
    bad = copy.deepcopy(example)
    _mimo(bad)["gestures"][1]["start"] = 80
    assert "scenes[0].render.layers[1].objects[0].gestures[1].start" in _errors(bad)
    bad = copy.deepcopy(example)
    bad["scenes"][0]["render"]["speech"]["cues"][1]["start"] = 40
    assert "scenes[0].render.speech.cues[1].start" in _errors(bad)
    bad = copy.deepcopy(example)
    bad["scenes"][0]["render"]["speech"]["speaker"] = "titre"  # objet texte : ne parle pas
    assert "scenes[0].render.speech.speaker" in _errors(bad)


def test_duplicate_ids(example):
    bad = copy.deepcopy(example)
    bad["scenes"][1]["id"] = "accueil"
    assert "scenes[1].id" in _errors(bad)
    bad = copy.deepcopy(example)
    bad["scenes"][0]["render"]["layers"][2]["objects"][0]["id"] = "mimo"
    assert "scenes[0].render.layers[2].objects[0].id" in _errors(bad)


def test_transitions_and_fallback(example):
    bad = copy.deepcopy(example)
    bad["scenes"][0]["render"]["transition_out"] = {"type": "cut", "frames": 5}
    assert "scenes[0].render.transition_out.frames" in _errors(bad)
    bad = copy.deepcopy(example)
    bad["scenes"][0]["render"]["transition_in"] = {"type": "fade", "frames": 0}
    assert "scenes[0].render.transition_in.frames" in _errors(bad)
    bad = copy.deepcopy(example)
    bad["scenes"][0]["render"]["fallback"] = {"type": "still", "frame": 150}  # dernière image = 149
    assert "scenes[0].render.fallback.frame" in _errors(bad)
    bad = copy.deepcopy(example)
    bad["scenes"][1]["render"]["fallback"] = {"type": "asset"}
    assert "scenes[1].render.fallback.asset" in _errors(bad)
    bad = copy.deepcopy(example)
    del bad["scenes"][0]["render"]["fallback"]
    assert "scenes[0].render" in _errors(bad)


def test_text_limits_and_secrets(example):
    bad = copy.deepcopy(example)
    bad["scenes"][0]["intent"]["narration"] = "x" * 1201
    assert "scenes[0].intent.narration" in _errors(bad)
    bad = copy.deepcopy(example)
    bad["scenes"][0]["intent"]["notes"] = f"clé {FAKE_KEY}"
    assert any("clé secrète" in message for message in _errors(bad).values())
    bad = copy.deepcopy(example)
    bad["scenes"][0]["render"]["layers"][2]["objects"][0]["text"]["content"] = "a\x00b"
    assert "scenes[0].render.layers[2].objects[0].text.content" in _errors(bad)


def test_asset_ref_format_and_latest(example):
    assert len(list(iter_asset_refs(example))) == 6  # 5 objets « asset » + 1 repli « asset »
    bad = copy.deepcopy(example)
    bad["scenes"][0]["render"]["layers"][0]["objects"][0]["asset"]["id"] = "../../etc/passwd"
    assert "scenes[0].render.layers[0].objects[0].asset.id" in _errors(bad)
    # « latest » est permis en travail, refusé dans un document figé
    errors = _errors(example, require_pinned=True)
    assert "scenes[0].render.layers[1].objects[0].asset.version" in errors and "frozen" in errors


def test_sha256_is_stable_and_ignores_frozen_block(example):
    first = document_sha256(example)
    assert document_sha256(json.loads(json.dumps(example, sort_keys=True))) == first
    assert document_sha256({**example, "frozen": {"anything": 1}}) == first
    changed = copy.deepcopy(example)
    changed["scenes"][0]["duration_frames"] = 151
    assert document_sha256(changed) != first
