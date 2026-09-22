"""Общие фикстуры: локальный HTTP-сервер вместо похода в интернет."""
from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from listam.config import Config


@pytest.fixture
def runner_config(tmp_path) -> Config:
    """Конфиг для каркаса оркестрации: хранилище и база, больше ничего.

    Объявлений и заявок здесь нет нарочно: каркас про них не знает — он про
    замок, копию из хранилища, миграции и заливку.
    """
    return Config(
        {
            "env": "test",
            "storage": {
                "kind": "local",
                "directory": str(tmp_path / "remote"),
                "work_dir": str(tmp_path / "work"),
                "db_filename": "listam.sqlite",
            },
        },
        env="test",
        path=Path("config/test.yaml"),
    )


class _Handler(http.server.BaseHTTPRequestHandler):
    routes: dict[str, tuple[int, str]] = {}

    def do_GET(self):  # noqa: N802
        if self.path == "/echo-headers":
            body = json.dumps({k.lower(): v for k, v in self.headers.items()})
            self._send(200, body)
            return
        status, body = self.routes.get(self.path, (404, "not found"))
        self._send(status, body)

    def _send(self, status: int, body: str):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # тишина в выводе тестов
        pass


@pytest.fixture
def http_site():
    """Поднимает локальный сайт; routes можно наполнять из теста."""
    _Handler.routes = {}
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class Site:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"

        def route(self, path: str, body: str, status: int = 200):
            _Handler.routes[path] = (status, body)

    try:
        yield Site()
    finally:
        server.shutdown()
        server.server_close()
