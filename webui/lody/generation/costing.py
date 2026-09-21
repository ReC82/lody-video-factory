"""Estimation du coût d'une génération.

Les tarifs viennent d'un fichier de configuration (jamais des vues) ; le dépôt n'en fournit aucun :
un tarif absent s'affiche « tarif non configuré » et rend le total *partiel*. Les montants sont des
estimations indicatives, jamais un coût mesuré.

Format (``pricing.toml``, voir docs/lody-generation.md) :

    currency = "EUR"
    [text]   openai = 0.0        # par script écrit
    [visual] openai_image = 0.0  # par image
    [voice]  elevenlabs = 0.0    # par tranche de 1 000 caractères
    [music]  elevenlabs = 0.0    # par morceau
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from lody import settings
from lody.generation.models import GenerationRequest, SceneUnits

logger = logging.getLogger("lody.costing")

# Fournisseurs qui n'appellent aucun service payant (fait, pas un tarif).
FREE_PROVIDERS = frozenset({
    ("text", "manual"), ("voice", "edge"), ("music", "none"), ("music", "library"), ("visual", "local"),
})
COMPONENT_LABELS = {"text": "Script", "visual": "Images", "voice": "Voix", "music": "Musique"}
UNIT_LABELS = {"text": "par script", "visual": "par image", "voice": "par 1 000 caractères", "music": "par morceau",
               "thumbnail": "par image"}
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF"}
DEFAULT_CURRENCY = "EUR"


@dataclass(frozen=True)
class PriceBook:
    currency: str = DEFAULT_CURRENCY
    rates: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    source: str = ""

    def rate(self, component: str, provider: str) -> Decimal | None:
        return self.rates.get((component, provider))


def pricing_path() -> Path:
    raw = os.environ.get("LODY_PRICING_PATH", "").strip()
    return Path(raw) if raw else settings.data_dir() / "pricing.toml"


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def load_price_book(path: Path | None = None) -> PriceBook:
    """Lit les tarifs ; fichier absent ou invalide → aucun tarif (rien n'est inventé)."""
    target = path or pricing_path()
    try:
        with target.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        return PriceBook(source="")
    except (OSError, tomllib.TOMLDecodeError) as error:
        logger.warning("fichier de tarifs illisible (%s) : aucun tarif appliqué", type(error).__name__)
        return PriceBook(source="")
    currency = str(raw.get("currency") or DEFAULT_CURRENCY).strip().upper()[:8] or DEFAULT_CURRENCY
    rates: dict[tuple[str, str], Decimal] = {}
    for component in COMPONENT_LABELS:
        table = raw.get(component)
        if isinstance(table, dict):
            for provider, value in table.items():
                amount = _decimal(value)
                if amount is not None:
                    rates[(component, str(provider))] = amount
    return PriceBook(currency=currency, rates=rates, source=str(target))


@dataclass(frozen=True)
class CostLine:
    component: str
    label: str
    provider: str
    quantity: tuple[int, int]
    unit: str
    rate: Decimal | None
    low: Decimal | None
    high: Decimal | None
    status: str  # "priced" | "free" | "unpriced"

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component, "label": self.label, "provider": self.provider,
            "quantity": list(self.quantity), "unit": self.unit,
            "rate": None if self.rate is None else str(self.rate),
            "low": None if self.low is None else str(self.low),
            "high": None if self.high is None else str(self.high),
            "status": self.status,
        }


@dataclass(frozen=True)
class CostEstimate:
    currency: str
    lines: tuple[CostLine, ...]
    total_low: Decimal | None
    total_high: Decimal | None
    partial: bool
    units: SceneUnits
    indicative: bool = True

    @property
    def has_amount(self) -> bool:
        return self.total_high is not None

    def to_detail(self) -> dict[str, Any]:
        return {
            "indicative": True,
            "partial": self.partial,
            "lines": [line.to_dict() for line in self.lines],
            "scenes": list(self.units.scenes),
            "audio_seconds": list(self.units.audio_seconds),
            "characters": list(self.units.characters),
            "thumbnails": self.units.thumbnails,
        }


def _line(component: str, provider: str, quantity: tuple[int, int], unit_divisor: int, book: PriceBook) -> CostLine:
    label = COMPONENT_LABELS[component]
    if (component, provider) in FREE_PROVIDERS:
        zero = Decimal(0)
        return CostLine(component, label, provider, quantity, UNIT_LABELS[component], zero, zero, zero, "free")
    rate = book.rate(component, provider)
    if rate is None:
        return CostLine(component, label, provider, quantity, UNIT_LABELS[component], None, None, None, "unpriced")
    low = rate * Decimal(quantity[0]) / Decimal(unit_divisor)
    high = rate * Decimal(quantity[1]) / Decimal(unit_divisor)
    return CostLine(component, label, provider, quantity, UNIT_LABELS[component], rate, low, high, "priced")


def estimate_cost(request: GenerationRequest, units: SceneUnits, book: PriceBook) -> CostEstimate:
    """Total = somme des lignes tarifées ; une ligne sans tarif rend le total partiel."""
    text_qty = (units.text_calls, units.text_calls)
    lines = [
        _line("text", request.text_provider if units.text_calls else "manual", text_qty, 1, book),
        _line("visual", request.visual_provider, units.images, 1, book),
        _line("voice", request.voice.provider or "elevenlabs", units.characters, 1000, book),
        _line("music", request.music_provider, (units.music_tracks, units.music_tracks), 1, book),
    ]
    if units.thumbnails:  # fond de miniature dédié : même tarif que les images, ligne distincte et visible
        base = _line("visual", request.visual_provider, (units.thumbnails, units.thumbnails), 1, book)
        lines.append(replace(base, component="thumbnail", label="Miniature (fond d’image)"))
    priced = [line for line in lines if line.low is not None and line.high is not None]
    partial = any(line.status == "unpriced" for line in lines)
    total_low = sum((line.low for line in priced), Decimal(0)) if priced else None
    total_high = sum((line.high for line in priced), Decimal(0)) if priced else None
    return CostEstimate(book.currency, tuple(lines), total_low, total_high, partial, units)


def format_money(amount: Decimal | None, currency: str) -> str:
    if amount is None:
        return "tarif non configuré"
    quantized = amount.quantize(Decimal("0.01"))
    text = f"{quantized:.2f}".replace(".", ",")
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    return f"{text} {symbol}"


def format_range(low: Decimal | None, high: Decimal | None, currency: str) -> str:
    if low is None or high is None:
        return "tarif non configuré"
    if format_money(low, currency) == format_money(high, currency):
        return f"≈ {format_money(high, currency)}"
    symbol = CURRENCY_SYMBOLS.get(currency, currency)
    return f"≈ {format_money(low, currency).removesuffix(' ' + symbol)} – {format_money(high, currency)}"


def format_total(estimate: CostEstimate) -> str:
    text = format_range(estimate.total_low, estimate.total_high, estimate.currency)
    if estimate.partial and estimate.has_amount:
        return f"{text} (partiel)"
    return text
