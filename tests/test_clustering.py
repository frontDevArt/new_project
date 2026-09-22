"""Дедуп кластеров: одна квартира у трёх агентств — один кластер.

Проверяются оба края решений 4 и 5 спеки: ключ строгий (без улицы объявление
остаётся само по себе, иначе разные квартиры одного района склеятся и вариант
спрячется от клиента), а допуск по площади — объединение соседей, а не бакет.
"""
from __future__ import annotations

from listam.domain.clustering import assign, clusters, normalize_street

from tests.contracts.test_database_contract import make_listing


def test_the_same_flat_from_three_agencies_is_one_cluster():
    items = [
        make_listing("1", price_usd=132_000.0, area=85.0),
        make_listing("2", price_usd=139_000.0, area=85.0),
        make_listing("3", price_usd=128_000.0, area=86.0),
    ]
    mapping = assign(items)
    assert len({mapping[item.id] for item in items}) == 1


def test_areas_two_metres_apart_join_and_three_metres_apart_do_not():
    close = assign([make_listing("1", area=85.0), make_listing("2", area=87.0)])
    assert len(set(close.values())) == 1
    far = assign([make_listing("1", area=85.0), make_listing("2", area=88.0)])
    assert len(set(far.values())) == 2


def test_a_chain_of_close_areas_breaks_where_the_tolerance_ends():
    # 85 — 87 — 89: соседи в пределах допуска, края — нет. Раньше цепочка
    # склеивала все три в кластер шириной 4 м²; показывалась одна карточка,
    # и 89 м² клиент не видел вовсе. Допуск в 2 м² этого не разрешал:
    # 85 и 89 — разные квартиры, а не одна, обмеренная тремя агентствами.
    items = [make_listing("1", area=85.0), make_listing("2", area=87.0),
             make_listing("3", area=89.0)]
    mapping = assign(items)
    assert mapping["1"] == mapping["2"]
    assert mapping["3"] != mapping["1"]


def test_a_listing_without_a_street_is_a_cluster_of_its_own():
    # Решение 4: улицы нет у 20% базы, и без неё ключ склеивает разные
    # квартиры одного района. Прятать вариант хуже, чем позвонить дважды.
    items = [make_listing("1", street=None), make_listing("2", street=None)]
    assert len(set(assign(items).values())) == 2


def test_a_listing_without_a_district_or_an_area_is_a_cluster_of_its_own():
    # Те же две дырки в данных, что и улица: без них ключ вырождается.
    items = [make_listing("1", district=None), make_listing("2", district=None),
             make_listing("3", area=None), make_listing("4", area=None)]
    assert len(set(assign(items).values())) == 4


def test_different_floors_are_different_flats():
    items = [make_listing("1", floor=4), make_listing("2", floor=5)]
    assert len(set(assign(items).values())) == 2


def test_the_street_is_read_the_same_however_it_is_written():
    assert normalize_street("ул. Туманяна") == normalize_street("Туманяна  ")
    assert normalize_street("улица Туманяна") == normalize_street("ТУМАНЯНА")


def test_an_empty_street_is_no_street():
    assert normalize_street(None) is None
    assert normalize_street("   ") is None
    assert normalize_street("ул.") is None


def test_the_cluster_id_does_not_depend_on_the_order_of_reading():
    items = [make_listing("1", area=85.0), make_listing("2", area=86.0)]
    assert set(assign(items).values()) == set(assign(list(reversed(items))).values())


def test_a_cluster_knows_its_cheapest_member_and_the_spread():
    found = clusters([make_listing("1", price_usd=132_000.0),
                      make_listing("2", price_usd=139_000.0)])
    assert len(found) == 1
    assert found[0].cheapest_id == "1"
    assert found[0].size == 2
    assert found[0].spread_usd == 7_000.0


def test_a_lonely_listing_has_no_spread():
    assert clusters([make_listing("1")])[0].spread_usd is None


def test_a_member_without_a_price_is_never_the_cheapest():
    # «Самое дешёвое объявление кластера» — это цена, а не её отсутствие:
    # показывать клиенту карточку без цены как лучший вариант нельзя.
    found = clusters([make_listing("1", price_usd=None, price_per_sqm=None),
                      make_listing("2", price_usd=139_000.0)])
    assert found[0].cheapest_id == "2"
    assert found[0].spread_usd is None


def test_a_chain_does_not_stretch_past_the_tolerance():
    found = clusters([
        make_listing("a", area=60.0), make_listing("b", area=62.0),
        make_listing("c", area=64.0), make_listing("d", area=66.0),
    ], area_tolerance=2.0)
    sizes = sorted(cluster.size for cluster in found)
    assert sizes == [2, 2], (
        "60 и 66 — разные квартиры: допуск 2 м² не разрешал их склеивать. "
        "Пары 60+62 и 64+66 он разрешал: ширина каждой ровно 2 м²"
    )


def test_neighbours_inside_the_tolerance_still_meet():
    found = clusters([make_listing("a", area=60.0), make_listing("b", area=61.9)],
                     area_tolerance=2.0)
    assert [cluster.size for cluster in found] == [2], (
        "бакет разрезал бы их по границе — объединение соседей этого не делает"
    )


def test_the_widest_cluster_is_no_wider_than_the_tolerance():
    found = clusters([make_listing(f"x{n}", area=60.0 + n * 0.5) for n in range(12)],
                     area_tolerance=2.0)
    for cluster in found:
        areas = [60.0 + int(item[1:]) * 0.5 for item in cluster.listing_ids]
        assert max(areas) - min(areas) <= 2.0


def test_a_newcomer_does_not_rename_the_cluster():
    before = clusters([make_listing("100", area=60.0), make_listing("101", area=61.0)],
                      area_tolerance=2.0)[0]
    after = clusters([make_listing("100", area=60.0), make_listing("101", area=61.0),
                      make_listing("102", area=61.5)], area_tolerance=2.0)[0]
    assert before.cluster_id == after.cluster_id, (
        "идентификатор кластера назван по самому старому объявлению в нём "
        "и от прихода соседа не меняется"
    )


def test_the_anchor_is_the_oldest_id_and_not_the_shortest_string():
    found = clusters([make_listing("9", area=60.0), make_listing("10", area=60.5)],
                     area_tolerance=2.0)[0]
    alone = clusters([make_listing("9", area=60.0)], area_tolerance=2.0)[0]
    assert found.cluster_id == alone.cluster_id, (
        "id list.am растут числами: 9 старше 10, хотя как строка — больше"
    )


def test_two_different_addresses_are_two_different_clusters():
    found = clusters([make_listing("100", area=60.0, street="Абовяна"),
                      make_listing("101", area=60.0, street="Маштоца")],
                     area_tolerance=2.0)
    assert len({cluster.cluster_id for cluster in found}) == 2


def test_a_name_that_merely_starts_like_an_abbreviation_is_left_alone():
    # «ул» — слово, а не первые две буквы. Съесть их — значит свести
    # «Улучшенная» и «Ицавановская» к одной улице.
    assert normalize_street("Улучшенная") == "улучшенная"
    assert normalize_street("Проспектная") == "проспектная"
