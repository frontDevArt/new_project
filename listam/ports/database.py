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
    def snapshot(self, target) -> None:
        """Целая копия базы в отдельный файл — с тем, что ещё не в основном файле."""

    @abstractmethod
    def migrate(self) -> None:
        """Приводит схему к последней версии. Только версионированными миграциями."""

    @abstractmethod
    def schema_version(self) -> int: ...

    @abstractmethod
    def table_names(self) -> set[str]: ...

    @abstractmethod
    def upsert_listing(
        self, listing: Listing, seen_at: datetime, rate_amd_per_usd: float | None = None
    ) -> str:
        """Возвращает 'new' | 'price_changed' | 'updated' | 'unchanged'.

        `rate_amd_per_usd` — курс прогона, он уходит в точку истории цен.
        """

    @abstractmethod
    def transaction(self):
        """Контекст, внутри которого записи применяются целиком или никак."""

    @abstractmethod
    def set_computed(
        self, listing_id: str, *, anomaly: str | None, price_amount: float | None
    ) -> None:
        """Переписывает посчитанное по уже записанному: пометку и сумму в валюте.

        Ни цены, ни даты, ни истории это не касается: пересчёт не узнаёт ничего
        нового о карточке, он доводит строку до вида, который ожидает код.
        """

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
    def mark_page(self, run_id: int, page: int) -> None:
        """Запоминает номер пройденной страницы: с неё продолжит `scrape --resume`."""

    @abstractmethod
    def last_run(self) -> Run | None: ...

    @abstractmethod
    def last_successful_run(self) -> Run | None:
        """Последний завершённый прогон без ошибок. Мерка полноты для следующего."""
