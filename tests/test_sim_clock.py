"""Часы имитации: весь listam видит время имитации, и только его."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.sim.clock import listam_modules, sim_clock

START = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами
LISTAM = Path(__file__).resolve().parents[1] / "listam"


def test_every_listam_module_sees_the_sim_clock():
    import listam.crawler
    import listam.notifications

    with sim_clock(START) as clock:
        assert listam.crawler.datetime.now(timezone.utc) == START
        clock.advance(hours=1)
        assert listam.notifications.datetime.now(timezone.utc) == START + timedelta(hours=1)
    assert listam.crawler.datetime is datetime


def test_lazily_imported_modules_see_the_sim_clock_too():
    with sim_clock(START):
        from listam.adapters import rate_fixed, storage_local  # wiring грузит их внутри функций

        assert rate_fixed.datetime.now(timezone.utc) == START
        assert all(getattr(m, "datetime", None) is not datetime for m in listam_modules()
                   if "datetime" in vars(m))


def test_a_real_datetime_is_still_a_datetime_under_the_sim_clock():
    with sim_clock(START):
        from listam.adapters import exporter_xlsx

        assert isinstance(datetime(2026, 1, 1), exporter_xlsx.datetime)  # календарь: не сравнивается с часами
        assert isinstance(START + timedelta(days=1), exporter_xlsx.datetime)


def test_database_timestamps_round_trip_under_the_sim_clock():
    from listam.adapters import db_sqlite

    with sim_clock(START):
        moment = db_sqlite.datetime.now(timezone.utc)
        assert db_sqlite.from_iso(db_sqlite.to_iso(moment)) == START


def test_naive_now_is_host_local_time_of_the_sim_moment():
    from listam import schedule

    with sim_clock(START):
        naive = schedule.datetime.now()
        assert naive.tzinfo is None
        assert naive == START.astimezone().replace(tzinfo=None)


def test_the_sim_clock_does_not_run_backwards():
    with sim_clock(START) as clock:
        with pytest.raises(ValueError):
            clock.set(START - timedelta(seconds=1))


# Источник времени в listam — только `datetime.now(...)` модульного `datetime`.
# Иначе имитация недели молча смешает часы стены с часами имитации.
FORBIDDEN = [
    (re.compile(r"\btime\.time\(\)"), "time.time()"),
    (re.compile(r"\bfrom time import\b"), "from time import"),
    (re.compile(r"\bdate\.today\(\)"), "date.today()"),
    (re.compile(r"\bdatetime\.(utcnow|today)\("), "datetime.utcnow()/today()"),
    (re.compile(r"^\s*import datetime\b", re.M), "import datetime"),
    (re.compile(r"\bdatetime\.datetime\b"), "datetime.datetime"),
    (re.compile(r"['\"]now['\"]"), "SQL 'now'"),
    (re.compile(r"CURRENT_(TIMESTAMP|DATE|TIME)\b"), "SQL CURRENT_*"),
]
# Возраст файла замка сравнивается с его mtime — это часы стены по делу.
ALLOWED = {("adapters/run_lock.py", "time.time()")}


def test_listam_takes_time_only_through_module_datetime_now():
    found = []
    for path in sorted(LISTAM.rglob("*")):
        if path.suffix not in (".py", ".sql"):
            continue
        text = path.read_text(encoding="utf-8")
        name = path.relative_to(LISTAM).as_posix()
        for pattern, label in FORBIDDEN:
            if pattern.search(text) and (name, label) not in ALLOWED:
                found.append(f"{name}: {label}")
    assert found == [], "источник времени мимо sim_clock: " + ", ".join(found)
