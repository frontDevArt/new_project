"""Масштаб подбора. Тест не про скорость, а про число обращений к базе:
секунды на разных машинах разные, а три чтения одной таблицы — везде три."""
from __future__ import annotations

import pytest

from listam.adapters.db_sqlite import SqliteDatabase
from listam.matching import run_match

from tests.contracts.test_database_contract import make_request
from tests.test_matching import cfg, fill, suitable


@pytest.fixture
def matching_config(tmp_path):
    """Дюжина подходящих объявлений, каждое на своей улице — дюжина матчей.

    Три штуки, как у `tests/test_matching.py`, для этого файла мало: прогон
    тратит три транзакции на служебное (кластеры, отметка подбора, сама
    запись), и «транзакций меньше, чем матчей» на трёх матчах неразличимо
    даже при записи по одной строке за раз.
    """
    config = cfg(tmp_path)
    fill(config,
         listings=[suitable(str(number), price_usd=100_000.0 + number * 1_000)
                   for number in range(12)],
         requests=[make_request("R-1")])
    return config


def test_one_matching_reads_the_listings_table_once(matching_config, monkeypatch):
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


def test_matches_are_written_in_batches_and_not_one_transaction_each(
        matching_config, monkeypatch):
    """Граница транзакции, а не скорость: на боевых числах одна транзакция
    на строку — это 67 000 фиксаций и минута прогона.

    Считается `transaction()`, а не строки SQL: `sqlite3.Connection` —
    неизменяемый тип, подменить у него `execute` нельзя, а BEGIN выполняется
    только здесь.
    """
    begins: list[str] = []
    original = SqliteDatabase.transaction

    def counted(self):
        begins.append("BEGIN")
        return original(self)

    monkeypatch.setattr(SqliteDatabase, "transaction", counted)
    report = run_match(matching_config)

    assert report.new > 1, "тест бессмыслен, если матч один"
    assert len(begins) < report.new, (
        f"{len(begins)} транзакций на {report.new} матчей: на боевых 67 000 "
        f"строках это минута записи вместо секунд"
    )


def test_the_window_does_not_read_listings_one_by_one(matching_config, monkeypatch):
    """На 50 заявках это были 66 910 отдельных запросов за одну витрину."""
    run_match(matching_config)
    from listam.matching import collect_matches

    calls: list[str] = []
    original = SqliteDatabase.get_listing

    def counted(self, listing_id):
        calls.append(listing_id)
        return original(self, listing_id)

    monkeypatch.setattr(SqliteDatabase, "get_listing", counted)
    rows = collect_matches(matching_config)

    assert len(rows) > 1, "тест бессмыслен на одной строке"
    assert calls == [], (
        f"{len(calls)} отдельных get_listing на {len(rows)} матчей: "
        f"на боевых числах это 66 910 запросов"
    )
