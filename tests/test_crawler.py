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


def test_dry_run_leaves_the_database_untouched(project):
    run = run_scrape(project, dry_run=True)

    database = opened(project)
    ids = database.known_ids()
    stored = database.last_run()
    database.close()

    assert ids == set()
    assert stored is None
    assert run.id is None
    assert run.listings_seen == 9      # страницы разобраны, просто ничего не записано


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
