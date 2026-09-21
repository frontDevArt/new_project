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


def _detect_currency(text: str, near: tuple[int, int] | None = None) -> str | None:
    """Валюта цены. Знаков в строке бывает больше одного — берём ближайший к числу.

    «23 800 000 ֏ (около $ 65 000)» — это драмы: доллары здесь чужие, они
    стоят у другого числа. Перебор словаря по порядку отдал бы доллары.
    """
    lowered = text.lower()
    best: str | None = None
    closest: int | None = None
    for sign, code in CURRENCY_SIGNS.items():
        start = lowered.find(sign)
        while start != -1:
            distance = _distance((start, start + len(sign)), near)
            if closest is None or distance < closest:
                best, closest = code, distance
            start = lowered.find(sign, start + 1)
    return best


def _distance(span: tuple[int, int], other: tuple[int, int] | None) -> int:
    if other is None:
        return 0
    return max(other[0] - span[1], span[0] - other[1], 0)


def to_number(digits: str | None) -> float | None:
    """Разделители тысяч и дробная часть. На сайте бывают пробел, запятая и точка.

    Решает позиция **последнего** разделителя: если за ним одна-две цифры —
    это дробная часть, всё остальное разрядные разделители; если три и больше —
    разрядные все. Счётчик точек так не годится: в «123,456,789.00» точка одна,
    но она не разрядная.
    """
    if digits is None:
        return None
    cleaned = re.sub(r"[\s  ']", "", digits)
    if not re.fullmatch(r"\d+([.,]\d+)*", cleaned):
        return None
    last = max(cleaned.rfind(","), cleaned.rfind("."))
    if last != -1 and len(cleaned) - last - 1 <= 2:
        whole = re.sub(r"[.,]", "", cleaned[:last])
        cleaned = f"{whole}.{cleaned[last + 1:]}"
    else:
        cleaned = re.sub(r"[.,]", "", cleaned)
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
    amount = to_number(match.group(0))
    if amount is None:
        return Money(raw=raw, amount=None, currency=None)
    return Money(raw=raw, amount=amount, currency=_detect_currency(raw, match.span()))
