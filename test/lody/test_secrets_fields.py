"""Table des champs gérés par « Paramètres système » : capacités, fournisseurs, liste blanche de validation."""

from __future__ import annotations

import pytest

from lody.generation.secrets_fields import FIELD_ORDER, FIELDS, ShapeError, validate


def test_every_field_has_a_capability_except_the_provider_choice():
    for path, spec in FIELDS.items():
        if spec.kind == "choice":
            assert spec.capability == "" and spec.provider_id == ""
        else:
            assert spec.capability and spec.provider_id


def test_both_openai_fields_share_the_same_provider_id_distinct_capabilities():
    script, image = FIELDS["app.openai_api_key"], FIELDS["app.openai_image_api_keys"]
    assert script.provider_id == image.provider_id == "openai"
    assert script.capability != image.capability


def test_field_order_is_stable_and_matches_fields_keys():
    assert tuple(FIELD_ORDER) == tuple(FIELDS)


def test_validate_rejects_a_field_not_in_the_table():
    with pytest.raises(ShapeError):
        validate("app.not_a_real_field", "value")


def test_validate_strips_surrounding_whitespace():
    assert validate("app.openai_api_key", "  sk-abc123  ") == "sk-abc123"
