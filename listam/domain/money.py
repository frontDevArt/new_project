"""Цены: на list.am они бывают в долларах и в драмах, разделители разные."""
from __future__ import annotations

import re
from dataclasses import dataclass

CURRENCY_SIGNS = {
    "$": "USD",
    "usd": "USD",
    "долл": "USD",
    "֏": "AMD",
    "драм": "AMD",
    "amd": "AMD",
    "€": "EUR",
    "eur": "EUR",
    "₽": "RUB",
    "руб": "RUB",
    "rub": "RUB",
}

NUMBER = re.compile(r"\d[\d\s.,  ']*\d|\d")


@dataclass(frozen=True)
class Money:
    raw: str | None
    amount: float | None
    currency: str | None

    def to_usd(self, rate_amd_per_usd: float | None) -> float | None:
        if self.amount is None:
            return None
        if self.currency == "USD":
            return self.amount
        if self.currency == "AMD" and rate_amd_per_usd:
            return round(self.amount / rate_amd_per_usd, 2)
        return None

    def to_amd(self, rate_amd_per_usd: float | None) -> float | None:
        if self.amount is None:
            return None
        if self.currency == "AMD":
            return self.amount
        if self.currency == "USD" and rate_amd_per_usd:
            return round(self.amount * rate_amd_per_usd, 2)
        return None


def _detect_currency(text: str) -> str | None:
    lowered = text.lower()
    for sign, code in CURRENCY_SIGNS.items():
        if sign in lowered:
            return code
    return None


def _to_number(digits: str) -> float | None:
    """Убирает разделители тысяч. На сайте встречаются пробел, запятая и точка."""
    cleaned = re.sub(r"[\s  ']", "", digits)
    # Разделители тысяч в группах по три: 59.500.000, 48,500,000
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", cleaned):
        cleaned = re.sub(r"[.,]", "", cleaned)
    else:
        # Единственная точка/запятая с одним-двумя знаками — дробная часть
        cleaned = cleaned.replace(",", ".")
        if cleaned.count(".") > 1:
            cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_price(raw: str | None) -> Money:
    if not raw:
        return Money(raw=raw, amount=None, currency=None)
    match = NUMBER.search(raw)
    if not match:
        return Money(raw=raw, amount=None, currency=None)
    amount = _to_number(match.group(0))
    if amount is None:
        return Money(raw=raw, amount=None, currency=None)
    return Money(raw=raw, amount=amount, currency=_detect_currency(raw))
