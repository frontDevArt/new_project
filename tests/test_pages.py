"""Шаг воронки `pages` (фаза 3 M3.5): страница объявления открывается только
кандидату под живую заявку, с паузой и потолком, и кэшируется.

Сайт здесь — папка с настоящими страницами (`FilesFetcher`): фикстура
панельного дома в Нор Норке лежит под именем каждого объявления.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import listam.pages as pages_module
from listam.config import Config, ConfigError
from listam.domain.models import ListingPage
from listam.pages import run_pages
from listam.ports.fetcher import FetchError, Fetcher
from listam.wiring import build_database

from tests.contracts.test_database_contract import make_listing, make_request

FIXTURE = Path(__file__).parent / "fixtures" / "item-24041732.html"
NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами

WISHES = {
    "ремонт": {"field": "renovation", "any_of": ["косметический", "евроремонт", "дизайнерский"]},
    "не панель": {"field": "building_type", "none_of": ["панельное"]},
    "лифт": {"field": "elevator", "is": True},
}


def cfg(tmp_path: Path, **funnel) -> Config:
    data = {
        "env": "test",
        "storage": {"kind": "local", "directory": str(tmp_path / "remote"),
                    "work_dir": str(tmp_path / "work"), "db_filename": "listam.sqlite"},
        "scrape": {"kind": "files", "pages_dir": str(tmp_path / "site")},
        "match": {"thresholds": {"hot": 70, "digest": 40}, "budget_stretch_percent": 10},
        "funnel": {"delay_seconds": 0, "max_opens_per_run": 30, "max_attempts": 2,
                   "wishes": WISHES, **funnel},
    }
    return Config(data, env="test", path=Path("config/test.yaml"))


def suitable(listing_id, **over):
    """Объявление, которое заявке из `make_request` подходит грубым ситом."""
    fields = dict(district="Кентрон", street=f"улица {listing_id}", rooms=3,
                  area=85.0, floor=4, floors_total=9, price_usd=110_000.0,
                  price_raw="$110,000", price_per_sqm=1294.0, seller_type="owner")
    fields.update(over)
    return make_listing(listing_id, **fields)


def publish_pages(config: Config, *listing_ids: str) -> None:
    """Кладёт настоящую страницу под именем каждого объявления."""
    site = Path(config.get("scrape.pages_dir"))
    site.mkdir(parents=True, exist_ok=True)
    for listing_id in listing_ids:
        shutil.copyfile(FIXTURE, site / f"ru-item-{listing_id}.html")


def fill(config: Config, listings=(), requests=(), seen_at=NOW) -> None:
    database = build_database(config)
    database.connect()
    database.migrate()
    try:
        for listing in listings:
            database.upsert_listing(listing, seen_at=seen_at)
        for request in requests:
            database.upsert_request(request, seen_at)
    finally:
        database.close()


def page_of(config: Config, listing_id: str) -> ListingPage | None:
    database = build_database(config)
    database.connect()
    try:
        return database.get_page(listing_id)
    finally:
        database.close()


def save(config: Config, page: ListingPage) -> None:
    database = build_database(config)
    database.connect()
    try:
        database.save_page(page)
    finally:
        database.close()


@pytest.fixture
def config(tmp_path):
    config = cfg(tmp_path)
    fill(config, listings=[suitable("1"), suitable("2")],
         requests=[make_request("R-1", must_have="ремонт")])
    publish_pages(config, "1", "2")
    return config


def test_only_candidates_are_opened(tmp_path):
    """Не тот район и дороже растянутого бюджета — не кандидаты: их страницы
    не открываются, хотя на «сайте» они есть."""
    config = cfg(tmp_path)
    fill(config,
         listings=[suitable("1"), suitable("far", district="Давташен"),
                   suitable("dear", price_usd=500_000.0)],
         requests=[make_request("R-1", must_have="ремонт")])
    publish_pages(config, "1", "far", "dear")

    report = run_pages(config)

    assert report.errors == 0
    assert (report.candidates, report.opened) == (1, 1)
    assert page_of(config, "1").status == "ok"
    assert page_of(config, "far") is None
    assert page_of(config, "dear") is None


def test_an_opened_page_is_cached_with_fields_and_price(config):
    run_pages(config)

    page = page_of(config, "1")
    assert page.status == "ok"
    assert page.attempts == 1
    assert page.price_raw == "$110,000"
    assert page.fetched_at is not None
    assert page.fields.values["building_type"] == "панельное"
    assert page.fields.values["renovation"] == "косметический"


def test_the_ceiling_is_respected(tmp_path):
    config = cfg(tmp_path, max_opens_per_run=2)
    fill(config, listings=[suitable(str(i)) for i in range(1, 6)],
         requests=[make_request("R-1", must_have="ремонт")])
    publish_pages(config, *[str(i) for i in range(1, 6)])

    report = run_pages(config)

    assert report.candidates == 5
    assert report.opened == 2
    assert report.ceiling == 2
    assert "потолок 2" in report.render()


def test_the_ceiling_takes_the_best_coarse_score_first(tmp_path):
    """Потолок не лотерея: первыми открываются лучшие по грубому баллу."""
    config = cfg(tmp_path, max_opens_per_run=1)
    fill(config,
         listings=[suitable("agency", seller_type="agency"), suitable("owner")],
         requests=[make_request("R-1", must_have="ремонт")])
    publish_pages(config, "agency", "owner")

    run_pages(config)

    assert page_of(config, "owner").status == "ok"
    assert page_of(config, "agency") is None


def test_a_smaller_max_narrows_the_ceiling(config):
    report = run_pages(config, max_opens=1)
    assert report.opened == 1


def test_a_max_above_the_ceiling_is_refused(config):
    with pytest.raises(ConfigError) as error:
        run_pages(config, max_opens=31)
    assert "funnel.max_opens_per_run" in str(error.value)


def test_a_fresh_page_is_not_reopened(config):
    run_pages(config)

    again = run_pages(config)

    assert again.opened == 0
    assert again.from_cache == 2


def test_a_price_change_reopens_the_page(config):
    run_pages(config)
    fill(config, listings=[suitable("1", price_raw="$105,000", price_usd=105_000.0)],
         seen_at=NOW + timedelta(hours=1))

    again = run_pages(config)

    assert again.opened == 1
    assert page_of(config, "1").price_raw == "$105,000"


def test_a_listing_back_on_the_feed_reopens_the_page(config):
    """Решение 13: вернулось на ленту после открытия — страницу пора открыть."""
    save(config, ListingPage(listing_id="1", status="ok", attempts=1,
                             fetched_at=NOW - timedelta(days=3), price_raw="$110,000"))
    database = build_database(config)
    database.connect()
    try:
        database.mark_gone(["1"], NOW - timedelta(days=2))
        database.upsert_listing(suitable("1"), seen_at=NOW)          # вернулось
    finally:
        database.close()

    report = run_pages(config)

    assert page_of(config, "1").fields is not None
    assert report.opened == 2


def test_gone_page_is_never_reopened(config):
    save(config, ListingPage(listing_id="1", status="gone", attempts=1))

    report = run_pages(config)

    assert report.opened == 1
    assert page_of(config, "1").status == "gone"


class Refusing(Fetcher):
    """Сайт, который на всё отвечает одним и тем же отказом."""

    def __init__(self, status=None):
        self.status = status
        self.urls: list[str] = []

    @property
    def requests_made(self) -> int:
        return len(self.urls)

    def get(self, url: str) -> str:
        self.urls.append(url)
        raise FetchError(f"{url} → отказ", status=self.status)


def test_a_missing_page_on_the_site_is_gone(config, monkeypatch):
    monkeypatch.setattr(pages_module, "build_fetcher",
                        lambda config, delay_seconds=None: Refusing(status=404))

    report = run_pages(config)

    assert report.gone == 2
    assert page_of(config, "1").status == "gone"
    assert report.errors == 0          # сайт ответил: это ответ, а не сбой


def test_failed_page_counts_attempts(config, monkeypatch):
    refusing = Refusing(status=None)
    monkeypatch.setattr(pages_module, "build_fetcher",
                        lambda config, delay_seconds=None: refusing)

    first = run_pages(config)
    second = run_pages(config)
    third = run_pages(config)

    assert first.failed == 2
    assert first.errors == 1           # ни одна из запрошенных не открылась
    page = page_of(config, "1")
    assert page.status == "failed"
    assert page.attempts == 2          # funnel.max_attempts: 2 — дальше не пробуем
    assert "отказ" in page.error
    assert second.failed == 2
    assert third.opened == third.failed == 0
    assert len(refusing.urls) == 4


def test_a_page_without_the_item_block_is_a_failure(config):
    Path(config.get("scrape.pages_dir"), "ru-item-1.html").write_text(
        "<html><body>что-то другое</body></html>", encoding="utf-8")

    report = run_pages(config)

    assert (report.opened, report.ok, report.failed) == (2, 1, 1)
    assert page_of(config, "1").status == "failed"


def test_request_without_wishes_opens_nothing(tmp_path, monkeypatch):
    """Заявке без слов из словаря страницы не нужны: к сайту ради неё не ходят."""
    config = cfg(tmp_path)
    fill(config, listings=[suitable("1")], requests=[make_request("R-1")])
    monkeypatch.setattr(pages_module, "build_fetcher",
                        lambda *args, **kwargs: pytest.fail("сайт тронут"))

    report = run_pages(config)

    assert report.errors == 0
    assert (report.requests, report.candidates, report.opened) == (0, 0, 0)
    assert "не нужны" in report.render()


def test_nice_to_have_alone_makes_a_candidate(tmp_path):
    config = cfg(tmp_path)
    fill(config, listings=[suitable("1")],
         requests=[make_request("R-1", nice_to_have="лифт")])
    publish_pages(config, "1")

    assert run_pages(config).opened == 1


def test_dry_run_opens_nothing(config, monkeypatch):
    monkeypatch.setattr(pages_module, "build_fetcher",
                        lambda *args, **kwargs: pytest.fail("сайт тронут"))

    report = run_pages(config, dry_run=True)

    assert report.candidates == 2
    assert report.opened == 0
    assert report.would_open == ["1", "2"] or report.would_open == ["2", "1"]
    assert page_of(config, "1") is None
    assert "Пробный прогон" in report.render()


def test_the_pause_between_pages_comes_from_the_funnel(tmp_path, monkeypatch):
    config = cfg(tmp_path, delay_seconds=7)
    fill(config, listings=[suitable("1")],
         requests=[make_request("R-1", must_have="ремонт")])
    asked = {}

    def fake(config, delay_seconds=None):
        asked["delay"] = delay_seconds
        return Refusing(status=404)

    monkeypatch.setattr(pages_module, "build_fetcher", fake)

    run_pages(config)

    assert asked["delay"] == 7


class Serving(Refusing):
    """Сайт, который отдаёт настоящую страницу на любой адрес."""

    def get(self, url: str) -> str:
        self.urls.append(url)
        return FIXTURE.read_text(encoding="utf-8")


def test_keep_html_puts_the_raw_page_into_the_pages_dir(config, tmp_path, monkeypatch):
    """Спека: сырьё для отладки — в `scrape.pages_dir` и только по флагу."""
    raw_dir = tmp_path / "raw"
    config.data["scrape"]["pages_dir"] = str(raw_dir)
    monkeypatch.setattr(pages_module, "build_fetcher",
                        lambda config, delay_seconds=None: Serving())

    run_pages(config)
    assert not raw_dir.exists()

    save(config, ListingPage(listing_id="1", status="failed", attempts=1))
    run_pages(config, keep_html=True)
    assert (raw_dir / "ru-item-1.html").exists()


def test_unknown_labels_are_counted_in_the_report(config):
    site = Path(config.get("scrape.pages_dir"))
    html = FIXTURE.read_text(encoding="utf-8").replace(">Лифт<", ">Сауна<")
    (site / "ru-item-1.html").write_text(html, encoding="utf-8")

    report = run_pages(config)

    assert report.unknown_labels == {"Сауна": 1}
    assert "Сауна" in report.render()


@pytest.mark.parametrize("key, value", [
    ("max_opens_per_run", 0), ("max_opens_per_run", None),
    ("max_attempts", 0), ("delay_seconds", -1), ("delay_seconds", "пять"),
])
def test_a_senseless_funnel_setting_is_refused(tmp_path, key, value):
    config = cfg(tmp_path, **{key: value})
    with pytest.raises(ConfigError) as error:
        run_pages(config)
    assert f"funnel.{key}" in str(error.value)
