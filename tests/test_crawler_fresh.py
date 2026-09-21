"""Инкрементальный обход `--fresh`: где он останавливается и почему.

Сайт здесь не при чём: лента — те же две сохранённые страницы, что и в
`tests/test_crawler.py`.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from listam.adapters.db_sqlite import SqliteDatabase
from listam.config import Config
from listam.crawler import incremental_stop, run_scrape
from listam.wiring import database_path


def test_incremental_walks_on_while_new_listings_keep_coming():
    assert incremental_stop(pages_without_new=1, threshold=2,
                            pages_fetched=1, ceiling=20) == (None, False)


def test_incremental_stops_after_the_configured_number_of_known_pages():
    reason, is_error = incremental_stop(pages_without_new=2, threshold=2,
                                        pages_fetched=2, ceiling=20)
    assert "2 страниц подряд без новых" in reason
    assert is_error is False


def test_hitting_the_ceiling_is_a_failure_not_a_finish():
    """Потолок значит, что до известных объявлений обход не дошёл: часть ленты
    он не видел, и молча считать такой прогон удачным нельзя."""
    reason, is_error = incremental_stop(pages_without_new=0, threshold=2,
                                        pages_fetched=20, ceiling=20)
    assert "fresh_max_pages" in reason
    assert is_error is True


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
                "min_cards_per_page": 3,          # фикстура — не боевая лента, порог свой
                "fresh_stop_after_known_pages": 1,  # фикстура — две страницы, порог свой
                "fresh_max_pages": 10,
            },
        },
        env="test",
        path=tmp_path / "config" / "test.yaml",
    )


def opened(config: Config) -> SqliteDatabase:
    database = SqliteDatabase(database_path(config))
    database.connect()
    return database


def test_fresh_run_after_a_full_one_stops_on_the_first_known_page(project):
    run_scrape(project)                      # полный обход: 2 страницы, 8 объявлений

    run = run_scrape(project, fresh=True)

    assert run.pages_fetched == 1
    assert run.new_listings == 0
    assert run.errors == 0
    assert run.mode == "fresh"
    assert "1 страниц подряд без новых объявлений" in run.stop_reason


def test_fresh_run_picks_up_a_genuinely_new_listing(project, tmp_path):
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8").replace("23987063", "99100001"),
                    encoding="utf-8")

    run = run_scrape(project, fresh=True)

    assert run.new_listings == 1
    database = opened(project)
    assert database.get_listing("99100001") is not None
    database.close()


def test_fresh_run_that_never_reached_known_listings_is_a_failure(project):
    """База пуста: новое на каждой странице. Такой обход обязан упереться
    в потолок и сказать, что полной картины он не собрал."""
    project.data["scrape"]["fresh_max_pages"] = 1

    run = run_scrape(project, fresh=True)

    assert run.errors == 1
    assert "fresh_max_pages" in run.notes


def test_fresh_run_is_not_accused_of_walking_too_few_pages(project):
    """Проверка недобора страниц из M0 к инкрементальному обходу не применяется:
    он укорочен нарочно."""
    run_scrape(project)
    project.data["scrape"]["expected_pages_min"] = 2

    run = run_scrape(project, fresh=True)

    assert run.errors == 0


def test_fresh_run_does_not_become_the_yardstick(project, tmp_path):
    run_scrape(project)                      # полный: 2 страницы
    run_scrape(project, fresh=True)          # инкрементальный: 1 страница

    (tmp_path / "pages" / "category-60-2.html").unlink()
    run = run_scrape(project)

    assert "прошлый удачный прогон прошёл 2" in run.notes


def test_resume_continues_the_interrupted_full_crawl_not_the_fresh_one(project, tmp_path):
    """Между прерванным полным обходом и `--resume` мог пройти инкрементальный.
    Продолжать надо полный: иначе `--resume` пойдёт со второй страницы вместо
    сто седьмой и отчитается успехом."""
    run_scrape(project, max_pages=1)         # обход, который дальше оборвали
    database = opened(project)
    database.conn.execute(
        "UPDATE runs SET mode = 'full', finished_at = NULL, errors = 1, last_page = 7"
    )
    database.conn.commit()
    database.close()
    run_scrape(project, fresh=True)          # между ними — инкрементальный прогон

    run = run_scrape(project, resume=True)

    assert "обход продолжен со страницы 8" in run.notes


def test_fresh_run_records_a_price_change(project, tmp_path):
    run_scrape(project)
    page = tmp_path / "pages" / "category-60.html"
    page.write_text(page.read_text(encoding="utf-8").replace("162,000", "155,000"),
                    encoding="utf-8")

    run = run_scrape(project, fresh=True)

    assert run.price_changed == 1


def test_a_zero_stop_threshold_is_not_an_endless_crawl():
    """Порог 0 страниц без новых — это «встань на первой же такой странице»."""
    assert incremental_stop(pages_without_new=0, threshold=0,
                            pages_fetched=1, ceiling=20)[0] is not None


def test_a_null_stop_threshold_turns_the_stop_off():
    """Выключается порог значением null: обход идёт до потолка."""
    assert incremental_stop(pages_without_new=9, threshold=None,
                            pages_fetched=1, ceiling=20) == (None, False)
