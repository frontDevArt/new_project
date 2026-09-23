"""Расписание: цикл из конфига, лог, тревога, задачи планировщика.

Системного планировщика здесь нет: `schtasks` и `crontab` зовутся через
внедрённый `run`, шаги цикла — через внедрённый `dispatch`. Проверяется
договор: что цикл делает с кодами шагов, замком, логом и тревогой, и какие
строки он отдаёт планировщику каждой платформы.
"""
from __future__ import annotations

import copy
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from listam.adapters.run_lock import RunLock
from listam.config import Config, ConfigError
from listam.ports.notifier import Notifier, NotifyError
from listam.schedule import (Cycle, cron_lines, cycles, install, remove, run_cycle,
                             windows_tasks)
from listam.wiring import run_lock_path

ROOT = Path(__file__).resolve().parent.parent

SCHEDULE = {
    "log_dir": None,                     # заполняется tmp_path
    "keep_logs_days": 14,
    "task_prefix": "listam-test",
    "alert_on_failure": True,
    "alert_every_hours": 6,
    "linux_xvfb": True,
    "cycles": {
        "hourly": {"every_minutes": 60,
                   "steps": ["scrape --fresh", "match --new", "notify --hot"]},
        "nightly": {"at": "04:00", "steps": ["scrape", "match --all", "notify --feed"]},
        "evening": {"at": "20:00", "steps": ["notify --digest"]},
    },
}


def make_config(tmp_path: Path, **schedule_over) -> Config:
    schedule = copy.deepcopy(SCHEDULE)
    schedule["log_dir"] = str(tmp_path / "logs")
    schedule.update(schedule_over)
    data = {
        "env": "test",
        "storage": {"kind": "local", "directory": str(tmp_path / "remote"),
                    "work_dir": str(tmp_path / "work"), "db_filename": "listam.sqlite"},
        "locale": {"timezone": "Asia/Yerevan"},
        "schedule": schedule,
        "notify": {"kind": "stdout"},
    }
    return Config(data, env="test", path=Path("config/test.yaml"))


class Recorder(Notifier):
    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail

    def send(self, text, to=None):
        if self.fail:
            raise NotifyError("сеть отказала")
        self.sent.append(text)

    def describe(self):
        return "запись в память"


def dispatcher(codes: dict[str, int], called: list[str]):
    def dispatch(step: str) -> int:
        called.append(step)
        print(f"вывод шага {step}")
        return codes.get(step, 0)
    return dispatch


NOW = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами


def log_text(config: Config) -> str:
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in sorted(Path(config.get("schedule.log_dir")).glob("cycle-*.log")))


# --- чтение циклов --------------------------------------------------------

def test_cycles_are_read_from_the_config(tmp_path):
    found = cycles(make_config(tmp_path))

    assert found["hourly"] == Cycle(name="hourly", every_minutes=60,
                                    steps=["scrape --fresh", "match --new", "notify --hot"])
    assert found["evening"].at == time(20, 0)
    assert found["evening"].every_minutes is None


@pytest.mark.parametrize("cycle, key", [
    ({"every_minutes": 60, "steps": []}, "schedule.cycles.bad.steps"),
    ({"every_minutes": 60}, "schedule.cycles.bad.steps"),
    ({"every_minutes": 60, "steps": ["scarpe"]}, "schedule.cycles.bad.steps"),
    ({"every_minutes": 60, "steps": ["cycle hourly"]}, "schedule.cycles.bad.steps"),
    ({"every_minutes": 60, "steps": ["--env prod scrape"]}, "schedule.cycles.bad.steps"),
    ({"at": "25:00", "steps": ["scrape"]}, "schedule.cycles.bad.at"),
    ({"at": 240, "steps": ["scrape"]}, "schedule.cycles.bad.at"),
    ({"every_minutes": 60, "at": "04:00", "steps": ["scrape"]}, "schedule.cycles.bad"),
    ({"steps": ["scrape"]}, "schedule.cycles.bad"),
    ({"every_minutes": 0, "steps": ["scrape"]}, "schedule.cycles.bad.every_minutes"),
    ({"every_minutes": 45, "steps": ["scrape"]}, "schedule.cycles.bad.every_minutes"),
    ({"every_minutes": 60, "step": ["scrape"], "steps": ["scrape"]}, "schedule.cycles.bad"),
])
def test_cycles_are_read_and_checked(tmp_path, cycle, key):
    """Бессмысленный цикл — отказ на входе с именем ключа: опечатка в шаге,
    найденная планировщиком в четыре утра, — это ночь без обхода."""
    config = make_config(tmp_path, cycles={"bad": cycle})

    with pytest.raises(ConfigError, match=key.replace(".", r"\.")):
        cycles(config)


def test_a_config_without_cycles_is_refused(tmp_path):
    with pytest.raises(ConfigError, match=r"schedule\.cycles"):
        cycles(make_config(tmp_path, cycles={}))


def test_an_unknown_timezone_is_refused(tmp_path):
    config = make_config(tmp_path)
    config.data["locale"]["timezone"] = "Asia/Erevan"
    with pytest.raises(ConfigError, match=r"locale\.timezone"):
        run_cycle(config, "evening", dispatch=dispatcher({}, []), notifier=Recorder(), now=NOW)


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_the_shipped_cycles_are_valid(env, monkeypatch, tmp_path):
    from listam.config import load_config

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1720")
    config = load_config(env=env, config_dir=ROOT / "config", dotenv_path=tmp_path / ".env")

    assert set(cycles(config)) == {"hourly", "nightly", "evening"}


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_the_shipped_fixed_cycles_do_not_start_with_a_periodic_one(env, monkeypatch, tmp_path):
    """Занятый замок — тихий пропуск (решение 4). Ночной в 04:00 и часовой
    в 04:00 стартуют разом: кто первым взял замок, тот и прошёл, второй
    пропущен. Пропущенный ночной — сутки без полного обхода, пропущенный
    вечерний — день без дайджеста. Циклы «в ЧЧ:ММ» не встречаются с
    периодическими в одну минуту."""
    from listam.config import load_config

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1720")
    found = cycles(load_config(env=env, config_dir=ROOT / "config",
                               dotenv_path=tmp_path / ".env")).values()

    periodic = [cycle.every_minutes for cycle in found if cycle.every_minutes]
    for cycle in found:
        if cycle.at is None:
            continue
        minute_of_day = cycle.at.hour * 60 + cycle.at.minute
        clashes = [every for every in periodic if minute_of_day % every == 0]
        assert not clashes, f"{cycle.name} в {cycle.at:%H:%M} стартует вместе с периодическим"


# --- исполнение цикла -----------------------------------------------------

def test_a_cycle_runs_its_steps_in_order(tmp_path):
    config = make_config(tmp_path)
    called: list[str] = []

    code = run_cycle(config, "hourly", dispatch=dispatcher({}, called),
                     notifier=Recorder(), now=NOW)

    assert code == 0
    assert called == ["scrape --fresh", "match --new", "notify --hot"]


def test_a_cycle_stops_at_the_first_failing_step(tmp_path):
    """Шаги — как `&&`: подбор по недообойдённой ленте хуже, чем никакого."""
    config = make_config(tmp_path)
    called: list[str] = []

    code = run_cycle(config, "hourly", dispatch=dispatcher({"match --new": 1}, called),
                     notifier=Recorder(), now=NOW)

    assert code == 1
    assert called == ["scrape --fresh", "match --new"]


def test_a_step_that_crashes_is_a_failed_step(tmp_path):
    config = make_config(tmp_path)

    def dispatch(step):
        raise RuntimeError("браузер не открылся")

    code = run_cycle(config, "hourly", dispatch=dispatch, notifier=Recorder(), now=NOW)

    assert code == 1
    assert "браузер не открылся" in log_text(config)


def test_a_busy_lock_skips_the_cycle_quietly(tmp_path):
    """Часовой пришёлся на ночной: пропуск — не сбой. Код 0, тревоги нет."""
    config = make_config(tmp_path)
    called: list[str] = []
    notifier = Recorder()
    holder = RunLock(run_lock_path(config)).acquire()
    try:
        code = run_cycle(config, "hourly", dispatch=dispatcher({}, called),
                         notifier=notifier, now=NOW)
    finally:
        holder.release()

    assert code == 0
    assert called == []
    assert notifier.sent == []
    assert "пропущен: идёт другой прогон" in log_text(config)


def test_the_cycle_writes_its_log(tmp_path):
    """По расписанию вывод иначе пропадает: лог — единственный свидетель."""
    config = make_config(tmp_path)

    run_cycle(config, "hourly", dispatch=dispatcher({"notify --hot": 3}, []),
              notifier=Recorder(), now=NOW)

    # 08:00 UTC — 12:00 в Ереване: день лога — местный.
    log = Path(config.get("schedule.log_dir")) / "cycle-2026-09-23.log"
    text = log.read_text(encoding="utf-8")
    assert "цикл hourly" in text
    assert "12:00" in text
    for step in ("scrape --fresh", "match --new", "notify --hot"):
        assert f"вывод шага {step}" in text
    assert "код 3" in text


def test_a_cycle_without_a_console_still_logs(tmp_path, monkeypatch):
    """Под планировщиком консоли может не быть вовсе (`sys.stdout is None`):
    лог — единственный свидетель, и он пишется."""
    config = make_config(tmp_path)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    code = run_cycle(config, "hourly", dispatch=dispatcher({}, []), notifier=Recorder(),
                     now=NOW)

    assert code == 0
    assert "вывод шага notify --hot" in log_text(config)


def test_the_log_day_is_local(tmp_path):
    """22:30 UTC — уже 02:30 следующего дня в Ереване."""
    config = make_config(tmp_path)
    late = datetime(2026, 9, 23, 22, 30, tzinfo=timezone.utc)  # календарь: не сравнивается с часами

    run_cycle(config, "evening", dispatch=dispatcher({}, []), notifier=Recorder(), now=late)

    assert (Path(config.get("schedule.log_dir")) / "cycle-2026-09-24.log").exists()


def test_old_logs_are_removed(tmp_path):
    config = make_config(tmp_path, keep_logs_days=14)
    logs = Path(config.get("schedule.log_dir"))
    logs.mkdir(parents=True)
    old = logs / "cycle-2026-09-01.log"
    kept = logs / "cycle-2026-09-10.log"
    stranger = logs / "notes.txt"
    for path in (old, kept, stranger):
        path.write_text("x", encoding="utf-8")

    run_cycle(config, "evening", dispatch=dispatcher({}, []), notifier=Recorder(), now=NOW)

    assert not old.exists()
    assert kept.exists()
    assert stranger.exists(), "чужой файл в папке логов не трогается"


def test_a_failed_cycle_alerts_once_per_window(tmp_path):
    """Два падения подряд — одна строка брокеру: тревога, которая приходит
    каждый час, перестаёт читаться. Отметка — файл в папке логов, а не база:
    упавший шаг мог быть как раз про базу."""
    config = make_config(tmp_path)
    notifier = Recorder()
    failing = dispatcher({"scrape --fresh": 1}, [])

    first = run_cycle(config, "hourly", dispatch=failing, notifier=notifier, now=NOW)
    second = run_cycle(config, "hourly", dispatch=failing, notifier=notifier,
                       now=NOW + timedelta(hours=1))

    assert (first, second) == (1, 1)
    assert len(notifier.sent) == 1
    assert "\n" not in notifier.sent[0], "тревога — одна строка"
    assert notifier.sent[0].startswith("⚠️ listam: часовой цикл не прошёл")
    assert "scrape --fresh" in notifier.sent[0]
    assert (Path(config.get("schedule.log_dir")) / "last-alert").exists()

    third = run_cycle(config, "hourly", dispatch=failing, notifier=notifier,
                      now=NOW + timedelta(hours=6))
    assert third == 1
    assert len(notifier.sent) == 2, "окно прошло — тревога снова"


def test_a_successful_cycle_does_not_alert(tmp_path):
    notifier = Recorder()
    run_cycle(make_config(tmp_path), "hourly", dispatch=dispatcher({}, []),
              notifier=notifier, now=NOW)
    assert notifier.sent == []


def test_alerts_can_be_switched_off(tmp_path):
    notifier = Recorder()
    config = make_config(tmp_path, alert_on_failure=False)
    run_cycle(config, "hourly", dispatch=dispatcher({"scrape --fresh": 1}, []),
              notifier=notifier, now=NOW)
    assert notifier.sent == []


def test_an_alert_that_did_not_leave_keeps_the_cycle_code(tmp_path):
    """Сеть легла — тревога не ушла: строка в логе, код цикла прежний, и
    отметка не ставится — следующий сбой попробует снова."""
    config = make_config(tmp_path)

    code = run_cycle(config, "hourly", dispatch=dispatcher({"match --new": 2}, []),
                     notifier=Recorder(fail=True), now=NOW)

    assert code == 2
    assert "тревога не ушла" in log_text(config)
    assert not (Path(config.get("schedule.log_dir")) / "last-alert").exists()


# --- командная строка -----------------------------------------------------

def write_config(tmp_path: Path, config: Config) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "test.yaml").write_text(yaml.safe_dump(config.data, allow_unicode=True),
                                          encoding="utf-8")
    return config_dir


def test_an_unknown_cycle_is_refused_with_the_known_ones(tmp_path, capsys):
    from listam.cli import main

    config_dir = write_config(tmp_path, make_config(tmp_path))

    code = main(["--env", "test", "--config-dir", str(config_dir), "cycle", "hourli"])

    assert code == 2
    err = capsys.readouterr().err
    assert "hourli" in err
    for name in ("hourly", "nightly", "evening"):
        assert name in err


def test_the_cycle_command_runs_the_steps_of_its_environment(tmp_path, monkeypatch):
    """Шаги наследуют окружение цикла через уже загруженный конфиг: `cycle`
    зовёт тот же `_dispatch`, что командная строка, с конфигом цикла."""
    import listam.cli as cli

    seen = []
    real = cli._dispatch

    def spy(args, config):
        if args.command == "cycle":
            return real(args, config)
        seen.append((args.command, args.digest, config.env))
        return 0

    monkeypatch.setattr(cli, "_dispatch", spy)
    config = make_config(tmp_path, cycles={"evening": {"at": "20:00",
                                                       "steps": ["notify --digest"]}})
    config_dir = write_config(tmp_path, config)

    code = cli.main(["--env", "test", "--config-dir", str(config_dir), "cycle", "evening"])

    assert code == 0
    assert seen == [("notify", True, "test")]


# --- задачи планировщика --------------------------------------------------

WIN_ROOT = Path("D:/work/listam")
WIN_PYTHON = "D:/work/listam/.venv/Scripts/python.exe"
LINUX_ROOT = "/home/broker/listam"
LINUX_PYTHON = "/home/broker/listam/.venv/bin/python"


def test_windows_tasks(tmp_path):
    config = make_config(tmp_path)

    tasks = {task.name: task for task in windows_tasks(
        config, root=WIN_ROOT, python=WIN_PYTHON, env="prod",
        host_tz=timezone.utc, now=NOW)}

    assert set(tasks) == {"listam-test-hourly", "listam-test-nightly", "listam-test-evening"}
    hourly = tasks["listam-test-hourly"].args
    assert hourly[:3] == ["schtasks", "/create", "/tn"]
    at = hourly.index("/sc")
    assert hourly[at:at + 4] == ["/sc", "minute", "/mo", "60"]
    assert hourly[hourly.index("/st") + 1] == "00:00", "часовой — ровно в начале часа, как cron"
    assert "/it" in hourly and "/f" in hourly
    evening = tasks["listam-test-evening"].args
    at = evening.index("/sc")
    # 20:00 в Ереване — это 16:00 на хосте в UTC: планировщик живёт по часам хоста.
    assert evening[at:at + 4] == ["/sc", "daily", "/st", "16:00"]

    task = tasks["listam-test-evening"]
    assert task.wrapper_path == WIN_ROOT / "data" / "schedule" / "listam-test-evening.cmd"
    assert evening[evening.index("/tr") + 1] == f'"{task.wrapper_path}"', \
        "schtasks /tr не берёт больше 261 символа — команда живёт в обёртке"
    assert f'cd /d "{WIN_ROOT}"' in task.wrapper
    assert f'"{WIN_PYTHON}" -m listam --env prod cycle evening' in task.wrapper


def test_windows_tasks_follow_the_host_clock(tmp_path):
    """Хост в Ереване — 04:00 остаётся 04:00."""
    from zoneinfo import ZoneInfo

    tasks = {task.name: task for task in windows_tasks(
        make_config(tmp_path), root=WIN_ROOT, python=WIN_PYTHON, env="prod",
        host_tz=ZoneInfo("Asia/Yerevan"), now=NOW)}
    args = tasks["listam-test-nightly"].args
    assert args[args.index("/st") + 1] == "04:00"


def test_cron_lines(tmp_path):
    config = make_config(tmp_path)

    lines = cron_lines(config, root=LINUX_ROOT, python=LINUX_PYTHON, env="prod",
                       host_tz=timezone.utc, now=NOW)

    assert lines[0] == (f'0 * * * * cd "{LINUX_ROOT}" && xvfb-run -a "{LINUX_PYTHON}" '
                        f"-m listam --env prod cycle hourly # listam-test:hourly")
    assert lines[1].startswith("0 0 * * * ")          # 04:00 Ереван = 00:00 UTC
    assert lines[2].startswith("0 16 * * * ")
    assert all(line.endswith(f"# listam-test:{name}")
               for line, name in zip(lines, ("hourly", "nightly", "evening")))


def test_cron_lines_without_xvfb(tmp_path):
    config = make_config(tmp_path, linux_xvfb=False)

    lines = cron_lines(config, root=LINUX_ROOT, python=LINUX_PYTHON, env="prod",
                       host_tz=timezone.utc, now=NOW)

    assert all("xvfb-run" not in line for line in lines)
    assert f'&& "{LINUX_PYTHON}" -m listam' in lines[0]


@pytest.mark.parametrize("minutes, expected", [
    (15, "*/15 * * * *"), (60, "0 * * * *"), (120, "0 */2 * * *"), (1440, "0 0 * * *"),
])
def test_cron_periods(tmp_path, minutes, expected):
    config = make_config(tmp_path, cycles={"x": {"every_minutes": minutes, "steps": ["scrape"]}})
    line = cron_lines(config, root=LINUX_ROOT, python=LINUX_PYTHON, env="prod",
                      host_tz=timezone.utc, now=NOW)[0]
    assert line.startswith(expected + " ")


def test_no_path_is_hardcoded():
    """Корень проекта и интерпретатор приходят аргументами в момент установки:
    в репозитории им не место — на другой машине они другие."""
    texts = {"listam/schedule.py": (ROOT / "listam" / "schedule.py").read_text(encoding="utf-8"),
             "README.md": (ROOT / "README.md").read_text(encoding="utf-8")}
    for path in (ROOT / "config").glob("*.yaml"):
        texts[f"config/{path.name}"] = path.read_text(encoding="utf-8")
    for name, text in texts.items():
        for bad in ("C:\\", "/srv/"):
            assert bad not in text, f"{name}: зашитый путь {bad}"


class FakeSystem:
    """`schtasks` и `crontab` на подмене: что звали и что они ответили."""

    def __init__(self, crontab: str | None = ""):
        self.calls: list[tuple[list[str], str | None]] = []
        self.crontab = crontab

    def __call__(self, cmd, input=None):
        self.calls.append((list(cmd), input))
        if cmd[:2] == ["crontab", "-l"]:
            if self.crontab is None:
                return 1, "", "no crontab for broker"
            return 0, self.crontab, ""
        if cmd[:2] == ["crontab", "-"]:
            self.crontab = input
        return 0, "", ""

    def commands(self, program):
        return [cmd for cmd, _ in self.calls if cmd[0] == program]


def test_install_on_windows_writes_wrappers_and_tasks(tmp_path):
    system = FakeSystem()
    root = tmp_path / "project"

    install(make_config(tmp_path), "windows", root=root, python=WIN_PYTHON, run=system,
            host_tz=timezone.utc, now=NOW)

    created = [cmd for cmd in system.commands("schtasks") if cmd[1] == "/create"]
    assert [cmd[3] for cmd in created] == ["listam-test-hourly", "listam-test-nightly",
                                           "listam-test-evening"]
    for name in ("hourly", "nightly", "evening"):
        wrapper = root / "data" / "schedule" / f"listam-test-{name}.cmd"
        assert f"cycle {name}" in wrapper.read_text(encoding="utf-8")


def test_reinstall_on_windows_removes_a_dropped_cycle(tmp_path):
    system = FakeSystem()
    root = tmp_path / "project"
    install(make_config(tmp_path), "windows", root=root, python=WIN_PYTHON, run=system,
            host_tz=timezone.utc, now=NOW)
    system.calls.clear()

    fewer = make_config(tmp_path, cycles={"hourly": SCHEDULE["cycles"]["hourly"]})
    install(fewer, "windows", root=root, python=WIN_PYTHON, run=system,
            host_tz=timezone.utc, now=NOW)

    deleted = [cmd[3] for cmd in system.commands("schtasks") if cmd[1] == "/delete"]
    assert set(deleted) == {"listam-test-nightly", "listam-test-evening"}
    assert not (root / "data" / "schedule" / "listam-test-evening.cmd").exists()


def test_remove_on_windows(tmp_path):
    system = FakeSystem()
    root = tmp_path / "project"
    config = make_config(tmp_path)
    install(config, "windows", root=root, python=WIN_PYTHON, run=system,
            host_tz=timezone.utc, now=NOW)
    system.calls.clear()

    remove(config, "windows", root=root, run=system)

    deleted = [cmd[3] for cmd in system.commands("schtasks") if cmd[1] == "/delete"]
    assert set(deleted) == {"listam-test-hourly", "listam-test-nightly", "listam-test-evening"}
    assert not list((root / "data" / "schedule").glob("listam-test*"))


FOREIGN = ("MAILTO=broker\n"
           "30 2 * * * /usr/local/bin/backup\n"
           '0 * * * * cd "/old" && python -m listam --env prod cycle hourly # listam-test:hourly\n'
           '0 * * * * cd "/old" && python -m listam --env dev cycle hourly # listam-test-dev:hourly\n')


def test_install_on_linux_keeps_foreign_lines(tmp_path):
    system = FakeSystem(crontab=FOREIGN)

    install(make_config(tmp_path), "linux", root=LINUX_ROOT, python=LINUX_PYTHON,
            run=system, host_tz=timezone.utc, now=NOW)

    table = system.crontab.splitlines()
    assert "MAILTO=broker" in table
    assert "30 2 * * * /usr/local/bin/backup" in table
    assert any(line.endswith("# listam-test-dev:hourly") for line in table), \
        "чужой префикс — чужие задачи"
    ours = [line for line in table if "# listam-test:" in line]
    assert len(ours) == 3
    assert all('cd "/old"' not in line for line in ours)


def test_install_on_linux_without_a_crontab(tmp_path):
    system = FakeSystem(crontab=None)

    install(make_config(tmp_path), "linux", root=LINUX_ROOT, python=LINUX_PYTHON,
            run=system, host_tz=timezone.utc, now=NOW)

    assert len([line for line in system.crontab.splitlines() if "# listam-test:" in line]) == 3


def test_remove_on_linux(tmp_path):
    system = FakeSystem(crontab=FOREIGN)

    remove(make_config(tmp_path), "linux", root=LINUX_ROOT, run=system)

    table = system.crontab.splitlines()
    assert not any("# listam-test:" in line for line in table)
    assert "30 2 * * * /usr/local/bin/backup" in table


def test_show_can_look_at_the_other_platform(tmp_path, capsys):
    from listam.cli import main

    config_dir = write_config(tmp_path, make_config(tmp_path))

    code = main(["--env", "test", "--config-dir", str(config_dir),
                 "schedule", "show", "--platform", "linux"])

    assert code == 0
    out = capsys.readouterr().out
    assert "# listam-test:hourly" in out
    assert "xvfb-run" in out


def test_install_on_a_foreign_platform_is_refused(tmp_path, capsys, monkeypatch):
    from listam import schedule
    from listam.cli import main

    monkeypatch.setattr(schedule, "_system", lambda *a, **k: pytest.fail("система не зовётся"))
    config_dir = write_config(tmp_path, make_config(tmp_path))
    foreign = "linux" if sys.platform.startswith("win") else "windows"

    code = main(["--env", "test", "--config-dir", str(config_dir),
                 "schedule", "install", "--platform", foreign])

    assert code == 2
    assert foreign in capsys.readouterr().err
