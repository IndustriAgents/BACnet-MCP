# bacnet-python

Scaffold of a Python MCP server for BACnet/IP. Built with `uv`, `BAC0`, and `mcp[cli]`, it exposes the `bacnet-mcp` CLI that will let AI agents and MCP-compatible clients discover devices, read/write object properties, and address objects through friendly aliases. The protocol bindings are in progress: today the tools return placeholder data and nothing is sent to a device.

> **Status:** scaffold. The tools, configuration and response envelope are in place, but discovery and property reads return placeholder data (with `success: true`) and writes are not yet sent to a device. See [Status](../README.md#status) in the repository README.

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- BAC0 dependencies (BACnet/IP stack, libsqlite, etc.) — installed by `uv sync`

## Quick Start

From this directory (`bacnet-python/`):

```bash
uv sync
BACNET_INTERFACE=127.0.0.1 BACNET_PORT=47808 BACNET_WRITES_ENABLED=false uv run bacnet-mcp
```

This keeps the BAC0 stack on loopback with writes refused; it still announces itself with an I-Am broadcast on that interface at startup. Environment variables configure the interface, port, device instance, write toggle, and the optional object map path; set them in the process environment, because a `.env` file is read too late for the interface, port and device instance. See [Configuration](../README.md#configuration) in the repository README for the full list.

## Layout

```
bacnet-python/
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
└── src/bacnet_mcp
    ├── __init__.py
    ├── cli.py
    ├── server.py
    ├── tools.py
    └── bacnet_client.py
```

This scaffold wires a BACnet client wrapper (using BAC0), FastMCP server, object-map loader, and tools for (placeholder) device discovery, property reads/writes, alias listing and access, and ping diagnostics. Extend these modules to implement real BACnet services, then advanced ones (COV subscriptions, alarms, schedules, trends, files).
