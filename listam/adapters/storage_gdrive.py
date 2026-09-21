"""Хранилище в Google Drive.

Скрипт ходит в Drive от сервисного аккаунта: идентификатор папки и путь к
ключу приходят из окружения, в коде их нет. Папку нужно расшарить на адрес
сервисного аккаунта с правом редактирования.

Зависимости ставятся отдельно: pip install -r requirements-gdrive.txt
"""
from __future__ import annotations

from pathlib import Path

from listam.adapters.filenames import drop_wal_sidecars
from listam.ports.storage import CheckReport, Storage, StorageError

SCOPES = ["https://www.googleapis.com/auth/drive"]
MIME_BINARY = "application/octet-stream"


class GDriveStorage(Storage):
    def __init__(self, folder_id: str, credentials_file: str | Path):
        if not folder_id:
            raise StorageError("Не задан GDRIVE_FOLDER — идентификатор папки в Drive")
        self.folder_id = folder_id
        self.credentials_file = Path(credentials_file) if credentials_file else None
        self._service = None

    # --- служебное -------------------------------------------------------
    def _connect(self):
        if self._service is not None:
            return self._service
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - зависит от окружения
            raise StorageError(
                "Не установлены библиотеки Google API. "
                "Поставь их: pip install -r requirements-gdrive.txt"
            ) from exc
        if not self.credentials_file or not self.credentials_file.exists():
            raise StorageError(
                f"Не найден ключ сервисного аккаунта: {self.credentials_file}. "
                "Путь задаётся переменной GDRIVE_CREDENTIALS_FILE."
            )
        credentials = service_account.Credentials.from_service_account_file(
            str(self.credentials_file), scopes=SCOPES
        )
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self._service

    def _find(self, name: str) -> str | None:
        service = self._connect()
        safe = Path(name).name.replace("'", "\\'")
        query = f"name = '{safe}' and '{self.folder_id}' in parents and trashed = false"
        result = (
            service.files()
            .list(q=query, fields="files(id, name)", pageSize=1, supportsAllDrives=True,
                  includeItemsFromAllDrives=True)
            .execute()
        )
        files = result.get("files", [])
        return files[0]["id"] if files else None

    # --- порт ------------------------------------------------------------
    def exists(self, name: str) -> bool:
        return self._find(name) is not None

    def download(self, name: str, target: str | Path) -> bool:
        from googleapiclient.http import MediaIoBaseDownload

        file_id = self._find(name)
        if file_id is None:
            return False
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        request = self._connect().files().get_media(fileId=file_id, supportsAllDrives=True)
        with open(target, "wb") as handle:
            downloader = MediaIoBaseDownload(handle, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        drop_wal_sidecars(target)   # спутники относились к прежнему файлу
        return True

    def upload(self, source: str | Path, name: str) -> None:
        from googleapiclient.http import MediaFileUpload

        source = Path(source)
        if not source.exists():
            raise StorageError(f"Нечего заливать: {source} не существует")
        service = self._connect()
        media = MediaFileUpload(str(source), mimetype=MIME_BINARY, resumable=True)
        file_id = self._find(name)
        if file_id:
            service.files().update(
                fileId=file_id, media_body=media, supportsAllDrives=True
            ).execute()
        else:
            service.files().create(
                body={"name": Path(name).name, "parents": [self.folder_id]},
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            ).execute()

    def names(self, prefix: str = "") -> list[str]:
        service = self._connect()
        query = f"'{self.folder_id}' in parents and trashed = false"
        found: list[str] = []
        token = None
        while True:
            result = (
                service.files()
                .list(q=query, fields="nextPageToken, files(name)", pageSize=200,
                      pageToken=token, supportsAllDrives=True,
                      includeItemsFromAllDrives=True)
                .execute()
            )
            found.extend(item["name"] for item in result.get("files", []))
            token = result.get("nextPageToken")
            if not token:
                break
        return sorted(name for name in found if name.startswith(prefix))

    def delete(self, name: str) -> None:
        file_id = self._find(name)
        if file_id is None:
            return
        self._connect().files().delete(fileId=file_id, supportsAllDrives=True).execute()

    def check(self) -> CheckReport:
        try:
            service = self._connect()
            folder = (
                service.files()
                .get(fileId=self.folder_id, fields="id, name, capabilities/canAddChildren",
                     supportsAllDrives=True)
                .execute()
            )
        except StorageError as exc:
            return CheckReport(ok=False, details=str(exc))
        except Exception as exc:  # pragma: no cover - сетевые ошибки Google API
            return CheckReport(ok=False, details=f"Drive недоступен: {exc}")
        can_write = folder.get("capabilities", {}).get("canAddChildren", False)
        if not can_write:
            return CheckReport(
                ok=False,
                details=(
                    f"папка «{folder.get('name')}» видна, но запись запрещена — "
                    "дай сервисному аккаунту права редактора"
                ),
            )
        return CheckReport(ok=True, details=f"Google Drive, папка «{folder.get('name')}»")
