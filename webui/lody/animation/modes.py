"""Mode de rendu d'un projet (#120) : Classique, Animation 2D ou Hybride.

- **Classique** (défaut) : le pipeline historique MoneyPrinterTurbo, strictement inchangé. Un projet qui
  n'a jamais choisi de mode est classique ; sa demande, son instantané et leur empreinte ne changent pas.
- **Animation 2D** : le storyboard animé figé (#119) est rendu scène par scène par le worker 2D ; une scène
  en échec fait échouer la production (rien n'est présenté comme animé s'il ne l'est pas).
- **Hybride** : même rendu, mais une scène dont l'animation échoue est remplacée par son **image de repli**
  (``render.fallback`` du contrat), avec un avertissement explicite par scène.

Le mode est un réglage du projet (``settings["render_mode"]``), hors du brief : il choisit le fournisseur.
"""

from __future__ import annotations

from typing import Any

CLASSIC = "classique"
ANIMATION = "animation_2d"
HYBRID = "hybride"

SETTINGS_KEY = "render_mode"
RENDER_MODES = {CLASSIC: "Classique", ANIMATION: "Animation 2D", HYBRID: "Hybride"}
DESCRIPTIONS = {
    CLASSIC: "Images générées et voix off assemblées par le moteur historique (inchangé).",
    ANIMATION: "Storyboard animé 2D rendu image par image ; une scène en échec arrête la production.",
    HYBRID: "Storyboard animé 2D ; une scène en échec est remplacée par son image de repli, signalée.",
}

# Identifiants des fournisseurs (chaînes, pour éviter tout import circulaire avec ``lody.generation``).
CLASSIC_PROVIDER = "moneyprinterturbo"
ANIMATION_PROVIDER = "animation_2d"


def project_render_mode(settings: dict[str, Any] | None) -> str:
    """Mode enregistré d'un projet ; toute valeur absente ou inconnue vaut Classique (jamais d'erreur)."""
    value = (settings or {}).get(SETTINGS_KEY)
    return value if value in RENDER_MODES else CLASSIC


def is_animated(mode: str) -> bool:
    return mode in (ANIMATION, HYBRID)


def provider_for_mode(mode: str) -> str:
    """Fournisseur d'un mode : le moteur historique pour Classique, le worker 2D sinon."""
    return ANIMATION_PROVIDER if is_animated(mode) else CLASSIC_PROVIDER


def validate_mode(value: Any) -> str:
    if value not in RENDER_MODES:
        raise ValueError("Mode de rendu inconnu : " + ", ".join(RENDER_MODES.values()) + ".")
    return value
