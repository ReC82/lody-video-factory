"""Projets de démonstration du prototype.

Ces profils sont **statiques et temporaires** : ils illustrent ce que la couche
« profils de chaîne » exposera plus tard (voir docs/audits). Ils ne contiennent
aucun secret et ne sont lus par aucun fournisseur.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectProfile:
    key: str
    name: str
    headline: str
    tagline: str
    example: str
    format: str
    duration: str
    voice: str
    style: str
    subtitles: str
    # Teintes d'accent (CSS) : elles changent l'ambiance de l'écran par projet.
    accent_from: str
    accent_via: str
    accent_to: str


PROJECTS: tuple[ProjectProfile, ...] = (
    ProjectProfile(
        key="audiovisuel",
        name="Audiovisuel",
        headline="Rends la technique broadcast limpide.",
        tagline="Des explications courtes et précises sur la régie, le studio et la chaîne de diffusion.",
        example="Explique simplement la différence entre Fill et Key en télévision.",
        format="Vertical 9:16",
        duration="45 – 65 s",
        voice="ElevenLabs · Kev",
        style="Réaliste broadcast : régie, plateau, écrans — sans texte ni logo",
        subtitles="Par phrase, fond arrondi",
        accent_from="#22D3EE",
        accent_via="#38BDF8",
        accent_to="#60A5FA",
    ),
    ProjectProfile(
        key="lodycrypto",
        name="LodyCrypto",
        headline="Rends le Web3 clair en une minute.",
        tagline="Une vidéo pédagogique pour débutants, du concept au jeu — sans promesse financière.",
        example="Explique ce qu’est une blockchain pour quelqu’un qui découvre le Web3 gaming.",
        format="Vertical 9:16",
        duration="45 – 65 s",
        voice="ElevenLabs · Kev",
        style="Sombre et moderne, cyan et bleu — sans texte ni logo",
        subtitles="Par phrase, fond arrondi",
        accent_from="#60A5FA",
        accent_via="#818CF8",
        accent_to="#A78BFA",
    ),
)

DEFAULT_PROJECT_KEY = PROJECTS[0].key
_BY_KEY = {project.key: project for project in PROJECTS}


def get_project(key: str | None) -> ProjectProfile:
    """Retourne le projet demandé, ou le projet par défaut si la clé est inconnue."""
    return _BY_KEY.get(str(key or "").lower(), _BY_KEY[DEFAULT_PROJECT_KEY])
