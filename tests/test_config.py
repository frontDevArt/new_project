"""Тесты загрузки конфигурации."""
import os

import pytest

from listam.config import ConfigError, load_config


def write_cfg(tmp_path, name, text):
    d = tmp_path / "config"
    d.mkdir(exist_ok=True)
    (d / f"{name}.yaml").write_text(text, encoding="utf-8")
    return d


def test_loads_yaml_for_requested_env(tmp_path):
    d = write_cfg(tmp_path, "dev", "env: dev\nscrape:\n  category: 60\n")
    cfg = load_config(env="dev", config_dir=d)
    assert cfg.get("scrape.category") == 60


def test_env_var_placeholder_is_substituted(tmp_path, monkeypatch):
    d = write_cfg(tmp_path, "dev", "storage:\n  folder: ${GDRIVE_FOLDER}\n")
    monkeypatch.setenv("GDRIVE_FOLDER", "folder-abc123")
    cfg = load_config(env="dev", config_dir=d)
    assert cfg.get("storage.folder") == "folder-abc123"


def test_missing_env_var_raises_named_error(tmp_path, monkeypatch):
    d = write_cfg(tmp_path, "dev", "storage:\n  folder: ${GDRIVE_FOLDER}\n")
    monkeypatch.delenv("GDRIVE_FOLDER", raising=False)
    with pytest.raises(ConfigError) as e:
        load_config(env="dev", config_dir=d)
    assert "GDRIVE_FOLDER" in str(e.value)


def test_env_defaults_to_app_env_variable(tmp_path, monkeypatch):
    write_cfg(tmp_path, "prod", "env: prod\n")
    d = tmp_path / "config"
    monkeypatch.setenv("APP_ENV", "prod")
    cfg = load_config(config_dir=d)
    assert cfg.get("env") == "prod"


def test_unknown_env_raises(tmp_path):
    d = write_cfg(tmp_path, "dev", "env: dev\n")
    with pytest.raises(ConfigError):
        load_config(env="staging", config_dir=d)


def test_get_returns_default_for_missing_key(tmp_path):
    d = write_cfg(tmp_path, "dev", "scrape:\n  delay_seconds: 1.5\n")
    cfg = load_config(env="dev", config_dir=d)
    assert cfg.get("scrape.max_pages", 20) == 20


def test_require_raises_for_missing_key(tmp_path):
    d = write_cfg(tmp_path, "dev", "env: dev\n")
    cfg = load_config(env="dev", config_dir=d)
    with pytest.raises(ConfigError):
        cfg.require("storage.kind")


def test_values_are_read_from_dotenv_file(tmp_path, monkeypatch):
    d = write_cfg(tmp_path, "dev", "storage:\n  folder: ${GDRIVE_FOLDER}\n")
    (tmp_path / ".env").write_text("GDRIVE_FOLDER=from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("GDRIVE_FOLDER", raising=False)
    cfg = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")
    assert cfg.get("storage.folder") == "from-dotenv"


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch):
    d = write_cfg(tmp_path, "dev", "storage:\n  folder: ${GDRIVE_FOLDER}\n")
    (tmp_path / ".env").write_text("GDRIVE_FOLDER=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("GDRIVE_FOLDER", "from-shell")
    cfg = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")
    assert cfg.get("storage.folder") == "from-shell"
