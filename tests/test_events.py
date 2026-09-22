"""Что считается событием: чистые функции, ни базы, ни сети."""
from __future__ import annotations

from datetime import datetime, timezone

from listam.domain.events import CHEAPER, NEW, RETIRED, REVIVED, classify, \
    events_for, limited
from listam.domain.models import Listing, Match

SINCE = datetime(2026, 9, 21, 0, 0, tzinfo=timezone.utc)
INSIDE = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 9, 22, 0, 0, tzinfo=timezone.utc)
BEFORE = datetime(2026, 9, 20, 0, 0, tzinfo=timezone.utc)


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
                    datetime(2026, 9, 23, tzinfo=timezone.utc)) is None


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
