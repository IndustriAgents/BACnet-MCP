> **0.3.0 package and tool migration:** Install `industriagents-bacnet-mcp`
> from npm (requires uv) or PyPI; the command remains `bacnet-mcp`.
> The advertised catalog now contains **5 tools**. Older tool names in
> the examples below remain compatibility calls. See the
> [architecture and canonical tool guide](../docs/architecture.md)
> and [release guide](../docs/release.md).

# bacnet-python

A Python MCP server for BACnet/IP. Built with `uv`, [bacpypes3](https://github.com/JoelBender/BACpypes3)
and the `mcp` SDK (FastMCP), it exposes the `bacnet-mcp` CLI, which lets AI
agents and MCP-compatible clients discover devices (Who-Is/I-Am), read and
write object properties (ReadProperty/WriteProperty), and address objects
through friendly aliases.

> **Status:** early. Discovery, reads and (opt-in) writes go over BACnet/IP and
> are tested in CI against the mock device in
> [`../bacnet-mock-device`](../bacnet-mock-device), not yet against
> third-party controllers. See [Status](../README.md#status) in the
> repository README for what is not implemented.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- bacpypes3 and the `mcp` SDK — installed by `uv sync`

## Quick start

From this directory (`bacnet-python/`):

```bash
uv sync
uv run bacnet-mcp
```

By default the server binds UDP `127.0.0.1:47808`, has no broadcast address
(so discovery needs a target `address`, such as the mock's `127.0.0.1:47809`)
and refuses writes. Environment variables configure the interface and prefix,
port, device instance, write switch, timeouts, log level and the optional
object map; see [Configuration](../README.md#configuration) for the full
list and [`.env.example`](.env.example) for this directory's `.env` — the
only one read without `--env-file`, and only for `BACNET_*` connection
settings. A `.env` in the working directory or a parent is never read. The
write switch `BACNET_WRITES_ENABLED` and `OBJECT_MAP_FILE` are read only from
the process environment or a file named with `--env-file`.

`bacnet-mcp --help` lists the options (`--env-file PATH`, `--version`).

The process speaks MCP JSON-RPC on stdout and nothing else: all logging goes
to stderr, and while the server runs `sys.stdout` points at stderr too.

## Tests and lint

```bash
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

The suite binds BACnet/IP only on free `127.0.0.1` UDP ports. It covers the
configuration, the BACnet client against the mock device (imported from
`../bacnet-mock-device`), the tools through an in-memory MCP session, and an
end-to-end run that starts the mock and `python -m bacnet_mcp.cli` as
separate processes and drives the server over stdio with the mcp client SDK.

## Layout

```
bacnet-python/
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example
├── src/bacnet_mcp
│   ├── __init__.py
│   ├── cli.py            # entry point: .env, stderr logging, startup checks
│   ├── server.py         # FastMCP wiring, lifespan, stdout guard
│   ├── tools.py          # the seven tools, argument checks, object map
│   └── bacnet_client.py  # configuration and the bacpypes3 client
└── tests/
```
