"""Service d'authentification administrateur pour Lody Video Factory (voir docs/lody-auth.md).

Conteneur séparé, dépendances standard uniquement, aucune donnée persistée en dehors du fichier d'identifiant
(``secrets/admin-auth.json``, 0640, groupe ``lody-secrets``). Les sessions vivent en mémoire du processus.
"""
