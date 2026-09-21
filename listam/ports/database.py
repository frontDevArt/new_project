"""Порт Database: хранение объявлений, истории цен и журнала прогонов."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from listam.domain.models import Listing, PricePoint, Run


class Database(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def migrate(self) -> None:
        """Приводит схему к последней версии. Только версионированными миграциями."""

    @abstractmethod
    def schema_version(self) -> int: ...

    @abstractmethod
    def table_names(self) -> set[str]: ...

    @abstractmethod
    def upsert_listing(self, listing: Listing, seen_at: datetime) -> str:
        """Возвращает 'new' | 'price_changed' | 'updated' | 'unchanged'."""

    @abstractmethod
    def get_listing(self, listing_id: str) -> Listing | None: ...

    @abstractmethod
    def iter_listings(self): ...

    @abstractmethod
    def known_ids(self) -> set[str]: ...

    @abstractmethod
    def price_history(self, listing_id: str) -> list[PricePoint]: ...

    @abstractmethod
    def start_run(self, started_at: datetime, rate_amd_per_usd: float | None) -> int: ...

    @abstractmethod
    def finish_run(self, run_id: int, finished_at: datetime, **counters) -> None: ...

    @abstractmethod
    def last_run(self) -> Run | None: ...
