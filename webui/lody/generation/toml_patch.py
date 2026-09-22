"""Modifie UNE seule clé d'un fichier TOML existant, en laissant tout le reste — commentaires, ordre,
mise en forme, autres sections — strictement identique octet pour octet.

Volontairement plus étroit qu'une bibliothèque TOML générale : ``config.toml`` est à la fois lu par le moteur
(``toml.load``, qui ignore les commentaires) et modifié par sa propre WebUI (``save_config()``, qui réécrit tout
le fichier et perd déjà les commentaires à cette occasion). Ici, à l'inverse, on ne touche JAMAIS aux lignes qui
ne portent pas le champ visé : c'est la garantie demandée pour la page « Paramètres système ».

Ne comprend qu'un seul niveau de table (``[section]``), ce qui couvre exactement les champs de
``secrets_fields.FIELDS``. Toute forme inattendue (section absente, clé absente, valeur qui déborde sur
plusieurs lignes) lève ``PatchError`` plutôt que de deviner : mieux vaut refuser que corrompre.
"""

from __future__ import annotations

import re

_SECTION = re.compile(r"^\s*\[([^\[\].]+)\]\s*(#.*)?$")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


class PatchError(ValueError):
    """Message toujours sûr à journaliser : ne contient jamais la valeur en cause."""


def toml_string(value: str) -> str:
    """Chaîne TOML basique entre guillemets doubles, sur une seule ligne. Public : réutilisée par ``secrets_store``
    pour écrire son propre fichier (schéma fixe, mêmes règles d'échappement)."""
    out = []
    for ch in value:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _key_line(indent: str, key: str, value: str, kind: str) -> str:
    if kind == "list1":
        return f"{indent}{key} = [{toml_string(value)}]\n"
    return f"{indent}{key} = {toml_string(value)}\n"


def _find_section(lines: list[str], section: str) -> tuple[int, int]:
    """(première ligne APRÈS l'en-tête, première ligne de la section suivante ou len(lines))."""
    start = None
    for index, line in enumerate(lines):
        match = _SECTION.match(line)
        if match and match.group(1).strip() == section:
            start = index + 1
            continue
        if start is not None and match:
            return start, index
    if start is None:
        raise PatchError(f"section [{section}] introuvable dans config.toml.")
    return start, len(lines)


def patch(text: str, *, section: str, key: str, value: str, kind: str = "scalar") -> str:
    """Retourne le texte de ``config.toml`` avec ``section.key`` remplacé par ``value``.

    Seule la ligne portant cette clé, dans cette section, est modifiée. Une ligne commentée
    (``# key = "..."``) est ignorée : on cherche la première affectation ACTIVE.
    """
    lines = text.splitlines(keepends=True)
    lo, hi = _find_section(lines, section)
    pattern = re.compile(rf"^(\s*){re.escape(key)}(\s*)=")
    for index in range(lo, hi):
        raw = lines[index]
        stripped = raw.lstrip()
        if stripped.startswith("#"):
            continue
        match = pattern.match(raw)
        if not match:
            continue
        # La valeur doit tenir sur cette ligne (pas de tableau/chaîne multi-ligne) : sinon on refuse plutôt
        # que de deviner où elle se termine.
        rest = raw[match.end():]
        if rest.count("[") != rest.count("]") or rest.count('"""') or rest.count("'''"):
            raise PatchError(f"{section}.{key} : forme inattendue (valeur multi-ligne), modification refusée.")
        lines[index] = _key_line(match.group(1), key, value, kind)
        return "".join(lines)
    raise PatchError(f"{section}.{key} introuvable (ou commentée) dans config.toml.")
