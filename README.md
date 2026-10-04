# BACnet MCP Server

Model Context Protocol (MCP) tooling for BACnet/IP building automation systems:
a Python MCP server (`bacnet-python`) that lets AI agents and other
MCP-compatible clients discover BACnet devices and read and write object
properties, plus a mock BACnet/IP device (`bacnet-mock-device`) to develop and
test against. Both are built on [bacpypes3](https://github.com/JoelBender/BACpypes3).

It follows the same pattern as the other protocol servers in
[IndustriConnect](https://github.com/IndustriAgents/IndustriConnect) (Modbus,
DNP3, MQTT, …): one Python MCP server over stdio, one mock, one shared
`{ success, data, error, meta }` response envelope.

## Status

Early, but real: the tools speak BACnet/IP. What works today:

- **Discovery**: `discover_devices` sends Who-Is (a global broadcast, or
  directed at one address) and returns the devices that answer with I-Am,
  plus each device's name, vendor and model read from its device object.
- **Reads**: `read_property` / `read_object_by_alias` send ReadProperty and
  return the decoded value (numbers, strings, enumeration names, lists; a
  REAL that is NaN or infinite comes back as `"NaN"`, `"Infinity"` or
  `"-Infinity"`). When you give an `address`, the device there is first
  checked to really be `device_id`.
- **Writes**: `write_property` / `write_object_by_alias` send WriteProperty
  (with an optional priority, or `null` to relinquish a priority slot) and
  report success only when the device acknowledges. They are **off by
  default** and refuse to act unless `BACNET_WRITES_ENABLED=true` comes from
  the process environment or `--env-file` (never from an automatically found
  `.env`). A write goes out **at most once**: if no answer comes back it is
  not retransmitted, and the result is `success: false` with
  `meta.outcome: "unknown"` — the write may have been applied, so read it
  back before retrying.
- Every failure — a BACnet error, reject or abort, a timeout, a datagram the
  OS refused to send, a bad or unknown argument — comes back as
  `success: false` with a readable `error`. Bad settings stop the server at
  startup.
- CI runs the server against the mock over BACnet/IP on the runner's loopback
  interface, through a real MCP stdio session.

Known limitations:

- It has been tested against the mock device only, not yet against
  third-party controllers, and only with directed (unicast) Who-Is on
  loopback: the global broadcast path is implemented but not exercised by
  the tests. Treat results from real equipment with the care you would give
  any new integration, and read [SECURITY.md](SECURITY.md).
- One property per request: no ReadPropertyMultiple, COV subscriptions,
  schedules, trend logs, alarms/events or file access yet.
- BACnet/IP on IPv4 only, as a normal device on the local subnet: no foreign
  device registration with a BBMD, no BACnet/SC, no IPv6. Devices behind a
  BACnet router (`network:station` addresses such as `2:5`) are not
  supported as targets.
- A global Who-Is needs a broadcast address, which comes from the prefix
  length in `BACNET_INTERFACE` (e.g. `192.168.1.10/24`). With the default
  `127.0.0.1` there is none, so discovery and reads need the device's
  `address`, and a server bound to loopback cannot reach other hosts. On
  macOS the loopback interface has no broadcast address at all, so
  `127.0.0.1/8` and the like are rejected at startup.
- Without a broadcast address the server receives only unicast replies.
  Many devices answer Who-Is with a *broadcast* I-Am, so a directed
  `discover_devices` may not see them; reading them by `address` still
  works, because their identity is checked with a ReadProperty (whose reply
  is unicast).
- Writes have no confirmation step beyond the `BACNET_WRITES_ENABLED` switch:
  when it is on, the model can write any writable property.

## Install

You need Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/IndustriAgents/BACnet-MCP.git
cd BACnet-MCP
```

## Quick start

Start the mock device. It is a BACnet/IP device (device instance `12345`) on
UDP `127.0.0.1:47809`:

```bash
cd bacnet-mock-device
uv sync
uv run bacnet-mock-device
```

Run the MCP server (from the repo root, in another terminal). It speaks MCP
over stdio, so it waits for a client; by default it binds UDP
`127.0.0.1:47808` and refuses writes:

```bash
cd bacnet-python
uv sync
uv run bacnet-mcp
```

To try the tools interactively, use the
[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) from the
repo root (add `-e BACNET_WRITES_ENABLED=true` to try writes against the mock):

```bash
npx @modelcontextprotocol/inspector uv run --directory bacnet-python bacnet-mcp
```

The Inspector needs Node.js: its current release (v2, npm `latest`) requires
Node.js 22.19+. On older Node (22.7.5+), run the v1 line with the same
arguments: `npx @modelcontextprotocol/inspector@v1-latest …`.

Then, against the mock:

| Tool | Arguments | Result |
|------|-----------|--------|
| `discover_devices` | `{"address": "127.0.0.1:47809"}` | device `12345`, vendor `MockVendor`, model `TestController` |
| `read_property` | `{"device_id": 12345, "object_type": "analog-input", "object_instance": 1}` | the zone temperature (starts at 72.5 and drifts) |
| `write_property` | `{"device_id": 12345, "object_type": "analog-output", "object_instance": 1, "property_id": "present-value", "value": 30, "priority": 8}` | refused unless writes are enabled; then acknowledged |

`read_property` finds the device's address from the earlier
`discover_devices`; you can also pass `"address": "127.0.0.1:47809"` directly.

Or register it with an MCP client such as Claude Desktop:

```json
{
  "mcpServers": {
    "bacnet": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/BACnet-MCP/bacnet-python", "bacnet-mcp"],
      "env": {
        "BACNET_INTERFACE": "127.0.0.1",
        "BACNET_WRITES_ENABLED": "false"
      }
    }
  }
}
```

If the client cannot find `uv` (Claude Desktop on macOS does not inherit your
shell's `PATH`), use its absolute path, the output of `which uv`, as
`"command"`.

To reach real devices, set `BACNET_INTERFACE` to the host's address on the
building automation network *with its prefix length*, e.g.
`192.168.1.10/24`, so that Who-Is can be broadcast — and read
[SECURITY.md](SECURITY.md) first.

## Configuration

The server reads these environment variables when it starts. Set them in the
process environment (your shell, or the `env` block of your MCP client's
config) or in a `.env` file.

| Variable | Default | Meaning |
|----------|---------|---------|
| `BACNET_INTERFACE` | `127.0.0.1` | IPv4 address to bind, optionally with a prefix length (`192.168.1.10/24`); the prefix sets the broadcast address used for global Who-Is |
| `BACNET_PORT` | `47808` | Local BACnet/IP UDP port |
| `BACNET_DEVICE_INSTANCE` | `1234` | Device instance the server presents on the network (pick one no real device uses) |
| `BACNET_WRITES_ENABLED` | `false` | `true` (or `1`, `yes`, `on`) allows the write tools; otherwise they refuse every write. Anything other than these or `false`/`0`/`no`/`off` is an error. **Read only from the process environment or `--env-file`**, see below |
| `BACNET_APDU_TIMEOUT_MS` | `3000` | How long to wait for a device's answer before retrying (100–60000) |
| `BACNET_APDU_RETRIES` | `3` | Retries of a *read* after a timeout (0–10); writes are never retried |
| `BACNET_LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`; logs always go to stderr |
| `OBJECT_MAP_FILE` | unset | JSON file of aliases for the `*_by_alias` tools |

`bacnet-mcp` loads a `.env` file from its working directory (or the nearest
parent that has one) before it reads any of these; see
[`bacnet-python/.env.example`](bacnet-python/.env.example). Values already
set in the process environment win over the file. Such a `.env` may set
connection settings but **not** `BACNET_WRITES_ENABLED`: an MCP client that
starts the server directly runs it in the client's project, whose `.env` you
may not control, so the server ignores the switch there with a warning on
stderr. To keep the switch in a file, name the file explicitly:
`bacnet-mcp --env-file /path/to/bacnet.env` (add `"--env-file", "…"` to the
`args` in your MCP client's config). An invalid value (say
`BACNET_WRITES_ENABLED=maybe`), an object map that cannot be read or parsed,
a UDP port that is already in use, or a broadcast address that cannot be
bound stops the server at startup with a message on stderr. The object map
is re-read when the file changes; a problem then is reported by the alias
tools.

An object map is a JSON object keyed by alias. `device`, `object_type` and
`object_instance` are required; `address`, `property_id` (default
`present-value`), `priority` and `description` are optional:

```json
{
  "zone1_temp": { "device": 12345, "address": "127.0.0.1:47809", "object_type": "analog-input", "object_instance": 1, "description": "Zone 1 temperature" },
  "ahu1_fan_cmd": { "device": 12345, "object_type": "binary-value", "object_instance": 1, "priority": 8 }
}
```

## Tools

| Tool | What it does |
|------|--------------|
| `discover_devices` | Who-Is (broadcast, or to `address`), optional `low_limit`/`high_limit`, waits `timeout_ms`; returns each device's instance, address, vendor id, name, vendor and model |
| `read_property` | ReadProperty of `device_id` / `object_type` / `object_instance` / `property_id` (default `present-value`), optional `array_index` and `address` |
| `write_property` | WriteProperty of `value` with optional `priority` (1–16; `null`, or the string `"null"`, relinquishes); refused unless `BACNET_WRITES_ENABLED=true` |
| `list_objects` | Lists the aliases loaded from `OBJECT_MAP_FILE` |
| `read_object_by_alias` | `read_property` for an alias from the object map |
| `write_object_by_alias` | `write_property` for an alias, at the alias's priority; refused unless writes are enabled |
| `ping` | Reports the BACnet/IP settings, the write switch, known device addresses and the object map status; sends nothing |

Every tool returns `{ "success": bool, "data": ..., "error": str | null, "meta": {...} }`,
including for invalid arguments. An argument a tool does not take (say a
misspelt `priorty`) is an error, not silently ignored, and instance numbers,
indexes and priorities must be JSON integers (`true` or `"8"` is refused).

The write tools also report `meta.outcome` and `meta.request_sent`:
`written` (the device acknowledged), `rejected` (the device refused it),
`unknown` (it was sent and no answer came back: it may have been applied,
read it back before retrying) or `not_sent` (refused before sending).

## Layout

```
BACnet-MCP/
├── README.md
├── LICENSE
├── CONTRIBUTING.md · CODE_OF_CONDUCT.md · SECURITY.md
├── .github/              # CI, issue/PR templates, IndustriConnect notification
├── bacnet-python/        # Python MCP server (uv, bacpypes3), tests/
└── bacnet-mock-device/   # Mock BACnet/IP device (uv, bacpypes3), tests/
```

Inside IndustriConnect this repository appears as the `BACnet-Project/` folder.

See [`bacnet-python/README.md`](bacnet-python/README.md) and
[`bacnet-mock-device/README.md`](bacnet-mock-device/README.md) for details on
each part, and [CONTRIBUTING.md](CONTRIBUTING.md#checks) for running the tests.

## Part of IndustriConnect

This server is one of the protocol servers in
[IndustriConnect](https://github.com/IndustriAgents/IndustriConnect), a suite of
MCP servers for industrial protocols (Modbus, MQTT/Sparkplug B, OPC UA, BACnet,
DNP3, EtherCAT, EtherNet/IP, PROFIBUS, PROFINET and S7comm). IndustriConnect
includes this repository as the `BACnet-Project` git submodule, tracking `main`.

Development happens here: issues and pull requests for the BACnet server belong
in [IndustriAgents/BACnet-MCP](https://github.com/IndustriAgents/BACnet-MCP),
not in IndustriConnect. See [CONTRIBUTING.md](CONTRIBUTING.md). Questions and
ideas are welcome in [Discussions](https://github.com/IndustriAgents/BACnet-MCP/discussions).

## Security

BACnet/IP has no authentication or encryption, and the server joins the BACnet
network as a device. Read [SECURITY.md](SECURITY.md) before pointing it at
anything real, and report vulnerabilities privately as described there
([private vulnerability reporting](https://github.com/IndustriAgents/BACnet-MCP/security/advisories/new)).

## License

[MIT](LICENSE) © 2026 IndustriAgents
