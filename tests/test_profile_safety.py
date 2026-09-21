"""M1: профиль браузера сносится только свой, и не может стоять на рабочей папке.

`_reset_profile` делает `rmtree` по пути из конфига. Опечатка `profile_dir: ./data`
уносит боевую базу. Поэтому две преграды: адаптер трогает только тот каталог,
который сам завёл, а конфиг не даёт назначить профилем рабочую папку.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from listam.adapters.fetcher_playwright import PROFILE_MARKER, PlaywrightFetcher
from listam.config import Config, ConfigError
from listam.wiring import build_fetcher


def fetcher(profile: Path) -> PlaywrightFetcher:
    return PlaywrightFetcher(base_url="https://example.invalid", delay_seconds=0,
                             user_data_dir=profile)


def test_profile_the_adapter_created_is_swept_away(tmp_path):
    profile = tmp_path / "browser"
    browser = fetcher(profile)
    browser._ensure_profile_dir()
    (profile / "Cookies").write_text("cf_clearance", encoding="utf-8")

    assert browser._reset_profile() is True
    assert not profile.exists()


def test_foreign_directory_is_never_swept_away(tmp_path):
    """`profile_dir: ./data` — опечатка, а не разрешение стереть базу."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "listam.sqlite").write_text("боевая база", encoding="utf-8")

    browser = fetcher(data)
    browser._ensure_profile_dir()

    assert browser._reset_profile() is False
    assert (data / "listam.sqlite").exists()
    assert not (data / PROFILE_MARKER).exists()


def test_profile_left_by_an_older_version_is_recognised_by_its_shape(tmp_path):
    """Профиль, заведённый до появления маркера, сносить всё равно можно."""
    profile = tmp_path / "browser"
    (profile / "Default").mkdir(parents=True)
    (profile / "Local State").write_text("{}", encoding="utf-8")

    assert fetcher(profile)._reset_profile() is True
    assert not profile.exists()


def config_with(tmp_path, profile: str) -> Config:
    return Config(
        {
            "storage": {"work_dir": str(tmp_path / "data"), "db_filename": "listam.sqlite"},
            "scrape": {
                "kind": "playwright",
                "base_url": "https://www.list.am/ru",
                "pages_dir": str(tmp_path / "data" / "pages"),
                "profile_dir": profile,
                "delay_seconds": 0,
            },
        },
        env="test",
        path=tmp_path / "test.yaml",
    )


@pytest.mark.parametrize("profile", ["data", "data/pages", "."])
def test_profile_on_a_working_directory_is_refused(tmp_path, profile):
    config = config_with(tmp_path, str(tmp_path / profile))

    with pytest.raises(ConfigError) as error:
        build_fetcher(config)

    assert "profile_dir" in str(error.value)


def test_profile_beside_the_working_directory_is_fine(tmp_path):
    config = config_with(tmp_path, str(tmp_path / "data" / "browser"))

    assert build_fetcher(config) is not None


def test_profile_on_the_storage_directory_is_refused(tmp_path):
    """Находка 16: `storage.directory` — тоже рабочая папка.

    При `storage.kind: local` в ней лежит общая копия базы и все бэкапы.
    Профиль браузера, назначенный туда, сносится вместе с ними.
    """
    config = config_with(tmp_path, str(tmp_path / "remote"))
    config.data["storage"]["directory"] = str(tmp_path / "remote")

    with pytest.raises(ConfigError) as error:
        build_fetcher(config)

    assert "storage.directory" in str(error.value)


def test_profile_over_the_export_directory_is_refused(tmp_path):
    """Профиль-родитель рабочей папки так же опасен, как она сама."""
    config = config_with(tmp_path, str(tmp_path / "out"))
    config.data["export"] = {"path": str(tmp_path / "out" / "xlsx")}

    with pytest.raises(ConfigError) as error:
        build_fetcher(config)

    assert "export.path" in str(error.value)
