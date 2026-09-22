"""Заявки из Google Sheet: брокер ведёт таблицу руками, скрипт её читает.

Идентификатор таблицы и ключ сервисного аккаунта — из конфига и .env,
в коде их нет. Адаптер достаёт значения и отдаёт строки словарями;
разбор — общий, в домене.
"""
from __future__ import annotations

from listam.ports.requests_source import RequestsSource

SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly",)


class GSheetUnavailable(Exception):
    """Нет библиотек Google или ключа сервисного аккаунта."""


class GSheetRequestsSource(RequestsSource):
    def __init__(self, sheet_id: str, credentials_file: str | None = None,
                 range_name: str = "A1:Z1000"):
        self.sheet_id = sheet_id
        self.credentials_file = credentials_file
        self.range_name = range_name

    def _service(self):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:      # pragma: no cover — зависит от окружения
            raise GSheetUnavailable(
                "Нет библиотек Google: pip install -r requirements-gdrive.txt"
            ) from exc
        if not self.credentials_file:
            raise GSheetUnavailable(
                "Не задан ключ сервисного аккаунта: requests.credentials_file "
                "в config/<env>.yaml (значение — из .env)"
            )
        credentials = service_account.Credentials.from_service_account_file(
            self.credentials_file, scopes=list(SCOPES)
        )
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def _rows_from(self, values: list[list]) -> list[dict]:
        """Ответ Sheets в строки словарями. Разбор значений — в домене.

        Значений в строке больше, чем колонок в шапке, — строка разъехалась,
        и читать её нельзя. `csv.DictReader` кладёт остаток под ключ `None`,
        и домен на этот ключ уже умеет отказывать (решение 2). Повторяем его
        договор ровно, а не «почти»: иначе одна и та же строка в двух
        источниках даст две разные заявки, и решение 1 перестанет что-либо
        значить.
        """
        if not values:
            return []
        header = [str(name).strip() for name in values[0]]
        rows: list[dict] = []
        for raw in values[1:]:
            cells = [str(cell) for cell in raw]
            if not any(cell.strip() for cell in cells):
                continue
            # Пустые хвостовые ячейки Sheets не присылает вовсе: короткую
            # строку дополняем пустыми, иначе колонки разъедутся на первой же
            # заявке без заметки.
            padded = cells + [""] * (len(header) - len(cells))
            row = dict(zip(header, padded))
            if len(cells) > len(header):
                row[None] = cells[len(header):]
            rows.append(row)
        return rows

    def rows(self) -> list[dict]:
        service = self._service()
        values = service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=self.range_name
        ).execute().get("values", [])
        return self._rows_from(values)

    def describe(self) -> str:
        return f"Google Sheet: {self.sheet_id} (диапазон {self.range_name})"
