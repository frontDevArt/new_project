"""Прогон по ленте: команда `scrape` на сохранённых страницах.

Сайт здесь не при чём: `scrape.kind: files` читает те же две страницы с диска,
что читал бы из сети. Курс фиксированный — прогон должен воспроизводиться.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from listam.adapters.db_sqlite import SqliteDatabase
from listam.config import Config
from listam.crawler import run_scrape
from listam.wiring import database_path

FIXTURES = Path(__file__).parent / "fixtures"
RATE = 400.0  # драмов за доллар: круглое число, чтобы пересчёт читался глазами


@pytest.fixture
def project(tmp_path) -> Config:
    """Конфиг, в котором лента — две сохранённые страницы, а курс задан числом."""
    pages = tmp_path / "pages"
    pages.mkdir()
    shutil.copyfile(FIXTURES / "category-60-page1.html", pages / "category-60.html")
    shutil.copyfile(FIXTURES / "category-60-page2.html", pages / "category-60-2.html")
    return Config(
        {
            "storage": {
                "kind": "local",
                "directory": str(tmp_path / "remote"),
                "work_dir": str(tmp_path / "work"),
                "db_filename": "listam.sqlite",
            },
            "rate": {"kind": "fixed", "amd_per_usd": RATE},
            "scrape": {
                "kind": "files",
                "pages_dir": str(pages),
                "base_url": "https://www.list.am/ru",
                "category": 60,
                "delay_seconds": 0,
                "max_pages": None,
                "min_cards_per_page": 3,   # фикстура — не боевая лента, порог свой
            },
        },
        env="test",
        path=tmp_path / "config" / "test.yaml",
    )


def opened(config: Config) -> SqliteDatabase:
    database = SqliteDatabase(database_path(config))
    database.connect()
    return database


def test_scrape_stores_every_listing_from_both_pages(project):
    run = run_scrape(project)

    database = opened(project)
    ids = database.known_ids()
    database.close()
    # 6 карточек первой страницы и 3 второй, одна повторяется на обеих
    assert len(ids) == 8
    assert run.listings_seen == 9
    assert run.new_listings == 8
    assert run.pages_fetched == 2


def test_scrape_converts_prices_by_the_run_rate(project):
    run_scrape(project)

    database = opened(project)
    amd = database.get_listing("24100002")   # 40 000 000 ֏
    usd = database.get_listing("24100001")   # $100 000, 50 кв.м.
    database.close()

    assert amd.price_usd == pytest.approx(40_000_000 / RATE, abs=0.01)
    assert amd.price_amd == 40_000_000
    assert usd.price_amd == pytest.approx(100_000 * RATE, abs=0.01)
    assert usd.price_per_sqm == pytest.approx(2000.0, abs=0.01)


def test_scrape_writes_a_run_row_with_the_rate(project):
    run = run_scrape(project)

    database = opened(project)
    stored = database.last_run()
    database.close()

    assert stored.id == run.id
    assert stored.rate_amd_per_usd == RATE
    assert stored.started_at is not None and stored.finished_at is not None
    assert stored.started_at.tzinfo is not None      # время в UTC
    assert stored.pages_fetched == 2


def test_first_run_puts_one_point_in_price_history(project):
    run_scrape(project)

    database = opened(project)
    history = database.price_history("24100001")
    database.close()

    assert len(history) == 1
    assert history[0].price_usd == 100_000


def test_second_run_over_the_same_pages_adds_nothing(project):
    run_scrape(project)
    second = run_scrape(project)

    database = opened(project)
    ids = database.known_ids()
    history = database.price_history("24100001")
    database.close()

    assert len(ids) == 8
    assert len(history) == 1
    assert second.new_listings == 0
    assert second.updated_listings == 0


def test_max_pages_stops_the_run_early(project):
    run = run_scrape(project, max_pages=1)

    database = opened(project)
    ids = database.known_ids()
    database.close()

    assert run.pages_fetched == 1
    assert len(ids) == 6
    assert "24100001" not in ids


def test_max_pages_comes_from_config_when_flag_is_absent(project):
    project.data["scrape"]["max_pages"] = 1

    run = run_scrape(project)

    assert run.pages_fetched == 1


def test_dry_run_creates_no_database_file_at_all(project):
    """«Ничего не записано» значит и файла базы на диске не появилось."""
    run = run_scrape(project, dry_run=True)

    assert run.id is None
    assert run.listings_seen == 9      # страницы разобраны, просто ничего не записано
    assert not database_path(project).exists()


def test_dry_run_leaves_an_existing_database_untouched(project):
    run_scrape(project)
    before = database_path(project).read_bytes()

    run_scrape(project, dry_run=True)

    database = opened(project)
    runs = database.last_run()
    database.close()
    assert runs.pages_fetched == 2     # журнал остался от настоящего прогона
    assert database_path(project).read_bytes() == before


def test_run_uploads_the_database_to_storage(project):
    run_scrape(project)

    remote = Path(project.get("storage.directory")) / "listam.sqlite"
    assert remote.exists()


def test_dry_run_does_not_upload_anything(project):
    run_scrape(project, dry_run=True)

    remote = Path(project.get("storage.directory")) / "listam.sqlite"
    assert not remote.exists()


def test_missing_page_is_counted_as_an_error_and_stops_the_run(project, tmp_path):
    (tmp_path / "pages" / "category-60-2.html").unlink()

    run = run_scrape(project)

    assert run.pages_fetched == 1
    assert run.errors == 1
    assert run.new_listings == 6


# --- B1: пустая выдача и мёртвый пагинатор — это сбой, а не успешный прогон ---

def card(
    listing_id: int,
    district: str = "Аван",
    agency: bool = False,
    price: str = "100,000",
    attributes: str = "2 ком., 50 кв.м., 3/9 этаж",
) -> str:
    """Одна карточка ленты — ровно та разметка, которую отдаёт сайт."""
    badge = '<span class="ge3">Агентство</span>' if agency else ""
    location = (
        f'<div class="at category-data-list-card__location">{district}</div>'
        if district else ""
    )
    return (
        f'<a href="/ru/item/{listing_id}">'
        '<div class="p"><span class="category-data-list-card__amount">'
        f'<span class="category-data-list-card__currency">$</span>{price}</span></div>'
        '<div class="l">2-комн. квартира на ул. Ачаряна в Аване, 50 кв.м., 3/9 этаж</div>'
        f'<div class="at">{attributes}</div>'
        f'<div class="po78">{badge}</div>'
        f"{location}"
        "</a>"
    )


def paginator(current: int, last: int) -> str:
    links = "".join(
        f'<span class="c">{n}</span>' if n == current else f'<a href="/category/60/{n}">{n}</a>'
        for n in range(1, last + 1)
    )
    return f'<div class="dlf"><span class="pp">{links}</span></div>'


def feed(project: Config, *pages: str) -> None:
    """Кладёт ленту из нескольких страниц с живым пагинатором."""
    directory = Path(project.get("scrape.pages_dir"))
    for old in directory.glob("*.html"):
        old.unlink()
    total = len(pages)
    for number, cards in enumerate(pages, start=1):
        name = "category-60.html" if number == 1 else f"category-60-{number}.html"
        (directory / name).write_text(
            page(cards, paginator(number, total)), encoding="utf-8"
        )


def page(cards: str = "", paginator: str = "") -> str:
    return (
        '<!doctype html><html><body><div id="contentr"><div class="dl"><div class="gl">'
        f"{cards}</div></div>{paginator}</div></body></html>"
    )


def only_page(project: Config, html: str) -> None:
    """Заменяет ленту одной-единственной страницей."""
    pages = Path(project.get("scrape.pages_dir"))
    for old in pages.glob("*.html"):
        old.unlink()
    (pages / "category-60.html").write_text(html, encoding="utf-8")


def test_page_without_cards_is_counted_as_a_failure(project):
    only_page(project, page())

    run = run_scrape(project)

    assert run.listings_seen == 0
    assert run.errors >= 1
    assert "карточ" in (run.notes or "")


def test_page_with_half_the_threshold_of_cards_is_a_failure(project):
    project.data["scrape"]["min_cards_per_page"] = 6
    only_page(project, page("".join(card(i) for i in range(1, 4))))

    run = run_scrape(project)

    assert run.errors >= 1


def test_run_that_never_left_the_first_page_is_a_failure(project):
    """Пагинатор сломался: страница есть, карточки есть, следующей страницы нет."""
    only_page(project, page("".join(card(i) for i in range(1, 7))))

    run = run_scrape(project)

    assert run.pages_fetched == 1
    assert run.errors >= 1
    assert "пагинатор" in (run.notes or "").lower()


def test_healthy_two_page_run_still_has_no_errors(project):
    run = run_scrape(project)

    assert run.errors == 0


# --- B2: без курса прогон не пишет ничего ---

def break_the_rate(project: Config) -> None:
    """Источник курса перестал отдавать цифру — ровно как мусор вместо снимка rate.am."""
    project.data["rate"]["amd_per_usd"] = 0


def test_run_without_a_rate_creates_no_database_at_all(project):
    break_the_rate(project)

    run = run_scrape(project)

    assert run.errors >= 1
    assert run.rate_amd_per_usd is None
    assert not database_path(project).exists()
    assert not (Path(project.get("storage.directory")) / "listam.sqlite").exists()


def test_run_without_a_rate_leaves_listings_and_history_untouched(project):
    run_scrape(project)
    database = opened(project)
    before = {row.id: (row.price_usd, row.price_amd, row.price_per_sqm)
              for row in database.iter_listings()}
    before_history = len(database.price_history("24100001"))
    database.close()

    break_the_rate(project)
    failed = run_scrape(project)

    database = opened(project)
    after = {row.id: (row.price_usd, row.price_amd, row.price_per_sqm)
             for row in database.iter_listings()}
    after_history = len(database.price_history("24100001"))
    database.close()

    assert failed.errors >= 1
    assert after == before
    assert after_history == before_history


def test_run_with_a_rate_after_a_failed_one_restores_the_picture(project):
    break_the_rate(project)
    run_scrape(project)

    project.data["rate"]["amd_per_usd"] = RATE
    healthy = run_scrape(project)

    database = opened(project)
    amd = database.get_listing("24100002")
    database.close()

    assert healthy.errors == 0
    assert amd.price_usd == pytest.approx(40_000_000 / RATE, abs=0.01)


def test_apply_rate_does_not_erase_what_it_cannot_recompute():
    from listam.crawler import apply_rate
    from listam.domain.models import Listing

    listing = Listing(id="1", url="u", price_raw="23,800,000 ֏", currency="AMD",
                      price_amd=23_800_000.0, price_usd=65519.61,
                      price_per_sqm=1637.99, area=40.0)

    apply_rate(listing, None)

    assert listing.price_usd == 65519.61
    assert listing.price_amd == 23_800_000.0
    assert listing.price_per_sqm == 1637.99


# --- B3: движение курса — это не изменение цены ---

def two_healthy_pages(project: Config, first_price: str = "100,000") -> None:
    feed(
        project,
        "".join(card(i, price=first_price) for i in range(101, 107)),
        "".join(card(i) for i in range(201, 205)),
    )


def test_a_different_rate_alone_changes_nothing(project):
    two_healthy_pages(project)
    run_scrape(project)

    project.data["rate"]["amd_per_usd"] = 363.25
    second = run_scrape(project)

    database = opened(project)
    history = database.price_history("101")
    database.close()

    assert second.errors == 0
    assert second.updated_listings == 0
    assert len(history) == 1


def test_a_changed_raw_price_adds_exactly_one_history_point(project):
    two_healthy_pages(project)
    run_scrape(project)

    two_healthy_pages(project, first_price="90,000")
    second = run_scrape(project)

    database = opened(project)
    history = database.price_history("101")
    database.close()

    assert second.updated_listings == 6
    assert [point.price_usd for point in history] == [100_000.0, 90_000.0]


def test_history_point_remembers_the_rate_of_its_run(project):
    two_healthy_pages(project)
    run_scrape(project)

    database = opened(project)
    history = database.price_history("101")
    database.close()

    assert history[0].rate_amd_per_usd == RATE


# --- H3: две машины и один файл базы ---

def remote_file(project: Config) -> Path:
    return Path(project.get("storage.directory")) / "listam.sqlite"


def local_copy_with_run_at(project: Config, moment) -> None:
    """Подменяет локальную копию базы пустой, но с прогоном в заданный момент."""
    path = database_path(project)
    path.unlink(missing_ok=True)
    database = SqliteDatabase(path)
    database.connect()
    database.migrate()
    run_id = database.start_run(moment, 400.0)
    database.finish_run(run_id, moment, pages_fetched=1)
    database.close()


def test_run_prefers_the_fresher_remote_database(project):
    """Вторая машина или откат папки data/ не имеет права затереть общую базу."""
    from datetime import datetime, timedelta, timezone

    run_scrape(project)
    local_copy_with_run_at(project, datetime.now(timezone.utc) - timedelta(days=3))

    second = run_scrape(project)

    assert second.new_listings == 0      # объявления пришли из удалённой копии


def test_run_keeps_the_local_copy_when_it_is_the_fresher_one(project):
    from datetime import datetime, timedelta, timezone

    run_scrape(project)
    remote_file(project).unlink()
    shutil.copyfile(database_path(project), remote_file(project))
    local_copy_with_run_at(project, datetime.now(timezone.utc) + timedelta(days=3))

    second = run_scrape(project)

    assert second.new_listings == 8      # локальная копия новее, объявления собраны заново


def test_upload_keeps_the_previous_copy_in_storage(project):
    run_scrape(project)
    run_scrape(project)

    backups = sorted(Path(project.get("storage.directory")).glob("listam-*.sqlite"))
    assert len(backups) == 1


def test_old_backups_are_rotated_down_to_the_configured_number(project):
    project.data["storage"]["keep_backups"] = 2

    for _ in range(4):
        run_scrape(project)

    backups = sorted(Path(project.get("storage.directory")).glob("listam-*.sqlite"))
    assert len(backups) == 2


def test_upload_is_refused_when_the_database_shrinks_too_much(project):
    from datetime import datetime, timedelta, timezone

    two_healthy_pages(project)                       # 6 + 4 = 10 объявлений
    run_scrape(project)
    before = remote_file(project).read_bytes()

    local_copy_with_run_at(project, datetime.now(timezone.utc) + timedelta(days=3))
    feed(project, "".join(card(i) for i in range(301, 304)),
                  "".join(card(i) for i in range(401, 404)))

    run = run_scrape(project)

    assert run.errors >= 1
    assert "сжал" in (run.notes or "") or "меньше" in (run.notes or "")
    assert remote_file(project).read_bytes() == before


def test_shrinking_upload_goes_through_with_the_explicit_flag(project):
    from datetime import datetime, timedelta, timezone

    two_healthy_pages(project)
    run_scrape(project)
    before = remote_file(project).read_bytes()

    local_copy_with_run_at(project, datetime.now(timezone.utc) + timedelta(days=3))
    feed(project, "".join(card(i) for i in range(301, 304)),
                  "".join(card(i) for i in range(401, 404)))

    run_scrape(project, allow_shrink=True)

    assert remote_file(project).read_bytes() != before


# --- H4: мусорные значения помечаются, а не уезжают в отчёт как данные ---

def test_a_typo_in_the_card_is_marked_in_the_database(project):
    feed(
        project,
        "".join(card(i) for i in range(101, 106))
        + card(199, attributes="3 ком., 1 кв.м., 3/9 этаж"),
        "".join(card(i) for i in range(201, 205)),
    )

    run_scrape(project)

    database = opened(project)
    suspicious = database.get_listing("199")
    clean = database.get_listing("101")
    database.close()

    assert "area" in (suspicious.anomaly or "")
    assert clean.anomaly is None


def test_thresholds_come_from_the_config(project):
    project.data["validate"] = {"area": {"min": 0.5, "max": 5000},
                                "price_per_sqm": {"min": 1, "max": 1_000_000},
                                "min_area_per_room": 0.1}
    feed(
        project,
        "".join(card(i) for i in range(101, 106))
        + card(199, attributes="3 ком., 1 кв.м., 3/9 этаж"),
        "".join(card(i) for i in range(201, 205)),
    )

    run_scrape(project)

    database = opened(project)
    suspicious = database.get_listing("199")
    database.close()

    assert suspicious.anomaly is None


def test_sync_works_when_the_path_has_a_space_in_it(project, tmp_path):
    """Windows-путь вроде C:/Users/John Doe/... не должен ломать сверку копий."""
    from datetime import datetime, timedelta, timezone

    roomy = tmp_path / "папка с пробелом"
    project.data["storage"]["work_dir"] = str(roomy)
    run_scrape(project)
    local_copy_with_run_at(project, datetime.now(timezone.utc) - timedelta(days=3))

    second = run_scrape(project)

    assert second.new_listings == 0


# --- M3: два прогона разом — это гонка, а не два прогона ---

def test_second_scrape_refuses_to_start_while_another_one_runs(project):
    from listam.wiring import run_lock_path
    from listam.adapters.run_lock import RunLock

    held = RunLock(run_lock_path(project))
    held.acquire()
    try:
        run = run_scrape(project)
    finally:
        held.release()

    assert run.errors >= 1
    assert "замок" in (run.notes or "").lower() or "прогон" in (run.notes or "").lower()
    assert not database_path(project).exists()


def test_the_lock_is_released_when_the_run_is_over(project):
    from listam.wiring import run_lock_path

    run_scrape(project)

    assert not run_lock_path(project).exists()


# --- M4: уехавшая вёрстка видна по долям заполненности ---

def with_coverage(project: Config) -> None:
    project.data["coverage"] = {
        "min_sample": 5,
        "min_filled": {"district": 0.9},
        "min_share": {"seller_type": {"agency": 0.05}},
    }


def test_run_where_every_card_lost_its_district_is_a_failure(project):
    with_coverage(project)
    feed(
        project,
        "".join(card(i, district="", agency=(i == 101)) for i in range(101, 107)),
        "".join(card(i, district="", agency=True) for i in range(201, 205)),
    )

    run = run_scrape(project)

    assert run.errors >= 1
    assert "district" in (run.notes or "")


def test_run_where_agencies_disappeared_is_a_failure(project):
    with_coverage(project)
    two_healthy_pages(project)          # в фикстуре агентств нет ни одного

    run = run_scrape(project)

    assert run.errors >= 1
    assert "agency" in (run.notes or "")


def test_run_within_the_coverage_thresholds_passes(project):
    with_coverage(project)
    feed(
        project,
        "".join(card(i, agency=(i % 2 == 0)) for i in range(101, 107)),
        "".join(card(i, agency=(i % 2 == 0)) for i in range(201, 205)),
    )

    run = run_scrape(project)

    assert run.errors == 0


# --- M5: прерванный обход продолжается, а не начинается заново ---

def three_pages(project: Config) -> None:
    feed(
        project,
        "".join(card(i) for i in range(101, 107)),
        "".join(card(i) for i in range(201, 207)),
        "".join(card(i) for i in range(301, 307)),
    )


def test_run_remembers_the_last_page_it_walked(project):
    three_pages(project)

    run_scrape(project)

    database = opened(project)
    last = database.last_run()
    database.close()
    assert last.last_page == 3


def interrupted_run_at(project: Config, page: int) -> None:
    """Пишет в базу журнал убитого прогона: дошёл до страницы и не закончился."""
    from datetime import datetime, timezone

    database = SqliteDatabase(database_path(project))
    database.connect()
    database.migrate()
    run_id = database.start_run(datetime.now(timezone.utc), RATE)
    database.mark_page(run_id, page)
    database.close()


def test_resume_after_a_finished_crawl_walks_the_feed_again(project):
    """Прошлый обход дошёл до конца — продолжать нечего, это новый обход.

    Раньше `--resume` после удачного прогона брал его последнюю страницу,
    проходил её одну и отчитывался успехом: в базе оставалась позавчерашняя лента.
    """
    three_pages(project)
    run_scrape(project)

    second = run_scrape(project, resume=True)

    assert second.pages_fetched == 3          # полный обход, а не одна последняя страница
    assert second.errors == 0
    assert "продолжать нечего" in (second.notes or "")


def test_resume_starts_after_the_page_the_interrupted_run_reached(project):
    """Страница 3 уже разобрана и записана — продолжаем с четвёртой."""
    feed(project, *["".join(card(number * 100 + i) for i in range(1, 7)) for number in range(1, 6)])
    interrupted_run_at(project, 3)

    run = run_scrape(project, resume=True)

    assert run.pages_fetched == 2             # страницы 4 и 5
    assert run.new_listings == 12
    assert "со страницы 4" in (run.notes or "")


def test_resume_without_a_previous_run_starts_from_the_first_page(project):
    three_pages(project)

    run = run_scrape(project, resume=True)

    assert run.pages_fetched == 3


def test_a_plain_run_ignores_the_remembered_page(project):
    three_pages(project)
    run_scrape(project, max_pages=2)

    again = run_scrape(project)

    assert again.pages_fetched == 3


# --- M7: у обхода есть потолок, даже когда max_pages не задан ---

def test_the_crawl_stops_at_the_hard_page_limit(project):
    """Зацикленный пагинатор не имеет права крутить обход бесконечно."""
    project.data["scrape"]["hard_page_limit"] = 2
    three_pages(project)

    run = run_scrape(project)

    assert run.pages_fetched == 2
    assert run.errors >= 1
    assert "hard_page_limit" in (run.notes or "")


def test_a_run_below_the_hard_limit_is_not_touched_by_it(project):
    project.data["scrape"]["hard_page_limit"] = 10
    three_pages(project)

    run = run_scrape(project)

    assert run.pages_fetched == 3
    assert run.errors == 0


# --- L4: заглушка Cloudflare приезжает с кодом 200 и выглядит как страница ---

CHALLENGE_PAGE = (
    "<!doctype html><html><head><title>Just a moment...</title></head>"
    "<body><div id='contentr'><div class='main-wrapper'>"
    "<h1>Проверяем, человек ли вы</h1>"
    "<script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script>"
    "</div></div></body></html>"
)


def test_cloudflare_stub_served_with_code_200_is_a_failed_run(project):
    """Ответ 200 с заглушкой вместо ленты — это сбой, а не пустая категория."""
    only_page(project, CHALLENGE_PAGE)

    run = run_scrape(project)

    assert run.listings_seen == 0
    assert run.errors >= 1
    assert "Cloudflare" in (run.notes or "")


def test_apply_rate_keeps_zero_price(monkeypatch):
    """ВЫСОКИЙ 12: ноль — это цена, а не «не смог посчитать»."""
    from listam.crawler import apply_rate
    from listam.domain.models import Listing

    listing = Listing(id="1", url="u", price_raw="0 $", currency="USD", price_usd=0.0)

    apply_rate(listing, 363.25)

    assert listing.price_usd == 0.0
    assert listing.price_amd == 0.0


def test_run_says_that_the_layout_check_was_skipped(project):
    """Находка 14: прогон, который не проверял вёрстку, обязан это сказать.

    Порог `coverage.min_sample` выше числа карточек прогона — проверка не
    состоялась. Без этой строки такой прогон в журнале неотличим от здорового.
    """
    project.data["coverage"] = {"min_sample": 100, "min_filled": {"district": 0.98}}

    run = run_scrape(project)

    assert "карточек меньше coverage.min_sample = 100, проверка вёрстки пропущена" in run.notes
    assert run.errors == 0     # это предупреждение, а не сбой прогона


def test_a_checked_run_keeps_quiet_about_skipping(project):
    project.data["coverage"] = {"min_sample": 5, "min_filled": {"district": 0.5}}

    run = run_scrape(project)

    assert "проверка вёрстки пропущена" not in run.notes


def feed_page(cards: int) -> str:
    """Страница ленты с заданным числом карточек — ровно такой формы, как на сайте."""
    items = "".join(
        f'<a href="/ru/item/{9000000 + n}"><div class="l">квартира {n}</div>'
        f'<div class="at">2 ком., 56 кв.м., 3/9 этаж</div>'
        f'<div class="at category-data-list-card__location">Аван</div>'
        f'<span class="category-data-list-card__amount">$ 100,000</span></a>'
        for n in range(cards)
    )
    return f'<html><body><div id="contentr">{items}</div></body></html>'


def test_a_page_without_the_feed_container_stops_the_run(project, tmp_path):
    """Находка 15: заглушка вместо ленты — это сбой, а не пять карточек из шапки."""
    pages = Path(project.get("scrape.pages_dir"))
    (pages / "category-60.html").write_text(
        '<html><body><div id="header"><a href="/ru/item/1"><div class="l">шапка</div></a></div>'
        "</body></html>",
        encoding="utf-8",
    )

    run = run_scrape(project)

    assert run.errors == 1
    assert "контейнер ленты не найден" in run.notes
    assert run.listings_seen == 0


def test_too_many_cards_on_a_page_stops_the_run(project):
    """Верхний порог: 140 карточек на странице — это не лента, а склейка."""
    pages = Path(project.get("scrape.pages_dir"))
    (pages / "category-60.html").write_text(feed_page(140), encoding="utf-8")
    project.data["scrape"]["max_cards_per_page"] = 120

    run = run_scrape(project)

    assert run.errors == 1
    assert "scrape.max_cards_per_page = 120" in run.notes


def test_a_page_within_the_upper_limit_is_parsed(project):
    pages = Path(project.get("scrape.pages_dir"))
    (pages / "category-60.html").write_text(feed_page(96), encoding="utf-8")
    project.data["scrape"]["max_cards_per_page"] = 120

    run = run_scrape(project)

    assert "max_cards_per_page" not in run.notes
    assert run.listings_seen >= 96


def test_rotation_keeps_its_hands_off_other_files(tmp_path):
    """Находка 18: ротация чистила по префиксу и сносила чужое.

    `storage.names(prefix="listam-")` ловит и выгрузку `listam-20260921-0937.xlsx`,
    и любой файл, начинающийся так же. Удалять можно только свои копии базы —
    их видно по расширению и по отметке времени, которую ставит сама ротация.
    """
    from listam.adapters.storage_local import LocalStorage
    from listam.crawler import rotate_backups

    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "listam.sqlite").write_text("общая база", encoding="utf-8")
    (remote / "listam-20260921-0937.xlsx").write_text("выгрузка", encoding="utf-8")
    (remote / "listam-заметки.txt").write_text("чужое", encoding="utf-8")
    stamps = [f"2026092{n}-093700-000001" for n in range(1, 7)]
    for stamp in stamps:
        (remote / f"listam-{stamp}.sqlite").write_text(stamp, encoding="utf-8")

    rotate_backups(LocalStorage(remote), "listam.sqlite", keep=5, work_dir=tmp_path / "work")

    left = {item.name for item in remote.iterdir()}
    assert "listam-20260921-0937.xlsx" in left
    assert "listam-заметки.txt" in left
    assert f"listam-{stamps[0]}.sqlite" not in left      # самая старая копия ушла
    assert all(f"listam-{stamp}.sqlite" in left for stamp in stamps[1:])


def test_rotation_counts_only_backups_when_it_decides_what_to_drop(tmp_path):
    """Чужие файлы не должны занимать места в счёте `keep`."""
    from listam.adapters.storage_local import LocalStorage
    from listam.crawler import rotate_backups

    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "listam.sqlite").write_text("общая база", encoding="utf-8")
    for n in range(1, 5):
        (remote / f"listam-2026092{n}-093700-000001.sqlite").write_text("копия", encoding="utf-8")
    for n in range(20):
        (remote / f"listam-отчёт-{n}.xlsx").write_text("выгрузка", encoding="utf-8")

    rotate_backups(LocalStorage(remote), "listam.sqlite", keep=5, work_dir=tmp_path / "work")

    backups = sorted(item.name for item in remote.iterdir() if item.suffix == ".sqlite")
    # четыре прежних копии плюс сделанная сейчас — ровно keep, ничего не удалено
    assert len(backups) == 6        # пять копий и сама база
    assert len([n for n in backups if n != "listam.sqlite"]) == 5


def test_unopenable_database_is_a_run_error_not_a_traceback(project):
    """Файл базы не открылся — прогон говорит об этом и отпускает замок.

    Ловился только OSError, а SQLite на папке вместо файла отвечает своим
    OperationalError: он летел наружу трейсбеком мимо журнала.
    """
    from listam.wiring import run_lock_path

    path = database_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()                      # на месте файла базы — папка

    run = run_scrape(project)

    assert run.errors == 1
    assert "файл базы недоступен" in run.notes
    assert not run_lock_path(project).exists()


def test_a_full_crawl_is_written_down_as_full(project):
    run_scrape(project)

    database = opened(project)
    stored = database.last_run()
    database.close()
    assert stored.mode == "full"


def test_a_crawl_cut_by_max_pages_is_written_down_as_partial(project):
    run_scrape(project, max_pages=1)

    database = opened(project)
    stored = database.last_run()
    database.close()
    assert stored.mode == "partial"


def test_a_short_run_does_not_become_the_yardstick_for_the_next_full_one(project, tmp_path):
    """Прогон на одну страницу не имеет права стать нормой: следующий полный
    обход, вставший на первой странице, обязан быть пойман по прошлому полному."""
    run_scrape(project)                      # полный: 2 страницы
    run_scrape(project, max_pages=1)         # укороченный: 1 страница, ошибок нет

    (tmp_path / "pages" / "category-60-2.html").unlink()   # пагинатор ведёт в никуда
    run = run_scrape(project)

    assert run.errors >= 1
    assert "прошлый удачный прогон прошёл 2" in run.notes


def test_a_changed_price_is_counted_apart_from_other_updates(project, tmp_path):
    """Смена цены — это и обновление карточки тоже: updated_listings остаётся
    счётчиком «изменилось хоть что-то», price_changed отвечает на «что с ценой»."""
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    # 162,000 — карточка 23973917 из самой ленты: цены верхних объявлений
    # (блок «Топ объявления») парсер в разбор не берёт.
    page.write_text(page.read_text(encoding="utf-8").replace("162,000", "155,000"),
                    encoding="utf-8")

    run = run_scrape(project)

    assert run.price_changed == 1
    assert run.updated_listings == 1


def test_a_finished_crawl_says_why_it_stopped(project):
    run = run_scrape(project)

    assert "конец ленты" in run.stop_reason
