"""Прогон по ленте: страницы → объявления → база.

Один проход устроен так: сначала фиксируется курс (он уходит в журнал, чтобы
старые цифры воспроизводились), потом страницы идут одна за другой по путевой
пагинации, и каждая карточка кладётся в базу апсертом.

Откуда берутся страницы, куда ложится база и какой курс — решает конфиг.
Здесь про это ничего не известно: только порты.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from listam.adapters.db_sqlite import from_iso
from listam.adapters.filenames import drop_wal_sidecars
from listam.adapters.run_lock import LockBusy
from listam.config import Config, threshold
from listam.domain.coverage import Coverage, rules_from as coverage_rules_from
from listam.domain.models import Listing, Run
from listam.domain.money import Money
from listam.domain.validate import detect_anomalies, rules_from
from listam.parsers.category_list import (
    DEFAULT_BASE_URL,
    ListingContainerMissing,
    parse_listing_cards,
    parse_next_page,
)
from listam.ports.fetcher import FetchError
from listam.ports.rate import Rate, RateError
from listam.wiring import (
    build_database,
    build_fetcher,
    build_rate_provider,
    build_run_lock,
    build_storage,
    database_path,
)


DEFAULT_MIN_CARDS = 20        # здоровая страница ленты отдаёт под сотню карточек
DEFAULT_MAX_CARDS = 120       # больше сотни с лишним — это не лента, а склейка
DEFAULT_KEEP_BACKUPS = 5      # сколько прежних копий базы держим в хранилище
DEFAULT_MAX_SHRINK = 10       # на сколько процентов база может усохнуть без разрешения
DEFAULT_HARD_PAGE_LIMIT = 1000  # потолок обхода: категория 60 — это 215 страниц


def page_path(category: int | str, page: int) -> str:
    """Пагинация только путевая: /category/60, /category/60/2.

    Query-параметры (`n=`, `price1=`, `srt=`, …) запрещены в robots.txt сайта.
    """
    return f"/category/{category}" if page <= 1 else f"/category/{category}/{page}"


def apply_rate(listing: Listing, rate_amd_per_usd: float | None) -> Listing:
    """Достраивает вторую валюту и цену за метр. Парсер этого не умеет: он не знает курса.

    Посчитать не вышло — оставляем как было. Пустой пересчёт означает «не смог»,
    а не «цены больше нет»: затирать им уже посчитанное нельзя.
    """
    amount = listing.price_usd if listing.currency == "USD" else listing.price_amd
    money = Money(raw=listing.price_raw, amount=amount, currency=listing.currency)
    recomputed_usd = money.to_usd(rate_amd_per_usd)
    recomputed_amd = money.to_amd(rate_amd_per_usd)
    # Явная проверка на None, а не `or`: ноль — это цена, и она не «не смог».
    listing.price_usd = recomputed_usd if recomputed_usd is not None else listing.price_usd
    listing.price_amd = recomputed_amd if recomputed_amd is not None else listing.price_amd
    if listing.price_usd is not None and listing.area:
        listing.price_per_sqm = round(listing.price_usd / listing.area, 2)
    return listing



def _latest_run_at(path) -> datetime | None:
    """Когда по этой копии базы последний раз ходил прогон. Нечитаемая копия — None."""
    if not Path(path).exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT MAX(started_at) AS at FROM runs").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return from_iso(row[0]) if row and row[0] else None


def _listings_in(path) -> int | None:
    if not Path(path).exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT COUNT(*) FROM listings").fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        return None
    return int(row[0]) if row else None


@dataclass
class _Remote:
    """Что мы знаем об удалённой копии базы к началу прогона."""

    note: str
    listings: int | None = None


def take_the_fresher_copy(storage, remote_name: str, local_db: Path) -> _Remote:
    """Сверяет локальную копию с удалённой и оставляет более свежую.

    Без этого вторая машина или откат папки data/ молча затирает общую базу
    старым снимком: заливка-то происходит всегда, а скачивание — только когда
    локального файла нет.
    """
    if not local_db.exists():
        if not storage.download(remote_name, local_db):
            return _Remote(note="в хранилище копии нет — база заводится заново")
        return _Remote(
            note="локальной копии не было — взята из хранилища",
            listings=_listings_in(local_db),
        )

    candidate = local_db.with_name(local_db.name + ".remote")
    try:
        if not storage.download(remote_name, candidate):
            return _Remote(note="в хранилище копии нет — идём с локальной")
        there, here = _latest_run_at(candidate), _latest_run_at(local_db)
        listings = _listings_in(candidate)
        if there is not None and (here is None or there > here):
            candidate.replace(local_db)
            drop_wal_sidecars(local_db)   # спутники относились к прежнему файлу
            return _Remote(
                note=f"удалённая копия свежее ({there:%Y-%m-%d %H:%M} UTC) — взята она",
                listings=listings,
            )
        return _Remote(note="локальная копия не старее удалённой", listings=listings)
    finally:
        candidate.unlink(missing_ok=True)


def shrink_refusal(
    before: int | None, after: int, max_shrink: float, allowed: bool
) -> str | None:
    """Заливка, срезающая базу больше порога, не выполняется без явного разрешения."""
    if allowed or not before or max_shrink <= 0:
        return None
    if after >= before * (1 - max_shrink):
        return None
    return (
        f"заливка отменена: база сжалась с {before} объявлений до {after}, "
        f"это больше порога storage.max_shrink_percent = {max_shrink * 100:.0f}%. "
        f"Если так и задумано — scrape --allow-shrink"
    )


DEFAULT_MAX_PAGES_DROP = 20   # на столько процентов обход может быть короче прошлого


def pages_shortfall(
    pages_fetched: int,
    expected_min: int | None,
    previous_pages: int | None,
    drop_percent: float | None,
) -> str | None:
    """Недобор страниц: обход кончился заметно раньше, чем должен был.

    Лента категории — это сотни страниц. Обход, вставший на второй из-за
    пропавшего пагинатора, записывает проценты рынка, и без этой проверки
    такой прогон выглядит удачным. Сверяемся с явным порогом из конфига и с
    прошлым удачным прогоном: сколько страниц он прошёл — столько их и есть.

    Оба порога выключаются значением `null`. Ноль — это ноль: `drop_percent: 0`
    значит «короче прошлого удачного быть не должно», а не «не проверяем».
    """
    if expected_min is not None and pages_fetched < int(expected_min):
        return (
            f"обход оборвался: пройдено страниц {pages_fetched}, это меньше порога "
            f"scrape.expected_pages_min = {int(expected_min)}"
        )
    if previous_pages and drop_percent is not None:
        floor = int(previous_pages) * (1 - float(drop_percent) / 100)
        if pages_fetched < floor:
            return (
                f"обход оборвался: пройдено страниц {pages_fetched}, а прошлый удачный "
                f"прогон прошёл {int(previous_pages)} — падение больше порога "
                f"scrape.max_pages_drop_percent = {float(drop_percent):.0f}%"
            )
    return None


def resume_start_page(previous: Run | None) -> tuple[int, str | None]:
    """С какой страницы идёт `--resume` и что об этом сказано в журнале.

    Возобновлять можно только прерванный обход. Прогон, который дошёл до конца
    без ошибок, продолжать нечего: раньше `--resume` брал его последнюю страницу,
    проходил её одну и отчитывался успехом — в базе при этом лежала позавчерашняя лента.

    Страница из `last_page` уже разобрана и записана — продолжаем со следующей.
    """
    if previous is None:
        return 1, None
    if previous.finished_at is not None and not previous.errors:
        return 1, (
            "прошлый обход завершён без ошибок, продолжать нечего — "
            "иду с первой страницы"
        )
    if previous.last_page < 1:
        return 1, "прошлый обход не дошёл ни до одной страницы — иду с первой"
    page = int(previous.last_page) + 1
    return page, f"обход продолжен со страницы {page}"


def run_mode(*, fresh: bool, resume: bool, limit_asked: int | None) -> str:
    """Каким был этот обход. От режима зависит, кому он норма и кого он снимает.

    Полным считается обход, который никто не просил укорачивать: продолженный,
    инкрементальный и укороченный человеком видели не всю ленту.

    Меркой служит именно `--max-pages`, а не итоговый потолок: `scrape.max_pages`
    в конфиге — рабочая настройка окружения, и она не должна навсегда выключать
    пометку снятых и проверку недобора. Недобор такому прогону предъявляется
    наравне с остальными полными — иначе дыру просто перенесли бы.
    """
    if fresh:
        return "fresh"
    if resume:
        return "resume"
    if limit_asked is not None:
        return "partial"
    return "full"


DEFAULT_FRESH_STOP_PAGES = 2     # столько страниц подряд без новых — и хватит
DEFAULT_FRESH_MAX_PAGES = 20     # потолок инкрементального обхода


def incremental_stop(
    *,
    pages_without_new: int,
    threshold: int | None,
    pages_fetched: int,
    ceiling: int | None,
    ceiling_source: str = "config",
    ceiling_key: str = "scrape.fresh_max_pages",
) -> tuple[str | None, bool]:
    """Пора ли кончать инкрементальный обход и честный ли это конец.

    Спека говорит «до первого известного ID», но лента переставляет объявление
    наверх при поднятии: первое известное встречается на первой же странице
    почти всегда. Поэтому считаем страницы подряд, на которых не было ни одного
    нового ID: две страницы — это около 190 карточек запаса.

    Потолок — не конец, а сбой: обход не дошёл до известного, значит часть ленты
    он не видел, и следующим должен идти полный обход.

    Оба порога выключаются значением `null`, а не нулём: порог 0 страниц без
    новых значит «встань на первой же такой странице», а не «иди без конца».
    """
    if threshold is not None and pages_without_new >= threshold:
        return (
            f"{pages_without_new} страниц подряд без новых объявлений "
            f"(scrape.fresh_stop_after_known_pages = {threshold})",
            False,
        )
    if ceiling is not None and pages_fetched >= ceiling:
        if ceiling_source == "flag":
            # Потолок с флага — просьба, а не сбой: человек попросил короткий
            # обход и получил его. Помечать такой прогон ошибкой и отказывать
            # в заливке базы — перебор, да и объяснять отказ ключом конфига,
            # которого человек не задавал, нечестно.
            return (
                f"инкрементальный обход остановлен на странице {pages_fetched}: "
                f"столько и просили (--max-pages {ceiling})",
                False,
            )
        return (
            f"инкрементальный обход упёрся в потолок {ceiling_key} = {ceiling}, "
            f"до известных объявлений он не дошёл — нужен полный обход",
            True,
        )
    return None, False


DEFAULT_MAX_GONE = 10     # доля активных, которая может пропасть с ленты за один обход


def gone_refusal(
    missing: int, active_total: int, max_percent: float | None
) -> str | None:
    """Причина, по которой снятыми не помечается ничего. None — помечаем.

    С ленты за час уходит десяток объявлений. Если пропала пятая часть базы,
    объяснение не в рынке: обход не дошёл до конца, уехала вёрстка или в разбор
    попала не та страница. Пометить их снятыми — значит выбросить из работы
    тысячи живых квартир, а вернёт их только следующий удачный обход.

    Три случая порога: `None` — проверки нет (её выключили явным `null`);
    `0` — пропало хоть что-то, значит сбой; число — допустимая доля.
    Ноль, прочитанный как «предохранителя нет», стоил бы базы.
    """
    if max_percent is None:
        return None
    if not missing or not active_total:
        return None
    share = missing / active_total * 100
    if share <= max_percent:
        return None
    return (
        f"снятыми не помечено ничего: с ленты пропало {missing} объявлений "
        f"из {active_total} активных ({share:.1f}%), это больше порога "
        f"scrape.max_gone_percent = {max_percent:.0f}%. Так выглядит оборванный "
        f"обход, а не рынок"
    )


def upload_refusal(
    *, errors: int, shrink: str | None, allowed_errors: bool
) -> str | None:
    """Причина, по которой эта копия не уезжает в хранилище. None — заливаем.

    Заливка подменяет общую копию. Прогон, в котором были ошибки, записал в базу
    неполную картину: уехала вёрстка, оборвался обход, не открылась страница.
    Залить такую — значит испортить ту, что лежала в хранилище и была в порядке.
    Локальная копия при этом остаётся на диске: её можно посмотреть глазами и,
    если всё в порядке, залить следующим прогоном с флагом.
    """
    if shrink:
        return shrink
    if errors > 0 and not allowed_errors:
        return (
            f"база не залита: ошибок в прогоне — {errors}, в хранилище осталась "
            f"прежняя копия. Локальная копия на диске — посмотри её и, если всё "
            f"в порядке, scrape --allow-upload-with-errors"
        )
    return None


BACKUP_STAMP = "%Y%m%d-%H%M%S-%f"      # отметка времени в имени копии базы


def backup_names(names, stem: str, suffix: str) -> list[str]:
    """Из имён хранилища — только копии базы, сделанные ротацией.

    Хранилище общее: рядом с базой лежат выгрузки (`listam-20260921-0937.xlsx`)
    и что угодно ещё, что туда положил человек. Префикса мало — чужое имя
    начинается так же, и чистка сносила его вместе со своими. Своё узнаётся
    по расширению базы и по отметке времени, которую ставит сама ротация.
    """
    shape = re.compile(
        rf"^{re.escape(stem)}-\d{{8}}-\d{{6}}-\d{{6}}{re.escape(suffix)}$"
    )
    return [name for name in names if shape.match(name)]


def rotate_backups(storage, remote_name: str, keep: int, work_dir: Path) -> None:
    """Кладёт прежнюю копию рядом под именем с отметкой времени и подчищает старые."""
    if keep <= 0 or not storage.exists(remote_name):
        return
    stem, suffix = Path(remote_name).stem, Path(remote_name).suffix
    stamp = datetime.now(timezone.utc).strftime(BACKUP_STAMP)
    work_dir.mkdir(parents=True, exist_ok=True)
    spare = work_dir / f".{stem}-backup{suffix}"
    try:
        if storage.download(remote_name, spare):
            storage.upload(spare, f"{stem}-{stamp}{suffix}")
    finally:
        spare.unlink(missing_ok=True)
    kept = backup_names(storage.names(prefix=f"{stem}-"), stem, suffix)
    for name in kept[:-keep]:
        storage.delete(name)


@dataclass
class _Counters:
    pages_fetched: int = 0
    listings_seen: int = 0
    new_listings: int = 0
    updated_listings: int = 0
    price_changed: int = 0
    gone_marked: int = 0
    returned: int = 0
    errors: int = 0


def _fetch_rate(provider) -> tuple[Rate | None, str]:
    try:
        rate = provider.amd_per_usd()
    except RateError as exc:
        return None, f"курс не получен: {exc}"
    banks = f", банков: {rate.banks_counted}" if rate.banks_counted else ""
    return rate, f"курс {rate.value} ({rate.source}{banks})"


def _as_int(value) -> int | None:
    """Порог числом или None. Выключенный порог остаётся выключенным."""
    return None if value is None else int(value)


def _as_float(value) -> float | None:
    return None if value is None else float(value)


def run_scrape(
    config: Config,
    *,
    max_pages: int | None = None,
    dry_run: bool = False,
    allow_shrink: bool = False,
    resume: bool = False,
    allow_upload_with_errors: bool = False,
    fresh: bool = False,
) -> Run:
    """Один проход по ленте категории. Возвращает журнал прогона.

    `dry_run` разбирает страницы и считает, но не пишет ни строки: ни объявлений,
    ни журнала, ни базы в хранилище.

    `resume` продолжает прерванный обход с последней пройденной страницы:
    215 страниц с паузой между запросами — это часы, начинать их заново из-за
    оборванной связи незачем.

    `fresh` проходит только свежую часть ленты — до страниц, на которых нет
    ничего нового. Полный обход — сотни страниц и десяток минут; раз в час так
    ходить незачем.
    """
    # Потолок обхода: флаг сильнее конфига, `null` — «сколько есть».
    # `int(limit) if limit else None` съедал ноль и превращал `--max-pages 0`
    # в полный обход: тот записывался меркой полноты и помечал снятых.
    limit_asked = _as_int(max_pages)
    limit = limit_asked if limit_asked is not None else _as_int(
        threshold(config, "scrape.max_pages", None)
    )
    if limit is not None and limit < 1:
        raise ValueError(
            f"страниц для обхода задано {limit}, а должна быть хотя бы одна. "
            f"Полный обход — это отсутствие --max-pages (и scrape.max_pages: null), "
            f"а не ноль."
        )
    category = config.get("scrape.category", 60)
    base_url = config.get("scrape.base_url") or DEFAULT_BASE_URL

    # Пороги читаются одной меркой: `null` — выключено, число — число, в том
    # числе 0. `or default` здесь был бы дырой: ноль на пороге безопасности
    # означает самый строгий режим, а не отсутствие проверки.
    min_cards = _as_int(threshold(config, "scrape.min_cards_per_page", DEFAULT_MIN_CARDS))
    max_cards = _as_int(threshold(config, "scrape.max_cards_per_page", DEFAULT_MAX_CARDS))
    # Исключение из правила: `hard_page_limit: 0` остаётся «выключено».
    # Это не предохранитель данных, а защита от зацикленного пагинатора, и
    # «потолок в ноль страниц» не значит ничего, кроме «обхода не будет».
    hard_limit = _as_int(threshold(config, "scrape.hard_page_limit", DEFAULT_HARD_PAGE_LIMIT))
    rules = rules_from(config)
    coverage = Coverage(coverage_rules_from(config))

    counters = _Counters()
    started_at = datetime.now(timezone.utc)
    # Режим считается до похода за курсом: прогон, который не начался, тоже
    # должен быть отличим в журнале, а не лежать там безрежимным.
    mode = run_mode(fresh=fresh, resume=resume, limit_asked=limit_asked)
    stop_reason = None
    fresh_threshold = _as_int(
        threshold(config, "scrape.fresh_stop_after_known_pages", DEFAULT_FRESH_STOP_PAGES)
    )
    fresh_ceiling = _as_int(
        threshold(config, "scrape.fresh_max_pages", DEFAULT_FRESH_MAX_PAGES)
    )
    # Откуда взялся потолок инкрементального обхода — это то, что будет названо
    # человеку в объяснении остановки, и то, ошибка это или просьба.
    fresh_ceiling_source = "config"
    fresh_ceiling_key = "scrape.fresh_max_pages"
    if fresh and limit is not None:
        # --max-pages опускает потолок: ниже него инкрементальный обход не идёт.
        if fresh_ceiling is None or limit <= fresh_ceiling:
            fresh_ceiling = limit
            if limit_asked is not None:
                fresh_ceiling_source = "flag"
            else:
                fresh_ceiling_key = "scrape.max_pages"

    # Курс — предусловие прогона, а не его счётчик: без него все пересчитанные
    # цены вышли бы пустыми и затёрли бы посчитанное прошлым прогоном.
    # Поэтому его берём до того, как тронули базу, и без него не идём дальше.
    fetcher = build_fetcher(config)
    rate, note = _fetch_rate(build_rate_provider(config, fetcher))
    if rate is None:
        fetcher.close()
        counters.errors += 1
        return Run(
            id=None,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            rate_amd_per_usd=None,
            errors=counters.errors,
            mode=mode,
            notes=f"{note}; прогон не начат: без курса база не трогается",
        )
    rate_value = rate.value

    # Замок: одновременно по ленте ходит ровно один процесс. Два прогона разом —
    # это гонка, в которой побеждает тот, кто позже залил файл в хранилище.
    lock = None
    if not dry_run:
        lock = build_run_lock(config)
        try:
            lock.acquire()
        except LockBusy as exc:
            fetcher.close()
            counters.errors += 1
            return Run(
                id=None,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                rate_amd_per_usd=rate_value,
                errors=counters.errors,
                mode=mode,
                notes=f"{note}; {exc}",
            )

    try:
        # Пробный прогон не трогает диск вообще: ни скачивания базы, ни файла,
        # ни миграций — иначе «ничего не записано» было бы неправдой.
        storage = None
        local_db = database_path(config)
        remote_name = config.get("storage.db_filename", "listam.sqlite")
        database = None
        remote = _Remote(note="")
        if not dry_run:
            # Файл базы может быть занят другой программой или лежать на папке,
            # куда нет прав. Это ошибка прогона с внятным текстом, а не трейсбек:
            # журнала ещё нет, поэтому рассказывает о ней возвращённый Run.
            try:
                storage = build_storage(config)
                remote = take_the_fresher_copy(storage, remote_name, local_db)
                note = f"{note}; {remote.note}" if remote.note else note
                database = build_database(config)
                database.connect()
                database.migrate()
            except (OSError, sqlite3.Error) as exc:
                # Папка вместо файла, нет прав, битый файл: SQLite отвечает на это
                # своей ошибкой, а не OSError, и без неё трейсбек летел мимо журнала.
                if database is not None:
                    database.close()
                fetcher.close()
                counters.errors += 1
                return Run(
                    id=None,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    rate_amd_per_usd=rate_value,
                    errors=counters.errors,
                    mode=mode,
                    notes=f"{note}; файл базы недоступен: {exc}",
                )

        # Прошлый удачный прогон — мерка полноты обхода: столько страниц в ленте и есть.
        previous_success = database.last_successful_run() if database is not None else None

        # Известное к началу прогона: инкрементальный обход идёт, пока на странице
        # попадается хоть что-то новое. Пробный прогон базы не касается и знать
        # её содержимое не имеет права — такой обход дойдёт до потолка и скажет об этом.
        known = database.known_ids() if (fresh and database is not None) else set()
        pages_without_new = 0

        start_page = 1
        if resume and database is not None:
            # --resume продолжает прерванный ОБХОД, а не строку журнала: полный
            # прогон и продолжающие его `resume` идут по одной ленте, и мерка —
            # самая дальняя пройденная страница среди них. Инкрементальный
            # прогон, прошедший между ними, в обход не входит.
            start_page, resume_note = resume_start_page(database.crawl_to_resume())
            if resume_note:
                note = f"{note}; {resume_note}"

        run_id = None if dry_run else database.start_run(started_at, rate_value, mode=mode)
        seen_ids: set[str] = set()
        try:
            # Падение посреди обхода — это ошибка прогона, а не «ничего не было».
            # Без этого журнал оставался с errors = 0, а испорченная половинная база
            # уезжала в хранилище как удачный прогон.
            try:
                page = start_page
                while True:
                    try:
                        html = fetcher.get(page_path(category, page))
                    except FetchError as exc:
                        counters.errors += 1
                        stop_reason = "ошибка обхода"
                        note = f"{note}; страница {page} не получена: {exc}"
                        break

                    try:
                        cards = parse_listing_cards(html, base_url=base_url)
                    except ListingContainerMissing as exc:
                        counters.errors += 1
                        stop_reason = "ошибка обхода"
                        note = f"{note}; страница {page}: {exc}"
                        break

                    if max_cards is not None and len(cards) > max_cards:
                        counters.errors += 1
                        stop_reason = "ошибка обхода"
                        note = (
                            f"{note}; страница {page}: карточек {len(cards)}, "
                            f"это больше порога scrape.max_cards_per_page = {max_cards}. "
                            f"Столько лента не отдаёт — похоже, в разбор попала не она"
                        )
                        break
                    if min_cards is not None and len(cards) < min_cards:
                        counters.errors += 1
                        stop_reason = "ошибка обхода"
                        note = (
                            f"{note}; страница {page}: карточек {len(cards)}, "
                            f"это меньше порога scrape.min_cards_per_page = {min_cards}. "
                            f"Так выглядит смена вёрстки или заглушка Cloudflare с кодом 200"
                        )
                        break
                    counters.pages_fetched += 1   # страница засчитана: она разобралась
                    fresh_on_page = sum(1 for card in cards if card.id not in known)

                    for listing in cards:
                        counters.listings_seen += 1
                        if listing.id in seen_ids:
                            continue          # одно и то же объявление бывает на двух страницах подряд
                        seen_ids.add(listing.id)
                        coverage.add(listing)
                        apply_rate(listing, rate_value)
                        listing.anomaly = detect_anomalies(listing, rules)
                        if dry_run:
                            continue
                        outcome = database.upsert_listing(
                            listing,
                            seen_at=datetime.now(timezone.utc),
                            rate_amd_per_usd=rate_value,
                        )
                        if outcome == "new":
                            counters.new_listings += 1
                        elif outcome == "returned":
                            # Возврат к обновлениям не подмешивается: «вернулось
                            # на рынок» — событие рынка, а не правка поля.
                            counters.returned += 1
                        elif outcome == "price_changed":
                            counters.price_changed += 1
                            counters.updated_listings += 1
                        elif outcome == "updated":
                            counters.updated_listings += 1

                    if run_id is not None:
                        database.mark_page(run_id, page)    # убитый прогон продолжится отсюда
                    if lock is not None:
                        lock.touch()        # прогон жив: срок замка отсчитывается заново

                    if fresh:
                        known.update(card.id for card in cards)
                        pages_without_new = 0 if fresh_on_page else pages_without_new + 1
                        reason, is_error = incremental_stop(
                            pages_without_new=pages_without_new,
                            threshold=fresh_threshold,
                            pages_fetched=counters.pages_fetched,
                            ceiling=fresh_ceiling,
                            ceiling_source=fresh_ceiling_source,
                            ceiling_key=fresh_ceiling_key,
                        )
                        if reason:
                            stop_reason = reason
                            if is_error:
                                counters.errors += 1
                                note = f"{note}; {reason}"
                            break

                    if limit is not None and counters.pages_fetched >= limit:
                        stop_reason = f"лимит страниц: scrape.max_pages = {limit}"
                        break
                    if hard_limit and counters.pages_fetched >= hard_limit:
                        counters.errors += 1
                        stop_reason = "ошибка обхода"
                        note = (
                            f"{note}; обход упёрся в потолок scrape.hard_page_limit = "
                            f"{hard_limit}: похоже, пагинатор зациклился"
                        )
                        break
                    following = parse_next_page(html)
                    if following is None or following <= page:
                        stop_reason = "конец ленты: пагинатор не дал следующей страницы"
                        break
                    page = following

                # Укороченный обход сверяем с полнотой только тогда, когда его никто
                # не укорачивал нарочно: с --max-pages и --resume это норма, а не сбой.
                # Инкрементальный обход укорочен нарочно — недобор ему не предъявляется.
                if limit_asked is None and not resume and not fresh:
                    shortfall = pages_shortfall(
                        counters.pages_fetched,
                        _as_int(threshold(config, "scrape.expected_pages_min", None)),
                        previous_success.pages_fetched if previous_success else None,
                        _as_float(
                            threshold(config, "scrape.max_pages_drop_percent",
                                      DEFAULT_MAX_PAGES_DROP)
                        ),
                    )
                    if shortfall:
                        counters.errors += 1
                        note = f"{note}; {shortfall}"

                # Проверка, которой не было, обязана сказать о себе: иначе
                # прогон на малой выборке в журнале выглядит как здоровый.
                skipped = coverage.skipped_note()
                if skipped:
                    note = f"{note}; {skipped}"

                for failure in coverage.failures():
                    counters.errors += 1
                    note = f"{note}; {failure}"

                # Возврат считает любой обход, а не только полный: встретить
                # снятое объявление на голове ленты `--fresh` умеет не хуже.
                if counters.returned:
                    note = f"{note}; вернулось на ленту: {counters.returned}"

                # Продолженный обход законно кончается на первой же своей странице —
                # это конец ленты, а не сломанный пагинатор.
                alone = (
                    counters.pages_fetched == 1
                    and not fresh
                    and not (resume and start_page > 1)
                )
                if counters.errors == 0 and alone and limit != 1:
                    counters.errors += 1
                    # Человеку объясняем то, что он задавал: потолка не было —
                    # говорим, чем одна страница плоха сама по себе; потолок
                    # с флага — называем флаг; из конфига — ключ конфига.
                    # `scrape.max_pages = None` объяснением не является.
                    if limit is None:
                        because = "а лента категории — это сотни страниц"
                    elif limit_asked is not None:
                        because = f"хотя просили --max-pages {limit}"
                    else:
                        because = f"хотя scrape.max_pages = {limit}"
                    note = (
                        f"{note}; пагинатор не дал следующей страницы: обход кончился "
                        f"на первой, {because}"
                    )

                # Снятыми помечает только полный удачный обход: он один видел
                # всю ленту, и только у него «не встретилось» значит «ушло».
                if mode == "full" and counters.errors == 0 and database is not None:
                    active = database.active_ids()
                    missing = active - seen_ids
                    refusal = gone_refusal(
                        len(missing), len(active),
                        _as_float(threshold(config, "scrape.max_gone_percent", DEFAULT_MAX_GONE)),
                    )
                    if refusal:
                        counters.errors += 1
                        note = f"{note}; {refusal}"
                    elif missing:
                        counters.gone_marked = database.mark_gone(
                            missing, gone_at=datetime.now(timezone.utc)
                        )
                        note = f"{note}; снято с публикации: {counters.gone_marked}"
            except BaseException as exc:
                counters.errors += 1
                if isinstance(exc, KeyboardInterrupt):
                    note = f"{note}; прогон прерван человеком"
                else:
                    note = f"{note}; прогон упал: {type(exc).__name__}: {exc}"
                raise
        finally:
            finished_at = datetime.now(timezone.utc)

            def journal() -> None:
                """Записывает журнал прогона тем, что известно к этой минуте.

                Зовётся не один раз: отказ от заливки и несделанный снимок — это тоже
                ошибки прогона, и узнаём мы о них уже после того, как строка записана.
                """
                if run_id is not None:
                    database.finish_run(
                        run_id,
                        finished_at,
                        pages_fetched=counters.pages_fetched,
                        listings_seen=counters.listings_seen,
                        new_listings=counters.new_listings,
                        updated_listings=counters.updated_listings,
                        price_changed=counters.price_changed,
                        gone_marked=counters.gone_marked,
                        returned=counters.returned,
                        errors=counters.errors,
                        stop_reason=stop_reason,
                        notes=note,
                    )

            refusal = None
            snapshot = None
            if storage is not None:
                shrink = shrink_refusal(
                    remote.listings,
                    database.count_listings(),
                    float(config.get("storage.max_shrink_percent", DEFAULT_MAX_SHRINK)) / 100,
                    allow_shrink,
                )
                if shrink:
                    counters.errors += 1
                    note = f"{note}; {shrink}"
                journal()
                # Снимок — после журнала и на ещё открытой базе: в хранилище уезжает
                # копия, в которой этот прогон уже записан.
                snapshot = local_db.with_name(local_db.name + ".snapshot")
                try:
                    database.snapshot(snapshot)
                except Exception as exc:      # sqlite3.Error, OSError — заливать нечего
                    counters.errors += 1
                    note = f"{note}; снимок базы не сделан: {exc}"
                    snapshot = None
                refusal = upload_refusal(
                    errors=counters.errors,
                    shrink=shrink,
                    allowed_errors=allow_upload_with_errors,
                )
                if refusal and refusal is not shrink:
                    note = f"{note}; {refusal}"
            journal()
            if database is not None:
                database.close()
            fetcher.close()
            if storage is not None and refusal is None and snapshot is not None:
                rotate_backups(
                    storage,
                    remote_name,
                    int(config.get("storage.keep_backups", DEFAULT_KEEP_BACKUPS) or 0),
                    local_db.parent,
                )
                storage.upload(snapshot, remote_name)
            if snapshot is not None:
                snapshot.unlink(missing_ok=True)

        return Run(
            id=run_id,
            started_at=started_at,
            finished_at=finished_at,
            rate_amd_per_usd=rate_value,
            pages_fetched=counters.pages_fetched,
            listings_seen=counters.listings_seen,
            new_listings=counters.new_listings,
            updated_listings=counters.updated_listings,
            price_changed=counters.price_changed,
            gone_marked=counters.gone_marked,
            returned=counters.returned,
            errors=counters.errors,
            mode=mode,
            stop_reason=stop_reason,
            notes=note,
        )
    finally:
        # Замок держится ровно столько, сколько идёт прогон: любой выход отсюда,
        # включая падение подготовки, обязан его отпустить — иначе папка заперта
        # до тех пор, пока замок не протухнет или его не снимут руками.
        if lock is not None:
            lock.release()
