"""Контрактный тест порта Fetcher: проходит для любой реализации.

Реализаций три: http — ходит в сеть голым запросом, files — читает страницы,
сохранённые на диск, playwright — ходит в сеть настоящим браузером (иначе
list.am отдаёт проверку Cloudflare). Смысл контракта: подменили реализацию —
тесты обязаны пройти без изменений.

Тесты playwright пропускаются, если пакет не установлен или браузер не скачан.
"""
from __future__ import annotations

import json
import time

import pytest

from listam.adapters.fetcher_files import FilesFetcher
from listam.adapters.fetcher_http import HttpFetcher
from listam.adapters.fetcher_playwright import PlaywrightFetcher
from listam.ports.fetcher import FetchError, Fetcher

try:
    import playwright  # noqa: F401

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


@pytest.fixture(params=["http", "files", "playwright"])
def site(request, http_site, tmp_path):
    """Одинаковый интерфейс к «сайту» для обеих реализаций Fetcher."""

    class HttpSite:
        def publish(self, path: str, body: str) -> None:
            http_site.route(path, body)

        def fetcher(self) -> Fetcher:
            return HttpFetcher(base_url=http_site.base_url, delay_seconds=0, retries=1)

    class FilesSite:
        def __init__(self):
            self.directory = tmp_path / "pages"
            self.directory.mkdir()

        def publish(self, path: str, body: str) -> None:
            (self.directory / FilesFetcher.filename_for(path)).write_text(body, encoding="utf-8")

        def fetcher(self) -> Fetcher:
            return FilesFetcher(directory=self.directory)

    class PlaywrightSite(HttpSite):
        def __init__(self):
            self._fetchers: list[Fetcher] = []

        def fetcher(self) -> Fetcher:
            fetcher = PlaywrightFetcher(
                base_url=http_site.base_url, delay_seconds=0, retries=1, timeout=30
            )
            self._fetchers.append(fetcher)
            return fetcher

        def shutdown(self):
            for fetcher in self._fetchers:
                fetcher.close()

    if request.param == "http":
        yield HttpSite()
        return
    if request.param == "files":
        yield FilesSite()
        return
    if not HAS_PLAYWRIGHT:
        pytest.skip("playwright не установлен: pip install -r requirements-playwright.txt")
    site = PlaywrightSite()
    yield site
    site.shutdown()


def test_is_a_fetcher(site):
    assert isinstance(site.fetcher(), Fetcher)


def test_returns_page_text(site):
    site.publish("/page", "<html>привет</html>")
    assert "привет" in site.fetcher().get("/page")


def test_missing_page_raises_fetch_error(site):
    with pytest.raises(FetchError):
        site.fetcher().get("/missing")


def test_counts_pages_it_read(site):
    site.publish("/a", "a")
    fetcher = site.fetcher()
    fetcher.get("/a")
    fetcher.get("/a")
    assert fetcher.requests_made == 2


def test_paginated_paths_are_distinct_pages(site):
    site.publish("/category/60", "первая")
    site.publish("/category/60/2", "вторая")
    fetcher = site.fetcher()
    assert "первая" in fetcher.get("/category/60")
    assert "вторая" in fetcher.get("/category/60/2")


# --- дальше поведение, которое есть только у http ----------------------


def test_absolute_url_works_without_base(http_site):
    http_site.route("/abs", "ok")
    assert HttpFetcher(delay_seconds=0).get(f"{http_site.base_url}/abs") == "ok"


def test_delay_is_kept_between_requests(http_site):
    http_site.route("/a", "a")
    http_site.route("/b", "b")
    fetcher = HttpFetcher(base_url=http_site.base_url, delay_seconds=0.4)
    started = time.monotonic()
    fetcher.get("/a")
    fetcher.get("/b")
    # Допуск на разрешение таймера Windows (~16 мс): time.sleep(0.4) иногда
    # возвращается чуть раньше, и тест мигал без всякой вины фетчера.
    assert time.monotonic() - started >= 0.38


def test_sends_configured_user_agent(http_site):
    fetcher = HttpFetcher(
        base_url=http_site.base_url, delay_seconds=0, user_agent="listam-broker/0.1"
    )
    headers = json.loads(fetcher.get("/echo-headers"))
    assert headers["user-agent"] == "listam-broker/0.1"


# --- поведение, которое есть только у files ------------------------------


def test_files_fetcher_maps_url_path_to_filename():
    assert FilesFetcher.filename_for("/category/60/2") == "category-60-2.html"
    assert FilesFetcher.filename_for("https://www.list.am/ru/category/60") == "ru-category-60.html"


def test_files_fetcher_error_names_the_expected_filename(tmp_path):
    with pytest.raises(FetchError) as error:
        FilesFetcher(directory=tmp_path).get("/category/60")
    assert "category-60.html" in str(error.value)


# --- поведение, которое есть только у playwright ---------------------------


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright не установлен")
def test_playwright_runs_scripts_on_the_page(http_site):
    """Смысл браузера — страница доживает до состояния после JS."""
    http_site.route(
        "/js",
        "<html><body><div id=x>пусто</div>"
        "<script>document.getElementById('x').textContent='из скрипта'</script>"
        "</body></html>",
    )
    fetcher = PlaywrightFetcher(base_url=http_site.base_url, delay_seconds=0, retries=1)
    try:
        assert "из скрипта" in fetcher.get("/js")
    finally:
        fetcher.close()


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright не установлен")
def test_playwright_reports_cloudflare_challenge(http_site):
    """Проверку Cloudflare отличаем от страницы и говорим об этом человеку."""
    http_site.route(
        "/guard",
        "<html><head><title>Just a moment...</title></head>"
        "<body><script src='https://challenges.cloudflare.com/turnstile/v0/api.js'></script>"
        "</body></html>",
    )
    fetcher = PlaywrightFetcher(
        base_url=http_site.base_url, delay_seconds=0, retries=1, challenge_wait_seconds=2
    )
    try:
        with pytest.raises(FetchError) as error:
            fetcher.get("/guard")
        assert "Cloudflare" in str(error.value)
    finally:
        fetcher.close()


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright не установлен")
def test_playwright_hides_the_automation_flag():
    """Без этого флага Cloudflare режет всё, кроме первой страницы сессии."""
    from listam.adapters.fetcher_playwright import LAUNCH_ARGS

    assert "--disable-blink-features=AutomationControlled" in LAUNCH_ARGS


@pytest.mark.skipif(not HAS_PLAYWRIGHT, reason="playwright не установлен")
def test_playwright_wipes_a_rotten_profile_once(tmp_path):
    """Протухший профиль сносим — но ровно один раз за прогон, не по кругу."""
    profile = tmp_path / "browser"
    profile.mkdir()
    (profile / "Cookies").write_text("протухло", encoding="utf-8")
    fetcher = PlaywrightFetcher(user_data_dir=profile, delay_seconds=0)

    assert fetcher._reset_profile() is True
    assert not profile.exists()
    assert fetcher._reset_profile() is False


def test_fetcher_without_profile_does_not_wipe_anything(tmp_path):
    assert PlaywrightFetcher(delay_seconds=0)._reset_profile() is False
