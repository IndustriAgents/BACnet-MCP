"""Tests for the mock BACnet/IP device, driven by a real bacpypes3 client."""

from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from bacpypes3.apdu import ErrorRejectAbortNack, WritePropertyRequest
from bacpypes3.app import Application
from bacpypes3.constructeddata import Any as AnyValue
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.pdu import Address
from bacpypes3.primitivedata import Null, ObjectIdentifier, Real

import bacnet_mock_device as mock_module
from bacnet_mock_device import BACnetMockDevice, MockConfig, MockConfigError

MOCK_SCRIPT = Path(__file__).resolve().parents[1] / "bacnet_mock_device.py"


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_client(port: int) -> Application:
    return Application.from_object_list(
        [
            DeviceObject(
                objectIdentifier=("device", 4000),
                objectName="test-client",
                vendorIdentifier=999,
                apduTimeout=500,
                numberOfApduRetries=0,
            ),
            NetworkPortObject(
                f"127.0.0.1:{port}",
                objectIdentifier=("network-port", 1),
                objectName="NetworkPort-1",
            ),
        ]
    )


def run_with_mock(scenario, **config):
    """Start the mock and a client on free loopback ports, run the scenario."""

    async def main():
        mock = BACnetMockDevice(MockConfig(port=free_udp_port(), **config))
        await mock.start(drift=False)
        client = make_client(free_udp_port())
        try:
            return await scenario(mock, client, Address(f"127.0.0.1:{mock.config.port}"))
        finally:
            client.close()
            await mock.stop()

    return asyncio.run(main())


async def read(client, addr, objid, prop="present-value"):
    return await client.read_property(addr, objid, prop)


async def error_of(coro) -> str:
    try:
        await coro
    except ErrorRejectAbortNack as exc:
        return str(exc.reason)
    raise AssertionError("expected a BACnet error")


def test_who_is_answers_with_i_am():
    async def scenario(mock, client, addr):
        i_ams = await client.who_is(address=addr, timeout=2)
        assert len(i_ams) == 1
        assert i_ams[0].iAmDeviceIdentifier[1] == 12345
        assert i_ams[0].vendorID == 999

    run_with_mock(scenario)


def test_device_identity_and_objects():
    async def scenario(mock, client, addr):
        assert await read(client, addr, "device,12345", "vendor-name") == "MockVendor"
        assert await read(client, addr, "device,12345", "model-name") == "TestController"
        object_list = await read(client, addr, "device,12345", "object-list")
        names = {f"{t},{i}" for t, i in object_list}
        assert {
            "analog-input,1",
            "analog-input,2",
            "analog-output,1",
            "binary-value,1",
        } <= names

    run_with_mock(scenario)


def test_wildcard_device_instance_reads_this_device():
    async def scenario(mock, client, addr):
        object_type, instance = await read(client, addr, "device,4194303", "object-identifier")
        assert (str(object_type), instance) == ("device", 12345)
        assert await read(client, addr, "device,4194303", "model-name") == "TestController"

    run_with_mock(scenario)


def test_initial_values():
    async def scenario(mock, client, addr):
        assert await read(client, addr, "analog-input,1") == pytest.approx(72.5)
        assert await read(client, addr, "analog-input,2") == pytest.approx(68.0)
        assert await read(client, addr, "analog-output,1") == pytest.approx(50.0)
        assert str(await read(client, addr, "binary-value,1")) == "active"

    run_with_mock(scenario)


def test_custom_device_id():
    async def scenario(mock, client, addr):
        i_ams = await client.who_is(address=addr, timeout=2)
        assert i_ams[0].iAmDeviceIdentifier[1] == 777

    run_with_mock(scenario, device_id=777)


def test_commandable_writes_and_relinquish():
    async def scenario(mock, client, addr):
        await client.write_property(addr, "analog-output,1", "present-value", 30.0, priority=8)
        assert await read(client, addr, "analog-output,1") == pytest.approx(30.0)
        await client.write_property(addr, "analog-output,1", "present-value", Null(()), priority=8)
        assert await read(client, addr, "analog-output,1") == pytest.approx(50.0)

        await client.write_property(addr, "binary-value,1", "present-value", "inactive", priority=8)
        assert str(await read(client, addr, "binary-value,1")) == "inactive"
        await client.write_property(addr, "binary-value,1", "present-value", Null(()), priority=8)
        assert str(await read(client, addr, "binary-value,1")) == "active"

    run_with_mock(scenario)


def test_write_to_missing_instance_fails_and_creates_nothing():
    """Old JSON mock bug: a write to an unknown instance created it."""

    async def scenario(mock, client, addr):
        err = await error_of(client.write_property(addr, "analog-output,9", "present-value", 1.0))
        assert err == "unknown-object"
        assert await error_of(read(client, addr, "analog-output,9")) == "unknown-object"
        err = await error_of(client.write_property(addr, "binary-value,2", "present-value", 1))
        assert err == "unknown-object"

    run_with_mock(scenario)


def test_read_only_properties_refuse_writes():
    async def scenario(mock, client, addr):
        err = await error_of(client.write_property(addr, "analog-input,1", "present-value", 1.0))
        assert err == "write-access-denied"
        err = await error_of(client.write_property(addr, "device,12345", "object-name", "x"))
        assert err == "write-access-denied"
        err = await error_of(client.write_property(addr, "analog-output,1", "units", "percent"))
        assert err == "write-access-denied"
        err = await error_of(client.write_property(addr, "network-port,1", "object-name", "x"))
        assert err == "write-access-denied"
        assert await read(client, addr, "analog-input,1") == pytest.approx(72.5)

    run_with_mock(scenario)


def test_unknown_property_is_an_error():
    async def scenario(mock, client, addr):
        assert await error_of(read(client, addr, "analog-input,1", "priority-array")) == (
            "unknown-property"
        )
        # bacpypes3 alone would answer this write with operational-problem
        request = WritePropertyRequest(
            objectIdentifier=ObjectIdentifier("analog-input,1"),
            propertyIdentifier="priorityArray",
            propertyValue=AnyValue(Real(1.0)),
            destination=addr,
        )
        assert await error_of(client.request(request)) == "unknown-property"

    run_with_mock(scenario)


def test_drift_stays_within_span(monkeypatch):
    async def scenario(mock, client, addr):
        steps = iter([mock_module.DRIFT_STEP] * 100)
        monkeypatch.setattr(mock_module.random, "uniform", lambda a, b: next(steps))
        for _ in range(40):
            mock.drift_once()
        value = await read(client, addr, "analog-input,1")
        assert value == pytest.approx(72.5 + mock_module.DRIFT_SPAN)

    run_with_mock(scenario)


def test_drift_task_changes_values():
    async def scenario(mock, client, addr):
        mock._drift_task = asyncio.create_task(mock._drift_loop())
        values = set()
        for _ in range(5):
            await asyncio.sleep(0.06)
            values.add(float(await read(client, addr, "analog-input,1")))
        assert len(values) > 1

    run_with_mock(scenario, update_interval=0.05)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host": "not-an-ip"},
        {"port": 0},
        {"port": 70000},
        {"device_id": -1},
        {"device_id": 4194303},
        {"update_interval": 0},
    ],
)
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(MockConfigError):
        MockConfig(**kwargs)


def test_start_returns_with_the_port_bound_and_stop_releases_it():
    async def main():
        mock = BACnetMockDevice(MockConfig(port=free_udp_port()))
        await mock.start(drift=False)
        port = mock.config.port
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            with pytest.raises(OSError):
                probe.bind(("127.0.0.1", port))
        await mock.stop()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", port))

    asyncio.run(main())


def test_busy_port_fails_fast():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        with pytest.raises(MockConfigError, match="cannot bind"):
            asyncio.run(BACnetMockDevice(MockConfig(port=port)).start(drift=False))


def test_env_defaults_are_read_when_parsing(monkeypatch):
    monkeypatch.setenv("MOCK_BACNET_PORT", "47999")
    monkeypatch.setenv("MOCK_BACNET_DEVICE_ID", "42")
    args = mock_module.parse_args([])
    assert args.port == 47999
    assert args.device_id == 42


def test_help_runs():
    result = subprocess.run(
        [sys.executable, str(MOCK_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "--device-id" in result.stdout
