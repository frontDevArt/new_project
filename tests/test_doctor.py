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
