"""Table unique des champs gérés par « Paramètres système » : où ils vivent dans ``config.toml``, comment les
valider, comment les nommer dans l'interface. Partagée par ``secrets_store`` (côté Lody) et ``apply_secrets``
(côté hôte, hors conteneur) : un seul endroit décide de la forme acceptée et de l'emplacement dans le fichier.

Volontairement limité aux fournisseurs que le catalogue de Lody (``catalog.py``) propose réellement :
OpenAI (script, images) et ElevenLabs (voix, musique). Les ~20 autres fournisseurs du moteur ne sont proposés
nulle part dans l'interface de Lody ; ce n'est pas un oubli.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Choix autorisés pour le fournisseur de script actif (liste blanche stricte, jamais de valeur libre).
# MVP : seul "openai" est proposé, c'est le seul fournisseur de script que le catalogue de Lody sait utiliser.
LLM_PROVIDER_CHOICES = ("openai",)

_MAX_LEN = 512
# Aucun espace ni caractère de contrôle : une clé d'API n'en contient jamais ; ceci rejette un texte collé par erreur.
_SHAPE = re.compile(r"^\S+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class Field:
    path: str            # identifiant stable ("app.openai_api_key"), utilisé par l'UI et le statut
    section: str          # section TOML ("app", "elevenlabs")
    key: str              # clé TOML dans cette section
    kind: str             # "scalar" | "list1" (liste à un seul élément) | "choice"
    secret: bool          # True : jamais journalisé/affiché en clair, jamais dans un message d'erreur
    label: str
    provider_label: str
    provider_id: str = ""      # identifiant du fournisseur dans engine_facts (llm_key/eleven_key) : "" si sans objet
    capability: str = ""       # "Texte", "Image", "Voix, musique" — affiché sur la carte
    choices: tuple[str, ...] = field(default_factory=tuple)


FIELDS: dict[str, Field] = {
    f.path: f for f in (
        Field("app.openai_api_key", "app", "openai_api_key", "scalar", True,
              "Clé de script", "OpenAI", provider_id="openai", capability="Texte"),
        Field("app.openai_image_api_keys", "app", "openai_image_api_keys", "list1", True,
              "Clé d’images", "OpenAI", provider_id="openai", capability="Image"),
        Field("app.llm_provider", "app", "llm_provider", "choice", False,
              "Fournisseur de script actif", "Moteur", choices=LLM_PROVIDER_CHOICES),
        Field("elevenlabs.api_key", "elevenlabs", "api_key", "scalar", True,
              "Clé voix et musique", "ElevenLabs", provider_id="elevenlabs", capability="Voix, musique"),
    )
}
FIELD_ORDER = tuple(FIELDS)  # ordre d'affichage stable
# Fournisseurs de script reconnus par le moteur (voir app/models/llm_provider.py) mais que le catalogue de Lody
# ne propose PAS de sélectionner : affichés en lecture seule (« Autres fournisseurs »), sans formulaire de
# remplacement — Lody n'enverrait jamais de requête à travers eux.
_MANAGED_LLM_PROVIDERS = frozenset({"openai"})


class ShapeError(ValueError):
    """Message toujours sûr à afficher : ne contient jamais la valeur refusée."""


def validate(field_path: str, value: str) -> str:
    """Valide la FORME d'une valeur (jamais son exactitude auprès du fournisseur). Retourne la valeur nettoyée
    (espaces de bord retirés) ou lève ``ShapeError`` avec un message qui ne cite jamais la valeur."""
    spec = FIELDS.get(field_path)
    if spec is None:
        raise ShapeError("Champ inconnu.")
    cleaned = value.strip()
    if spec.kind == "choice":
        if cleaned not in spec.choices:
            raise ShapeError(f"{spec.label} : valeur non reconnue (liste blanche).")
        return cleaned
    if not cleaned:
        raise ShapeError(f"{spec.label} : la valeur ne peut pas être vide.")
    if len(cleaned) > _MAX_LEN:
        raise ShapeError(f"{spec.label} : valeur trop longue.")
    if _CONTROL.search(cleaned) or not _SHAPE.fullmatch(cleaned):
        raise ShapeError(f"{spec.label} : la valeur ne doit contenir ni espace ni saut de ligne.")
    return cleaned
