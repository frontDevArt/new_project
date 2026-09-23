"""События на настоящем подборе: что брокер увидит после `match`.

Модульные тесты `test_events.py` проверяют правила на готовых матчах; здесь
матчи пишет сам `run_match` — ровно так, как их потом прочитает уведомление.
Аудит QA после M3 нашёл три находки именно на этом стыке: правила были
верны, а подбор давал им не то, чего они ждали.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from listam.matches_view import collect_events, render_events
from listam.matching import run_match
from listam.wiring import build_database
from tests.contracts.test_database_contract import make_listing, make_request
from tests.test_matching import cfg, fill, suitable

_last_moment = datetime.min.replace(tzinfo=timezone.utc)


def now() -> datetime:
    """Строго растущие отметки теста. Часы Windows тикают крупно: отметка
    окна и точка цены, взятые подряд, совпадали до микросекунды, и окно
    `(since, until]` честно относило точку к прошлому окну."""
    global _last_moment
    moment = max(datetime.now(timezone.utc), _last_moment + timedelta(microseconds=1))
    _last_moment = moment
    return moment


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


def cheaper(config, listing_id: str, price_usd: float, price_per_sqm: float) -> None:
    """Обход увидел новую цену: строка в `listings` и точка в `price_history`."""
    database = build_database(config)
    database.connect()
    try:
        listing = database.get_listing(listing_id)
        database.upsert_listing(
            replace(listing, price_usd=price_usd, price_per_sqm=price_per_sqm,
                    price_raw=f"{int(price_usd)} $"),
            seen_at=now(),
        )
    finally:
        database.close()


def test_a_deep_discount_that_did_not_move_the_score_is_still_an_event(tmp_path):
    """H-2 аудита: 60 000 → 50 000 $ у самой выгодной квартиры района —
    «обновлённых 0», и до фазы 3 брокер об этом не узнавал никогда."""
    config = cfg(tmp_path)
    fill(config,
         listings=[suitable("1", price_usd=60_000.0, price_per_sqm=705.0),
                   suitable("2"), suitable("3", price_usd=112_000.0)],
         requests=[make_request("R-1")])
    run_match(config)
    mark = now()
    cheaper(config, "1", 50_000.0, 588.0)

    report = run_match(config)

    assert report.updated == 0, "балл не сдвинулся — ради этого случая тест и написан"
    _, found = events_after(config, mark)
    assert ("cheaper", "1") in found


def test_a_price_drop_is_not_lost_when_the_digest_runs_before_the_match(tmp_path):
    """H-1 аудита: дайджест встал между `scrape` и `match`. До фазы 3 в его
    окне цена упала, но матч не пересчитан, а в следующем окне матч
    пересчитан, но «цена до окна» уже новая, — и подешевевшее терялось."""
    config = cfg(tmp_path)
    fill(config, listings=[suitable("1"), suitable("2", price_usd=118_000.0)],
         requests=[make_request("R-1")])
    run_match(config)
    previous = now()
    cheaper(config, "2", 100_000.0, 1176.0)
    digest_at = now()

    _, before_match = events_after(config, previous, digest_at)
    run_match(config)
    _, after_match = events_after(config, digest_at)

    both = before_match + after_match
    assert ("cheaper", "2") in both, "подешевевшее потеряно"
    assert both.count(("cheaper", "2")) == 1, "и пришло ровно один раз"
