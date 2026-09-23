"""Отчёт имитации: разбор текста уведомлений и счёт."""
from __future__ import annotations

from tests.sim.report import CardSection, percentile, sections

HOT = """\
🔥 ЗВОНИ СЕЙЧАС · R-1 · ПРИМЕР узкая
Арабкир · 3 комн. · 70–95 м² · до $120 000
━━━━━━━━━━━━━━━

🆕 $109 900 · 75 м² · $1 465/м² (−43% к району)
📍 Арабкир · этаж 2/5
👤 Собственник · 🟢 балл 100 · 🔗 Открыть: https://www.list.am/ru/item/24123988

📉 $120 000 · 82 м² · $1 463/м² (−43% к району)
📍 Арабкир · этаж 4/5
🏢 Агентство · 🟢 балл 95 · 🔗 Открыть: https://www.list.am/ru/item/23803420
➕ ещё 3 варианта — в дайджесте вечером

🔥 ЗВОНИ СЕЙЧАС · R-3 · ПРИМЕР широкая
━━━━━━━━━━━━━━━

🆕 $200 000 · 90 м²
👤 Собственник · 🟡 балл 82 · 🔗 Открыть: https://www.list.am/ru/item/24000001
"""


def test_a_hot_text_splits_into_request_sections():
    assert sections(HOT, {"R-1", "R-2", "R-3"}) == [
        CardSection("hot", "R-1", ["24123988", "23803420"], 3),
        CardSection("hot", "R-3", ["24000001"], 0),
    ]


def test_an_empty_hot_has_no_sections():
    assert sections("🔥 ЗВОНИ СЕЙЧАС · событий нет", {"R-1"}) == []


def test_a_digest_summary_head_is_not_a_request():
    text = ("📋 ДАЙДЖЕСТ · 23.09 · 21:07\n\n📋 ДАЙДЖЕСТ · R-1 · ПРИМЕР узкая — первичная подборка\n"
            "🔗 Открыть: https://www.list.am/ru/item/5\n")
    assert sections(text, {"R-1"}) == [CardSection("digest", "R-1", ["5"], 0)]


def test_percentile_is_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.5) == 5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.95) == 10
    assert percentile([], 0.5) is None


def test_fits_uses_the_system_hard_criteria_more_rooms_and_area_are_fine():
    """Жёсткие критерии спеки M2: комнат не меньше минимума, площадь не ниже
    `area_min`; список комнат и `area_max` — мягкий фактор балла, не фильтр."""
    import csv
    import io

    from listam.domain.requests import parse_rows
    from tests.sim.report import _fits
    from tests.sim.sandbox import EXAMPLE_REQUESTS

    parsed, _ = parse_rows(csv.DictReader(io.StringIO(EXAMPLE_REQUESTS)))
    r2 = next(r for r in parsed if r.external_id == "R-2")
    big = {"district": "Канакер-Зейтун", "rooms": 4, "area": 139.0,
           "currency": "USD", "price": 129800}
    assert _fits(big, r2, 10.0, 363.25)
    assert not _fits({**big, "rooms": 1}, r2, 10.0, 363.25)
    assert not _fits({**big, "area": 70.0}, r2, 10.0, 363.25)
    assert not _fits({**big, "district": "Ачапняк"}, r2, 10.0, 363.25)
    assert not _fits({**big, "price": 250000}, r2, 10.0, 363.25)


def test_a_drop_whose_day_has_not_ended_is_pending_not_missed():
    """С-8: «не дошло за 24 ч» знает только закрытое окно. Подешевевшее за
    сутки до конца прогона ещё может дойти — оно не промах, а «ждёт»."""
    from datetime import datetime, timedelta, timezone

    from tests.sim.market import TruthEvent
    from tests.sim.report import unseen_drops

    t0 = datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами
    end = t0 + timedelta(days=7)
    shown = TruthEvent("cheaper", "1", t0)
    lost = TruthEvent("cheaper", "2", t0)
    late = TruthEvent("cheaper", "3", end - timedelta(hours=5))
    delivered = [(t0 + timedelta(hours=2), CardSection("hot", "R-1", ["1"], 0))]
    assert unseen_drops([shown, lost, late], delivered, end) == ([lost], [late])
