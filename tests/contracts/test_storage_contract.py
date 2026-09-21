"""Контрактный тест порта Storage.

Storage хранит файл базы между прогонами. Реализация local используется при
отладке, gdrive — в расписании. Тест один и тот же: подменили реализацию —
он обязан пройти без изменений.
"""
from __future__ import annotations

import os

import pytest

from listam.adapters.storage_local import LocalStorage
from listam.ports.storage import Storage

GDRIVE_READY = bool(os.environ.get("GDRIVE_FOLDER") and os.environ.get("GDRIVE_CREDENTIALS_FILE"))


@pytest.fixture(params=["local", pytest.param("gdrive", marks=pytest.mark.skipif(
    not GDRIVE_READY, reason="GDRIVE_FOLDER/GDRIVE_CREDENTIALS_FILE не заданы"))])
def storage(request, tmp_path) -> Storage:
    if request.param == "local":
        return LocalStorage(directory=tmp_path / "remote")
    from listam.adapters.storage_gdrive import GDriveStorage
    return GDriveStorage(
        folder_id=os.environ["GDRIVE_FOLDER"],
        credentials_file=os.environ["GDRIVE_CREDENTIALS_FILE"],
    )


@pytest.fixture
def local_file(tmp_path):
    path = tmp_path / "listam.sqlite"
    path.write_bytes(b"fake-sqlite-payload")
    return path


def test_is_a_storage(storage):
    assert isinstance(storage, Storage)


def test_missing_file_does_not_exist(storage):
    assert storage.exists("нет-такого.sqlite") is False


def test_uploaded_file_exists(storage, local_file):
    storage.upload(local_file, "listam.sqlite")
    assert storage.exists("listam.sqlite") is True


def test_download_returns_false_when_nothing_stored(storage, tmp_path):
    assert storage.download("listam.sqlite", tmp_path / "copy.sqlite") is False


def test_uploaded_bytes_come_back_unchanged(storage, local_file, tmp_path):
    storage.upload(local_file, "listam.sqlite")
    target = tmp_path / "copy.sqlite"
    assert storage.download("listam.sqlite", target) is True
    assert target.read_bytes() == b"fake-sqlite-payload"


def test_second_upload_replaces_the_file(storage, local_file, tmp_path):
    storage.upload(local_file, "listam.sqlite")
    local_file.write_bytes(b"newer-payload")
    storage.upload(local_file, "listam.sqlite")
    target = tmp_path / "copy.sqlite"
    storage.download("listam.sqlite", target)
    assert target.read_bytes() == b"newer-payload"


def test_check_reports_writable_storage(storage):
    report = storage.check()
    assert report.ok is True
    assert report.details
