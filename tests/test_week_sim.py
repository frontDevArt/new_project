"""Малый прогон имитатора: сутки на синтетическом рынке — вся цепочка цела."""
from __future__ import annotations

import pytest

from tests.sim.report import compute, sections
from tests.sim.week import simulate


@pytest.fixture(scope="module")
def day(tmp_path_factory):
    out = tmp_path_factory.mktemp("sim")
    result = simulate(out, days=1, seed=7, synthetic=300, say=lambda *_: None)
    return result, {m.id: m for m in compute(result)}


def test_every_cycle_of_a_day_is_green(day):
    result, metrics = day
    assert result.bootstrap_codes == [0, 0, 0]
    assert [c.code for c in result.cycles] == [0] * 26
    assert metrics["С-1"].ok and metrics["С-2"].ok


def test_the_funnel_stays_under_its_ceiling_and_inside_the_requests(day):
    _, metrics = day
    assert metrics["С-5"].ok, metrics["С-5"]
    assert metrics["С-6"].ok, metrics["С-6"]


def test_the_broker_gets_hot_messages_and_the_marks_go_through(day):
    result, metrics = day
    assert "/ None" not in str(metrics["С-3"].value)
    assert result.marks and all(m.code == 0 for m in result.marks)


def test_a_hot_card_is_not_repeated_without_a_market_event(day):
    """Края окон: пара (заявка, объявление) второй раз — только после события рынка."""
    import contextlib
    import sqlite3

    from listam.adapters.db_sqlite import from_iso
    from listam.wiring import database_path

    result, _ = day
    with contextlib.closing(sqlite3.connect(database_path(result.config))) as db:
        rows = db.execute("SELECT sent_at, text FROM notifications WHERE kind = 'hot' "
                          "ORDER BY sent_at").fetchall()
    events = {}
    for event in result.market.truth:
        events.setdefault(event.flat_id, []).append(event.at)
    seen: dict[tuple[str, str], object] = {}
    repeats = []
    for sent, text in rows:
        sent = from_iso(sent)
        for section in sections(text, {"R-1", "R-2", "R-3"}):
            for listing_id in section.ids:
                pair = (section.request, listing_id)
                if pair in seen and not any(seen[pair] < at <= sent
                                            for at in events.get(listing_id, [])):
                    repeats.append(pair)
                seen[pair] = sent
    assert repeats == []


def test_the_simulation_never_writes_into_project_data(day, tmp_path):
    result, _ = day
    assert str(result.out) in str(result.config.get("storage.work_dir"))


def test_the_hot_load_shows_both_events_and_cards(day):
    """С-3 меряет события (карточки + «➕ ещё N»); карточки — рядом, для сверки хвоста."""
    _, metrics = day
    value = str(metrics["С-3"].value)
    assert "события" in value and "карточки" in value
