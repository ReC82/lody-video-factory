"""Garde-fou éditorial avant génération (#167) : contrôle « niveau novice » et cohérence script ↔ storyboard.

Couche PURE et HORS RÉSEAU : aucun appel à un fournisseur (LLM, TTS, image), aucune base, aucune écriture.
Le contrôle ne corrige JAMAIS le texte : il signale, renvoie à la scène et au champ exacts, et propose une
piste de correction que l'auteur applique lui-même (un sens technique ne se modifie pas automatiquement).

- ``preprod`` : format de préproduction ``lody.preprod/1`` (brief éditorial + scènes : voix, écran,
  sous-titres, visuel) et adaptateur depuis un storyboard animé ``lody.animation/1`` (#119).
- ``checks`` : les contrôles et le verdict (``check_document``).
- ``report`` : vue lisible scène par scène (tableau Markdown) et export JSON.

Guide : ``docs/lody-garde-fou-editorial.md``.
"""
