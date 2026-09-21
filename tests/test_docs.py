"""L2, L3: документация и .env.example не должны врать.

После `git clone` папки `data/` нет — она в .gitignore. Ссылка на файл внутри
неё ведёт в пустоту. Ручка в `.env.example`, которую не читает ни один конфиг,
обманывает ровно так же: покрутишь — ничего не изменится.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

# Файлы-инструкции, на которые README ссылается в обратных кавычках
INSTRUCTION_FILE = re.compile(r"`([^`\s]+\.(?:txt|md))`")

# Переменные, которые появятся на следующих этапах: M2 — заявки, M3 — уведомления
PLANNED = {"APP_ENV", "REQUESTS_SHEET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}


def test_readme_points_only_at_files_that_survive_a_clone():
    for reference in INSTRUCTION_FILE.findall(README):
        assert not reference.startswith("data/"), (
            f"README ссылается на {reference}, а вся папка data/ в .gitignore: "
            f"после клонирования файла нет"
        )
        assert (ROOT / reference).exists(), f"README ссылается на несуществующий {reference}"


def test_env_example_has_no_knob_that_nothing_reads():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    declared = {
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    configs = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "config").glob("*.yaml")
    )
    referenced = set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", configs))

    assert declared - referenced - PLANNED == set()
