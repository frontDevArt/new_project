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


def test_readme_says_which_commands_migrate_the_base():
    """Приёмка фазы 8 QA: `match --all` на схеме 7 не отказал, а накатил
    миграции и поехал. `doctor` при этом говорил «запускать рано».
    Ни план, ни README этого не говорили."""
    assert "мигрируют базу сами" in README
    assert "python -m listam recheck" in README
    for command in ("scrape", "cluster", "requests", "match", "notify"):
        assert f"`{command}`" in README.split("мигрируют базу сами")[1][:600], (
            f"абзац о самомиграции не называет `{command}`"
        )


def test_the_schedule_sends_the_notifications():
    """Уведомление, которое никто не запускает, брокеру не приходит.

    Спека M3 («Команды») ставит `notify --hot` в часовой слой за `match --new`,
    а `notify --digest` — раз в сутки. С M3.5 шаги живут в `schedule.cycles`,
    а задачи `schtasks` и строки `cron` пишет `schedule install` (решение 3):
    проверяется поставляемый конфиг и его выдержка в README.
    """
    import yaml

    for env in ("dev", "prod"):
        shipped = yaml.safe_load((ROOT / "config" / f"{env}.yaml").read_text(encoding="utf-8"))
        cycles = shipped["schedule"]["cycles"]
        hourly = [c["steps"] for c in cycles.values() if c.get("every_minutes")]
        daily = [c["steps"] for c in cycles.values() if c.get("at")]
        assert any(steps.index("notify --hot") > steps.index("match --new")
                   for steps in hourly if "notify --hot" in steps and "match --new" in steps), env
        assert any("notify --digest" in steps for steps in daily), env

    schedule = README.split("## Как запускать по расписанию")[1].split("\n## ")[0]
    lines = schedule.splitlines()
    hourly = [line for line in lines if "match --new" in line and "scrape --fresh" in line]
    assert hourly and all("notify --hot" in line for line in hourly), hourly
    assert any("notify --digest" in line and "at:" in line for line in lines)
    for command in ("schedule show", "schedule install", "schedule remove"):
        assert f"python -m listam {command}" in schedule, command


# Пометка на строке с датой из календаря: «эта дата с часами кода не встречается».
CALENDAR_MARK = "# календарь: не сравнивается с часами"


def test_no_calendar_dates_against_real_clock():
    """Дата из календаря в тесте, которую код сравнивает с `now()`, — бомба:
    сегодня тест зелёный, завтра падает без единой правки. Приёмка QA после M3
    нашла такие в подборе. Каждая календарная дата в тестах либо помечена
    как не встречающаяся с часами кода, либо заменена на «сейчас ± дельта»."""
    unmarked = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "datetime(20" in line and CALENDAR_MARK not in line \
                    and "CALENDAR_MARK" not in line and '"datetime(20"' not in line:
                unmarked.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not unmarked, (
        "дата из календаря без пометки — сравнивает ли её код с настоящими "
        "часами? Сравнивает — datetime.now(timezone.utc) ± timedelta; нет — "
        f"пометка «{CALENDAR_MARK}»:\n" + "\n".join(unmarked)
    )
