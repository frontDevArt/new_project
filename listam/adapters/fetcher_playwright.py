"""Реализация Fetcher поверх Playwright: страницы берёт настоящий браузер.

Зачем это нужно. Перед list.am стоит проверка Cloudflare: на обычный
HTTP-запрос сайт отвечает `403` с заголовком `Cf-Mitigated: challenge`.
Проверку проходит браузер, который умеет исполнить её скрипт.

Проверено 21.09.2026 на этой машине:

    обычный requests           → 403
    Playwright, headless       → 403  (и bundled chromium, и channel=chrome)
    Playwright, headed         → 200, 105 карточек на странице категории

То есть headless режим Cloudflare отличает и режет. Поэтому `headless` по
умолчанию False. На сервере без экрана браузер запускают под `xvfb-run`:

    xvfb-run -a python -m listam doctor

`user_data_dir` держит профиль браузера между запусками: полученная кука
`cf_clearance` переживает перезапуск, и проверку не приходится проходить
каждый раз заново.

Браузер поднимается лениво, на первом `get`, и живёт до `close()` — один
процесс на весь прогон, а не на страницу.
"""
from __future__ import annotations

import time
from pathlib import Path

from listam.ports.fetcher import FetchError, Fetcher

DEFAULT_USER_AGENT = None  # у настоящего браузера свой; подменять его — повод для подозрений

# По этим приметам видно, что вместо страницы отдали проверку Cloudflare.
# Скрипта `/cdn-cgi/challenge-platform/` здесь нет намеренно: Cloudflare
# подмешивает его и в обычную, успешно отданную страницу — проверено на
# живой `category/60`, которая приехала с ним и с 105 карточками.
CHALLENGE_MARKERS = (
    "challenges.cloudflare.com",
    "cf-browser-verification",
)
CHALLENGE_TITLES = ("just a moment", "один момент", "подождите", "attention required")

# Без этого флага Chromium сообщает странице, что им управляет автоматика, и
# Cloudflare отдаёт проверку на всё, кроме самой первой страницы в сессии —
# проверено 21.09.2026: без флага страницы 2 и 3 категории дали 403, с флагом
# все три подряд отдались с кодом 200.
LAUNCH_ARGS = ("--disable-blink-features=AutomationControlled",)

# Метка «этот каталог завёл адаптер». Без неё сносить профиль нельзя:
# `_reset_profile` делает rmtree по пути из конфига, и опечатка
# `profile_dir: ./data` унесла бы боевую базу.
PROFILE_MARKER = ".listam-browser-profile"


def _is_own_profile(path: Path) -> bool:
    """Наш ли это каталог. Своим считаем помеченный — и профиль Chromium по виду.

    Вторая проверка нужна для профилей, заведённых до появления метки: сносить
    их можно, а вот чужую папку с базой — нельзя ни при каких условиях.
    """
    if (path / PROFILE_MARKER).exists():
        return True
    return (path / "Local State").exists() and (path / "Default").is_dir()



class PlaywrightFetcher(Fetcher):
    def __init__(
        self,
        base_url: str | None = None,
        delay_seconds: float = 1.5,
        timeout: float = 45.0,
        retries: int = 3,
        headless: bool = False,
        channel: str | None = None,
        user_data_dir: str | Path | None = None,
        locale: str = "ru-RU",
        challenge_wait_seconds: float = 25.0,
        user_agent: str | None = DEFAULT_USER_AGENT,
    ):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.delay_seconds = float(delay_seconds)
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self.headless = bool(headless)
        self.channel = channel or None
        self.user_data_dir = Path(user_data_dir) if user_data_dir else None
        self.locale = locale
        self.challenge_wait_seconds = float(challenge_wait_seconds)
        self.user_agent = user_agent

        self._requests_made = 0
        self._last_request_at: float | None = None
        self._profile_reset_done = False
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # --- порт ---------------------------------------------------------

    @property
    def requests_made(self) -> int:
        return self._requests_made

    def get(self, url: str) -> str:
        target = self._url(url)
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._wait_turn()
            self._requests_made += 1
            try:
                return self._load(target)
            except FetchError as exc:
                if getattr(exc, "fatal", False):
                    raise
                last_error = exc
                if getattr(exc, "challenge", False) and self._reset_profile():
                    continue  # протухший профиль — не попытка, а повод начать с чистого
            except Exception as exc:  # падение браузера — поднимем заново
                last_error = exc
                self._drop_browser()
            finally:
                self._last_request_at = time.monotonic()
            if attempt < self.retries:
                time.sleep(min(30.0, self.delay_seconds * 2 * attempt) or 1.0)
        raise FetchError(f"{target} не отдался за {self.retries} попыток: {last_error}")

    def close(self) -> None:
        self._drop_browser()

    # --- внутренности ---------------------------------------------------

    def _url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if not self.base_url:
            raise FetchError(f"Относительный путь {url!r} без base_url")
        return f"{self.base_url}/{url.lstrip('/')}"

    def _wait_turn(self) -> None:
        if self._last_request_at is None or self.delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    def _page_ready(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError(
                "Не установлен playwright. Поставь: pip install -r requirements-playwright.txt "
                "и python -m playwright install chromium"
            ) from exc

        self._playwright = sync_playwright().start()
        launch: dict = {"headless": self.headless, "args": list(LAUNCH_ARGS)}
        if self.channel:
            launch["channel"] = self.channel
        context_options: dict = {"locale": self.locale, "viewport": {"width": 1366, "height": 900}}
        if self.user_agent:
            context_options["user_agent"] = self.user_agent
        try:
            if self.user_data_dir:
                self._context = self._playwright.chromium.launch_persistent_context(
                    str(self._ensure_profile_dir()), **launch, **context_options
                )
            else:
                self._browser = self._playwright.chromium.launch(**launch)
                self._context = self._browser.new_context(**context_options)
        except Exception as exc:
            self._drop_browser()
            raise FetchError(
                f"Браузер не запустился: {exc}. "
                f"Проверь: python -m playwright install chromium"
            ) from exc
        self._context.set_default_timeout(self.timeout * 1000)
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        return self._page

    def _load(self, target: str) -> str:
        page = self._page_ready()
        response = page.goto(target, wait_until="domcontentloaded", timeout=self.timeout * 1000)
        status = response.status if response else None

        deadline = time.monotonic() + self.challenge_wait_seconds
        html = page.content()
        while self._is_challenge(page, html, status) and time.monotonic() < deadline:
            page.wait_for_timeout(1000)
            html = page.content()
            status = None  # статус был у первого ответа; дальше судим по содержимому

        if self._is_challenge(page, html, status):
            raise self._challenge_error(target)
        if status is not None and status >= 400 and status != 429:
            error = FetchError(f"{target} → HTTP {status}")
            error.fatal = True
            raise error
        return html

    @staticmethod
    def _is_challenge(page, html: str, status: int | None) -> bool:
        if any(marker in html for marker in CHALLENGE_MARKERS):
            return True
        try:
            title = (page.title() or "").strip().lower()
        except Exception:
            title = ""
        if any(title.startswith(t) for t in CHALLENGE_TITLES):
            return True
        return status == 403

    def _reset_profile(self) -> bool:
        """Стирает профиль браузера и разрешает одну попытку с чистого листа.

        Профиль протухает: сохранённая кука `cf_clearance` перестаёт годиться,
        и Cloudflare начинает возвращать проверку на каждую страницу, хотя
        чистый браузер с того же адреса получает её без единого вопроса —
        проверено 21.09.2026. Сам себя такой профиль не чинит, поэтому сносим.
        """
        if not self.user_data_dir or self._profile_reset_done:
            return False
        self._profile_reset_done = True
        if not _is_own_profile(self.user_data_dir):
            return False      # чужая папка: путь в конфиге — опечатка, а не разрешение
        self._drop_browser()
        import shutil

        shutil.rmtree(self.user_data_dir, ignore_errors=True)
        return True

    def _ensure_profile_dir(self) -> Path:
        """Заводит каталог профиля и метит его своим — но только если он пуст.

        Непустой чужой каталог не метим: раз мы его не заводили, сносить его
        потом тоже не станем.
        """
        path = self.user_data_dir
        fresh = not path.exists() or not any(path.iterdir())
        path.mkdir(parents=True, exist_ok=True)
        if fresh:
            (path / PROFILE_MARKER).write_text(
                "Каталог завёл listam (адаптер playwright). Его можно удалять.",
                encoding="utf-8",
            )
        return path

    def _challenge_error(self, target: str) -> FetchError:
        hint = (
            " Браузер запущен в headless — Cloudflare его отличает и режет. "
            "Поставь scrape.headless: false (на сервере без экрана — xvfb-run)."
            if self.headless
            else " Помогает другой IP: из армянского интернета или с VPS."
        )
        error = FetchError(f"{target}: не прошли проверку Cloudflare за "
                           f"{self.challenge_wait_seconds:.0f} с.{hint}")
        error.challenge = True
        return error

    def _drop_browser(self) -> None:
        for obj in (self._context, self._browser, self._playwright):
            if obj is None:
                continue
            try:
                obj.stop() if hasattr(obj, "stop") else obj.close()
            except Exception:
                pass
        self._playwright = self._browser = self._context = self._page = None
