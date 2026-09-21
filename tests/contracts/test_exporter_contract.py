"""Контрактный тест порта Exporter: витрина для человека."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook

from listam.adapters.exporter_xlsx import XlsxExporter
from listam.domain.models import Listing
from listam.ports.exporter import Exporter

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


def listing(listing_id: str, first_seen: datetime, **over) -> Listing:
    fields = dict(
        id=listing_id,
        url=f"https://www.list.am/ru/item/{listing_id}",
        title="3 комн. квартира",
        district="Центр",
        price_raw="$132,000",
        currency="USD",
        price_usd=132000.0,
        price_amd=47916000.0,
        area=85.0,
        rooms=3,
        floor=4,
        floors_total=9,
        price_per_sqm=1552.94,
        seller_type="owner",
        verified=True,
        new_build=False,
        first_seen=first_seen,
        last_seen=first_seen,
    )
    fields.update(over)
    return Listing(**fields)


@pytest.fixture(params=["xlsx_local"])
def exporter(request, tmp_path) -> Exporter:
    return XlsxExporter(directory=tmp_path)


def test_is_an_exporter(exporter):
    assert isinstance(exporter, Exporter)


def test_writes_a_file_and_returns_its_path(exporter):
    path = exporter.export([listing("1", NOW)])
    assert path.exists()
    assert path.suffix == ".xlsx"


def test_header_is_frozen_and_filtered(exporter):
    sheet = load_workbook(exporter.export([listing("1", NOW)])).active
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None


def test_price_and_area_are_numbers_not_text(exporter):
    sheet = load_workbook(exporter.export([listing("1", NOW)])).active
    header = [cell.value for cell in sheet[1]]
    row = {header[i]: cell.value for i, cell in enumerate(sheet[2])}
    assert isinstance(row["Цена, $"], (int, float))
    assert isinstance(row["Площадь, м²"], (int, float))
    assert isinstance(row["$/м²"], (int, float))


def test_url_is_a_clickable_link(exporter):
    sheet = load_workbook(exporter.export([listing("24254997", NOW)])).active
    header = [cell.value for cell in sheet[1]]
    cell = sheet[2][header.index("Ссылка")]
    assert cell.hyperlink.target == "https://www.list.am/ru/item/24254997"


def test_rows_are_sorted_by_first_seen_newest_first(exporter):
    older = listing("old", NOW - timedelta(days=2))
    newer = listing("new", NOW)
    sheet = load_workbook(exporter.export([older, newer])).active
    header = [cell.value for cell in sheet[1]]
    ids = [row[header.index("ID")].value for row in sheet.iter_rows(min_row=2)]
    assert ids == ["new", "old"]


def test_seller_type_is_written_in_russian(exporter):
    sheet = load_workbook(exporter.export([listing("1", NOW, seller_type="agency")])).active
    header = [cell.value for cell in sheet[1]]
    assert sheet[2][header.index("Продавец")].value == "агентство"


def test_empty_fields_do_not_break_the_export(exporter):
    sparse = listing("1", NOW, price_usd=None, area=None, rooms=None, district=None,
                     price_per_sqm=None, seller_type=None, verified=None)
    sheet = load_workbook(exporter.export([sparse])).active
    assert sheet.max_row == 2


def test_export_of_empty_database_still_writes_header(exporter):
    sheet = load_workbook(exporter.export([])).active
    assert sheet.max_row == 1
    assert sheet["A1"].value == "ID"
