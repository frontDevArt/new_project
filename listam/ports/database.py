"""Порт Database: хранение объявлений, истории цен, заявок и журнала прогонов."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from listam.domain.models import Listing, Match, Notification, PricePoint, Request, Run


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
        """Возвращает 'new' | 'returned' | 'price_changed' | 'updated' | 'unchanged'.

        `returned` — строка лежала снятой и снова встретилась на ленте. Это
        событие рынка, а не правка поля: оно крупнее смены цены и заголовка,
        поэтому перекрывает их. Точка истории цен при этом ставится как обычно.

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
    def listings_returned_since(self, since: datetime) -> list[Listing]:
        """Объявления, вернувшиеся на ленту после отметки.

        Дата возврата живёт в своей колонке: `gone_at` у вернувшегося обнулён,
        `first_seen` не двигался, а `last_seen` прогон двигает всей ленте
        разом — по нему возврат от обычной встречи не отличить.
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
    def set_cluster_ids(self, mapping: dict[str, str]) -> int:
        """Проставляет кластеры и отдаёт, сколько строк на самом деле изменилось.

        Считаются изменённые, а не переданные: пересчёт по неизменившейся базе
        обязан отвечать «изменено 0», иначе идемпотентность нечем проверить.
        """

    @abstractmethod
    def listings_for_matching(self, since: datetime | None = None) -> list[Listing]:
        """Объявления, которые вообще могут стать матчем.

        Снятые и помеченные аномалией выпадают: по снятому звонить некуда,
        а аномалия — это цена, которой мы сами не верим, и предлагать её
        клиенту нельзя. `since` сужает выборку до появившихся после отметки
        (мерка — `first_seen`), это и есть `match --new`.
        """

    @abstractmethod
    def listings_touched_since(self, since: datetime) -> list[Listing]:
        """Активные без аномалии, которых с отметки что-то коснулось.

        Коснулось — это появилось, сменило цену или вернулось на ленту.
        Мерить одним `first_seen`, как `listings_for_matching(since=...)`,
        мало: подешевевшая квартира новой не становится, а подбор её ждёт.
        """

    @abstractmethod
    def mark_requests_matched(self, request_ids: list[int], now: datetime) -> None:
        """Отмечает, что по этим заявкам только что шёл подбор.

        Отметка нужна выборке `--new`: заявку, которую тронули после её
        последнего подбора, выборка объявлений не покрывает — изменился
        не рынок, а условия, и такую заявку надо вести по всей базе.
        """

    @abstractmethod
    def upsert_request(self, request: Request, now: datetime) -> str:
        """Возвращает 'new' | 'updated' | 'unchanged'.

        `unchanged` — источник перечитан, а заявка та же. Отметка правки
        при этом **не двигается**: иначе чтение таблицы раз в час выглядело
        бы как правка всех пятидесяти заявок разом, и «что изменилось
        со вчера» перестало бы отвечать на вопрос.

        Дата рождения ставится один раз, при вставке: правка бюджета
        не делает заявку новой.
        """

    @abstractmethod
    def iter_requests(self, status: str | None = "active"):
        """Заявки в порядке внешнего идентификатора; `status=None` — все.

        По умолчанию только `active`: матчатся они, а `paused` и `closed`
        лежат в базе ради истории звонков, а не ради новых матчей.
        """

    @abstractmethod
    def get_request(self, external_id: str) -> Request | None:
        """Заявка по внешнему идентификатору; незнакомая — None, а не ошибка."""

    @abstractmethod
    def close_requests_missing_from(self, external_ids: set[str],
                                    now: datetime) -> int:
        """Закрывает активные заявки, которых нет в этом списке. Отдаёт, сколько закрыл.

        Брокер удалил строку из таблицы — значит клиент ушёл. Удалять заявку
        нельзя: на ней висят матчи и след звонков. Трогаются только активные:
        `paused` и `closed` — решение человека, а не источника.
        """

    @abstractmethod
    def upsert_match(self, match: Match, now: datetime) -> str:
        """Возвращает 'new' | 'updated' | 'unchanged'.

        Пересчёт трогает только вычисленное: балл, его разбор и снимок
        кластера. `status` и `reject_reason` — это след звонка, а не
        вычисленное значение (решение 7), и затереть их пересчётом нельзя.

        `unchanged` — балл и кластер те же. Отметка пересчёта при этом
        **не двигается**: `matched_at` отвечает на вопрос «когда это в
        последний раз стало другим», а не «когда мы последний раз считали».

        Дата рождения матча ставится один раз, при вставке: подорожавшая
        квартира не становится новой находкой.
        """

    @abstractmethod
    def upsert_matches(self, matches: list[Match], now: datetime) -> dict[str, int]:
        """Пачка матчей одной транзакцией. Отдаёт счётчики new/updated/unchanged.

        Правила те же, что у `upsert_match`: след звонка не трогается,
        `matched_at` не двигается у неизменившихся, закрытие гасится
        подтверждением. Отличие одно — граница транзакции: на боевых числах
        одна транзакция на строку стоит минуту на прогон.
        """

    @abstractmethod
    def retire_matches(self, request_id: int, keep: set[str],
                       now: datetime, reason: str) -> int:
        """Закрывает матчи заявки, которых нет в `keep`. Отдаёт, сколько закрыл.

        Закрытие — не удаление: в строке лежит след звонка, и он переживает
        подорожавшее объявление. Уже закрытые повторно не трогаются, иначе
        `retired_at` двигался бы каждым прогоном и переставал отвечать на
        вопрос «когда вариант отпал».
        """

    @abstractmethod
    def matches_for_request(self, request_id: int, min_score: float | None = None,
                            limit: int | None = None,
                            include_retired: bool = False) -> list[Match]:
        """Матчи заявки от лучшего к худшему; `min_score` и `limit` сужают список.

        По умолчанию отдаются только живые: закрытый матч — это вариант,
        который больше не подходит, и в витрине ему не место. `include_retired`
        нужен тому, кто разбирается, куда делся вчерашний вариант.

        Матч на снятое объявление отсюда не выпадает (решение 8): «мы звонили
        по этой квартире» переживает уход объявления с ленты, а пометить
        снятое — дело витрины.
        """

    @abstractmethod
    def matches_with_listings(self, request_id: int, min_score: float | None = None,
                              limit: int | None = None
                              ) -> list[tuple[Match, Listing]]:
        """Живые матчи заявки вместе с объявлениями, одним запросом.

        Снятое объявление приходит с пометкой, а не выпадает (решение 8):
        «мы звонили по этой квартире» не исчезает вместе с карточкой.
        """

    @abstractmethod
    def match_events_since(self, since: datetime, until: datetime,
                           request_id: int | None = None
                           ) -> list[tuple[Match, Listing, float | None]]:
        """Матчи, которых коснулось окно `(since, until]`, с карточкой и старой ценой.

        Коснулось — это любая из четырёх отметок: матч появился
        (`first_matched_at`), пересчитался (`matched_at`), закрылся
        (`retired_at`) или вернулся (`revived_at`). Что из этого считать
        событием и как назвать, решает домен (`listam/domain/events.py`):
        база отдаёт сырьё, а не приговор.

        Третье значение строки — последняя цена объявления **до** окна.
        Без неё «подешевело с 225 000 до 150 000» не написать, а балл
        на этот вопрос не отвечает: его двигают и пересчёт кластера,
        и смена медианы района.

        Закрытые матчи из выборки не выпадают: дайджест обязан сказать,
        почему вчерашняя карточка пропала.
        """

    @abstractmethod
    def set_match_status(self, match_id: int, status: str,
                         reject_reason: str | None = None) -> None:
        """След звонка: `new` | `sent` | `called` | `rejected` и причина отказа.

        Единственный способ тронуть эти две колонки. Пересчёт их не пишет
        вовсе, поэтому проставленное человеком не зависит от того, когда
        в следующий раз посчитают баллы.
        """

    @abstractmethod
    def count_matches(self, request_id: int | None = None) -> int:
        """Сколько матчей в базе; `request_id` сужает до одной заявки."""

    @abstractmethod
    def count_matches_alive(self, request_id: int, min_score: float | None = None) -> int:
        """Сколько живых матчей заявки выше порога — счётом, без чтения строк.

        Считает ровно то же, что отдала бы `matches_with_listings` без потолка:
        живые, с карточкой в базе, от порога и выше. Иначе «…и ещё 704»
        обещало бы человеку строки, которых он не получит.
        """

    @abstractmethod
    def record_notification(self, notification: Notification) -> int:
        """Записывает успешную отправку и отдаёт её идентификатор.

        Строка пишется **только после того, как сообщение ушло**: окно
        следующего запуска считается от неё, и запись до отправки означала бы
        потерянное событие при первом же отказе сети.
        """

    @abstractmethod
    def last_notification(self, kind: str) -> Notification | None:
        """Последняя отправка этого вида; не было ни одной — None.

        Виды не смешиваются: часовое «горячее» не двигает окно дневного
        дайджеста, иначе вечерняя сводка показывала бы последний час.
        """

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
    def crawl_to_resume(self) -> Run | None:
        """Прерванный обход по полной ленте — тот, который продолжает `--resume`.

        Обход — это не одна строка журнала: полный прогон и продолжающие его
        `resume` идут по одной и той же ленте. Мерка — самая дальняя пройденная
        страница среди них, а не последняя строка: иначе второе продолжение
        выбрасывает всё, что прошло первое.

        Отдаётся строка последнего прогона этого обхода — по ней видно, закрыт
        он или оборвался, — но с `last_page`, догнанным до самой дальней
        страницы. `fresh` в обход не входит: он ходит по голове ленты.
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
