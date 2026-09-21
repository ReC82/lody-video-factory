"""Typographie des sous-titres français : élisions, apostrophes et choix de police.

Module volontairement sans dépendance (bibliothèque standard ; Pillow seulement pour mesurer une police) : il est
aussi copié dans le conteneur du moteur par l'outil de réparation de rendu.

Ce que ce module corrige, et pourquoi (incident V7, « l’  idée ») :

* **Cause racine — la police.** Les polices CJK (Microsoft YaHei, STHeiti…) dessinent l'apostrophe
  typographique U+2019 en *pleine largeur* (58 px à corps 58, soit 3,4 espaces) : « l’idée » s'affiche « l’   idée »
  alors qu'aucune espace n'existe dans le texte, le SRT ni la narration. ``pick_subtitle_font`` choisit une police
  latine quand la langue est latine et que la police demandée a une apostrophe pleine largeur (mesurée).
* **Protection supplémentaire — le texte.** ``normalize_french_text`` retire les espaces réellement présentes après
  une apostrophe d'élision (« l’ idée », « qu’  il »), sans toucher aux URL, chemins, courriels ni code.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

APOSTROPHES = "'’ʼ"  # droite, typographique U+2019, modificateur U+02BC (jamais U+2018 : guillemet ouvrant)
_HSPACE = " \t\u00a0\u202f\u2009\u2007"  # espaces horizontales (dont insécables) ; jamais les retours à la ligne

# Élisions du français : le préfixe est un mot à lui seul (« l’ », « qu’ », « jusqu’ », « aujourd’hui »…).
ELISION_PREFIXES = ("aujourd", "quelqu", "jusqu", "lorsqu", "puisqu", "quoiqu", "presqu", "prud", "qu",
                    "c", "d", "j", "l", "m", "n", "s", "t")
_ELISION = re.compile(
    rf"(?<![^\W_])({'|'.join(ELISION_PREFIXES)})([{APOSTROPHES}])[{_HSPACE}]+(?=[^\W_])", re.IGNORECASE)

# Zones à ne jamais modifier : code, URL, courriels, chemins Windows.
_PROTECTED = re.compile(r"`[^`\n]*`|https?://\S+|www\.\S+|\S+@\S+\.\S+|[A-Za-z]:\\\S*")
_SRT_TIMING = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{3}\s*-->")
_ASS_OVERRIDE = re.compile(r"(\{[^}]*\})")


def _fix(segment: str) -> str:
    return _ELISION.sub(lambda match: match.group(1) + match.group(2), segment)


def normalize_french_text(text: str) -> str:
    """Retire les espaces après une apostrophe d'élision française (« l’ idée » → « l’idée »).

    N'agit que si le mot qui précède l'apostrophe est une élision connue (c, d, j, l, m, n, s, t, qu, jusqu,
    lorsqu, puisqu, quoiqu, presqu, quelqu, aujourd, prud) : « the 'quick' fox » ou « les chiens' jouets » ne
    sont pas modifiés. Les espaces ordinaires ailleurs, les accents et le type d'apostrophe sont conservés.
    """
    if not text:
        return text
    out: list[str] = []
    last = 0
    for match in _PROTECTED.finditer(text):
        out.append(_fix(text[last:match.start()]))
        out.append(match.group(0))  # zone protégée : intacte
        last = match.end()
    out.append(_fix(text[last:]))
    return "".join(out)


def normalize_for_language(text: str, language: str) -> str:
    """Normalisation française uniquement pour une langue française (fr, fr-FR, fr-CA…)."""
    return normalize_french_text(text) if language.lower().replace("_", "-").split("-")[0] == "fr" else text


def normalize_srt(content: str) -> tuple[str, int]:
    """SRT : ne touche que le texte des sous-titres (pas les numéros ni les minutages). Retourne (texte, nb de lignes changées)."""
    changed = 0
    lines = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or stripped.isdigit() or _SRT_TIMING.match(line):
            lines.append(line)
            continue
        fixed = normalize_french_text(line)
        changed += fixed != line
        lines.append(fixed)
    return "".join(lines), changed


def normalize_ass(content: str) -> tuple[str, int]:
    """ASS : ne touche que le champ Texte des lignes ``Dialogue:``, en épargnant les balises ``{\\...}``."""
    changed = 0
    lines = []
    for line in content.splitlines(keepends=True):
        if not line.startswith("Dialogue:"):
            lines.append(line)
            continue
        parts = line.split(",", 9)  # les 9 premiers champs ASS, puis le texte (qui peut contenir des virgules)
        if len(parts) < 10:
            lines.append(line)
            continue
        pieces = _ASS_OVERRIDE.split(parts[9])
        fixed_text = "".join(piece if piece.startswith("{") else normalize_french_text(piece) for piece in pieces)
        changed += fixed_text != parts[9]
        lines.append(",".join(parts[:9] + [fixed_text]))
    return "".join(lines), changed


def normalize_subtitle_file(path: Path, target: Path | None = None) -> int:
    """Normalise un fichier .srt/.vtt/.ass/.ssa (au besoin vers ``target``). Retourne le nombre de lignes corrigées."""
    content = path.read_text(encoding="utf-8")
    fixer = normalize_ass if path.suffix.lower() in (".ass", ".ssa") else normalize_srt
    fixed, changed = fixer(content)
    (target or path).write_text(fixed, encoding="utf-8")
    return changed


def join_timed_words(words: Iterable[str | tuple[str, float, float]]) -> str:
    """Reconstruit le texte d'un segment depuis des mots horodatés (mot ou ``(mot, début, fin)``).

    Une espace sépare les mots, sauf après une élision (« l’ » + « idée » → « l’idée ») et avant un mot qui commence
    par une apostrophe (« l » + « ’idée »). Le moteur de sous-titres n'utilise pas ce chemin aujourd'hui (son SRT est
    construit depuis le script) : c'est le point unique à utiliser si un rendu doit un jour reconstruire les segments.
    """
    tokens = [(item[0] if isinstance(item, tuple) else item).strip() for item in words]
    text = ""
    for token in (t for t in tokens if t):
        if text and not (token[0] in APOSTROPHES or text[-1] in APOSTROPHES):
            text += " "
        text += token
    return normalize_french_text(text)


# -- police -----------------------------------------------------------------------------------------------------------
LATIN_SAFE_FONT = "BeVietnamPro-Bold.ttf"  # couvre le français ; apostrophe de largeur normale
CJK_LANGUAGES = frozenset({"zh", "ja", "ko"})
# Polices connues pour leur apostrophe pleine largeur (repli quand elles ne peuvent pas être mesurées).
KNOWN_WIDE_APOSTROPHE = frozenset({"MicrosoftYaHeiBold.ttc", "MicrosoftYaHeiNormal.ttc", "STHeitiLight.ttc",
                                   "STHeitiMedium.ttc"})
WIDE_RATIO = 0.6  # avance de ’ supérieure à 0,6 em = pleine largeur


def apostrophe_is_wide(font_path: Path, size: int = 58) -> bool | None:
    """Vrai si ’ (U+2019) est en pleine largeur dans cette police ; ``None`` si elle ne peut pas être mesurée."""
    try:
        from PIL import ImageFont

        return ImageFont.truetype(str(font_path), size).getlength("’") > WIDE_RATIO * size
    except Exception:
        return None


def pick_subtitle_font(requested: str, language: str, fonts_dir: Path) -> tuple[str, str]:
    """(police à utiliser, raison du changement ou ``""``).

    Une langue latine ne doit pas s'afficher avec une police dont l'apostrophe typographique est pleine largeur :
    on la remplace alors par ``LATIN_SAFE_FONT`` (si elle est disponible), et on le dit. Sinon la police demandée est
    conservée telle quelle, de même que pour le chinois, le japonais et le coréen.
    """
    primary = language.lower().replace("_", "-").split("-")[0]
    if primary in CJK_LANGUAGES:
        return requested, ""
    safe_available = (fonts_dir / LATIN_SAFE_FONT).is_file()
    if not requested:
        return (LATIN_SAFE_FONT, "aucune police demandée") if safe_available else (requested, "")
    if requested == LATIN_SAFE_FONT or not safe_available:
        return requested, ""
    path = fonts_dir / requested
    wide = apostrophe_is_wide(path) if path.is_file() else None
    if wide is None:
        wide = requested in KNOWN_WIDE_APOSTROPHE
    if wide:
        return LATIN_SAFE_FONT, (f"la police « {requested} » dessine l’apostrophe typographique en pleine largeur "
                                 f"(« l’ idée » s’afficherait « l’   idée ») ; police latine « {LATIN_SAFE_FONT} » utilisée")
    return requested, ""
