"""BACnetClient against the mock BACnet/IP device, in one event loop.

These replace the old placeholder backends (known issue 2): every value
asserted here comes from the mock over BACnet/IP on 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest
from bacpypes3.primitivedata import Real
from helpers import (
    MOCK_DEVICE_ID,
    free_udp_port,
    mock_address,
    mock_object,
    running_client,
    running_mock,
)

from bacnet_mcp.bacnet_client import BACnetClient, BACnetClientError, BACnetConfig


def run(coro):
    return asyncio.run(coro)


def test_discovery_reports_the_real_device():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            devices, meta = await client.discover_devices(2000, mock_address(mock))
        assert meta["count"] == 1
        (device,) = devices
        assert device["device_id"] == MOCK_DEVICE_ID
        assert device["address"] == mock_address(mock)
        assert device["vendor_id"] == 999
        assert device["vendor"] == "MockVendor"
        assert device["model"] == "TestController"
        assert device["name"] == "BACnet Mock Device"

    run(scenario())


def test_discovery_sees_the_configured_device_instance():
    async def scenario():
        async with running_mock(device_id=777) as mock, running_client() as client:
            devices, _ = await client.discover_devices(2000, mock_address(mock))
        assert [d["device_id"] for d in devices] == [777]

    run(scenario())


def test_discovery_honours_the_instance_range():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            devices, _ = await client.discover_devices(300, mock_address(mock), 1, 100)
        assert devices == []

    run(scenario())


def test_discovery_waits_for_timeout_ms_when_nobody_answers():
    async def scenario():
        async with running_client() as client:
            start = time.perf_counter()
            devices, meta = await client.discover_devices(400, f"127.0.0.1:{free_udp_port()}")
            elapsed = time.perf_counter() - start
        assert devices == []
        assert meta["count"] == 0
        assert 0.35 <= elapsed < 3

    run(scenario())


def test_global_discovery_needs_a_broadcast_address():
    async def scenario():
        async with running_client() as client:
            with pytest.raises(BACnetClientError, match="no broadcast address"):
                await client.discover_devices(300)

    run(scenario())


def test_read_returns_the_device_value_not_a_placeholder():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            mock_object(mock, "analog-input", 1).presentValue = Real(12.25)
            addr = mock_address(mock)
            ai1, meta = await client.read_property(
                MOCK_DEVICE_ID, "analog-input", 1, "present-value", address=addr
            )
            ai2, _ = await client.read_property(MOCK_DEVICE_ID, "analog-input", 2)
            name, _ = await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, "object-name")
            units, _ = await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, "units")
        assert ai1 == pytest.approx(12.25)
        assert ai2 == pytest.approx(68.0)
        assert name == "Zone 1 Temperature"
        assert units == "degrees-fahrenheit"
        assert meta["address"] == addr

    run(scenario())


def test_read_decodes_structured_values():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            addr = mock_address(mock)
            objects, _ = await client.read_property(
                MOCK_DEVICE_ID, "device", MOCK_DEVICE_ID, "object-list", address=addr
            )
            length, _ = await client.read_property(
                MOCK_DEVICE_ID, "device", MOCK_DEVICE_ID, "object-list", array_index=0
            )
            priorities, _ = await client.read_property(
                MOCK_DEVICE_ID, "analog-output", 1, "priority-array"
            )
            fan, _ = await client.read_property(MOCK_DEVICE_ID, "binary-value", 1)
        assert "analog-input,1" in objects
        assert length == len(objects)
        assert priorities == [None] * 16
        assert fan == "active"

    run(scenario())


@pytest.mark.parametrize(
    ("object_type", "instance", "prop", "message"),
    [
        ("analog-input", 9, "present-value", "unknown-object"),
        ("analog-input", 1, "priority-array", "unknown-property"),
        ("bogus-type", 1, "present-value", "unknown object type"),
        ("analog-input", 1, "bogus-property", "unknown property"),
    ],
)
def test_read_errors_are_reported(object_type, instance, prop, message):
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            with pytest.raises(BACnetClientError, match=message):
                await client.read_property(
                    MOCK_DEVICE_ID, object_type, instance, prop, address=mock_address(mock)
                )

    run(scenario())


def test_a_device_instance_is_checked_against_the_address():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            with pytest.raises(BACnetClientError, match="no device 999 answered"):
                await client.read_property(999, "analog-input", 1, address=mock_address(mock))

    run(scenario())


def test_unknown_device_without_address_or_broadcast_fails():
    async def scenario():
        async with running_client() as client:
            with pytest.raises(BACnetClientError, match="no broadcast address"):
                await client.read_property(MOCK_DEVICE_ID, "analog-input", 1)

    run(scenario())


def test_write_reaches_the_device_and_can_be_relinquished():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            addr = mock_address(mock)
            meta = await client.write_property(
                MOCK_DEVICE_ID, "analog-output", 1, "present-value", 30.5, 8, address=addr
            )
            assert meta["priority"] == 8
            assert float(mock_object(mock, "analog-output", 1).presentValue) == 30.5
            await client.write_property(
                MOCK_DEVICE_ID, "analog-output", 1, "present-value", None, 8
            )
            assert float(mock_object(mock, "analog-output", 1).presentValue) == 50.0

            await client.write_property(
                MOCK_DEVICE_ID, "binary-value", 1, "present-value", False, 8
            )
            assert str(mock_object(mock, "binary-value", 1).presentValue) == "inactive"

    run(scenario())


def test_write_is_refused_when_writes_are_disabled():
    async def scenario():
        async with running_mock() as mock, running_client(writes_enabled=False) as client:
            with pytest.raises(BACnetClientError, match="disabled"):
                await client.write_property(
                    MOCK_DEVICE_ID,
                    "analog-output",
                    1,
                    "present-value",
                    10.0,
                    8,
                    address=mock_address(mock),
                )
            assert float(mock_object(mock, "analog-output", 1).presentValue) == 50.0

    run(scenario())


@pytest.mark.parametrize(
    ("object_type", "instance", "value", "priority", "message"),
    [
        ("analog-input", 1, 1.0, None, "write-access-denied"),
        ("analog-output", 9, 1.0, None, "unknown-object"),
        ("analog-output", 1, "abc", 8, "not valid"),
        ("binary-value", 1, "maybe", 8, "not valid"),
        ("analog-output", 1, None, None, "needs a priority"),
    ],
)
def test_failed_writes_never_report_success(object_type, instance, value, priority, message):
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            with pytest.raises(BACnetClientError, match=message):
                await client.write_property(
                    MOCK_DEVICE_ID,
                    object_type,
                    instance,
                    "present-value",
                    value,
                    priority,
                    address=mock_address(mock),
                )
            assert float(mock_object(mock, "analog-output", 1).presentValue) == 50.0

    run(scenario())


def test_a_device_that_stops_answering_times_out_and_is_forgotten():
    async def scenario():
        async with running_client() as client:
            async with running_mock() as mock:
                addr = mock_address(mock)
                await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
                assert client.connection_status()["known_devices"] == {str(MOCK_DEVICE_ID): addr}
            with pytest.raises(BACnetClientError, match="no-response"):
                await client.read_property(MOCK_DEVICE_ID, "analog-input", 1)
            assert client.connection_status()["known_devices"] == {}

    run(scenario())


def test_operations_need_an_open_client():
    async def scenario():
        client = BACnetClient(BACnetConfig(port=free_udp_port()))
        with pytest.raises(BACnetClientError, match="not running"):
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1)

    run(scenario())


def test_close_releases_the_port():
    async def scenario():
        async with running_client() as client:
            port = client.config.port
            assert client.is_open
        assert not client.is_open
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(("127.0.0.1", port))

    run(scenario())


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1:47809", "192.168.1.20", "2:5", "2:0x0a", "2:10.0.0.5"],
)
def test_valid_addresses(address):
    assert BACnetClient.parse_address(address) is not None


@pytest.mark.parametrize(
    "address",
    ["", "abc", "localhost:47808", "1.2.3", "300.1.1.1", "127.0.0.1:99999", "0:5", "65535:5"],
)
def test_invalid_addresses_are_rejected_without_dns(address):
    with pytest.raises(BACnetClientError, match="invalid BACnet address"):
        BACnetClient.parse_address(address)


def test_broadcast_addresses_only_for_discovery():
    with pytest.raises(BACnetClientError, match="broadcast"):
        BACnetClient.parse_address("*:*")
    assert BACnetClient.parse_address("*:*", allow_broadcast=True) is not None
