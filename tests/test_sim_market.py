"""Рынок имитации: ведёт себя как лента и помнит правду о каждом событии."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from tests.sim.market import Market, MarketParams

NOW = datetime(2026, 9, 23, 23, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами


def test_the_same_seed_gives_the_same_week():
    one = Market.synthetic(500, seed=3, params=MarketParams(), now=NOW)
    two = Market.synthetic(500, seed=3, params=MarketParams(), now=NOW)
    one.advance_to(NOW + timedelta(days=2))
    two.advance_to(NOW + timedelta(days=2))
    assert one.truth == two.truth
    assert [f.id for f in one.feed()] == [f.id for f in two.feed()]


def test_new_listings_arrive_at_the_asked_pace_and_some_are_relists():
    market = Market.synthetic(1000, seed=5, params=MarketParams(new_per_hour=20), now=NOW)
    market.advance_to(NOW + timedelta(hours=24))
    born = [e for e in market.truth if e.kind in ("new", "relist")]
    relists = [e for e in born if e.kind == "relist"]
    assert 380 <= len(born) <= 580
    assert 0.1 <= len(relists) / len(born) <= 0.3


def test_a_relist_is_the_same_flat_under_a_new_id():
    market = Market.synthetic(300, seed=11, params=MarketParams(relist_share=1.0), now=NOW)
    market.advance_to(NOW + timedelta(hours=3))
    event = next(e for e in market.truth if e.kind == "relist")
    child = market.get(event.flat_id)
    twins = [f for f in market.flats.values() if f.flat_key == child.flat_key and f.id != child.id]
    assert twins and abs(twins[0].area - child.area) <= 1
    assert twins[0].district == child.district and twins[0].rooms == child.rooms


def test_a_cheaper_listing_loses_three_to_ten_percent():
    market = Market.synthetic(2000, seed=2, params=MarketParams(cheaper_per_day=0.5), now=NOW)
    market.advance_to(NOW + timedelta(hours=2))
    drops = [e for e in market.truth if e.kind == "cheaper"]
    assert drops
    for event in drops:
        assert 0.88 <= event.new_price / event.old_price <= 0.975


def test_a_gone_listing_leaves_the_feed_and_the_newest_is_on_top():
    market = Market.synthetic(2000, seed=4, params=MarketParams(gone_per_day=0.5), now=NOW)
    market.advance_to(NOW + timedelta(hours=2))
    gone = {e.flat_id for e in market.truth if e.kind == "gone"}
    feed = market.feed()
    assert gone and not gone & {f.id for f in feed}
    assert all(a.bumped_at >= b.bumped_at for a, b in zip(feed, feed[1:]))


def test_the_market_moves_in_whole_hours_only():
    market = Market.synthetic(300, seed=1, params=MarketParams(), now=NOW)
    market.advance_to(NOW + timedelta(minutes=90))
    assert market.now == NOW + timedelta(hours=1)


def test_a_snapshot_keeps_live_usd_and_amd_listings_with_all_fields(tmp_path):
    path = tmp_path / "snap.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE listings (id TEXT, title TEXT, district TEXT, currency TEXT,
            price_amount REAL, area REAL, rooms INTEGER, floor INTEGER, floors_total INTEGER,
            seller_type TEXT, verified INTEGER, new_build INTEGER, status TEXT)""")
        rows = [
            ("100", "3-комн. квартира", "Арабкир", "USD", 120000, 80, 3, 2, 5, "owner", 1, 0, "active"),
            ("101", "2-комн. квартира", "Кентрон", "AMD", 40000000, 55, 2, 4, 9, "agency", None, 1, "active"),
            ("102", "евро", "Кентрон", "EUR", 90000, 50, 2, 3, 9, "owner", None, 0, "active"),
            ("103", "снятая", "Кентрон", "USD", 90000, 50, 2, 3, 9, "owner", None, 0, "gone"),
            ("104", "без площади", "Кентрон", "USD", 90000, None, 2, 3, 9, "owner", None, 0, "active"),
        ]
        db.executemany("INSERT INTO listings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    market = Market.from_snapshot(path, seed=1, params=MarketParams(), now=NOW)
    assert [f.id for f in market.feed()] == ["101", "100"]
    assert market.skipped == 2          # EUR и без площади; снятая в выборку не входит
    amd = market.get("101")
    assert (amd.currency, amd.price, amd.agency, amd.verified, amd.new_build) == ("AMD", 40000000, True, None, True)
    assert market.get("100").verified is True
