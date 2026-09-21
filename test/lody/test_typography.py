"""Typographie des sous-titres : élisions, apostrophes, SRT/ASS, mots horodatés, police (incident V7)."""

from __future__ import annotations

import re

import pytest

from lody.generation import typography as ty
from lody.generation.typography import (
    join_timed_words,
    normalize_ass,
    normalize_for_language,
    normalize_french_text,
    normalize_srt,
    normalize_subtitle_file,
    pick_subtitle_font,
)
from lody import settings

TYPO, STRAIGHT = "’", "'"


# -- exemples obligatoires ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("before,after", [
    ("l’  idée", "l’idée"),
    ("c’ est une idée", "c’est une idée"),
    ("qu’ il n’ y a", "qu’il n’y a"),
    ("aujourd’ hui", "aujourd’hui"),
    ("l' idée", "l'idée"),
    ("l’ idée", "l’idée"),
    ("l'  idée", "l'idée"),
    ("d’ accord", "d’accord"),
    ("j’ ai", "j’ai"),
    ("mais l’  idée de base est simple", "mais l’idée de base est simple"),
])
def test_required_examples(before, after):
    assert normalize_french_text(before) == after


def test_windows_path_is_left_intact():
    assert normalize_french_text("C:\\Users\\Test") == "C:\\Users\\Test"
    assert normalize_french_text("ouvre C:\\Users\\l' Test\\fichier et l’ idée") == "ouvre C:\\Users\\l' Test\\fichier et l’idée"


def test_url_with_an_encoded_apostrophe_is_not_damaged():
    text = "voir https://exemple.fr/l%27idee?q=qu%27il et l’ idée, puis www.exemple.fr/d' accord"
    assert normalize_french_text(text) == "voir https://exemple.fr/l%27idee?q=qu%27il et l’idée, puis www.exemple.fr/d' accord"


def test_email_and_code_spans_are_protected():
    assert normalize_french_text("écris à jean@ex.fr ou `l' idée`, l’ idée") == "écris à jean@ex.fr ou `l' idée`, l’idée"
    assert normalize_french_text("écris à jean.d' x@ex.fr") == "écris à jean.d' x@ex.fr"


# -- apostrophes, espaces, accents ---------------------------------------------------------------------------------
@pytest.mark.parametrize("apostrophe", [TYPO, STRAIGHT, "ʼ"])
@pytest.mark.parametrize("spaces", [" ", "  ", "     ", "\u00a0", " \u202f "])
def test_every_apostrophe_kind_and_any_amount_of_spaces(apostrophe, spaces):
    assert normalize_french_text(f"l{apostrophe}{spaces}idée") == f"l{apostrophe}idée"


def test_several_apostrophes_in_one_sentence_and_no_double_space_left():
    result = normalize_french_text("Aujourd’  hui, qu’ il n’ y a rien, j’ ai dit : c’ est l’  idée d’ un été")
    assert result == "Aujourd’hui, qu’il n’y a rien, j’ai dit : c’est l’idée d’un été"
    assert not re.search(r"[’']\s", result)


def test_accents_and_unicode_are_preserved():
    text = "l’  éléphant à l’ œuvre : « ça n’ était pas l’ été » — naïve café"
    assert normalize_french_text(text) == "l’éléphant à l’œuvre : « ça n’était pas l’été » — naïve café"


def test_typographic_apostrophes_are_never_replaced_by_straight_ones():
    text = "l’idée et l'idée"
    assert normalize_french_text(text) == text  # rien à corriger : rien ne change, aucun remplacement aveugle
    assert normalize_french_text("l’ idée et l' idée") == "l’idée et l'idée"


# -- non-régression : ponctuation et espaces ordinaires ---------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "Bonjour, ça va ? Oui : très bien ! Voilà… fin.",
    "Il a dit : « Pas de panique ».   Deux  espaces  ordinaires  restent.",
    "the 'quick' fox jumps over the dogs' toys",
    "les chiens' jouets et l’idée",
    "Web3 gaming — 45 à 60 s ; 9:16 (vertical)",
    "",
])
def test_normal_punctuation_and_ordinary_spaces_are_untouched(text):
    assert normalize_french_text(text) == text


def test_only_french_languages_are_normalised():
    assert normalize_for_language("l’ idée", "fr-FR") == "l’idée"
    assert normalize_for_language("l’ idée", "fr") == "l’idée"
    assert normalize_for_language("l’ idée", "fr_CA") == "l’idée"
    assert normalize_for_language("l' idea", "en-US") == "l' idea"


# -- « phrase entière », SRT, ASS, mots horodatés -------------------------------------------------------------------------
SRT = """1
00:00:00,000 --> 00:00:01,869
Si tu découvres le Web3 gaming

2
00:00:04,984 --> 00:00:06,853
mais l’  idée de base est simple

3
00:00:06,853 --> 00:00:09,407
c’ est un grand registre
"""


def test_srt_sentence_mode_only_cue_text_is_touched():
    fixed, changed = normalize_srt(SRT)
    assert changed == 2
    assert "mais l’idée de base est simple" in fixed and "c’est un grand registre" in fixed
    assert re.findall(r"^\d+$", fixed, re.M) == ["1", "2", "3"]                      # numéros intacts
    assert re.findall(r"\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}", fixed) == \
           re.findall(r"\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}", SRT)          # minutages intacts
    assert "  " not in fixed.replace("\n\n", "\n")
    assert normalize_srt(fixed) == (fixed, 0)                                          # idempotent


def test_ass_only_dialogue_text_is_touched_and_override_tags_survive():
    ass = ("[Script Info]\nTitle: l' idée\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
           "Dialogue: 0,0:00:04.98,0:00:06.85,Default,,0,0,0,,{\\an2\\fs58}mais l’  idée de base, c’ est simple\n"
           "Dialogue: 0,0:00:06.85,0:00:09.40,Default,,0,0,0,,{\\b1}qu’ il{\\b0} n’ y a rien\n")
    fixed, changed = normalize_ass(ass)
    assert changed == 2
    assert "{\\an2\\fs58}mais l’idée de base, c’est simple" in fixed
    assert "{\\b1}qu’il{\\b0} n’y a rien" in fixed
    assert "Title: l' idée" in fixed                       # en-tête : jamais modifié
    assert "0:00:04.98,0:00:06.85,Default" in fixed        # champs : jamais modifiés


def test_subtitle_files_are_normalised_by_extension(tmp_path):
    srt, ass = tmp_path / "a.srt", tmp_path / "a.ass"
    srt.write_text(SRT, encoding="utf-8")
    ass.write_text("Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,l’  idée\n", encoding="utf-8")
    target = tmp_path / "out.srt"
    assert normalize_subtitle_file(srt, target) == 2 and "l’idée" in target.read_text(encoding="utf-8")
    assert "l’  idée" in srt.read_text(encoding="utf-8")   # la source n'est pas modifiée quand une cible est donnée
    assert normalize_subtitle_file(ass) == 1 and ass.read_text(encoding="utf-8").endswith("l’idée\n")


@pytest.mark.parametrize("tokens,expected", [
    (["mais", "l’", "idée", "de", "base", "est", "simple"], "mais l’idée de base est simple"),
    (["mais", "l", "’idée", "de"], "mais l’idée de"),
    (["c'", "est", "qu’", "il", "n’", "y", "a"], "c'est qu’il n’y a"),
    ([("aujourd’", 0.0, 0.4), ("hui", 0.4, 0.7), ("on", 0.7, 0.8)], "aujourd’hui on"),
    (["  Bonjour ", "", "le", "monde  "], "Bonjour le monde"),
])
def test_text_rebuilt_from_timed_words_has_no_gap_after_an_elision(tokens, expected):
    assert join_timed_words(tokens) == expected
    assert "’ " not in join_timed_words(tokens) and "' " not in join_timed_words(tokens)


# -- police : la vraie cause racine ------------------------------------------------------------------------------------------
FONTS = settings.fonts_dir()
needs_fonts = pytest.mark.skipif(not (FONTS / "BeVietnamPro-Bold.ttf").is_file(), reason="polices absentes")


@needs_fonts
def test_cjk_fonts_draw_the_typographic_apostrophe_full_width_and_latin_fonts_do_not():
    for name in sorted(ty.KNOWN_WIDE_APOSTROPHE):
        assert ty.apostrophe_is_wide(FONTS / name) is True, name
    assert ty.apostrophe_is_wide(FONTS / "BeVietnamPro-Bold.ttf") is False
    assert ty.apostrophe_is_wide(FONTS / "absente.ttf") is None


@needs_fonts
def test_the_v7_defect_is_a_glyph_width_not_a_character():
    from PIL import ImageFont

    wide, latin = (ImageFont.truetype(str(FONTS / n), 58) for n in ("MicrosoftYaHeiBold.ttc", "BeVietnamPro-Bold.ttf"))
    # « l’ » occupe 2,5 fois plus de place avec la police CJK, alors que le texte est identique et sans espace
    assert wide.getlength("l’") > 2 * latin.getlength("l’")
    assert wide.getlength("’") > 3 * wide.getlength(" ")


@needs_fonts
@pytest.mark.parametrize("language", ["fr-FR", "fr", "en-US", "es-ES", "de-DE"])
def test_latin_languages_never_keep_a_full_width_apostrophe_font(language):
    font, reason = pick_subtitle_font("MicrosoftYaHeiBold.ttc", language, FONTS)
    assert font == "BeVietnamPro-Bold.ttf" and "pleine largeur" in reason


@needs_fonts
def test_font_choice_keeps_good_fonts_cjk_languages_and_reports_missing_ones():
    assert pick_subtitle_font("BeVietnamPro-Medium.ttf", "fr-FR", FONTS) == ("BeVietnamPro-Medium.ttf", "")
    assert pick_subtitle_font("MicrosoftYaHeiBold.ttc", "zh-CN", FONTS) == ("MicrosoftYaHeiBold.ttc", "")   # CJK : inchangé
    assert pick_subtitle_font("MicrosoftYaHeiBold.ttc", "ja-JP", FONTS)[0] == "MicrosoftYaHeiBold.ttc"
    assert pick_subtitle_font("", "fr-FR", FONTS)[0] == "BeVietnamPro-Bold.ttf"
    assert pick_subtitle_font("STHeitiMedium.ttc", "fr-FR", FONTS.parent)[0] == "STHeitiMedium.ttc"     # police sûre indisponible : on ne casse rien


def test_font_choice_without_pillow_or_font_file_falls_back_to_the_known_list(tmp_path, monkeypatch):
    (tmp_path / "BeVietnamPro-Bold.ttf").write_bytes(b"x")
    (tmp_path / "MicrosoftYaHeiBold.ttc").write_bytes(b"not a font")   # illisible : non mesurable
    assert ty.apostrophe_is_wide(tmp_path / "MicrosoftYaHeiBold.ttc") is None
    assert pick_subtitle_font("MicrosoftYaHeiBold.ttc", "fr-FR", tmp_path)[0] == "BeVietnamPro-Bold.ttf"
    (tmp_path / "Autre.ttf").write_bytes(b"not a font")
    assert pick_subtitle_font("Autre.ttf", "fr-FR", tmp_path) == ("Autre.ttf", "")
