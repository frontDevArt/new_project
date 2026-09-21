"""Командная строка: `python -m listam ...`."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from listam.adapters.db_sqlite import latest_schema_version
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
  min_cards_per_page: 3
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
    run(project, "scrape")          # база появляется от прогона, а не от выгрузки
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


def test_output_survives_a_non_utf8_console(project, monkeypatch, capsys):
    """Под Windows перенаправленный вывод приходит в cp1252: кириллица не должна ронять прогон."""
    import io
    import sys

    stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", line_buffering=True)
    monkeypatch.setattr(sys, "stdout", stream)
    code = run(project, "scrape")
    stream.flush()
    text = stream.buffer.getvalue().decode("utf-8")
    assert code == 0
    assert "страниц: 2" in text


def test_scrape_exits_nonzero_when_a_page_has_no_cards(project, capsys):
    pages = project / "pages"
    for old in pages.glob("*.html"):
        old.unlink()
    (pages / "category-60.html").write_text(
        '<html><body><div id="contentr"></div></body></html>', encoding="utf-8"
    )

    assert run(project, "scrape") == 1
    assert "карточек: 0" in capsys.readouterr().out


def test_allow_shrink_flag_reaches_the_run(project, monkeypatch):
    """Заливку, срезающую базу, разрешает только явный флаг — он не должен теряться по дороге."""
    import listam.crawler
    from listam.domain.models import Run

    seen = {}

    def fake_run_scrape(config, **kwargs):
        seen.update(kwargs)
        return Run()

    monkeypatch.setattr(listam.crawler, "run_scrape", fake_run_scrape)

    assert run(project, "scrape", "--allow-shrink") == 0
    assert seen["allow_shrink"] is True


def test_allow_shrink_is_off_unless_asked(project, monkeypatch):
    import listam.crawler
    from listam.domain.models import Run

    seen = {}
    monkeypatch.setattr(
        listam.crawler, "run_scrape",
        lambda config, **kwargs: (seen.update(kwargs), Run())[1],
    )

    run(project, "scrape")
    assert seen["allow_shrink"] is False


def test_resume_flag_reaches_the_run(project, monkeypatch):
    import listam.crawler
    from listam.domain.models import Run

    seen = {}
    monkeypatch.setattr(
        listam.crawler, "run_scrape",
        lambda config, **kwargs: (seen.update(kwargs), Run())[1],
    )

    assert run(project, "scrape", "--resume") == 0
    assert seen["resume"] is True


def database_at_schema(project, version: int) -> None:
    """Кладёт на место рабочей копии базу, на которую накатили только первые миграции."""
    from listam.adapters.db_sqlite import MIGRATIONS_DIR, SqliteDatabase

    older = project / "older-migrations"
    older.mkdir(exist_ok=True)
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name.split("_", 1)[0]) <= version:
            shutil.copyfile(path, older / path.name)
    work = project / "work"
    work.mkdir(exist_ok=True)
    database = SqliteDatabase(work / "listam.sqlite", migrations_dir=older)
    database.connect()
    database.migrate()
    database.close()


def schema_of(project) -> int:
    from listam.adapters.db_sqlite import SqliteDatabase

    database = SqliteDatabase(project / "work" / "listam.sqlite")
    database.connect()
    version = database.schema_version()
    database.close()
    return version


def test_export_refuses_a_database_older_than_the_code(project, capsys):
    """Блокер 5: выгрузка мигрировала боевую базу молча, по дороге к .xlsx.

    Миграция — это правка общей базы, и делать её попутно, за спиной у человека,
    нельзя: `export` читает, а не чинит.
    """
    database_at_schema(project, 1)

    code = main(["--env", "test", "--config-dir", str(project / "config"), "export"])

    assert code == 1
    out = capsys.readouterr()
    message = out.out + out.err
    assert "схема базы" in message
    assert "recheck" in message          # что делать — сказано в самой ошибке
    assert schema_of(project) == 1       # схема не тронута
    assert not list((project / "out").glob("*.xlsx"))


def test_export_without_a_database_says_so_instead_of_crashing(project, capsys):
    """Базы нет вовсе — это версия 0, а не трейсбек."""
    code = main(["--env", "test", "--config-dir", str(project / "config"), "export"])

    out = capsys.readouterr()
    assert code == 1
    assert "схема базы 0" in (out.out + out.err)


def test_recheck_reports_what_it_recomputed(project, capsys):
    run(project, "scrape")

    assert run(project, "recheck") == 0

    out = capsys.readouterr().out
    assert "Пересчёт: строк: 8" in out
    assert f"схема {latest_schema_version()}" in out


def test_export_works_right_after_a_recheck(project, capsys):
    run(project, "scrape")
    run(project, "recheck")

    assert run(project, "export") == 0
