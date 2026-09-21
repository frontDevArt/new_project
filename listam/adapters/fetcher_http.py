"""Реализация Fetcher поверх requests: обычные HTTP-запросы, без браузера."""
from __future__ import annotations

import time

import requests

from listam.ports.fetcher import FetchError, Fetcher

DEFAULT_USER_AGENT = "listam-broker/0.1 (personal real-estate assistant)"


class HttpFetcher(Fetcher):
    def __init__(
        self,
        base_url: str | None = None,
        delay_seconds: float = 1.5,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 20.0,
        retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/") if base_url else None
        self.delay_seconds = float(delay_seconds)
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self._requests_made = 0
        self._last_request_at: float | None = None
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "ru,hy;q=0.8,en;q=0.6",
                "Accept": "text/html,application/xhtml+xml",
            }
        )

    @property
    def requests_made(self) -> int:
        return self._requests_made

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

    def get(self, url: str) -> str:
        target = self._url(url)
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._wait_turn()
            try:
                self._requests_made += 1
                response = self._session.get(target, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
            else:
                self._last_request_at = time.monotonic()
                if response.status_code == 200:
                    response.encoding = response.encoding or "utf-8"
                    return response.text
                if 400 <= response.status_code < 500 and response.status_code != 429:
                    raise FetchError(f"{target} → HTTP {response.status_code}")
                last_error = FetchError(f"{target} → HTTP {response.status_code}")
            self._last_request_at = time.monotonic()
            if attempt < self.retries:
                time.sleep(min(30.0, self.delay_seconds * 2 * attempt) or 1.0)
        raise FetchError(f"{target} не отдался за {self.retries} попыток: {last_error}")

    def close(self) -> None:
        self._session.close()
