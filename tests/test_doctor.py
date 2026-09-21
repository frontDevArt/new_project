"""`listam doctor` — то, чем проверяется переезд на другой аккаунт."""
from __future__ import annotations

from pathlib import Path

from listam.config import Config
from listam.doctor import run_doctor


def cfg(tmp_path, **over) -> Config:
    data = {
        "env": "test",
        "storage": {
            "kind": "local",
            "directory": str(tmp_path / "remote"),
            "work_dir": str(tmp_path / "work"),
            "db_filename": "listam.sqlite",
        },
        "rate": {"kind": "fixed", "amd_per_usd": 363.25},
        "export": {"kind": "xlsx_local", "path": str(tmp_path / "out")},
        "scrape": {"base_url": "https://example.invalid", "delay_seconds": 0},
        "notify": {"kind": "none"},
    }
    data.update(over)
    return Config(data, env="test", path=Path("config/test.yaml"))


def test_healthy_setup_reports_ok(tmp_path):
    report = run_doctor(cfg(tmp_path), check_network=False)
    assert report.ok is True


def test_every_check_is_named_and_explained(tmp_path):
    report = run_doctor(cfg(tmp_path), check_network=False)
    names = [check.name for check in report.checks]
    assert "Конфиг" in names
    assert "Хранилище" in names
    assert "Схема базы" in names
    assert "Курс AMD→USD" in names
    assert "Выгрузка" in names
    assert all(check.details for check in report.checks)


def test_unwritable_export_directory_fails_the_report(tmp_path):
    blocker = tmp_path / "out"
    blocker.write_text("я файл, а не папка", encoding="utf-8")
    report = run_doctor(cfg(tmp_path), check_network=False)
    assert report.ok is False
    assert any(not check.ok and check.name == "Выгрузка" for check in report.checks)


def test_unknown_adapter_is_reported_not_raised(tmp_path):
    broken = cfg(tmp_path, storage={"kind": "dropbox"})
    report = run_doctor(broken, check_network=False)
    assert report.ok is False
    assert any("dropbox" in check.details for check in report.checks)


def test_rate_check_reports_the_number(tmp_path):
    report = run_doctor(cfg(tmp_path), check_network=False)
    rate_check = next(c for c in report.checks if c.name == "Курс AMD→USD")
    assert "363.25" in rate_check.details


def test_doctor_does_not_leave_junk_in_storage(tmp_path):
    run_doctor(cfg(tmp_path), check_network=False)
    remote = tmp_path / "remote"
    assert list(remote.glob("*")) == []


def test_report_renders_as_readable_text(tmp_path):
    text = run_doctor(cfg(tmp_path), check_network=False).render()
    assert "Хранилище" in text
    assert "OK" in text


# --- пороги прогона (F-13) -------------------------------------------
# `doctor` предупреждает, но не чинит: опасный порог — это ⚠ в отчёте и
# ненулевой код возврата на явном вредительстве. Конфиг — территория человека.

HEALTHY = {
    "base_url": "https://example.invalid",
    "delay_seconds": 0,
    "max_pages": None,
    "fresh_stop_after_known_pages": 2,
    "fresh_max_pages": 20,
    "expected_pages_min": None,
    "max_pages_drop_percent": 20,
    "max_gone_percent": 10,
}


def thresholds(report):
    return next(c for c in report.checks if c.name == "Пороги прогона")


def test_working_thresholds_are_listed_and_green(tmp_path):
    """Боевой конфиг: проверка зелёная и показывает, с чем прогон пойдёт."""
    report = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY)), check_network=False)
    check = thresholds(report)

    assert check.ok is True and check.warn is False
    assert "max_gone_percent = 10" in check.details
    assert "fresh_stop_after_known_pages = 2" in check.details
    assert "max_pages = не задан" in check.details


def test_zero_gone_percent_is_reported_as_harm(tmp_path):
    """0 значит «пропало хоть что-то — сбой»: снятыми не будет помечено ничего."""
    broken = dict(HEALTHY, max_gone_percent=0)
    report = run_doctor(cfg(tmp_path, scrape=broken), check_network=False)

    assert report.ok is False
    assert thresholds(report).ok is False
    assert "снятыми не будет помечено ничего" in thresholds(report).details


def test_a_page_ceiling_in_the_config_is_a_warning_not_a_failure(tmp_path):
    """`max_pages: 2` — рабочая настройка окружения, но обход укорочен, и это видно."""
    report = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY, max_pages=2)),
                        check_network=False)
    check = thresholds(report)

    assert check.warn is True
    assert check.ok is True
    assert report.ok is True
    assert "укорочен" in check.details


def test_fresh_without_a_stop_condition_is_a_warning(tmp_path):
    """`null` выключает остановку: `--fresh` пойдёт до потолка и кончится ошибкой."""
    report = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY, fresh_stop_after_known_pages=None)),
                        check_network=False)

    assert thresholds(report).warn is True


def test_a_warning_is_marked_in_the_rendered_report(tmp_path):
    text = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY, max_pages=2)),
                      check_network=False).render()

    assert "⚠" in text


def test_the_verdict_does_not_say_everything_is_fine_when_it_warns(tmp_path):
    """«Всё на месте» поверх ⚠ — это неправда: человек ради неё и читает итог."""
    text = run_doctor(cfg(tmp_path, scrape=dict(HEALTHY, max_pages=2)),
                      check_network=False).render()

    assert "Всё на месте" not in text
    assert "предупреждени" in text
