"""Витрина дельты: что принёс последний прогон.

Сайт здесь не при чём: лента — те же две сохранённые страницы, что и в
`tests/test_crawler.py`. «Пришло», «подешевело» и «пропало» изображаются
правкой файлов страниц.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from listam.adapters.db_sqlite import latest_schema_version
from listam.changes import render, run_changes
from listam.config import Config
from listam.crawler import run_scrape
from listam.wiring import database_path

FIXTURES = Path(__file__).parent / "fixtures"
RATE = 400.0

# Объявление 23973917 лежит на ПЕРВОЙ странице фикстуры и стоит `$ 162,000` —
# смена цены изображается правкой именно её. Цена `290,000` с той же страницы
# лежит в блоке «Топ объявления» и в разбор не попадает.
PRICED_PAGE = "category-60.html"
PRICED_ID = "23973917"

# Объявление 24100001 лежит на ВТОРОЙ странице — «пропало с ленты» и «пришло
# новое» изображаются подменой его идентификатора.
FEED_PAGE = "category-60-2.html"


@pytest.fixture
def project(tmp_path) -> Config:
    """Лента — две сохранённые страницы; порог снятых поднят под размер фикстуры.

    В фикстуре всего 8 объявлений, и одно пропавшее — это 12.5%: при боевом
    пороге 10 не сработала бы ни одна пометка.
    """
    pages = tmp_path / "pages"
    pages.mkdir()
    shutil.copyfile(FIXTURES / "category-60-page1.html", pages / PRICED_PAGE)
    shutil.copyfile(FIXTURES / "category-60-page2.html", pages / FEED_PAGE)
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


def _edit(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def _roll_schema_back_to(path, version: int) -> None:
    """Делает вид, что база отстала на версию: сносит отметки старше нужной.

    Колонки при этом остаются — проверяем именно отказ по версии, а не падение
    запроса. Так же ведёт себя настоящая база, которую не домигрировали.
    """
    connection = sqlite3.connect(path)
    try:
        connection.execute("DELETE FROM schema_version WHERE version > ?", (version,))
        connection.commit()
    finally:
        connection.close()


def test_changes_since_the_last_run_show_what_it_brought(project, tmp_path):
    run_scrape(project)
    _edit(tmp_path / "pages" / FEED_PAGE, "24100001", "99100001")
    _edit(tmp_path / "pages" / PRICED_PAGE, "162,000", "153,000")
    run_scrape(project)

    report = run_changes(project)

    assert [item.id for item in report.new] == ["99100001"]
    assert [move.listing.id for move in report.moved] == [PRICED_ID]
    assert (report.moved[0].was, report.moved[0].now) == (162_000.0, 153_000.0)
    assert [item.id for item in report.gone] == ["24100001"]


def test_a_repeat_run_over_an_unchanged_feed_shows_no_changes(project):
    """Критерий M1 с другой стороны: лента та же — предъявлять нечего.

    Окошком «за N часов» это не проверить: прогон по фикстуре укладывается
    в доли секунды, и любое окно накрыло бы его целиком.
    """
    run_scrape(project)
    run_scrape(project)

    report = run_changes(project)

    assert (report.new, report.moved, report.gone) == ([], [], [])
    assert "изменений нет" in render(report, limit=50)


def test_the_window_can_be_asked_for_in_hours(project):
    run_scrape(project)

    report = run_changes(project, hours=24)

    assert "за последние 24 ч" in report.since_note
    assert len(report.new) == 8


def test_render_shows_the_direction_of_the_price_move(project, tmp_path):
    run_scrape(project)
    _edit(tmp_path / "pages" / PRICED_PAGE, "162,000", "153,000")
    run_scrape(project)

    text = render(run_changes(project), limit=50)

    assert "было $162,000 → стало $153,000" in text
    assert "−5.6%" in text


def test_a_long_section_is_cut_and_says_how_much_was_left_out(project, tmp_path):
    """Лимит режет раздел, а не врёт про его размер: обрезано — сказано, сколько."""
    run_scrape(project)
    _edit(tmp_path / "pages" / FEED_PAGE, "24100001", "99100001")
    _edit(tmp_path / "pages" / FEED_PAGE, "24100002", "99100002")
    run_scrape(project)

    report = run_changes(project)
    text = render(report, limit=1)

    assert len(report.new) == 2
    assert "…и ещё 1" in text


def test_changes_do_not_migrate_the_database(project, tmp_path):
    """`changes` читает базу, а не чинит её: молчаливая правка общей базы
    по дороге к списку — это то же, за что в M0 отучили `export`."""
    run_scrape(project)
    behind = latest_schema_version() - 1
    _roll_schema_back_to(database_path(project), behind)

    report = run_changes(project)

    assert report.errors == 1
    assert f"схема базы {behind}" in report.notes


def test_gone_survive_an_incremental_run(project, tmp_path):
    """Снятых ставит только полный обход, а `--fresh` ходит каждый час.
    Если окно двигает любой прогон, раздел «Снято» человек не увидит никогда."""
    run_scrape(project)
    _edit(tmp_path / "pages" / FEED_PAGE, "24100001", "99100001")
    run_scrape(project)                       # полный: 24100001 ушёл с ленты и помечен

    run_scrape(project, fresh=True)           # обычный час спустя

    report = run_changes(project)

    assert [item.id for item in report.gone] == ["24100001"]


def test_a_full_run_resets_the_gone_section(project, tmp_path):
    """Следующий полный обход — новая мерка: снятые прошлого в список не тянутся."""
    run_scrape(project)
    _edit(tmp_path / "pages" / FEED_PAGE, "24100001", "99100001")
    run_scrape(project)
    run_scrape(project)                       # следующий полный: мерка сдвинулась

    assert run_changes(project).gone == []


def test_without_a_full_run_the_gone_window_falls_back_and_says_so(project):
    """Журнал без полных прогонов: мерки для снятых нет — берём общую и говорим об этом."""
    run_scrape(project, fresh=True)

    report = run_changes(project)

    assert report.gone_since == report.since
    assert "полных прогонов" in report.gone_note


def test_render_explains_the_second_yardstick_under_the_gone_section(project, tmp_path):
    """Мерки разошлись — человеку сказано, с какого обхода считаны снятые."""
    run_scrape(project)
    _edit(tmp_path / "pages" / FEED_PAGE, "24100001", "99100001")
    run_scrape(project)
    run_scrape(project, fresh=True)

    text = render(run_changes(project), limit=50)

    assert "снятые — с полного обхода 2" in text


def test_the_price_counter_counts_listings_and_matches_the_journal(project, tmp_path):
    """Счётчик «Сменили цену» считает объявления, а не точки истории, —
    и тогда он сходится с `runs.price_changed`. Это боевая пара 69/68."""
    run_scrape(project)
    _edit(tmp_path / "pages" / PRICED_PAGE, "162,000", "153,000")
    run = run_scrape(project)

    report = run_changes(project)

    assert [move.listing.id for move in report.moved] == [PRICED_ID]
    assert len(report.moved) == run.price_changed
