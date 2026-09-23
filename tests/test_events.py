"""Что считается событием: чистые функции, ни базы, ни сети."""
from __future__ import annotations

from datetime import datetime, timezone

from listam.domain.events import CHEAPER, NEW, NOT_REPRESENTATIVE, RETIRED, \
    REVIVED, classify, events_for, limited
from listam.domain.models import Listing, Match

SINCE = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами
INSIDE = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами
UNTIL = datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами
BEFORE = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами


def listing(price=150000.0) -> Listing:
    return Listing(id="1", url="https://www.list.am/ru/item/1", district="Кентрон",
                   price_usd=price, area=68.0, rooms=2)


def match(**over) -> Match:
    fields = dict(request_id=1, listing_id="1", score=85.0,
                  first_matched_at=INSIDE, matched_at=INSIDE)
    fields.update(over)
    return Match(**fields)


def test_a_match_born_inside_the_window_is_new():
    event = classify(match(), listing(), None, SINCE, UNTIL)
    assert event.kind == NEW


def test_a_match_that_only_got_recounted_is_not_an_event():
    """Пересчёт двигает cluster_size у тысяч матчей: это не событие рынка."""
    event = classify(match(first_matched_at=BEFORE), listing(), None, SINCE, UNTIL)
    assert event is None


def test_a_match_whose_listing_got_cheaper_is_an_event():
    event = classify(match(first_matched_at=BEFORE), listing(150000.0), 225000.0,
                     SINCE, UNTIL)
    assert event.kind == CHEAPER
    assert event.price_before == 225000.0


def test_a_match_whose_listing_got_dearer_is_not_an_event():
    """Подорожание не повод звонить: если вариант вышел за бюджет, полный
    проход его закроет, и это уже другое событие."""
    assert classify(match(first_matched_at=BEFORE), listing(260000.0), 225000.0,
                    SINCE, UNTIL) is None


def test_a_retired_match_is_an_event_of_its_own_kind():
    event = classify(match(retired_at=INSIDE, retired_reason="бюджет"),
                     listing(), None, SINCE, UNTIL)
    assert event.kind == RETIRED


def test_a_revived_match_is_an_event_once():
    """Воскресший — событие ровно один раз: в следующем окне отметка возврата
    уже позади, и матч молчит, пока с ним снова что-нибудь не случится."""
    revived = match(first_matched_at=BEFORE, revived_at=INSIDE)
    assert classify(revived, listing(), None, SINCE, UNTIL).kind == REVIVED
    assert classify(revived, listing(), None, UNTIL,
                    datetime(2026, 9, 23, tzinfo=timezone.utc)) is None  # календарь: не сравнивается с часами


def test_a_retired_match_never_pretends_to_be_new():
    """Закрытый матч в уведомление не уходит ни под каким видом — даже если
    родился в этом же окне."""
    event = classify(match(retired_at=INSIDE), listing(), None, SINCE, UNTIL)
    assert event.kind == RETIRED


def test_events_are_filtered_by_score_and_sorted():
    rows = [
        (match(score=91.0, listing_id="a"), listing(), None),
        (match(score=45.0, listing_id="b"), listing(), None),
    ]
    events = events_for(rows, SINCE, UNTIL, min_score=70.0)
    assert [event.match.listing_id for event in events] == ["a"]


def test_retired_events_ignore_the_score_floor():
    """Закрытие объясняет пропавшую карточку, а балл у закрытого — вчерашний."""
    rows = [(match(score=41.0, retired_at=INSIDE), listing(), None)]
    assert [event.kind for event in events_for(rows, SINCE, UNTIL, min_score=70.0)] \
        == [RETIRED]


def test_limited_keeps_the_best_and_tells_the_truth_about_the_rest():
    events = events_for(
        [(match(score=float(90 - index), listing_id=str(index)), listing(), None)
         for index in range(7)],
        SINCE, UNTIL, min_score=None,
    )
    shown, total = limited(events, 3)
    assert total == 7
    assert [event.match.listing_id for event in shown] == ["0", "1", "2"]


def test_limited_without_a_ceiling_shows_everything():
    events = events_for([(match(), listing(), None)], SINCE, UNTIL, min_score=None)
    shown, total = limited(events, None)
    assert len(shown) == total == 1


def card(listing_id: str, price: float) -> Listing:
    return Listing(id=listing_id, url=f"https://www.list.am/ru/item/{listing_id}",
                   district="Кентрон", price_usd=price, area=68.0, rooms=2)


def twins(old_born=BEFORE, reason=NOT_REPRESENTATIVE, new_price=145000.0):
    """Одна квартира, две карточки: прежняя уступила место новой в этом окне."""
    old = match(listing_id="old", cluster_id="c1", first_matched_at=old_born,
                matched_at=old_born, retired_at=INSIDE, retired_reason=reason)
    new = match(listing_id="new", cluster_id="c1")
    return [(old, card("old", 150000.0), None), (new, card("new", new_price), None)]


def test_a_cheaper_twin_is_a_cheaper_flat_and_not_a_new_one():
    """Брокер эту квартиру уже видел. Новое в ней одно — цена."""
    events = events_for(twins(), SINCE, UNTIL, min_score=None)

    assert [(event.kind, event.match.listing_id, event.price_before)
            for event in events] == [(CHEAPER, "new", 150000.0)]


def test_a_twin_at_the_same_price_is_not_an_event_at_all():
    """Та же квартира по той же цене под другой карточкой: звонить не о чем."""
    assert events_for(twins(new_price=150000.0), SINCE, UNTIL, min_score=None) == []


def test_a_twin_of_a_card_nobody_saw_is_new():
    """Прежняя карточка родилась и уступила место в одном окне — брокер её
    не видел, и квартира для него новая."""
    events = events_for(twins(old_born=INSIDE), SINCE, UNTIL, min_score=None)

    assert [(event.kind, event.match.listing_id) for event in events] == [(NEW, "new")]


def test_a_closure_for_another_reason_is_not_merged():
    """«Бюджет» — это другая история: вариант отпал, а не сменил карточку."""
    events = events_for(twins(reason="бюджет"), SINCE, UNTIL, min_score=None)

    assert sorted(event.kind for event in events) == sorted([NEW, RETIRED])


def test_a_price_drop_is_an_event_even_when_the_recount_did_not_move():
    """Глубокая скидка упирается в потолок фактора выгодности: балл тот же,
    `matched_at` не двигается, а квартира стала дешевле на $10 000."""
    quiet = match(first_matched_at=BEFORE, matched_at=BEFORE)

    event = classify(quiet, listing(50000.0), 60000.0, SINCE, UNTIL)

    assert event.kind == CHEAPER
    assert event.price_before == 60000.0
