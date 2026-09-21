"""Выгрузка в .xlsx: шапка заморожена, автофильтр, ссылки кликабельные,
цена и площадь — числа, сортировка по дате появления."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from listam.adapters.filenames import safe_filename
from listam.domain.models import Listing
from listam.ports.exporter import Exporter

SELLER_TYPES = {"owner": "собственник", "agency": "агентство"}
STATUSES = {"active": "на ленте", "gone": "снято"}

# заголовок, как достать значение, ширина колонки, формат числа
COLUMNS: list[tuple[str, str, int, str | None]] = [
    ("ID", "id", 12, None),
    ("Ссылка", "url", 16, None),
    ("Заголовок", "title", 46, None),
    ("Район", "district", 18, None),
    ("Улица", "street", 22, None),
    ("Цена, $", "price_usd", 13, "#,##0"),
    ("Цена, ֏", "price_amd", 15, "#,##0"),
    ("Цена как на сайте", "price_raw", 18, None),
    ("Цена в валюте", "price_amount", 14, "#,##0.##"),
    ("Площадь, м²", "area", 12, "0.0"),
    ("$/м²", "price_per_sqm", 10, "#,##0"),
    ("Комнат", "rooms", 9, "0"),
    ("Этаж", "floor", 8, "0"),
    ("Этажей", "floors_total", 9, "0"),
    ("Продавец", "seller_type", 14, None),
    ("Новостройка", "new_build", 13, None),
    ("Проверено", "verified", 11, None),
    ("Сомнительно", "anomaly", 20, None),
    ("Статус", "status", 10, None),
    ("Появилось", "first_seen", 18, None),
    ("Видели", "last_seen", 18, None),
    ("Снято", "gone_at", 18, None),
]

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(color="FFFFFF", bold=True)
LINK_FONT = Font(color="0563C1", underline="single")


class XlsxExporter(Exporter):
    def __init__(self, directory: str | Path = "./out", timezone_name: str = "UTC"):
        self.directory = Path(directory)
        self.timezone_name = timezone_name

    def export(self, listings: Iterable[Listing], name: str | None = None) -> Path:
        rows = sorted(
            listings,
            key=lambda item: (item.first_seen or datetime.min.replace(tzinfo=timezone.utc)),
            reverse=True,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        # Имя приходит из командной строки: берём из него только имя файла.
        # `--name ../../отчёт.xlsx` обязан писать в свою папку, а не мимо неё.
        path = self.directory / (safe_filename(name) or f"listam-{stamp}.xlsx")

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Объявления"

        for index, (title, _, width, _) in enumerate(COLUMNS, start=1):
            cell = sheet.cell(row=1, column=index, value=title)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(vertical="center", wrap_text=False)
            sheet.column_dimensions[get_column_letter(index)].width = width

        for row_index, listing in enumerate(rows, start=2):
            for column_index, (title, attribute, _, number_format) in enumerate(COLUMNS, start=1):
                value = _present(listing, attribute)
                cell = sheet.cell(row=row_index, column=column_index, value=value)
                if number_format and isinstance(value, (int, float)):
                    cell.number_format = number_format
                if title == "Ссылка" and listing.url:
                    cell.hyperlink = listing.url
                    cell.font = LINK_FONT

        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(sheet.max_row, 1)}"
        workbook.save(path)
        return path


def _present(listing: Listing, attribute: str):
    value = getattr(listing, attribute, None)
    if attribute == "url":
        return "открыть" if value else None
    if attribute == "seller_type":
        return SELLER_TYPES.get(value, value)
    if attribute == "status":
        return STATUSES.get(value, value)
    if attribute in ("new_build", "verified"):
        return None if value is None else ("да" if value else "нет")
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return value
