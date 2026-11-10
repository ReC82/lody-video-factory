"""Animation 2D (#119) : contrat de storyboard animé ``lody.animation/1`` et catalogue d'assets versionnés.

Couche PURE : aucun moteur de rendu, aucun appel réseau, aucun code exécuté. Le contrat décrit une scène ;
l'adaptateur vers un moteur (Remotion, prototypé en #118) viendra avec #120. Un projet ou une production
sans animation ne lit jamais ce paquet : comportement strictement inchangé.

- ``contract`` : format JSON, validation stricte, JSON canonique et empreinte.
- ``assets`` : catalogue d'assets réutilisables d'un projet, versions immuables, export/import.
- ``freeze`` : fige un storyboard pour une production (versions explicites + empreinte).
- ``modes`` (#120) : mode de rendu d'un projet — Classique (défaut, inchangé), Animation 2D, Hybride.
- ``worker`` (#120) : worker de rendu isolé (dossier par tâche, un rendu à la fois, reprise, repli hybride) ;
  le fournisseur correspondant est ``lody.generation.animation_connector``.
"""
