"""Фаза 7 M3.5 (долг №8): README — руководство оператора, «почему так» — в
`docs/устройство.md`.

README в 100 КБ брокер не читает: команда, которую он ищет, тонет в разборе
того, почему прогон устроен так, а не иначе. Объяснения не выброшены — они
переехали в документ об устройстве, и README на него ссылается.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
DESIGN_PATH = ROOT / "docs" / "устройство.md"

README_LIMIT_BYTES = 25 * 1024

# Разделы руководства оператора (задача 7.2 плана M3.5)
OPERATOR_SECTIONS = (
    "Быстрый старт", "Команды", "Заявки", "расписанию", "Telegram",
    "Воронка", "Отметки", "Переезд",
)


def design() -> str:
    assert DESIGN_PATH.exists(), "нет docs/устройство.md — объяснениям некуда переехать"
    return DESIGN_PATH.read_text(encoding="utf-8")


def test_the_readme_fits_in_25_kb():
    size = len(README.encode("utf-8"))
    assert size <= README_LIMIT_BYTES, f"README {size} байт, предел {README_LIMIT_BYTES}"


def test_the_readme_sends_the_why_to_the_design_doc():
    design()
    assert "`docs/устройство.md`" in README


def test_the_readme_has_the_operator_sections():
    headings = [line for line in README.splitlines() if line.startswith("## ")]
    missing = [name for name in OPERATOR_SECTIONS
               if not any(name in heading for heading in headings)]
    assert missing == [], f"нет разделов: {missing}; есть: {headings}"


def test_the_design_doc_points_only_at_files_that_survive_a_clone():
    for reference in re.findall(r"`([^`\s]+\.(?:txt|md))`", design()):
        assert not reference.startswith("data/"), reference
        assert (ROOT / reference).exists(), f"ссылка на несуществующий {reference}"


def test_the_design_doc_hardcodes_no_path():
    text = design()
    for bad in ("C:\\", "/srv/"):
        assert bad not in text, f"зашитый путь {bad}"
