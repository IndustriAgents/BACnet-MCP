"""BACnet/IP client for the MCP tools, built on bacpypes3.

The server joins the BACnet/IP network as a small device of its own (so that
it can send and receive Who-Is, I-Am, ReadProperty and WriteProperty) and
talks to other devices through bacpypes3's asyncio application layer.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import os
import re
import socket
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bacpypes3.apdu import (
    AbortPDU,
    ErrorPDU,
    ErrorRejectAbortNack,
    ReadPropertyACK,
    ReadPropertyRequest,
    RejectPDU,
    SimpleAckPDU,
    WritePropertyRequest,
)
from bacpypes3.app import Application
from bacpypes3.constructeddata import Any as AnyValue
from bacpypes3.constructeddata import AnyAtomic, Array, Choice, Sequence
from bacpypes3.errors import PropertyError
from bacpypes3.json.util import atomic_encode, taglist_to_json_list
from bacpypes3.local.device import DeviceObject
from bacpypes3.local.networkport import NetworkPortObject
from bacpypes3.pdu import Address, IPv4Address
from bacpypes3.primitivedata import (
    Atomic,
    Boolean,
    Enumerated,
    Integer,
    Null,
    ObjectIdentifier,
    Unsigned,
)

log = logging.getLogger(__name__)

DEFAULT_INTERFACE = "127.0.0.1"
DEFAULT_PORT = 47808
DEFAULT_DEVICE_INSTANCE = 1234
DEFAULT_APDU_TIMEOUT_MS = 3000
DEFAULT_APDU_RETRIES = 3

MAX_DEVICE_INSTANCE = 4194302  # 4194303 is the "any device" wildcard
MAX_INSTANCE = 4194303
VENDOR_IDENTIFIER = 999  # bacpypes3's default for applications without their own

# ip or ip:port
_IPV4_TARGET_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3})(?::(\d{1,5}))?$")
# network:station or network:* -- a device or broadcast behind a BACnet router
_REMOTE_RE = re.compile(r"^\d{1,5}:\S")
# * (local broadcast) and *:* (global broadcast)
_BROADCAST_FORMS = {"*", "*:*"}
_ADDRESS_HELP = "expected an IPv4 address with optional port, e.g. 192.168.1.20 or 127.0.0.1:47809"
LIMITED_BROADCAST = ipaddress.IPv4Address("255.255.255.255")
WILDCARD_DEVICE = ("device", 4194303)

_TRUE = {"1", "true", "yes", "y", "on"}
_FALSE = {"0", "false", "no", "n", "off"}


class ConfigError(ValueError):
    """Raised when the configuration is unusable."""


class BACnetClientError(RuntimeError):
    """Raised when a BACnet operation fails; the message is shown to the model."""


def env_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}")


def env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip(), 10)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None


@dataclass(frozen=True, slots=True)
class BACnetConfig:
    """Network identity of the server and the write switch.

    ``interface`` is an IPv4 address, optionally with a prefix length. The
    prefix defines the subnet whose broadcast address is used for global
    Who-Is: ``192.168.1.10/24`` broadcasts to ``192.168.1.255``. Without a
    prefix (the default ``127.0.0.1``) the server has no broadcast address and
    discovery needs an explicit target address.
    """

    interface: str = DEFAULT_INTERFACE
    port: int = DEFAULT_PORT
    device_instance: int = DEFAULT_DEVICE_INSTANCE
    writes_enabled: bool = False
    apdu_timeout_ms: int = DEFAULT_APDU_TIMEOUT_MS
    apdu_retries: int = DEFAULT_APDU_RETRIES

    def __post_init__(self) -> None:
        try:
            iface = ipaddress.IPv4Interface(self.interface.strip())
        except (ValueError, AttributeError) as exc:
            raise ConfigError(
                "BACNET_INTERFACE must be an IPv4 address, optionally with a prefix "
                f"length (e.g. 192.168.1.10/24), got {self.interface!r}"
            ) from exc
        object.__setattr__(self, "interface", iface.with_prefixlen)
        if not 1 <= self.port <= 65535:
            raise ConfigError(f"BACNET_PORT must be 1-65535, got {self.port}")
        if not 0 <= self.device_instance <= MAX_DEVICE_INSTANCE:
            raise ConfigError(
                f"BACNET_DEVICE_INSTANCE must be 0-{MAX_DEVICE_INSTANCE}, "
                f"got {self.device_instance}"
            )
        if not 100 <= self.apdu_timeout_ms <= 60000:
            raise ConfigError(
                f"BACNET_APDU_TIMEOUT_MS must be 100-60000, got {self.apdu_timeout_ms}"
            )
        if not 0 <= self.apdu_retries <= 10:
            raise ConfigError(f"BACNET_APDU_RETRIES must be 0-10, got {self.apdu_retries}")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> BACnetConfig:
        """Read the configuration from the environment at call time.

        The CLI calls this after loading ``.env``, so values from the file are
        honoured; nothing is read when the module is imported.
        """
        env = os.environ if environ is None else environ
        return cls(
            interface=env.get("BACNET_INTERFACE", "").strip() or DEFAULT_INTERFACE,
            port=env_int(env, "BACNET_PORT", DEFAULT_PORT),
            device_instance=env_int(env, "BACNET_DEVICE_INSTANCE", DEFAULT_DEVICE_INSTANCE),
            writes_enabled=env_bool(env, "BACNET_WRITES_ENABLED", False),
            apdu_timeout_ms=env_int(env, "BACNET_APDU_TIMEOUT_MS", DEFAULT_APDU_TIMEOUT_MS),
            apdu_retries=env_int(env, "BACNET_APDU_RETRIES", DEFAULT_APDU_RETRIES),
        )

    @property
    def ip_interface(self) -> ipaddress.IPv4Interface:
        return ipaddress.IPv4Interface(self.interface)

    @property
    def ip(self) -> str:
        return str(self.ip_interface.ip)

    @property
    def broadcast_ip(self) -> str | None:
        """The local broadcast address, or None when there is no subnet."""
        iface = self.ip_interface
        if iface.network.prefixlen >= 31 or iface.ip == ipaddress.IPv4Address("0.0.0.0"):
            return None
        return str(iface.network.broadcast_address)

    @property
    def bacnet_address(self) -> str:
        """The local address in bacpypes3 notation, ``ip/prefix:port``."""
        iface = self.ip_interface
        prefix = iface.network.prefixlen if self.broadcast_ip else 32
        return f"{iface.ip}/{prefix}:{self.port}"


class _ReadOnly:
    """Refuse WriteProperty from the network to the server's own objects.

    bacpypes3's local objects accept a write to any property, so without this
    anyone on the BACnet network could rename the server's device object or
    change its APDU timeout and retries.
    """

    async def write_property(self, attr, value, index=None, priority=None):
        raise PropertyError("writeAccessDenied")


class ServerDeviceObject(_ReadOnly, DeviceObject):
    pass


class ServerNetworkPortObject(_ReadOnly, NetworkPortObject):
    pass


class ServerApplication(Application):
    """The server's bacpypes3 application: WriteProperty is always refused,
    and ReadProperty understands the wildcard device instance.

    Refusing it here, before bacpypes3 decodes the value, also answers a write
    to a property the object does not have cleanly instead of with
    ``device: operational-problem``.
    """

    async def do_ReadPropertyRequest(self, apdu) -> None:  # noqa: N802 - bacpypes3 name
        # Device instance 4194303 means "the device you are"; bacpypes3's own
        # check for it never matches.
        object_type, instance = apdu.objectIdentifier
        if str(object_type) == "device" and instance == 4194303 and self.device_object:
            apdu.objectIdentifier = self.device_object.objectIdentifier
        await super().do_ReadPropertyRequest(apdu)

    async def do_WritePropertyRequest(self, apdu) -> None:  # noqa: N802 - bacpypes3 name
        raise PropertyError("writeAccessDenied")


def _check_bindable(ip: str, port: int, what: str) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind((ip, port))
        except OSError as exc:
            raise BACnetClientError(
                f"cannot bind the BACnet/IP {what} UDP {ip}:{port}: {exc.strerror or exc}"
            ) from exc


def describe_bacnet_error(exc: BaseException) -> str:
    """A short, model-readable description of an Error/Reject/Abort PDU."""
    if isinstance(exc, ErrorPDU):
        error_class = getattr(exc, "errorClass", None)
        error_code = getattr(exc, "errorCode", None)
        return f"BACnet error: {error_class}: {error_code}"
    if isinstance(exc, RejectPDU):
        return f"BACnet reject: {exc.apduAbortRejectReason}"
    if isinstance(exc, AbortPDU):
        reason = str(exc.apduAbortRejectReason)
        if reason == "no-response":
            return "BACnet abort: no-response (the device did not answer)"
        return f"BACnet abort: {reason}"
    return f"BACnet failure: {exc}"


def to_jsonable(value: Any) -> Any:
    """Convert a decoded BACnet value into JSON-serialisable Python."""
    if value is None or isinstance(value, Null):
        return None
    if isinstance(value, AnyAtomic):
        return to_jsonable(value.get_value())
    if isinstance(value, Atomic):
        return finite_or_name(atomic_encode(value))
    if isinstance(value, Choice):
        choice = getattr(value, "_choice", None)
        return to_jsonable(getattr(value, choice)) if choice else None
    if isinstance(value, Sequence):
        result: dict[str, Any] = {}
        for attr in value._elements:
            item = getattr(value, attr, None)
            if item is not None:
                result[attr] = to_jsonable(item)
        return result
    if isinstance(value, list | tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, AnyValue):
        return taglist_to_json_list(value.tagList)
    if isinstance(value, bool | int | float | str):
        return finite_or_name(value)
    return str(value)


def finite_or_name(value: Any) -> Any:
    """JSON has no NaN or Infinity (common on faulted sensors): name them."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    return value


class BACnetClient:
    """Who-Is, ReadProperty and WriteProperty over BACnet/IP, for the MCP tools.

    Must be opened inside the server's event loop (``open()``), which binds the
    UDP port. Every failure is raised as :class:`BACnetClientError`, including
    the Error/Reject/Abort PDUs that bacpypes3 raises as ``BaseException``.
    """

    def __init__(self, config: BACnetConfig | None = None) -> None:
        self.config = config or BACnetConfig.from_env()
        self._app: Application | None = None
        self._addresses: dict[int, Address] = {}
        self._send_errors: list[tuple[float, OSError]] = []

    # -- lifecycle -----------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._app is not None

    def preflight(self) -> None:
        """Fail early if the UDP port (and broadcast address) cannot be bound.

        bacpypes3 retries a failed bind forever in the background, and every
        send waits for it; it also binds with SO_REUSEPORT, so a port that is
        already in use would be shared silently. Checking here turns both into
        a clear startup error.
        """
        _check_bindable(self.config.ip, self.config.port, "port")
        broadcast = self.config.broadcast_ip
        if broadcast and os.name != "nt":
            _check_bindable(broadcast, self.config.port, "broadcast address")

    async def open(self) -> None:
        if self._app is not None:
            return
        self.preflight()
        device = ServerDeviceObject(
            objectIdentifier=("device", self.config.device_instance),
            objectName=f"bacnet-mcp-{self.config.device_instance}",
            vendorIdentifier=VENDOR_IDENTIFIER,
            vendorName="IndustriAgents BACnet MCP server",
            modelName="bacnet-mcp",
            apduTimeout=self.config.apdu_timeout_ms,
            numberOfApduRetries=self.config.apdu_retries,
        )
        network_port = ServerNetworkPortObject(
            self.config.bacnet_address,
            objectIdentifier=("network-port", 1),
            objectName="NetworkPort-1",
        )
        app = ServerApplication.from_object_list([device, network_port])
        pending = self._transport_tasks(app)
        if pending:
            _, not_done = await asyncio.wait(pending, timeout=5)
            if not_done:
                self._shutdown(app)
                raise BACnetClientError(
                    f"timed out binding BACnet/IP UDP {self.config.ip}:{self.config.port}"
                )
        self._watch_send_errors(app)
        self._app = app
        log.info(
            "BACnet/IP stack up on %s:%s as device %s (broadcast %s)",
            self.config.ip,
            self.config.port,
            self.config.device_instance,
            self.config.broadcast_ip or "none",
        )

    async def close(self) -> None:
        if self._app is not None:
            self._shutdown(self._app)
            self._app = None
            # asyncio closes datagram sockets on the next loop iterations
            for _ in range(3):
                await asyncio.sleep(0)

    @staticmethod
    def _transport_tasks(app: Application) -> list[asyncio.Task]:
        tasks: list[asyncio.Task] = []
        for link_layer in app.link_layers.values():
            server = getattr(link_layer, "server", None)
            tasks.extend(t for t in getattr(server, "_transport_tasks", []) if not t.done())
        return tasks

    def _watch_send_errors(self, app: Application) -> None:
        """Record datagrams the OS refused to send.

        asyncio hands a failed ``sendto`` (e.g. no route from the bound
        interface) to the protocol's ``error_received``, which bacpypes3 only
        logs at debug level; the request then just times out. Recording the
        error lets a Who-Is that never left the host fail with the reason
        instead of looking like an empty network.
        """
        for link_layer in app.link_layers.values():
            server = getattr(link_layer, "server", None)
            for name in ("local_protocol", "broadcast_protocol"):
                protocol = getattr(server, name, None)
                if protocol is None:
                    continue
                original = protocol.error_received

                def error_received(exc: Exception, _original=original) -> None:
                    if isinstance(exc, OSError):
                        self._send_errors.append((time.monotonic(), exc))
                        del self._send_errors[:-20]
                        log.warning("BACnet/IP send failed: %s", exc)
                    _original(exc)

                protocol.error_received = error_received

    def _send_error_since(self, mark: float) -> OSError | None:
        for when, exc in reversed(self._send_errors):
            if when >= mark:
                return exc
        return None

    def _send_failure(self, mark: float) -> str:
        exc = self._send_error_since(mark)
        return f" (sending failed: {exc.strerror or exc})" if exc else ""

    def _shutdown(self, app: Application) -> None:
        for task in self._transport_tasks(app):
            task.cancel()
        app.close()

    def _require_app(self) -> Application:
        if self._app is None:
            raise BACnetClientError("the BACnet/IP stack is not running")
        return self._app

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def parse_address(address: str, *, allow_broadcast: bool = False) -> Address:
        """Parse a device address (``ip`` or ``ip:port``) into a bacpypes3 Address.

        Only literal IPv4 addresses are accepted (no host names, so nothing is
        ever looked up in DNS). ``*`` and ``*:*`` are allowed for discovery.
        Addresses behind a BACnet router (``network:station``) are refused:
        routed access is not supported yet.
        """
        text = address.strip() if isinstance(address, str) else ""
        if text in _BROADCAST_FORMS:
            if not allow_broadcast:
                raise BACnetClientError(
                    f"address {address!r} is a broadcast address; give one device's address"
                )
            return Address(text)
        match = _IPV4_TARGET_RE.match(text)
        if match is None:
            if _REMOTE_RE.match(text):
                raise BACnetClientError(
                    f"address {address!r} is on a remote BACnet network (network:station); "
                    "routed addresses are not supported yet. Use the device's IP address."
                )
            raise BACnetClientError(f"invalid BACnet address {address!r}: {_ADDRESS_HELP}")
        try:
            ip = ipaddress.IPv4Address(match.group(1))
        except ValueError as exc:
            raise BACnetClientError(f"invalid BACnet address {address!r}: {exc}") from None
        port = int(match.group(2)) if match.group(2) else DEFAULT_PORT
        if not 1 <= port <= 65535:
            raise BACnetClientError(f"invalid BACnet address {address!r}: port out of range")
        if ip.is_unspecified or ip.is_multicast or ip == LIMITED_BROADCAST:
            raise BACnetClientError(
                f"address {address!r} is not a device address (unspecified, multicast or "
                "broadcast); use '*' for a broadcast Who-Is"
            )
        return Address(f"{ip}:{port}")

    def _target(self, address: str, *, allow_broadcast: bool = False) -> Address:
        """parse_address plus the checks that depend on this server's interface."""
        addr = self.parse_address(address, allow_broadcast=allow_broadcast)
        if not isinstance(addr, IPv4Address):
            return addr
        ip = ipaddress.IPv4Address(addr.addrTuple[0])
        if self.config.broadcast_ip and str(ip) == self.config.broadcast_ip:
            if allow_broadcast:
                return Address("*")
            raise BACnetClientError(
                f"address {address!r} is this subnet's broadcast address; give one device's address"
            )
        if self.config.ip_interface.ip.is_loopback and not ip.is_loopback:
            raise BACnetClientError(
                f"the server is bound to loopback (BACNET_INTERFACE={self.config.interface}) "
                f"and cannot reach {ip}; set BACNET_INTERFACE to the host's address on that "
                "network"
            )
        return addr

    def _require_broadcast(self) -> None:
        if self.config.broadcast_ip is None:
            raise BACnetClientError(
                f"no broadcast address: BACNET_INTERFACE={self.config.interface} has no "
                "subnet, so a global Who-Is cannot be sent. Pass the device's address, "
                "or set BACNET_INTERFACE to the host's address with its prefix length "
                "(e.g. 192.168.1.10/24)."
            )

    async def _resolve(self, device_id: int, address: str | None) -> Address:
        """Find (and confirm) the address of a device instance."""
        app = self._require_app()
        if address is not None:
            addr = self._target(address)
            cached = self._addresses.get(device_id)
            if cached is not None and cached == addr:
                return addr
            await self._confirm_identity(app, addr, device_id, address)
            self._addresses[device_id] = addr
            return addr

        cached = self._addresses.get(device_id)
        if cached is not None:
            return cached
        self._require_broadcast()
        mark = time.monotonic()
        i_ams = await app.who_is(device_id, device_id, timeout=self._resolve_timeout)
        if not i_ams:
            raise BACnetClientError(
                f"device {device_id} did not answer a Who-Is broadcast"
                f"{self._send_failure(mark)}; pass its address or run discover_devices"
            )
        self._addresses[device_id] = i_ams[0].pduSource
        return i_ams[0].pduSource

    async def _confirm_identity(
        self, app: Application, addr: Address, device_id: int, address: str
    ) -> None:
        """Check that the device at ``addr`` is ``device_id``.

        Reads the wildcard device's object-identifier, whose reply is always
        unicast; many devices answer Who-Is with a broadcast I-Am, which a
        server without a broadcast socket would never see. Falls back to a
        directed Who-Is for devices that do not support the wildcard.
        """
        mark = time.monotonic()
        request = ReadPropertyRequest(
            objectIdentifier=ObjectIdentifier(WILDCARD_DEVICE),
            propertyIdentifier="objectIdentifier",
            destination=addr,
        )
        try:
            response = await app.request(request)
        except ErrorRejectAbortNack as exc:
            if isinstance(exc, AbortPDU) and str(exc.apduAbortRejectReason) == "no-response":
                raise BACnetClientError(
                    f"no BACnet device answered at {address}{self._send_failure(mark)}"
                ) from None
            response = None  # wildcard not supported: ask with Who-Is instead
        if isinstance(response, ReadPropertyACK):
            try:
                found = response.propertyValue.cast_out(ObjectIdentifier)
            except Exception:  # noqa: BLE001 - malformed reply; fall back to Who-Is
                found = None
            if found is not None:
                if found[1] != device_id:
                    raise BACnetClientError(
                        f"the device at {address} is device {found[1]}, not {device_id}"
                    )
                return
        i_ams = await app.who_is(device_id, device_id, addr, timeout=self._resolve_timeout)
        if not i_ams:
            raise BACnetClientError(
                f"no device {device_id} answered a Who-Is at {address}"
                f"{self._send_failure(mark)}; check the device instance and address"
            )

    @property
    def _resolve_timeout(self) -> float:
        return max(1.0, self.config.apdu_timeout_ms / 1000.0)

    async def _request(self, app: Application, request: Any, device_id: int) -> Any:
        mark = time.monotonic()
        try:
            return await app.request(request)
        except ErrorRejectAbortNack as exc:
            message = describe_bacnet_error(exc)
            if isinstance(exc, AbortPDU) and str(exc.apduAbortRejectReason) == "no-response":
                self._addresses.pop(device_id, None)
                message += self._send_failure(mark)
            raise BACnetClientError(message) from None

    async def _references(
        self,
        app: Application,
        addr: Address,
        object_type: str,
        object_instance: int,
        property_id: str,
        array_index: int | None,
    ) -> tuple[Any, Any, Any, int | None]:
        vendor_info = await app.get_vendor_info(device_address=addr)
        try:
            objid = await app.parse_object_identifier(
                f"{object_type},{object_instance}", vendor_info=vendor_info
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise BACnetClientError(f"unknown object type {object_type!r}") from exc
        try:
            prop = await app.parse_property_reference(property_id, vendor_info=vendor_info)
        except (ValueError, TypeError, KeyError) as exc:
            raise BACnetClientError(f"unknown property {property_id!r}") from exc
        if prop.propertyArrayIndex is not None:
            if array_index is not None:
                raise BACnetClientError(
                    "give the array index either in property_id or as array_index, not both"
                )
            array_index = prop.propertyArrayIndex
        return vendor_info, objid, prop.propertyIdentifier, array_index

    @staticmethod
    def _property_type(vendor_info: Any, objid: Any, prop: Any, array_index: int | None) -> Any:
        object_class = vendor_info.get_object_class(objid[0])
        if object_class is None:
            raise BACnetClientError(f"no definition for object type {objid[0]}")
        property_type = object_class.get_property_type(prop)
        if property_type is None:
            raise BACnetClientError(f"{objid[0]} objects have no property {prop}")
        if issubclass(property_type, Array) and array_index is not None:
            property_type = Unsigned if array_index == 0 else property_type._subtype
        return property_type

    # -- services ------------------------------------------------------------

    async def discover_devices(
        self,
        timeout_ms: int = 3000,
        address: str | None = None,
        low_limit: int | None = None,
        high_limit: int | None = None,
        include_details: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Send Who-Is and collect the I-Am answers for ``timeout_ms``."""
        app = self._require_app()
        start = time.perf_counter()
        destination = None if address is None else self._target(address, allow_broadcast=True)
        if destination is None or not destination.is_localstation:
            # bacpypes3 would fail to send a broadcast in the background and the
            # Who-Is would just time out, which looks like "no devices".
            self._require_broadcast()
        mark = time.monotonic()
        i_ams = await app.who_is(low_limit, high_limit, destination, timeout=timeout_ms / 1000.0)
        if not i_ams and self._send_error_since(mark) is not None:
            raise BACnetClientError(f"the Who-Is could not be sent{self._send_failure(mark)}")

        devices: list[dict[str, Any]] = []
        sources: dict[int, Address] = {}
        for i_am in sorted(i_ams, key=lambda a: a.iAmDeviceIdentifier[1]):
            device_id = int(i_am.iAmDeviceIdentifier[1])
            self._addresses[device_id] = sources[device_id] = i_am.pduSource
            devices.append(
                {
                    "device_id": device_id,
                    "address": str(i_am.pduSource),
                    "vendor_id": int(i_am.vendorID),
                    "max_apdu_length": int(i_am.maxAPDULengthAccepted),
                    "segmentation": to_jsonable(i_am.segmentationSupported),
                }
            )
        if include_details and devices:
            limit = asyncio.Semaphore(8)

            async def details(device: dict[str, Any]) -> None:
                async with limit:
                    await self._add_details(device, sources[device["device_id"]])

            await asyncio.gather(*(details(device) for device in devices))
        meta = {
            "duration_ms": round((time.perf_counter() - start) * 1000.0, 3),
            "count": len(devices),
            "target": address or "global broadcast",
        }
        return devices, meta

    async def _add_details(self, device: dict[str, Any], addr: Address) -> None:
        """Best effort: object-name, vendor-name and model-name of a device."""
        device_id = device["device_id"]
        for key, prop in (
            ("name", "object-name"),
            ("vendor", "vendor-name"),
            ("model", "model-name"),
        ):
            try:
                device[key] = await self._read_at(addr, device_id, "device", device_id, prop, None)
            except BACnetClientError as exc:
                device[key] = None
                device.setdefault("detail_errors", {})[prop] = str(exc)

    async def read_property(
        self,
        device_id: int,
        object_type: str,
        object_instance: int,
        property_id: str = "present-value",
        array_index: int | None = None,
        address: str | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """ReadProperty; returns the decoded value as JSON-ready data."""
        self._require_app()
        start = time.perf_counter()
        addr = await self._resolve(device_id, address)
        value = await self._read_at(
            addr, device_id, object_type, object_instance, property_id, array_index
        )
        meta = {
            "duration_ms": round((time.perf_counter() - start) * 1000.0, 3),
            "device_id": device_id,
            "address": str(addr),
        }
        return value, meta

    async def _read_at(
        self,
        addr: Address,
        device_id: int,
        object_type: str,
        object_instance: int,
        property_id: str,
        array_index: int | None,
    ) -> Any:
        app = self._require_app()
        vendor_info, objid, prop, array_index = await self._references(
            app, addr, object_type, object_instance, property_id, array_index
        )
        request = ReadPropertyRequest(
            objectIdentifier=objid, propertyIdentifier=prop, destination=addr
        )
        if array_index is not None:
            request.propertyArrayIndex = array_index
        response = await self._request(app, request, device_id)
        if not isinstance(response, ReadPropertyACK):
            raise BACnetClientError(
                f"unexpected response to ReadProperty: {type(response).__name__}"
            )
        property_type = self._property_type(vendor_info, objid, prop, array_index)
        try:
            value = response.propertyValue.cast_out(property_type)
        except Exception as exc:  # noqa: BLE001 - decoding of device data
            raise BACnetClientError(f"could not decode the value of {prop}: {exc}") from exc
        return to_jsonable(value)

    async def write_property(
        self,
        device_id: int,
        object_type: str,
        object_instance: int,
        property_id: str,
        value: Any,
        priority: int | None = None,
        array_index: int | None = None,
        address: str | None = None,
    ) -> dict[str, Any]:
        """WriteProperty; succeeds only when the device answers with SimpleAck."""
        if not self.config.writes_enabled:
            raise BACnetClientError(
                "Write operations are disabled by configuration (set BACNET_WRITES_ENABLED=true)"
            )
        app = self._require_app()
        start = time.perf_counter()
        addr = await self._resolve(device_id, address)
        vendor_info, objid, prop, array_index = await self._references(
            app, addr, object_type, object_instance, property_id, array_index
        )
        property_type = self._property_type(vendor_info, objid, prop, array_index)
        if value is None:
            if priority is None:
                raise BACnetClientError(
                    "a null value relinquishes a priority-array slot and needs a priority"
                )
            encoded: Any = Null(())
        elif isinstance(value, bool) and not issubclass(property_type, Boolean | Enumerated):
            raise BACnetClientError(
                f"value {value!r} is not valid for {prop} ({property_type.__name__}): "
                "give a number or string"
            )
        else:
            if (
                isinstance(value, float)
                and value.is_integer()
                and issubclass(property_type, Unsigned | Integer)
            ):
                value = int(value)  # JSON 30.0 for an integer property
            try:
                encoded = value if isinstance(value, property_type) else property_type(value)
            except Exception as exc:  # noqa: BLE001 - user-supplied value
                reason = str(exc) or type(exc).__name__
                raise BACnetClientError(
                    f"value {value!r} is not valid for {prop} ({property_type.__name__}): {reason}"
                ) from exc
        request = WritePropertyRequest(
            objectIdentifier=objid,
            propertyIdentifier=prop,
            propertyValue=encoded,
            destination=addr,
        )
        if array_index is not None:
            request.propertyArrayIndex = array_index
        if priority is not None:
            request.priority = priority
        response = await self._request(app, request, device_id)
        if not isinstance(response, SimpleAckPDU):
            raise BACnetClientError(
                f"the device did not acknowledge the write ({type(response).__name__})"
            )
        return {
            "duration_ms": round((time.perf_counter() - start) * 1000.0, 3),
            "device_id": device_id,
            "address": str(addr),
            "priority": priority,
        }

    def connection_status(self) -> dict[str, Any]:
        return {
            "running": self.is_open,
            "interface": self.config.interface,
            "port": self.config.port,
            "broadcast": self.config.broadcast_ip,
            "device_instance": self.config.device_instance,
            "writes_enabled": self.config.writes_enabled,
            "known_devices": {str(k): str(v) for k, v in sorted(self._addresses.items())},
        }
