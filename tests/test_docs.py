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
    # Живую проверку канала (tests/live) настраивают переменные, которые читает
    # не конфиг, а сам код: `os.environ.get("TELEGRAM_LIVE")`. Читает — значит
    # ручка настоящая.
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for folder in ("listam", "tests")
        for path in (ROOT / folder).rglob("*.py")
    )
    read = set(re.findall(r"environ(?:\.get\(|\[)\s*[\"']([A-Z_][A-Z0-9_]*)[\"']", sources))

    assert declared - referenced - read - PLANNED == set()


def test_readme_names_every_command_the_cli_has():
    """Команда, которой нет в README, не существует для человека."""
    from listam.cli import build_parser

    commands = set()
    for action in build_parser()._subparsers._group_actions:
        commands.update(action.choices)

    missing = [name for name in sorted(commands)
               if f"python -m listam {name}" not in README]
    assert missing == [], f"README не упоминает команды: {missing}"


def test_readme_describes_every_column_of_the_requests_table():
    """Колонка, которой нет в README, брокеру не известна — и он её не заполнит."""
    from listam.domain.requests import COLUMNS

    missing = [column for column in COLUMNS if f"`{column}`" not in README]
    assert missing == [], f"README не описывает колонки заявки: {missing}"


def test_readme_names_the_scoring_thresholds_that_the_config_ships_with():
    """Пороги 70 и 40 — то, по чему человек читает витрину."""
    import yaml

    shipped = yaml.safe_load((ROOT / "config" / "prod.yaml").read_text(encoding="utf-8"))
    thresholds = shipped["match"]["thresholds"]

    for value in (thresholds["hot"], thresholds["digest"]):
        assert str(value) in README
