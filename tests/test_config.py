"""Тесты загрузки конфигурации."""
import os
from pathlib import Path

import pytest

from listam.config import ConfigError, load_config, positive, threshold
from listam.domain.scoring import DEFAULT_WEIGHTS


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


def test_a_zero_threshold_is_a_number_not_a_missing_value(tmp_path):
    """Ноль в пороге — самый строгий режим, а не «порога нет»."""
    d = write_cfg(tmp_path, "dev", "scrape:\n  max_gone_percent: 0\n")
    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    assert threshold(config, "scrape.max_gone_percent", 10) == 0


def test_a_null_threshold_is_the_off_switch(tmp_path):
    d = write_cfg(tmp_path, "dev", "scrape:\n  max_gone_percent: null\n")
    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    assert threshold(config, "scrape.max_gone_percent", 10) is None


def test_a_threshold_nobody_mentioned_keeps_its_default(tmp_path):
    d = write_cfg(tmp_path, "dev", "scrape:\n  category: 60\n")
    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    assert threshold(config, "scrape.max_gone_percent", 10) == 10


# Восемь порогов, на которых держатся полнота обхода и пометка снятых.
# Ноль ни у одного из них не означает «выключено» (кроме hard_page_limit,
# которому ноль здесь всё равно не положен), поэтому ноль в боевом конфиге —
# это снятый предохранитель, а не настройка.
SAFETY_THRESHOLDS = (
    "scrape.min_cards_per_page",
    "scrape.max_cards_per_page",
    "scrape.hard_page_limit",
    "scrape.max_pages_drop_percent",
    "scrape.expected_pages_min",
    "scrape.max_gone_percent",
    "scrape.fresh_stop_after_known_pages",
    "scrape.fresh_max_pages",
)

ROOT = Path(__file__).resolve().parent.parent


def shipped_config(env, tmp_path, monkeypatch):
    """Поставляемый конфиг со всеми секретами, на которые он ссылается.

    С фазы 5 M3 `prod.yaml` ссылается ещё и на ключи Telegram. Без них тест
    проходил только в общей батарее — после того как чужой тест загрузил
    настоящий `.env` в окружение, — а отдельно и на машине без ключей падал.
    """
    for name, value in (("GDRIVE_FOLDER", "folder-abc123"),
                        ("GDRIVE_CREDENTIALS_FILE", "credentials.json"),
                        ("TELEGRAM_BOT_TOKEN", "123:token"),
                        ("TELEGRAM_CHAT_ID", "1720")):
        monkeypatch.setenv(name, value)
    return load_config(env=env, config_dir=ROOT / "config",
                       dotenv_path=tmp_path / ".env")


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_shipped_configs_keep_every_safety_threshold_off_zero(env, tmp_path, monkeypatch):
    """Ноль в любом из восьми порогов — это снятый предохранитель.

    Выключить порог можно только `null`, и тогда он читается как «проверки нет»
    осознанно. Ноль же означает самый строгий режим и в боевом конфиге стоять
    не должен ни по недосмотру, ни «чтобы не мешал».
    """
    config = shipped_config(env, tmp_path, monkeypatch)

    for key in SAFETY_THRESHOLDS:
        value = threshold(config, key, "ключа нет")
        assert value != "ключа нет", f"{env}.yaml: порог {key} не задан вовсе"
        assert value != 0, f"{env}.yaml: порог {key} — ноль, это снятый предохранитель"


# Секция `match` (фаза 4 M2): веса, пороги и допуск кластеров. Ключ,
# который читается только кодом, а в поставляемом конфиге отсутствует, —
# это настройка, о которой человек не узнает: он её не увидит и не поправит.
MATCH_KEYS = (
    "match.weights",
    "match.thresholds.hot",
    "match.thresholds.digest",
    "match.budget_stretch_percent",
    "match.cluster.area_tolerance",
    "match.limit",
)


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_shipped_configs_carry_the_whole_match_section(env, tmp_path, monkeypatch):
    config = shipped_config(env, tmp_path, monkeypatch)

    for key in MATCH_KEYS:
        assert threshold(config, key, "ключа нет") != "ключа нет", \
            f"{env}.yaml: ключ {key} не задан вовсе"


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_shipped_configs_name_every_scoring_factor(env, tmp_path, monkeypatch):
    """Вес, которого нет в конфиге, не читается ничем: фактор молча пропадает."""
    config = shipped_config(env, tmp_path, monkeypatch)

    assert set(config.section("match.weights")) == set(DEFAULT_WEIGHTS)


# --- счётчик, которому ноль не годится --------------------------------
# Правило командной строки («--limit 0 не годится: меньше одной строки
# показывать нечего») ровно так же верно для конфига: ноль, пришедший из
# yaml, давал витрину из одной строки «…и ещё 30» — то есть молча прятал
# весь ответ.

def test_a_positive_threshold_refuses_zero(tmp_path):
    d = write_cfg(tmp_path, "dev", "match:\n  limit: 0\n")
    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    with pytest.raises(ConfigError) as exc:
        positive(config, "match.limit", 50)
    assert "match.limit" in str(exc.value)


def test_a_positive_threshold_refuses_a_negative_number(tmp_path):
    d = write_cfg(tmp_path, "dev", "match:\n  limit: -5\n")
    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    with pytest.raises(ConfigError):
        positive(config, "match.limit", 50)


def test_a_positive_threshold_lets_null_through_as_off(tmp_path):
    d = write_cfg(tmp_path, "dev", "match:\n  limit: null\n")
    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    assert positive(config, "match.limit", 50) is None


def test_a_missing_key_gets_the_default(tmp_path):
    d = write_cfg(tmp_path, "dev", "env: dev\n")
    config = load_config(env="dev", config_dir=d, dotenv_path=tmp_path / ".env")

    assert positive(config, "match.limit", 50) == 50



def test_both_configs_declare_the_notification_knobs():
    """Ручка, которой нет в конфиге, не существует для человека: он не знает,
    что её можно покрутить, и правит код."""
    import yaml
    from pathlib import Path

    for name in ("dev", "prod"):
        data = yaml.safe_load(Path(f"config/{name}.yaml").read_text(encoding="utf-8"))
        notify = data["notify"]
        assert notify["kind"] in ("none", "stdout", "telegram")
        for kind in ("hot", "digest", "feed"):
            assert "enabled" in notify[kind], f"{name}: notify.{kind}.enabled"
            assert "fallback_hours" in notify[kind], f"{name}: notify.{kind}.fallback_hours"
        assert "per_request" in notify["hot"]
        assert "per_request" in notify["digest"]
        assert "wide_request" in notify["digest"]
        assert "limit" in notify["feed"]
