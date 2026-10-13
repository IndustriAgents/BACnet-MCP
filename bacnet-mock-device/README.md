# bacnet-mock-device

A mock BACnet/IP device for local testing. It is a real BACnet/IP device built
on [bacpypes3](https://github.com/JoelBender/BACpypes3): it answers Who-Is with
I-Am and serves ReadProperty and WriteProperty over UDP, so the MCP server in
[`../bacnet-python`](../bacnet-python) talks to it with the same client code it
uses for real equipment.

## Usage

From this directory (`bacnet-mock-device/`):

```bash
uv sync
uv run bacnet-mock-device
```

It logs to stderr and runs until Ctrl-C (or SIGTERM).

| Option | Environment variable | Default | Meaning |
|--------|----------------------|---------|---------|
| `--host` | `MOCK_BACNET_HOST` | `127.0.0.1` | IPv4 address to bind; add a prefix length (e.g. `192.168.1.20/24`) to also receive broadcasts on that subnet |
| `--port` | `MOCK_BACNET_PORT` | `47809` | UDP port |
| `--device-id` | `MOCK_BACNET_DEVICE_ID` | `12345` | Device instance number |
| `--update-interval` | `MOCK_BACNET_UPDATE_INTERVAL` | `1.0` | Seconds between analog input updates |
| `--verbose` | `MOCK_BACNET_VERBOSE` | off | Log every update |
| `--exit-on-stdin-eof` | — | off | Stop when stdin closes (used by the tests, so a killed test run leaves no mock behind) |

The default port is 47809, not the BACnet/IP standard 47808, so the mock and
the MCP server (which binds 47808 by default) can run on one host. The mock
refuses to start if its UDP port is already bound.

## Simulated objects

| Object | Name | Value | Writable |
|--------|------|-------|----------|
| `device,12345` | BACnet Mock Device | vendor `MockVendor`, model `TestController`, vendor id 999 | no |
| `analog-input,1` | Zone 1 Temperature | starts at 72.5 °F, drifts by up to 0.2 per update (within ±5) | no |
| `analog-input,2` | Zone 2 Temperature | starts at 68.0 °F, drifts the same way | no |
| `analog-output,1` | Damper Command | 50.0 %, commandable (priority array, relinquish default 50.0) | `present-value` |
| `binary-value,1` | Fan Enable | `active`, commandable (priority array, relinquish default `active`) | `present-value` |

The objects carry the usual standard properties (`object-name`, `description`,
`units`, `status-flags`, `priority-array`, `property-list`, …), and a
ReadProperty of `device,4194303` (the wildcard instance) reads the mock's own
device object. It answers these wrong requests the way a controller would
(it is a mock, so other corners of the standard may differ):

- an object that does not exist → `object: unknown-object` (a write never
  creates one);
- a property the object does not have → `property: unknown-property`;
- a write to anything other than the two writable `present-value`s →
  `property: write-access-denied`;
- a write at priority 6 (reserved for minimum on/off) →
  `property: write-access-denied`;
- an undefined enumeration value, e.g. binary `present-value` 2 →
  `property: value-out-of-range`;
- a value of the wrong datatype → a BACnet reject.

## Talking to it

Through the MCP server (see the [Quick start](../README.md#quick-start)):
call `discover_devices` with `"address": "127.0.0.1:47809"` (a directed
Who-Is), then `read_property` with `"device_id": 12345`. `write_property`
also needs the server started with `BACNET_WRITES_ENABLED=true`.

Or with bacpypes3's own console, from this directory. Give it a port of its
own (47810 here): 47808 is the MCP server's default, and bacpypes3 would
silently share a port that is already in use.

```bash
uv run python -m bacpypes3 --address 127.0.0.1:47810
> whois 127.0.0.1:47809
> read 127.0.0.1:47809 analog-input,1 present-value
> write 127.0.0.1:47809 analog-output,1 present-value 30.0 8
> write 127.0.0.1:47809 analog-output,1 present-value null 8
```

The last line relinquishes priority 8 again.

## Tests

```bash
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

The tests start the mock on free loopback ports and drive it with a bacpypes3
client. Nothing leaves 127.0.0.1.
