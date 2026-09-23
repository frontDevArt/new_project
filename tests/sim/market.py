"""Рынок имитации: объявления, которые живут своей жизнью по часам.

Всё случайное — из одного `random.Random(seed)` и в одном порядке (живые
объявления сортируются по id перед выборкой): одно зерно — одна неделя.
Правда о каждом событии пишется в `truth` — по ней отчёт узнаёт, что
брокер должен был увидеть.
"""
from __future__ import annotations

import contextlib
import math
import random
import sqlite3
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

# Район → $/м² середины рынка: синтетический рынок похож на ленту по разбросу.
DISTRICTS = {
    "Кентрон": 2600, "Арабкир": 2100, "Давташен": 1600, "Канакер-Зейтун": 1400,
    "Нор Норк": 1200, "Аван": 1100, "Малатия-Себастия": 1100, "Шенгавит": 1150,
}
AMD_PER_USD = 363.25


@dataclass(frozen=True)
class MarketParams:
    new_per_hour: float = 20.0
    relist_share: float = 0.2      # доля перевыставлений среди новых
    cheaper_per_day: float = 0.01  # доля живых, подешевевших за сутки
    gone_per_day: float = 0.007
    bump_per_day: float = 0.02     # поднятые наверх ленты
    cheaper_min: float = 0.03
    cheaper_max: float = 0.10


@dataclass
class Flat:
    id: str
    flat_key: str                  # одна квартира — один ключ; перевыставление его наследует
    title: str
    district: str
    currency: str                  # USD | AMD
    price: int                     # в валюте объявления
    area: float
    rooms: int
    floor: int
    floors_total: int
    agency: bool
    verified: bool | None
    new_build: bool
    posted_at: datetime
    bumped_at: datetime
    page: dict[str, str] = field(default_factory=dict)
    status: str = "active"         # active | gone

    def usd(self, amd_per_usd: float) -> float:
        return float(self.price) if self.currency == "USD" else self.price / amd_per_usd


@dataclass(frozen=True)
class TruthEvent:
    kind: str                      # new | relist | cheaper | gone | bump
    flat_id: str
    at: datetime
    old_price: int | None = None
    new_price: int | None = None


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 500:
        return max(0, round(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _round_price(value: float, currency: str) -> int:
    return int(round(value, -2 if currency == "USD" else -4))


def _title(rooms: int, district: str, area: float, floor: int, floors_total: int) -> str:
    return f"{rooms}-комн. квартира в районе {district}, {area:g} кв.м., {floor}/{floors_total} этаж"


def _page_fields(rng: random.Random, floors_total: int, new_build: bool) -> dict[str, str]:
    """Поля страницы объявления — закреплены за квартирой, как на сайте."""
    return {
        "Ремонт": rng.choices(["без ремонта", "косметический", "евроремонт", "дизайнерский"],
                              [25, 35, 30, 10])[0],
        "Тип здания": rng.choices(["панельное", "каменное", "монолит"], [35, 35, 30])[0],
        "Балкон": rng.choices(["открытый", "закрытый", "нет"], [40, 35, 25])[0],
        "Лифт": "есть" if floors_total > 5 and rng.random() < 0.9 else "нет",
        "Высота потолков": rng.choice(["2.7 м", "2.8 м", "3 м"]),
        "Новостройка": "да" if new_build else "нет",
    }


SNAPSHOT_SQL = """
SELECT id, title, district, currency, price_amount, area, rooms, floor, floors_total,
       seller_type, verified, new_build
FROM listings WHERE status = 'active'
"""


class Market:
    def __init__(self, flats: list[Flat], *, seed: int, params: MarketParams, now: datetime,
                 skipped: int = 0):
        if not flats:
            raise ValueError("рынок имитации пуст")
        self.rng = random.Random(seed)
        self.params = params
        self.now = now
        self.flats: dict[str, Flat] = {flat.id: flat for flat in flats}
        self.truth: list[TruthEvent] = []
        self.next_id = max(int(flat_id) for flat_id in self.flats) + 1
        self.start_active = len(flats)
        self.skipped = skipped
        self._version = 0
        self._feed: list[Flat] = []
        self._feed_version = -1

    # ------------------------------------------------------------ откуда рынок

    @classmethod
    def synthetic(cls, count: int, *, seed: int = 1, params: MarketParams = MarketParams(),
                  now: datetime) -> "Market":
        rng = random.Random(seed * 7919)
        flats = []
        for index in range(count):
            district = rng.choice(sorted(DISTRICTS))
            rooms = rng.choice([1, 2, 2, 3, 3, 3, 4, 5])
            area = float(round(rng.uniform(22, 30) * rooms + rng.uniform(10, 25)))
            floors_total = rng.choice([5, 9, 12, 14, 16])
            floor = rng.randint(1, floors_total)
            new_build = rng.random() < 0.3
            price_usd = area * DISTRICTS[district] * rng.uniform(0.55, 1.35)
            currency = "AMD" if rng.random() < 0.1 else "USD"
            price = _round_price(price_usd * (AMD_PER_USD if currency == "AMD" else 1), currency)
            flat_id = str(24_000_000 + index)
            posted = now - timedelta(minutes=10 * (count - index))
            flats.append(Flat(
                id=flat_id, flat_key=flat_id,
                title=_title(rooms, district, area, floor, floors_total),
                district=district, currency=currency, price=price, area=area, rooms=rooms,
                floor=floor, floors_total=floors_total, agency=rng.random() < 0.3,
                verified=rng.choice([True, False, None]), new_build=new_build,
                posted_at=posted, bumped_at=posted,
                page=_page_fields(rng, floors_total, new_build)))
        return cls(flats, seed=seed, params=params, now=now)

    @classmethod
    def from_snapshot(cls, path: str | Path, *, seed: int = 1,
                      params: MarketParams = MarketParams(), now: datetime) -> "Market":
        uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True)) as db:
            rows = db.execute(SNAPSHOT_SQL).fetchall()
        rng = random.Random(seed * 7919)
        usable = [row for row in rows
                  if row[3] in ("USD", "AMD") and all(row[i] is not None for i in (2, 4, 5, 6, 7, 8))]
        usable.sort(key=lambda row: int(row[0]))
        flats = []
        for rank, row in enumerate(usable):
            (flat_id, title, district, currency, amount, area, rooms, floor, floors_total,
             seller, verified, new_build) = row
            posted = now - timedelta(minutes=len(usable) - rank)
            flats.append(Flat(
                id=str(flat_id), flat_key=str(flat_id),
                title=title or _title(rooms, district, area, floor, floors_total),
                district=district, currency=currency, price=int(amount), area=float(area),
                rooms=int(rooms), floor=int(floor), floors_total=int(floors_total),
                agency=seller == "agency",
                verified=None if verified is None else bool(verified),
                new_build=bool(new_build), posted_at=posted, bumped_at=posted,
                page=_page_fields(rng, int(floors_total), bool(new_build))))
        return cls(flats, seed=seed, params=params, now=now, skipped=len(rows) - len(usable))

    # ------------------------------------------------------------ что видно

    def feed(self) -> list[Flat]:
        if self._feed_version != self._version:
            live = [flat for flat in self.flats.values() if flat.status == "active"]
            live.sort(key=lambda flat: (flat.bumped_at, int(flat.id)), reverse=True)
            self._feed, self._feed_version = live, self._version
        return self._feed

    def get(self, flat_id: str) -> Flat | None:
        return self.flats.get(str(flat_id))

    # ------------------------------------------------------------ ход времени

    def advance_to(self, moment: datetime) -> None:
        while self.now + timedelta(hours=1) <= moment:
            self._hour(self.now + timedelta(hours=1))

    def _hour(self, at: datetime) -> None:
        rng, p = self.rng, self.params
        live = sorted((f for f in self.flats.values() if f.status == "active"),
                      key=lambda flat: int(flat.id))
        for _ in range(_poisson(rng, p.new_per_hour)):
            self._born(rng.choice(live), at, relist=rng.random() < p.relist_share)
        for flat in self._sample(live, len(live) * p.cheaper_per_day / 24):
            old = flat.price
            flat.price = _round_price(old * (1 - rng.uniform(p.cheaper_min, p.cheaper_max)),
                                      flat.currency)
            self.truth.append(TruthEvent("cheaper", flat.id, at, old, flat.price))
        for flat in self._sample(live, len(live) * p.gone_per_day / 24):
            if flat.status == "active":
                flat.status = "gone"
                self.truth.append(TruthEvent("gone", flat.id, at))
        for flat in self._sample(live, len(live) * p.bump_per_day / 24):
            if flat.status == "active":
                flat.bumped_at = at - timedelta(minutes=rng.uniform(0, 59))
                self.truth.append(TruthEvent("bump", flat.id, at))
        self.now = at
        self._version += 1

    def _sample(self, items: list[Flat], rate: float) -> list[Flat]:
        return self.rng.sample(items, min(len(items), _poisson(self.rng, rate)))

    def _born(self, base: Flat, at: datetime, *, relist: bool) -> None:
        rng = self.rng
        flat_id = str(self.next_id)
        self.next_id += 1
        posted = at - timedelta(minutes=rng.uniform(0, 59))
        if relist:
            child = replace(
                base, id=flat_id, area=base.area + rng.choice([-1, 0, 1]),
                price=_round_price(base.price * rng.uniform(0.97, 1.03), base.currency),
                agency=rng.random() < 0.6, posted_at=posted, bumped_at=posted,
                page=dict(base.page), status="active")
            kind = "relist"
        else:
            area = float(round(base.area * rng.uniform(0.95, 1.05)))
            floor = rng.randint(1, base.floors_total)
            child = replace(
                base, id=flat_id, flat_key=flat_id, area=area, floor=floor,
                title=_title(base.rooms, base.district, area, floor, base.floors_total),
                price=_round_price(base.price * rng.uniform(0.9, 1.1), base.currency),
                agency=rng.random() < 0.3, posted_at=posted, bumped_at=posted,
                page=_page_fields(rng, base.floors_total, base.new_build), status="active")
            kind = "new"
        self.flats[flat_id] = child
        self.truth.append(TruthEvent(kind, flat_id, posted))
