"""`listam mark`: «звонил» и «отказ» (фаза 6 M3.5, решение 15).

След звонка пишет только человек — этой командой. Отказ всегда исключает
отвергнутую квартиру (кластер); причина из `feedback.reasons` сужает заявку
в базе, а не в таблице брокера; неизвестная — записывается и не применяется.
`new` откатывает отметку вместе с её исключениями. После отметки — подбор
по этой заявке.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from listam.cli import main
from listam.config import load_config
from listam.domain.models import ListingPage, PageFields, Request
from listam.matching import run_match
from listam.wiring import build_database

from tests.contracts.test_database_contract import make_listing

REASONS = {
    "первый этаж": {"exclude": "first_floor"},
    "последний этаж": {"exclude": "last_floor"},
    "район": {"exclude": "district"},
    "тип дома": {"exclude": "building_type"},
}


def write_config(tmp_path: Path, **over) -> Path:
    data = {
        "env": "test",
        "storage": {"kind": "local", "directory": (tmp_path / "remote").as_posix(),
                    "work_dir": (tmp_path / "work").as_posix(),
                    "db_filename": "listam.sqlite"},
        "notify": {"kind": "none"},
        "match": {"thresholds": {"hot": 80, "digest": 40}, "budget_stretch_percent": 10},
        "feedback": {"reasons": REASONS},
    }
    for section, values in over.items():
        data[section] = values
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "test.yaml").write_text(yaml.safe_dump(data, allow_unicode=True),
                                          encoding="utf-8")
    return config_dir


def config_of(config_dir: Path):
    return load_config(env="test", config_dir=str(config_dir))


def mark(config_dir: Path, *args) -> int:
    return main(["--env", "test", "--config-dir", str(config_dir), "mark", *args])


def flat(listing_id, **over):
    """Арабкир или Кентрон, 3 комнаты, 85 м², $110 000 — подходит заявке R-1."""
    fields = dict(district="Арабкир", street=f"улица {listing_id}", rooms=3,
                  area=85.0, floor=4, floors_total=9, price_usd=110_000.0,
                  price_per_sqm=1294.0, seller_type="owner")
    fields.update(over)
    return make_listing(listing_id, **fields)


def prepare(config_dir: Path, *listings, pages=()) -> None:
    """База с объявлениями, заявкой R-1 и её первым подбором."""
    database = build_database(config_of(config_dir))
    database.connect()
    database.migrate()
    try:
        now = datetime.now(timezone.utc)
        for listing in listings:
            database.upsert_listing(listing, seen_at=now)
        for page in pages:
            database.save_page(page)
        database.upsert_request(Request(
            external_id="R-1", client_name="ПРИМЕР", budget_max=120_000.0,
            districts=["Арабкир", "Кентрон"], rooms=[3]), now=now)
    finally:
        database.close()
    assert run_match(config_of(config_dir), external_id="R-1").errors == 0


class State:
    """Что лежит в базе после команды: матчи и исключения заявки R-1."""

    def __init__(self, config_dir: Path):
        database = build_database(config_of(config_dir))
        database.connect()
        try:
            request = database.get_request("R-1")
            self.matches = {match.listing_id: match for match in
                            database.matches_for_request(request.id,
                                                         include_retired=True)}
            self.exclusions = database.exclusions_for(request.id)
        finally:
            database.close()

    def kinds(self):
        return [(item.kind, item.value) for item in self.exclusions]

    def alive(self):
        return sorted(key for key, match in self.matches.items()
                      if match.retired_at is None)


@pytest.fixture
def config_dir(tmp_path):
    return write_config(tmp_path)


# --- отказ ---------------------------------------------------------------

def test_rejected_excludes_the_cluster_always(config_dir, capsys):
    """Даже без причины: отвергнутая квартира не возвращается."""
    prepare(config_dir, flat("1"), flat("2"))

    assert mark(config_dir, "R-1", "1", "rejected") == 0

    state = State(config_dir)
    assert state.kinds() == [("cluster", state.matches["1"].cluster_id)]
    assert state.matches["1"].status == "rejected"
    assert state.matches["1"].retired_reason == "клиент отказал"
    assert state.alive() == ["2"]


def test_first_floor_reason_narrows_the_request(config_dir, capsys):
    prepare(config_dir, flat("1", floor=1), flat("2", floor=1), flat("3", floor=5))

    assert mark(config_dir, "R-1", "1", "rejected", "--reason", "Первый этаж") == 0

    state = State(config_dir)
    assert ("first_floor", None) in state.kinds()
    assert state.matches["1"].reject_reason == "Первый этаж"
    assert state.matches["2"].retired_reason == "клиент отказал: первый этаж"
    assert state.alive() == ["3"]
    out = capsys.readouterr().out
    assert "без 1-го этажа" in out


def test_district_reason_uses_the_listing_district(config_dir, capsys):
    prepare(config_dir, flat("1", district="Кентрон"), flat("2", district="Кентрон"),
            flat("3", district="Арабкир"))

    assert mark(config_dir, "R-1", "1", "rejected", "--reason", "район") == 0

    state = State(config_dir)
    assert ("district", "Кентрон") in state.kinds()
    assert state.alive() == ["3"]


def test_unknown_reason_is_recorded_not_applied(config_dir, capsys):
    prepare(config_dir, flat("1"), flat("2"))

    assert mark(config_dir, "R-1", "1", "rejected", "--reason", "дорого") == 0

    state = State(config_dir)
    assert state.matches["1"].reject_reason == "дорого"
    assert [kind for kind, _ in state.kinds()] == ["cluster"]
    assert state.matches["1"].retired_reason == "клиент отказал: дорого"
    assert state.alive() == ["2"]
    out = capsys.readouterr().out
    assert "«дорого»" in out and "feedback.reasons" in out


def test_a_page_reason_takes_the_value_from_the_page(config_dir, capsys):
    page = ListingPage(listing_id="1", status="ok", attempts=1, price_raw="$110,000",
                       fetched_at=datetime.now(timezone.utc),
                       fields=PageFields(values={"building_type": "панельное"}))
    prepare(config_dir, flat("1"), flat("2"), pages=[page])

    assert mark(config_dir, "R-1", "1", "rejected", "--reason", "тип дома") == 0

    assert ("building_type", "панельное") in State(config_dir).kinds()
    assert "без панели" in capsys.readouterr().out


def test_a_page_reason_without_the_page_is_only_recorded(config_dir, capsys):
    prepare(config_dir, flat("1"), flat("2"))

    assert mark(config_dir, "R-1", "1", "rejected", "--reason", "тип дома") == 0

    state = State(config_dir)
    assert [kind for kind, _ in state.kinds()] == ["cluster"]
    assert state.matches["1"].reject_reason == "тип дома"
    out = capsys.readouterr().out
    assert "страница" in out and "не сужает" in out


def test_several_reasons_are_parsed_by_the_comma(config_dir, capsys):
    prepare(config_dir, flat("1", floor=1, district="Кентрон"), flat("2"))

    assert mark(config_dir, "R-1", "1", "rejected",
                "--reason", "первый этаж, район, дорого") == 0

    kinds = State(config_dir).kinds()
    assert ("first_floor", None) in kinds and ("district", "Кентрон") in kinds
    assert len(kinds) == 3


def test_marking_again_replaces_the_exclusions_of_the_mark(config_dir, capsys):
    prepare(config_dir, flat("1", floor=1), flat("2"))
    mark(config_dir, "R-1", "1", "rejected", "--reason", "первый этаж")

    assert mark(config_dir, "R-1", "1", "rejected", "--reason", "дорого") == 0

    assert [kind for kind, _ in State(config_dir).kinds()] == ["cluster"]


# --- звонил и откат -------------------------------------------------------

def test_called_is_a_trace_and_not_a_refusal(config_dir, capsys):
    prepare(config_dir, flat("1"), flat("2"))

    assert mark(config_dir, "R-1", "1", "called") == 0

    state = State(config_dir)
    assert state.matches["1"].status == "called"
    assert state.exclusions == []
    assert state.alive() == ["1", "2"]


def test_new_undoes_the_mark(config_dir, capsys):
    prepare(config_dir, flat("1", floor=1), flat("2", floor=1))
    mark(config_dir, "R-1", "1", "rejected", "--reason", "первый этаж")
    assert State(config_dir).alive() == []

    assert mark(config_dir, "R-1", "1", "new") == 0

    state = State(config_dir)
    assert state.exclusions == []
    assert state.matches["1"].status == "new"
    assert state.matches["1"].reject_reason is None
    assert state.alive() == ["1", "2"]


def test_new_leaves_other_marks_alone(config_dir, capsys):
    prepare(config_dir, flat("1"), flat("2"), flat("3"))
    mark(config_dir, "R-1", "1", "rejected")
    mark(config_dir, "R-1", "2", "rejected")

    mark(config_dir, "R-1", "1", "new")

    state = State(config_dir)
    assert [value for _, value in state.kinds()] == [state.matches["2"].cluster_id]
    assert state.alive() == ["1", "3"]


# --- поиск матча -----------------------------------------------------------

def test_mark_finds_the_match_by_cluster(config_dir, capsys):
    """Карточку квартиры могли сменить на дешёвую: брокер называет ту, по
    которой звонил, а матч лежит на представителе кластера."""
    same = dict(district="Арабкир", street="ул. Комитаса", rooms=3, floor=4,
                floors_total=9, seller_type="agency")
    prepare(config_dir, flat("cheap", area=85.0, price_usd=100_000.0, **same),
            flat("dear", area=86.0, price_usd=115_000.0, **same), flat("2"))
    assert "dear" not in State(config_dir).matches

    assert mark(config_dir, "R-1", "dear", "rejected") == 0

    state = State(config_dir)
    assert state.matches["cheap"].status == "rejected"
    assert state.alive() == ["2"]


def test_no_match_is_code_one_in_words(config_dir, capsys):
    prepare(config_dir, flat("1"), flat("чужое", district="Давташен"))

    assert mark(config_dir, "R-1", "чужое", "called") == 1

    assert "у заявки R-1 нет матча на чужое и его кластер" in capsys.readouterr().err
    assert State(config_dir).matches["1"].status == "new"


def test_an_unknown_request_is_code_one(config_dir, capsys):
    prepare(config_dir, flat("1"))

    assert mark(config_dir, "R-9", "1", "called") == 1

    assert "R-9" in capsys.readouterr().err


# --- ввод --------------------------------------------------------------------

def test_a_reason_is_only_for_a_refusal(config_dir, capsys):
    prepare(config_dir, flat("1"))

    assert mark(config_dir, "R-1", "1", "called", "--reason", "первый этаж") == 2

    assert "--reason" in capsys.readouterr().err
    assert State(config_dir).matches["1"].status == "new"


def test_an_unknown_status_is_refused(config_dir, capsys):
    with pytest.raises(SystemExit) as exit_:
        mark(config_dir, "R-1", "1", "sent")
    assert exit_.value.code == 2


@pytest.mark.parametrize("reasons, named", [
    ({"первый этаж": "first_floor"}, "первый этаж"),
    ({"первый этаж": {"exclude": ""}}, "первый этаж"),
    ({"первый этаж": {"exclude": "first_floor", "лишнее": 1}}, "лишнее"),
    (["первый этаж"], "feedback.reasons"),
])
def test_a_senseless_reasons_section_is_refused_before_the_work(tmp_path, capsys,
                                                                 reasons, named):
    config_dir = write_config(tmp_path, feedback={"reasons": reasons})

    assert mark(config_dir, "R-1", "1", "rejected") == 2

    assert named in capsys.readouterr().err


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_the_shipped_configs_carry_the_reasons_of_the_spec(env, monkeypatch):
    """Словарь спеки (раздел «Конфиг»): пять слов отказа в обоих конфигах."""
    from listam.feedback import reasons

    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(name, "x")
    vocab = reasons(load_config(env=env, config_dir="config"))

    assert vocab == {"первый этаж": "first_floor", "последний этаж": "last_floor",
                     "район": "district", "тип дома": "building_type",
                     "ремонт": "renovation"}


def test_the_mark_speaks_before_the_match(config_dir, capsys):
    """Сначала — что сделала отметка, потом — отчёт подбора."""
    prepare(config_dir, flat("1"), flat("2"))

    mark(config_dir, "R-1", "1", "called")

    out = capsys.readouterr().out
    assert out.index("база с отметкой залита") < out.index("Подбор: заявка R-1")


def test_a_paused_request_is_marked_without_a_match_run(config_dir, capsys):
    """След звонка переживает паузу заявки; подбор по ней не идёт."""
    prepare(config_dir, flat("1"))
    database = build_database(config_of(config_dir))
    database.connect()
    try:
        request = database.get_request("R-1")
        request.status = "paused"
        database.upsert_request(request, now=datetime.now(timezone.utc))
    finally:
        database.close()

    assert mark(config_dir, "R-1", "1", "called") == 0

    out = capsys.readouterr().out
    assert "не active — подбор по ней не шёл" in out and "Подбор:" not in out
    assert State(config_dir).matches["1"].status == "called"
