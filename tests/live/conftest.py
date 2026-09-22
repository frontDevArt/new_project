"""Браузер с готовой сессией Telegram Web и конфиг с kind: telegram.

Всё здесь нужно только живому прогону (`TELEGRAM_LIVE=1`); в обычной батарее
тесты этой папки пропускаются раньше, чем фикстура откроет браузер.
"""
import os
import shutil
from pathlib import Path

import pytest
import yaml

PROFILE = Path("tmp/telegram-profile")


@pytest.fixture(scope="session")
def chat():
    """Вкладка с чатом из TELEGRAM_WEB_CHAT в персистентном профиле.

    Персистентный профиль, а не `storage_state`: Telegram Web держит часть
    сессии в IndexedDB, и слепок `storage_state` её не увозит. Входа нет —
    тест пропускается **до** отправки: слать то, что некому прочитать, незачем.
    """
    if not PROFILE.exists():
        pytest.skip("нет профиля tmp/telegram-profile: выполни шаг 1 задачи 5.4 руками")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as play:
        context = play.chromium.launch_persistent_context(str(PROFILE), headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(os.environ.get("TELEGRAM_WEB_CHAT", "https://web.telegram.org/k/"))
        # `networkidle` у Telegram Web не наступает никогда: соединение с сервером
        # открыто всё время. Ждём то, что видит человек: поле ввода открытого
        # чата — или экран входа.
        ready = page.locator(".input-message-input").or_(page.get_by_text("Log in to Telegram"))
        ready.first.wait_for(timeout=60_000)
        if page.get_by_text("Log in to Telegram").count():
            context.close()
            pytest.skip("в профиле нет входа: выполни шаг 1 задачи 5.4 руками")
        yield page
        context.close()


@pytest.fixture(scope="session")
def live_config_dir(tmp_path_factory):
    """Копия папки конфига с notify.kind: telegram — исходный конфиг не трогаем.

    Откуда копировать — `TELEGRAM_LIVE_CONFIG` (по умолчанию `config`): живой
    дайджест идёт по базе, на которую смотрит этот конфиг, и её журнал
    отправок сдвигается, как после настоящей отправки.
    """
    source = Path(os.environ.get("TELEGRAM_LIVE_CONFIG") or "config")
    target = tmp_path_factory.mktemp("config-live")
    shutil.copytree(source, target, dirs_exist_ok=True)
    path = target / "dev.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["notify"].update({"kind": "telegram",
                           "token": "${TELEGRAM_BOT_TOKEN}",
                           "chat_id": "${TELEGRAM_CHAT_ID}"})
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return target
