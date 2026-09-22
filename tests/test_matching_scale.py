"""Масштаб подбора. Тест не про скорость, а про число обращений к базе:
секунды на разных машинах разные, а четыре чтения одной таблицы — везде четыре."""
from __future__ import annotations

from listam.adapters.db_sqlite import SqliteDatabase
from listam.matching import run_match

from tests.test_matching import matching_config  # noqa: F401  (фикстура)


def test_one_matching_reads_the_listings_table_once(matching_config, monkeypatch):  # noqa: F811
    calls: list[str] = []
    original = SqliteDatabase.listings_for_matching

    def counted(self, since=None):
        calls.append("since" if since is not None else "вся")
        return original(self, since)

    monkeypatch.setattr(SqliteDatabase, "listings_for_matching", counted)
    run_match(matching_config)

    assert len(calls) <= 1, (
        f"вся таблица объявлений прочитана {len(calls)} раза: {calls}. "
        f"На боевых 20 826 строках каждое чтение — больше секунды"
    )
