"""Вёрстка по пункту 8 анализа: модель сообщения, карточка, три вида.

Сети и базы здесь нет: вёрстка — чистые функции над событиями, заявками
и медианами. Отправку проверяют тесты адаптера, выборку — уведомления.
"""
from __future__ import annotations

from datetime import datetime, timezone

from listam.config import Config
from listam.domain.events import CHEAPER, NEW, RETIRED, REVIVED, MatchEvent
from listam.domain.models import Listing, Match, Request
from listam.layout import (Message, Section, Span, card, digest_message,
                           feed_message, hot_message, plain, request_head,
                           to_html, to_plain)

AT = datetime(2026, 9, 23, 20, 0, tzinfo=timezone.utc)  # календарь: не сравнивается с часами


def make_config(green=80, hot=70) -> Config:
    from pathlib import Path

    return Config({"match": {"thresholds": {"hot": hot, "digest": 40}},
                   "notify": {"layout": {"score_green": green},
                              "feed": {"gem_percent": 15}}},
                  env="test", path=Path("config/test.yaml"))


def listing(**fields) -> Listing:
    base = dict(id="1", url="https://www.list.am/ru/item/1", district="Канакер-Зейтун",
                street="ул. Азатутян", price_usd=165000.0, area=100.0,
                price_per_sqm=1650.0, rooms=2, floor=5, floors_total=9,
                seller_type="owner")
    base.update(fields)
    return Listing(**base)


def event(kind=NEW, score=82.0, before=None, cluster=1, spread=None, request_id=4,
          reason=None, **fields) -> MatchEvent:
    return MatchEvent(kind=kind,
                      match=Match(request_id=request_id, listing_id="1", score=score,
                                  cluster_size=cluster, cluster_spread_usd=spread,
                                  retired_reason=reason),
                      listing=listing(**fields), price_before=before)


def request(**fields) -> Request:
    base = dict(id=4, external_id="R-4", client_name="Клиент 4",
                districts=["Канакер-Зейтун"], rooms=[2], area_min=80.0,
                area_max=110.0, budget_max=175000.0)
    base.update(fields)
    return Request(**base)


def texts(lines) -> list[str]:
    return ["".join(span.text for span in line) for line in lines]


# ---------------------------------------------------------------- 2.1 модель

def test_html_escapes_listing_text():
    section = Section(head=[[Span("ул. <Раффи> & Co")]], cards=[])

    html = to_html(section)

    assert "ул. &lt;Раффи&gt; &amp; Co" in html
    assert "<Раффи>" not in html


def test_html_escapes_the_link_too():
    section = Section(head=[], cards=[[[Span("Открыть", href='https://x/?a=1&b="2"')]]])

    assert '<a href="https://x/?a=1&amp;b=&quot;2&quot;">Открыть</a>' in to_html(section)


def test_plain_has_no_tags():
    section = Section(head=[[Span("ЗВОНИ", bold=True)]],
                      cards=[[[Span("$1", bold=True)], [Span("📍 А")],
                              [Span("Открыть", href="https://www.list.am/ru/item/1")]]])

    text = to_plain(section)

    assert "<" not in text and ">" not in text
    assert "ЗВОНИ" in text and "$1" in text


def test_link_is_a_word_not_a_url():
    section = Section(head=[], cards=[[[Span("🔗 "), Span("Открыть", href="https://u/1")]]])

    assert '🔗 <a href="https://u/1">Открыть</a>' in to_html(section)
    assert "🔗 Открыть: https://u/1" in to_plain(section)


def test_bold_is_a_b_tag():
    assert "<b>$165 000</b>" in to_html(Section(head=[[Span("$165 000", bold=True)]], cards=[]))


def test_cards_are_separated_by_a_blank_line_and_the_head_by_one_too():
    section = Section(head=[[Span("шапка")]],
                      cards=[[[Span("a1")], [Span("a2")]], [[Span("b1")], [Span("b2")]]],
                      tail=[[Span("хвост")]])

    assert to_plain(section) == "шапка\n\na1\na2\n\nb1\nb2\n\nхвост"


def test_one_line_cards_go_one_after_another():
    """Строки 💎 ленты — карточки из одной строки: пустая строка между ними
    растянула бы список вдвое без пользы."""
    section = Section(head=[[Span("шапка")]], cards=[[[Span("💎 1")]], [[Span("💎 2")]]])

    assert to_plain(section) == "шапка\n\n💎 1\n💎 2"


def test_a_message_is_its_sections_in_order():
    message = Message([Section(head=[[Span("один")]], cards=[]),
                       Section(head=[[Span("два")]], cards=[])])

    assert plain(message) == "один\n\nдва"


# ---------------------------------------------------------------- 2.2 карточка

def test_card_has_three_lines():
    lines = texts(card(event(), median=1875.0, config=make_config()))

    assert len(lines) == 3
    assert lines[0] == "🆕 $165 000 · 100 м² · $1 650/м² (−12% к району)"
    assert lines[1] == "📍 Канакер-Зейтун, ул. Азатутян · этаж 5/9"
    assert lines[2] == "👤 Собственник · 🟢 балл 82 · 🔗 Открыть"


def test_price_is_bold_and_open_is_the_link():
    first, _, third = card(event(), median=None, config=make_config())

    assert [span.text for span in first if span.bold] == ["$165 000"]
    assert [(span.text, span.href) for span in third if span.href] == [
        ("Открыть", "https://www.list.am/ru/item/1")]


def test_cheaper_card_names_the_old_price():
    lines = texts(card(event(kind=CHEAPER, before=171000.0, price_usd=158000.0,
                             area=95.0, price_per_sqm=1663.0),
                       median=None, config=make_config()))

    assert lines[0] == "📉 $158 000 (было $171 000) · 95 м² · $1 663/м²"


def test_revived_card_has_its_own_icon():
    assert texts(card(event(kind=REVIVED), None, make_config()))[0].startswith("♻️ ")


def test_a_gone_listing_says_so_on_the_first_line():
    first = texts(card(event(status="gone"), None, make_config()))[0]

    assert first.endswith(" · ❌ снято")


def test_agency_and_cluster_on_the_third_line():
    third = texts(card(event(score=78.0, cluster=3, spread=8000.0, seller_type="agency"),
                       None, make_config()))[2]

    assert third == "🏢 Агентство · 3 объявления, разброс $8 000 · 🟡 балл 78 · 🔗 Открыть"


def test_score_circle_follows_config():
    def circle(score, **knobs):
        return texts(card(event(score=score), None, make_config(**knobs)))[2]

    assert "🟢 балл 80" in circle(80.0)
    assert "🟡 балл 79" in circle(79.0)
    assert "🟡 балл 70" in circle(70.0)
    assert "⚪ балл 69" in circle(69.0)
    assert "🟢 балл 75" in circle(75.0, green=75)
    assert "⚪ балл 75" in circle(75.0, hot=76)


def test_percent_to_district_median():
    def first(median, per_sqm=1650.0):
        return texts(card(event(price_per_sqm=per_sqm), median, make_config()))[0]

    assert first(1875.0).endswith("$1 650/м² (−12% к району)")
    assert first(1500.0).endswith("$1 650/м² (+10% к району)")
    assert first(None).endswith("$1 650/м²")
    assert first(1650.0).endswith("$1 650/м² (0% к району)")


def test_unknown_fields_are_left_out_not_dashed():
    lines = texts(card(event(street=None, floor=None, floors_total=None,
                             seller_type=None, area=None, price_per_sqm=None),
                       None, make_config()))

    assert lines[0] == "🆕 $165 000"
    assert lines[1] == "📍 Канакер-Зейтун"
    assert lines[2] == "🟢 балл 82 · 🔗 Открыть"


# ---------------------------------------------------------------- шапка

def test_request_head_names_what_the_client_asked():
    lines = texts(request_head("🔥 ЗВОНИ СЕЙЧАС", request()))

    assert lines == ["🔥 ЗВОНИ СЕЙЧАС · R-4 · Клиент 4",
                     "Канакер-Зейтун · 2 комн. · 80–110 м² · до $175 000",
                     "━━━━━━━━━━━━━━━"]


def test_request_head_prints_only_what_is_set():
    lines = texts(request_head("🔥 ЗВОНИ СЕЙЧАС",
                               request(client_name=None, districts=["А", "Б"],
                                       rooms=[2, 3, 4], area_max=None, budget_max=None)))

    assert lines == ["🔥 ЗВОНИ СЕЙЧАС · R-4", "А, Б · 2–4 комн. · от 80 м²",
                     "━━━━━━━━━━━━━━━"]


def test_request_head_without_any_wishes_has_no_empty_line():
    lines = texts(request_head("🔥 ЗВОНИ СЕЙЧАС",
                               request(districts=[], rooms=[], area_min=None,
                                       area_max=None, budget_max=None)))

    assert lines == ["🔥 ЗВОНИ СЕЙЧАС · R-4 · Клиент 4", "━━━━━━━━━━━━━━━"]


# ---------------------------------------------------------------- «звони сейчас»

class Page:
    """То, что отдаёт `collect_events`: события и заявки по ключу."""

    def __init__(self, events, requests):
        self.events = events
        self.requests = {item.external_id: item for item in requests}

    def calls(self):
        return [item for item in self.events if item.kind != RETIRED]


def test_hot_is_one_section_per_request_with_a_human_tail():
    page = Page([event(score=90.0 - index) for index in range(4)], [request()])

    message = hot_message(page, {}, make_config(), per_request=3)

    assert len(message.sections) == 1
    section = message.sections[0]
    assert texts(section.head)[0] == "🔥 ЗВОНИ СЕЙЧАС · R-4 · Клиент 4"
    assert len(section.cards) == 3
    assert texts(section.tail) == ["➕ ещё 1 вариант — в дайджесте вечером"]


def test_hot_without_events_says_so_in_one_line():
    message = hot_message(Page([], []), {}, make_config(), per_request=5)

    assert plain(message) == "🔥 ЗВОНИ СЕЙЧАС · событий нет"


def test_no_cli_hints_in_messages():
    page = Page([event(score=90.0 - index) for index in range(12)]
                + [event(kind=RETIRED, reason="бюджет")], [request()])
    fresh = [listing(id=str(n), price_per_sqm=1000.0 + n) for n in range(20)]

    messages = [hot_message(page, {}, make_config(), per_request=2),
                digest_message(page, {}, make_config(), per_request=2, wide=5,
                               active=3, at=AT),
                feed_message(fresh, cheaper=1, gone=2,
                             medians={"Канакер-Зейтун": 2000.0},
                             config=make_config(), limit=2)]

    for message in messages:
        assert "python -m listam" not in plain(message)
        assert "listam " not in plain(message)


# ---------------------------------------------------------------- дайджест

def test_digest_starts_with_a_summary():
    page = Page([event(), event(), event(kind=CHEAPER, before=180000.0),
                 event(kind=REVIVED, request_id=7)],
                [request(), request(id=7, external_id="R-7", client_name="Клиент 7")])

    message = digest_message(page, {}, make_config(), per_request=10, wide=None,
                             active=12, at=AT)

    summary = to_plain(message.sections[0]).splitlines()
    # 20:00 UTC — это 20:00 в конфиге без locale.timezone
    assert summary[0] == "📋 ДАЙДЖЕСТ · 23.09 · 20:00"
    assert "🆕 новых — 2   📉 подешевело — 1   ♻️ вернулось — 1" in summary
    assert "👥 заявок с находками — 2 из 12" in summary
    assert "R-4 · Клиент 4 — 🆕 2  📉 1" in summary
    assert "R-7 · Клиент 7 — ♻️ 1" in summary
    assert len(message.sections) == 3


def test_digest_time_is_local():
    config = make_config()
    config.data["locale"] = {"timezone": "Asia/Yerevan"}

    message = digest_message(Page([], []), {}, config, per_request=10, wide=None,
                             active=0, at=AT)

    assert plain(message).splitlines()[0] == "📋 ДАЙДЖЕСТ · 24.09 · 00:00"


def test_retired_only_request_is_a_summary_line_not_a_message():
    """Решение 8: закрытия уходят строкой в сводку, сообщения не получают."""
    page = Page([event(), event(kind=RETIRED, request_id=51, reason="бюджет")],
                [request(), request(id=51, external_id="R-51", client_name="Клиент 51")])

    message = digest_message(page, {}, make_config(), per_request=10, wide=None,
                             active=2, at=AT)

    assert len(message.sections) == 2
    assert "R-51 · Клиент 51 — ❌ 1 (бюджет 1)" in to_plain(message.sections[0])
    assert all("R-51" not in to_plain(section) for section in message.sections[1:])


def test_a_wide_request_is_marked_in_the_summary_and_in_its_section():
    page = Page([event(score=90.0 - index) for index in range(3)], [request()])

    message = digest_message(page, {}, make_config(), per_request=1, wide=2,
                             active=1, at=AT)

    assert "R-4 · Клиент 4 — 🆕 3  ⚠️ слишком широкая" in to_plain(message.sections[0])
    section = message.sections[1]
    assert texts(section.head)[0] == "📋 ДАЙДЖЕСТ · R-4 · Клиент 4"
    assert any("⚠️ слишком широкая: 3 события за окно" in line for line in texts(section.head))
    assert texts(section.tail) == ["➕ ещё 2 варианта ниже по баллу"]


def test_an_empty_digest_is_a_summary_only():
    message = digest_message(Page([], []), {}, make_config(), per_request=10, wide=None,
                             active=3, at=AT)

    assert len(message.sections) == 1
    assert "👥 заявок с находками — 0 из 3" in plain(message)


# ---------------------------------------------------------------- лента

def test_feed_counts_and_gems():
    fresh = [listing(id="a", price_usd=89000.0, area=62.0, price_per_sqm=1435.0,
                     district="Шенгавит", url="https://www.list.am/ru/item/a"),
             listing(id="b", price_per_sqm=1900.0, district="Шенгавит")]

    message = feed_message(fresh, cheaper=41, gone=57,
                           medians={"Шенгавит": 1888.0}, config=make_config(), limit=15)

    lines = plain(message).splitlines()
    assert lines[0] == "🗞 НА ЛЕНТЕ ЗА СУТКИ"
    assert lines[1] == "🆕 2 новых · 📉 41 подешевели · ❌ 57 снято"
    assert "Самые выгодные против медианы района:" in lines
    assert ("💎 $89 000 · 62 м² · $1 435/м² (−24%) · Шенгавит · 🔗 Открыть: "
            "https://www.list.am/ru/item/a") in lines


def test_feed_gem_only_below_threshold():
    """💎 — только у тех, кто дешевле медианы на `gem_percent` и больше."""
    fresh = [listing(id="at", price_per_sqm=850.0, district="А"),     # −15%
             listing(id="near", price_per_sqm=851.0, district="А"),   # −14.9%
             listing(id="nomedian", price_per_sqm=100.0, district="Б")]

    message = feed_message(fresh, cheaper=0, gone=0, medians={"А": 1000.0},
                           config=make_config(), limit=15)

    gems = [line for line in plain(message).splitlines() if line.startswith("💎")]
    assert len(gems) == 1
    assert "(−15%)" in gems[0]


def test_feed_keeps_the_tail_honest():
    fresh = [listing(id=str(n), price_per_sqm=500.0 + n, district="А") for n in range(3)]

    message = feed_message(fresh, cheaper=0, gone=0, medians={"А": 1000.0},
                           config=make_config(), limit=1)

    assert sum(line.startswith("💎") for line in plain(message).splitlines()) == 1
    assert plain(message).endswith("➕ ещё 2 заметно дешевле медианы")


def test_feed_without_gems_says_so():
    message = feed_message([listing()], cheaper=0, gone=0, medians={},
                           config=make_config(), limit=15)

    assert "💎 заметно дешевле медианы района — нет" in plain(message)


def test_the_wide_mark_agrees_with_its_number():
    """Боевой `--dry-run` фазы 2 напечатал «444 событий за окно»."""
    page = Page([event(score=90.0 - index) for index in range(4)], [request()])

    message = digest_message(page, {}, make_config(), per_request=1, wide=1,
                             active=1, at=AT)

    assert any("⚠️ слишком широкая: 4 события за окно" in line
               for line in texts(message.sections[1].head))


def test_feed_gem_skips_anomalies():
    """Боевой `--dry-run` фазы 2: первыми 💎 шли $190 за 200 м² и $100 за
    131 м² — объявления с меткой `anomaly`. Медиана их не видит (`stats.clean`),
    и 💎 не видит тоже: «дешевле медианы на 100%» — опечатка, а не находка."""
    fresh = [listing(id="typo", price_usd=190.0, area=200.0, price_per_sqm=1.0,
                     district="А", anomaly="price_usd,price_per_sqm"),
             listing(id="real", price_per_sqm=700.0, district="А")]

    message = feed_message(fresh, cheaper=0, gone=0, medians={"А": 1000.0},
                           config=make_config(), limit=15)

    gems = [line for line in plain(message).splitlines() if line.startswith("💎")]
    assert len(gems) == 1 and "(−30%)" in gems[0]
    assert "🆕 2 новых" in plain(message), "в счёт новых аномалия входит"
