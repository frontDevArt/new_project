"""БЛОКЕР 1: прогон с ошибкой не имеет права заливать базу в хранилище.

Заливка — это подмена общей копии, которой пользуются все. Прогон, у которого
уехала вёрстка или оборвался обход, записал в базу неполную или неверную
картину; залить её означает испортить то, что лежало в хранилище и было в
порядке. Поэтому любая ошибка прогона отменяет и ротацию, и заливку, а
локальная копия остаётся на диске — посмотреть её глазами.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from listam.crawler import run_scrape
from listam.wiring import run_lock_path

from tests.test_crawler import card, feed, opened, project  # noqa: F401  (фикстура и помощники)


def remote_db(project) -> Path:
    return Path(project.get("storage.directory")) / "listam.sqlite"


def healthy(project) -> None:
    feed(
        project,
        "".join(card(i) for i in range(101, 107)),
        "".join(card(i) for i in range(201, 205)),
    )


def layout_moved(project) -> None:
    """Та же лента, но класс района вырезан — ровно как переименование на сайте."""
    project.data["coverage"] = {"min_sample": 1, "min_filled": {"district": 0.99}}
    feed(
        project,
        "".join(card(i, district="") for i in range(101, 107)),
        "".join(card(i, district="") for i in range(201, 205)),
    )


def test_run_with_errors_leaves_the_copy_in_storage_alone(project):
    healthy(project)
    run_scrape(project)
    before = remote_db(project).read_bytes()

    layout_moved(project)
    run = run_scrape(project)

    assert run.errors == 1
    assert remote_db(project).read_bytes() == before
    assert "база не залита" in (run.notes or "")


def test_the_flag_puts_the_upload_back(project):
    healthy(project)
    run_scrape(project)
    before = remote_db(project).read_bytes()

    layout_moved(project)
    run = run_scrape(project, allow_upload_with_errors=True)

    assert run.errors == 1
    assert remote_db(project).read_bytes() != before


def test_a_clean_run_still_uploads(project):
    healthy(project)

    run = run_scrape(project)

    assert run.errors == 0
    assert remote_db(project).exists()
    assert "база не залита" not in (run.notes or "")


def test_the_cli_knows_the_flag():
    from listam.cli import build_parser

    args = build_parser().parse_args(["scrape", "--allow-upload-with-errors"])

    assert args.allow_upload_with_errors is True


# --- БЛОКЕР 2: падение прогона не притворяется, что его не было ---

def exploding(monkeypatch, exception: BaseException):
    """Подменяет разбор страницы так, что вторая страница роняет прогон."""
    import listam.crawler

    real = listam.crawler.parse_listing_cards
    seen = {"pages": 0}

    def parse(*args, **kwargs):
        seen["pages"] += 1
        if seen["pages"] >= 2:
            raise exception
        return real(*args, **kwargs)

    monkeypatch.setattr(listam.crawler, "parse_listing_cards", parse)


def test_a_crash_in_the_middle_lands_in_the_journal(project, monkeypatch):
    healthy(project)
    run_scrape(project)
    before = remote_db(project).read_bytes()
    exploding(monkeypatch, MemoryError("не хватило памяти на разбор страницы"))

    with pytest.raises(MemoryError):
        run_scrape(project)

    database = opened(project)
    last = database.last_run()
    database.close()
    assert last.errors >= 1
    assert "MemoryError" in (last.notes or "")
    assert "не хватило памяти на разбор страницы" in last.notes
    assert remote_db(project).read_bytes() == before


def test_an_interrupted_run_says_so_and_frees_the_lock(project, monkeypatch):
    healthy(project)
    exploding(monkeypatch, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_scrape(project)

    database = opened(project)
    last = database.last_run()
    database.close()
    assert last.errors >= 1
    assert "прогон прерван" in (last.notes or "")
    assert not run_lock_path(project).exists()
