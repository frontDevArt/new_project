"""БЛОКЕР 3: недобор страниц — это сбой, а не короткий прогон.

Лента категории 60 — это 215 страниц. Прогон, который прошёл две и остановился,
потому что на странице не оказалось пагинатора, записывает в базу два процента
рынка и до сих пор считался удачным: errors = 0, база уезжает в хранилище.
Полнота обхода проверяется двумя порогами — явным числом страниц из конфига
и падением относительно прошлого удачного прогона.
"""
from __future__ import annotations

from pathlib import Path

from listam.cli import _scrape
from listam.crawler import pages_shortfall, run_scrape

from tests.test_crawler import card, opened, page, paginator, project  # noqa: F401


def feed_with_a_dead_paginator(project, pages: int = 9, dead: int = 2) -> None:
    """Лента на `pages` страниц, у страницы `dead` вырезан блок пагинации."""
    directory = Path(project.get("scrape.pages_dir"))
    for old in directory.glob("*.html"):
        old.unlink()
    for number in range(1, pages + 1):
        name = "category-60.html" if number == 1 else f"category-60-{number}.html"
        cards = "".join(card(number * 100 + n) for n in range(1, 7))
        links = "" if number == dead else paginator(number, pages)
        (directory / name).write_text(page(cards, links), encoding="utf-8")


def whole_feed(project, pages: int = 9) -> None:
    feed_with_a_dead_paginator(project, pages=pages, dead=0)


def test_a_run_that_stopped_halfway_is_an_error(project):
    project.data["scrape"]["expected_pages_min"] = 9
    feed_with_a_dead_paginator(project)

    run = run_scrape(project)

    assert run.pages_fetched == 2
    assert run.errors >= 1
    assert "обход оборвался" in (run.notes or "")


def test_the_command_returns_one_when_the_crawl_is_short(project, capsys):
    project.data["scrape"]["expected_pages_min"] = 9
    feed_with_a_dead_paginator(project)

    assert _scrape(project, max_pages=None, dry_run=False) == 1


def test_a_drop_against_the_last_successful_run_is_an_error(project):
    project.data["scrape"]["max_pages_drop_percent"] = 20
    whole_feed(project)
    first = run_scrape(project)
    assert first.pages_fetched == 9 and first.errors == 0

    feed_with_a_dead_paginator(project)
    second = run_scrape(project)

    assert second.errors >= 1
    assert "обход оборвался" in (second.notes or "")


def test_max_pages_and_resume_do_not_switch_the_check_on(project):
    project.data["scrape"]["expected_pages_min"] = 9
    project.data["scrape"]["max_pages_drop_percent"] = 20
    whole_feed(project)
    run_scrape(project)

    short = run_scrape(project, max_pages=2)
    resumed = run_scrape(project, resume=True)

    assert short.pages_fetched == 2 and short.errors == 0
    assert "обход оборвался" not in (short.notes or "")
    assert "обход оборвался" not in (resumed.notes or "")


def test_pages_shortfall_keeps_quiet_when_there_is_nothing_to_compare_with():
    assert pages_shortfall(2, None, None, 20) is None
    assert pages_shortfall(9, 9, 9, 20) is None
    assert pages_shortfall(8, None, 9, 20) is None      # падение 11% — в пределах порога
