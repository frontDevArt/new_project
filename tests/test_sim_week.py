"""Расписание имитации — то же, что у задач Планировщика."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.sim.sandbox import build_config
from tests.sim.week import schedule_times

YEREVAN = ZoneInfo("Asia/Yerevan")
START = datetime(2026, 9, 24, 0, 0, tzinfo=YEREVAN)  # календарь: не сравнивается с часами


def test_one_day_is_twenty_four_hourly_one_nightly_one_evening(tmp_path):
    times = schedule_times(build_config(tmp_path), START, days=1)
    names = [name for _, name in times]
    assert names.count("hourly") == 24
    assert names.count("nightly") == 1 and names.count("evening") == 1
    assert [at for at, _ in times] == sorted(at for at, _ in times)
    local = {name: at.astimezone(YEREVAN).strftime("%H:%M") for at, name in times
             if name != "hourly"}
    assert local == {"nightly": "04:30", "evening": "20:30"}
    assert all(START <= at < START + timedelta(days=1) for at, _ in times)


def test_a_folder_with_a_previous_run_is_refused(tmp_path):
    """База прошлого прогона в `--out` смешала бы две недели в одном отчёте."""
    import pytest

    from tests.sim.sandbox import SimRefused
    from tests.sim.week import simulate

    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "listam-sim.sqlite").write_bytes(b"")
    with pytest.raises(SimRefused, match="прошлого прогона"):
        simulate(tmp_path, days=1, synthetic=20, say=lambda _: None)
