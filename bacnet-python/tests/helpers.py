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
    config.setdefault("port", free_udp_port())
    mock = BACnetMockDevice(MockConfig(**config))
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


def lose_write_acks(mock: BACnetMockDevice) -> list[int]:
    """The mock applies every WriteProperty, but its reply never leaves.

    Returns the invoke IDs of the WritePropertys the mock processed, so a
    test can check that each write reached the device exactly once. Replies
    to other requests (e.g. the identity check before a write) still go out.
    """
    assert mock.app is not None
    seen: list[int] = []
    state = {"dropping": False}
    for link_layer in mock.app.link_layers.values():
        transport = link_layer.server.local_transport
        real_sendto = transport.sendto

        def sendto(data, addr=None, _real=real_sendto):
            if not state["dropping"]:
                _real(data, addr)

        transport.sendto = sendto
    original = mock.app.do_WritePropertyRequest

    async def apply_and_lose_ack(apdu):
        seen.append(apdu.apduInvokeID)
        state["dropping"] = True
        try:
            await original(apdu)
        finally:
            state["dropping"] = False

    mock.app.do_WritePropertyRequest = apply_and_lose_ack
    return seen


GARBAGE = {
    "complex-ack": lambda invoke_id: bytes([0x30, invoke_id, 0x0F, 0xFF, 0xFF]),
    "error": lambda invoke_id: bytes([0x50, invoke_id, 0x0F, 0xFF]),
    "simple-ack-truncated": lambda invoke_id: bytes([0x20, invoke_id]),
}


def answer_with_garbage(mock: BACnetMockDevice, handler_name: str, kind: str) -> list[int]:
    """Make the mock answer ``handler_name`` requests with an undecodable reply.

    bacpypes3 drops such a reply after closing the transaction, so without a
    deadline the request would wait forever. WriteProperty is applied first.
    """
    assert mock.app is not None
    server = next(iter(mock.app.link_layers.values())).server
    seen: list[int] = []

    async def garbled(apdu):
        seen.append(apdu.apduInvokeID)
        if handler_name == "do_WritePropertyRequest":
            obj = mock.app.get_object_id(apdu.objectIdentifier)
            obj.presentValue = 77.0  # applied, but the answer is garbage
        payload = bytes([0x01, 0x00]) + GARBAGE[kind](apdu.apduInvokeID)
        bvll = bytes([0x81, 0x0A]) + (4 + len(payload)).to_bytes(2, "big") + payload
        server.local_transport.sendto(bvll, apdu.pduSource.addrTuple)

    setattr(mock.app, handler_name, garbled)
    return seen
