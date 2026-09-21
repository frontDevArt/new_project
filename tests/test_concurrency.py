"""M3: база переживает второго читателя, а прогон — второго себя.

`export` во время прогона давал «database is locked», а два одновременных
`scrape` — гонку, в которой побеждал тот, кто позже залил файл в хранилище.
"""
from __future__ import annotations

import sqlite3

import pytest

from listam.adapters.db_sqlite import SqliteDatabase
from listam.adapters.run_lock import LockBusy, RunLock
from listam.wiring import build_database

from tests.test_crawler import project, three_pages  # noqa: F401  (фикстура прогона на фикстурах)


def opened(path, **over) -> SqliteDatabase:
    database = SqliteDatabase(path, **over)
    database.connect()
    database.migrate()
    return database


def test_database_runs_in_wal_mode(tmp_path):
    database = opened(tmp_path / "test.sqlite")

    mode = database.conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode.lower() == "wal"
    database.close()


def test_busy_timeout_comes_from_the_caller(tmp_path):
    database = opened(tmp_path / "test.sqlite", busy_timeout_ms=7000)

    assert database.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 7000
    database.close()


def test_a_reader_is_not_locked_out_by_a_writer(tmp_path):
    """Выгрузка во время прогона обязана читать, а не падать с «database is locked»."""
    path = tmp_path / "test.sqlite"
    writer = opened(path)
    writer.conn.execute("BEGIN IMMEDIATE")
    writer.conn.execute(
        "INSERT INTO listings (id, url, first_seen, last_seen) VALUES ('1', 'u', 'n', 'n')"
    )

    reader = SqliteDatabase(path, busy_timeout_ms=200)
    reader.connect()
    assert reader.count_listings() == 0      # запись ещё не зафиксирована — но читается
    reader.close()

    writer.conn.execute("COMMIT")
    writer.close()


def test_busy_timeout_from_config(tmp_path):
    from listam.config import Config

    config = Config(
        {"storage": {"work_dir": str(tmp_path), "db_filename": "listam.sqlite",
                     "busy_timeout_ms": 12000}},
        env="test",
        path=tmp_path / "test.yaml",
    )
    database = build_database(config)
    database.connect()

    assert database.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 12000
    database.close()


# --- замок: одновременно идёт только один прогон ---

def test_second_run_cannot_take_the_lock(tmp_path):
    first = RunLock(tmp_path / "scrape.lock")
    first.acquire()

    with pytest.raises(LockBusy):
        RunLock(tmp_path / "scrape.lock").acquire()

    first.release()
    RunLock(tmp_path / "scrape.lock").acquire().release()


def test_lock_releases_itself_on_the_way_out(tmp_path):
    with RunLock(tmp_path / "scrape.lock"):
        pass

    assert not (tmp_path / "scrape.lock").exists()


def test_a_stale_lock_does_not_block_forever(tmp_path):
    """Прогон убили по-жёсткому — замок остался. Он протухает, а не держит вечно."""
    lock = tmp_path / "scrape.lock"
    RunLock(lock).acquire()

    taken = RunLock(lock, stale_after_seconds=0).acquire()

    assert taken is not None
    taken.release()


def test_lock_survives_a_missing_directory(tmp_path):
    lock = RunLock(tmp_path / "нет-такой-папки" / "scrape.lock")
    lock.acquire()
    lock.release()


# --- ВЫСОКИЙ 8: замок не имеет права пережить прогон ---

def test_lock_is_released_when_the_database_file_is_unreachable(project, monkeypatch):  # noqa: F811
    """Замок берётся до сверки копий. Падение сверки не должно запирать папку насовсем."""
    import listam.crawler as crawler
    from listam.crawler import run_scrape
    from listam.wiring import run_lock_path

    def refuse(*args, **kwargs):
        raise PermissionError("[Errno 13] отказано в доступе: 'listam.sqlite'")

    monkeypatch.setattr(crawler, "take_the_fresher_copy", refuse)

    run = run_scrape(project)

    assert run.errors >= 1
    assert "файл базы недоступен" in (run.notes or "")
    assert not run_lock_path(project).exists()

    monkeypatch.undo()
    again = run_scrape(project)
    assert again.errors == 0
    assert again.pages_fetched == 2


def test_a_long_run_keeps_its_lock_alive(project, monkeypatch):  # noqa: F811
    """Прогон длиннее срока замка не имеет права отдать его второму процессу.

    215 страниц с паузой между запросами — это часы. Замок без сердцебиения
    протухает прямо под живым прогоном, и второй `scrape` уходит в ту же ленту.
    """
    import time

    import listam.crawler as crawler
    from listam.adapters.fetcher_files import FilesFetcher
    from listam.crawler import run_scrape
    from listam.wiring import run_lock_path

    project.data["storage"]["lock_stale_seconds"] = 1
    three_pages(project)
    verdicts = []

    class Slow:
        """Тот же чтец страниц, но каждая страница даётся ему не мгновенно."""

        def __init__(self, inner):
            self.inner = inner
            self.pages = 0

        def get(self, url: str) -> str:
            time.sleep(0.7)
            self.pages += 1
            if self.pages == 3:       # с начала прогона прошло больше срока замка
                try:
                    RunLock(run_lock_path(project), stale_after_seconds=1).acquire()
                    verdicts.append("перехватил")
                except LockBusy:
                    verdicts.append("не дали")
            return self.inner.get(url)

        def close(self) -> None:
            self.inner.close()

    monkeypatch.setattr(
        crawler,
        "build_fetcher",
        lambda config: Slow(FilesFetcher(directory=config.get("scrape.pages_dir"))),
    )

    run_scrape(project)

    assert verdicts == ["не дали"]
