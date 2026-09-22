"""`listam match`: подбор объявлений под заявки.

Матчинг — это место, где сходятся все три домена M2: заявка задаёт рамку,
кластер решает, какую из тридцати одинаковых карточек показать, скоринг
ставит балл. Здесь проверяется оркестрация, а не арифметика: что выборка
взята верно, что представитель кластера один, что пересчёт не плодит
дубли и что бессмысленная выборка названа словами.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from listam.config import Config
from listam.matching import run_match
from listam.wiring import build_database

from tests.contracts.test_database_contract import make_listing, make_request

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
YESTERDAY = NOW - timedelta(days=1)


def cfg(tmp_path: Path, **over) -> Config:
    data = {
        "env": "test",
        "storage": {
            "kind": "local",
            "directory": str(tmp_path / "remote"),
            "work_dir": str(tmp_path / "work"),
            "db_filename": "listam.sqlite",
        },
        "match": {
            "weights": {"budget": 30, "district": 20, "price_per_sqm": 20,
                        "area_rooms": 15, "floor": 10, "seller_type": 5},
            "thresholds": {"hot": 70, "digest": 40},
            "budget_stretch_percent": 10,
            "cluster": {"area_tolerance": 2},
        },
    }
    data.update(over)
    return Config(data, env="test", path=Path("config/test.yaml"))


def fill(config: Config, listings=(), requests=(), seen_at=NOW) -> None:
    database = build_database(config)
    database.connect()
    database.migrate()
    try:
        for listing in listings:
            database.upsert_listing(listing, seen_at=seen_at)
        for request in requests:
            database.upsert_request(request, seen_at)
    finally:
        database.close()


def matches_of(config: Config, external_id="R-1"):
    database = build_database(config)
    database.connect()
    try:
        request = database.get_request(external_id)
        return database.matches_for_request(request.id)
    finally:
        database.close()


def suitable(listing_id, **over):
    """Объявление, которое заявке из `make_request` подходит целиком."""
    fields = dict(district="Кентрон", street=f"улица {listing_id}", rooms=3,
                  area=85.0, floor=4, floors_total=9, price_usd=110_000.0,
                  price_per_sqm=1294.0, seller_type="owner")
    fields.update(over)
    return make_listing(listing_id, **fields)


@pytest.fixture
def matching_config(tmp_path):
    config = cfg(tmp_path)
    fill(config,
         listings=[suitable("1"), suitable("2", price_usd=118_000.0),
                   suitable("3", price_usd=95_000.0)],
         requests=[make_request("R-1")])
    return config


@pytest.fixture
def matching_config_with_duplicates(tmp_path):
    config = cfg(tmp_path)
    # Одна квартира у трёх агентств: тот же дом, тот же этаж, обмеры в пределах
    # допуска. Клиенту она показывается один раз, по самой дешёвой карточке.
    same = dict(district="Кентрон", street="ул. Туманяна", rooms=3, floor=4,
                floors_total=9, seller_type="agency")
    fill(config,
         listings=[make_listing("cheap", area=85.0, price_usd=119_000.0, **same),
                   make_listing("mid", area=86.0, price_usd=125_000.0, **same),
                   make_listing("dear", area=87.0, price_usd=130_000.0, **same)],
         requests=[make_request("R-1")])
    return config


@pytest.fixture
def matching_config_with_two_runs(tmp_path):
    config = cfg(tmp_path)
    fill(config, listings=[suitable("old-1"), suitable("old-2")], seen_at=YESTERDAY,
         requests=[make_request("R-1")])
    database = build_database(config)
    database.connect()
    try:
        run_id = database.start_run(NOW - timedelta(minutes=30), rate_amd_per_usd=385.0)
        database.upsert_listing(suitable("new-1"), seen_at=NOW)
        database.upsert_listing(suitable("new-2"), seen_at=NOW)
        database.finish_run(run_id, NOW, listings_seen=2, new_listings=2)
    finally:
        database.close()
    return config


@pytest.fixture
def matching_config_with_paused_request(tmp_path):
    config = cfg(tmp_path)
    fill(config, listings=[suitable("1")],
         requests=[make_request("R-1", status="paused")])
    return config


def test_a_new_request_is_matched_against_the_whole_base(matching_config):
    report = run_match(matching_config, external_id="R-1")
    assert report.requests == 1
    assert report.new > 0
    assert "R-1" in report.scope


def test_only_the_cheapest_listing_of_a_cluster_becomes_a_match(
        matching_config_with_duplicates):
    run_match(matching_config_with_duplicates, external_id="R-1")
    matches = matches_of(matching_config_with_duplicates)
    assert [m.listing_id for m in matches] == ["cheap"]
    assert matches[0].cluster_size == 3
    assert matches[0].cluster_spread_usd == 11_000.0


def test_new_only_looks_at_listings_from_the_last_run(matching_config_with_two_runs):
    report = run_match(matching_config_with_two_runs, only_new=True)
    assert report.listings == 2          # столько принёс последний прогон
    assert "прогон" in report.scope


def test_recounting_everything_creates_no_duplicates(matching_config):
    run_match(matching_config, recount_all=True)
    second = run_match(matching_config, recount_all=True)
    assert second.new == 0
    assert second.unchanged > 0


def test_a_request_that_does_not_exist_is_an_error_naming_it(matching_config):
    report = run_match(matching_config, external_id="R-404")
    assert report.errors == 1
    assert "R-404" in report.notes


def test_a_paused_request_named_by_hand_is_an_error_naming_its_status(
        matching_config_with_paused_request):
    report = run_match(matching_config_with_paused_request, external_id="R-1")
    assert report.errors == 1
    assert "paused" in report.notes


def test_a_paused_request_is_not_matched(matching_config_with_paused_request):
    report = run_match(matching_config_with_paused_request, recount_all=True)
    assert report.requests == 0
    assert "нет активных заявок" in report.notes.lower()


def test_hot_and_digest_are_counted_by_the_config_thresholds(matching_config):
    report = run_match(matching_config, external_id="R-1")
    assert report.hot + report.digest <= report.new + report.updated + report.unchanged


def test_a_rejected_listing_does_not_become_a_match(tmp_path):
    """Отказы в базе не нужны: их миллионы, и звонить по ним некуда."""
    config = cfg(tmp_path)
    fill(config,
         listings=[suitable("1"),
                   suitable("2", district="Давташен"),        # район не из заявки
                   suitable("3", price_usd=200_000.0)],       # выше растянутого потолка
         requests=[make_request("R-1")])

    report = run_match(config, external_id="R-1")

    assert [m.listing_id for m in matches_of(config)] == ["1"]
    assert report.considered == 3
    assert report.new == 1


def test_a_human_touched_match_keeps_its_status_through_a_recount(matching_config):
    # Решение 7, но уже на уровне команды: ночной пересчёт не имеет права
    # стереть «звонили, не подошло».
    run_match(matching_config, external_id="R-1")
    database = build_database(matching_config)
    database.connect()
    request = database.get_request("R-1")
    first = database.matches_for_request(request.id)[0]
    database.set_match_status(first.id, "called", reject_reason="окна во двор")
    database.close()

    run_match(matching_config, recount_all=True)

    after = [m for m in matches_of(matching_config) if m.id == first.id][0]
    assert after.status == "called"
    assert after.reject_reason == "окна во двор"


def test_clusters_are_counted_before_matching_if_the_base_has_none(
        matching_config_with_duplicates):
    """Спека: кластеры считаются автоматически перед матчингом.

    Иначе первый же `match` на свежей базе показал бы клиенту три карточки
    одной квартиры — команду `cluster` никто не обязан помнить.
    """
    run_match(matching_config_with_duplicates, external_id="R-1")

    database = build_database(matching_config_with_duplicates)
    database.connect()
    stored = {item.id: item.cluster_id for item in database.iter_listings()}
    database.close()
    assert all(stored.values())
    assert stored["cheap"] == stored["mid"] == stored["dear"]


def test_an_empty_base_is_not_an_error(tmp_path):
    config = cfg(tmp_path)
    fill(config, requests=[make_request("R-1")])

    report = run_match(config, external_id="R-1")

    assert report.errors == 0
    assert report.new == 0
    assert report.listings == 0


def test_a_broken_database_is_an_error_and_not_a_crash(tmp_path):
    config = cfg(tmp_path)
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)
    (tmp_path / "work" / "listam.sqlite").write_text("это не база", encoding="utf-8")

    report = run_match(config, external_id="R-1")

    assert report.errors == 1
    assert report.notes


def test_the_report_names_the_scope_and_has_no_none_in_it(matching_config):
    rendered = run_match(matching_config, recount_all=True).render()
    assert "вся база" in rendered
    assert "None" not in rendered
