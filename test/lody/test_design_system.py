"""Design system #214 : garde-fous statiques sur la feuille de style commune.

Ces tests lisent seulement ``webui/lody/styles.css`` (aucun navigateur) : ils échouent si une règle
réintroduit les défauts corrigés par #214 (titres géants, cibles tactiles trop petites, champs < 16 px
qui déclenchent le zoom iOS, jeton utilisé mais jamais défini).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CSS_PATH = Path(__file__).resolve().parents[2] / "webui" / "lody" / "styles.css"
CSS = CSS_PATH.read_text(encoding="utf-8")
CSS_NO_COMMENTS = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)

# Variables injectées par theme.inject_theme (palette du projet), pas par la feuille.
INJECTED = {"--accent-1", "--accent-2", "--accent-3"}
TOKENS = (
    "--space-1", "--space-2", "--space-3", "--space-4", "--space-5", "--space-6",
    "--text-xs", "--text-sm", "--text-md", "--text-lg", "--title-page", "--title-section",
    "--radius", "--radius-sm", "--radius-lg", "--card-pad", "--page-gap", "--page-pad-top", "--touch",
)


def _root_block() -> str:
    match = re.search(r":root\s*\{(.*?)\n\}", CSS_NO_COMMENTS, flags=re.S)
    assert match, "bloc :root introuvable"
    return match.group(1)


def _token(name: str) -> str:
    match = re.search(rf"{re.escape(name)}\s*:\s*([^;]+);", _root_block())
    assert match, f"jeton {name} absent de :root"
    return match.group(1).strip()


def _rem(value: str) -> float:
    alias = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if alias:
        return _rem(_token(alias.group(1)))
    match = re.fullmatch(r"([\d.]+)(rem|px)", value)
    assert match, f"valeur inattendue : {value}"
    number = float(match.group(1))
    return number if match.group(2) == "rem" else number / 16


def _rule(selector_fragment: str) -> str:
    """Corps de la première règle dont le sélecteur commence par ``selector_fragment``."""
    match = re.search(rf"(?m)^{re.escape(selector_fragment)}[^{{]*\{{([^}}]*)\}}", CSS_NO_COMMENTS)
    assert match, f"règle {selector_fragment!r} introuvable"
    return match.group(1)


def test_braces_are_balanced():
    assert CSS_NO_COMMENTS.count("{") == CSS_NO_COMMENTS.count("}")


@pytest.mark.parametrize("token", TOKENS)
def test_design_tokens_are_defined_in_root(token):
    assert _token(token)


def test_every_used_variable_is_defined():
    used = set(re.findall(r"var\((--[\w-]+)", CSS_NO_COMMENTS))
    defined = set(re.findall(r"(--[\w-]+)\s*:", CSS_NO_COMMENTS)) | INJECTED
    assert used - defined == set()


def test_touch_target_is_at_least_44px():
    assert _rem(_token("--touch")) >= 2.75


def test_form_fields_never_go_below_16px():
    """En dessous de 16 px, Safari iOS zoome sur le champ à chaque saisie."""
    assert _rem(_token("--text-md")) >= 1
    for selector in ('[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {',
                     '[data-testid="stTextArea"] textarea {'):
        body = _rule(selector.rstrip(" {"))
        size = re.search(r"font-size:\s*([^;]+);", body).group(1).strip()
        assert size == "var(--text-md)", (selector, size)


def test_page_title_is_contained():
    """Titres de page : plus de H1 de 3rem ; maximum 2rem quel que soit l'écran."""
    page = _token("--title-page")
    match = re.fullmatch(r"clamp\(([\d.]+)rem,\s*[^,]+,\s*([\d.]+)rem\)", page)
    assert match, page
    assert float(match.group(2)) <= 2
    body = _rule(".stApp .hero-title")
    assert "font-size: var(--title-page)" in body
    assert "line-clamp" not in body, "un nom de projet ne doit jamais être tronqué"


def test_buttons_use_the_touch_token():
    for selector in ('button[kind="primary"], button[kind="primaryFormSubmit"] {',
                     'button[kind="secondary"], button[kind="secondaryFormSubmit"] {',
                     'button[kind="tertiary"] {'):
        assert "min-height: var(--touch)" in _rule(selector.rstrip(" {")), selector


def test_header_is_compact_and_beta_badge_discreet():
    container = _rule(".stMainBlockContainer, [data-testid=\"stMainBlockContainer\"]")
    assert "padding: var(--page-pad-top)" in container
    assert _rem(_token("--page-pad-top")) <= 1
    badge = _rule(".stApp .badge")
    size = re.search(r"font-size:\s*([\d.]+)rem", badge)
    assert size and float(size.group(1)) <= 0.7


def test_mobile_overrides_tokens_rather_than_values():
    """Sur téléphone, la densité se règle en redéfinissant les jetons (une seule source de vérité)."""
    mobile = re.search(r"@media \(max-width: 640px\) \{\s*:root \{([^}]*)\}", CSS_NO_COMMENTS)
    assert mobile, "redéfinition mobile des jetons absente"
    for token in ("--page-pad-x", "--page-gap", "--card-pad"):
        assert token in mobile.group(1)


def test_no_large_legacy_paddings_remain():
    """Les anciens rembourrages de carte (1.5rem et plus) passent par --card-pad."""
    assert not re.search(r"padding:\s*1\.[5-9]rem 1\.[5-9]rem", CSS_NO_COMMENTS)
    assert "calc(var(--radius) + 6px)" not in CSS_NO_COMMENTS


def test_theme_still_injects_the_stylesheet(monkeypatch):
    from lody import theme

    captured: list[str] = []
    monkeypatch.setattr(theme.st, "markdown", lambda body, **_: captured.append(body))
    theme.inject_theme()
    assert captured and "--space-4" in captured[0] and "--accent-1:#22D3EE" in captured[0]
