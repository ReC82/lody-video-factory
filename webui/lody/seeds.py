"""Projets d'exemple créés au premier démarrage (puis modifiables librement)."""

from __future__ import annotations

# Consignes permanentes de LodyCrypto : reprises des exigences éditoriales de la série
# (débutant complet, aucune promesse financière, séparation technique / usages / risques /
# spéculation, visuels sans texte ni logo).
_CRYPTO_INSTRUCTIONS = "\n".join([
    "Pars du principe que le spectateur ne connaît rien à la crypto : explique chaque terme technique en une phrase simple.",
    "Ne fais aucune promesse de gain et ne donne aucun conseil financier ; ne parle jamais de rendement garanti.",
    "Distingue toujours la technique, les usages, les risques et la spéculation.",
    "Reste honnête : dis clairement quand quelque chose est risqué, incertain ou que tu ne sais pas.",
    "Amène le sujet rapidement, mais progressivement, vers le Web3 gaming.",
    "Pas de posture d’expert, pas de jargon inutile, pas de ton condescendant.",
    "Aucun texte, logo ou marque dans les images.",
])

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
        "tone": "Simple, honnête, dynamique, sans posture d’expert",
        "visual_style": (
            "Univers sombre et moderne, accents cyan et bleu électrique, crypto et gaming, "
            "sans texte, sans logo, sans marque"
        ),
        "platforms": ["youtube_shorts", "tiktok", "instagram_reels"],
        "text_provider": "openai",
        "visual_provider": "openai_image",
        "voice_provider": "elevenlabs",
        "voice_name": "Kev - Young, Dynamic and Bright",
        "music_provider": "elevenlabs",
        "settings": {
            "accent": 1,
            "example_request": "Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming.",
            "brief": {
                "duration_min": 45,
                "duration_max": 60,
                "audience": "Débutants complets en crypto",
                "orientation": "Progression rapide vers le Web3 gaming",
                "narration_pace": "rapide",
                "scenes_per_minute_min": 8,
                "scenes_per_minute_max": 10,
                "voice_id": "jGpnMdbhtKgQbVrYezOx",
                "voice_model": "eleven_multilingual_v2",
                "structure": [
                    "Accroche",
                    "Explication simple",
                    "Exemple concret",
                    "Erreur ou piège à éviter",
                    "Idée à retenir",
                    "Annonce de l’épisode suivant",
                ],
                "standing_instructions": _CRYPTO_INSTRUCTIONS,
            },
        },
        # Valeurs d'origine (version précédente de l'exemple) : elles ne sont remplacées
        # sur un projet existant que si l'utilisateur ne les a pas modifiées.
        "legacy_values": {
            "tone": "Pédagogique et dynamique",
            "visual_style": "Sombre et moderne, cyan et bleu — sans texte ni logo",
        },
    },
]
