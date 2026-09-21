"""БЛОКЕР 4: в хранилище уезжает снимок базы, а не файл посреди WAL.

База живёт в режиме WAL: свежие записи лежат в соседнем файле `-wal`, пока
их не перенесли в основной. Обычно перенос делает закрытие последнего
соединения, но пока базу держит открытой кто-то ещё — выгрузка, вторая
консоль, — этого не происходит, и скопированный основной файл не содержит
последнего прогона. Поэтому заливается снимок `VACUUM INTO`: он всегда целен.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from listam.crawler import run_scrape
from listam.wiring import database_path

from tests.test_crawler import card, feed, project  # noqa: F401


def healthy(project) -> None:
    feed(
        project,
        "".join(card(i) for i in range(101, 107)),
        "".join(card(i) for i in range(201, 205)),
    )


def rows_in(path: Path, query: str) -> int:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return int(connection.execute(query).fetchone()[0])
    finally:
        connection.close()


def test_uploaded_copy_holds_the_run_that_is_still_in_the_wal(project, tmp_path):
    healthy(project)
    run_scrape(project)
    local = database_path(project)

    # Второй читатель держит базу открытой — перенос WAL при закрытии не случится.
    reader = sqlite3.connect(local)
    reader.execute("SELECT COUNT(*) FROM runs").fetchone()
    try:
        run_scrape(project)
    finally:
        reader.close()

    remote = Path(project.get("storage.directory")) / "listam.sqlite"
    copy = tmp_path / "checked.sqlite"
    copy.write_bytes(remote.read_bytes())
    assert rows_in(copy, "SELECT COUNT(*) FROM runs") == 2
    assert not (remote.parent / "listam.sqlite-wal").exists()
    assert not (remote.parent / "listam.sqlite-shm").exists()
