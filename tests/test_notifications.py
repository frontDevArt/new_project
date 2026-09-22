"""Уведомление: окно, текст, журнал, тумблеры. Сети здесь нет."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from listam.config import load_config
from listam.domain.models import Listing, Match, Request
from listam.notifications import run_notify
from listam.wiring import build_database

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)

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
match:
  limit: 50
  thresholds:
    hot: 70
    digest: 40
notify:
  kind: stdout
  hot:
    enabled: true
    per_request: 5
    fallback_hours: 2
  digest:
    enabled: true
    per_request: 10
    wide_request: 15
    include_retired: true
    fallback_hours: 24
  feed:
    enabled: true
    limit: 15
    fallback_hours: 24
"""


def make_config(tmp_path: Path):
    """Настоящий файл конфига в tmp: команда `matches --new` зовётся через
    `--config-dir`, а значит, конфиг обязан лежать на диске, а не в памяти."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "test.yaml").write_text(
        CONFIG.format(remote=(tmp_path / "remote").as_posix(),
                      work=(tmp_path / "work").as_posix()),
        encoding="utf-8",
    )
    return load_config(env="test", config_dir=config_dir, dotenv_path=tmp_path / ".env")


@pytest.fixture
def prepared(tmp_path):
    """Конфиг, база и одна заявка с двумя свежими матчами."""
    config = make_config(tmp_path)
    database = build_database(config)
    database.connect()
    database.migrate()
    for index in range(2):
        database.upsert_listing(
            Listing(id=str(index), url=f"https://www.list.am/ru/item/{index}",
                    district="Кентрон", price_usd=100000.0, area=60.0, rooms=2),
            seen_at=NOW,
        )
    database.upsert_request(Request(external_id="R-1", client_name="Ани"), now=NOW)
    request = database.get_request("R-1")
    database.upsert_matches([
        Match(request_id=request.id, listing_id="0", score=91.0),
        Match(request_id=request.id, listing_id="1", score=85.0),
    ], datetime.now(timezone.utc))
    database.close()
    return config


def test_a_send_writes_one_line_in_the_journal(prepared, capsys):
    report = run_notify(prepared, kind="hot")

    assert report.errors == 0
    assert report.events == 2
    assert report.sent is True

    database = build_database(prepared)
    database.connect()
    last = database.last_notification("hot")
    database.close()
    assert last is not None
    assert last.events == 2
    assert "Заявка R-1" in last.text


def test_the_second_run_sends_nothing_new(prepared):
    """Ради этого и заведён журнал: повторный запуск не шлёт то же дважды."""
    run_notify(prepared, kind="hot")

    again = run_notify(prepared, kind="hot")

    assert again.events == 0
    assert "событий нет" in again.text


def test_dry_run_prints_but_does_not_remember(prepared):
    """«Покажи, что послал бы» обязано быть безопасным: окно не двигается,
    и то же самое потом уйдёт в чат."""
    report = run_notify(prepared, kind="hot", dry_run=True)

    assert report.dry_run is True
    assert report.sent is False
    database = build_database(prepared)
    database.connect()
    assert database.last_notification("hot") is None
    database.close()


def test_a_disabled_kind_does_nothing(prepared):
    prepared.data["notify"]["hot"]["enabled"] = False

    report = run_notify(prepared, kind="hot")

    assert report.errors == 0
    assert report.sent is False
    assert "выключены в конфиге" in report.text


def test_a_broken_channel_keeps_the_window_where_it_was(prepared, monkeypatch):
    """Сообщение не ушло — строки в журнале нет: следующий запуск пошлёт то,
    что не дошло. Иначе событие теряется навсегда."""
    from listam.ports.notifier import NotifyError, StdoutNotifier

    def refuse(self, text, to=None):
        raise NotifyError("сеть отказала")

    monkeypatch.setattr(StdoutNotifier, "send", refuse)

    report = run_notify(prepared, kind="hot")

    assert report.errors == 1
    database = build_database(prepared)
    database.connect()
    assert database.last_notification("hot") is None
    database.close()


def test_a_wide_request_is_marked(prepared):
    """Сотни событий в сутки — это незаполненная заявка, а не рынок."""
    prepared.data["notify"]["digest"]["wide_request"] = 1
    prepared.data["notify"]["digest"]["per_request"] = 1

    report = run_notify(prepared, kind="digest", dry_run=True)

    assert "слишком широкая" in report.text
    assert "…и ещё 1 из 2" in report.text


def test_an_empty_window_still_moves_it(prepared):
    """Пустая отправка тоже пишется в журнал: иначе завтра придёт сегодняшняя
    пустота плюс завтрашние события — с окном в двое суток."""
    run_notify(prepared, kind="digest")
    report = run_notify(prepared, kind="digest")

    assert report.events == 0
    database = build_database(prepared)
    database.connect()
    assert database.last_notification("digest").events == 0
    database.close()
