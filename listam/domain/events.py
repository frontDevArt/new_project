"""Что считается событием: новый вариант, подешевевший, вернувшийся, закрытый.

Чистые функции. Базы здесь нет: выборку отдаёт порт
(`Database.match_events_since`), а эти функции решают, как назвать
случившееся и что из этого показывать человеку.

Два правила, из-за которых модуль и появился:

* **Пересчёт — не событие.** Ночной `match --all` двигает `matched_at` у
  тысяч матчей: меняется размер кластера, медиана района, разброс цен.
  Показать это человеку — значит утопить настоящее событие в шуме.
* **Подешевело — это про цену, а не про балл.** Балл двигают и кластер,
  и медиана; единственный честный признак — цена до окна против цены сейчас.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from listam.domain.models import Listing, Match

NEW = "new"
CHEAPER = "cheaper"
REVIVED = "revived"
RETIRED = "retired"

# Подписи для человека: один словарь на уведомление и на витрину, как
# `labels.MATCH_STATUSES` — два словаря с одним смыслом однажды разойдутся.
EVENT_LABELS = {
    NEW: "новый",
    CHEAPER: "подешевел",
    REVIVED: "вернулся",
    RETIRED: "отпал",
}


@dataclass
class MatchEvent:
    """Что случилось с матчем в окне: вид, матч, карточка и цена до окна."""

    kind: str
    match: Match
    listing: Listing
    price_before: float | None = None


def _inside(when: datetime | None, since: datetime, until: datetime) -> bool:
    """Отметка попала в окно `(since, until]`.

    Нижняя граница строгая, верхняя — нет: `since` это `window_to` прошлой
    отправки, и событие, ушедшее в неё, не имеет права уйти второй раз.
    """
    return when is not None and since < when <= until


def classify(match: Match, listing: Listing, price_before: float | None,
             since: datetime, until: datetime) -> MatchEvent | None:
    """Вид события или `None`, если в окне с матчем ничего не случилось.

    Порядок правил — это и есть решение. Закрытие идёт первым: закрытый матч
    не имеет права выглядеть новым, даже если он в этом же окне родился.
    Воскресение — вторым: оно крупнее пересчёта. Рождение — третьим.
    Подешевение — последним, потому что это единственное правило, которое
    смотрит не на матч, а на цену карточки.
    """
    if _inside(match.retired_at, since, until):
        return MatchEvent(kind=RETIRED, match=match, listing=listing)
    if match.retired_at is not None:
        # Закрыт раньше окна и не воскрес — звонить по нему некуда.
        return None
    if _inside(match.revived_at, since, until):
        return MatchEvent(kind=REVIVED, match=match, listing=listing,
                          price_before=price_before)
    if _inside(match.first_matched_at, since, until):
        return MatchEvent(kind=NEW, match=match, listing=listing)
    if _inside(match.matched_at, since, until) \
            and price_before is not None and listing.price_usd is not None \
            and listing.price_usd < price_before:
        return MatchEvent(kind=CHEAPER, match=match, listing=listing,
                          price_before=price_before)
    return None


def events_for(rows, since: datetime, until: datetime,
               min_score: float | None) -> list[MatchEvent]:
    """События окна, от лучшего к худшему.

    `min_score` не трогает закрытия: закрытие объясняет пропавшую карточку,
    а балл у закрытого матча — вчерашний, и порог о нём ничего не знает.
    """
    events: list[MatchEvent] = []
    for match, listing, price_before in rows:
        event = classify(match, listing, price_before, since, until)
        if event is None:
            continue
        if event.kind != RETIRED and min_score is not None \
                and (event.match.score or 0) < min_score:
            continue
        events.append(event)
    events.sort(key=lambda event: (-(event.match.score or 0), event.match.listing_id))
    return events


def limited(events: list[MatchEvent], per_request: int | None
            ) -> tuple[list[MatchEvent], int]:
    """Показанные события и сколько их всего.

    Два числа, а не одно: «показаны 10 из 412» — то же обещание, что «…и ещё N»
    в витрине, и оно обязано быть правдой. Одна широкая заявка даёт
    7 515 горячих матчей — потолок здесь не удобство, а условие разговора.
    """
    if per_request is None:
        return events, len(events)
    return events[:per_request], len(events)
