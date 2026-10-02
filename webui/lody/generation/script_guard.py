"""Frontière bloquante entre la demande de production et le texte réellement prononçable (ticket #76).

Contexte : lors d'un test réel (projet PNJ), le texte envoyé au TTS reprenait presque directement le brief
et des consignes de production — « Épisode 0 — Le réveil d'Eli », « Utiliser principalement Eli », « Images
fixes uniquement »... Le pipeline n'appliquait jusqu'ici qu'un contrôle de non-vacuité (``if not script:
raise ...``, voir ``generation/service.py``) avant le storyboard et l'envoi au moteur — aucun contrôle
sémantique séparant le texte à PRONONCER des métadonnées de production qui l'ont fait naître.

``validate_spoken_script`` s'applique au texte CANDIDAT — qu'il vienne d'un appel au générateur de script
ou d'un script fourni manuellement — à DEUX points de ``generation/service.py._run`` : juste après
acceptation du script (AVANT ``build_storyboard()``) et de nouveau juste avant ``provider.submit()``
(défense en profondeur, comme ``_assert_voice_unchanged`` du ticket #75). Elle lève ``ProviderError``
(``ErrorKind.INVALID_RESPONSE``, même famille que le contrôle de non-vacuité déjà existant) : la production
échoue proprement, AVANT tout appel TTS ou visuel, sans retry payant automatique (comportement déjà garanti
par le reste du pipeline pour toute ``ProviderError``).

Heuristique volontairement simple (jamais une analyse linguistique complète ni une modération générale,
hors scope du ticket) : reconnaît des MOTIFS de métadonnées de production (titre d'épisode, liste de
scènes, consignes techniques connues, durée/format) et une reprise verbatim substantielle d'un champ du
BRIEF DE CETTE PRODUCTION (jamais une liste de mots-clés figée pour un seul projet — voir le risque
« validation trop spécifique à PNJ » du ticket). Aucun extrait brut n'apparaît jamais dans un message
d'erreur : seule la NATURE du motif détecté est indiquée (voir le risque « fuite du contenu dans les logs »).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from lody.generation.models import ErrorKind, ProviderError
from lody.generation.store import Production

# Motifs génériques de métadonnées de production — jamais un mot du vocabulaire narratif ordinaire.
_EPISODE_TITLE = re.compile(r"(?i)épisode\s+\d+\s*[—–\-:]")
_SCENE_MARKER = re.compile(r"(?i)\bscènes?\s*\d+\b")
_DURATION = re.compile(r"\b\d{1,3}\s*(?:à|-|–)\s*\d{1,3}\s*secondes?\b", re.IGNORECASE)
_FORMAT_CODE = re.compile(r"\b(?:9\s*:\s*16|16\s*:\s*9|1\s*:\s*1)\b")

# Consignes de production connues : phrases d'instruction, jamais des tournures de narration naturelle.
_TECHNICAL_PHRASES = (
    "images fixes uniquement", "utiliser principalement", "consignes visuelles", "consignes permanentes",
    "à ne jamais montrer", "prompt de référence", "structure type", "rythme visuel", "scènes par minute",
    "storyboard", "video_script", "video_terms", "personnage principal sélectionné",
)

# Longueur minimale d'un extrait CONTIGU d'un champ du brief retrouvé tel quel dans le script : au-delà,
# une reprise verbatim n'est plus une coïncidence de vocabulaire (voir _brief_fields/_overlap_hit).
MIN_OVERLAP = 40
_STRIDE = 8  # pas de la fenêtre glissante : assez fin pour ne rien manquer, sans coût O(n²) déraisonnable


def _technical_phrase_hit(lowered_text: str) -> str | None:
    for phrase in _TECHNICAL_PHRASES:
        if phrase in lowered_text:
            return phrase
    return None


def _overlap_hit(lowered_text: str, fields: Iterable[str]) -> bool:
    """Vrai si un extrait contigu d'au moins ``MIN_OVERLAP`` caractères d'un des champs apparaît TEL QUEL
    (insensible à la casse) dans ``lowered_text``."""
    for field in fields:
        normalized = " ".join(str(field or "").split()).lower()
        if len(normalized) < MIN_OVERLAP:
            continue
        for start in range(0, len(normalized) - MIN_OVERLAP + 1, _STRIDE):
            if normalized[start:start + MIN_OVERLAP] in lowered_text:
                return True
    return False


def _brief_fields(production: Production) -> list[str]:
    """Tous les champs texte du brief ET de la sélection narrative figée (#35) de CETTE production —
    jamais une liste figée pour un seul projet : ce que l'utilisateur ou ses fiches personnage/lieu ont
    réellement fourni, propre à chaque production."""
    # ``demande`` (le sujet demandé par l'utilisateur) est délibérément EXCLU : un script est censé parler
    # de son sujet, parfois en le citant presque mot pour mot — ce n'est jamais une fuite de consigne de
    # production, contrairement aux métadonnées/instructions ci-dessous.
    brief = production.brief or {}
    visuels = brief.get("visuels") or {}
    fields = [
        brief.get("orientation", ""), brief.get("ton", ""),
        brief.get("duree_cible", ""), visuels.get("style", ""), visuels.get("regles", ""),
        brief.get("consignes_permanentes", ""),
    ]
    fields += list(visuels.get("a_eviter") or [])
    fields += list(brief.get("structure") or [])

    narrative = (production.snapshot or {}).get("narrative_context") or {}
    for character in narrative.get("characters") or []:
        fields += [
            character.get("role", ""), character.get("personality", ""), character.get("speech_style", ""),
            character.get("permanent_elements", ""), character.get("continuity_notes", ""),
            character.get("reference_prompt", ""),
        ]
    location = narrative.get("location")
    if location:
        fields += [location.get("description", ""), location.get("continuity_notes", ""),
                  location.get("reference_prompt", "")]
    return fields


def validate_spoken_script(script: str, production: Production) -> None:
    """Lève ``ProviderError(ErrorKind.INVALID_RESPONSE)`` si ``script`` ressemble encore à une consigne de
    production plutôt qu'à un texte prononçable. Ne lève jamais autre chose ; ne modifie jamais ``script``
    (jamais un remplacement silencieux par le sujet brut — voir le docstring du module)."""
    text = str(script or "")
    lowered = text.lower()
    if _EPISODE_TITLE.search(text):
        raise ProviderError(ErrorKind.INVALID_RESPONSE,
                            "Le texte généré ressemble à un titre d’épisode ou de production, pas à un "
                            "texte prononçable : production refusée avant tout envoi.", stage="script")
    if _SCENE_MARKER.search(text):
        raise ProviderError(ErrorKind.INVALID_RESPONSE,
                            "Le texte généré contient une liste de scènes numérotées, pas un texte "
                            "prononçable : production refusée avant tout envoi.", stage="script")
    if _DURATION.search(text):
        raise ProviderError(ErrorKind.INVALID_RESPONSE,
                            "Le texte généré contient une durée technique de production, pas un texte "
                            "prononçable : production refusée avant tout envoi.", stage="script")
    if _FORMAT_CODE.search(text):
        raise ProviderError(ErrorKind.INVALID_RESPONSE,
                            "Le texte généré contient un format vidéo technique, pas un texte prononçable : "
                            "production refusée avant tout envoi.", stage="script")
    if _technical_phrase_hit(lowered):
        raise ProviderError(ErrorKind.INVALID_RESPONSE,
                            "Le texte généré contient une consigne de production, pas un texte prononçable : "
                            "production refusée avant tout envoi.", stage="script")
    if _overlap_hit(lowered, _brief_fields(production)):
        raise ProviderError(ErrorKind.INVALID_RESPONSE,
                            "Le texte généré reprend quasi intégralement une consigne du brief de "
                            "production : production refusée avant tout envoi.", stage="script")
