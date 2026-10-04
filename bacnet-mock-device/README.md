# bacnet-mock-device

Simple BACnet mock for local testing. Rather than implementing the full BACnet stack, this scaffold provides a JSON-over-TCP bridge that mimics Who-Is/I-Am discovery and property reads/writes, with analog inputs that drift slightly every second. It does not speak BACnet/IP, and the MCP server in [`../bacnet-python`](../bacnet-python) does not connect to it yet (see [Status](../README.md#status)).

## Usage

From this directory (`bacnet-mock-device/`):

```bash
uv sync
uv run bacnet-mock-device
```

Defaults:

- Listens on `127.0.0.1:7900` (TCP); change with `--host` / `--port` or `MOCK_BACNET_HOST` / `MOCK_BACNET_PORT`
- Responds to newline-delimited JSON commands, one JSON response per line:
  - `{ "op": "discover" }`
  - `{ "op": "read", "object": "analog-input:1", "property": "present-value" }`
  - `{ "op": "write", "object": "analog-output:1", "property": "present-value", "value": 50.0 }`

Simulated objects: `analog-input:1`, `analog-input:2`, `analog-output:1`, `binary-value:1` (only `present-value`; writes are accepted on `analog-output` and `binary-value`).

Writes are not validated yet: a write to an instance that does not exist creates it, and a non-numeric `value` for an `analog-output` closes the connection without a reply.

Try it by hand:

```bash
printf '%s\n' '{"op": "read", "object": "analog-input:1", "property": "present-value"}' | nc 127.0.0.1 7900
```

(The mock keeps the connection open; on Linux, OpenBSD `nc` needs `-N` to exit after the reply.)

Extend `bacnet_mock_device.py` later to integrate with BAC0/bacpypes3 for real BACnet/IP behavior (COV notifications, schedules, alarms).
