"""Index de la documentation Lody (`docs/README.md`, ticket #129).

Vérifie que chaque page `docs/lody*.md` est référencée par l'index et que chaque lien relatif
de l'index pointe vers un fichier existant. Lecture de fichiers uniquement."""

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
INDEX = DOCS / "README.md"

# Lien Markdown en ligne : [texte](cible) ; la cible s'arrête au premier espace ou ')'.
LINK_RE = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def _relative_targets(text: str) -> list[str]:
    targets = []
    for raw in LINK_RE.findall(text):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw) or raw.startswith("#"):
            continue  # URL externe (http:, mailto:…) ou ancre interne
        path = unquote(raw.split("#", 1)[0])
        if path:
            targets.append(path)
    return targets


def _referenced_pages(text: str) -> set[Path]:
    return {(DOCS / target).resolve() for target in _relative_targets(text)}


def test_index_exists():
    assert INDEX.is_file(), "docs/README.md (index de la documentation Lody) est absent"


def test_every_lody_page_is_referenced():
    referenced = _referenced_pages(INDEX.read_text(encoding="utf-8"))
    pages = sorted(DOCS.glob("lody*.md"))
    assert pages, "aucune page docs/lody*.md trouvée"
    missing = [p.name for p in pages if p.resolve() not in referenced]
    assert not missing, f"pages absentes de docs/README.md : {missing}"


def test_index_has_no_broken_link():
    broken = [t for t in _relative_targets(INDEX.read_text(encoding="utf-8")) if not (DOCS / t).exists()]
    assert not broken, f"liens cassés dans docs/README.md : {broken}"


def test_helpers_detect_missing_page_and_broken_link():
    text = "- [A](lody-ui.md) — x\n- [B](absent.md#ancre) — y\n- [C](https://example.org/a.md)\n"
    assert _relative_targets(text) == ["lody-ui.md", "absent.md"]
    assert (DOCS / "lody-ui.md").resolve() in _referenced_pages(text)
    assert (DOCS / "lody-auth.md").resolve() not in _referenced_pages(text)
