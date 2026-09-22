"""Дедуп: одна квартира, выставленная несколькими агентствами, — один кластер.

Клиенту нельзя звонить дважды про одну и ту же квартиру, это выглядит как
непрофессионализм. Но и склеить две разные квартиры нельзя: склейка прячет
от клиента вариант, о котором он никогда не узнает. Отсюда два правила:
ключ строгий (решение 4 — без улицы объявление остаётся само по себе),
а допуск по площади — объединение соседей, а не бакет (решение 5).

Модуль чистый: ни базы, ни сети. На вход — объявления, на выход — кластеры.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable

from listam.domain.models import Listing

DEFAULT_AREA_TOLERANCE = 2.0

# «ул.», «улица», «փող.» — это не часть названия, а способ его записать.
# Сокращение снимается только когда оно и есть отдельное слово: с точкой или
# с пробелом следом. Иначе `ул` съедает начало настоящего имени — «Улучшенная»
# превращается в «ица», и две разные улицы сходятся в одну.
STREET_NOISE = re.compile(
    r"^(?:(?:улица|проспект|փողոց)(?=\s)"
    r"|(?:ул|пр|փող)\.\s*"
    r"|(?:ул|пр|փող)(?=\s))\s*",
    re.IGNORECASE,
)
SPACES = re.compile(r"\s+")


def normalize_street(street: str | None) -> str | None:
    """Улица в виде, в котором её можно сравнивать: без украшений и регистра."""
    if not street:
        return None
    cleaned = SPACES.sub(" ", str(street).strip())
    cleaned = STREET_NOISE.sub("", cleaned).strip()
    return cleaned.casefold() or None


@dataclass
class Cluster:
    """Группа объявлений про одну квартиру.

    `listing_ids` отсортированы, первый — самый дешёвый: именно он показывается
    клиенту и именно он ложится в `matches` представителем кластера.
    """

    cluster_id: str
    listing_ids: list[str]
    cheapest_id: str
    size: int
    spread_usd: float | None      # max(price_usd) - min(price_usd); один член — None


def _key(listing: Listing) -> tuple | None:
    """Ключ группы до учёта площади. None — объявление не кластеризуется."""
    street = normalize_street(listing.street)
    if not (listing.district and street and listing.area):
        return None
    return (listing.district.strip().casefold(), street,
            listing.rooms, listing.floor, listing.floors_total)


def _cluster_id(key: tuple, low: float, high: float) -> str:
    raw = "|".join(str(part) for part in key) + f"|{low:.1f}-{high:.1f}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _groups(listings: list[Listing],
            area_tolerance: float) -> list[tuple[tuple, list[Listing]]]:
    buckets: dict[tuple, list[Listing]] = {}
    lonely: list[tuple[tuple, list[Listing]]] = []
    for listing in listings:
        key = _key(listing)
        if key is None:
            lonely.append((("одиночка", listing.id), [listing]))
            continue
        buckets.setdefault(key, []).append(listing)

    groups: list[tuple[tuple, list[Listing]]] = []
    for key, members in buckets.items():
        # Сортировка по площади, затем по id: порядок чтения базы не должен
        # влиять ни на состав кластеров, ни на их идентификаторы.
        members.sort(key=lambda item: (item.area, item.id))
        chain = [members[0]]
        for listing in members[1:]:
            # Мерка — от первого члена цепочки, а не от предыдущего. Иначе
            # цепочка разгоняется: 60→62→64→66 при допуске 2 даёт кластер
            # шириной 6 м², в котором три квартиры из четырёх клиент никогда
            # не увидит — показывается только самая дешёвая.
            if listing.area - chain[0].area <= area_tolerance:
                chain.append(listing)
                continue
            groups.append((key, chain))
            chain = [listing]
        groups.append((key, chain))
    return groups + lonely


def clusters(listings: Iterable[Listing],
             area_tolerance: float = DEFAULT_AREA_TOLERANCE) -> list[Cluster]:
    """Кластеры по выборке объявлений. Порядок выборки на результат не влияет."""
    found: list[Cluster] = []
    for key, members in _groups(list(listings), area_tolerance):
        areas = [item.area for item in members if item.area is not None] or [0.0]
        cluster_id = _cluster_id(key, min(areas), max(areas))
        prices = [item.price_usd for item in members if item.price_usd is not None]
        # Объявление без цены в хвосте: «самый дешёвый» — это цена, а не её
        # отсутствие, и показывать карточку без цены лучшим вариантом нельзя.
        ordered = sorted(members, key=lambda item: (item.price_usd is None,
                                                    item.price_usd or 0.0, item.id))
        found.append(Cluster(
            cluster_id=cluster_id,
            listing_ids=[item.id for item in ordered],
            cheapest_id=ordered[0].id,
            size=len(members),
            spread_usd=round(max(prices) - min(prices), 2) if len(prices) > 1 else None,
        ))
    return found


def assign(listings: Iterable[Listing],
           area_tolerance: float = DEFAULT_AREA_TOLERANCE) -> dict[str, str]:
    """Объявление → идентификатор его кластера."""
    return {
        listing_id: cluster.cluster_id
        for cluster in clusters(listings, area_tolerance)
        for listing_id in cluster.listing_ids
    }
