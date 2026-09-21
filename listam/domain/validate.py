"""Проверка карточки на мусор.

Продавцы ошибаются: «3-комн., 1 кв.м.», «$ 87,000,000» за 82 метра, этаж 12
из 2. Это не данные, но и не повод выбрасывать чужое объявление — мы его
храним как есть и помечаем. Помеченное видно в выгрузке и не участвует
в медианах.

Пороги здесь — только значения по умолчанию. Рабочие приходят из конфига
(секция `validate`), потому что «дорого» и «мало» — это про рынок, а не про код.
"""
from __future__ import annotations

from listam.domain.models import Listing

# Правила и пороги по умолчанию. Диапазоны — включительно.
RULES: dict = {
    "price_usd": {"min": 5_000, "max": 5_000_000},
    "price_per_sqm": {"min": 100, "max": 20_000},
    "area": {"min": 5, "max": 2_000},
    "min_area_per_room": 8,     # метров на комнату: меньше — опечатка в площади
}

RANGE_RULES = ("price_usd", "price_per_sqm", "area")


def _out_of_range(value: float | None, bounds) -> bool:
    if value is None or not isinstance(bounds, dict):
        return False
    low, high = bounds.get("min"), bounds.get("max")
    if low is not None and value < low:
        return True
    return high is not None and value > high


def detect_anomalies(listing: Listing, rules: dict | None = None) -> str | None:
    """Имена сработавших правил через запятую. Ничего не сработало — None.

    Пустое поле аномалией не считается: «парсер не нашёл» и «в карточке мусор» —
    разные вещи, и путать их нельзя.
    """
    rules = RULES if rules is None else rules
    found = [
        name for name in RANGE_RULES
        if _out_of_range(getattr(listing, name, None), rules.get(name))
    ]

    per_room = rules.get("min_area_per_room")
    if per_room and listing.area and listing.rooms and listing.area / listing.rooms < per_room:
        found.append("rooms_vs_area")

    if listing.floor and listing.floors_total and listing.floor > listing.floors_total:
        found.append("floor")

    return ",".join(found) or None


def _deep_merge(base: dict, over: dict) -> dict:
    """Накладывает `over` на `base`, не теряя соседей внутри вложенных словарей.

    Плоский merge подменял правило целиком: `price_usd: {min: 1000}` уносил
    верхнюю границу, и «$ 87,000,000» переставал быть аномалией. Конфиг говорит
    о том, о чём сказал, — остальное остаётся значением по умолчанию.
    """
    merged = dict(base)
    for key, value in (over or {}).items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = value
    return merged


def rules_from(config) -> dict:
    """Пороги из секции `validate` поверх значений по умолчанию."""
    return _deep_merge(RULES, config.section("validate") or {})
