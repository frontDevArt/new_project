"""Пометка снятых: объявление, пропавшее с ленты, получает status=gone.

Сайт здесь не при чём: лента — те же две сохранённые страницы, что и в
`tests/test_crawler.py`. «Пропало» изображается правкой файла страницы.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from listam.adapters.db_sqlite import SqliteDatabase
from listam.config import Config
from listam.crawler import gone_refusal, run_scrape
from listam.wiring import database_path

FIXTURES = Path(__file__).parent / "fixtures"
RATE = 400.0


@pytest.fixture
def project(tmp_path) -> Config:
    """Лента — две сохранённые страницы; порог снятых поднят под размер фикстуры.

    В фикстуре всего 8 объявлений, и одно пропавшее — это 12.5%: при боевом
    пороге 10 не сработала бы ни одна пометка.
    """
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
                "min_cards_per_page": 3,
                "max_gone_percent": 50,
            },
        },
        env="test",
        path=tmp_path / "config" / "test.yaml",
    )


def opened(config: Config) -> SqliteDatabase:
    database = SqliteDatabase(database_path(config))
    database.connect()
    return database


def test_a_handful_of_missing_listings_is_just_the_market():
    assert gone_refusal(missing=5, active_total=1000, max_percent=10) is None


def test_half_the_feed_missing_is_a_broken_crawl_not_a_market():
    refusal = gone_refusal(missing=500, active_total=1000, max_percent=10)
    assert "max_gone_percent" in refusal
    assert "500" in refusal


def test_an_empty_base_marks_nothing_and_says_nothing():
    assert gone_refusal(missing=0, active_total=0, max_percent=10) is None


# Объявление 24100001 лежит на ВТОРОЙ странице фикстуры — «пропало с ленты»
# изображается правкой именно её.
FEED_PAGE = "category-60-2.html"


def test_a_listing_that_left_the_feed_is_marked_gone(project, tmp_path):
    run_scrape(project)
    page = tmp_path / "pages" / FEED_PAGE
    page.write_text(page.read_text(encoding="utf-8").replace("24100001", "99100001"),
                    encoding="utf-8")

    run = run_scrape(project)

    assert run.gone_marked == 1
    database = opened(project)
    left = database.get_listing("24100001")
    database.close()
    assert left.status == "gone"
    assert left.gone_at is not None


def test_the_date_a_listing_was_last_seen_does_not_move_when_it_goes(project, tmp_path):
    run_scrape(project)
    database = opened(project)
    seen_before = database.get_listing("24100001").last_seen
    database.close()

    page = tmp_path / "pages" / FEED_PAGE
    page.write_text(page.read_text(encoding="utf-8").replace("24100001", "99100001"),
                    encoding="utf-8")
    run_scrape(project)

    database = opened(project)
    assert database.get_listing("24100001").last_seen == seen_before
    database.close()


def test_a_listing_back_on_the_feed_is_active_again(project, tmp_path):
    page = tmp_path / "pages" / FEED_PAGE
    original = page.read_text(encoding="utf-8")
    run_scrape(project)
    page.write_text(original.replace("24100001", "99100001"), encoding="utf-8")
    run_scrape(project)

    page.write_text(original, encoding="utf-8")     # объявление вернулось на ленту
    run_scrape(project)

    database = opened(project)
    back = database.get_listing("24100001")
    database.close()
    assert back.status == "active"
    assert back.gone_at is None


def test_an_incremental_run_marks_nothing_gone(project):
    """Инкрементальный обход видел одну страницу. «Не встретилось» у него
    не значит «снято»."""
    run_scrape(project)

    run = run_scrape(project, fresh=True)

    assert run.gone_marked == 0
    database = opened(project)
    assert all(item.status == "active" for item in database.iter_listings())
    database.close()


def test_a_crawl_cut_by_max_pages_marks_nothing_gone(project):
    run_scrape(project)

    run = run_scrape(project, max_pages=1)

    assert run.gone_marked == 0


def test_a_run_with_errors_marks_nothing_gone(project, tmp_path):
    run_scrape(project)
    (tmp_path / "pages" / "category-60-2.html").unlink()

    run = run_scrape(project)

    assert run.errors >= 1
    assert run.gone_marked == 0


def test_too_many_missing_listings_stop_the_marking(project, tmp_path):
    project.data["scrape"]["max_gone_percent"] = 10
    run_scrape(project)
    page = tmp_path / "pages" / FEED_PAGE
    page.write_text(page.read_text(encoding="utf-8").replace("24100001", "99100001"),
                    encoding="utf-8")

    run = run_scrape(project)

    assert run.gone_marked == 0
    assert run.errors >= 1
    assert "max_gone_percent" in run.notes
    database = opened(project)
    assert database.get_listing("24100001").status == "active"
    database.close()


def test_a_zero_threshold_forbids_marking_instead_of_allowing_everything():
    """Порог 0 — это «пропало хоть что-то, значит сбой», а не «предохранителя нет».
    Выключается порог значением null, а не нулём."""
    assert gone_refusal(missing=1, active_total=1000, max_percent=0) is not None


def test_a_null_threshold_turns_the_guard_off():
    assert gone_refusal(missing=999, active_total=1000, max_percent=None) is None


def test_a_config_ceiling_does_not_switch_off_marking(project, tmp_path):
    """`max_pages` в конфиге — потолок окружения, а не «человек укоротил обход».
    Пока каждый прогон partial, снятых не помечает никто и мерки полноты нет."""
    project.data["scrape"]["max_pages"] = 2      # ровно лента фикстуры

    run = run_scrape(project)

    assert run.mode == "full"


def test_a_ceiling_asked_for_on_the_command_line_still_marks_nothing(project, tmp_path):
    """Обратная дыра: `--max-pages` — это «человек укоротил обход», и такой
    прогон всей ленты не видел, значит помечать снятых ему нельзя."""
    run_scrape(project)
    page = tmp_path / "pages" / FEED_PAGE
    page.write_text(page.read_text(encoding="utf-8").replace("24100001", "99100001"),
                    encoding="utf-8")

    run = run_scrape(project, max_pages=2)

    assert run.mode == "partial"
    assert run.gone_marked == 0
