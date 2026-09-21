"""Listes de choix proposées à l'utilisateur (libellés en français).

Les valeurs stockées sont des identifiants stables ; les libellés peuvent changer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Option:
    value: str
    label: str


CONTENT_TYPES = (
    Option("pedagogique", "Explication pédagogique"),
    Option("actualite", "Actualité et veille"),
    Option("storytelling", "Histoire racontée"),
    Option("tutoriel", "Tutoriel pas à pas"),
    Option("divertissement", "Divertissement"),
)
LANGUAGES = (
    Option("fr-FR", "Français"),
    Option("en-US", "Anglais"),
    Option("es-ES", "Espagnol"),
    Option("de-DE", "Allemand"),
    Option("it-IT", "Italien"),
    Option("pt-PT", "Portugais"),
)
FORMATS = (
    Option("9:16", "Vertical 9:16"),
    Option("16:9", "Horizontal 16:9"),
    Option("1:1", "Carré 1:1"),
)
PLATFORMS = (
    Option("youtube_shorts", "YouTube Shorts"),
    Option("tiktok", "TikTok"),
    Option("instagram_reels", "Instagram Reels"),
    Option("youtube", "YouTube"),
    Option("linkedin", "LinkedIn"),
)
TONES = (
    Option("Pédagogique et dynamique", "Pédagogique et dynamique"),
    Option("Clair et précis", "Clair et précis"),
    Option("Sérieux et posé", "Sérieux et posé"),
    Option("Décontracté", "Décontracté"),
    Option("Inspirant", "Inspirant"),
)

TEXT_PROVIDERS = (
    Option("openai", "OpenAI"),
    Option("manual", "Je fournis mon script"),
)
VISUAL_PROVIDERS = (
    Option("openai_image", "Images IA (OpenAI)"),
    Option("local", "Mes propres fichiers"),
)
VOICE_PROVIDERS = (
    Option("elevenlabs", "ElevenLabs"),
    Option("edge", "Voix gratuite (Edge)"),
)
MUSIC_PROVIDERS = (
    Option("none", "Sans musique"),
    Option("library", "Bibliothèque intégrée"),
    Option("elevenlabs", "ElevenLabs Music"),
)

CATALOGS = {
    "content_type": CONTENT_TYPES,
    "language": LANGUAGES,
    "format": FORMATS,
    "text_provider": TEXT_PROVIDERS,
    "visual_provider": VISUAL_PROVIDERS,
    "voice_provider": VOICE_PROVIDERS,
    "music_provider": MUSIC_PROVIDERS,
}
PROVIDER_KIND_BY_FIELD = {
    "text_provider": "text",
    "visual_provider": "visual",
    "voice_provider": "voice",
    "music_provider": "music",
}


def values(options: tuple[Option, ...]) -> tuple[str, ...]:
    return tuple(option.value for option in options)


def label(options: tuple[Option, ...], value: str) -> str:
    for option in options:
        if option.value == value:
            return option.label
    return value or "—"
