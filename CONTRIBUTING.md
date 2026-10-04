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
| `bacnet-python/` | The MCP server: a `uv` project, package `src/bacnet_mcp/`, console script `bacnet-mcp` (FastMCP + BAC0) |
| `bacnet-mock-device/` | A mock device answering newline-delimited JSON over TCP on `127.0.0.1:7900`, console script `bacnet-mock-device` |
| `.github/workflows/` | `ci.yml` (checks below) and `notify-industriconnect.yml` (tells the suite that `main` moved) |

Each directory is its own `uv` project with its own committed `uv.lock`, so
work inside the one you are changing.

## Getting set up

You need Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/IndustriAgents/BACnet-MCP.git
cd BACnet-MCP

# The mock device (first terminal); the server does not connect to it yet
cd bacnet-mock-device
uv sync
uv run bacnet-mock-device            # --host/--port to move it off 127.0.0.1:7900

# The MCP server (second terminal, from the repo root)
cd bacnet-python
uv sync
BACNET_INTERFACE=127.0.0.1 BACNET_WRITES_ENABLED=false uv run bacnet-mcp
```

`bacnet-mcp` speaks MCP over **stdio**, so it waits for a client on stdin. To
drive it by hand, use the
[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) from the
repo root:

```bash
npx @modelcontextprotocol/inspector -e BACNET_INTERFACE=127.0.0.1 -e BACNET_WRITES_ENABLED=false \
  uv run --directory bacnet-python bacnet-mcp
```

The Inspector needs Node.js: its current release (v2, npm `latest`) requires
Node.js 22.19+. On older Node (22.7.5+), run the v1 line with the same
arguments: `npx @modelcontextprotocol/inspector@v1-latest …`.

Note that the server starts a BAC0 (BACnet/IP) stack on startup, which binds
UDP `BACNET_PORT` (default `47808`) on `BACNET_INTERFACE` (default `0.0.0.0`,
every interface) and broadcasts an I-Am, hence `127.0.0.1` above. On macOS that
logs a harmless `[Errno 49]` traceback for the `127.0.0.255` broadcast address.
Set these variables in the environment, not in a `.env` file: the interface,
port and device instance are read before `.env` is loaded.

## Checks

There is no automated test suite yet. CI (`.github/workflows/ci.yml`) runs the
following, and they should pass locally before you open a pull request:

```bash
# MCP server
cd bacnet-python
uv sync --locked
uv run --locked python -m compileall -q src
# bacnet_client swallows a failed `import BAC0`, so check the stack directly
uv run --locked python -c "import BAC0; from bacnet_mcp import bacnet_client; assert bacnet_client.BAC0 is not None"
uv run --locked python -c "import asyncio; from bacnet_mcp.server import BACnetMCPServer; names = sorted(t.name for t in asyncio.run(BACnetMCPServer()._server.list_tools())); print(names); assert names == ['discover_devices', 'list_objects', 'ping', 'read_object_by_alias', 'read_property', 'write_object_by_alias', 'write_property'], names"

# Mock device
cd ../bacnet-mock-device
uv sync --locked
uv run --locked python -m compileall -q bacnet_mock_device.py
uv run --locked bacnet-mock-device --help
```

If you change dependencies, run `uv lock` in that project and commit the
updated `uv.lock`; CI uses `--locked` and fails when the lockfile is stale.
CI also checks the exact set of registered tools, so update that list in
`ci.yml` (and above) when you add, rename or remove a tool.

Then drive the server the way a user would — through the Inspector or a real
MCP client — call the tool you changed, and check the envelope, not just the
value.

**Do not test against production equipment.** If a change genuinely cannot be
verified against the mock, say so in the pull request and describe the bench
you used.

## House rules for the code

These are shared with every server in the IndustriConnect suite:

- **One response envelope.** Every tool returns `{ success, data, error, meta }`.
- **`stdout` belongs to the protocol.** MCP runs over stdio, so all logging
  goes to `stderr`. A stray `print()` corrupts the session.
- **Gate writes.** Anything that writes to a device must honour
  `BACNET_WRITES_ENABLED` and stay separate from the read tools.
- **Errors are returned, not raised.** A failure is a
  `{ success: false, error }` the model can read, not an exception that kills
  the server.
- **Fail at startup**, not at the first tool call, when configuration is
  unusable.
- **A mock for every tool.** If you add a tool, the mock should be able to
  answer it, or nobody can test it without a building.
- Keep tool and argument names aligned with the other IndustriConnect servers.

## Pull requests

- Branch off `main` and keep commits focused, with descriptive messages.
- Say which part is affected (server, mock, docs/CI), how you tested, and
  whether the change touches anything that can write to a device.
- Reference related issues (e.g. "Fixes #1").
- If a change was AI-assisted, keep the `Co-Authored-By:` trailer.

## Code of Conduct

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
