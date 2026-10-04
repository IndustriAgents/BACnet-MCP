"""Helpers shared by the BACnet MCP server tests.

Everything binds 127.0.0.1 on free UDP ports, so the suite runs on a CI runner
next to other BACnet software and never touches a real network.
"""

from __future__ import annotations

import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_DIR = REPO_ROOT / "bacnet-mock-device"
MOCK_SCRIPT = MOCK_DIR / "bacnet_mock_device.py"

# The mock is a single module that needs only bacpypes3, which the server
# depends on too, so the tests import it (or run it) with this interpreter.
if str(MOCK_DIR) not in sys.path:
    sys.path.insert(0, str(MOCK_DIR))

from bacnet_mock_device import BACnetMockDevice, MockConfig  # noqa: E402

from bacnet_mcp.bacnet_client import BACnetClient, BACnetConfig  # noqa: E402

MOCK_DEVICE_ID = 12345
ENV_KEYS = (
    "BACNET_INTERFACE",
    "BACNET_PORT",
    "BACNET_DEVICE_INSTANCE",
    "BACNET_WRITES_ENABLED",
    "BACNET_APDU_TIMEOUT_MS",
    "BACNET_APDU_RETRIES",
    "BACNET_LOG_LEVEL",
    "OBJECT_MAP_FILE",
)


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def client_config(**overrides) -> BACnetConfig:
    settings = {
        "interface": "127.0.0.1",
        "port": free_udp_port(),
        "writes_enabled": True,
        "apdu_timeout_ms": 300,
        "apdu_retries": 0,
    }
    settings.update(overrides)
    return BACnetConfig(**settings)


@asynccontextmanager
async def running_mock(**config) -> AsyncIterator[BACnetMockDevice]:
    mock = BACnetMockDevice(MockConfig(port=free_udp_port(), **config))
    await mock.start(drift=False)
    try:
        yield mock
    finally:
        await mock.stop()


@asynccontextmanager
async def running_client(**overrides) -> AsyncIterator[BACnetClient]:
    client = BACnetClient(client_config(**overrides))
    await client.open()
    try:
        yield client
    finally:
        await client.close()


def mock_address(mock: BACnetMockDevice) -> str:
    return f"127.0.0.1:{mock.config.port}"


def mock_object(mock: BACnetMockDevice, object_type: str, instance: int):
    assert mock.app is not None
    for obj in mock.app.objectIdentifier.values():
        if str(obj.objectIdentifier[0]) == object_type and obj.objectIdentifier[1] == instance:
            return obj
    raise KeyError(f"{object_type},{instance}")
