# 24 — local-uptime

Spec v0.1.4 — product v0.1.0 — 13/11/2026

> Copy of the issue #1 description kept in-repo so the recorded spec hash is
> verifiable. The text below is the specification; the SHA-256 of this file is
> recorded in `RUNS.md`.

## 1. Objective and scope

Original idea #24: **Uptime Monitoring SaaS**. Difficulty: medium. Primary
interface: CLI.

Single-round monitor with loopback endpoints and local JSON history. No SaaS,
scheduler, notifications or Internet.

## 2. Platform

Declared target **POSIX: Ubuntu + macOS**; CI ubuntu-latest and macos-latest.
Native Windows is explicitly outside the scope of this version.
Runtime: Python 3.12. Test data is entirely fictitious; offline execution.
Standard library only at runtime.
Main entrypoint: `app`. `python app --version` returns **exactly**
`local-uptime 0.1.0\n` on stdout, exit 0 and empty stderr. `python app --help`
returns exit 0, empty stderr, stdout **contains** `usage:`, `app`, `--help`,
`--version` and all commands/options documented in this spec. These two
options work alone, without configuration, DB, FILE or a server. Neither the
full help text nor its line width is prescribed. Spec version 0.1.3 and
product version 0.1.0 are distinct. Unknown argument/missing required argument
→ exit 2.

### Observable conventions

- UTF-8 text and JSON files without BOM; textual stdout uses LF. JSON data on
  one line followed by LF; key order and Unicode escapes are not criteria,
  except for --version output. Arrays follow the specified ordering.
  NaN/Infinity, duplicate keys and extra fields in input objects are prohibited.
  Strings/types have the case-specific limits; bool never substitutes for an
  integer.
- CLI: on success, stdout contains only data and stderr is empty. Errors: empty
  stdout, stderr starts with `error:`, one line without traceback,
  case-specific exit code; messages need not be exact. I/O=exit 3 (except
  server: failed bind exit 1). Validation precedes mutations and does not echo
  sensitive content.
- HTTP: bind 127.0.0.1, --port integer 0..65535, zero selects an ephemeral port;
  startup stdout is exactly one JSON object `{"port":PORTA_REAL}` + LF after
  becoming ready. No other logs on stdout. JSON response with Content-Type
  application/json (charset=utf-8 optional). Server must be ready in 5 s;
  occupied port terminates with exit 1. Stopping with process terminate/kill
  does not require a shutdown endpoint. Do not accept a body in GET; GET/HEAD
  tests send an empty body. For HEAD the entire body is empty. HTTP rules apply
  only to HTTP cases; a lib has no implicit server.
- Files: complete reading/validation before writing. For persisted JSON, a
  temporary file **in the same directory**, flush, fsync, close and
  os.replace; failure before replacement preserves original bytes and removes
  the temporary file when possible. Do not open the destination in truncation
  mode. SQLite/DuckDB use complete durable transactions instead of this JSON
  file protocol. Do not promise resistance to physical disk failure or
  simultaneous writers where the case declares a single writer. Inputs are
  regular files; symlinks and special devices are outside the contract. Missing
  parent → I/O error.
- No access to credentials, accounts, paid APIs, external telemetry or a shell
  for interpolating data. Unicode paths/paths with spaces must work. Input
  files are not modified, except for explicitly identified state.

## 3. Commands and format

`python app check INPUT --state STATE --at MS`: INPUT is a JSON array (0..100)
of exact objects `{"id":"api","port":9000,"path":"/health"}`. Unique IDs,
ASCII `[a-z][a-z0-9-]{0,31}`; integer port 1..65535; ASCII path starts with `/`,
1..256 characters, without controls, space, `#`, `\` or a `//` prefix. Only
HTTP to 127.0.0.1; redirects are not followed. MS integer 0..9007199254740991.

`python app report --state STATE`: no network access; prints history.
Missing state on the first check is equivalent to `{"runs":[]}`. Existing state
must have this schema, including types/limits of the objects described below;
invalid JSON must not be overwritten. report with missing state is an I/O error.
Each check appends a round. Limit of 1000 rounds: the 1001st is a validation
error, without network access or changing bytes. --at must be strictly greater
than the last at (empty state accepts zero).

## 4. Semantics

check output: `{"at":MS,"results":[{"id":"api","status":200,"up":true}]}`.
Results sorted by ascending ASCII id. HTTP 200..299 → up=true; other statuses
→ false, preserving status. Timeout of 500 ms/connection refused/protocol error
→ status=null, up=false. Limit reading to 65536 bytes; exceeding also becomes
status=null/up=false. No latency is measured or required. HEADERS ignored.

Persist exactly the structure `{"runs":[RODADA,...]}` in --at order; check
prints RODADA only after commit. report returns `{"runs":[...],"summary":[...]}`;
summary sorted by id, only IDs ever seen, each item
`{"id":"api","checks":N,"up":U,"down":D}` with D=N-U. IDs absent from a round
do not count as failures. Exact integers; no floating-point percentages.

Invalid input is exit 2; I/O or corrupt state is exit 3. HTTP failure is data,
check returns 0 even if everything is down. One writer per file, no daemon.

## 5. Acceptance criteria and edge cases

| ID | Black-box test and result |
|---|---|
| AC-1 | HTTP 200 → status=200/up=true; check persists a round. |
| AC-2 | HTTP 503 → status=503/up=false, exit 0. |
| AC-3 | 302 is not followed; preserves status=302/up=false. |
| AC-4 | Closed port/timeout/excessive body → null/false, without traceback. |
| AC-5 | Two out-of-order IDs are stored sorted; summary too. |
| AC-6 | Two rounds, one up and one down → checks=2, up=1, down=1. |
| AC-7 | ID not included in the second round retains checks=1. |
| AC-8 | Duplicate IDs, port=true or path //host → exit 2, without requests. |
| AC-9 | Repeated/regressing/out-of-range --at → exit 2, state intact. |
| AC-10 | Corrupt state → exit 3, original bytes preserved. |
| AC-11 | Empty INPUT persists results=[]; empty summary on the first round. |
| AC-12 | Write/replacement failure → exit 3, no partial round or stdout. |
| AC-13 | report opens no connection; report without state → exit 3. |
| AC-14 | 1000 rounds valid; next rejected before HTTP access. |

## 6. Limitations

Sequential local monitor, without credentials or concurrent persistence. Time is
an explicit argument; tests do not require operating-system latency precision.

## 7. Portfolio requirements and delivery contract (v0.1.3)

1. README in English: problem and scope, copyable quickstart, real inputs/outputs,
   API/CLI, limits, platform, test/lint commands and decisions.
   Include at least one Mermaid flow/architecture block.
2. `How this was built`: record authorship and observed model when known;
   implementation by an AI agent, black-box tests by another independent AI agent
   based on the spec. Do not claim human authorship, model-family independence
   or absence of human editing without evidence. The maintainer records actual
   corrections and human participation. Spec, tests and implementation
   are distinct stages; do not invent future results.
3. Public tests in tests/, traceable to ACs, with one documented command.
   CLI via subprocess; HTTP via loopback/ephemeral port; lib only through the
   described public API. Do not import private modules. A write-failure fixture
   must be portable (isolated subprocess with externally simulated FS failure
   or a directory without permission with a justified skip); injection must not
   require a hidden test dependent on an internal function name.
4. Runtime manifest, complete MIT LICENSE (2026), CHANGELOG and .gitignore.
   Linters/formatters and test dependencies pinned in the manifest; no installing
   dependencies during tests. Without runtime dependencies, do not create setup.
   With dependencies, executable `./setup` with shebang and `python setup`
   must be idempotent, installing into .venv; the main entrypoint uses the prepared environment.
   Network only during preparation, on registries authorized by the harness.
5. CI on push/pull_request, lint and tests on ubuntu-latest + macos-latest; Python 3.12 and PYTHONUTF8=1.
   The completion gate is green CI on the declared targets. A local Linux result
   does not prove Windows/macOS. Use sys.executable in Python tests;
   portable terminate/kill, directory/DB/port per test, no global state.
6. POSIX: executable `./app` with shebang and `python app` are equivalent.
   Only in cases whose §2 requires Windows+Ubuntu: also `app.cmd`, propagating exit code and arguments with
   spaces; `python app` must work without Bash. Explicit UTF-8 encoding;
   binary fixtures must not undergo line-ending conversion. Libraries also
   have the described CLI, with version/help observable through the entrypoint.
7. Tag and Release v0.1.0 are the maintainer's responsibility **after the merge**;
   implementation creates no tag, account, repo, publication or external submission. Optional GIF.
8. Hidden tests external to the clone, authored by a model family different from
   the implementer's, spec×test audit **before implementing**. This spec does not
   include hidden tests or permit reading them for implementation.
9. Record per run: ID/spec hash/judge hash, stage, requested and observed model,
   tokens/cost and source (unknown=null), turns and correction rounds,
   stop reason, human interventions, verdicts and external verification source.
   Separate raw results from results after judge correction, without adding duplicate
   snapshots. The step's budget cap does not authorize spending during this preparation.

## 8. Normative audit resolutions (v0.1.4)

These decisions make the contract explicit; they supersede interpretations in
the test notes. MiB means 1024*1024 bytes; limits are inclusive. Described
objects are exact.

No AMBIGUIDADES file in this case's source. State must contain a runs list; each
round requires integer at and a results list, as per §§3–4.
