"""Неделя имитации: настоящие циклы по расписанию, рынок между ними, брокер вечером.

Порядок как в жизни: запуск с нуля (`requests`, `scrape`, `match --all`) за
час до начала, дальше — часовой в :00, ночной и вечерний по
`schedule.cycles`. Перед каждым циклом рынок доживает до его часа. Каждый
шаг цикла сдвигает часы на минуту: живой цикл идёт минуты, и отметки
шагов одного цикла не совпадают — окна уведомлений на границах такие же,
как у живых.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import time as wall
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from listam.config import Config, score_threshold
from tests.sim.clock import SimClock, sim_clock
from tests.sim.market import Market, MarketParams
from tests.sim.sandbox import SimRefused, build_config, patched_fetcher, refuse
from tests.sim.site import SimFetcher

START = datetime(2026, 9, 24, 0, 0, tzinfo=ZoneInfo("Asia/Yerevan"))  # календарь: не сравнивается с часами
BOOTSTRAP = ("requests", "scrape", "match --all")
REJECT_WORD = "первый этаж"          # слово из feedback.reasons: сужает заявку
REJECT_OTHER = "не понравилась"      # слова нет в словаре: исключается только квартира


@dataclass
class CycleRecord:
    name: str
    at: datetime
    code: int
    wall_seconds: float


@dataclass
class MarkRecord:
    at: datetime
    request: str
    listing_id: str
    status: str
    reason: str | None
    code: int


@dataclass
class SimResult:
    out: Path
    config: Config
    market: Market
    fetcher: SimFetcher
    start: datetime
    days: int
    bootstrap_codes: list[int] = field(default_factory=list)
    cycles: list[CycleRecord] = field(default_factory=list)
    marks: list[MarkRecord] = field(default_factory=list)
    wall_seconds: float = 0.0


def schedule_times(config: Config, start: datetime, days: int) -> list[tuple[datetime, str]]:
    from listam.schedule import local_zone

    zone = local_zone(config)
    local_start = start.astimezone(zone)
    end = local_start + timedelta(days=days)
    times: list[tuple[datetime, str]] = []
    for name, cycle in config.get("schedule.cycles").items():
        if cycle.get("every_minutes"):
            step = timedelta(minutes=int(cycle["every_minutes"]))
            moment = local_start
            while moment < end:
                times.append((moment, name))
                moment += step
        else:
            hour, minute = (int(part) for part in str(cycle["at"]).split(":"))
            for day in range(days):
                date = (local_start + timedelta(days=day)).date()
                moment = datetime.combine(date, time(hour, minute), tzinfo=zone)
                if local_start <= moment < end:
                    times.append((moment, name))
    return sorted(((at.astimezone(timezone.utc), name) for at, name in times),
                  key=lambda item: item[0])


def _stepping_dispatch(config: Config, clock: SimClock):
    from listam.schedule import _default_dispatch

    inner = _default_dispatch(config)

    def dispatch(step: str) -> int:
        clock.advance(minutes=1)
        return inner(step)

    return dispatch


def _broker(config: Config, dispatch, clock: SimClock) -> list[MarkRecord]:
    """Вечером брокер отмечает по каждой заявке три горячих матча дня."""
    from listam.adapters.db_sqlite import to_iso
    from listam.wiring import database_path

    hot = score_threshold(config, "match.thresholds.hot", 80)
    since = to_iso(clock.now() - timedelta(days=1))
    with contextlib.closing(sqlite3.connect(database_path(config))) as db:
        rows = db.execute(
            """SELECT r.external_id, m.listing_id FROM matches m
               JOIN requests r ON r.id = m.request_id
               WHERE m.status = 'new' AND m.retired_at IS NULL AND m.score >= ?
                 AND m.first_matched_at >= ?
               ORDER BY r.external_id, m.score DESC, m.listing_id""", (hot, since)).fetchall()
    picked: dict[str, list[str]] = {}
    for request, listing_id in rows:
        picked.setdefault(request, [])
        if len(picked[request]) < 3:
            picked[request].append(listing_id)
    plan = [("called", None), ("rejected", REJECT_WORD), ("rejected", REJECT_OTHER)]
    marks = []
    for request, listings in picked.items():
        for listing_id, (status, reason) in zip(listings, plan):
            step = f"mark {request} {listing_id} {status}"
            if reason:
                step += f' --reason "{reason}"'
            code = dispatch(step)
            marks.append(MarkRecord(clock.now(), request, listing_id, status, reason, code))
    return marks


def simulate(out: str | Path, *, days: int = 7, seed: int = 1,
             params: MarketParams = MarketParams(), snapshot: str | Path | None = None,
             synthetic: int = 300, start: datetime = START, say=print) -> SimResult:
    from listam.schedule import run_cycle

    out = Path(out).resolve()
    for leftover in (out / "work", out / "store"):
        if leftover.is_dir() and any(leftover.iterdir()):
            raise SimRefused(f"в {out} база прошлого прогона ({leftover.name}/) — "
                             "две недели смешались бы в одном отчёте; нужна пустая папка")
    config = build_config(out)
    refuse(config)
    boot = start.astimezone(timezone.utc) - timedelta(hours=1)
    market = (Market.from_snapshot(snapshot, seed=seed, params=params, now=boot) if snapshot
              else Market.synthetic(synthetic, seed=seed, params=params, now=boot))
    tick = wall.monotonic()
    with sim_clock(boot) as clock:
        fetcher = SimFetcher(market, clock)
        result = SimResult(out, config, market, fetcher, start, days)
        with patched_fetcher(fetcher), open(os.devnull, "w", encoding="utf-8") as quiet:
            dispatch = _stepping_dispatch(config, clock)
            fetcher.cycle = "bootstrap"
            with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                result.bootstrap_codes = [dispatch(step) for step in BOOTSTRAP]
            say(f"запуск с нуля: коды {result.bootstrap_codes}, "
                f"объявлений {len(market.feed())}")
            for at, name in schedule_times(config, start, days):
                market.advance_to(at)
                clock.set(at)
                fetcher.cycle = f"{name}@{at.isoformat()}"
                began = wall.monotonic()
                with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                    code = run_cycle(config, name, dispatch=dispatch, now=at)
                    marks = _broker(config, dispatch, clock) if name == "evening" else []
                result.cycles.append(CycleRecord(name, at, code, wall.monotonic() - began))
                result.marks += marks
                if name != "hourly" or code != 0:
                    say(f"{at:%d.%m %H:%M} UTC {name}: код {code}, "
                        f"{wall.monotonic() - began:.0f} с, отметок {len(marks)}")
    result.wall_seconds = wall.monotonic() - tick
    return result
