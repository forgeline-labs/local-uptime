"""Black-box acceptance tests for local-uptime v0.1.0 (spec v0.1.4).

Each test names the acceptance criterion it exercises. The CLI is always run
through a subprocess with sys.executable; HTTP servers listen on loopback with
an ephemeral port.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys

import pytest
from helpers import (
    APP,
    assert_cli_error,
    closed_port,
    read_json,
    run_app,
    single_line_json,
    target,
    write_json,
)

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX is the declared platform for v0.1.0"
)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_version_is_exact():
    result = run_app("--version")
    assert result.returncode == 0
    assert result.stdout == "local-uptime 0.1.0\n"
    assert result.stderr == ""


def test_direct_execution_matches_python_app():
    direct = subprocess.run(
        [str(APP), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert direct.returncode == 0
    assert direct.stdout == "local-uptime 0.1.0\n"
    assert direct.stderr == ""


def test_help_documents_the_interface():
    result = run_app("--help")
    assert result.returncode == 0
    assert result.stderr == ""
    tokens = (
        "usage:",
        "app",
        "--help",
        "--version",
        "check",
        "report",
        "INPUT",
        "--state",
        "--at",
    )
    for token in tokens:
        assert token in result.stdout, token


@pytest.mark.parametrize(
    "args",
    [
        ("--nope",),
        ("check",),
        ("check", "input.json"),
        ("report",),
        ("unknown-command",),
    ],
)
def test_cli_usage_errors_exit_2(args, tmp_path):
    result = run_app(*args, cwd=tmp_path)
    assert_cli_error(result, 2)


# ---------------------------------------------------------------------------
# AC-1 / AC-2 / AC-3 — status handling
# ---------------------------------------------------------------------------


def test_ac1_http_200_is_up_and_persists_round(tmp_path, http_server):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(input_path, [target("api", http_server.port, "/ok")])

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert result.returncode == 0
    assert result.stderr == ""
    output = single_line_json(result.stdout)
    assert output == {"at": 0, "results": [{"id": "api", "status": 200, "up": True}]}

    state = read_json(state_path)
    assert set(state) == {"runs"}
    assert state["runs"] == [output]


def test_ac2_http_503_is_down_and_exit_zero(tmp_path, http_server):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(input_path, [target("api", http_server.port, "/down")])

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert result.returncode == 0
    assert result.stderr == ""
    output = single_line_json(result.stdout)
    assert output["results"] == [{"id": "api", "status": 503, "up": False}]


def test_ac3_redirect_is_not_followed(tmp_path, http_server):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(input_path, [target("api", http_server.port, "/redirect")])

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert result.returncode == 0
    output = single_line_json(result.stdout)
    assert output["results"] == [{"id": "api", "status": 302, "up": False}]
    assert http_server.requests == ["/redirect"]


# ---------------------------------------------------------------------------
# AC-4 — failures are data, not errors
# ---------------------------------------------------------------------------


def test_ac4_network_failures_and_body_limit(tmp_path, http_server):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(
        input_path,
        [
            target("refused", closed_port(), "/"),
            target("slow", http_server.port, "/slow"),
            target("big", http_server.port, "/big"),
            target("proto", http_server.port, "/closeproto"),
        ],
    )

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert result.returncode == 0
    assert result.stderr == ""
    results = single_line_json(result.stdout)["results"]
    assert results == [
        {"id": "big", "status": None, "up": False},
        {"id": "proto", "status": None, "up": False},
        {"id": "refused", "status": None, "up": False},
        {"id": "slow", "status": None, "up": False},
    ]


def test_body_limit_is_inclusive_at_65536(tmp_path, http_server):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(input_path, [target("exact", http_server.port, "/exact")])

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert result.returncode == 0
    assert single_line_json(result.stdout)["results"] == [
        {"id": "exact", "status": 200, "up": True}
    ]


# ---------------------------------------------------------------------------
# AC-5 / AC-6 / AC-7 — ordering and summary
# ---------------------------------------------------------------------------


def test_ac5_results_and_summary_are_sorted_by_id(tmp_path, http_server):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(
        input_path,
        [
            target("zeta", http_server.port, "/ok"),
            target("alpha", http_server.port, "/down"),
        ],
    )

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert result.returncode == 0
    results = single_line_json(result.stdout)["results"]
    assert [item["id"] for item in results] == ["alpha", "zeta"]
    assert [item["id"] for item in read_json(state_path)["runs"][0]["results"]] == [
        "alpha",
        "zeta",
    ]

    report = run_app("report", "--state", state_path)
    assert report.returncode == 0
    summary = single_line_json(report.stdout)["summary"]
    assert [item["id"] for item in summary] == ["alpha", "zeta"]


def test_ac6_ac7_two_rounds_counts_are_exact(tmp_path, http_server):
    state_path = tmp_path / "state.json"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_json(
        first,
        [
            target("api", http_server.port, "/ok"),
            target("web", http_server.port, "/down"),
        ],
    )
    write_json(second, [target("api", http_server.port, "/down")])

    assert run_app("check", first, "--state", state_path, "--at", 0).returncode == 0
    assert run_app("check", second, "--state", state_path, "--at", 1).returncode == 0

    report = run_app("report", "--state", state_path)
    assert report.returncode == 0
    summary = single_line_json(report.stdout)["summary"]
    assert summary == [
        {"id": "api", "checks": 2, "up": 1, "down": 1},
        {"id": "web", "checks": 1, "up": 0, "down": 1},
    ]


# ---------------------------------------------------------------------------
# AC-8 — input validation happens before any request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        [target("api", 9000, "/a"), target("api", 9000, "/b")],
        [{"id": "api", "port": True, "path": "/a"}],
        [target("api", 9000, "//host")],
        [target("api", 9000, "/has space")],
        [target("api", 9000, "/back\\slash")],
        [{"id": "API", "port": 9000, "path": "/a"}],
        [{"id": "api", "port": 9000, "path": "/a", "extra": 1}],
        [{"id": "api", "port": 0, "path": "/a"}],
        [{"id": "api", "port": 65536, "path": "/a"}],
        [{"id": "api", "port": 1.5, "path": "/a"}],
        {"id": "api", "port": 9000, "path": "/a"},
        [target("a" * 33, 9000, "/a")],
        [target("api", 9000, "/" + "a" * 256)],
        [target(f"host-{index}", 9000, "/") for index in range(101)],
    ],
)
def test_ac8_invalid_input_exits_2_without_requests(document, tmp_path, http_server):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(input_path, document)
    http_server.reset()

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert_cli_error(result, 2)
    assert http_server.count() == 0
    assert not state_path.exists()


def test_invalid_json_input_exits_2_but_missing_file_exits_3(tmp_path, http_server):
    bad = tmp_path / "bad.json"
    bad.write_text("[{", encoding="utf-8")
    result = run_app("check", bad, "--state", tmp_path / "state.json", "--at", 0)
    assert_cli_error(result, 2)

    missing = tmp_path / "missing.json"
    result = run_app("check", missing, "--state", tmp_path / "state.json", "--at", 0)
    assert_cli_error(result, 3)


# ---------------------------------------------------------------------------
# AC-9 — monotonic --at and range
# ---------------------------------------------------------------------------


def test_ac9_bad_at_values_exit_2_and_preserve_state(tmp_path, http_server):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(input_path, [target("api", http_server.port, "/ok")])

    assert run_app("check", input_path, "--state", state_path, "--at", 100).returncode == 0
    before = state_path.read_bytes()

    for bad_at in (100, 99, 9007199254740992, -1, "1.5"):
        result = run_app("check", input_path, "--state", state_path, "--at", bad_at)
        assert_cli_error(result, 2)
        assert state_path.read_bytes() == before


# ---------------------------------------------------------------------------
# AC-10 — corrupt state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"{not json",
        b'{"runs": []} trailing',
        b'{"runs": "x"}',
        b'{"runs": [{"at": 1}]}',
        b'{"runs": [], "other": 1}',
        b'{"runs": [{"at": true, "results": []}]}',
        b'{"runs": [{"at": 0, "results": [{"id": "a", "status": "200", "up": true}]}]}',
        b'{"runs": [{"at": 1, "results": []}, {"at": 1, "results": []}]}',
        b'{"runs": [], "runs": []}',
        b'{"runs": [{"at": NaN, "results": []}]}',
        b"\xff\xfe",
    ],
)
def test_ac10_corrupt_state_exits_3_and_is_not_rewritten(payload, tmp_path):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(input_path, [])
    state_path.write_bytes(payload)

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert_cli_error(result, 3)
    assert state_path.read_bytes() == payload

    report = run_app("report", "--state", state_path)
    assert_cli_error(report, 3)
    assert state_path.read_bytes() == payload


def test_state_with_extra_result_field_is_corrupt(tmp_path):
    state_path = tmp_path / "state.json"
    input_path = tmp_path / "input.json"
    write_json(input_path, [])
    state_path.write_text(
        '{"runs":[{"at":0,"results":[{"id":"a","status":null,"up":false,"x":1}]}]}',
        encoding="utf-8",
    )
    before = state_path.read_bytes()
    result = run_app("check", input_path, "--state", state_path, "--at", 1)
    assert_cli_error(result, 3)
    assert state_path.read_bytes() == before


# ---------------------------------------------------------------------------
# AC-11 — empty input
# ---------------------------------------------------------------------------


def test_ac11_empty_input_persists_empty_results(tmp_path):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    write_json(input_path, [])

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert result.returncode == 0
    assert single_line_json(result.stdout) == {"at": 0, "results": []}
    assert read_json(state_path) == {"runs": [{"at": 0, "results": []}]}

    report = run_app("report", "--state", state_path)
    assert report.returncode == 0
    assert single_line_json(report.stdout) == {
        "runs": [{"at": 0, "results": []}],
        "summary": [],
    }


# ---------------------------------------------------------------------------
# AC-12 — write/replacement failure
# ---------------------------------------------------------------------------


def test_ac12_write_failure_exits_3_without_partial_round(tmp_path, http_server):
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("permission-based write failure cannot be simulated as root")
    if not hasattr(os, "chmod"):
        pytest.skip("POSIX permissions are required")

    readonly = tmp_path / "readonly"
    readonly.mkdir()
    state_path = readonly / "state.json"
    input_path = tmp_path / "input.json"
    write_json(input_path, [target("api", http_server.port, "/ok")])
    original = b'{"runs":[]}\n'
    state_path.write_bytes(original)

    readonly.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = run_app("check", input_path, "--state", state_path, "--at", 0)
        assert_cli_error(result, 3)
        assert state_path.read_bytes() == original
        assert list(readonly.iterdir()) == [state_path]
    finally:
        readonly.chmod(stat.S_IRWXU)


def test_ac12_write_failure_does_not_create_state(tmp_path, http_server):
    if not hasattr(os, "geteuid") or os.geteuid() == 0:
        pytest.skip("permission-based write failure cannot be simulated as root")

    readonly = tmp_path / "readonly"
    readonly.mkdir()
    state_path = readonly / "state.json"
    input_path = tmp_path / "input.json"
    write_json(input_path, [target("api", http_server.port, "/ok")])

    readonly.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        result = run_app("check", input_path, "--state", state_path, "--at", 0)
        assert_cli_error(result, 3)
        assert list(readonly.iterdir()) == []
    finally:
        readonly.chmod(stat.S_IRWXU)


# ---------------------------------------------------------------------------
# AC-13 — report is offline
# ---------------------------------------------------------------------------


def test_ac13_report_does_not_need_network_and_missing_state_is_3(tmp_path):
    state_path = tmp_path / "state.json"
    write_json(
        state_path,
        {
            "runs": [
                {
                    "at": 0,
                    "results": [
                        {"id": "api", "status": None, "up": False},
                    ],
                }
            ]
        },
    )
    report = run_app("report", "--state", state_path)
    assert report.returncode == 0
    body = single_line_json(report.stdout)
    assert body["summary"] == [{"id": "api", "checks": 1, "up": 0, "down": 1}]

    missing = run_app("report", "--state", tmp_path / "missing.json")
    assert_cli_error(missing, 3)


# ---------------------------------------------------------------------------
# AC-14 — round limit
# ---------------------------------------------------------------------------


def test_ac14_1000_rounds_valid_and_the_1001st_rejected_before_http(tmp_path, http_server):
    state_path = tmp_path / "state.json"
    input_path = tmp_path / "input.json"
    write_json(input_path, [target("api", http_server.port, "/ok")])

    runs = [{"at": index, "results": []} for index in range(999)]
    write_json(state_path, {"runs": runs})

    result = run_app("check", input_path, "--state", state_path, "--at", 999)
    assert result.returncode == 0
    assert len(read_json(state_path)["runs"]) == 1000
    http_server.reset()
    before = state_path.read_bytes()

    result = run_app("check", input_path, "--state", state_path, "--at", 1000)
    assert_cli_error(result, 2)
    assert state_path.read_bytes() == before
    assert http_server.count() == 0


# ---------------------------------------------------------------------------
# Filesystem conventions
# ---------------------------------------------------------------------------


def test_unicode_and_space_paths_work(tmp_path, http_server):
    directory = tmp_path / "dir with space" / "ünïcode"
    directory.mkdir(parents=True)
    input_path = directory / "in put.json"
    state_path = directory / "stäte.json"
    write_json(input_path, [target("api", http_server.port, "/ok")])

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert result.returncode == 0
    assert state_path.read_bytes().endswith(b"\n")
    assert read_json(state_path)["runs"][0]["at"] == 0


def test_missing_state_parent_is_io_error(tmp_path, http_server):
    input_path = tmp_path / "input.json"
    write_json(input_path, [target("api", http_server.port, "/ok")])
    state_path = tmp_path / "missing-dir" / "state.json"

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert_cli_error(result, 3)
    assert not state_path.exists()


def test_state_file_is_not_modified_if_input_is_invalid(tmp_path):
    input_path = tmp_path / "input.json"
    state_path = tmp_path / "state.json"
    input_path.write_text("not json", encoding="utf-8")
    original = b'{"runs":[]}\n'
    state_path.write_bytes(original)

    result = run_app("check", input_path, "--state", state_path, "--at", 0)
    assert_cli_error(result, 2)
    assert state_path.read_bytes() == original


def test_app_is_executable_with_shebang():
    from helpers import APP

    assert APP.read_bytes().startswith(b"#!")
    assert os.access(APP, os.X_OK)
