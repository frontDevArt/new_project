"""`listam mark`: «звонил» и «отказ» — обратная связь брокера (фаза 6 M3.5).

След звонка (`matches.status`, `matches.reject_reason`) пишет только человек,
и только эта команда (решение 7 спеки M2). Пересчёт его не трогает.

Решение 15 спеки M3.5: отказ клиента сужает заявку **в базе**, а не в
таблице — таблицу ведёт брокер, и синхронизация её не перезаписывает.

* Отвергнутая квартира (кластер) исключается всегда, какой бы ни была
  причина: второй раз её клиенту не покажут ни этой карточкой, ни двойником.
* Причина разбирается по словарю `feedback.reasons`: «первый этаж»,
  «район» (район этого объявления), поле страницы («тип дома» — значение
  с открытой страницы этого объявления). Слова нет в словаре или страницы
  нет — причина записана в `reject_reason`, заявку не сужает, и команда
  так и говорит.
* `new` — откат: статус и причина стираются, исключения этой отметки
  снимаются. Чужие отметки по той же заявке остаются.

После отметки — подбор по этой заявке: отпавшее закрывается со словами
«клиент отказал: …» и уходит в вечерний дайджест.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from listam.config import Config, ConfigError
from listam.domain.models import Exclusion, Match
from listam.domain.scoring import (CLUSTER, DISTRICT, FIRST_FLOOR, LAST_FLOOR,
                                   refusal_words)
from listam.domain.wishes import field_label
from listam.matching import MatchReport, run_match
from listam.ports.database import Database
from listam.runner import SessionRefused, publish, working_session

STATUSES = ("called", "rejected", "new")


class MarkError(Exception):
    """Бессмысленный ввод: код 2, до работы."""


def _word(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def reasons(config: Config) -> dict[str, str]:
    """Словарь `feedback.reasons`: слово отказа → что исключить. Кривой — отказ на входе."""
    raw = config.get("feedback.reasons", None)
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("feedback.reasons должен быть словарём "
                          "«слово: {exclude: что исключить}»")
    vocab: dict[str, str] = {}
    for word, entry in raw.items():
        problem = None
        if not isinstance(entry, dict):
            problem = "нужна секция {exclude: …}"
        elif set(entry) - {"exclude"}:
            problem = f"таких ключей нет: {', '.join(sorted(set(entry) - {'exclude'}))}"
        elif not isinstance(entry.get("exclude"), str) or not entry["exclude"].strip():
            problem = "не названо, что исключить (exclude)"
        if problem:
            raise ConfigError(
                f"feedback.reasons: «{word}» — {problem}. Исключить можно "
                f"{FIRST_FLOOR}, {LAST_FLOOR}, {DISTRICT} или поле страницы "
                f"(building_type, renovation …)."
            )
        vocab[_word(word)] = entry["exclude"].strip()
    return vocab


@dataclass
class MarkReport:
    """Что сделала отметка. Печатает CLI."""

    external_id: str
    listing_id: str
    status: str
    match_listing: str | None = None       # на каком объявлении лежит матч
    applied: list[Exclusion] = field(default_factory=list)
    dropped: int = 0                        # снято исключений прошлой отметки
    recorded_only: list[str] = field(default_factory=list)
    refusal: str | None = None              # почему отметки нет: код 1
    matched: MatchReport | None = None
    errors: int = 0
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        where = self.match_listing or self.listing_id
        via = (f" (матч лежит на {self.match_listing} — тот же кластер)"
               if self.match_listing and self.match_listing != self.listing_id else "")
        lines = [f"Отметка: {self.external_id} · {where} → {self.status}{via}"]
        if self.dropped:
            lines.append(f"Снято исключений прошлой отметки: {self.dropped}")
        if self.status == "rejected":
            words = refusal_words(self.applied)
            lines.append("Квартира исключена из заявки (кластер)")
            if words:
                lines.append("Заявка сужена: " + ", ".join(words))
        lines.extend(self.recorded_only)
        lines.extend(self.notes)
        if self.matched is not None:
            lines.append(self.matched.render())
        return "\n".join(lines)


def _find_match(database: Database, request_id: int, listing_id: str) -> Match | None:
    """Матч по объявлению, иначе по его кластеру (карточку могли сменить на дешёвую).

    Из нескольких в кластере — живой прежде закрытого.
    """
    matches = database.matches_for_request(request_id, include_retired=True)
    for match in matches:
        if match.listing_id == listing_id:
            return match
    listing = database.get_listing(listing_id)
    cluster = listing.cluster_id if listing else None
    if cluster is None:
        return None
    same = [match for match in matches if match.cluster_id == cluster]
    same.sort(key=lambda match: match.retired_at is not None)
    return same[0] if same else None


def _page_value(database: Database, kind: str, listing_ids: list[str]):
    for listing_id in listing_ids:
        page = database.get_page(listing_id)
        if page is not None and page.status == "ok" and page.fields is not None:
            value = page.fields.values.get(kind)
            if value is not None and not isinstance(value, (bool, int, float)):
                return str(value)
    return None


def _exclusions(database: Database, report: MarkReport, match: Match,
                request_id: int, reason: str | None, vocab: dict[str, str],
                now: datetime) -> list[Exclusion]:
    """Кластер — всегда; остальное — по словам причины из словаря."""
    def exclusion(kind, value=None, words=None) -> Exclusion:
        return Exclusion(request_id=request_id, kind=kind, value=value,
                         reason=words, match_id=match.id, created_at=now)

    found = [exclusion(CLUSTER, match.cluster_id or match.listing_id,
                       reason.strip() if reason and reason.strip() else None)]
    listing = database.get_listing(match.listing_id)
    for part in (reason or "").split(","):
        word = _word(part)
        if not word:
            continue
        kind = vocab.get(word)
        if kind is None:
            report.recorded_only.append(
                f"«{word}» нет в feedback.reasons — причина записана, "
                f"заявку не сужает (исключена только эта квартира)")
            continue
        if kind in (FIRST_FLOOR, LAST_FLOOR):
            found.append(exclusion(kind, words=word))
        elif kind == DISTRICT:
            if listing is None or not listing.district:
                report.recorded_only.append(
                    f"«{word}»: район объявления неизвестен — причина записана, "
                    f"заявку не сужает")
                continue
            found.append(exclusion(kind, listing.district, word))
        else:
            value = _page_value(database, kind, [match.listing_id, report.listing_id])
            if value is None:
                report.recorded_only.append(
                    f"«{word}»: поля «{field_label(kind)}» нет — страница объявления "
                    f"не открыта или не назвала его; причина записана, заявку не сужает")
                continue
            found.append(exclusion(kind, value, word))
    return found


def run_mark(config: Config, external_id: str, listing_id: str, status: str,
             reason: str | None = None) -> MarkReport:
    """Отметка матча и её исключения, затем подбор по заявке. Сводку печатает CLI."""
    if status not in STATUSES:
        raise MarkError(f"статус {status!r} не годится: бывает {', '.join(STATUSES)}")
    if reason is not None and status != "rejected":
        raise MarkError("--reason — только для rejected: у звонка и отката причины нет")
    # Словарь — до замка: кривой конфиг отклоняется на входе, а не посреди записи.
    vocab = reasons(config)

    report = MarkReport(external_id=external_id, listing_id=listing_id, status=status)
    active = False
    try:
        with working_session(config) as session:
            report.notes.extend(session.notes)
            session.notes = report.notes
            database = session.database

            request = database.get_request(external_id)
            if request is None:
                report.refusal = (f"заявки {external_id} в базе нет — сначала прочитай "
                                  f"источник: python -m listam requests")
                report.errors = 1
                return report
            match = _find_match(database, request.id, listing_id)
            if match is None:
                report.refusal = (f"у заявки {external_id} нет матча на {listing_id} "
                                  f"и его кластер")
                report.errors = 1
                return report
            report.match_listing = match.listing_id
            active = request.status == "active"

            now = datetime.now(timezone.utc)
            found = (_exclusions(database, report, match, request.id, reason, vocab, now)
                     if status == "rejected" else [])
            # Повторная отметка заменяет исключения прошлой, а не копит их.
            # Три записи — три транзакции порта (вложенных у него нет); оборвись
            # между ними — повтор той же команды доводит базу до конца.
            report.dropped = database.drop_exclusions(match.id)
            database.set_match_status(
                match.id, status, reason if status == "rejected" else None)
            database.add_exclusions(found)
            report.applied = found
            publish(session, config, "база с отметкой")
            report.errors += session.failures
    except SessionRefused as exc:
        report.errors = 1
        report.notes.append(str(exc))
        return report

    if not active:
        report.notes.append(f"заявка {external_id} не active — подбор по ней не шёл")
        return report
    # Подбор — после сессии: у него свой замок и своя заливка.
    report.matched = run_match(config, external_id=external_id)
    report.errors += report.matched.errors
    return report
