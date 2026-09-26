"""Drive от имени владельца папки.

Сервисный аккаунт видит расшаренную папку личного Drive, но создать в ней файл
не может: своей квоты у него нет, Google отвечает 403 «Service Accounts do not
have storage quota» (проверено вживую 27.09.2026). Поэтому хранилище умеет
ходить в Drive токеном владельца — `storage.token_file`, его один раз пишет
`python -m listam drive-login`. Таблица заявок остаётся на сервисном аккаунте:
её только читают.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from listam.adapters.storage_gdrive import GDriveStorage
from listam.config import Config
from listam.ports.storage import StorageError
from listam.wiring import build_storage


def cfg(data: dict) -> Config:
    return Config(data, env="test", path="config/test.yaml")


def test_wiring_passes_token_file_to_gdrive_storage():
    storage = build_storage(cfg({"storage": {
        "kind": "gdrive", "folder": "folder-1", "credentials_file": "k.json",
        "token_file": "t.json"}}))
    assert str(storage.token_file) == "t.json"


def test_without_token_file_storage_stays_on_service_account():
    storage = build_storage(cfg({"storage": {
        "kind": "gdrive", "folder": "folder-1", "credentials_file": "k.json"}}))
    assert storage.token_file is None


def test_missing_token_file_names_the_login_command(tmp_path):
    storage = GDriveStorage("folder-1", tmp_path / "k.json", token_file=tmp_path / "nope.json")
    with pytest.raises(StorageError) as error:
        storage._connect()
    assert "drive-login" in str(error.value)
    report = storage.check()
    assert not report.ok and "drive-login" in report.details


def test_token_file_is_used_instead_of_service_account_key(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"refresh_token": "r"}), encoding="utf-8")
    seen = {}

    class FakeCredentials:
        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            seen["path"], seen["scopes"] = path, scopes
            return "user-creds"

    fake_module = types.SimpleNamespace(Credentials=FakeCredentials)
    monkeypatch.setitem(sys.modules, "google.oauth2.credentials", fake_module)
    monkeypatch.setattr("googleapiclient.discovery.build",
                        lambda *a, credentials, **k: ("service", credentials))

    # ключа сервисного аккаунта нет вовсе — с токеном он не нужен
    storage = GDriveStorage("folder-1", tmp_path / "absent-key.json", token_file=token)
    assert storage._connect() == ("service", "user-creds")
    assert seen["path"] == str(token)
    assert seen["scopes"] == ["https://www.googleapis.com/auth/drive"]


def _write_env(tmp_path, token_line: str):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "t.yaml").write_text(
        "storage:\n  kind: gdrive\n  folder: f\n" + token_line, encoding="utf-8")
    return config_dir


def test_drive_login_writes_owner_token_where_storage_reads_it(tmp_path, monkeypatch, capsys):
    from listam import cli

    token = tmp_path / "token.json"
    client = tmp_path / "client.json"
    client.write_text("{}", encoding="utf-8")
    config_dir = _write_env(tmp_path, f"  token_file: {token.as_posix()}\n")
    seen = {}

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            seen["client"], seen["scopes"] = path, scopes
            return cls()

        def run_local_server(self, port):
            return types.SimpleNamespace(to_json=lambda: '{"refresh_token": "r"}')

    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow",
                        types.SimpleNamespace(InstalledAppFlow=FakeFlow))
    code = cli.main(["--env", "t", "--config-dir", str(config_dir),
                     "drive-login", "--client", str(client)])
    assert code == 0
    assert json.loads(token.read_text(encoding="utf-8")) == {"refresh_token": "r"}
    assert seen == {"client": str(client), "scopes": ["https://www.googleapis.com/auth/drive"]}
    assert token.as_posix() in capsys.readouterr().out


def test_drive_login_without_token_file_in_config_refuses(tmp_path, capsys):
    from listam import cli

    config_dir = _write_env(tmp_path, "")
    code = cli.main(["--env", "t", "--config-dir", str(config_dir), "drive-login"])
    assert code == 2
    assert "storage.token_file" in capsys.readouterr().err
