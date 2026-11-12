"""Descriptions de lieux détaillées (#202) : bornes 8 000 / 4 000, structure multi-lignes conservée, parcours
formulaire → enregistrement → rechargement → export → réimport sans perte, et condensation explicite au moment
de composer les prompts (texte canonique jamais modifié). Aucun fournisseur, aucun coût."""

from __future__ import annotations

import json

import pytest

from lody import project_transfer, resource_transfer
from lody.generation.storyboard import Scene
from lody.generation.narrative_context import (
    CONDENSED_MARK,
    LOCATION_SCRIPT_LINE_MAX,
    LOCATION_VISUAL_LINE_MAX,
    NARRATIVE_BLOCK_MAX,
    condense_fields,
    enrich_visual_prompts,
    render_prompt_block,
)
from lody.locations import (
    CONTINUITY_NOTES_MAX,
    DESCRIPTION_MAX,
    REFERENCE_PROMPT_MAX,
    LocationRepository,
    LocationValidationError,
    normalize_multiline,
    validate_fields,
)
from lody.projects import ProjectRepository

STUDIO_DESCRIPTION = "\n".join([
    "Identité : plateau de télévision « Studio Lody », fond bleu nuit, logo Lody lumineux au centre du mur.",
    "Agencement : bureau en arc de cercle au centre ; caméra 1 face au bureau, caméra 2 à gauche à 45°, "
    "caméra 3 sur grue derrière le public. Les positions ne bougent jamais d'un épisode à l'autre.",
    "",
    "Lumière et couleurs : contre-jour cyan, face douce 5 600 K, sol noir brillant, touches jaune Voltix.",
    "Équipements : trois moniteurs de retour sous le bureau, un écran géant à droite, micros cravate.",
] * 6)  # ~3 000 caractères, bien au-delà de l'ancienne borne de 500
STUDIO_CONTINUITY = "\n".join([
    "Constance : le bureau, le logo et l'écran géant sont visibles sur tous les plans larges.",
    "Ce qui peut changer : contenu de l'écran géant, carton de l'invité.",
] * 12)
STUDIO_PROMPT = ("plateau de télévision moderne, bureau en arc, fond bleu nuit, contre-jour cyan, sol noir "
                 "brillant, trois caméras visibles, ") * 8


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "lody.sqlite3"


@pytest.fixture
def project(db_path):
    return ProjectRepository(db_path).create(name="Projet de test")


@pytest.fixture
def repo(db_path, project):
    return LocationRepository(db_path)


def _studio(**override):
    return {"name": "Studio Lody", "location_type": "Plateau TV", "description": STUDIO_DESCRIPTION,
            "reference_prompt": STUDIO_PROMPT.strip(), "continuity_notes": STUDIO_CONTINUITY, **override}


# -- bornes et messages ---------------------------------------------------------------------------------------------
def test_new_limits_are_per_field_and_far_above_500():
    assert DESCRIPTION_MAX == 8000
    assert CONTINUITY_NOTES_MAX == 8000
    assert REFERENCE_PROMPT_MAX == 4000


@pytest.mark.parametrize("key, maximum", [("description", DESCRIPTION_MAX), ("reference_prompt", REFERENCE_PROMPT_MAX),
                                          ("continuity_notes", CONTINUITY_NOTES_MAX)])
def test_exact_limit_is_accepted_and_one_more_is_refused_with_a_clear_message(key, maximum):
    accepted = validate_fields({"name": "Studio", key: "a" * maximum})
    assert len(accepted[key]) == maximum
    with pytest.raises(LocationValidationError) as raised:
        validate_fields({"name": "Studio", key: "a" * (maximum + 1)})
    message = raised.value.errors[key]
    assert f"{maximum + 1:,}".replace(",", " ") in message  # longueur réelle
    assert f"{maximum:,}".replace(",", " ") in message  # capacité du produit
    assert "tronqué" in message  # dit explicitement que rien n'a été coupé en silence


def test_4000_and_8000_character_fields_are_stored_without_loss(repo, project):
    created = repo.create(project.id, name="Grand studio", description="d" * 8000,
                          reference_prompt="r" * 4000, continuity_notes="c" * 8000)
    reloaded = repo.get(project.id, created.id)
    assert (len(reloaded.description), len(reloaded.reference_prompt), len(reloaded.continuity_notes)) == (8000, 4000, 8000)


def test_short_fields_keep_their_previous_limits():
    with pytest.raises(LocationValidationError) as raised:
        validate_fields({"name": "x" * 81, "location_type": "t" * 61})
    assert set(raised.value.errors) == {"name", "location_type"}


# -- structure multi-lignes -------------------------------------------------------------------------------------------
def test_line_breaks_are_preserved_and_normalisation_is_idempotent():
    raw = "  Identité :   plateau  \r\nAgencement : bureau\r\n\r\n\r\n\nLumière :\tcyan  \n\n"
    clean = normalize_multiline(raw)
    assert clean == "Identité : plateau\nAgencement : bureau\n\nLumière : cyan"
    assert normalize_multiline(clean) == clean


def test_single_line_fields_are_still_flattened():
    clean = validate_fields({"name": "Studio\nLody", "location_type": "Plateau\nTV"})
    assert clean["name"] == "Studio Lody"
    assert clean["location_type"] == "Plateau TV"


# -- parcours complet : enregistrer, recharger, éditer, exporter, réimporter -----------------------------------------
def test_studio_lody_import_edit_reload_export_reimport_without_loss(db_path, repo, project):
    assert len(STUDIO_DESCRIPTION) > 500 and len(STUDIO_CONTINUITY) > 500 and len(STUDIO_PROMPT.strip()) > 500
    payload = {"schema_version": 1, "resource_type": "locations", "locations": [_studio()]}
    preview = resource_transfer.preview_location_import(json.dumps(payload, ensure_ascii=False))
    assert preview.is_valid, preview.errors
    [imported] = resource_transfer.commit_location_import(db_path, project.id, preview)
    assert imported.description == STUDIO_DESCRIPTION
    assert imported.continuity_notes == STUDIO_CONTINUITY
    assert imported.reference_prompt == STUDIO_PROMPT.strip()

    edited_description = STUDIO_DESCRIPTION + "\nAjout : une plante verte à droite du bureau."
    repo.update(project.id, imported.id, description=edited_description)
    reloaded = repo.get(project.id, imported.id)
    assert reloaded.description == edited_description

    exported = json.dumps(resource_transfer.export_locations([reloaded]), ensure_ascii=False)
    other = ProjectRepository(db_path).create(name="Autre projet")
    preview_again = resource_transfer.preview_location_import(exported)
    assert preview_again.is_valid, preview_again.errors
    [reimported] = resource_transfer.commit_location_import(db_path, other.id, preview_again)
    for field in ("name", "location_type", "description", "reference_prompt", "continuity_notes"):
        assert getattr(reimported, field) == getattr(reloaded, field)


def test_project_import_accepts_long_location_and_reports_the_precise_path_beyond_the_limit(db_path):
    template = project_transfer.blank_template()
    template["locations"] = [_studio()]
    assert project_transfer.preview_import(json.dumps(template, ensure_ascii=False)).is_valid

    template["locations"] = [_studio(description="d" * (DESCRIPTION_MAX + 1))]
    preview = project_transfer.preview_import(json.dumps(template, ensure_ascii=False))
    assert not preview.is_valid
    assert any(issue.path == "locations[0].description" and "8 000" in issue.message for issue in preview.errors)


def test_fifty_fully_detailed_locations_fit_in_one_import_file():
    locations = [{"name": f"Lieu {i:02d}", "description": "é" * DESCRIPTION_MAX, "reference_prompt": "à" * REFERENCE_PROMPT_MAX,
                  "continuity_notes": "è" * CONTINUITY_NOTES_MAX} for i in range(project_transfer.MAX_ITEMS)]
    raw = json.dumps({"schema_version": 1, "resource_type": "locations", "locations": locations}, ensure_ascii=False)
    assert len(raw.encode("utf-8")) <= project_transfer.MAX_FILE_BYTES
    assert resource_transfer.preview_location_import(raw).is_valid


def test_downloadable_template_shows_a_multiline_location_and_documents_the_capacity():
    template = resource_transfer.location_template()
    description = template["locations"][0]["description"]
    assert "\n" in description and "8 000" in description
    assert resource_transfer.preview_location_import(json.dumps(template, ensure_ascii=False)).is_valid


# -- composition des prompts : condensation explicite, texte canonique intact -----------------------------------------
def test_condense_fields_shares_the_budget_and_marks_every_cut():
    item = {"a": "court", "b": "mot " * 500, "c": "terme " * 500}
    condensed = condense_fields(item, ("a", "b", "c"), 300)
    assert condensed["a"] == "court"  # un champ court n'est jamais coupé, sa part restante va aux autres
    assert sum(len(value) for value in condensed.values()) <= 300
    assert condensed["b"].endswith(CONDENSED_MARK) and condensed["c"].endswith(CONDENSED_MARK)
    assert abs(len(condensed["b"]) - len(condensed["c"])) < 20  # partage équitable


def test_detailed_location_is_condensed_in_every_scene_never_dropped_and_canonical_text_untouched():
    location = {"id": "loc_1", **_studio()}
    snapshot = json.loads(json.dumps(location))
    narrative = {"characters": [], "location": location}
    scenes = [Scene(1, "Ouverture.", "p1", 4.0), Scene(2, "Suite.", "p2", 4.0)]
    for scene in enrich_visual_prompts(scenes, narrative):
        assert "Lieu : Studio Lody" in scene.prompt  # jamais abandonné entièrement comme avant #202
        assert CONDENSED_MARK in scene.prompt
        assert "(continuité tronquée" not in scene.prompt
        [line] = [line for line in scene.prompt.splitlines() if line.startswith("Lieu : ")]
        assert len(line) <= LOCATION_VISUAL_LINE_MAX
    block = render_prompt_block(narrative)
    assert "Lieu : Studio Lody" in block and CONDENSED_MARK in block
    assert len(block) <= NARRATIVE_BLOCK_MAX
    [line] = [line for line in block.splitlines() if line.startswith("Lieu : ")]
    assert len(line) <= LOCATION_SCRIPT_LINE_MAX
    assert location == snapshot  # l'instantané figé n'est jamais modifié par la composition


def test_location_filled_within_former_limits_is_rendered_exactly_as_before():
    location = {"id": "l1", "name": "N" * 80, "location_type": "T" * 60,
                "description": "E" * 500, "reference_prompt": "F" * 500, "continuity_notes": "G" * 500}
    [scene] = enrich_visual_prompts([Scene(1, "Rien.", "p", 3.0)], {"characters": [], "location": location})
    assert "E" * 500 in scene.prompt and "F" * 500 in scene.prompt and "G" * 500 in scene.prompt
    assert CONDENSED_MARK not in scene.prompt
