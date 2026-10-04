"""Mock BACnet/IP device for testing the BACnet MCP server.

This is a real BACnet/IP device built on bacpypes3, not a stand-in protocol:
it answers Who-Is with I-Am and serves ReadProperty and WriteProperty over UDP,
so the MCP server's real client code path is exercised against it.

Objects (instance numbers match the old JSON mock):

- ``device,12345``: vendor ``MockVendor``, model ``TestController``
- ``analog-input,1`` and ``analog-input,2``: zone temperatures that drift by up
  to 0.2 every update interval; read-only
- ``analog-output,1``: a commandable damper command (priority array)
- ``binary-value,1``: a commandable fan enable (priority array)

Only ``present-value`` of the analog output and the binary value can be
written. Any other write is refused with ``property: write-access-denied``,
and a read or write of an object that does not exist fails with
``object: unknown-object``, as on a real device.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import random
import signal
import socket
import sys
from dataclasses import dataclass

from bacpypes3.app import Application
from bacpypes3.errors import PropertyError
from bacpypes3.local.analog import AnalogInputObject, AnalogOutputObject
from bacpypes3.local.cmd import Commandable
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.local.object import Object as LocalObject
from bacpypes3.object import BinaryValueObject as _BinaryValueObject

log = logging.getLogger("bacnet_mock_device")

DEFAULT_HOST = "127.0.0.1"
# 47809 rather than the BACnet/IP default 47808, so the mock and the MCP server
# (which binds 47808 by default) can share one host.
DEFAULT_PORT = 47809
DEFAULT_DEVICE_ID = 12345
DEFAULT_UPDATE_INTERVAL = 1.0

VENDOR_IDENTIFIER = 999  # bacpypes3's sample/test vendor identifier
VENDOR_NAME = "MockVendor"
MODEL_NAME = "TestController"

DRIFT_STEP = 0.2  # maximum change of an analog input per update
DRIFT_SPAN = 5.0  # analog inputs stay within +/- this of their start value


class MockConfigError(ValueError):
    """Raised when the mock's configuration is unusable."""


@dataclass(slots=True)
class MockConfig:
    """Where the mock listens and how it behaves."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    device_id: int = DEFAULT_DEVICE_ID
    update_interval: float = DEFAULT_UPDATE_INTERVAL

    def __post_init__(self) -> None:
        try:
            ipaddress.IPv4Interface(self.host)
        except ValueError as exc:
            raise MockConfigError(
                f"host must be an IPv4 address, optionally with a prefix length: {exc}"
            ) from exc
        if not 1 <= self.port <= 65535:
            raise MockConfigError(f"port must be 1-65535, got {self.port}")
        if not 0 <= self.device_id <= 4194302:
            raise MockConfigError(f"device id must be 0-4194302, got {self.device_id}")
        if self.update_interval <= 0:
            raise MockConfigError(f"update interval must be > 0, got {self.update_interval}")

    @property
    def interface(self) -> ipaddress.IPv4Interface:
        return ipaddress.IPv4Interface(self.host)

    @property
    def bacnet_address(self) -> str:
        """The address in bacpypes3 form, ``ip/prefix:port``."""
        return f"{self.interface.with_prefixlen}:{self.port}"


class _RestrictedWrites:
    """Refuse writes to every property not listed in ``_writable``.

    bacpypes3's local objects accept a write to any property; a real device
    does not, so the mock only allows what a controller would.
    """

    _writable: frozenset[str] = frozenset()

    async def write_property(self, attr, value, index=None, priority=None):
        name = attr if isinstance(attr, str) else self._property_identifier_class(attr).attr
        if name not in self._writable:
            raise PropertyError("writeAccessDenied")
        await super().write_property(attr, value, index, priority)


class MockDeviceObject(_RestrictedWrites, DeviceObject):
    pass


class MockNetworkPort(_RestrictedWrites, NetworkPortObject):
    pass


class MockAnalogInput(_RestrictedWrites, AnalogInputObject):
    pass


class MockAnalogOutput(_RestrictedWrites, AnalogOutputObject):
    _writable = frozenset({"presentValue"})


class MockBinaryValue(_RestrictedWrites, Commandable, LocalObject, _BinaryValueObject):
    # Built like bacpypes3's AnalogOutputObject: its BinaryValueObjectCmd lacks
    # the local Object base, so it would not serve property-list.
    _writable = frozenset({"presentValue"})


class MockApplication(Application):
    """bacpypes3 application that answers like a real controller.

    - ReadProperty of ``device,4194303`` (the wildcard instance) reads this
      device's own device object.
    - A WriteProperty to a property the object does not have is answered with
      ``property: unknown-property``; bacpypes3 alone answers
      ``device: operational-problem`` (it fails while decoding the value).
    """

    async def do_ReadPropertyRequest(self, apdu) -> None:  # noqa: N802 - bacpypes3 name
        # Device instance 4194303 means "the device you are": controllers
        # support it, but bacpypes3's own check never matches.
        object_type, instance = apdu.objectIdentifier
        if str(object_type) == "device" and instance == 4194303 and self.device_object:
            apdu.objectIdentifier = self.device_object.objectIdentifier
        await super().do_ReadPropertyRequest(apdu)

    async def do_WritePropertyRequest(self, apdu) -> None:  # noqa: N802 - bacpypes3 name
        obj = self.get_object_id(apdu.objectIdentifier)
        if obj is not None and obj.get_property_type(apdu.propertyIdentifier) is None:
            raise PropertyError("unknownProperty")
        await super().do_WritePropertyRequest(apdu)


def build_objects(device_id: int) -> list:
    """The device object and the simulated points."""
    return [
        MockDeviceObject(
            objectIdentifier=("device", device_id),
            objectName="BACnet Mock Device",
            vendorIdentifier=VENDOR_IDENTIFIER,
            vendorName=VENDOR_NAME,
            modelName=MODEL_NAME,
            description="Mock BACnet/IP device for the BACnet MCP server",
        ),
        MockAnalogInput(
            objectIdentifier=("analog-input", 1),
            objectName="Zone 1 Temperature",
            presentValue=72.5,
            units="degreesFahrenheit",
            description="Zone 1 temperature (drifts)",
        ),
        MockAnalogInput(
            objectIdentifier=("analog-input", 2),
            objectName="Zone 2 Temperature",
            presentValue=68.0,
            units="degreesFahrenheit",
            description="Zone 2 temperature (drifts)",
        ),
        MockAnalogOutput(
            objectIdentifier=("analog-output", 1),
            objectName="Damper Command",
            presentValue=50.0,
            units="percent",
            description="Damper position command (commandable)",
        ),
        MockBinaryValue(
            objectIdentifier=("binary-value", 1),
            objectName="Fan Enable",
            presentValue="active",
            description="Supply fan enable (commandable)",
        ),
    ]


def check_bindable(addr: tuple[str, int]) -> None:
    """Fail fast if a UDP address cannot be bound.

    bacpypes3 retries a failed bind forever in the background and binds with
    SO_REUSEPORT, so without this check a busy port either hangs the mock or is
    silently shared with another BACnet process.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind(addr)
        except OSError as exc:
            raise MockConfigError(f"cannot bind UDP {addr[0]}:{addr[1]}: {exc.strerror}") from exc


def _transport_tasks(app: Application) -> list[asyncio.Task]:
    """bacpypes3's pending socket-binding tasks (it binds in the background)."""
    tasks: list[asyncio.Task] = []
    for link_layer in app.link_layers.values():
        server = getattr(link_layer, "server", None)
        tasks.extend(t for t in getattr(server, "_transport_tasks", []) if not t.done())
    return tasks


def _shutdown(app: Application) -> None:
    for task in _transport_tasks(app):
        task.cancel()
    app.close()


class BACnetMockDevice:
    """A bacpypes3 application serving the mock objects."""

    def __init__(self, config: MockConfig | None = None) -> None:
        self.config = config or MockConfig()
        self.app: Application | None = None
        self._drift_task: asyncio.Task | None = None
        self._initial: dict[tuple[str, int], float] = {}

    @property
    def analog_inputs(self) -> list[MockAnalogInput]:
        if self.app is None:
            return []
        return [o for o in self.app.objectIdentifier.values() if isinstance(o, MockAnalogInput)]

    async def start(self, drift: bool = True) -> None:
        if self.app is not None:
            return
        interface = self.config.interface
        check_bindable((str(interface.ip), self.config.port))
        if interface.network.prefixlen < 32 and os.name != "nt":
            check_bindable((str(interface.network.broadcast_address), self.config.port))

        network_port = MockNetworkPort(
            self.config.bacnet_address,
            objectIdentifier=("network-port", 1),
            objectName="NetworkPort-1",
        )
        app = MockApplication.from_object_list(
            [*build_objects(self.config.device_id), network_port]
        )
        # Only report "listening" once the sockets are really bound.
        pending = _transport_tasks(app)
        if pending:
            _, not_done = await asyncio.wait(pending, timeout=5)
            if not_done:
                _shutdown(app)
                raise MockConfigError(f"timed out binding UDP {interface.ip}:{self.config.port}")
        self.app = app
        for obj in self.analog_inputs:
            key = (str(obj.objectIdentifier[0]), obj.objectIdentifier[1])
            self._initial[key] = float(obj.presentValue)
        if drift:
            self._drift_task = asyncio.create_task(self._drift_loop())
        log.info(
            "BACnet mock device %s listening on %s:%s (BACnet/IP over UDP)",
            self.config.device_id,
            interface.ip,
            self.config.port,
        )

    async def stop(self) -> None:
        if self._drift_task is not None:
            self._drift_task.cancel()
            try:
                await self._drift_task
            except asyncio.CancelledError:
                pass
            self._drift_task = None
        if self.app is not None:
            _shutdown(self.app)
            self.app = None
            # asyncio closes datagram sockets on the next loop iterations
            for _ in range(3):
                await asyncio.sleep(0)

    def drift_once(self) -> None:
        """Move every analog input by up to DRIFT_STEP, within DRIFT_SPAN."""
        for obj in self.analog_inputs:
            key = (str(obj.objectIdentifier[0]), obj.objectIdentifier[1])
            start = self._initial.get(key, float(obj.presentValue))
            value = float(obj.presentValue) + random.uniform(-DRIFT_STEP, DRIFT_STEP)
            obj.presentValue = min(max(value, start - DRIFT_SPAN), start + DRIFT_SPAN)

    async def _drift_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.update_interval)
            self.drift_once()
            log.debug("updated analog inputs")


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bacnet-mock-device",
        description="Mock BACnet/IP device (bacpypes3) for testing the BACnet MCP server.",
    )
    parser.add_argument(
        "--host",
        default=_env("MOCK_BACNET_HOST", DEFAULT_HOST),
        help="IPv4 address to bind, optionally with a prefix length such as "
        "192.168.1.20/24 to also receive broadcasts on that subnet "
        "(default: %(default)s, env MOCK_BACNET_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(_env("MOCK_BACNET_PORT", str(DEFAULT_PORT))),
        help="UDP port (default: %(default)s, env MOCK_BACNET_PORT)",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        default=int(_env("MOCK_BACNET_DEVICE_ID", str(DEFAULT_DEVICE_ID))),
        help="device instance number (default: %(default)s, env MOCK_BACNET_DEVICE_ID)",
    )
    parser.add_argument(
        "--update-interval",
        type=float,
        default=float(_env("MOCK_BACNET_UPDATE_INTERVAL", str(DEFAULT_UPDATE_INTERVAL))),
        help="seconds between analog input updates "
        "(default: %(default)s, env MOCK_BACNET_UPDATE_INTERVAL)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=_env("MOCK_BACNET_VERBOSE", "false").strip().lower() in {"1", "true", "yes", "on"},
        help="log every analog input update (env MOCK_BACNET_VERBOSE)",
    )
    return parser.parse_args(argv)


async def run(config: MockConfig) -> None:
    device = BACnetMockDevice(config)
    await device.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass
    try:
        await stop.wait()
    finally:
        log.info("stopping BACnet mock device")
        await device.stop()


def main(argv: list[str] | None = None) -> None:
    try:
        args = parse_args(argv)
    except ValueError as exc:  # a malformed MOCK_BACNET_* value
        sys.exit(f"bacnet-mock-device: {exc}")
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    if not args.verbose:
        logging.getLogger("bacpypes3").setLevel(logging.WARNING)
    try:
        config = MockConfig(
            host=args.host,
            port=args.port,
            device_id=args.device_id,
            update_interval=args.update_interval,
        )
        asyncio.run(run(config))
    except MockConfigError as exc:
        sys.exit(f"bacnet-mock-device: {exc}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
