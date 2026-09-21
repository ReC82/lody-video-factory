"""Miniature : texte rendu LOCALEMENT sur un fond sans texte, en trois compositions depuis un seul fond.

Le modèle d'image ne reçoit jamais le titre à écrire : le fond est une image sans texte (image de scène existante, ou fond
généré), et le texte est dessiné ici, de façon déterministe (mêmes entrées → mêmes octets). Aucun appel réseau, aucun coût.

Trois variantes : ``centre`` (bandeau central), ``haut`` (texte en haut, dégradé) et ``lateral`` (colonne à gauche, sujet
visuel dégagé à droite). Un voile sombre dont l'opacité monte jusqu'à obtenir un contraste ≥ 4,5:1 garantit la lisibilité.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from lody import settings
from lody.generation.typography import LATIN_SAFE_FONT

VARIANTS = ("centre", "haut", "lateral")
VARIANT_LABELS = {"centre": "Texte centré", "haut": "Texte en haut", "lateral": "Texte latéral"}
SIZES = {"9:16": (1080, 1920), "16:9": (1280, 720), "1:1": (1080, 1080)}
TEXT_MAX_CHARS = 60
MIN_CONTRAST = 4.5          # WCAG AA : texte blanc sur fond assombri
MIN_PHONE_GLYPH_PX = 20     # corps du texte dans l'aperçu téléphone (largeur 360 px)
PHONE_PREVIEW_WIDTH = 360
ACCENT = (34, 211, 238)     # cyan par défaut ; remplaçable par l'accent du projet
_MAX_LINES = {"centre": 3, "haut": 3, "lateral": 4}  # la colonne latérale est étroite : une ligne de plus

# Zones de texte (fractions du canevas : gauche, haut, droite, bas) par variante et par orientation.
_AREAS = {
    "portrait": {"centre": (0.08, 0.36, 0.92, 0.64), "haut": (0.08, 0.07, 0.92, 0.30), "lateral": (0.06, 0.24, 0.62, 0.76)},
    "paysage": {"centre": (0.08, 0.30, 0.92, 0.70), "haut": (0.06, 0.06, 0.94, 0.42), "lateral": (0.05, 0.14, 0.55, 0.86)},
    "carre": {"centre": (0.08, 0.32, 0.92, 0.68), "haut": (0.07, 0.06, 0.93, 0.34), "lateral": (0.06, 0.20, 0.60, 0.80)},
}


@dataclass(frozen=True)
class Layout:
    """Ce qui a réellement été dessiné : sert aux tests, à l'aperçu et au contrôle de lisibilité."""

    variant: str
    size: tuple[int, int]
    lines: tuple[str, ...]
    font_px: int
    text_box: tuple[int, int, int, int]
    scrim_alpha: float
    contrast_ratio: float

    @property
    def text(self) -> str:
        return " ".join(self.lines)


@dataclass(frozen=True)
class Readability:
    ok: bool
    phone_glyph_px: float
    contrast_ratio: float
    messages: tuple[str, ...]


def clean_text(text: str) -> str:
    return " ".join(str(text or "").split())[:TEXT_MAX_CHARS]


def _orientation(size: tuple[int, int]) -> str:
    return "portrait" if size[1] > size[0] * 1.15 else ("paysage" if size[0] > size[1] * 1.15 else "carre")


def _font(px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = Path(settings.fonts_dir()) / LATIN_SAFE_FONT
    try:
        return ImageFont.truetype(str(path), px)
    except OSError:
        return ImageFont.load_default()


def _wrap(text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit(text: str, area: tuple[int, int, int, int], max_lines: int) -> tuple[list[str], int]:
    """Plus grand corps qui tient dans la zone, en ``max_lines`` lignes au plus."""
    width, height = area[2] - area[0], area[3] - area[1]
    top = int(min(width * 0.30, height * 0.55))
    low = max(28, int(width * 0.075))
    for px in range(top, low - 1, -4):
        font = _font(px)
        lines = _wrap(text, font, width)
        ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") else (px, 0)
        block = len(lines) * int((ascent + descent) * 1.02)
        if len(lines) <= max_lines and block <= height and all(font.getlength(line) <= width for line in lines):
            return lines, px
    return _wrap(text, _font(low), width)[:max_lines], low


def _luminance(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Luminance relative moyenne (WCAG) d'une zone, sur une version réduite : rapide et déterministe."""
    region = image.crop(box).convert("RGB").resize((24, 24), Image.BILINEAR)
    total = 0.0
    for r, g, b in region.getdata():
        channel = [((c / 255) / 12.92) if c / 255 <= 0.03928 else (((c / 255) + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
        total += 0.2126 * channel[0] + 0.7152 * channel[1] + 0.0722 * channel[2]
    return total / (24 * 24)


def _scrim_mask(variant: str, size: tuple[int, int], box: tuple[int, int, int, int]) -> Image.Image:
    """Voile opaque sur toute la zone de texte puis dégradé jusqu'à zéro : le contraste est garanti là où le texte se trouve."""
    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    if variant == "haut":
        solid, fade_end = int(height * 0.32), int(height * 0.52)
        for y in range(fade_end):
            draw.line((0, y, width, y), fill=255 if y <= solid else int(255 * (fade_end - y) / (fade_end - solid)))
    elif variant == "lateral":
        solid, fade_end = int(width * 0.62), int(width * 0.80)  # au-delà de 80 % : le fond reste intact (sujet dégagé)
        for x in range(fade_end):
            draw.line((x, 0, x, height), fill=255 if x <= solid else int(255 * (fade_end - x) / (fade_end - solid)))
    else:
        pad = int(height * 0.05)
        draw.rectangle((0, max(0, box[1] - pad), width, min(height, box[3] + pad)), fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(int(height * 0.022)))
    return mask


def compose(background: Image.Image, text: str, variant: str, size: tuple[int, int] = SIZES["9:16"],
            accent: tuple[int, int, int] = ACCENT) -> tuple[Image.Image, Layout]:
    """Une composition. ``text`` est dessiné tel quel (aucune transformation de casse), jamais confié au modèle d'image."""
    if variant not in VARIANTS:
        raise ValueError("variante inconnue")
    text = clean_text(text)
    if not text:
        raise ValueError("Le texte de la miniature est vide.")
    width, height = size
    canvas = ImageOps.fit(background.convert("RGB"), size, Image.LANCZOS, centering=(0.5, 0.5))
    fractions = _AREAS[_orientation(size)][variant]
    area = (int(fractions[0] * width), int(fractions[1] * height), int(fractions[2] * width), int(fractions[3] * height))
    lines, px = _fit(text, area, _MAX_LINES[variant])
    font = _font(px)
    ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") else (px, 0)
    line_height = int((ascent + descent) * 1.02)
    block_h = line_height * len(lines)
    block_w = max(int(font.getlength(line)) for line in lines)
    left_aligned = variant == "lateral"
    x0 = area[0] if left_aligned else (width - block_w) // 2
    y0 = area[1] + (area[3] - area[1] - block_h) // 2
    text_box = (max(0, x0 - px // 4), max(0, y0), min(width, x0 + block_w + px // 4), min(height, y0 + block_h))

    base = _scrim_mask(variant, size, text_box)
    black = Image.new("RGB", size, (0, 0, 0))
    alpha, ratio, shaded = 0.55, 0.0, canvas
    while True:  # l'opacité monte jusqu'au contraste voulu (déterministe, plafonnée)
        shaded = Image.composite(black, canvas, base.point(lambda v, a=alpha: int(v * a)))
        ratio = 1.05 / (_luminance(shaded, text_box) + 0.05)
        if ratio >= MIN_CONTRAST or alpha >= 0.94:
            break
        alpha = round(alpha + 0.07, 2)

    draw = ImageDraw.Draw(shaded)
    stroke = max(3, px // 14)
    for index, line in enumerate(lines):
        line_w = int(font.getlength(line))
        x = x0 if left_aligned else (width - line_w) // 2
        draw.text((x, y0 + index * line_height), line, font=font, fill=(255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0))
    bar = max(6, height // 160)
    if left_aligned:  # filet d'accent vertical à gauche du texte
        draw.rectangle((max(0, x0 - px // 2), y0, max(0, x0 - px // 2) + bar, y0 + block_h), fill=accent)
    else:             # filet d'accent sous le texte
        bar_w = max(block_w // 3, width // 8)
        draw.rectangle(((width - bar_w) // 2, y0 + block_h + px // 6, (width + bar_w) // 2, y0 + block_h + px // 6 + bar), fill=accent)
    return shaded, Layout(variant, size, tuple(lines), px, text_box, alpha, round(ratio, 2))


def compose_all(background: Image.Image, text: str, size: tuple[int, int] = SIZES["9:16"],
                accent: tuple[int, int, int] = ACCENT) -> dict[str, tuple[Image.Image, Layout]]:
    """Les trois variantes depuis UN SEUL fond : aucun nouvel appel au fournisseur d'images."""
    return {variant: compose(background, text, variant, size, accent) for variant in VARIANTS}


def readability(layout: Layout, preview_width: int = PHONE_PREVIEW_WIDTH) -> Readability:
    """Lisibilité sur téléphone : corps du texte dans l'aperçu réduit et contraste du texte blanc sur le fond."""
    glyph = layout.font_px * preview_width / layout.size[0]
    messages = []
    if glyph < MIN_PHONE_GLYPH_PX:
        messages.append(f"texte trop petit sur téléphone ({glyph:.0f} px) : raccourcis le texte")
    if layout.contrast_ratio < MIN_CONTRAST:
        messages.append(f"contraste insuffisant ({layout.contrast_ratio:.1f}:1) : choisis un fond plus sombre")
    if word_count(layout.text) > 5:
        messages.append("texte long : 2 à 5 mots sont plus lisibles sur téléphone")
    return Readability(glyph >= MIN_PHONE_GLYPH_PX and layout.contrast_ratio >= MIN_CONTRAST, round(glyph, 1), layout.contrast_ratio, tuple(messages))


def phone_preview(image: Image.Image, width: int = PHONE_PREVIEW_WIDTH) -> Image.Image:
    """Aperçu réduit tel qu'affiché dans un flux de téléphone : largeur 360 px, coins arrondis."""
    height = round(image.height * width / image.width)
    preview = image.convert("RGB").resize((width, height), Image.LANCZOS)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=max(10, width // 16), fill=255)
    framed = Image.new("RGB", (width, height), (10, 14, 26))
    framed.paste(preview, (0, 0), mask)
    return framed


def background_prompt(*, topic: str, visual_style: str = "", visual_rules: str = "", visual_avoid: tuple[str, ...] = (),
                      aspect: str = "9:16") -> str:
    """Prompt du fond de miniature : construit UNIQUEMENT depuis le profil visuel du projet et le sujet de la vidéo.

    Il demande explicitement un fond SANS texte (le titre est ajouté localement), sans visage humain ni logo, avec un sujet
    simple et fort et une zone dégagée pour le texte. Aucune règle d'un autre projet ne peut y figurer.
    """
    parts = []
    if visual_style.strip():
        parts.append(f"Style visuel : {visual_style.strip().rstrip('.')}.")
    if visual_rules.strip():
        parts.append(f"Consignes visuelles : {visual_rules.strip().rstrip('.')}.")
    orientation = "vertical" if aspect == "9:16" else ("horizontal" if aspect == "16:9" else "carré")
    parts.append(f"Image de fond {orientation} pour la miniature d’une vidéo sur ce sujet : « {' '.join(topic.split())[:160]} ».")
    parts.append("Un seul sujet visuel fort et simple, gros plan lisible sur téléphone, contraste fort, formes nettes, peu de détails fins, "
                 "une zone dégagée pour du texte ajouté ensuite.")
    parts.append("Aucun texte, aucune lettre, aucun chiffre, aucun logo, aucune marque, aucun filigrane, aucun visage humain.")
    if visual_avoid:
        parts.append("Ne montre jamais : " + " ; ".join(item.rstrip(".") for item in visual_avoid) + ".")
    return " ".join(parts)


def word_count(text: str) -> int:
    return len(clean_text(text).split())


def to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def to_jpeg(image: Image.Image, quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality, optimize=False, progressive=False, subsampling=0)
    return buffer.getvalue()


def placeholder_background(seed: str, size: tuple[int, int] = (1024, 1536)) -> Image.Image:
    """Fond sombre bleu/cyan synthétique et déterministe (mode démonstration et tests) : jamais de texte."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    width, height = size
    image = Image.new("RGB", size, (8, 14, 30))
    draw = ImageDraw.Draw(image)
    for row in range(height):
        shade = int(20 + 60 * row / height)
        draw.line((0, row, width, row), fill=(6, 12 + shade // 3, 30 + shade))
    for index in range(7):
        cx = digest[index] * width // 255
        cy = digest[index + 7] * height // 255
        radius = width // 8 + digest[index + 14] % (width // 6)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(34, 211 - index * 8, 238), width=max(4, width // 120))
    return image.filter(ImageFilter.GaussianBlur(2))
