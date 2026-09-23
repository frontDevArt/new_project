"""Отчёт имитации: С-1…С-10 спеки, каждое число — с источником.

Источники два. База имитации (`runs`, `notifications`, `matches`,
`request_exclusions`) — то, что сделала система. Правда рынка и журнал
подставного list.am — то, что было на самом деле. Разрыв между ними и есть
находка.
"""
from __future__ import annotations

import contextlib
import csv
import json
import math
import re
import sqlite3
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from listam.adapters.db_sqlite import from_iso, to_iso
from listam.config import score_threshold
from listam.domain.requests import parse_rows
from listam.wiring import database_path
from tests.sim.week import schedule_times

HEADS = {"🔥 ЗВОНИ СЕЙЧАС": "hot", "📋 ДАЙДЖЕСТ": "digest"}
HEAD = re.compile(r"^(🔥 ЗВОНИ СЕЙЧАС|📋 ДАЙДЖЕСТ) · ([^\s·—]+)")
URL = re.compile(r"/item/(\d+)")
MORE = re.compile(r"➕ ещё (\d+)")
AREA_SLACK = 5.0          # м²: С-6 ловит явные промахи воронки, а не допуски подбора


@dataclass
class CardSection:
    kind: str
    request: str
    ids: list[str] = field(default_factory=list)
    more: int = 0

    @property
    def events(self) -> int:
        return len(self.ids) + self.more


def sections(text: str, requests: set[str]) -> list[CardSection]:
    found: list[CardSection] = []
    current: CardSection | None = None
    for line in (text or "").splitlines():
        head = HEAD.match(line)
        if head:
            current = (CardSection(HEADS[head.group(1)], head.group(2))
                       if head.group(2) in requests else None)
            if current is not None:
                found.append(current)
            continue
        if current is None:
            continue
        current.ids += URL.findall(line)
        more = MORE.search(line)
        if more:
            current.more += int(more.group(1))
    return found


def percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


@dataclass
class Metric:
    id: str
    title: str
    value: object
    threshold: str
    ok: bool | None
    source: str


def _requests(result):
    with open(result.config.require("requests.path"), encoding="utf-8", newline="") as handle:
        parsed, _ = parse_rows(csv.DictReader(handle))
    return [request for request in parsed if request.status == "active"]


def _fits(facts: dict, request, percent: float, amd_per_usd: float) -> bool:
    """Жёсткие критерии спеки M2: район, комнат не меньше минимума, бюджет с
    растяжкой, площадь не ниже `area_min`. Список комнат и `area_max` — мягкий
    фактор балла (`area_rooms`): квартира больше заказанной кандидатом быть может."""
    usd = facts["price"] if facts["currency"] == "USD" else facts["price"] / amd_per_usd
    ceiling = request.stretch(percent)
    return ((not request.districts or facts["district"] in request.districts)
            and (not request.rooms or facts["rooms"] is None
                 or facts["rooms"] >= min(request.rooms))
            and (ceiling is None or usd <= ceiling * 1.01)
            and (request.area_min is None or facts["area"] is None
                 or facts["area"] >= request.area_min - AREA_SLACK))


def unseen_drops(drops, delivered, end: datetime):
    """С-8: подешевевшие без карточки за 24 ч — и те, чьи 24 ч к концу прогона
    не истекли: про них «не дошло» ещё не известно, они не промах."""
    missed, pending = [], []
    for event in drops:
        if any(event.at <= sent <= event.at + timedelta(hours=24) and event.flat_id in section.ids
               for sent, section in delivered):
            continue
        (pending if event.at + timedelta(hours=24) > end else missed).append(event)
    return missed, pending


def compute(result) -> list[Metric]:
    config = result.config
    requests = _requests(result)
    ids = {request.external_id for request in requests}
    wishful = {r.external_id for r in requests if r.must_have or r.nice_to_have}
    per_request = config.get("notify.hot.per_request")
    cap = config.get("funnel.max_opens_per_run")
    hot_score = score_threshold(config, "match.thresholds.hot", 80)
    digest_score = score_threshold(config, "match.thresholds.digest", 40)
    since = to_iso(result.start)
    metrics: list[Metric] = []

    with contextlib.closing(sqlite3.connect(database_path(config))) as db:
        runs = db.execute("SELECT count(*), coalesce(sum(errors > 0), 0) FROM runs "
                          "WHERE mode = 'full' AND started_at >= ?", (since,)).fetchone()
        notes = [(from_iso(sent), kind, events, text) for sent, kind, events, text in db.execute(
            "SELECT sent_at, kind, events, text FROM notifications WHERE sent_at >= ? "
            "ORDER BY sent_at", (since,))]
        hot_ids = {row[0] for row in db.execute(
            "SELECT DISTINCT listing_id FROM matches WHERE score >= ?", (hot_score,))}
        digest_ids = {row[0] for row in db.execute(
            "SELECT DISTINCT listing_id FROM matches WHERE score >= ?", (digest_score,))}
    delivered: list[tuple[datetime, CardSection]] = [
        (sent, section) for sent, kind, _, text in notes if kind in ("hot", "digest")
        for section in sections(text, ids)]

    # С-1, С-2 — циклы
    bad = [c for c in result.cycles if c.code != 0]
    bad_marks = [m for m in result.marks if m.code != 0]
    ran = len(result.cycles)
    planned = len(schedule_times(config, result.start, result.days))
    metrics.append(Metric("С-1", "циклы: всего / код ≠ 0 / отметок с кодом ≠ 0",
                          f"{ran} / {len(bad)} / {len(bad_marks)}",
                          f"{planned} / 0 / 0",
                          ran == planned and not bad and not bad_marks
                          and not any(result.bootstrap_codes),
                          "коды run_cycle и mark; запуск с нуля: "
                          f"{result.bootstrap_codes}; упавшие: "
                          + ", ".join(f"{c.name}@{c.at:%d.%m %H:%M}" for c in bad[:5])))
    metrics.append(Metric("С-2", "ночных full / из них с ошибками",
                          f"{runs[0]} / {runs[1]}", f"{result.days} / 0",
                          runs[0] == result.days and runs[1] == 0,
                          "SELECT count(*), sum(errors > 0) FROM runs WHERE mode='full' "
                          "AND started_at >= старт"))

    # С-3, С-4 — «звони сейчас»
    hot_rows = [(sent, events, text) for sent, kind, events, text in notes if kind == "hot"]
    hot_sections = [s for _, _, text in hot_rows for s in sections(text, ids)]
    counts = [s.events for s in hot_sections]
    median = statistics.median(counts) if counts else None
    p95 = percentile(counts, 0.95)
    # Карточки layout режет на per_request — по ним порог выполнен всегда;
    # они рядом, чтобы было видно, что «НЕТ» дал хвост «➕ ещё N».
    cards = [len(s.ids) for s in hot_sections]
    metrics.append(Metric(
        "С-3", "«звони сейчас»: сообщений (пустых) / на заявку — медиана, p95",
        f"{len(hot_rows)} ({sum(1 for _, e, _ in hot_rows if not e)}) / "
        f"{median}, {p95} — события; "
        f"{statistics.median(cards) if cards else None}, {percentile(cards, 0.95)} — карточки",
        f"медиана ≤ 3, p95 ≤ {per_request}",
        None if median is None else median <= 3 and p95 <= per_request,
        "notifications kind='hot': секции «🔥 ЗВОНИ СЕЙЧАС · R-…», события = карточки + «➕ ещё N»"))
    wish_sections = [s for s in hot_sections if s.request in wishful]
    at_cap = [s for s in wish_sections if len(s.ids) >= per_request]
    metrics.append(Metric("С-4", "заявки с пожеланиями у потолка per_request (Д-11)",
                          f"{len(at_cap)} из {len(wish_sections)} секций", "— (число в отчёт)", None,
                          f"секции заявок {sorted(wishful)} с {per_request} карточками"))

    # С-5, С-6 — воронка
    items = [f for f in result.fetcher.journal if f.kind == "item"]
    per_cycle: dict[str, int] = {}
    for fetch in items:
        per_cycle[fetch.cycle or "?"] = per_cycle.get(fetch.cycle or "?", 0) + 1
    worst = max(per_cycle.values(), default=0)
    metrics.append(Metric("С-5", "страниц открыто: всего / максимум за цикл",
                          f"{len(items)} / {worst}", f"максимум ≤ {cap}", worst <= cap,
                          "журнал подставного list.am, kind='item', по меткам цикла"))
    percent = float(config.get("match.budget_stretch_percent", 10))
    rate = float(config.get("rate.amd_per_usd"))
    outside = [f for f in items if f.status == 200
               and not any(_fits(f.facts, r, percent, rate) for r in requests)]
    metrics.append(Metric("С-6", "открытия вне жёстких фильтров всех заявок",
                          len(outside), "0", not outside,
                          "район, комнат ≥ минимума, бюджет с растяжкой ×1.01, "
                          "площадь ≥ area_min − 5 м² — своим кодом; первые: " + ", ".join(f.key for f in outside[:5])))

    # С-7 — задержка нового горячего
    first_seen: dict[str, datetime] = {}
    for sent, section in delivered:
        if section.kind == "hot":
            for listing_id in section.ids:
                first_seen.setdefault(listing_id, sent)
    born = [e for e in result.market.truth if e.kind in ("new", "relist") and e.flat_id in hot_ids]
    lags = [(first_seen[e.flat_id] - e.at).total_seconds() / 3600
            for e in born if e.flat_id in first_seen]
    lag_median = statistics.median(lags) if lags else None
    metrics.append(Metric(
        "С-7", "новое горячее: дошло / всего; задержка, ч — медиана, максимум",
        f"{len(lags)} / {len(born)}; "
        f"{None if lag_median is None else round(lag_median, 2)}, "
        f"{None if not lags else round(max(lags), 2)}",
        "медиана ≤ 1 ч", None if lag_median is None else lag_median <= 1.0,
        "правда рынка (new/relist, у объявления матч ≥ hot) → первое «звони сейчас» с его ссылкой"))

    # С-8 — подешевевшее подходящее (Д-10)
    drops = [e for e in result.market.truth if e.kind == "cheaper" and e.flat_id in digest_ids]
    end = result.cycles[-1].at if result.cycles else result.start
    missed, pending = unseen_drops(drops, delivered, end)
    metrics.append(Metric("С-8", "подешевевшие с матчем ≥ digest: не дошли за 24 ч / всего "
                          "(ещё в окне на конец прогона) (Д-10)",
                          f"{len(missed)} / {len(drops) - len(pending)} ({len(pending)})",
                          "— (число в отчёт)", None,
                          "правда рынка (cheaper) против «звони сейчас» и дайджеста за 24 ч; "
                          "окно, не закрытое к последнему циклу, — в скобках, не в счёте"))

    # С-9 — вернувшийся отказ (Д-12)
    key_of = {flat.id: flat.flat_key for flat in result.market.flats.values()}
    returned = []
    for mark in (m for m in result.marks if m.status == "rejected" and m.code == 0):
        rejected_key = key_of.get(mark.listing_id)
        for sent, section in delivered:
            if (section.kind == "hot" and section.request == mark.request and sent > mark.at
                    and any(key_of.get(i) == rejected_key for i in section.ids)):
                returned.append((mark.request, mark.listing_id, sent))
                break
    metrics.append(Metric("С-9", "отвергнутые квартиры, вернувшиеся в «звони сейчас» (Д-12)",
                          len(returned), "0", not returned,
                          "отметки rejected → поздние секции той же заявки с квартирой того же "
                          "flat_key; первые: " + ", ".join(f"{r}:{l}" for r, l, _ in returned[:5])))

    # С-10 — размер и время
    size = database_path(config).stat().st_size
    kinds: dict[str, int] = {}
    for event in result.market.truth:
        kinds[event.kind] = kinds.get(event.kind, 0) + 1
    metrics.append(Metric("С-10", "база, МБ / время стены, мин / рынок: старт → конец / события",
                          f"{size / 2**20:.1f} / {result.wall_seconds / 60:.1f} / "
                          f"{result.market.start_active} → {len(result.market.feed())} / {kinds}",
                          "— (число в отчёт)", None,
                          f"файл {database_path(config).name}; снимок без полей: "
                          f"{result.market.skipped}"))
    return metrics


def render_markdown(result, metrics: list[Metric]) -> str:
    params = result.market.params
    lines = [
        f"Имитация: {result.days} сут. с {result.start:%d.%m.%Y %H:%M %Z}, "
        f"новых в час {params.new_per_hour:g}, зерно рынка — см. команду; "
        f"рабочая папка {result.out}",
        "",
        "| # | Что | Значение | Порог | Итог |",
        "| --- | --- | --- | --- | --- |",
    ]
    for m in metrics:
        verdict = {True: "да", False: "**НЕТ**", None: "—"}[m.ok]
        lines.append(f"| {m.id} | {m.title} | {m.value} | {m.threshold} | {verdict} |")
    lines += ["", "Источники:"] + [f"* {m.id}: {m.source}" for m in metrics]
    return "\n".join(lines) + "\n"


def to_json(result, metrics: list[Metric]) -> str:
    return json.dumps({"days": result.days, "start": result.start.isoformat(),
                       "params": asdict(result.market.params),
                       "metrics": [asdict(m) for m in metrics]},
                      ensure_ascii=False, indent=2, default=str)
