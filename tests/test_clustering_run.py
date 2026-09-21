"""`listam cluster`: пересчёт кластеров по базе.

Сводка здесь не украшение: число кластеров без числа объявлений без улицы
выглядит подозрительно большим, и человек начинает искать несуществующую
поломку вместо того, чтобы читать её как дырку в данных (решение 4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from listam.clustering_run import run_clustering
from listam.config import Config
from listam.wiring import build_database

from tests.contracts.test_database_contract import make_listing

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


def cfg(tmp_path: Path, **over) -> Config:
    data = {
        "env": "test",
        "storage": {
            "kind": "local",
            "directory": str(tmp_path / "remote"),
            "work_dir": str(tmp_path / "work"),
            "db_filename": "listam.sqlite",
        },
    }
    data.update(over)
    return Config(data, env="test", path=Path("config/test.yaml"))


def fill(config: Config, listings) -> None:
    database = build_database(config)
    database.connect()
    database.migrate()
    try:
        for listing in listings:
            database.upsert_listing(listing, seen_at=NOW)
    finally:
        database.close()


def cluster_ids(config: Config) -> dict[str, str | None]:
    database = build_database(config)
    database.connect()
    try:
        return {item.id: item.cluster_id for item in database.iter_listings()}
    finally:
        database.close()


@pytest.fixture
def tmp_config_with_db(tmp_path):
    config = cfg(tmp_path)
    fill(config, [
        # Одна квартира у трёх агентств.
        make_listing("1", price_usd=132_000.0, area=85.0),
        make_listing("2", price_usd=139_000.0, area=86.0),
        make_listing("3", price_usd=128_000.0, area=87.0),
        # Та же улица, другой этаж — другая квартира.
        make_listing("4", floor=7),
        # Без улицы: кластер из самого себя.
        make_listing("5", street=None),
        make_listing("6", street=None),
    ])
    return config


def test_clustering_writes_ids_and_counts_the_lonely_ones(tmp_config_with_db):
    report = run_clustering(tmp_config_with_db)

    assert report.listings == 6
    assert report.clusters == 4          # тройка, одиночный этаж и двое без улицы
    assert report.multi == 1
    assert report.largest == 3
    assert report.without_street == 2
    assert report.changed == report.listings   # первый прогон проставляет всем
    assert report.errors == 0


def test_the_ids_land_in_the_database(tmp_config_with_db):
    run_clustering(tmp_config_with_db)
    stored = cluster_ids(tmp_config_with_db)

    assert all(value for value in stored.values())
    assert stored["1"] == stored["2"] == stored["3"]
    assert stored["5"] != stored["6"]
    assert stored["4"] not in {stored["1"], stored["5"], stored["6"]}


def test_running_it_twice_changes_nothing(tmp_config_with_db):
    run_clustering(tmp_config_with_db)
    assert run_clustering(tmp_config_with_db).changed == 0


def test_gone_and_flagged_listings_stay_out_of_the_count(tmp_path):
    config = cfg(tmp_path)
    fill(config, [make_listing("1"),
                  make_listing("2", anomaly="цена за метр вне порога")])
    database = build_database(config)
    database.connect()
    database.mark_gone(["1"], NOW)
    database.close()

    report = run_clustering(config)

    assert report.listings == 0
    assert report.clusters == 0
    assert report.largest == 0


def test_the_tolerance_is_read_from_the_config(tmp_path):
    # Порог живёт в конфиге, а не в коде: ноль допуска — это ноль,
    # и две площади в метре друг от друга остаются разными квартирами.
    config = cfg(tmp_path, match={"cluster": {"area_tolerance": 0}})
    fill(config, [make_listing("1", area=85.0), make_listing("2", area=86.0)])

    assert run_clustering(config).clusters == 2


def test_a_broken_database_is_an_error_and_not_a_crash(tmp_path):
    config = cfg(tmp_path)
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "listam.sqlite").write_text("это не база", encoding="utf-8")

    report = run_clustering(config)

    assert report.errors == 1
    assert report.notes
