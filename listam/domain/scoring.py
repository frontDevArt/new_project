"""Скоринг: подходит ли объявление заявке и насколько.

Чистые функции. Ни базы, ни сети, ни конфига: веса и растяжка бюджета
приходят аргументами, значения по умолчанию — из спеки, а кто их читает
из секции `match`, модуль не знает.

Два разных решения, и путать их нельзя:

* `rejection` — жёсткий критерий. Район не тот, комнат меньше, цена выше
  растянутого бюджета, площадь ниже минимума — звонка не будет, и причина
  ложится в `matches.reject_reason` словом, которое читает человек.
* `score` — балл прошедшего. Шесть факторов, у каждого свой вес; фактор,
  для которого не хватает данных, **из знаменателя исключается**. Иначе
  заявка без диапазона площади получала бы систематически меньший балл,
  чем заявка с диапазоном, — не потому что варианты хуже, а потому что
  клиент меньше рассказал о себе.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from listam.domain.models import Listing, Request

DEFAULT_WEIGHTS: dict[str, float] = {
    "budget": 30, "district": 20, "price_per_sqm": 20,
    "area_rooms": 15, "floor": 10, "seller_type": 5,
}
DEFAULT_STRETCH_PERCENT = 10.0
CHEAP_SATURATION = 0.25        # −25% к медиане района — полный балл за выгодность


@dataclass
class Score:
    """Балл, его разбор и причина отказа. Одно из двух: либо балл, либо причина."""

    value: int = 0                                          # 0–100
    breakdown: dict[str, tuple[float, float]] = field(default_factory=dict)
    rejected_by: str | None = None                          # фактор → (набрано, вес)

    @property
    def matched(self) -> bool:
        return self.rejected_by is None


def rejection(request: Request, listing: Listing, stretch_percent: float) -> str | None:
    """Почему объявление не годится вовсе, или `None`, если годится.

    Причина — одно слово: она попадает в отчёт и в базу, и её читает человек,
    а не разбирает программа.
    """
    if request.districts and listing.district not in request.districts:
        return "район"

    if request.rooms and listing.rooms is not None and listing.rooms < min(request.rooms):
        return "комнаты"

    ceiling = request.stretch(stretch_percent)
    if ceiling is not None:
        # Пропустить бюджет нельзя: объявление без цены может оказаться
        # и вдвое дороже потолка. Это отказ с отдельной причиной — чтобы
        # в отчёте было видно дырку в данных, а не придирку к клиенту.
        if listing.price_usd is None:
            return "цена неизвестна"
        if listing.price_usd > ceiling:
            return "бюджет"

    if request.area_min is not None and listing.area is not None \
            and listing.area < request.area_min:
        return "площадь"

    return None


# --- факторы ----------------------------------------------------------
# Каждый возвращает долю 0…1 или None, если считать не из чего.
# None — это не ноль: вес такого фактора уходит из знаменателя целиком.

def _budget(request: Request, listing: Listing, stretch_percent: float) -> float | None:
    ceiling = request.stretch(stretch_percent)
    if request.budget_max is None or ceiling is None or listing.price_usd is None:
        return None
    if listing.price_usd <= request.budget_max:
        return 1.0
    if ceiling <= request.budget_max:          # растяжки нет — за потолком ноль
        return 0.0
    over = (ceiling - listing.price_usd) / (ceiling - request.budget_max)
    return max(0.0, min(1.0, over))


def _district(request: Request, listing: Listing) -> float | None:
    if listing.district is None:
        return None
    if request.districts_priority and listing.district in request.districts_priority:
        return 1.0
    if not request.districts and not request.districts_priority:
        return None                            # клиент район не назвал — нечего сравнивать
    return 0.5 if listing.district in request.districts else 0.0


def _price_per_sqm(listing: Listing, median_by_district: dict[str, float] | None) -> float | None:
    if not median_by_district or listing.price_per_sqm is None or listing.district is None:
        return None
    median = median_by_district.get(listing.district)
    if not median:
        return None
    cheapness = (median - listing.price_per_sqm) / median
    clamped = max(-CHEAP_SATURATION, min(CHEAP_SATURATION, cheapness))
    return (clamped + CHEAP_SATURATION) / (2 * CHEAP_SATURATION)


def _area_rooms(request: Request, listing: Listing) -> float | None:
    """Половина за площадь, половина за комнаты; незаданная половина не считается."""
    halves: list[float] = []

    asked_area = request.area_min is not None or request.area_max is not None
    if asked_area and listing.area is not None:
        low = request.area_min is None or listing.area >= request.area_min
        high = request.area_max is None or listing.area <= request.area_max
        halves.append(1.0 if low and high else 0.0)

    if request.rooms and listing.rooms is not None:
        halves.append(1.0 if listing.rooms in request.rooms else 0.0)

    return sum(halves) / len(halves) if halves else None


def _floor(request: Request, listing: Listing) -> float | None:
    """Доля выполненных этажных правил заявки. Правил нет — фактор не считается."""
    if listing.floor is None:
        return None
    rules: list[bool] = []

    if request.no_first_floor:
        rules.append(listing.floor != 1)
    if request.no_last_floor and listing.floors_total is not None:
        rules.append(listing.floor != listing.floors_total)
    if request.floor_min is not None:
        rules.append(listing.floor >= request.floor_min)
    if request.floor_max is not None:
        rules.append(listing.floor <= request.floor_max)

    if not rules:
        return None
    return sum(1.0 for ok in rules if ok) / len(rules)


def _seller_type(listing: Listing) -> float | None:
    if listing.seller_type is None:
        return None
    return 1.0 if listing.seller_type == "owner" else 0.0


def score(request: Request, listing: Listing, *,
          median_by_district: dict[str, float] | None = None,
          weights: dict[str, float] | None = None,
          stretch_percent: float = DEFAULT_STRETCH_PERCENT) -> Score:
    """Балл объявления по заявке: 0–100 и разбор, из чего он сложился."""
    refused = rejection(request, listing, stretch_percent)
    if refused is not None:
        return Score(value=0, breakdown={}, rejected_by=refused)

    weights = DEFAULT_WEIGHTS if weights is None else weights
    shares = {
        "budget": lambda: _budget(request, listing, stretch_percent),
        "district": lambda: _district(request, listing),
        "price_per_sqm": lambda: _price_per_sqm(listing, median_by_district),
        "area_rooms": lambda: _area_rooms(request, listing),
        "floor": lambda: _floor(request, listing),
        "seller_type": lambda: _seller_type(listing),
    }

    breakdown: dict[str, tuple[float, float]] = {}
    for name, weight in weights.items():
        compute = shares.get(name)
        if compute is None:
            continue
        share = compute()
        if share is None:
            continue
        breakdown[name] = (round(share * float(weight), 4), float(weight))

    total_weight = sum(weight for _, weight in breakdown.values())
    if not total_weight:
        # Считать нечем: либо все веса нули, либо данных не хватило на всё
        # сразу. Балл ноль, но отказа нет — объявление жёсткие критерии прошло.
        return Score(value=0, breakdown=breakdown, rejected_by=None)

    got = sum(points for points, _ in breakdown.values())
    return Score(value=round(100 * got / total_weight), breakdown=breakdown, rejected_by=None)
