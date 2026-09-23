"""Расписание: цикл из конфига и задачи системного планировщика.

Расписание описано в конфиге (`schedule.cycles`), а исполняется одной командой:
`listam cycle <имя>` проходит шаги цикла по порядку, как `&&`. Системному
планировщику достаётся одна строка на цикл, и `listam schedule install` пишет
её сам — задачу `schtasks` на Windows или строку `crontab` на Linux. Переезд
с одной машины на другую — `git clone`, `.env`, `schedule install`, без правки
кода (решение 3 спеки M3.5).

Цикл не тревожит по пустякам и не молчит о главном (решение 4): занятый замок —
тихий пропуск, упавший шаг — одна строка брокеру, не чаще
`schedule.alert_every_hours`. Лог каждого цикла — в `schedule.log_dir`: при
запуске по расписанию вывод иначе пропадает.
"""
from __future__ import annotations

import contextlib
import io
import re
import shlex
import sys
import time as clock
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from listam.config import Config, ConfigError, hours, positive, switch

# Как цикл называется в тревоге. Имя цикла — ключ конфига, и незнакомое
# печатается как есть: словарь здесь — подпись, а не список разрешённых.
TITLES = {"hourly": "часовой", "nightly": "ночной", "evening": "вечерний"}

CYCLE_KEYS = {"steps", "every_minutes", "at"}
# Шаг не может звать сам цикл или установщик: цикл в цикле — рекурсия, а
# установка задач из задачи — планировщик, переписывающий сам себя.
FORBIDDEN_STEPS = {"cycle", "schedule"}
LOG_NAME = re.compile(r"^cycle-(\d{4}-\d{2}-\d{2})\.log$")
ALERT_MARK = "last-alert"
DEFAULT_KEEP_LOGS_DAYS = 14
DEFAULT_ALERT_EVERY_HOURS = 6.0


@dataclass
class Cycle:
    name: str
    steps: list[str]                 # «scrape --fresh» — аргументы CLI без «python -m listam»
    every_minutes: int | None = None
    at: time | None = None           # местное время locale.timezone


def local_zone(config: Config) -> ZoneInfo:
    """Часовой пояс человека: расписание, день лога, шапка дайджеста."""
    name = config.get("locale.timezone", "UTC") or "UTC"
    try:
        return ZoneInfo(str(name))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"locale.timezone = {name!r} не годится: такого часового пояса нет "
            f"(пример — Asia/Yerevan). На Windows нужен пакет tzdata "
            f"из requirements.txt."
        ) from exc


def cycles(config: Config) -> dict[str, Cycle]:
    """Циклы из `schedule.cycles`, проверенные на входе.

    Опечатка в шаге, найденная планировщиком в четыре утра, — это ночь без
    обхода. Поэтому каждый шаг разбирается тем же разбором, что командная
    строка, ещё при чтении конфига.
    """
    raw = config.get("schedule.cycles")
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("schedule.cycles: циклов нет — расписанию нечего исполнять")
    return {str(name): _cycle(str(name), body) for name, body in raw.items()}


def _cycle(name: str, body) -> Cycle:
    key = f"schedule.cycles.{name}"
    if not isinstance(body, dict):
        raise ConfigError(f"{key}: цикл — это секция с ключами steps и every_minutes или at")
    unknown = sorted(set(body) - CYCLE_KEYS)
    if unknown:
        raise ConfigError(f"{key}: незнакомые ключи {', '.join(unknown)}; "
                          f"у цикла бывают только {', '.join(sorted(CYCLE_KEYS))}")
    steps = body.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ConfigError(f"{key}.steps: шагов нет — цикл ничего бы не делал")
    for step in steps:
        _check_step(f"{key}.steps", step)

    every, at = body.get("every_minutes"), body.get("at")
    if (every is None) == (at is None):
        raise ConfigError(f"{key}: нужен ровно один из ключей every_minutes "
                          f"(«каждые N минут») или at («в ЧЧ:ММ»)")
    if every is not None:
        return Cycle(name=name, steps=list(steps), every_minutes=_every(f"{key}.every_minutes", every))
    return Cycle(name=name, steps=list(steps), at=_at(f"{key}.at", at))


def _check_step(key: str, step) -> None:
    from listam.cli import build_parser

    if not isinstance(step, str) or not step.strip():
        raise ConfigError(f"{key}: шаг {step!r} — не строка команды")
    words = shlex.split(step)
    if words[0].startswith("-"):
        raise ConfigError(f"{key}: шаг «{step}» начинается с флага — окружение и "
                          f"папку конфига шаги наследуют от цикла")
    if words[0] in FORBIDDEN_STEPS:
        raise ConfigError(f"{key}: шаг «{step}» не может звать {words[0]} — "
                          f"цикл в цикле не исполняется")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            build_parser().parse_args(words)
    except SystemExit:
        raise ConfigError(f"{key}: шаг «{step}» — не команда listam "
                          f"(проверь `python -m listam --help`)") from None


def _every(key: str, value) -> int:
    """Период, который одинаково понимают `schtasks` и `cron`.

    Меньше часа — делитель 60 («каждые 15 минут»), больше — целые часы,
    делящие сутки. Иначе на двух платформах вышли бы два разных расписания.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{key} = {value!r} не годится: нужно целое число минут больше нуля")
    if value < 60 and 60 % value == 0:
        return value
    if value % 60 == 0 and 24 % (value // 60) == 0:
        return value
    raise ConfigError(
        f"{key} = {value} не годится: период меньше часа должен делить час "
        f"(5, 10, 15, 20, 30), больше — быть целыми часами, делящими сутки "
        f"(60, 120, 180, 240, 360, 480, 720, 1440). Иначе cron и schtasks "
        f"поймут его по-разному."
    )


def _at(key: str, value) -> time:
    if not isinstance(value, str):
        raise ConfigError(f"{key} = {value!r} не годится: время пишется в кавычках, "
                          f"(\"04:00\"): без них yaml читает 04:00 как число минут")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ConfigError(f"{key} = {value!r} не годится: нужно время ЧЧ:ММ от 00:00 до 23:59")
    return time(int(match.group(1)), int(match.group(2)))


# --- исполнение цикла -----------------------------------------------------

class _Tee(io.TextIOBase):
    """Вывод шага — и в консоль, и в лог: человеку у экрана и планировщику."""

    def __init__(self, *streams):
        # Под планировщиком консоли может не быть (`sys.stdout is None`).
        self.streams = [stream for stream in streams if stream is not None]

    def write(self, text: str) -> int:
        for stream in self.streams:
            try:
                stream.write(text)
                stream.flush()
            except (OSError, ValueError, UnicodeError):
                pass          # консоль закрыта или не та кодировка — лог важнее
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            with contextlib.suppress(OSError, ValueError):
                stream.flush()


def _default_dispatch(config: Config) -> Callable[[str], int]:
    """Шаг исполняется в том же процессе, тем же разбором, что командная строка.

    Флаги `--env` и `--config-dir` цикла шаги наследуют через уже загруженный
    конфиг: шаг не может уйти в чужое окружение.
    """
    from listam import cli

    def dispatch(step: str) -> int:
        args = cli.build_parser().parse_args(shlex.split(step))
        try:
            return cli._dispatch(args, config)
        except ConfigError as exc:
            print(f"Конфигурация не годится: {exc}", file=sys.stderr)
            return 2

    return dispatch


def run_cycle(config: Config, name: str, *, dispatch: Callable[[str], int] | None = None,
              notifier=None, now: datetime | None = None) -> int:
    """Шаги цикла по порядку, как `&&`. Отдаёт код возврата цикла.

    Замок занят — цикл пропущен, код 0: часовой, пришедшийся на ночной, — не
    сбой. Упавший шаг останавливает остальные, и брокеру уходит одна строка.
    """
    zone = local_zone(config)
    cycle = cycles(config)[name]
    log_dir = Path(config.require("schedule.log_dir"))
    keep_days = positive(config, "schedule.keep_logs_days", DEFAULT_KEEP_LOGS_DAYS)
    started = now or datetime.now(timezone.utc)
    local = started.astimezone(zone)

    log_dir.mkdir(parents=True, exist_ok=True)
    _remove_old_logs(log_dir, local.date(), keep_days)
    log_path = log_dir / f"cycle-{local:%Y-%m-%d}.log"
    with log_path.open("a", encoding="utf-8") as log:
        def say(line: str) -> None:
            _Tee(log, sys.stdout).write(line + "\n")

        say(f"=== {local:%Y-%m-%d %H:%M} ({zone.key}) · цикл {name}: "
            + " → ".join(cycle.steps))

        from listam.wiring import build_run_lock

        holder = build_run_lock(config).busy()
        if holder is not None:
            say(f"пропущен: идёт другой прогон ({holder})")
            return 0

        dispatch = dispatch or _default_dispatch(config)
        code, failed = 0, None
        tick = clock.monotonic()
        for step in cycle.steps:
            say(f"--- шаг: {step}")
            tee = _Tee(log, sys.stdout)
            with contextlib.redirect_stdout(tee), contextlib.redirect_stderr(tee):
                try:
                    code = dispatch(step)
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
                except Exception:
                    traceback.print_exc()
                    code = 1
            code = int(code or 0)
            say(f"--- шаг «{step}»: код {code}")
            if code != 0:
                failed = step
                break
        say(f"=== цикл {name}: код {code} за {clock.monotonic() - tick:.0f} с")

        if failed is not None:
            _alert(config, cycle, failed, code, started, log_dir, log_path, notifier, say)
    return code


def _remove_old_logs(log_dir: Path, today: date, keep_days) -> None:
    if keep_days is None:
        return
    border = today - timedelta(days=int(keep_days))
    for path in log_dir.glob("cycle-*.log"):
        match = LOG_NAME.match(path.name)
        if match is None:
            continue
        try:
            day = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if day < border:
            with contextlib.suppress(OSError):
                path.unlink()


def _alert(config: Config, cycle: Cycle, step: str, code: int, now: datetime,
           log_dir: Path, log_path: Path, notifier, say) -> None:
    """Одна строка брокеру, не чаще окна. Отметка — файл, а не база: упавший
    шаг мог быть как раз про базу."""
    if not switch(config, "schedule.alert_on_failure", True):
        return
    every = hours(config, "schedule.alert_every_hours", DEFAULT_ALERT_EVERY_HOURS)
    mark = log_dir / ALERT_MARK
    last = _read_mark(mark)
    if last is not None and now - last < timedelta(hours=every):
        say(f"тревога не послана: прошлая ушла {last:%Y-%m-%d %H:%M} UTC, "
            f"окно {every:g} ч (schedule.alert_every_hours)")
        return

    title = TITLES.get(cycle.name)
    what = f"{title} цикл" if title else f"цикл {cycle.name}"
    text = (f"⚠️ listam: {what} не прошёл — шаг «{step}» вернул код {code}. "
            f"Лог: {log_path.as_posix()}")
    try:
        if notifier is None:
            from listam.wiring import build_notifier

            notifier = build_notifier(config)
        notifier.send(text)
    except Exception as exc:        # NotifyError, ConfigError без токена, сеть
        say(f"тревога не ушла: {exc}")
        return
    mark.write_text(now.astimezone(timezone.utc).isoformat(), encoding="utf-8")
    say("тревога послана")


def _read_mark(path: Path) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


# --- задачи системного планировщика ---------------------------------------

PLATFORMS = ("windows", "linux")
WRAPPER_DIR = Path("data") / "schedule"     # относительно корня проекта: data/ не коммитится
DEFAULT_PREFIX = "listam"


class ScheduleError(Exception):
    """Планировщик системы отказал: задача не создалась или не снялась."""


@dataclass
class Task:
    """Задача `schtasks`: имя, аргументы `/create` и обёртка `.cmd`.

    Команда живёт в обёртке, а не в `/tr`: `schtasks /tr` не берёт больше
    261 символа, а путь к интерпретатору в venv съедает половину.
    """
    name: str
    args: list[str]
    wrapper_path: Path
    wrapper: str
    when: str                       # по-человечески, для `schedule show`


Runner = Callable[..., tuple[int, str, str]]


def this_platform() -> str | None:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return None


def _system(cmd: list[str], input: str | None = None) -> tuple[int, str, str]:
    """Настоящий вызов `schtasks` / `crontab`. Вывод — в кодировке системы."""
    import subprocess

    done = subprocess.run(cmd, input=input, capture_output=True, text=True,
                          errors="replace")
    return done.returncode, done.stdout or "", done.stderr or ""


def _prefix(config: Config) -> str:
    prefix = str(config.get("schedule.task_prefix") or DEFAULT_PREFIX)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", prefix):
        raise ConfigError(f"schedule.task_prefix = {prefix!r} не годится: только латиница, "
                          f"цифры, «-» и «_» — это имя задачи и метка в crontab")
    return prefix


def _host_time(at: time, zone: ZoneInfo, host_tz: tzinfo | None, now: datetime | None) -> time:
    """Местное время брокера → часы хоста, по которым живёт планировщик.

    Считается на сегодняшний день: хосту с переходом на летнее время задачи
    нужно переустановить после перехода — пояс брокера (Ереван) его не знает.
    """
    today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
    moment = datetime.combine(today, at, tzinfo=zone)
    host = host_tz or datetime.now().astimezone().tzinfo
    return moment.astimezone(host).time()


def _when(cycle: Cycle) -> str:
    if cycle.every_minutes is not None:
        return f"каждые {cycle.every_minutes} мин"
    return f"ежедневно в {cycle.at:%H:%M}"


def windows_tasks(config: Config, root: Path, python: str, env: str, *,
                  host_tz: tzinfo | None = None, now: datetime | None = None) -> list[Task]:
    zone = local_zone(config)
    prefix = _prefix(config)
    tasks = []
    for cycle in cycles(config).values():
        name = f"{prefix}-{cycle.name}"
        wrapper_path = Path(root) / WRAPPER_DIR / f"{name}.cmd"
        if cycle.every_minutes is not None:
            # С начала суток: часовой идёт ровно в начале часа, как `0 * * * *`
            # у cron, а не с той минуты, когда задачу поставили.
            when = ["/sc", "minute", "/mo", str(cycle.every_minutes), "/st", "00:00"]
            human = _when(cycle)
        else:
            host = _host_time(cycle.at, zone, host_tz, now)
            when = ["/sc", "daily", "/st", f"{host:%H:%M}"]
            human = f"{_when(cycle)} ({zone.key}), {host:%H:%M} по часам хоста"
        wrapper = "\r\n".join([
            "@echo off",
            f"rem {name}: цикл {cycle.name}. Пишет и снимает `listam schedule install|remove`.",
            "chcp 65001 >nul",
            "set PYTHONIOENCODING=utf-8",
            f'cd /d "{root}"',
            f'"{python}" -m listam --env {env} cycle {cycle.name}',
            "",
        ])
        # /it — только пока пользователь в системе: обход открывает окно
        # браузера, а без рабочего стола Cloudflare его не пропускает.
        args = ["schtasks", "/create", "/tn", name, "/tr", f'"{wrapper_path}"',
                *when, "/it", "/f"]
        tasks.append(Task(name=name, args=args, wrapper_path=wrapper_path,
                          wrapper=wrapper, when=human))
    return tasks


def _cron_when(cycle: Cycle, zone: ZoneInfo, host_tz, now) -> str:
    minutes = cycle.every_minutes
    if minutes is not None:
        if minutes < 60:
            return f"*/{minutes} * * * *"
        if minutes == 60:
            return "0 * * * *"
        if minutes == 1440:
            return "0 0 * * *"
        return f"0 */{minutes // 60} * * *"
    host = _host_time(cycle.at, zone, host_tz, now)
    return f"{host.minute} {host.hour} * * *"


def cron_lines(config: Config, root, python: str, env: str, *,
               host_tz: tzinfo | None = None, now: datetime | None = None) -> list[str]:
    """Строки crontab. Метка `# <prefix>:<цикл>` — чтобы снимать только свои."""
    zone = local_zone(config)
    prefix = _prefix(config)
    xvfb = "xvfb-run -a " if switch(config, "schedule.linux_xvfb", True) else ""
    return [
        f'{_cron_when(cycle, zone, host_tz, now)} cd "{root}" && {xvfb}"{python}" '
        f"-m listam --env {env} cycle {cycle.name} # {prefix}:{cycle.name}"
        for cycle in cycles(config).values()
    ]


def _manifest(root, prefix: str) -> Path:
    """Какие задачи ставили в прошлый раз: цикл, убранный из конфига, снимается.

    По префиксу искать нельзя: `listam-` — начало и у задач `listam-dev-…`.
    """
    return Path(root) / WRAPPER_DIR / f"{prefix}.tasks"


def _read_manifest(path: Path) -> list[str]:
    try:
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    except OSError:
        return []


def _mine(line: str, prefix: str) -> bool:
    return f"# {prefix}:" in line


def _read_crontab(run: Runner) -> list[str]:
    code, out, err = run(["crontab", "-l"])
    if code != 0:
        if "no crontab" in (err + out).lower():
            return []
        raise ScheduleError(f"crontab -l отказал (код {code}): {err.strip() or out.strip()}")
    return out.splitlines()


def _write_crontab(run: Runner, lines: list[str]) -> None:
    text = "\n".join(lines).rstrip("\n") + "\n"
    code, out, err = run(["crontab", "-"], input=text)
    if code != 0:
        raise ScheduleError(f"crontab отказал (код {code}): {err.strip() or out.strip()}")


def install(config: Config, platform: str, *, root, python: str, run: Runner | None = None,
            host_tz: tzinfo | None = None, now: datetime | None = None) -> list[str]:
    """Ставит задачи платформы по конфигу. Отдаёт строки отчёта."""
    run = run or _system
    prefix = _prefix(config)
    if platform == "linux":
        lines = cron_lines(config, root=root, python=python, env=config.env,
                           host_tz=host_tz, now=now)
        kept = [line for line in _read_crontab(run) if not _mine(line, prefix)]
        _write_crontab(run, kept + lines)
        return [f"crontab: {len(lines)} строк с меткой # {prefix}:"] + lines

    tasks = windows_tasks(config, root=Path(root), python=python, env=config.env,
                          host_tz=host_tz, now=now)
    manifest = _manifest(root, prefix)
    wanted = {task.name for task in tasks}
    report: list[str] = []
    for stale in _read_manifest(manifest):
        if stale not in wanted:
            _delete_task(run, stale, report)
            (Path(root) / WRAPPER_DIR / f"{stale}.cmd").unlink(missing_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        task.wrapper_path.write_text(task.wrapper, encoding="utf-8", newline="")
        code, out, err = run(task.args)
        if code != 0:
            raise ScheduleError(f"schtasks не создал {task.name} (код {code}): "
                                f"{err.strip() or out.strip()}")
        report.append(f"задача {task.name}: {task.when} → {task.wrapper_path}")
    manifest.write_text("\n".join(sorted(wanted)) + "\n", encoding="utf-8")
    return report


def _delete_task(run: Runner, name: str, report: list[str]) -> None:
    code, out, err = run(["schtasks", "/delete", "/tn", name, "/f"])
    if code == 0:
        report.append(f"задача {name} снята")
    else:
        report.append(f"задача {name} не снята (код {code}): {err.strip() or out.strip()}")


def remove(config: Config, platform: str, *, root, run: Runner | None = None) -> list[str]:
    """Снимает свои задачи: строки с меткой в crontab или задачи из манифеста."""
    run = run or _system
    prefix = _prefix(config)
    if platform == "linux":
        table = _read_crontab(run)
        kept = [line for line in table if not _mine(line, prefix)]
        _write_crontab(run, kept)
        return [f"crontab: снято строк с меткой # {prefix}: — {len(table) - len(kept)}"]

    manifest = _manifest(root, prefix)
    names = set(_read_manifest(manifest)) | {f"{prefix}-{name}" for name in cycles(config)}
    report: list[str] = []
    for name in sorted(names):
        _delete_task(run, name, report)
        (Path(root) / WRAPPER_DIR / f"{name}.cmd").unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)
    return report


def show(config: Config, platform: str, *, root, python: str,
         host_tz: tzinfo | None = None, now: datetime | None = None) -> list[str]:
    """Что поставит `install` на платформе — ничего не трогая."""
    if platform == "linux":
        return ["crontab (Linux):"] + cron_lines(config, root=root, python=python,
                                                 env=config.env, host_tz=host_tz, now=now)
    lines = ["schtasks (Windows):"]
    for task in windows_tasks(config, root=Path(root), python=python, env=config.env,
                              host_tz=host_tz, now=now):
        lines.append(f"{task.name}: {task.when}")
        lines.append("  " + " ".join(task.args))
        lines.extend("  | " + line for line in task.wrapper.splitlines())
    return lines
