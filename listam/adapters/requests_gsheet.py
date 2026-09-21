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

    def rows(self) -> list[dict]:
        service = self._service()
        values = service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id, range=self.range_name
        ).execute().get("values", [])
        if not values:
            return []
        header = [str(name).strip() for name in values[0]]
        # Пустые хвостовые ячейки Sheets не присылает вовсе: короткую строку
        # дополняем пустыми, иначе колонки разъедутся на первой же заявке
        # без заметки.
        return [
            dict(zip(header, list(row) + [""] * (len(header) - len(row))))
            for row in values[1:]
            if any(str(cell).strip() for cell in row)
        ]

    def describe(self) -> str:
        return f"Google Sheet: {self.sheet_id}"
