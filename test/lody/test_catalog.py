"""Normalisation des identifiants techniques de catalogue (ticket #63) : accepte le libellé affiché dans
l'interface (ex. « ElevenLabs ») en plus de l'identifiant technique (« elevenlabs »), sans ambiguïté."""

from __future__ import annotations

from lody import catalog


def test_normalize_accepts_the_exact_canonical_value():
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "elevenlabs") == "elevenlabs"
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "edge") == "edge"


def test_normalize_accepts_the_label_case_insensitively():
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "ElevenLabs") == "elevenlabs"
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "ELEVENLABS") == "elevenlabs"
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "voix gratuite (edge)") == "edge"


def test_normalize_accepts_the_value_with_different_casing():
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "ElEvEnLaBs") == "elevenlabs"


def test_normalize_strips_surrounding_whitespace():
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "  elevenlabs  ") == "elevenlabs"


def test_normalize_rejects_an_unknown_value():
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "myspace-voice") is None


def test_normalize_rejects_a_substring_match_never_ambiguous():
    """Un fragment du libellé « Voix gratuite (Edge) » ne doit PAS suffire à identifier l'option : seule une
    correspondance EXACTE (insensible à la casse) sur la valeur ou le libellé entier est acceptée, jamais une
    sous-chaîne — condition explicite du ticket."""
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "Voix gratuite") is None
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "gratuite") is None
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "(Edge)") is None


def test_normalize_rejects_empty_or_non_string_input():
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "") is None
    assert catalog.normalize(catalog.VOICE_PROVIDERS, "   ") is None
    assert catalog.normalize(catalog.VOICE_PROVIDERS, None) is None
    assert catalog.normalize(catalog.VOICE_PROVIDERS, ["elevenlabs"]) is None
    assert catalog.normalize(catalog.VOICE_PROVIDERS, 42) is None


def test_normalize_works_on_every_registered_catalog():
    """Le mécanisme est générique : vérifié sur chaque catalogue de CATALOGS, pas seulement les voix."""
    for options in catalog.CATALOGS.values():
        for option in options:
            assert catalog.normalize(options, option.label.upper()) == option.value
            assert catalog.normalize(options, option.value.upper()) == option.value


def test_allowed_values_text_lists_technical_identifiers_not_labels():
    text = catalog.allowed_values_text(catalog.VOICE_PROVIDERS)
    assert '"elevenlabs"' in text and '"edge"' in text
    assert "ElevenLabs" not in text  # les IDENTIFIANTS techniques, jamais les libellés d'affichage


def test_allowed_values_text_matches_values_exactly():
    for options in catalog.CATALOGS.values():
        text = catalog.allowed_values_text(options)
        for value in catalog.values(options):
            assert f'"{value}"' in text
