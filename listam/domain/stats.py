"""Сводки по базе.

Одно правило на все агрегаты: помеченное аномалией в них не участвует.
Медиана $/м² по району, посчитанная вместе с объявлением за 1 060 975 $/м²,
это не медиана рынка, а медиана чужой опечатки.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Iterable

from listam.domain.models import Listing


def clean(listings: Iterable[Listing]) -> list[Listing]:
    """Только то, что прошло проверку: без метки `anomaly`."""
    return [item for item in listings if not item.anomaly]


def median_price_per_sqm_by_district(listings: Iterable[Listing]) -> dict[str, float]:
    """Медиана цены за метр по районам. Районы без чистых объявлений не попадают."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for item in clean(listings):
        if item.district and item.price_per_sqm is not None:
            buckets[item.district].append(item.price_per_sqm)
    return {
        district: round(statistics.median(values), 2)
        for district, values in buckets.items()
        if values
    }
