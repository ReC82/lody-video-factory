"""Projets d'exemple créés au premier démarrage (puis modifiables librement)."""

from __future__ import annotations

SEED_PROJECTS: list[dict] = [
    {
        "seed_key": "audiovisuel",
        "name": "Audiovisuel",
        "description": "Des explications courtes et précises sur la régie, le studio et la chaîne de diffusion.",
        "content_type": "pedagogique",
        "language": "fr-FR",
        "format": "9:16",
        "tone": "Clair et précis",
        "visual_style": "Réaliste broadcast : régie, plateau, écrans — sans texte ni logo",
        "platforms": ["youtube_shorts", "tiktok", "instagram_reels"],
        "text_provider": "openai",
        "visual_provider": "openai_image",
        "voice_provider": "elevenlabs",
        "voice_name": "Kev - Young, Dynamic and Bright",
        "music_provider": "library",
        "settings": {
            "accent": 0,
            "target_duration": "45 – 65 s",
            "voice_id": "jGpnMdbhtKgQbVrYezOx",
            "voice_model": "eleven_multilingual_v2",
            "example_request": "Explique simplement la différence entre Fill et Key en télévision.",
        },
    },
    {
        "seed_key": "lodycrypto",
        "name": "LodyCrypto",
        "description": "Une vidéo pédagogique pour débutants, du concept au jeu — sans promesse financière.",
        "content_type": "pedagogique",
        "language": "fr-FR",
        "format": "9:16",
        "tone": "Pédagogique et dynamique",
        "visual_style": "Sombre et moderne, cyan et bleu — sans texte ni logo",
        "platforms": ["youtube_shorts", "tiktok", "instagram_reels"],
        "text_provider": "openai",
        "visual_provider": "openai_image",
        "voice_provider": "elevenlabs",
        "voice_name": "Kev - Young, Dynamic and Bright",
        "music_provider": "elevenlabs",
        "settings": {
            "accent": 1,
            "target_duration": "45 – 65 s",
            "voice_id": "jGpnMdbhtKgQbVrYezOx",
            "voice_model": "eleven_multilingual_v2",
            "example_request": "Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming.",
        },
    },
]
