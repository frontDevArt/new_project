"""`listam match`: подбор объявлений под заявки.

Матчинг — это место, где сходятся все три домена M2: заявка задаёт рамку,
кластер решает, какую из тридцати одинаковых карточек показать, скоринг
ставит балл. Здесь проверяется оркестрация, а не арифметика: что выборка
взята верно, что представитель кластера один, что пересчёт не плодит
дубли и что бессмысленная выборка названа словами.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from listam.config import Config, ConfigError
from listam.domain.scoring import DEFAULT_WEIGHTS
from listam.matches_view import (MatchesError, collect_matches, display_limit,
                                 render_matches)
from listam.matching import run_match, settings
from listam.wiring import build_database

from tests.contracts.test_database_contract import make_listing, make_request

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами
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


def test_new_takes_everything_touched_since_the_last_run(matching_config_with_two_runs):
    """Мерка — начало последнего прогона, а событие — не только появление.

    Вчерашнее объявление, которое с тех пор подешевело, в выборку входит;
    вчерашнее, которого никто не трогал, — нет.
    """
    config = matching_config_with_two_runs
    database = build_database(config)
    database.connect()
    old = database.get_listing("old-1")
    database.upsert_listing(
        replace(old, price_usd=(old.price_usd or 0) - 10_000, price_raw="подешевело"),
        datetime(2026, 9, 23, tzinfo=timezone.utc),  # календарь: не сравнивается с часами
    )
    database.close()

    report = run_match(config, only_new=True)

    assert report.listings == 3          # два от последнего прогона плюс подешевевшее
    assert "прогон" in report.scope


def test_recounting_everything_creates_no_duplicates(matching_config):
    run_match(matching_config)
    second = run_match(matching_config)
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
    report = run_match(matching_config_with_paused_request)
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

    run_match(matching_config)

    after = [m for m in matches_of(matching_config) if m.id == first.id][0]
    assert after.status == "called"
    assert after.reject_reason == "окна во двор"


def test_clusters_are_counted_before_every_matching(
        matching_config_with_duplicates):
    """Спека: кластеры считаются автоматически перед матчингом — каждым.

    Иначе первый же `match` на свежей базе показал бы клиенту три карточки
    одной квартиры — команду `cluster` никто не обязан помнить. А проверка
    «есть ли в базе непроставленные» ловила только появление: второй подбор
    по той же базе пересчёта уже не делал, хотя состав кластеров меняет
    и уход с ленты.
    """
    config = matching_config_with_duplicates
    run_match(config, external_id="R-1")

    database = build_database(config)
    database.connect()
    stored = {item.id: item.cluster_id for item in database.iter_listings()}
    database.close()
    assert all(stored.values())
    assert stored["cheap"] == stored["mid"] == stored["dear"]

    report = run_match(config, external_id="R-1")

    assert "кластеры пересчитаны" in (report.notes or ""), (
        "второй подбор по той же базе кластеры тоже считает"
    )


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
    rendered = run_match(matching_config).render()
    assert "вся база" in rendered
    assert "None" not in rendered


# --- витрина: `listam matches` (фаза 6) --------------------------------

@pytest.fixture
def matching_config_gone(tmp_path):
    """Матч есть, а объявления на ленте уже нет: решение 8."""
    config = cfg(tmp_path)
    fill(config, listings=[suitable("1")], requests=[make_request("R-1")])
    run_match(config, external_id="R-1")
    database = build_database(config)
    database.connect()
    try:
        database.mark_gone(["1"], NOW + timedelta(hours=1))
    finally:
        database.close()
    return config


def test_the_shop_window_shows_score_price_district_and_the_cluster(matching_config):
    run_match(matching_config, external_id="R-1")

    printed = render_matches(collect_matches(matching_config, external_id="R-1"),
                             limit=50)

    assert "балл" in printed.lower()
    assert "None" not in printed          # правило, добытое F-09 в M1
    assert "$" in printed
    assert "Кентрон" in printed
    assert "R-1" in printed and "Ани" in printed


def test_a_cluster_of_three_says_so_and_names_the_spread(matching_config_with_duplicates):
    run_match(matching_config_with_duplicates, external_id="R-1")

    printed = render_matches(collect_matches(matching_config_with_duplicates,
                                             external_id="R-1"), limit=50)

    assert "3 объявления" in printed
    assert "разброс" in printed
    assert "11,000" in printed


def test_a_match_whose_listing_went_away_is_marked_and_not_hidden(matching_config_gone):
    page = collect_matches(matching_config_gone, external_id="R-1")

    assert [listing.id for _, _, listing in page.rows] == ["1"]
    assert "снято" in render_matches(page, limit=50)


def test_the_window_shows_every_active_request_when_none_is_named(tmp_path):
    config = cfg(tmp_path)
    fill(config, listings=[suitable("1")],
         requests=[make_request("R-1"), make_request("R-2", client_name="Ваган")])
    run_match(config)

    printed = render_matches(collect_matches(config), limit=50)

    assert "R-1" in printed and "R-2" in printed


def test_a_match_below_the_digest_threshold_stays_out_of_the_window(matching_config):
    run_match(matching_config, external_id="R-1")

    everything = collect_matches(matching_config, external_id="R-1", min_score=0)
    strict = collect_matches(matching_config, external_id="R-1", min_score=99)

    assert len(strict.rows) < len(everything.rows)
    assert all(match.score >= 99 for _, match, _ in strict.rows)


def test_the_limit_cuts_the_list_and_says_how_many_are_left(matching_config):
    run_match(matching_config, external_id="R-1")
    rows = collect_matches(matching_config, external_id="R-1", min_score=0)

    printed = render_matches(rows, limit=1)

    assert "…и ещё 2" in printed


def test_an_empty_window_says_so_instead_of_printing_nothing(tmp_path):
    config = cfg(tmp_path)
    fill(config, requests=[make_request("R-1")])

    printed = render_matches(collect_matches(config, external_id="R-1"), limit=50)

    assert "нет" in printed.lower()
    assert "None" not in printed


def test_a_request_that_does_not_exist_is_named_and_not_silently_empty(matching_config):
    with pytest.raises(MatchesError) as failure:
        collect_matches(matching_config, external_id="R-404")
    assert "R-404" in str(failure.value)


def test_the_header_names_the_digest_threshold_the_window_was_cut_by(matching_config):
    run_match(matching_config, external_id="R-1")

    printed = render_matches(collect_matches(matching_config, external_id="R-1"),
                             limit=50, min_score=40)

    assert "40" in printed


def test_a_cluster_priced_the_same_says_the_spread_is_zero(tmp_path):
    """Ноль значит ноль: три карточки по одной цене — это «разброс $0».

    Пустая пометка на её месте читается как «разброса нет данных», хотя он
    как раз известен и как раз нулевой — это самый частый кластер на боевой
    базе, и молчать о нём нельзя.
    """
    config = cfg(tmp_path)
    same = dict(district="Кентрон", street="ул. Туманяна", rooms=3, floor=4,
                floors_total=9, seller_type="agency", price_usd=119_000.0)
    fill(config,
         listings=[make_listing("a", area=85.0, **same),
                   make_listing("b", area=86.0, **same),
                   make_listing("c", area=87.0, **same)],
         requests=[make_request("R-1")])
    run_match(config, external_id="R-1")

    printed = render_matches(collect_matches(config, external_id="R-1"), limit=50)

    assert "3 объявления, разброс $0" in printed


# --- конец жизни матча (B-1, B-2) --------------------------------------

def test_a_listing_that_left_the_budget_leaves_the_window(matching_config):
    run_match(matching_config)
    config = matching_config

    database = build_database(config)
    database.connect()
    listing = database.get_listing("1")
    database.upsert_listing(
        replace(listing, price_usd=900_000.0, price_raw="900000 $"),
        datetime(2026, 9, 23, tzinfo=timezone.utc),  # календарь: не сравнивается с часами
    )
    database.close()

    report = run_match(config)

    assert report.retired == 1
    page = collect_matches(config)
    assert "1" not in [listing.id for _, _, listing in page.rows]


def test_a_cheaper_twin_replaces_the_old_representative_and_not_doubles_it(
        matching_config_with_duplicates):
    config = matching_config_with_duplicates
    run_match(config)
    before = {listing.id for _, _, listing in collect_matches(config).rows}

    database = build_database(config)
    database.connect()
    twin = database.get_listing(sorted(before)[0])
    database.upsert_listing(
        replace(twin, id="L-cheap", url="https://www.list.am/ru/item/L-cheap",
                price_usd=(twin.price_usd or 0) - 5_000),
        datetime(2026, 9, 23, tzinfo=timezone.utc),  # календарь: не сравнивается с часами
    )
    database.close()

    run_match(config)

    after = [listing for _, _, listing in collect_matches(config).rows]
    clusters_shown = [match.cluster_id for _, match, _ in collect_matches(config).rows]
    assert len(clusters_shown) == len(set(clusters_shown)), \
        "одна квартира не может стоять в витрине дважды"
    assert "L-cheap" in [listing.id for listing in after]


def test_new_sees_the_one_that_got_cheaper(tmp_path):
    """Главное событие рынка — снижение цены, а не появление карточки.

    Квартира вчера стоила 200 000 $ и в бюджет не влезала; сегодня она стоит
    118 000 $. Новой она не стала — и по мерке `first_seen` не попадёт
    в `--new` никогда.
    """
    config = cfg(tmp_path)
    fill(config, listings=[suitable("pricey", price_usd=200_000.0)],
         requests=[make_request("R-1")], seen_at=YESTERDAY)

    run_match(config)          # полный проход: матча нет, заявка отмечена
    assert collect_matches(config).rows == []

    database = build_database(config)
    database.connect()
    listing = database.get_listing("pricey")
    database.upsert_listing(
        replace(listing, price_usd=118_000.0, price_raw="подешевело"),
        # Выборка --new без прогонов в журнале — окно fallback_hours от настоящих
        # часов: дата из календаря через сутки из окна выпадала.
        datetime.now(timezone.utc),
    )
    database.close()

    report = run_match(config, only_new=True)

    assert report.listings >= 1
    assert "pricey" in [listing.id for _, _, listing in collect_matches(config).rows]


def test_a_request_edited_after_its_last_matching_is_swept_whole(matching_config):
    config = matching_config
    run_match(config)

    database = build_database(config)
    database.connect()
    request = next(iter(database.iter_requests()))
    # Правка — после подбора по его же отметке: подбор ставит `matched_at`
    # по настоящим часам, и число из календаря однажды оказывается в прошлом.
    database.upsert_request(
        replace(request, budget_max=(request.budget_max or 0) * 3),
        request.matched_at + timedelta(seconds=1),
    )
    database.close()

    report = run_match(config, only_new=True)

    assert report.requests >= 1
    assert "правленых заявок" in (report.notes or ""), (
        "заявка, которую тронули после подбора, идёт по всей базе, "
        "а не по выборке последнего прогона"
    )


def test_matching_only_the_new_ones_closes_nothing(matching_config_with_two_runs):
    config = matching_config_with_two_runs
    run_match(config)
    report = run_match(config, only_new=True)
    assert report.retired == 0, \
        "выборка --new неполна: закрывать по ней — значит выкинуть всё, чего в ней нет"


def test_a_cluster_that_lost_a_member_is_the_same_in_the_base_and_in_the_match(
        matching_config_with_duplicates):
    """Ушедший с ленты член меняет кластер, колонок в базе не трогая.

    Признак «есть объявление без cluster_id» ловит только появление, и после
    ухода `listings.cluster_id` остаётся вчерашним — а снимок в матче
    считается заново каждым подбором. База и матч начинают говорить разное
    про один и тот же кластер.
    """
    config = matching_config_with_duplicates
    run_match(config)

    database = build_database(config)
    database.connect()
    # «mid» — якорь кластера: по нему кластер и назван. Его уход кластер
    # переименовывает, ничего в колонках не обнуляя.
    database.mark_gone(["mid"], datetime(2026, 9, 23, tzinfo=timezone.utc))  # календарь: не сравнивается с часами
    database.close()

    run_match(config)

    database = build_database(config)
    database.connect()
    stored = {item.id: item.cluster_id for item in database.iter_listings()}
    shown = database.matches_for_request(database.get_request("R-1").id)[0]
    database.close()

    assert shown.cluster_id == stored[shown.listing_id], (
        "объявление ушло с ленты — кластер стал другим; пока база его не "
        "пересчитала, снимок в матче и колонка в listings говорят разное"
    )


def retired_reasons(config: Config, external_id="R-1") -> dict[str, str | None]:
    database = build_database(config)
    database.connect()
    try:
        request = database.get_request(external_id)
        return {match.listing_id: match.retired_reason
                for match in database.matches_for_request(request.id,
                                                          include_retired=True)
                if match.retired_at is not None}
    finally:
        database.close()


def test_a_match_closed_by_budget_says_budget(matching_config):
    """Живьём: квартира подорожала — в базе должно лежать «бюджет», а не
    общая фраза. Приёмка фазы 8 нашла именно это: причина была одна на всех."""
    config = matching_config
    run_match(config)
    database = build_database(config)
    database.connect()
    listing = database.get_listing("1")
    database.upsert_listing(
        replace(listing, price_usd=900_000.0, price_raw="900000 $"),
        datetime(2026, 9, 23, tzinfo=timezone.utc),  # календарь: не сравнивается с часами
    )
    database.close()

    run_match(config)

    assert retired_reasons(config) == {"1": "бюджет"}


def test_a_match_closed_by_a_cheaper_twin_says_so(matching_config_with_duplicates):
    """Вторая живая причина: в кластере появился вариант дешевле, и матч
    на прежнего представителя закрывается — но не «по бюджету»."""
    config = matching_config_with_duplicates
    run_match(config)
    database = build_database(config)
    database.connect()
    twin = database.get_listing("cheap")
    database.upsert_listing(
        replace(twin, id="L-cheap", url="https://www.list.am/ru/item/L-cheap",
                price_usd=(twin.price_usd or 0) - 5_000),
        datetime(2026, 9, 23, tzinfo=timezone.utc),  # календарь: не сравнивается с часами
    )
    database.close()

    run_match(config)

    assert retired_reasons(config) == {"cheap": "не представитель кластера"}


def test_a_match_whose_listing_left_the_feed_is_not_retired(matching_config_gone):
    """Решение 8: снятое объявление витрина помечает, а не прячет.

    Полный проход его не видит — `listings_for_matching` отдаёт только
    активные. «Не подтвердился» для него значит «его не было в проходе»,
    и закрывать по этому нельзя.
    """
    report = run_match(matching_config_gone)

    assert report.retired == 0
    page = collect_matches(matching_config_gone, external_id="R-1")
    assert [listing.id for _, _, listing in page.rows] == ["1"]


# --- конфиг, который не врёт -------------------------------------------


def test_a_misspelled_weight_is_refused_and_not_dropped_from_the_score(tmp_path):
    config = cfg(tmp_path, match={"weights": {"budjet": 30, "district": 20}})
    with pytest.raises(ConfigError) as exc:
        settings(config)
    assert "budjet" in str(exc.value)
    assert "budget" in str(exc.value), "отказ обязан назвать, как правильно"


def test_a_missing_weight_is_refused_too(tmp_path):
    config = cfg(tmp_path, match={"weights": {"budget": 30}})
    with pytest.raises(ConfigError) as exc:
        settings(config)
    assert "district" in str(exc.value)


def test_match_refuses_a_threshold_outside_the_scale(tmp_path):
    """Отказ приходит до работы: порог, прочитанный посреди прохода, прилетал
    бы человеку поверх пересчитанных кластеров."""
    config = cfg(tmp_path)
    config.data["match"]["thresholds"]["hot"] = 170

    with pytest.raises(ConfigError) as exc:
        settings(config)
    assert "match.thresholds.hot" in str(exc.value)


def test_a_weight_of_zero_is_a_weight_and_not_an_absence(tmp_path):
    config = cfg(tmp_path, match={"weights": dict(DEFAULT_WEIGHTS, seller_type=0)})
    assert settings(config).weights["seller_type"] == 0


def test_a_zero_display_limit_is_refused_by_name(tmp_path):
    config = cfg(tmp_path, match={"limit": 0})
    with pytest.raises(ConfigError) as exc:
        display_limit(config)
    assert "match.limit" in str(exc.value)


def test_a_storage_that_refuses_the_upload_is_a_note_and_not_a_crash(
        matching_config, monkeypatch):
    """База уже записана и закрыта: провал заливки не имеет права съесть отчёт.

    Хранилище подменяется в каркасе (`listam/runner.py`): замок, копия и
    заливка у всех команд общие, и собирает хранилище теперь он.
    """
    import listam.runner as runner_module

    class Refusing:
        """Хранилище, до которого не дошла сеть — но уже после чтения базы.

        `download` отвечает «копии нет» ровно как исправное пустое хранилище:
        отказать раньше — значит проверить другую ветку, ту, где подбор не
        начинался вовсе.
        """

        def download(self, *args, **kwargs):
            return False

        def exists(self, *args, **kwargs):
            return False

        def names(self, *args, **kwargs):
            return []

        def upload(self, *args, **kwargs):
            raise OSError("хранилище недоступно")

    monkeypatch.setattr(runner_module, "build_storage", lambda config: Refusing())

    report = run_match(matching_config)

    assert report.errors >= 1
    assert "хранилище недоступно" in (report.notes or "")


def test_a_misspelled_weight_stops_the_match_before_it_touches_the_base(matching_config):
    """«Отклоняется на входе» значит «до работы», а не «на середине прохода».

    Веса читались после пересчёта кластеров: отказ прилетал человеку уже
    поверх записанной базы. Читать конфиг надо до того, как что-то сделано.
    """
    matching_config.data["match"]["weights"] = {"budjet": 30, "district": 20,
                                                "price_per_sqm": 20, "area_rooms": 15,
                                                "floor": 10, "seller_type": 5}

    with pytest.raises(ConfigError):
        run_match(matching_config)

    database = build_database(matching_config)
    database.connect()
    try:
        assert all(item.cluster_id is None for item in database.iter_listings()), \
            "кластеры пересчитаны — значит, проход успел тронуть базу"
    finally:
        database.close()
