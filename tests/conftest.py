"""Shared fixtures for the local-uptime black-box test suite."""

from __future__ import annotations

import http.server
import threading
import time

import pytest


class LocalHTTPServer:
    """Loopback test server with deterministic routes and a request log."""

    def __init__(self):
        self.requests: list[str] = []
        self._lock = threading.Lock()
        handler = self._make_handler()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.httpd.requests = self.requests
        self.httpd.lock = self._lock
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    @staticmethod
    def _make_handler():
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence test output
                pass

            def _send(self, status, body=b"", headers=None):
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                with self.server.lock:
                    self.server.requests.append(self.path)
                route = self.path
                if route == "/ok":
                    self._send(200, b"ok")
                elif route == "/down":
                    self._send(503, b"unavailable")
                elif route == "/redirect":
                    self._send(302, b"", {"Location": "/ok"})
                elif route == "/exact":
                    self._send(200, b"x" * 65536)
                elif route == "/big":
                    self._send(200, b"x" * 65537)
                elif route == "/slow":
                    time.sleep(1.2)
                    self._send(200, b"slow")
                elif route == "/closeproto":
                    self.close_connection = True
                    self.connection.close()
                elif route == "/unicode path/ü":
                    self._send(200, b"unicode")
                else:
                    self._send(404, b"missing")

        return Handler

    def count(self) -> int:
        with self._lock:
            return len(self.requests)

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def http_server():
    server = LocalHTTPServer()
    try:
        yield server
    finally:
        server.close()
