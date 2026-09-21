"""Командная строка: `python -m listam ...`."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from listam.cli import main

CONFIG = """
env: test
storage:
  kind: local
  directory: {remote}
  work_dir: {work}
  db_filename: listam.sqlite
rate:
  kind: fixed
  amd_per_usd: 363.25
export:
  kind: xlsx_local
  path: {out}
scrape:
  kind: files
  pages_dir: {pages}
  base_url: https://www.list.am/ru
  category: 60
  delay_seconds: 0
notify:
  kind: none
"""


@pytest.fixture
def project(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    pages = tmp_path / "pages"
    pages.mkdir()
    fixtures = Path(__file__).parent / "fixtures"
    shutil.copyfile(fixtures / "category-60-page1.html", pages / "category-60.html")
    shutil.copyfile(fixtures / "category-60-page2.html", pages / "category-60-2.html")
    (config_dir / "test.yaml").write_text(
        CONFIG.format(
            remote=(tmp_path / "remote").as_posix(),
            work=(tmp_path / "work").as_posix(),
            out=(tmp_path / "out").as_posix(),
            pages=pages.as_posix(),
        ),
        encoding="utf-8",
    )
    return tmp_path


def run(project, *args) -> int:
    return main(["--env", "test", "--config-dir", str(project / "config"), *args])


def test_doctor_exits_zero_on_healthy_setup(project, capsys):
    assert run(project, "doctor", "--no-network") == 0
    assert "Хранилище" in capsys.readouterr().out


def test_doctor_exits_nonzero_when_something_is_broken(project, capsys):
    (project / "out").write_text("я файл, а не папка", encoding="utf-8")
    assert run(project, "doctor", "--no-network") == 1


def test_export_writes_a_file_from_the_stored_database(project, capsys):
    run(project, "doctor", "--no-network")
    assert run(project, "export") == 0
    out = capsys.readouterr().out
    assert ".xlsx" in out
    assert list((project / "out").glob("*.xlsx"))


def test_scrape_reports_what_it_collected(project, capsys):
    assert run(project, "scrape") == 0
    out = capsys.readouterr().out
    assert "страниц: 2" in out
    assert "новых: 8" in out


def test_scrape_dry_run_says_that_nothing_was_written(project, capsys):
    assert run(project, "scrape", "--dry-run") == 0
    assert "ничего не записано" in capsys.readouterr().out


def test_scrape_max_pages_flag_limits_the_run(project, capsys):
    assert run(project, "scrape", "--max-pages", "1") == 0
    assert "страниц: 1" in capsys.readouterr().out


def test_unknown_command_is_rejected(project):
    with pytest.raises(SystemExit):
        run(project, "станцуй")


def test_missing_config_reports_clearly_without_traceback(tmp_path, capsys):
    code = main(["--env", "нет-такого", "--config-dir", str(tmp_path), "doctor"])
    assert code == 2
    assert "нет-такого" in capsys.readouterr().err
