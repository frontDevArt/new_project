"""Живой канал: сообщение доходит до чата и читается в Telegram Web.

Прогон только явный:
    TELEGRAM_LIVE=1 .venv/Scripts/python.exe -m pytest -q tests/live -s
Нужны: .env с ключами и профиль tmp/telegram-profile со входом (шаг 1 задачи 5.4).
Каждый прогон шлёт НАСТОЯЩИЕ сообщения в чат из TELEGRAM_CHAT_ID — сейчас это
личка брокера.
"""
import os
import subprocess
import sys
import time
import uuid

import pytest

# Ключи лежат в .env, а pytest его сам не читает: load_dotenv зовётся внутри
# load_config(). Тянем его здесь — но только под флагом, чтобы в обычном
# прогоне токен не появился в os.environ и не расскипал контракт telegram.
if os.environ.get("TELEGRAM_LIVE") == "1":
    from dotenv import load_dotenv

    load_dotenv(".env", override=False)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("TELEGRAM_LIVE") != "1"
        or not os.environ.get("TELEGRAM_BOT_TOKEN")
        or not os.environ.get("TELEGRAM_CHAT_ID"),
        reason="живой канал выключен: нет TELEGRAM_LIVE=1 или ключей в .env",
    ),
]

# Пауза между отправками: Bot API режет темп, и 429 на пачке частей ловится
# и при меньшем. Пустая переменная в .env — это «по умолчанию», а не ноль.
SEND_DELAY = float(os.environ.get("TELEGRAM_SEND_DELAY") or "4")
RENDER_WAIT = 20_000  # мс: сколько ждём, пока сообщение доедет до вкладки
# Больше стольких сообщений сквозной дайджест в личку брокера не шлёт:
# на синтетических заявках фазы 4 дайджест — это 50 сообщений.
MAX_DIGEST_PARTS = int(os.environ.get("TELEGRAM_LIVE_MAX_PARTS") or "3")


def notifier():
    from listam.adapters.notify_telegram import TelegramNotifier

    return TelegramNotifier(token=os.environ["TELEGRAM_BOT_TOKEN"],
                            chat_id=os.environ["TELEGRAM_CHAT_ID"])


def test_short_message_is_visible_in_the_chat(chat):
    from listam.layout import text_message

    mark = f"listam-live {uuid.uuid4().hex[:8]}"
    notifier().send(text_message(f"{mark}\nпроверка канала, это не заявка"))
    chat.get_by_text(mark).last.wait_for(timeout=RENDER_WAIT)
    time.sleep(SEND_DELAY)


def live_section(mark: str, count: int):
    """Раздел заявки из `count` карточек по три строки — как у настоящего
    «звони сейчас», с символами, которые HTML обязан экранировать (Т-3)."""
    from listam.layout import Section, Span

    cards = [[[Span("🆕 "), Span(f"${100000 + n:,}".replace(",", " "), bold=True),
               Span(" · 100 м² · $1 650/м² (−12% к району)")],
              [Span(f"📍 {mark} карточка {n:04d}, ул. <Раффи> & Co · этаж 5/9")],
              [Span("👤 Собственник · 🟢 балл 82 · 🔗 "),
               Span("Открыть", href="https://www.list.am/ru")]]
             for n in range(count)]
    return Section(head=[[Span(f"🔥 ЗВОНИ СЕЙЧАС · {mark} · это не заявка", bold=True)],
                         [Span("━━━━━━━━━━━━━━━")]],
                   cards=cards, tail=[[Span(f"➕ {mark} конец")]])


def test_a_card_arrives_as_html(chat):
    """Т-1, Т-3: цена жирным, «Открыть» — ссылка словом, `<` `>` `&` на месте."""
    from listam.layout import Message

    mark = f"listam-live {uuid.uuid4().hex[:8]}"
    notifier().send(Message([live_section(mark, 2)]))
    chat.get_by_text(f"{mark} карточка 0001, ул. <Раффи> & Co").last.wait_for(
        timeout=RENDER_WAIT)
    chat.get_by_role("link", name="Открыть").last.wait_for(timeout=RENDER_WAIT)
    time.sleep(SEND_DELAY)


def test_long_message_is_cut_between_cards_with_the_head(chat):
    """Т-2: длинная заявка режется между карточками, продолжение — с шапкой."""
    from listam.adapters.notify_telegram import LIMIT, split_section
    from listam.layout import Message, to_html

    mark = f"listam-live {uuid.uuid4().hex[:8]}"
    section = live_section(mark, 40)
    assert len(to_html(section)) > LIMIT, "тест бессмыслен, если раздел влезает целиком"
    print(f"длинное: частей {len(split_section(section))}")

    notifier().send(Message([section]))
    chat.get_by_text(f"{mark} конец").last.wait_for(timeout=RENDER_WAIT)
    chat.get_by_text(f"{mark} · это не заявка (продолжение)").last.wait_for(
        timeout=RENDER_WAIT)
    seen = "\n".join(chat.get_by_text(mark).all_inner_texts())
    assert all(f"{mark} карточка {n:04d}" in seen for n in (0, 1, 38, 39))
    time.sleep(SEND_DELAY)


def run_listam(config_dir, *args):
    return subprocess.run(
        [sys.executable, "-m", "listam", "--env", "dev", "--config-dir", str(config_dir),
         *args],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def test_digest_command_reaches_the_chat(chat, live_config_dir):
    """Сквозняк: команда CLI, а не только адаптер. Сначала сухой прогон —
    отправленное не отзывается, и 50 сообщений в личку брокера тест не шлёт."""
    from listam.adapters.notify_telegram import LIMIT

    dry = run_listam(live_config_dir, "notify", "--digest", "--dry-run")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    lines = dry.stdout.splitlines()
    text = "\n".join(lines[1:next(n for n, line in enumerate(lines)
                                  if line.startswith("Событий:"))])
    # Простой текст не хранит разметку карточек: оцениваем снизу — по
    # сообщению на заявку (черта под шапкой) плюс сводка, и по объёму.
    parts = max(text.count("━━━━━━━━━━━━━━━") + 1, -(-len(text) // LIMIT))
    if parts > MAX_DIGEST_PARTS:
        pytest.skip(f"дайджест вышел бы {parts} сообщениями (> {MAX_DIGEST_PARTS}): "
                    f"в личку брокера не шлём; подними TELEGRAM_LIVE_MAX_PARTS осознанно")

    run = run_listam(live_config_dir, "notify", "--digest")
    assert run.returncode == 0, run.stdout + run.stderr
    assert ", отправлено" in run.stdout
    # Первая строка сообщения называет окно с точностью до минуты — она своя
    # у каждой отправки, и чужое вчерашнее сообщение за неё не сойдёт.
    head = run.stdout.splitlines()[1]
    print(f"дайджест: частей {parts}, первая строка: {head}")
    chat.get_by_text(head[:60]).last.wait_for(timeout=RENDER_WAIT)
    time.sleep(SEND_DELAY)
