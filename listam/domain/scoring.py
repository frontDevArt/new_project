"""Скоринг: подходит ли объявление заявке и насколько.

Чистые функции. Ни базы, ни сети, ни конфига: веса и растяжка бюджета
приходят аргументами, значения по умолчанию — из спеки, а кто их читает
из секции `match`, модуль не знает.

Два разных решения, и путать их нельзя:

* `rejection` — жёсткий критерий. Район не тот, комнат меньше, цена выше
  растянутого бюджета, площадь ниже минимума — звонка не будет, и причина
  ложится в `matches.reject_reason` словом, которое читает человек.
* `score` — балл прошедшего. Семь факторов, у каждого свой вес; фактор,
  для которого не хватает данных, **из знаменателя исключается**. Иначе
  заявка без диапазона площади получала бы систематически меньший балл,
  чем заявка с диапазоном, — не потому что варианты хуже, а потому что
  клиент меньше рассказал о себе.

Пожелания со страницы объявления (фаза 3 M3.5, решения 9 и 11): `must` —
жёсткие, проверяются в `rejection`, и без открытой страницы объявление не
матч, а кандидат («страница не открыта»); `nice` — седьмой фактор `wishes`.
Поле страницы, которого нет, — не отказ и не ноль: оно неизвестно.

Отказы клиента (фаза 6 M3.5, решение 15) — ещё жёсткие критерии:
отвергнутая квартира (кластер) и то, чем брокер объяснил отказ, — первый
этаж, район, тип дома. Причина закрытия — «клиент отказал: <слова>».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from listam.domain.models import Exclusion, Listing, PageFields, Request
from listam.domain.wishes import Wish, field_label

DEFAULT_WEIGHTS: dict[str, float] = {
    "budget": 10, "district": 20, "price_per_sqm": 40,     # фаза 4: было 30 и 20
    "area_rooms": 15, "floor": 10, "seller_type": 5,
    "wishes": 15,       # предварительно: фаза 4 не мерила — в кэше 5 страниц (Д-1)
}
NOT_OPENED = "страница не открыта"
DEFAULT_STRETCH_PERCENT = 10.0
DEFAULT_HOT = 80.0             # порог «звони сейчас»; фаза 4 M3.5: было 70, замер в плане
# Доля фактора `district` у непервоочередного района. Была 0,5: широкая заявка
# получала горячим почти всё, что лежит в названных районах (замер фазы 4 M3.5).
DEFAULT_SECONDARY_DISTRICT = 0.0
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


def rejection(request: Request, listing: Listing, stretch_percent: float, *,
              page: PageFields | None = None, must: Sequence[Wish] = (),
              refused: Sequence[Exclusion] = (),
              cluster_id: str | None = None) -> str | None:
    """Почему объявление не годится вовсе, или `None`, если годится.

    Причина — одно слово: она попадает в отчёт и в базу, и её читает человек,
    а не разбирает программа.

    `refused` — исключения заявки из отказов клиента; `cluster_id` — кластер
    объявления в этом проходе (колонка `listings.cluster_id` бывает вчерашней).
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

    # Отказ клиента — после грубого сита: «бюджет» честнее, вариант не
    # подошёл бы и без отказа.
    for exclusion in refused:
        if _refuses(exclusion, listing, cluster_id, page):
            return REFUSED + (f": {exclusion.reason}" if exclusion.reason else "")

    # Страничные условия — последними: отказ грубого сита честнее, чем
    # «страница не открыта», и ради такого объявления страницу не откроют.
    if must:
        if page is None:
            return NOT_OPENED
        for wish in must:
            if wish.check(page) is False:
                return field_label(wish.field)

    return None


REFUSED = "клиент отказал"
FIRST_FLOOR, LAST_FLOOR, DISTRICT, CLUSTER = "first_floor", "last_floor", "district", "cluster"
NOT_PAGE_KINDS = frozenset({FIRST_FLOOR, LAST_FLOOR, DISTRICT, CLUSTER})  # прочие — поля страницы
# «без панели» читается, «тип дома не «панельное»» — нет. Прочие значения —
# общей формой.
BUILDING_WORDS = {"панельное": "без панели", "каменное": "без камня",
                  "монолит": "без монолита"}


def _refuses(exclusion: Exclusion, listing: Listing, cluster_id: str | None,
             page: PageFields | None) -> bool:
    """Попадает ли объявление под исключение. Неизвестное — не попадает."""
    kind, value = exclusion.kind, exclusion.value
    if kind == CLUSTER:
        return value is not None and value in {cluster_id or listing.cluster_id, listing.id}
    if kind == FIRST_FLOOR:
        return listing.floor == 1
    if kind == LAST_FLOOR:
        return listing.floor is not None and listing.floor == listing.floors_total
    if kind == DISTRICT:
        return value is not None and listing.district == value
    # Поле страницы: страница не открыта или поля на ней нет — не отказ.
    if page is None or value is None:
        return False
    known = page.values.get(kind)
    return known is not None and str(known).strip().lower() == value.strip().lower()


def refusal_words(refused: Sequence[Exclusion]) -> list[str]:
    """Исключения заявки словами для шапки: «без 1-го этажа», «не Арабкир».

    Отвергнутые квартиры не перечисляются: их список ничего не говорит
    о том, чего клиент не хочет вообще. Повторы схлопываются.
    """
    words: list[str] = []
    for exclusion in refused:
        kind, value = exclusion.kind, exclusion.value
        if kind == CLUSTER:
            continue
        if kind == FIRST_FLOOR:
            word = "без 1-го этажа"
        elif kind == LAST_FLOOR:
            word = "без последнего этажа"
        elif kind == DISTRICT:
            word = f"не {value}"
        elif kind == "building_type" and str(value).lower() in BUILDING_WORDS:
            word = BUILDING_WORDS[str(value).lower()]
        else:
            word = f"{field_label(kind)} не «{value}»"
        if word not in words:
            words.append(word)
    return words


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


def _district(request: Request, listing: Listing, secondary: float) -> float | None:
    if listing.district is None:
        return None
    if request.districts_priority and listing.district in request.districts_priority:
        return 1.0
    if not request.districts and not request.districts_priority:
        return None                            # клиент район не назвал — нечего сравнивать
    if listing.district not in request.districts:
        return 0.0
    # Приоритета нет — названные районы клиенту равны, «второго сорта» среди
    # них нет. Доля непервоочередного здесь обнулила бы район всей заявке.
    return secondary if request.districts_priority else 1.0


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


def _wishes(nice: Sequence[Wish], page: PageFields | None) -> float | None:
    """Доля выполненных пожеланий среди тех, чьё поле известно."""
    known = [answer for answer in (wish.check(page) for wish in nice)
             if answer is not None]
    if not known:
        return None
    return sum(1.0 for answer in known if answer) / len(known)


def best_case(result: Score, weights: dict[str, float] | None,
              nice: Sequence[Wish]) -> int:
    """Лучший балл, до которого вариант дорастёт, когда откроется страница.

    Грубый балл считается без страницы: фактор `wishes` из знаменателя
    ушёл. Лучший случай — все пожелания `nice` выполнены (доля 1). Так
    воронка решает, стоит ли тратить на страницу одно из немногих открытий.
    """
    weights = DEFAULT_WEIGHTS if weights is None else weights
    got = sum(points for points, _ in result.breakdown.values())
    total = sum(weight for _, weight in result.breakdown.values())
    extra = float(weights.get("wishes", 0)) if nice else 0.0
    if not total + extra:
        return result.value
    return round(100 * (got + extra) / (total + extra))


def score(request: Request, listing: Listing, *,
          median_by_district: dict[str, float] | None = None,
          weights: dict[str, float] | None = None,
          stretch_percent: float = DEFAULT_STRETCH_PERCENT,
          page: PageFields | None = None,
          must: Sequence[Wish] = (),
          nice: Sequence[Wish] = (),
          secondary_district: float = DEFAULT_SECONDARY_DISTRICT,
          refused: Sequence[Exclusion] = (),
          cluster_id: str | None = None) -> Score:
    """Балл объявления по заявке: 0–100 и разбор, из чего он сложился.

    `secondary_district` — доля фактора `district` у района из списка заявки,
    но не из приоритетных (`match.secondary_district`). `refused` и
    `cluster_id` — см. `rejection`.
    """
    reason = rejection(request, listing, stretch_percent, page=page, must=must,
                       refused=refused, cluster_id=cluster_id)
    if reason is not None:
        return Score(value=0, breakdown={}, rejected_by=reason)

    weights = DEFAULT_WEIGHTS if weights is None else weights
    shares = {
        "budget": lambda: _budget(request, listing, stretch_percent),
        "district": lambda: _district(request, listing, secondary_district),
        "price_per_sqm": lambda: _price_per_sqm(listing, median_by_district),
        "area_rooms": lambda: _area_rooms(request, listing),
        "floor": lambda: _floor(request, listing),
        "seller_type": lambda: _seller_type(listing),
        "wishes": lambda: _wishes(nice, page),
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
