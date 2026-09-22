"""Patch chirurgical d'une seule clé de config.toml : tout le reste doit rester identique octet pour octet."""

from __future__ import annotations

import pytest

from lody.generation.toml_patch import PatchError, patch

SAMPLE = (
    "log_level = \"DEBUG\"\n"
    "\n"
    "[app]\n"
    "# API key: https://platform.openai.com/api-keys\n"
    "openai_api_key = \"sk-old-value-1234\"\n"
    "openai_base_url = \"\"\n"
    "openai_image_api_keys = []\n"
    "llm_provider = \"moonshot\"\n"
    "\n"
    "[elevenlabs]\n"
    "api_key = \"\"\n"
    "model_id = \"eleven_multilingual_v2\"\n"
)


def test_patches_only_the_target_line_everything_else_is_byte_identical():
    out = patch(SAMPLE, section="app", key="openai_api_key", value="sk-new-value-9999")
    assert 'openai_api_key = "sk-new-value-9999"\n' in out
    before, after = SAMPLE.splitlines(), out.splitlines()
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert changed == [before.index('openai_api_key = "sk-old-value-1234"')]
    assert "# API key: https://platform.openai.com/api-keys" in out  # commentaire conservé
    assert 'model_id = "eleven_multilingual_v2"' in out               # autre section intacte


def test_patches_a_different_section_and_key():
    out = patch(SAMPLE, section="elevenlabs", key="api_key", value="voice-secret")
    assert 'api_key = "voice-secret"\n' in out
    assert 'openai_api_key = "sk-old-value-1234"' in out  # section [app] inchangée


def test_patches_list1_field():
    out = patch(SAMPLE, section="app", key="openai_image_api_keys", value="img-secret", kind="list1")
    assert 'openai_image_api_keys = ["img-secret"]\n' in out


def test_same_key_name_in_a_different_section_is_not_touched():
    out = patch(SAMPLE, section="elevenlabs", key="api_key", value="voice-secret")
    assert out.count('api_key = "voice-secret"') == 1


def test_commented_out_line_is_ignored_active_assignment_is_used():
    text = "[app]\n# openai_api_key = \"decoy\"\nopenai_api_key = \"real\"\n"
    out = patch(text, section="app", key="openai_api_key", value="new")
    assert '# openai_api_key = "decoy"' in out
    assert 'openai_api_key = "new"' in out


def test_value_is_toml_escaped():
    out = patch(SAMPLE, section="app", key="openai_api_key", value='weird"value\\with\nnewline')
    assert '\\"value\\\\with\\nnewline' in out
    # Round-trips through a real TOML parser.
    import tomllib

    assert tomllib.loads(out)["app"]["openai_api_key"] == 'weird"value\\with\nnewline'


def test_unicode_value_round_trips():
    import tomllib

    out = patch(SAMPLE, section="app", key="openai_api_key", value="clé-éàü-🔑")
    assert tomllib.loads(out)["app"]["openai_api_key"] == "clé-éàü-🔑"


@pytest.mark.parametrize("section", ["missing"])
def test_missing_section_is_refused(section):
    with pytest.raises(PatchError):
        patch(SAMPLE, section=section, key="openai_api_key", value="x")


def test_missing_key_is_refused():
    with pytest.raises(PatchError):
        patch(SAMPLE, section="app", key="does_not_exist", value="x")


def test_multiline_value_is_refused_rather_than_guessed():
    text = '[app]\nsome_array = [\n  "a",\n  "b",\n]\n'
    with pytest.raises(PatchError):
        patch(text, section="app", key="some_array", value="x")


def test_only_the_key_in_the_right_section_matches_not_a_later_section_reusing_the_name():
    text = "[app]\napi_key = \"app-one\"\n[elevenlabs]\napi_key = \"eleven-one\"\n"
    out = patch(text, section="elevenlabs", key="api_key", value="new")
    assert 'app]\napi_key = "app-one"' in out
    assert 'api_key = "new"' in out


def test_trailing_newline_and_line_endings_preserved():
    out = patch(SAMPLE, section="app", key="llm_provider", value="openai")
    assert out.endswith("\n")
    assert out.count("\n") == SAMPLE.count("\n")
