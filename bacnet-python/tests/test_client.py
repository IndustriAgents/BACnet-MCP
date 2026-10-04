"""BACnetClient against the mock BACnet/IP device, in one event loop.

These replace the old placeholder backends (known issue 2): every value
asserted here comes from the mock over BACnet/IP on 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import errno
import math
import socket
import time
from functools import partial

import pytest
from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.app import Application
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
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
            with pytest.raises(BACnetClientError, match="is device 12345, not 999"):
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
    ("address", "expected"),
    [
        ("127.0.0.1:47809", "127.0.0.1:47809"),
        ("192.168.1.20", "192.168.1.20"),
        (" 10.0.0.5:47810 ", "10.0.0.5:47810"),
    ],
)
def test_valid_addresses(address, expected):
    assert str(BACnetClient.parse_address(address)) == expected


@pytest.mark.parametrize(
    ("address", "message"),
    [
        ("", "invalid BACnet address"),
        ("abc", "invalid BACnet address"),
        ("localhost:47808", "invalid BACnet address"),
        ("1.2.3", "invalid BACnet address"),
        ("300.1.1.1", "invalid BACnet address"),
        ("127.0.0.1:99999", "port out of range"),
        ("0.0.0.0", "not a device address"),
        ("255.255.255.255", "not a device address"),
        ("224.0.0.1:47808", "not a device address"),
        ("2:5", "remote BACnet network"),
        ("2:0x0a", "remote BACnet network"),
        ("2:10.0.0.5", "remote BACnet network"),
        ("2:*", "remote BACnet network"),
    ],
)
def test_unusable_addresses_are_rejected_without_dns(address, message):
    with pytest.raises(BACnetClientError, match=message):
        BACnetClient.parse_address(address, allow_broadcast=True)


def test_broadcast_addresses_only_for_discovery():
    with pytest.raises(BACnetClientError, match="broadcast"):
        BACnetClient.parse_address("*:*")
    assert BACnetClient.parse_address("*:*", allow_broadcast=True) is not None


def test_the_servers_own_objects_refuse_writes_from_the_network():
    """Another BACnet device can read the server's device object, not change it."""

    async def scenario():
        async with running_client(device_instance=4321) as client:
            peer = Application.from_object_list(
                [
                    DeviceObject(
                        objectIdentifier=("device", 4000),
                        objectName="peer",
                        vendorIdentifier=999,
                        apduTimeout=500,
                        numberOfApduRetries=0,
                    ),
                    NetworkPortObject(
                        f"127.0.0.1:{free_udp_port()}",
                        objectIdentifier=("network-port", 1),
                        objectName="NetworkPort-1",
                    ),
                ]
            )
            server = f"127.0.0.1:{client.config.port}"
            try:
                name = await peer.read_property(server, "device,4321", "object-name")
                assert str(name) == "bacnet-mcp-4321"
                for objid, prop, value in [
                    ("device,4321", "object-name", "renamed"),
                    ("device,4321", "apdu-timeout", 1),
                    ("network-port,1", "object-name", "renamed"),
                ]:
                    with pytest.raises(ErrorRejectAbortNack) as caught:
                        await peer.write_property(server, objid, prop, value)
                    assert str(caught.value.reason) == "write-access-denied"
                timeout = await peer.read_property(server, "device,4321", "apdu-timeout")
                assert int(timeout) == client.config.apdu_timeout_ms
            finally:
                peer.close()

    run(scenario())


@pytest.mark.parametrize("address", ["*", "*:*"])
def test_broadcast_discovery_without_a_broadcast_address_is_an_error(address):
    """It used to look like an empty network: the Who-Is was never sent."""

    async def scenario():
        async with running_client() as client:
            with pytest.raises(BACnetClientError, match="no broadcast address"):
                await client.discover_devices(300, address)

    run(scenario())


def test_booleans_are_only_written_to_boolean_or_enumerated_properties():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            with pytest.raises(BACnetClientError, match="give a number or string"):
                await client.write_property(
                    MOCK_DEVICE_ID,
                    "analog-output",
                    1,
                    "present-value",
                    True,
                    8,
                    address=mock_address(mock),
                )
            assert float(mock_object(mock, "analog-output", 1).presentValue) == 50.0
            await client.write_property(MOCK_DEVICE_ID, "binary-value", 1, "present-value", True, 8)
            assert str(mock_object(mock, "binary-value", 1).presentValue) == "active"

    run(scenario())


def test_targets_the_interface_cannot_reach_are_refused():
    """A loopback-bound server cannot reach 192.0.2.1: say so instead of timing out."""
    client = BACnetClient(BACnetConfig())
    with pytest.raises(BACnetClientError, match="bound to loopback"):
        client._target("192.0.2.1")


def test_the_subnet_broadcast_address_is_not_a_device():
    client = BACnetClient(BACnetConfig(interface="192.168.1.10/24"))
    with pytest.raises(BACnetClientError, match="broadcast address"):
        client._target("192.168.1.255")
    assert str(client._target("192.168.1.255", allow_broadcast=True)) == "*"
    assert str(client._target("192.168.1.20:47809")) == "192.168.1.20:47809"


def test_a_who_is_the_os_refused_to_send_is_an_error_not_an_empty_network():
    async def scenario():
        async with running_client() as client:
            for link_layer in client._app.link_layers.values():
                server = link_layer.server

                def refuse(data, addr, _protocol=server.local_protocol):
                    _protocol.error_received(OSError(errno.ENETUNREACH, "Network is unreachable"))

                server.local_transport.sendto = refuse
            with pytest.raises(BACnetClientError, match="could not be sent.*unreachable"):
                await client.discover_devices(300, f"127.0.0.1:{free_udp_port()}")

    run(scenario())


def test_identity_is_confirmed_even_when_the_i_am_is_not_received():
    """Many devices broadcast their I-Am, which a unicast-only socket never sees."""

    async def scenario():
        async with running_mock() as mock, running_client() as client:

            async def ignore_who_is(apdu):
                return None

            mock.app.do_WhoIsRequest = ignore_who_is
            value, _ = await client.read_property(
                MOCK_DEVICE_ID, "analog-input", 2, address=mock_address(mock)
            )
            assert value == pytest.approx(68.0)

    run(scenario())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(math.inf, "Infinity"), (-math.inf, "-Infinity"), (math.nan, "NaN")],
)
def test_non_finite_values_are_named_not_nulled(raw, expected):
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            mock_object(mock, "analog-input", 1).presentValue = Real(raw)
            value, _ = await client.read_property(
                MOCK_DEVICE_ID, "analog-input", 1, address=mock_address(mock)
            )
            return value

    assert run(scenario()) == expected


def test_integer_valued_floats_can_be_written_to_integer_properties():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            # Unsigned on the client side; the mock then refuses the write itself.
            with pytest.raises(BACnetClientError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID,
                    "analog-output",
                    1,
                    "time-delay",
                    30.0,
                    address=mock_address(mock),
                )
            assert "not valid" not in str(caught.value)
            assert "BACnet error" in str(caught.value)
            with pytest.raises(
                BACnetClientError, match=r"not valid for time-delay \(Unsigned\): \S"
            ):
                await client.write_property(MOCK_DEVICE_ID, "analog-output", 1, "time-delay", 2.5)

    run(scenario())


def test_identity_falls_back_to_who_is_without_wildcard_support():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            # bacpypes3's stock handler answers the wildcard with unknown-object
            mock.app.do_ReadPropertyRequest = partial(Application.do_ReadPropertyRequest, mock.app)
            value, _ = await client.read_property(
                MOCK_DEVICE_ID, "analog-input", 2, address=mock_address(mock)
            )
            assert value == pytest.approx(68.0)
            with pytest.raises(BACnetClientError, match="no device 999 answered a Who-Is"):
                await client.read_property(999, "analog-input", 2, address=mock_address(mock))

    run(scenario())


def test_peers_can_read_the_servers_wildcard_device_instance():
    async def scenario():
        async with running_client(device_instance=4321) as client, running_mock() as mock:
            identifier = await mock.app.read_property(
                f"127.0.0.1:{client.config.port}", "device,4194303", "object-identifier"
            )
            assert (str(identifier[0]), identifier[1]) == ("device", 4321)

    run(scenario())
