"""AC-4: truncated responses and a deadline across all HTTP reads."""

import socket
import threading
import time
from contextlib import contextmanager

import pytest
from helpers import read_json, run_app, single_line_json, target, write_json


@contextmanager
def raw_server(parts):
    """Send (delay, bytes) pairs on an isolated loopback connection."""
    stop = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(3)

        def serve():
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(3)
                    connection.recv(4096)
                    for delay, data in parts:
                        if stop.wait(delay):
                            break
                        connection.sendall(data)
            except OSError:
                pass  # The probe closes the connection at its deadline.

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            yield listener.getsockname()[1]
        finally:
            stop.set()
            worker.join(timeout=4)
            assert not worker.is_alive()


def check_failure(tmp_path, parts, *, deadline=False):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    with raw_server(parts) as port:
        write_json(input_path, [target("api", port, "/")])
        started = time.monotonic()
        result = run_app("check", input_path, "--state", state_path, "--at", 0)
        elapsed = time.monotonic() - started
    assert result.returncode == 0
    assert result.stderr == ""
    if deadline:
        # Includes interpreter startup and state persistence, with scheduling margin.
        assert elapsed < 1.0, f"probe exceeded its 500 ms deadline: {elapsed:.3f}s"
    output = single_line_json(result.stdout)
    assert output == {"at": 0, "results": [{"id": "api", "status": None, "up": False}]}
    assert read_json(state_path) == {"runs": [output]}


def test_ac4_truncated_content_length_is_down(tmp_path):
    check_failure(
        tmp_path,
        [(0, b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\nx")],
    )


@pytest.mark.parametrize("phase", ["headers", "body", "combined"])
def test_ac4_dripping_peer_obeys_whole_probe_deadline(tmp_path, phase):
    headers = b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n"
    if phase == "headers":
        parts = [(0, headers)] + [(0.15, b"X-Slow: x\r\n")] * 10 + [(0, b"\r\n" + b"x" * 12)]
    elif phase == "body":
        parts = [(0, headers + b"\r\n")] + [(0.15, b"x")] * 12
    else:
        parts = [(0.3, headers + b"\r\n"), (0.3, b"x" * 12)]
    check_failure(tmp_path, parts, deadline=True)
