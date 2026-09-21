"""Miniature : trois compositions depuis un seul fond, texte exact, lisibilité, déterminisme. Aucun fournisseur."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from lody import settings
from lody.generation import thumbnail as th

FONTS = settings.fonts_dir()
pytestmark = pytest.mark.skipif(not (FONTS / "BeVietnamPro-Bold.ttf").is_file(), reason="polices absentes")
TEXT = "Crypto & NFT, simplement"


@pytest.fixture(scope="module")
def background():
    return th.placeholder_background("fond-de-test")


def test_three_compositions_come_from_one_background_with_no_network(background, monkeypatch):
    import socket

    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("réseau interdit")))
    results = th.compose_all(background, TEXT)
    assert list(results) == ["centre", "haut", "lateral"]
    sizes = {image.size for image, _ in results.values()}
    assert sizes == {(1080, 1920)}
    pngs = {th.to_png(image) for image, _ in results.values()}
    assert len(pngs) == 3                                                    # trois rendus réellement différents


def test_text_is_exactly_the_requested_text_in_every_variant(background):
    for variant, (_, layout) in th.compose_all(background, TEXT).items():
        assert layout.text == TEXT and layout.variant == variant
        assert "".join(layout.lines).replace(" ", "") == TEXT.replace(" ", "")     # aucun mot perdu ni ajouté
    accented = "Où est l’idée ? Été"
    _, layout = th.compose(background, accented, "centre")
    assert layout.text == accented                                           # accents et apostrophe typographique conservés


def test_text_is_placed_where_each_variant_promises(background):
    width, height = 1080, 1920
    boxes = {v: layout.text_box for v, (_, layout) in th.compose_all(background, TEXT).items()}
    assert abs((boxes["centre"][0] + boxes["centre"][2]) / 2 - width / 2) < 20
    assert 0.3 * height < (boxes["centre"][1] + boxes["centre"][3]) / 2 < 0.7 * height
    assert boxes["haut"][3] < 0.36 * height                                   # tout en haut
    assert boxes["lateral"][2] < 0.66 * width                                 # colonne de gauche : le sujet reste dégagé à droite


def test_lateral_variant_leaves_the_right_side_of_the_background_untouched(background):
    image, layout = th.compose(background, TEXT, "lateral")
    reference = th.compose(background, TEXT, "centre")[0]
    plain = Image.new("RGB", image.size)
    from PIL import ImageOps

    fitted = ImageOps.fit(background.convert("RGB"), image.size, Image.LANCZOS)
    right = (int(0.80 * image.width), 0, image.width, image.height)
    assert list(image.crop(right).getdata())[:200] == list(fitted.crop(right).getdata())[:200]   # aucun voile ni texte à droite
    del plain, reference, layout


def test_contrast_is_strong_even_on_a_bright_background():
    bright = Image.new("RGB", (1024, 1536), (245, 245, 245))
    for variant in th.VARIANTS:
        _, layout = th.compose(bright, TEXT, variant)
        assert layout.contrast_ratio >= th.MIN_CONTRAST and layout.scrim_alpha >= 0.55 and th.readability(layout).ok


@pytest.mark.parametrize("variant", th.VARIANTS)
def test_phone_preview_keeps_the_text_readable(background, variant):
    image, layout = th.compose(background, TEXT, variant)
    report = th.readability(layout, 360)
    assert report.ok and report.phone_glyph_px >= th.MIN_PHONE_GLYPH_PX and report.contrast_ratio >= th.MIN_CONTRAST
    preview = th.phone_preview(image)
    assert preview.size == (360, 640)
    assert th.readability(layout, 60).ok is False and th.readability(layout, 60).messages   # un aperçu minuscule est signalé


def test_long_text_is_flagged_and_fits_in_the_zone(background):
    text = "Un texte beaucoup trop long pour une miniature lisible sur téléphone"
    _, layout = th.compose(background, text, "centre")
    report = th.readability(layout)
    assert any("2 à 5 mots" in message for message in report.messages)
    x0, y0, x1, y1 = layout.text_box
    assert 0 <= x0 < x1 <= 1080 and 0 <= y0 < y1 <= 1920


def test_rendering_is_deterministic_and_encodes_png_and_jpeg(background):
    first, _ = th.compose(background, TEXT, "centre")
    second, _ = th.compose(background, TEXT, "centre")
    assert th.to_png(first) == th.to_png(second)
    png, jpg = th.to_png(first), th.to_jpeg(first)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and jpg[:3] == b"\xff\xd8\xff"
    assert Image.open(io.BytesIO(png)).size == (1080, 1920) and Image.open(io.BytesIO(jpg)).size == (1080, 1920)
    assert len(jpg) < len(png)


@pytest.mark.parametrize("aspect,size", [("9:16", (1080, 1920)), ("16:9", (1280, 720)), ("1:1", (1080, 1080))])
def test_every_project_format_is_supported(background, aspect, size):
    image, layout = th.compose(background, "Deux mots", "haut", th.SIZES[aspect])
    assert image.size == size and th.readability(layout).ok


def test_empty_or_unknown_input_is_refused(background):
    with pytest.raises(ValueError):
        th.compose(background, "   ", "centre")
    with pytest.raises(ValueError):
        th.compose(background, "Texte", "diagonale")
    assert th.clean_text("  a   b  ") == "a b" and len(th.clean_text("x" * 200)) == th.TEXT_MAX_CHARS
    assert th.word_count("un deux  trois") == 3


def test_placeholder_background_is_deterministic_and_seed_dependent():
    assert th.to_png(th.placeholder_background("a", (64, 96))) == th.to_png(th.placeholder_background("a", (64, 96)))
    assert th.to_png(th.placeholder_background("a", (64, 96))) != th.to_png(th.placeholder_background("b", (64, 96)))
