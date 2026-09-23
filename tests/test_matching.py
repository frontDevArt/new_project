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
                        "area_rooms": 15, "floor": 10, "seller_type": 5, "wishes": 15},
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


# --- подбор со страницами (фаза 3 M3.5, решения 9, 12, 14) ------------------

from listam.domain.models import ListingPage, PageFields  # noqa: E402

FUNNEL = {"max_attempts": 2, "wishes": {
    "ремонт": {"field": "renovation", "any_of": ["косметический", "евроремонт", "дизайнерский"]},
    "не панель": {"field": "building_type", "none_of": ["панельное"]},
    "лифт": {"field": "elevator", "is": True},
}}


def funnel_config(tmp_path, listings, requests) -> Config:
    config = cfg(tmp_path, funnel=FUNNEL)
    fill(config, listings=listings, requests=requests)
    return config


def save_page(config, listing_id, *, status="ok", attempts=1, **values):
    database = build_database(config)
    database.connect()
    try:
        database.save_page(ListingPage(
            listing_id=listing_id, status=status, attempts=attempts,
            fetched_at=datetime.now(timezone.utc) if status == "ok" else None,
            price_raw="$132,000",
            fields=PageFields(values=values) if status == "ok" else None))
    finally:
        database.close()


def live(config, external_id="R-1"):
    return [match for match in matches_of(config, external_id) if match.retired_at is None]


def test_a_candidate_without_its_page_is_not_a_match(tmp_path):
    """Решение 9: заявке нужны поля страницы — без открытой страницы
    объявление кандидат, а не матч."""
    config = funnel_config(tmp_path, [suitable("1")],
                           [make_request("R-1", must_have="ремонт")])

    report = run_match(config)

    assert report.new == 0
    assert matches_of(config) == []


def test_a_candidate_becomes_a_match_once_its_page_opens(tmp_path):
    config = funnel_config(tmp_path, [suitable("1")],
                           [make_request("R-1", must_have="ремонт")])
    run_match(config)
    save_page(config, "1", renovation="косметический")

    report = run_match(config)

    assert report.new == 1
    assert [match.listing_id for match in live(config)] == ["1"]


def test_match_new_sees_a_page_that_opened_after_the_last_run(tmp_path):
    """Страница открылась после прогона — `--new` обязан увидеть кандидата,
    хотя на ленте с ним ничего не случилось."""
    config = funnel_config(tmp_path, [suitable("1")],
                           [make_request("R-1", must_have="ремонт")])
    run_match(config)                                   # заявка подобрана: не правленая
    database = build_database(config)
    database.connect()
    try:
        run_id = database.start_run(datetime.now(timezone.utc) - timedelta(minutes=5),
                                    rate_amd_per_usd=385.0, mode="fresh")
        database.finish_run(run_id, datetime.now(timezone.utc))
    finally:
        database.close()
    save_page(config, "1", renovation="косметический")

    report = run_match(config, only_new=True)

    assert report.new == 1


def test_a_known_wrong_field_is_not_a_match(tmp_path):
    config = funnel_config(tmp_path, [suitable("1")],
                           [make_request("R-1", must_have="не панель")])
    save_page(config, "1", building_type="панельное")

    assert run_match(config).new == 0


def test_a_field_missing_from_the_page_does_not_refuse(tmp_path):
    config = funnel_config(tmp_path, [suitable("1")],
                           [make_request("R-1", must_have="не панель")])
    save_page(config, "1", renovation="косметический")

    assert run_match(config).new == 1


def test_a_page_that_ran_out_of_attempts_counts_as_unknown(tmp_path):
    """Решение 13: неудачное открытие повторяется до `max_attempts`, дальше
    поле неизвестно — а неизвестное не отказ."""
    config = funnel_config(tmp_path, [suitable("1"), suitable("2")],
                           [make_request("R-1", must_have="ремонт")])
    save_page(config, "1", status="failed", attempts=2)
    save_page(config, "2", status="failed", attempts=1)

    run_match(config)

    assert [match.listing_id for match in live(config)] == ["1"]


def test_unopened_page_does_not_retire(tmp_path):
    """Решение 12: «страница не открыта» — это «проход не видел», а не
    «видел и не подтвердил». Вчерашний матч живёт."""
    config = funnel_config(tmp_path, [suitable("1")], [make_request("R-1")])
    run_match(config)
    assert len(live(config)) == 1
    fill(config, requests=[make_request("R-1", must_have="ремонт")],
         seen_at=datetime.now(timezone.utc))

    report = run_match(config)

    assert report.retired == 0
    assert len(live(config)) == 1


def test_a_known_wrong_field_retires_the_old_match_with_its_word(tmp_path):
    config = funnel_config(tmp_path, [suitable("1")], [make_request("R-1")])
    run_match(config)
    save_page(config, "1", building_type="панельное")
    fill(config, requests=[make_request("R-1", must_have="не панель")],
         seen_at=datetime.now(timezone.utc))

    run_match(config)

    database = build_database(config)
    database.connect()
    try:
        request = database.get_request("R-1")
        [match] = database.matches_for_request(request.id, include_retired=True)
    finally:
        database.close()
    assert match.retired_at is not None
    assert match.retired_reason == "тип дома"


def test_nice_to_have_is_the_wishes_factor(tmp_path):
    config = funnel_config(tmp_path, [suitable("1")],
                           [make_request("R-1", nice_to_have="лифт")])
    save_page(config, "1", elevator=True)

    run_match(config)

    assert live(config)[0].breakdown["wishes"] == [15.0, 15.0]


def test_nice_to_have_alone_does_not_need_the_page(tmp_path):
    """Мягкое пожелание матч не отменяет: без страницы фактора просто нет."""
    config = funnel_config(tmp_path, [suitable("1")],
                           [make_request("R-1", nice_to_have="лифт")])

    run_match(config)

    match = live(config)[0]
    assert "wishes" not in match.breakdown


def test_new_request_matches_are_born_as_request(tmp_path):
    """Решение 14: сотни матчей новой заявки — первичная подборка, а не повод
    звонить в этот час. То, что принёс рынок потом, — market."""
    config = funnel_config(tmp_path, [suitable("1")], [make_request("R-1")])
    run_match(config)
    assert [match.origin for match in live(config)] == ["request"]

    database = build_database(config)
    database.connect()
    try:
        run_id = database.start_run(datetime.now(timezone.utc) - timedelta(minutes=5),
                                    rate_amd_per_usd=385.0, mode="fresh")
        database.upsert_listing(suitable("2"), seen_at=datetime.now(timezone.utc))
        database.finish_run(run_id, datetime.now(timezone.utc))
    finally:
        database.close()
    run_match(config, only_new=True)

    origins = {match.listing_id: match.origin for match in live(config)}
    assert origins == {"1": "request", "2": "market"}


def test_an_edited_request_gives_its_new_matches_the_request_origin(tmp_path):
    config = funnel_config(tmp_path, [suitable("1"), suitable("2", rooms=4)],
                           [make_request("R-1", rooms=[3])])
    run_match(config, only_new=True)
    fill(config, requests=[make_request("R-1", rooms=[3, 4])],
         seen_at=datetime.now(timezone.utc))

    run_match(config, only_new=True)

    origins = {match.listing_id: match.origin for match in live(config)}
    assert origins["2"] == "request"


def test_a_crooked_vocabulary_stops_the_match_before_the_base(tmp_path):
    config = funnel_config(tmp_path, [suitable("1")], [make_request("R-1")])
    config.data["funnel"] = {"wishes": {"лифт": {"field": "elevator"}}}

    with pytest.raises(ConfigError):
        run_match(config)


def test_the_secondary_district_share_comes_from_the_config(tmp_path):
    config = cfg(tmp_path, match={"secondary_district": 0.25})
    assert settings(config).secondary_district == 0.25


def test_the_secondary_district_share_defaults_to_the_measured_zero(tmp_path):
    """Фаза 4 M3.5: замер «до» → «после», см. план. Ключа нет — тот же ноль."""
    assert settings(cfg(tmp_path)).secondary_district == 0.0


@pytest.mark.parametrize("value", [1.5, -0.1, None, "половина", True])
def test_a_senseless_secondary_district_share_is_refused(tmp_path, value):
    config = cfg(tmp_path, match={"secondary_district": value})
    with pytest.raises(ConfigError) as exc:
        settings(config)
    assert "match.secondary_district" in str(exc.value)


# --- рычаги фазы 4 M3.5 на боевом и рабочем конфиге ------------------------
#
# Балл был сжат вверху: бюджет, район и площадь уже отсеяны грубым ситом и
# почти всем дают полную долю. Горячим выходило ~80 % подходящего (замер
# «до» в плане). Эти тесты держат разброс, ради которого числа менялись.

def _tuned(env: str, tmp_path: Path):
    from listam.config import load_config
    return settings(load_config(env, Path(__file__).parent.parent / "config",
                                dotenv_path=tmp_path / ".env"))


def _typical(tuning, per_sqm: float):
    """Широкая заявка, приоритетный район, агентство, в бюджете, без этажей."""
    from listam.domain.models import Request
    from listam.domain.scoring import score
    request = Request(external_id="R-3", budget_max=250_000.0,
                      districts=["Кентрон", "Арабкир"], districts_priority=["Кентрон"],
                      rooms=[2, 3, 4], area_min=60.0)
    listing = make_listing(district="Кентрон", price_usd=150_000.0, area=100.0,
                           rooms=3, price_per_sqm=per_sqm, seller_type="agency",
                           floor=None)
    return score(request, listing, median_by_district={"Кентрон": 1_500.0},
                 weights=tuning.weights, stretch_percent=tuning.stretch_percent,
                 secondary_district=tuning.secondary_district).value


@pytest.mark.parametrize("env", ["prod", "dev"])
def test_the_price_per_sqm_spreads_the_score(env, tmp_path):
    """Рычаг «веса»: выгодность против медианы района разводит баллы хотя бы
    на 20. На весах до фазы 4 (бюджет 30, цена/м² 20) — на 11: 94 против 83."""
    tuning = _tuned(env, tmp_path)
    bargain, ordinary = _typical(tuning, 1_125.0), _typical(tuning, 1_500.0)
    assert bargain - ordinary >= 20


@pytest.mark.parametrize("env", ["prod", "dev"])
def test_an_ordinary_flat_is_not_hot_and_a_bargain_is(env, tmp_path):
    """Рычаг «порог hot»: квартира по медиане района в приоритетном районе —
    не повод звонить сейчас (её место в дайджесте), на 25 % дешевле — повод."""
    tuning = _tuned(env, tmp_path)
    assert _typical(tuning, 1_500.0) < tuning.hot
    assert _typical(tuning, 1_125.0) >= tuning.hot
    assert _typical(tuning, 1_500.0) >= tuning.digest


@pytest.mark.parametrize("env", ["prod", "dev"])
def test_the_yellow_circle_still_exists_above_the_hot_threshold(env, tmp_path):
    """🟡 — от порога hot до 🟢. Поднятый до 80 hot при 🟢 от 80 съел бы 🟡
    целиком: у горячего не осталось бы «сильный» и «просто горячий»."""
    from listam.config import load_config
    from listam.layout import circles
    config = load_config(env, Path(__file__).parent.parent / "config",
                         dotenv_path=tmp_path / ".env")
    green, yellow = circles(config)
    assert yellow == settings(config).hot
    assert green > yellow


# --- исключения из отказов клиента (фаза 6 M3.5, решение 15) ----------------

from listam.domain.models import Exclusion  # noqa: E402


def refuse(config, external_id, kind, value=None, reason=None, match_id=None):
    database = build_database(config)
    database.connect()
    try:
        request = database.get_request(external_id)
        database.add_exclusions([Exclusion(
            request_id=request.id, kind=kind, value=value, reason=reason,
            match_id=match_id, created_at=datetime.now(timezone.utc))])
    finally:
        database.close()


def all_matches(config, external_id="R-1"):
    database = build_database(config)
    database.connect()
    try:
        request = database.get_request(external_id)
        return {match.listing_id: match
                for match in database.matches_for_request(request.id,
                                                          include_retired=True)}
    finally:
        database.close()


def test_a_refused_cluster_retires_with_the_clients_words(matching_config):
    run_match(matching_config)
    target = all_matches(matching_config)["2"]
    refuse(matching_config, "R-1", "cluster", target.cluster_id, "дорого",
           match_id=target.id)

    report = run_match(matching_config, external_id="R-1")

    after = all_matches(matching_config)
    assert report.retired == 1
    assert after["2"].retired_reason == "клиент отказал: дорого"
    assert after["1"].retired_at is None and after["3"].retired_at is None


def test_a_refused_cluster_stays_out_with_its_twin(matching_config_with_duplicates):
    """Карточку отвергнутой квартиры сменили на дешёвую — двойник той же
    квартиры матчем не становится."""
    run_match(matching_config_with_duplicates)
    target = all_matches(matching_config_with_duplicates)["cheap"]
    refuse(matching_config_with_duplicates, "R-1", "cluster", target.cluster_id,
           match_id=target.id)
    database = build_database(matching_config_with_duplicates)
    database.connect()
    try:
        database.upsert_listing(make_listing(
            "cheaper", area=85.5, price_usd=100_000.0, district="Кентрон",
            street="ул. Туманяна", rooms=3, floor=4, floors_total=9,
            seller_type="agency"), seen_at=NOW)
    finally:
        database.close()

    run_match(matching_config_with_duplicates)

    alive = [key for key, match in all_matches(matching_config_with_duplicates).items()
             if match.retired_at is None]
    assert alive == []


def test_one_requests_refusal_does_not_narrow_another(tmp_path):
    config = cfg(tmp_path)
    fill(config, listings=[suitable("1")],
         requests=[make_request("R-1"), make_request("R-2")])
    refuse(config, "R-1", "district", "Кентрон", "район")

    run_match(config)

    assert all_matches(config, "R-1") == {}
    assert list(all_matches(config, "R-2")) == ["1"]


def test_a_page_field_refusal_reads_the_page_without_wishes(tmp_path):
    """Заявка без пожеланий, а клиент отказал по типу дома: подбор обязан
    прочитать страницы, иначе отказ по полю страницы не сработает."""
    config = funnel_config(tmp_path, [suitable("1"), suitable("2")],
                           [make_request("R-1")])
    save_page(config, "1", building_type="панельное")
    save_page(config, "2", building_type="каменное")
    refuse(config, "R-1", "building_type", "панельное", "тип дома")

    run_match(config)

    assert [match.listing_id for match in live(config)] == ["2"]
