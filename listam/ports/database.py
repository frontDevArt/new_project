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
    def active_ids(self) -> set[str]:
        """Идентификаторы объявлений, которые сейчас на ленте (`status='active'`).

        Снятые из выборки выпадают: пометка снятых сравнивает ленту с активными,
        а не со всем, что база когда-либо видела.
        """

    @abstractmethod
    def mark_gone(self, listing_ids, gone_at: datetime) -> int:
        """Помечает объявления снятыми и отдаёт, сколько их оказалось.

        Дата снятия ставится один раз: уже снятое объявление второй обход
        не трогает. Дата встречи (`last_seen`) остаётся на месте — снятие
        это не встреча.
        """

    @abstractmethod
    def listings_first_seen_since(self, since: datetime) -> list[Listing]:
        """Объявления, впервые увиденные после отметки: это и есть «новое».

        Мерка — `first_seen`, а не `last_seen`: карточка, которую обход
        встретил сегодня в сотый раз, новой не стала.
        """

    @abstractmethod
    def listings_gone_since(self, since: datetime) -> list[Listing]:
        """Объявления, помеченные снятыми после отметки.

        Дата снятия живёт в своей колонке и ставится один раз, поэтому
        «снято за последний прогон» спрашивается именно у неё.
        """

    @abstractmethod
    def price_changes_since(
        self, since: datetime
    ) -> list[tuple[Listing, float | None, float | None]]:
        """(объявление, цена на начало окна, цена на конец окна) — по одной строке.

        Одна карточка — одна строка, сколько бы раз она ни двигала цену за окно:
        промежуточные точки схлопываются, и тогда счётчик «Сменили цену» считает
        объявления, а не точки истории.

        Объявление, у которого до окна цены не было, не показывается: оно внутри
        окна и появилось — это «Новое», а не смена цены.
        """

    @abstractmethod
    def price_history(self, listing_id: str) -> list[PricePoint]: ...

    @abstractmethod
    def start_run(self, started_at: datetime, rate_amd_per_usd: float | None,
                  mode: str = "full") -> int:
        """Открывает строку журнала. `mode` — full | partial | resume | fresh.

        Режим пишется сразу, на старте: прогон, оборвавшийся посередине, всё
        равно должен быть отличим от полного.
        """

    @abstractmethod
    def finish_run(self, run_id: int, finished_at: datetime, **counters) -> None: ...

    @abstractmethod
    def mark_page(self, run_id: int, page: int) -> None:
        """Запоминает номер пройденной страницы: с неё продолжит `scrape --resume`."""

    @abstractmethod
    def last_run(self, mode: str | None = None) -> Run | None:
        """Последний прогон журнала; `mode` сужает выборку до одного режима.

        `--resume` продолжает прерванный полный обход, а не инкрементальный,
        который прошёл между ними, — поэтому спрашивать умеет про режим.
        """

    @abstractmethod
    def last_successful_run(self) -> Run | None:
        """Последний **полный** прогон, дошедший до конца без ошибок.

        Мерка полноты для следующего обхода. Укороченный, продолженный и
        инкрементальный прогоны видели не всю ленту: мерить их числом страниц
        полноту следующего — значит выключить проверку недобора.
        """

    @abstractmethod
    def last_run_that_could_mark_gone(self) -> Run | None:
        """Последний завершённый полный обход: только он ставит `status='gone'`.

        Мерка для раздела «Снято» в `changes`. Инкрементальный прогон, прошедший
        между ними, снятых не ставил, и двигать им окно — значит стереть раздел.

        Ошибка прогона мерку не отменяет, поэтому `last_successful_run()` здесь
        не годится: обход мог дойти до конца, пометить снятых и споткнуться
        уже при заливке базы в хранилище.
        """
