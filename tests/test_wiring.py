"""Сборка адаптеров по конфигу: какой адаптер — решает конфиг, не код."""
from __future__ import annotations

import pytest

from listam.adapters.requests_csv import CsvRequestsSource
from listam.config import Config, ConfigError
from listam.ports.exporter import Exporter
from listam.ports.fetcher import Fetcher
from listam.ports.notifier import Notifier, NullNotifier, StdoutNotifier
from listam.ports.rate import RateProvider
from listam.ports.requests_source import RequestsSource
from listam.ports.storage import Storage
from listam.wiring import UnknownAdapter, build_exporter, build_fetcher, build_notifier, \
    build_rate_provider, build_requests_source, build_storage


def cfg(data: dict) -> Config:
    return Config(data, env="test", path="config/test.yaml")


def test_storage_kind_local_builds_local_storage(tmp_path):
    storage = build_storage(cfg({"storage": {"kind": "local", "directory": str(tmp_path)}}))
    assert isinstance(storage, Storage)
    assert type(storage).__name__ == "LocalStorage"


def test_storage_kind_gdrive_builds_gdrive_storage():
    storage = build_storage(
        cfg({"storage": {"kind": "gdrive", "folder": "folder-1", "credentials_file": "k.json"}})
    )
    assert type(storage).__name__ == "GDriveStorage"


def test_unknown_storage_kind_names_the_options():
    with pytest.raises(UnknownAdapter) as error:
        build_storage(cfg({"storage": {"kind": "dropbox"}}))
    assert "local" in str(error.value)


def test_rate_kind_fixed_uses_configured_value():
    provider = build_rate_provider(cfg({"rate": {"kind": "fixed", "amd_per_usd": 400}}), None)
    assert isinstance(provider, RateProvider)
    assert provider.amd_per_usd().value == 400


def test_rate_kind_rate_am_gets_the_fetcher():
    fetcher = build_fetcher(cfg({"scrape": {"base_url": "http://x", "delay_seconds": 0}}))
    provider = build_rate_provider(cfg({"rate": {"kind": "rate_am"}}), fetcher)
    assert type(provider).__name__ == "RateAmProvider"
    assert provider.fetcher is fetcher


def test_fetcher_takes_delay_and_base_url_from_config():
    fetcher = build_fetcher(
        cfg({"scrape": {"base_url": "https://www.list.am/ru", "delay_seconds": 2.5}})
    )
    assert isinstance(fetcher, Fetcher)
    assert fetcher.delay_seconds == 2.5
    assert fetcher.base_url == "https://www.list.am/ru"


def test_exporter_kind_xlsx_local(tmp_path):
    exporter = build_exporter(cfg({"export": {"kind": "xlsx_local", "path": str(tmp_path)}}))
    assert isinstance(exporter, Exporter)


def test_notifier_defaults_to_silence():
    assert isinstance(build_notifier(cfg({})), NullNotifier)


def test_notifier_kind_stdout():
    assert isinstance(build_notifier(cfg({"notify": {"kind": "stdout"}})), StdoutNotifier)
    assert isinstance(build_notifier(cfg({"notify": {"kind": "stdout"}})), Notifier)


def test_requests_source_is_empty_when_the_config_says_nothing():
    source = build_requests_source(cfg({}))
    assert isinstance(source, RequestsSource)
    assert source.active_requests() == []


def test_csv_source_is_built_from_the_config_path(tmp_path):
    source = build_requests_source(
        cfg({"requests": {"kind": "csv", "path": str(tmp_path / "r.csv")}})
    )
    assert isinstance(source, CsvRequestsSource)
    assert source.path == tmp_path / "r.csv"


def test_csv_source_without_a_path_is_a_config_error():
    with pytest.raises(ConfigError):
        build_requests_source(cfg({"requests": {"kind": "csv"}}))


def test_gsheet_source_is_built_from_the_config_sheet():
    source = build_requests_source(cfg({"requests": {"kind": "gsheet",
                                                     "sheet": "SHEET-ID",
                                                     "credentials_file": "key.json"}}))
    assert source.sheet_id == "SHEET-ID"
    assert source.credentials_file == "key.json"


def test_an_unknown_requests_kind_names_the_options():
    with pytest.raises(UnknownAdapter) as error:
        build_requests_source(cfg({"requests": {"kind": "телепатия"}}))
    assert "csv" in str(error.value) and "gsheet" in str(error.value)


def test_fetcher_kind_files_reads_saved_pages(tmp_path):
    fetcher = build_fetcher(
        cfg({"scrape": {"kind": "files", "pages_dir": str(tmp_path)}})
    )
    assert type(fetcher).__name__ == "FilesFetcher"
    assert fetcher.directory == tmp_path


def test_notifier_kind_telegram_takes_the_secrets_from_the_config():
    notifier = build_notifier(cfg({"notify": {"kind": "telegram", "token": "123:abc",
                                              "chat_id": 1930501720}}))
    assert isinstance(notifier, Notifier)
    assert type(notifier).__name__ == "TelegramNotifier"
    assert notifier.chat_id == "1930501720"


def test_telegram_without_a_token_is_a_config_error_naming_the_variable():
    """Отправлять в никуда мы не будем: пустой секрет — код 2 на входе."""
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        build_notifier(cfg({"notify": {"kind": "telegram", "token": "", "chat_id": "1"}}))


@pytest.mark.parametrize("kind", ["http", "playwright"])
def test_the_funnel_may_ask_the_fetcher_for_its_own_pause(kind, tmp_path):
    """Страницы объявлений открываются с паузой `funnel.delay_seconds`,
    длиннее ленточной: `build_fetcher` берёт её аргументом вместо конфига."""
    config = cfg({"scrape": {"kind": kind, "base_url": "http://x", "delay_seconds": 1.5,
                             "profile_dir": str(tmp_path / "browser")}})
    assert build_fetcher(config).delay_seconds == 1.5
    assert build_fetcher(config, delay_seconds=5).delay_seconds == 5
