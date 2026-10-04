# BACnet MCP Server

Model Context Protocol (MCP) tooling for BACnet/IP building automation systems,
at an early stage: a scaffold of a Python MCP server (`bacnet-python`) that will
let AI agents and other MCP-compatible clients discover BACnet devices and
read/write object properties, plus a JSON-over-TCP mock device
(`bacnet-mock-device`). The protocol bindings are in progress: today the tools
return placeholder data and nothing is sent to a device. See [Status](#status).

It follows the same pattern as the other protocol servers in
[IndustriConnect](https://github.com/IndustriAgents/IndustriConnect) (Modbus,
DNP3, OPC UA, …): one Python MCP server over stdio, one mock, one shared
`{ success, data, error, meta }` response envelope.

## Status

This is an early scaffold. The MCP surface — metadata, entry points, the BAC0
client wrapper, tool registration and the response envelope — is in place; the
BACnet protocol bindings behind the tools are not yet:

- `discover_devices` returns a fixed sample device rather than sending Who-Is.
- `read_property` and `read_object_by_alias` return `success: true` with the
  placeholder value `72.5` for any device, object and property, without reading
  from the network. Do not treat it as a reading.
- `write_property` / `write_object_by_alias` honour `BACNET_WRITES_ENABLED`, but
  when writes are allowed they return `success: true` without sending anything
  to a device.
- The tools have no descriptions yet, so `tools/list` gives an MCP client (and
  the model behind it) only tool and argument names, with nothing saying that
  results are placeholders.
- The mock speaks newline-delimited JSON over TCP, not BACnet/IP, and the server
  does not talk to it yet.
- BAC0 writes part of its log to `stdout`, which is also MCP's JSON-RPC stream,
  so clients may report parse errors for those lines: the Python `mcp` client
  logs a parse error per line and its `stdio_client` raises
  `BrokenResourceError` on shutdown; other clients may drop the session.

Planned next: real Who-Is/I-Am discovery and ReadProperty/WriteProperty through
BAC0, then COV subscriptions, schedules, trends, alarms and file access.

## Install

You need Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/IndustriAgents/BACnet-MCP.git
cd BACnet-MCP
```

## Quick start

Start the mock device (it listens on `127.0.0.1:7900`). The server does not
connect to the mock yet (see [Status](#status)); you can drive the mock by hand
as shown in [its README](bacnet-mock-device/README.md).

```bash
cd bacnet-mock-device
uv sync
uv run bacnet-mock-device
```

Run the MCP server (from the repo root, in another terminal). It speaks MCP over
stdio, so it waits for a client:

```bash
cd bacnet-python
uv sync
BACNET_INTERFACE=127.0.0.1 BACNET_PORT=47808 BACNET_WRITES_ENABLED=false uv run bacnet-mcp
```

On startup the server joins BACnet/IP as a device and broadcasts an I-Am, so
these examples keep it on loopback with writes refused. On macOS, BAC0 then logs
a harmless `[Errno 49] Can't assign requested address` traceback for the
derived `127.0.0.255` broadcast address. Use the address on your building
automation network only when you mean to join it, and read
[SECURITY.md](SECURITY.md) first.

To try the tools interactively, use the
[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) from the
repo root:

```bash
npx @modelcontextprotocol/inspector -e BACNET_INTERFACE=127.0.0.1 -e BACNET_WRITES_ENABLED=false \
  uv run --directory bacnet-python bacnet-mcp
```

The Inspector needs Node.js: its current release (v2, npm `latest`) requires
Node.js 22.19+. On older Node (22.7.5+), run the v1 line with the same
arguments: `npx @modelcontextprotocol/inspector@v1-latest …`.

Or register it with an MCP client such as Claude Desktop:

```json
{
  "mcpServers": {
    "bacnet": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/BACnet-MCP/bacnet-python", "bacnet-mcp"],
      "env": {
        "BACNET_INTERFACE": "127.0.0.1",
        "BACNET_PORT": "47808",
        "BACNET_WRITES_ENABLED": "false"
      }
    }
  }
}
```

Change `BACNET_INTERFACE` to the host's address on the building automation
network when the server should reach real devices.

## Configuration

The server is configured through environment variables. Set them in the
process environment: your shell, or the `env` block of your MCP client's
config.

| Variable | Default | Meaning |
|----------|---------|---------|
| `BACNET_INTERFACE` | `0.0.0.0` | Local address the BAC0 stack binds to (`0.0.0.0` is every interface) |
| `BACNET_PORT` | `47808` | Local BACnet/IP UDP port |
| `BACNET_DEVICE_INSTANCE` | `1234` | Device instance the server presents on the network |
| `BACNET_WRITES_ENABLED` | `true` | Set to `false` to refuse every write tool |
| `OBJECT_MAP_FILE` | unset | JSON file of aliases for the `*_by_alias` tools |

An object map is a JSON object keyed by alias:

```json
{
  "zone1_temp": { "device": 12345, "object_type": "analog-input", "object_instance": 1, "description": "Zone 1 temperature" },
  "ahu1_fan_cmd": { "device": 12345, "object_type": "binary-value", "object_instance": 1, "priority": 8 }
}
```

`property_id` defaults to `present-value`.

`bacnet-mcp` also loads a `.env` file at startup, but only after the BAC0
client has read its settings (they are read when the package is imported), so
`BACNET_INTERFACE`, `BACNET_PORT` and `BACNET_DEVICE_INSTANCE` in a `.env` file
are ignored. This is a known bug: a `BACNET_INTERFACE` set only in `.env` still
leaves the server on `0.0.0.0`. `BACNET_WRITES_ENABLED` and `OBJECT_MAP_FILE`
from `.env` do reach the tools, but `ping`'s `connection.writes_enabled` keeps
the value from the process environment.

## Tools

| Tool | What it does today |
|------|--------------------|
| `discover_devices` | Returns a sample device list (Who-Is not yet wired) |
| `read_property` | Reads `device_id` / `object_type` / `object_instance` / `property_id` — placeholder value for now |
| `write_property` | Write with optional `priority`; gated by `BACNET_WRITES_ENABLED`, not yet sent to the device |
| `list_objects` | Lists the aliases loaded from `OBJECT_MAP_FILE` |
| `read_object_by_alias` | `read_property` for an alias from the object map |
| `write_object_by_alias` | `write_property` for an alias from the object map |
| `ping` | Reports the BACnet configuration, write gating and alias count |

Every tool returns `{ "success": bool, "data": ..., "error": str | null, "meta": {...} }`.

## Layout

```
BACnet-MCP/
├── README.md
├── LICENSE
├── CONTRIBUTING.md · CODE_OF_CONDUCT.md · SECURITY.md
├── .github/              # CI, issue/PR templates, IndustriConnect notification
├── bacnet-python/        # Python MCP server (uv, BAC0/bacpypes3)
└── bacnet-mock-device/   # Mock BACnet device (JSON over TCP)
```

Inside IndustriConnect this repository appears as the `BACnet-Project/` folder.

See [`bacnet-python/README.md`](bacnet-python/README.md) and
[`bacnet-mock-device/README.md`](bacnet-mock-device/README.md) for details on
each part.

## Part of IndustriConnect

This server is one of the protocol servers in
[IndustriConnect](https://github.com/IndustriAgents/IndustriConnect), a suite of
MCP servers for industrial protocols (Modbus, MQTT/Sparkplug B, OPC UA, BACnet,
DNP3, EtherCAT, EtherNet/IP, PROFIBUS, PROFINET and S7comm). IndustriConnect
includes this repository as the `BACnet-Project` git submodule, tracking `main`.

Development happens here: issues and pull requests for the BACnet server belong
in [IndustriAgents/BACnet-MCP](https://github.com/IndustriAgents/BACnet-MCP),
not in IndustriConnect. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

BACnet/IP has no authentication or encryption, and the server joins the BACnet
network as a device. Read [SECURITY.md](SECURITY.md) before pointing it at
anything real, and report vulnerabilities privately as described there.

## License

[MIT](LICENSE) © 2026 IndustriAgents
