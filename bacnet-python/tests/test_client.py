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
from bacpypes3.apdu import (
    AbortPDU,
    ErrorRejectAbortNack,
    ReadPropertyACK,
    WritePropertyRequest,
)
from bacpypes3.app import Application
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.primitivedata import Real
from helpers import (
    GARBAGE,
    MOCK_DEVICE_ID,
    answer_with_garbage,
    free_udp_port,
    lose_write_acks,
    mock_address,
    mock_object,
    running_client,
    running_mock,
)

from bacnet_mcp.bacnet_client import BACnetClient, BACnetClientError, BACnetConfig, WriteError


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


def count_requests(mock, handler_name: str) -> list[int]:
    """Count the requests the mock processes with ``handler_name``."""
    seen: list[int] = []
    original = getattr(mock.app, handler_name)

    async def counting(apdu):
        seen.append(apdu.apduInvokeID)
        await original(apdu)

    setattr(mock.app, handler_name, counting)
    return seen


def drop_replies(mock) -> None:
    """The mock still processes every request, but its replies are lost."""
    for link_layer in mock.app.link_layers.values():
        link_layer.server.local_transport.sendto = lambda data, addr=None: None


def test_a_lost_ack_never_causes_a_second_write():
    """The device applies the write, its SimpleAck is lost: it must see ONE write.

    bacpypes3 retransmits a confirmed request with no answer (numberOfApduRetries
    times, same invoke ID) and the device applies every copy.
    """

    async def scenario():
        async with running_mock() as mock, running_client(apdu_retries=3) as client:
            addr = mock_address(mock)
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
            writes = lose_write_acks(mock)
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID, "analog-output", 1, "present-value", 30.0, 8
                )
            assert len(writes) == 1
            assert float(mock_object(mock, "analog-output", 1).presentValue) == 30.0
            return caught.value

    error = run(scenario())
    assert error.outcome == "unknown"
    assert error.request_sent is True
    assert "the write may have been applied; read it back before retrying" in str(error)


def test_concurrent_writes_with_lost_acks_each_reach_the_device_once():
    async def scenario():
        async with running_mock() as mock, running_client(apdu_retries=3) as client:
            addr = mock_address(mock)
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
            writes = lose_write_acks(mock)
            results = await asyncio.gather(
                *(
                    client.write_property(
                        MOCK_DEVICE_ID, "analog-output", 1, "present-value", 10.0 + n, n
                    )
                    for n in (8, 9, 10)
                ),
                *(client.read_property(MOCK_DEVICE_ID, "analog-input", n) for n in (1, 2)),
                return_exceptions=True,
            )
            return writes, results

    writes, results = run(scenario())
    assert len(writes) == 3 and len(set(writes)) == 3  # one copy of each write
    for result in results[:3]:
        assert isinstance(result, WriteError)
        assert (result.outcome, result.request_sent) == ("unknown", True)
    assert all(isinstance(r, tuple) for r in results[3:])  # the reads succeeded


@pytest.mark.parametrize("kind", sorted(GARBAGE))
def test_a_garbled_reply_to_a_write_ends_as_unknown_not_a_hang(kind):
    async def scenario():
        async with running_mock() as mock, running_client(apdu_timeout_ms=300) as client:
            addr = mock_address(mock)
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
            writes = answer_with_garbage(mock, "do_WritePropertyRequest", kind)
            start = time.perf_counter()
            with pytest.raises(WriteError) as caught:
                await asyncio.wait_for(
                    client.write_property(
                        MOCK_DEVICE_ID, "analog-output", 1, "present-value", 77.0, 8
                    ),
                    30,
                )
            return caught.value, time.perf_counter() - start, len(writes)

    error, elapsed, count = run(scenario())
    assert (error.outcome, error.request_sent) == ("unknown", True)
    assert "may have been applied" in str(error)
    assert count == 1
    assert elapsed < 10


@pytest.mark.parametrize("kind", sorted(GARBAGE))
def test_a_garbled_reply_to_a_read_is_a_clean_error_not_a_hang(kind):
    async def scenario():
        async with running_mock() as mock, running_client(apdu_timeout_ms=300) as client:
            addr = mock_address(mock)
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
            answer_with_garbage(mock, "do_ReadPropertyRequest", kind)
            start = time.perf_counter()
            with pytest.raises(BACnetClientError, match="no usable reply|no-response"):
                await asyncio.wait_for(client.read_property(MOCK_DEVICE_ID, "analog-input", 2), 30)
            return time.perf_counter() - start

    assert run(scenario()) < 10


def test_reads_are_still_retried():
    async def scenario():
        async with running_mock() as mock, running_client(apdu_retries=3) as client:
            addr = mock_address(mock)
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
            reads = count_requests(mock, "do_ReadPropertyRequest")
            drop_replies(mock)
            with pytest.raises(BACnetClientError, match="no-response"):
                await client.read_property(MOCK_DEVICE_ID, "analog-input", 1)
            assert len(reads) == 4

    run(scenario())


@pytest.mark.parametrize(
    ("kwargs", "outcome", "sent"),
    [
        ({"object_type": "analog-input", "value": 1.0}, "rejected", True),  # write-access-denied
        ({"object_type": "analog-output", "instance": 9, "value": 1.0}, "rejected", True),
        ({"object_type": "analog-output", "value": "abc"}, "not_sent", False),
        ({"object_type": "bogus-type", "value": 1.0}, "not_sent", False),
        ({"object_type": "analog-output", "value": 1.0, "device_id": 999}, "not_sent", False),
        ({"object_type": "analog-output", "value": 1.0, "writes": False}, "not_sent", False),
    ],
)
def test_every_failed_write_says_whether_it_was_sent(kwargs, outcome, sent):
    async def scenario():
        writes = kwargs.get("writes", True)
        async with running_mock() as mock, running_client(writes_enabled=writes) as client:
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    kwargs.get("device_id", MOCK_DEVICE_ID),
                    kwargs["object_type"],
                    kwargs.get("instance", 1),
                    "present-value",
                    kwargs["value"],
                    8,
                    address=mock_address(mock),
                )
            return caught.value

    error = run(scenario())
    assert (error.outcome, error.request_sent) == (outcome, sent)


def test_a_successful_write_reports_written():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            return await client.write_property(
                MOCK_DEVICE_ID,
                "analog-output",
                1,
                "present-value",
                12.0,
                8,
                address=mock_address(mock),
            )

    meta = run(scenario())
    assert (meta["outcome"], meta["request_sent"]) == ("written", True)


def test_an_unexpected_reply_to_a_write_is_not_success():
    """Only a SimpleAck means "written"; anything else may or may not have applied."""

    async def scenario():
        async with running_mock() as mock, running_client() as client:
            addr = mock_address(mock)
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
            real_request = client._app.request

            def odd_reply(request):
                if isinstance(request, WritePropertyRequest):
                    future = asyncio.get_running_loop().create_future()
                    future.set_result(ReadPropertyACK())
                    return future
                return real_request(request)

            client._app.request = odd_reply
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID, "analog-output", 1, "present-value", 1.0, 8
                )
            return caught.value

    error = run(scenario())
    assert (error.outcome, error.request_sent) == ("unknown", True)
    assert "unexpected reply" in str(error)


def answer_writes_with_abort(mock, reason: str) -> None:
    """The mock applies the write, then its application aborts the transaction."""

    async def apply_then_abort(apdu):
        mock.app.get_object_id(apdu.objectIdentifier).presentValue = 99.0
        await mock.app.response(AbortPDU(True, reason=reason, context=apdu))

    mock.app.do_WritePropertyRequest = apply_then_abort


@pytest.mark.parametrize(
    ("reason", "outcome"),
    [
        # may come after the device acted: the write may have been applied
        ("application-exceeded-reply-time", "unknown"),
        ("other", "unknown"),
        ("out-of-resources", "unknown"),
        ("tsm-timeout", "unknown"),
        ("preempted-by-higher-priority-task", "unknown"),
        # sent by a server transaction after the whole (unsegmented) request
        # arrived, e.g. from AWAIT_RESPONSE: may follow execution
        ("invalid-apdu-in-this-state", "unknown"),
        # refused before execution
        ("segmentation-not-supported", "rejected"),
        ("buffer-overflow", "rejected"),
    ],
)
def test_an_abort_is_rejected_only_when_it_precedes_execution(reason, outcome):
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            addr = mock_address(mock)
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
            answer_writes_with_abort(mock, reason)
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID, "analog-output", 1, "present-value", 99.0, 8
                )
            return caught.value

    error = run(scenario())
    assert (error.outcome, error.request_sent) == (outcome, True)
    assert ("may have been applied" in str(error)) == (outcome == "unknown")


def test_aborts_raised_on_our_side_are_never_rejected():
    from bacnet_mcp.bacnet_client import write_failure_outcome

    for reason in ("segmentation-not-supported", "no-response", "other"):
        assert write_failure_outcome(AbortPDU(False, reason=reason)) == "unknown"
    assert write_failure_outcome(AbortPDU(True, reason="segmentation-not-supported")) == (
        "rejected"
    )


@pytest.mark.parametrize(
    ("object_type", "prop", "value", "message"),
    [
        ("analog-output", "present-value", 1e40, "too large for a 32-bit REAL"),
        ("analog-output", "present-value", "NaN", "NaN and infinity"),
        ("analog-output", "present-value", "inf", "NaN and infinity"),
        ("analog-output", "present-value", "-inf", "NaN and infinity"),
        ("analog-output", "present-value", "abc", "give a number"),
        ("binary-value", "present-value", 2, "use one of"),
        ("binary-value", "present-value", "on", "use one of"),
        ("analog-output", "units", True, "give one of the enumeration's names"),
        ("analog-output", "units", 99999, "use one of"),
        ("analog-output", "out-of-service", 1, "give true or false"),
        ("analog-output", "description", "x" * 3000, "segmented writes are not supported"),
    ],
)
def test_values_that_cannot_be_sent_as_given_are_not_sent(object_type, prop, value, message):
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            writes = count_requests(mock, "do_WritePropertyRequest")
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID, object_type, 1, prop, value, 8, address=mock_address(mock)
                )
            return caught.value, writes

    error, writes = run(scenario())
    assert (error.outcome, error.request_sent) == ("not_sent", False)
    assert message in str(error)
    assert writes == []


@pytest.mark.parametrize(
    ("value", "expected"), [(True, "active"), (False, "inactive"), (0, "inactive")]
)
def test_binary_values_take_booleans_names_and_defined_numbers(value, expected):
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            await client.write_property(
                MOCK_DEVICE_ID,
                "binary-value",
                1,
                "present-value",
                value,
                8,
                address=mock_address(mock),
            )
            return str(mock_object(mock, "binary-value", 1).presentValue)

    assert run(scenario()) == expected


def test_a_write_rechecks_the_identity_behind_a_cached_address():
    """The device at a known address was replaced: the write must not go to it."""

    async def scenario():
        async with running_client() as client:
            port = free_udp_port()
            async with running_mock(port=port) as first:
                await client.read_property(
                    MOCK_DEVICE_ID, "analog-input", 1, address=mock_address(first)
                )
            async with running_mock(port=port, device_id=777) as second:
                writes = count_requests(second, "do_WritePropertyRequest")
                with pytest.raises(WriteError) as caught:
                    await client.write_property(
                        MOCK_DEVICE_ID, "analog-output", 1, "present-value", 1.0, 8
                    )
                return caught.value, writes

    error, writes = run(scenario())
    assert (error.outcome, error.request_sent) == ("not_sent", False)
    assert "is device 777, not 12345" in str(error)
    assert writes == []


def test_a_failure_while_building_the_request_is_not_sent(monkeypatch):
    """Anything that fails before the request leaves is not_sent, never unknown."""
    from bacnet_mcp import bacnet_client

    # Skip the value checks so the encoder itself fails (REAL overflow).
    monkeypatch.setattr(bacnet_client, "coerce_write_value", lambda value, t, p: Real(1e40))

    async def scenario():
        async with running_mock() as mock, running_client() as client:
            writes = count_requests(mock, "do_WritePropertyRequest")
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID,
                    "analog-output",
                    1,
                    "present-value",
                    1.0,
                    8,
                    address=mock_address(mock),
                )
            return caught.value, writes

    error, writes = run(scenario())
    assert (error.outcome, error.request_sent) == ("not_sent", False)
    assert "could not build the request" in str(error)
    assert writes == []


def test_a_disconnect_just_before_sending_stops_the_write(monkeypatch):
    """The last check before app.request(): a disconnect with no await left."""
    from bacnet_mcp import bacnet_client

    async def scenario():
        async with running_mock() as mock, running_client() as client:
            writes = count_requests(mock, "do_WritePropertyRequest")
            build = bacnet_client.BACnetClient._write_request

            async def build_then_disconnect(self, *args):
                request = await build(self, *args)
                self._closing = True  # the client leaves; no cancellation is delivered
                return request

            monkeypatch.setattr(bacnet_client.BACnetClient, "_write_request", build_then_disconnect)
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID,
                    "analog-output",
                    1,
                    "present-value",
                    5.0,
                    8,
                    address=mock_address(mock),
                )
            await asyncio.sleep(0.2)
            return caught.value, writes

    error, writes = run(scenario())
    assert (error.outcome, error.request_sent) == ("not_sent", False)
    assert "disconnected before the write was sent" in str(error)
    assert writes == []


def test_a_disconnect_cancels_a_pending_write_and_blocks_new_calls():
    async def scenario():
        # A long APDU timeout, so the write really waits for the slow identity check.
        async with running_mock() as mock, running_client(apdu_timeout_ms=5000) as client:
            writes = count_requests(mock, "do_WritePropertyRequest")
            original = mock.app.do_ReadPropertyRequest

            async def slow_identity(apdu):
                await asyncio.sleep(1.0)
                await original(apdu)

            mock.app.do_ReadPropertyRequest = slow_identity
            pending = asyncio.ensure_future(
                client.write_property(
                    MOCK_DEVICE_ID,
                    "analog-output",
                    1,
                    "present-value",
                    5.0,
                    8,
                    address=mock_address(mock),
                )
            )
            await asyncio.sleep(0.3)
            client.client_disconnected()
            stopped = time.perf_counter()
            with pytest.raises(WriteError) as first:
                await pending
            # Cancelled at once, not after the identity check comes back (1 s).
            assert time.perf_counter() - stopped < 0.5
            with pytest.raises(WriteError) as later:
                await client.write_property(
                    MOCK_DEVICE_ID, "analog-output", 1, "present-value", 6.0, 8
                )
            with pytest.raises(BACnetClientError, match="disconnected"):
                await client.read_property(MOCK_DEVICE_ID, "analog-input", 1)
            await asyncio.sleep(1.2)  # past the identity answer
            return first.value, later.value, writes

    first, later, writes = run(scenario())
    assert (first.outcome, first.request_sent) == ("not_sent", False)
    assert (later.outcome, later.request_sent) == ("not_sent", False)
    assert writes == []


def test_a_disconnect_while_waiting_for_the_ack_is_unknown():
    async def scenario():
        async with running_mock() as mock, running_client() as client:
            addr = mock_address(mock)
            await client.read_property(MOCK_DEVICE_ID, "analog-input", 1, address=addr)
            writes = lose_write_acks(mock)
            pending = asyncio.ensure_future(
                client.write_property(MOCK_DEVICE_ID, "analog-output", 1, "present-value", 7.0, 8)
            )
            while not writes:
                await asyncio.sleep(0.01)
            client.client_disconnected()
            with pytest.raises(WriteError) as caught:
                await pending
            return caught.value, writes

    error, writes = run(scenario())
    assert (error.outcome, error.request_sent) == ("unknown", True)
    assert "may have been applied" in str(error)
    assert len(writes) == 1


def test_discovery_details_have_a_budget(monkeypatch):
    """A slow device cannot stretch discover_devices: details end up partial."""
    from bacnet_mcp import bacnet_client

    monkeypatch.setattr(bacnet_client, "DETAIL_BUDGET_S", 0.5)

    async def scenario():
        async with running_mock() as mock, running_client(apdu_timeout_ms=5000) as client:
            original = mock.app.do_ReadPropertyRequest

            async def slow_names(apdu):
                if str(apdu.propertyIdentifier) == "vendor-name":
                    await asyncio.sleep(5)
                await original(apdu)

            mock.app.do_ReadPropertyRequest = slow_names
            start = time.perf_counter()
            devices, meta = await client.discover_devices(300, mock_address(mock))
            return devices, meta, time.perf_counter() - start

    devices, meta, elapsed = run(scenario())
    assert elapsed < 3
    assert meta["details"] == "partial"
    (device,) = devices
    assert device["details_partial"] is True
    assert device["name"] == "BACnet Mock Device"  # read before the budget ran out
    assert device["vendor"] is None


def test_a_write_whose_deadline_runs_out_while_checking_the_device_is_never_sent(monkeypatch):
    """The overall deadline starts with the call and includes the identity check.

    The MCP SDK sends no cancellation when its own client-side timeout fires,
    so the server's deadline is what stops a late write.
    """
    from bacnet_mcp import bacnet_client

    # apdu 2000 ms -> request deadline 4 s; cap 5 s -> 1 s left to prepare.
    monkeypatch.setattr(bacnet_client, "WRITE_DEADLINE_CAP_S", 5.0)

    async def scenario():
        async with running_mock() as mock, running_client(apdu_timeout_ms=2000) as client:
            writes = count_requests(mock, "do_WritePropertyRequest")
            original = mock.app.do_ReadPropertyRequest

            async def slow_identity(apdu):
                await asyncio.sleep(1.5)  # answered, but after the write's budget
                await original(apdu)

            mock.app.do_ReadPropertyRequest = slow_identity
            start = time.perf_counter()
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID,
                    "analog-output",
                    1,
                    "present-value",
                    3.0,
                    8,
                    address=mock_address(mock),
                )
            elapsed = time.perf_counter() - start
            await asyncio.sleep(2.0)  # long past the identity answer
            return caught.value, elapsed, writes

    error, elapsed, writes = run(scenario())
    assert (error.outcome, error.request_sent) == ("not_sent", False)
    assert "deadline" in str(error)
    assert elapsed < 1.4
    assert writes == []


def test_the_deadline_is_checked_right_before_sending(monkeypatch):
    """Even when nothing could interrupt the preparation (no await to cancel)."""
    from bacnet_mcp import bacnet_client

    monkeypatch.setattr(bacnet_client, "WRITE_DEADLINE_CAP_S", 5.0)  # 1 s to prepare
    build = bacnet_client.BACnetClient._write_request

    async def slow_build(self, *args):
        request = await build(self, *args)
        time.sleep(1.2)  # blocks the loop: no cancellation can be delivered
        return request

    monkeypatch.setattr(bacnet_client.BACnetClient, "_write_request", slow_build)

    async def scenario():
        async with running_mock() as mock, running_client(apdu_timeout_ms=2000) as client:
            writes = count_requests(mock, "do_WritePropertyRequest")
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID,
                    "analog-output",
                    1,
                    "present-value",
                    3.0,
                    8,
                    address=mock_address(mock),
                )
            await asyncio.sleep(0.3)
            return caught.value, writes

    error, writes = run(scenario())
    assert (error.outcome, error.request_sent) == ("not_sent", False)
    assert "deadline ran out before it was sent" in str(error)
    assert writes == []


def test_the_default_write_deadline_stays_under_50_s():
    assert BACnetClient(BACnetConfig()).write_deadline == 25.0
    assert BACnetClient(BACnetConfig(apdu_retries=10)).write_deadline == 45.0
    # One request that needs longer than the cap still gets its full window.
    assert BACnetClient(BACnetConfig(apdu_timeout_ms=60000)).write_deadline == 62.0


def test_one_deadline_covers_every_identity_attempt_and_the_write(monkeypatch):
    """The call's deadline is never restarted per request.

    The device never answers the identity check: each attempt times out after
    0.5 s and is retried (3 retries = 2 s), but the write call has 1 s to
    prepare in total, so it stops after about two attempts and sends nothing.
    """
    from bacnet_mcp import bacnet_client

    # apdu 500 ms -> request deadline 2.5 s; cap 3.5 s -> 1 s to prepare.
    monkeypatch.setattr(bacnet_client, "WRITE_DEADLINE_CAP_S", 3.5)

    async def scenario():
        async with (
            running_mock() as mock,
            running_client(apdu_timeout_ms=500, apdu_retries=3) as client,
        ):
            writes = count_requests(mock, "do_WritePropertyRequest")
            identity_reads = []

            async def never_answer(apdu):
                identity_reads.append(time.perf_counter())
                await asyncio.sleep(30)

            mock.app.do_ReadPropertyRequest = never_answer
            start = time.perf_counter()
            with pytest.raises(WriteError) as caught:
                await client.write_property(
                    MOCK_DEVICE_ID,
                    "analog-output",
                    1,
                    "present-value",
                    3.0,
                    8,
                    address=mock_address(mock),
                )
            elapsed = time.perf_counter() - start
            await asyncio.sleep(1.5)  # the per-request retries would still be going
            return caught.value, elapsed, [t - start for t in identity_reads], writes

    error, elapsed, attempts, writes = run(scenario())
    assert (error.outcome, error.request_sent) == ("not_sent", False)
    assert "deadline" in str(error)
    assert elapsed < 1.4
    assert len(attempts) <= 2 and all(t < 1.1 for t in attempts)
    assert writes == []


def test_a_read_call_has_one_deadline_too(monkeypatch):
    """Retries share the call's deadline instead of each getting a fresh one."""
    from bacnet_mcp import bacnet_client

    # read_deadline = one request window (2.5 s); 11 attempts would take 5.5 s.
    monkeypatch.setattr(bacnet_client, "WRITE_DEADLINE_CAP_S", 1.0)

    async def scenario():
        async with (
            running_mock() as mock,
            running_client(apdu_timeout_ms=500, apdu_retries=10) as client,
        ):
            assert client.read_deadline == 2.5

            async def never_answer(apdu):
                await asyncio.sleep(30)

            mock.app.do_ReadPropertyRequest = never_answer
            start = time.perf_counter()
            with pytest.raises(BACnetClientError, match="deadline"):
                await client.read_property(
                    MOCK_DEVICE_ID, "analog-input", 1, address=mock_address(mock)
                )
            return time.perf_counter() - start

    assert run(scenario()) < 3.2
