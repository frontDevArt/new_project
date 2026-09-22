"""События на настоящем подборе: что брокер увидит после `match`.

Модульные тесты `test_events.py` проверяют правила на готовых матчах; здесь
матчи пишет сам `run_match` — ровно так, как их потом прочитает уведомление.
Аудит QA после M3 нашёл три находки именно на этом стыке: правила были
верны, а подбор давал им не то, чего они ждали.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from listam.matches_view import collect_events, render_events
from listam.matching import run_match
from listam.wiring import build_database
from tests.contracts.test_database_contract import make_listing, make_request
from tests.test_matching import cfg, fill, suitable


def now() -> datetime:
    return datetime.now(timezone.utc)


def events_after(config, since, until=None):
    page = collect_events(config, since=since, until=until or now())
    return page, [(event.kind, event.match.listing_id) for event in page.events]


SAME_FLAT = dict(district="Кентрон", street="ул. Туманяна", rooms=3, floor=4,
                 floors_total=9, seller_type="agency")


def test_a_cheaper_card_of_the_same_flat_comes_as_cheaper(tmp_path):
    """B-2 аудита: `twin` дешевле `old` на $2 000, тот же дом и этаж — до
    фазы 2 брокер получал «новый: 1» и «отпало 1 (не представитель кластера)»."""
    config = cfg(tmp_path)
    fill(config,
         listings=[make_listing("old", area=85.0, price_usd=119_000.0, **SAME_FLAT)],
         requests=[make_request("R-1")])
    run_match(config)
    mark = now()
    database = build_database(config)
    database.connect()
    database.upsert_listing(
        make_listing("twin", area=85.5, price_usd=117_000.0, **SAME_FLAT), seen_at=now())
    database.close()

    run_match(config)

    page, found = events_after(config, mark)
    assert found == [("cheaper", "twin")]
    text = render_events(page, per_request=10)
    assert "подешевело с $119,000" in text
    assert "новый" not in text
    assert "отпало" not in text


def test_a_cheaper_card_found_by_the_hourly_match_comes_as_cheaper(tmp_path):
    """Расписание README подбирает по часу `match --new`, а он закрывать
    не вправе ничего, чего нет в выборке. Но «не представитель кластера»
    подбор знает по всей базе, а не по выборке: без этого закрытия двойник
    дешевле в «Звони сейчас» приходил «новым», а одинокое «отпало» доезжало
    до дайджеста после ночного `match --all`."""
    config = cfg(tmp_path)
    fill(config,
         listings=[make_listing("old", area=85.0, price_usd=119_000.0, **SAME_FLAT)],
         requests=[make_request("R-1")])
    run_match(config)
    mark = now()
    database = build_database(config)
    database.connect()
    database.upsert_listing(
        make_listing("twin", area=85.5, price_usd=117_000.0, **SAME_FLAT), seen_at=now())
    database.close()

    report = run_match(config, only_new=True)

    assert report.retired == 1
    page, found = events_after(config, mark)
    assert found == [("cheaper", "twin")]
    night = now()
    run_match(config)
    assert events_after(config, night)[1] == [], \
        "ночной полный проход не должен закрыть прежнюю карточку второй раз"
