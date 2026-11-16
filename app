#!/usr/bin/env python3
"""local-uptime: a small single-round uptime monitor for loopback endpoints.

Spec v0.1.4 / product v0.1.0. Standard library only.
"""

from __future__ import annotations

import argparse
import http.client
import io
import json
import os
import re
import sys
import tempfile
import time
from functools import partial

PRODUCT = "local-uptime"
VERSION = "0.1.0"
MAX_AT = 9007199254740991
MAX_ROUNDS = 1000
MAX_ITEMS = 100
MAX_BODY = 65536
TIMEOUT_SECONDS = 0.5

ID_RE = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
DECIMAL_RE = re.compile(r"[0-9]+\Z")


class ValidationError(Exception):
    """Invalid user input (exit code 2)."""


class StateError(Exception):
    """Corrupt or unreadable state (exit code 3)."""


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def strict_json_loads(data: bytes):
    """Decode UTF-8 JSON, rejecting BOMs, duplicate keys and NaN/Infinity."""
    text = data.decode("utf-8")
    if text.startswith("\ufeff"):
        raise ValueError("BOM is not allowed")
    return json.loads(
        text,
        object_pairs_hook=_no_duplicate_keys,
        parse_constant=_reject_constant,
    )


def emit_json(value) -> None:
    sys.stdout.write(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    )
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_valid_id(value) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def is_valid_port(value) -> bool:
    return is_int(value) and 1 <= value <= 65535


def is_valid_path(value) -> bool:
    if not isinstance(value, str):
        return False
    if not 1 <= len(value) <= 256:
        return False
    if any(ord(char) > 127 for char in value):
        return False
    if not value.startswith("/") or value.startswith("//"):
        return False
    for char in value:
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            return False
        if char in " #\\":
            return False
    return True


def has_exact_keys(value, keys) -> bool:
    return isinstance(value, dict) and set(value.keys()) == set(keys)


def validate_targets(document) -> list[dict]:
    if not isinstance(document, list):
        raise ValidationError("INPUT must be a JSON array")
    if not 0 <= len(document) <= MAX_ITEMS:
        raise ValidationError(f"INPUT must contain at most {MAX_ITEMS} objects")
    seen = set()
    targets = []
    for item in document:
        if not has_exact_keys(item, ("id", "port", "path")):
            raise ValidationError("each INPUT object must have exactly id, port and path")
        item_id = item["id"]
        if not is_valid_id(item_id):
            raise ValidationError("invalid id")
        if item_id in seen:
            raise ValidationError("duplicate id")
        if not is_valid_port(item["port"]):
            raise ValidationError("invalid port")
        if not is_valid_path(item["path"]):
            raise ValidationError("invalid path")
        seen.add(item_id)
        targets.append({"id": item_id, "port": item["port"], "path": item["path"]})
    return targets


def validate_result(value) -> None:
    if not has_exact_keys(value, ("id", "status", "up")):
        raise StateError("invalid result object")
    if not is_valid_id(value["id"]):
        raise StateError("invalid result id")
    status = value["status"]
    if status is not None:
        # http.client accepts any three-digit status code.
        if not is_int(status) or not 100 <= status <= 999:
            raise StateError("invalid result status")
    if not isinstance(value["up"], bool):
        raise StateError("invalid result up")


def validate_state(document) -> dict:
    if not has_exact_keys(document, ("runs",)):
        raise StateError("state must be an object with exactly a runs list")
    runs = document["runs"]
    if not isinstance(runs, list):
        raise StateError("runs must be a list")
    if len(runs) > MAX_ROUNDS:
        raise StateError("too many runs in state")
    previous_at = None
    for run in runs:
        if not has_exact_keys(run, ("at", "results")):
            raise StateError("invalid run object")
        at = run["at"]
        if not is_int(at) or not 0 <= at <= MAX_AT:
            raise StateError("invalid run at")
        if previous_at is not None and at <= previous_at:
            raise StateError("runs are not in strictly ascending at order")
        previous_at = at
        results = run["results"]
        if not isinstance(results, list) or len(results) > MAX_ITEMS:
            raise StateError("invalid run results")
        seen = set()
        for result in results:
            validate_result(result)
            if result["id"] in seen:
                raise StateError("duplicate result id in run")
            seen.add(result["id"])
    return {"runs": runs}


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def read_input_file(path: str) -> list[dict]:
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        raise
    try:
        document = strict_json_loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(f"invalid INPUT JSON: {exc}") from None
    return validate_targets(document)


def _state_parent(path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    return parent if parent else os.curdir


def load_state(path: str, allow_missing: bool) -> dict:
    parent = _state_parent(path)
    if not os.path.isdir(parent):
        raise OSError(f"state directory does not exist: {parent}")
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        if allow_missing:
            return {"runs": []}
        raise OSError(f"state file not found: {path}") from None
    except OSError as exc:
        raise OSError(str(exc)) from None
    try:
        document = strict_json_loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise StateError(f"corrupt state: {exc}") from None
    return validate_state(document)


def write_state(path: str, state: dict) -> None:
    parent = _state_parent(path)
    if not os.path.isdir(parent):
        raise OSError(f"state directory does not exist: {parent}")
    payload = (
        json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")
    descriptor = None
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".local-uptime-", suffix=".tmp", dir=parent)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# HTTP probing
# ---------------------------------------------------------------------------


class DeadlineReader(io.RawIOBase):
    """Apply the same deadline before every socket read, even inside readline."""

    def __init__(self, raw, sock, deadline):
        self.raw = raw
        self.sock = sock
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("probe deadline exceeded")
        self.sock.settimeout(remaining)
        return self.raw.readinto(buffer)

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class DeadlineResponse(http.client.HTTPResponse):
    def __init__(self, sock, *, deadline, **kwargs):
        super().__init__(sock, **kwargs)
        # Keep SocketIO's ownership of the socket when HTTPConnection detaches it.
        self.fp = io.BufferedReader(DeadlineReader(self.fp.detach(), sock, deadline))


def probe(port: int, path: str) -> tuple[int | None, bool]:
    """Perform one GET request; return (status, up) with failures as (None, False)."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_SECONDS)
    connection.response_class = partial(DeadlineResponse, deadline=deadline)
    try:
        connection.connect()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, False
        connection.sock.settimeout(remaining)
        connection.request(
            "GET",
            path,
            headers={"Host": f"127.0.0.1:{port}", "Connection": "close"},
        )
        with connection.getresponse() as response:
            body = response.read(MAX_BODY + 1)
            if (
                len(body) > MAX_BODY
                or response.length not in (None, 0)
                or time.monotonic() >= deadline
            ):
                return None, False
        status = response.status
        return status, 200 <= status <= 299
    except Exception:
        return None, False
    finally:
        try:
            connection.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def command_check(args) -> None:
    try:
        targets = read_input_file(args.input)
    except ValidationError as exc:
        raise SystemExit(_fail(str(exc), 2)) from None

    try:
        state = load_state(args.state, allow_missing=True)
    except (OSError, StateError) as exc:
        raise SystemExit(_fail(str(exc), 3)) from None

    runs = state["runs"]
    if len(runs) >= MAX_ROUNDS:
        raise SystemExit(_fail(f"state already holds {MAX_ROUNDS} runs", 2))

    at = args.at
    if runs:
        last_at = runs[-1]["at"]
        if at <= last_at:
            raise SystemExit(_fail("--at must be strictly greater than the last run", 2))
    elif at < 0:
        raise SystemExit(_fail("--at out of range", 2))

    results = []
    for target in targets:
        status, up = probe(target["port"], target["path"])
        results.append({"id": target["id"], "status": status, "up": up})
    results.sort(key=lambda result: result["id"])

    round_data = {"at": at, "results": results}
    new_state = {"runs": runs + [round_data]}

    try:
        write_state(args.state, new_state)
    except OSError as exc:
        raise SystemExit(_fail(str(exc), 3)) from None

    emit_json(round_data)


def summarize(runs: list[dict]) -> list[dict]:
    counters: dict[str, list[int]] = {}
    for run in runs:
        for result in run["results"]:
            counter = counters.setdefault(result["id"], [0, 0])
            counter[0] += 1
            if result["up"]:
                counter[1] += 1
    summary = []
    for item_id in sorted(counters):
        checks, up = counters[item_id]
        summary.append({"id": item_id, "checks": checks, "up": up, "down": checks - up})
    return summary


def command_report(args) -> None:
    try:
        state = load_state(args.state, allow_missing=False)
    except (OSError, StateError) as exc:
        raise SystemExit(_fail(str(exc), 3)) from None

    runs = state["runs"]
    emit_json({"runs": runs, "summary": summarize(runs)})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):  # noqa: D401 - argparse hook
        self.exit(2, f"error: {message}\n")


def parse_at(value: str) -> int:
    if DECIMAL_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    number = int(value)
    if number > MAX_AT:
        raise argparse.ArgumentTypeError(f"must be at most {MAX_AT}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(
        prog="app",
        description=(
            "Local uptime monitor. Checks loopback HTTP endpoints and keeps a local JSON history."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PRODUCT} {VERSION}")

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="{check,report}")

    check = subparsers.add_parser(
        "check",
        help="check INPUT --state STATE --at MS",
        description="Run one round of HTTP checks and append it to the state file.",
    )
    check.add_argument(
        "input",
        metavar="INPUT",
        help='JSON array of targets: [{"id":"api","port":9000,"path":"/health"}]',
    )
    check.add_argument("--state", metavar="STATE", required=True, help="path to state JSON")
    check.add_argument("--at", metavar="MS", required=True, type=parse_at, help="round time in ms")
    check.set_defaults(func=command_check)

    report = subparsers.add_parser(
        "report",
        help="report --state STATE",
        description="Print stored runs and an aggregate summary (no network access).",
    )
    report.add_argument("--state", metavar="STATE", required=True, help="path to state JSON")
    report.set_defaults(func=command_report)

    return parser


def _fail(message: str, code: int) -> int:
    # Errors are required to be a single line, whatever the source message held.
    single_line = str(message).replace("\r", " ").replace("\n", " ")
    sys.stderr.write(f"error: {single_line}\n")
    sys.stderr.flush()
    return code


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        raise
    except ValidationError as exc:
        return _fail(str(exc), 2)
    except (OSError, StateError) as exc:
        return _fail(str(exc), 3)
    except Exception as exc:  # never leak a traceback
        return _fail(str(exc), 3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
