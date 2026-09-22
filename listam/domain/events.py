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

# Причина закрытия, которую пишет подбор, когда у кластера сменился
# представитель. Одна строка на подбор и на классификатор: по ней домен
# узнаёт, что квартира не ушла, а сменила карточку.
NOT_REPRESENTATIVE = "не представитель кластера"


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
    смотрит не на матч, а на цену карточки, — и поэтому оно не спрашивает
    `matched_at`: цену двигает рынок, а `matched_at` — пересчёт.
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
    # Рождение в окне уже вернуло `new` выше. Здесь — матч, рождённый до окна,
    # чья цена сейчас ниже цены на начало окна. `matched_at` не спрашиваем:
    # его двигает пересчёт, а не рынок. Глубокая скидка балл не двигает, а
    # дайджест, вставший между обходом и подбором, видит цену раньше пересчёта.
    if price_before is not None and listing.price_usd is not None \
            and listing.price_usd < price_before:
        return MatchEvent(kind=CHEAPER, match=match, listing=listing,
                          price_before=price_before)
    return None


def events_for(rows, since: datetime, until: datetime,
               min_score: float | None) -> list[MatchEvent]:
    """События окна, от лучшего к худшему.

    Двойники склеиваются **до** порога: закрытие прежней карточки и рождение
    новой — одно событие, и решать, проходит ли оно порог, надо по нему, а не
    по половинкам.

    `min_score` не трогает закрытия: закрытие объясняет пропавшую карточку,
    а балл у закрытого матча — вчерашний, и порог о нём ничего не знает.
    """
    classified: list[MatchEvent] = []
    for match, listing, price_before in rows:
        event = classify(match, listing, price_before, since, until)
        if event is not None:
            classified.append(event)

    events: list[MatchEvent] = []
    for event in _merge_twins(classified, since, until):
        if event.kind != RETIRED and min_score is not None \
                and (event.match.score or 0) < min_score:
            continue
        events.append(event)
    events.sort(key=lambda event: (-(event.match.score or 0), event.match.listing_id))
    return events


def _merge_twins(events: list[MatchEvent], since: datetime,
                 until: datetime) -> list[MatchEvent]:
    """Смена представителя кластера — не новая квартира.

    Подбор кладёт в `matches` самую дешёвую карточку кластера. Пришла карточка
    дешевле — у той же квартиры в одном окне два следа: новый матч на новую
    карточку и закрытие прежней с причиной «не представитель кластера».
    Назвать это «новый» — значит позвать брокера звонить по квартире, о которой
    он уже знает; настоящее событие здесь — «подешевела».
    """
    stepped_aside = {
        (event.match.request_id, event.match.cluster_id): event
        for event in events
        if event.kind == RETIRED and event.match.cluster_id
        and event.match.retired_reason == NOT_REPRESENTATIVE
    }
    if not stepped_aside:
        return events

    merged: list[MatchEvent] = []
    absorbed: set[int] = set()
    for event in events:
        partner = stepped_aside.get((event.match.request_id, event.match.cluster_id))
        if event.kind != NEW or partner is None:
            merged.append(event)
            continue
        absorbed.add(id(partner))
        before, now = partner.listing.price_usd, event.listing.price_usd
        if _inside(partner.match.first_matched_at, since, until) \
                or before is None or now is None:
            # Прежнюю карточку брокер не видел (родилась и уступила место
            # в одном окне) или цену не с чем сравнить — квартира для него новая.
            merged.append(event)
        elif now < before:
            merged.append(MatchEvent(kind=CHEAPER, match=event.match,
                                     listing=event.listing, price_before=before))
        # Та же цена — та же квартира по другой карточке: звонить не о чем.
    return [event for event in merged if id(event) not in absorbed]


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
