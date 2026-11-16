# Changelog

All notable changes to this project are documented here.

## [0.1.0] - 2026-11-13

### Added

- `check INPUT --state STATE --at MS`: one sequential round of HTTP GET probes
  against `127.0.0.1`, with 500 ms timeout, 65536-byte body limit, no redirect
  following, and atomic persistence of the round.
- `report --state STATE`: offline history and per-ID summary
  (`checks`, `up`, `down`).
- Strict validation of INPUT and state documents, including duplicate keys,
  `NaN`/`Infinity`, boolean-as-integer and extra-field rejection.
- Atomic state writes (same-directory temporary file, `flush`, `fsync`,
  `os.replace`) preserving previous bytes on failure.
- Public black-box acceptance tests traceable to the specification's AC-1 to
  AC-14.
- CI workflow running lint and tests on Ubuntu and macOS with Python 3.12.
