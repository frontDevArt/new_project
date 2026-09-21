"""Контрактный тест порта RequestsSource: одинаков для любой реализации.

Разбор строки живёт в домене и один на всех (решение 1), поэтому контракт
проверяет не то, как адаптер лезет в свой источник, а то, что он отдаёт
словари и что общий разбор поверх них работает одинаково.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from listam.adapters.requests_csv import CsvRequestsSource
from listam.domain.requests import COLUMNS
from listam.ports.requests_source import EmptyRequestsSource, RequestsSource

ROW = {
    "id": "R-1", "client_name": "Ани", "client_phone": "+374 00 000000",
    "status": "active", "budget_max": "120000", "budget_stretch": "",
    "districts": "Кентрон", "districts_priority": "", "rooms": "3",
    "area_min": "60", "area_max": "95", "floor_min": "", "floor_max": "",
    "no_first_floor": "да", "no_last_floor": "нет",
    "must_have": "", "nice_to_have": "", "floor_rules": "", "notes": "",
}


def write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


@pytest.fixture(params=["none", "csv", pytest.param("gsheet", marks=pytest.mark.skipif(
    not os.environ.get("REQUESTS_SHEET"),
    reason="нет REQUESTS_SHEET: живой Google Sheet не проверить"))])
def source(request, tmp_path):
    if request.param == "none":
        return EmptyRequestsSource()
    if request.param == "csv":
        return CsvRequestsSource(path=write_csv(tmp_path / "requests.csv", [ROW]))
    from listam.adapters.requests_gsheet import GSheetRequestsSource
    return GSheetRequestsSource(
        sheet_id=os.environ["REQUESTS_SHEET"],
        credentials_file=os.environ.get("GDRIVE_CREDENTIALS_FILE"),
    )


def test_is_a_requests_source(source):
    assert isinstance(source, RequestsSource)


def test_rows_are_plain_dictionaries(source):
    for row in source.rows():
        assert isinstance(row, dict)


def test_describe_says_what_the_source_is(source):
    assert source.describe().strip()


def test_reading_gives_requests_and_the_list_of_refusals(source):
    parsed, errors = source.read()
    assert isinstance(parsed, list) and isinstance(errors, list)
    for item in parsed:
        assert item.external_id


def test_active_requests_are_only_the_active_ones(source):
    assert all(item.status == "active" for item in source.active_requests())


def test_reading_twice_gives_the_same_thing(source):
    first, _ = source.read()
    second, _ = source.read()
    assert [item.external_id for item in first] == [item.external_id for item in second]
