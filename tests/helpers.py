"""Helpers shared by the black-box tests (CLI invocation is always subprocess-based)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def run_app(*args, cwd=None, env_extra=None):
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(APP), *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env=env,
        timeout=120,
    )


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def single_line_json(stdout: str):
    assert stdout.endswith("\n"), "successful output must end with a newline"
    assert stdout.count("\n") == 1, f"expected exactly one line, got {stdout!r}"
    return json.loads(stdout)


def assert_cli_error(result, code: int) -> None:
    assert result.returncode == code, result.stderr
    assert result.stdout == ""
    assert result.stderr.startswith("error:")
    assert result.stderr.endswith("\n")
    assert result.stderr.count("\n") == 1, f"expected one error line, got {result.stderr!r}"
    assert "Traceback" not in result.stderr


def closed_port() -> int:
    """Return a loopback port that was just released, so connections fail."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def target(item_id: str, port: int, path: str) -> dict:
    return {"id": item_id, "port": port, "path": path}
