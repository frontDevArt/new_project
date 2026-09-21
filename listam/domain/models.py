"""Доменные сущности. Ядро системы — заявка; на M0 наполняется только объявление."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime


@dataclass
class Listing:
    """Объявление о продаже квартиры — снимок карточки из ленты."""

    id: str
    url: str
    title: str | None = None
    district: str | None = None
    street: str | None = None
    price_raw: str | None = None
    currency: str | None = None
    price_usd: float | None = None
    price_amd: float | None = None
    area: float | None = None
    rooms: int | None = None
    floor: int | None = None
    floors_total: int | None = None
    price_per_sqm: float | None = None
    seller_type: str | None = None        # owner | agency | None
    verified: bool | None = None
    new_build: bool | None = None
    cluster_id: str | None = None         # заполняется на M2 (дедуп)
    status: str = "active"                # active | gone
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]


@dataclass
class PricePoint:
    listing_id: str
    seen_at: datetime
    price_usd: float | None


@dataclass
class Run:
    """Журнал прогона: курс фиксируем, чтобы старые цифры воспроизводились."""

    id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rate_amd_per_usd: float | None = None
    pages_fetched: int = 0
    listings_seen: int = 0
    new_listings: int = 0
    updated_listings: int = 0
    errors: int = 0
    notes: str | None = None
