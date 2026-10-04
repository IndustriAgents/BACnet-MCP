# Contributing to BACnet MCP

Thanks for taking an interest. This repository holds an MCP server for
BACnet/IP building automation (`bacnet-python`) and a mock device to develop
against (`bacnet-mock-device`).

## Where changes go

This server is also shipped inside the
[IndustriConnect](https://github.com/IndustriAgents/IndustriConnect) suite,
where this repository is checked out as the `BACnet-Project` git submodule.
**Changes land here, in IndustriAgents/BACnet-MCP — not in IndustriConnect.**
IndustriConnect only pins a commit of this repository's `main` and bumps that
pointer automatically, so a pull request that edits `BACnet-Project/` over
there cannot be merged. Open it here instead.

## Repository layout

| Path | What it is |
|------|------------|
| `bacnet-python/` | The MCP server: a `uv` project, package `src/bacnet_mcp/`, console script `bacnet-mcp` (FastMCP + bacpypes3), tests in `tests/` |
| `bacnet-mock-device/` | A mock BACnet/IP device (bacpypes3) on UDP `127.0.0.1:47809`, console script `bacnet-mock-device`, tests in `tests/` |
| `.github/workflows/` | `ci.yml` (checks below) and `notify-industriconnect.yml` (tells the suite that `main` moved) |

Each directory is its own `uv` project with its own committed `uv.lock`, so
work inside the one you are changing.

## Getting set up

You need Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/IndustriAgents/BACnet-MCP.git
cd BACnet-MCP

# The mock device (first terminal): BACnet/IP device 12345 on UDP 127.0.0.1:47809
cd bacnet-mock-device
uv sync
uv run bacnet-mock-device            # --host/--port/--device-id to change it

# The MCP server (second terminal, from the repo root)
cd bacnet-python
uv sync
uv run bacnet-mcp                     # binds UDP 127.0.0.1:47808, writes refused
```

`bacnet-mcp` speaks MCP over **stdio**, so it waits for a client on stdin. To
drive it by hand, use the
[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) from the
repo root; `-e BACNET_WRITES_ENABLED=true` lets you try writes against the
mock:

```bash
npx @modelcontextprotocol/inspector -e BACNET_WRITES_ENABLED=true \
  uv run --directory bacnet-python bacnet-mcp
```

The Inspector needs Node.js: its current release (v2, npm `latest`) requires
Node.js 22.19+. On older Node (22.7.5+), run the v1 line with the same
arguments: `npx @modelcontextprotocol/inspector@v1-latest …`.

In the Inspector, `discover_devices` with `{"address": "127.0.0.1:47809"}`
finds the mock, and `read_property` / `write_property` then work with
`device_id` 12345.

The server joins BACnet/IP as a device of its own: it binds UDP `BACNET_PORT`
(default `47808`) on `BACNET_INTERFACE` (default `127.0.0.1`) and answers
Who-Is. Keep it on loopback while developing. It does not announce itself at
startup, and it refuses writes to its own objects.

## Checks

Both projects have a pytest suite and use ruff. CI
(`.github/workflows/ci.yml`) runs the following, and they should pass locally
before you open a pull request:

```bash
# MCP server
cd bacnet-python
uv sync --locked
uv run --locked python -m compileall -q src
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -v

# Mock device
cd ../bacnet-mock-device
uv sync --locked
uv run --locked python -m compileall -q bacnet_mock_device.py
uv run --locked bacnet-mock-device --help
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -v
```

The tests bind BACnet/IP only on free `127.0.0.1` UDP ports, so they do not
need (or reach) any network. The server suite includes an end-to-end test
that starts the mock and the server as separate processes and drives the
server over stdio with the mcp client SDK; it also asserts the exact set of
registered tools, so update it (and the tool table in README.md) when you add,
rename or remove a tool.

If you change dependencies, run `uv lock` in that project and commit the
updated `uv.lock`; CI uses `--locked` and fails when the lockfile is stale.
Dependency floors must be versions the tests pass on: CI's `lowest-direct`
job installs the lowest versions the direct dependencies allow and runs both
suites. To do the same locally (in either project):

```bash
uv venv --python 3.11 /path/to/scratch-venv
uv pip install --python /path/to/scratch-venv/bin/python --resolution lowest-direct -e . --group dev
/path/to/scratch-venv/bin/python -m pytest -q -p no:cacheprovider
```

Then drive the server the way a user would — through the Inspector or a real
MCP client — call the tool you changed, and check the envelope, not just the
value.

**Do not test against production equipment.** If a change genuinely cannot be
verified against the mock, say so in the pull request and describe the bench
you used.

## House rules for the code

These are shared with every server in the IndustriConnect suite:

- **One response envelope.** Every tool returns `{ success, data, error, meta }`.
- **Read-only by default.** Anything that writes to a device stays separate
  from the read tools and refuses to act unless the server was started with
  `BACNET_WRITES_ENABLED=true`; the default is `false`. A safety switch like
  this, and anything that decides where a write goes (`OBJECT_MAP_FILE`),
  comes only from the process environment or an explicit `--env-file`. The
  only implicit `.env` is `bacnet-python/.env`, never the working directory
  or a parent, and it may set only `BACNET_*` connection settings.
- **No duplicate writes.** A write reaches the device at most once: bacpypes3
  never retransmits (`numberOfApduRetries=0`) and only reads are retried, by
  the client itself. When a write's answer is lost, report
  `success: false`, `meta.outcome: "unknown"`, `meta.request_sent: true`
  ("may have been applied; read it back before retrying"). Write outcomes
  are `written`, `rejected` (only for a refusal that precedes execution),
  `unknown` or `not_sent` (including anything that fails before sending).
  Write tools are `destructiveHint: true, idempotentHint: false`.
- **Every request has a deadline.** No call may hang: a read gives up with an
  error, a write with outcome `unknown`.
- **Never report what did not happen.** A tool returns `success: true` only
  for data that came from a device, or a write the device acknowledged.
  Nothing returns placeholder or made-up values; what is not implemented
  says so in `error`.
- **`stdout` belongs to the protocol.** MCP runs over stdio, so all logging
  goes to `stderr`. A stray `print()` corrupts the session (the server
  points `sys.stdout` at stderr while it runs, but do not rely on that).
- **Errors are returned, not raised.** A failure — including a bad argument —
  is a `{ success: false, error }` the model can read, not an exception that
  kills the server. Note that bacpypes3 raises BACnet Error/Reject/Abort PDUs
  as `BaseException`, which `except Exception` does not catch.
- **Fail at startup**, not at the first tool call, when configuration is
  unusable. Configuration is read when the server starts, after `.env` is
  loaded, never at import time.
- **A mock for every tool.** If you add a tool, the mock should be able to
  answer it, or nobody can test it without a building.
- Every tool has a docstring: it is the description MCP clients show.
- Instance numbers, indexes and priorities are strict integers: a boolean or
  a numeric string is refused, never coerced.
- `serverInfo.version` is this package's version.
- A safety test must be able to fail: check that it fails when you remove
  the protection it covers.
- Keep tool and argument names aligned with the other IndustriConnect servers.

## Pull requests

- Branch off `main` and keep commits focused, with descriptive messages.
- Say which part is affected (server, mock, docs/CI), how you tested, and
  whether the change touches anything that can write to a device.
- Reference related issues (e.g. "Fixes #1").
- If a change was AI-assisted, keep the `Co-Authored-By:` trailer.

## Code of Conduct

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
