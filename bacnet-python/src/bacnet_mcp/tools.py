"""BACnet MCP tool implementations.

Every tool returns the suite's envelope ``{success, data, error, meta}``.
Arguments are validated before anything is sent, and every failure (bad
arguments, configuration, BACnet errors, timeouts) comes back as
``success: false`` with a readable ``error`` rather than as an exception.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bacpypes3.apdu import ErrorRejectAbortNack
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .bacnet_client import (
    MAX_DEVICE_INSTANCE,
    MAX_INSTANCE,
    NOT_SENT,
    UNKNOWN,
    BACnetClient,
    BACnetClientError,
    ConfigError,
    WriteError,
)

log = logging.getLogger(__name__)

Envelope = dict[str, Any]
# What a write tool accepts as a value: JSON scalars, or null to relinquish.
WriteValue = bool | int | float | str | None

MIN_TIMEOUT_MS = 100
MAX_TIMEOUT_MS = 60000

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
LOCAL_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITES_DEVICE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)


def envelope(
    success: bool,
    data: Any = None,
    error: str | None = None,
    meta: Mapping[str, Any] | None = None,
) -> Envelope:
    return {"success": success, "data": data, "error": error, "meta": dict(meta or {})}


def ok(data: Any = None, meta: Mapping[str, Any] | None = None) -> Envelope:
    return envelope(True, data=data, meta=meta)


def fail(message: str, meta: Mapping[str, Any] | None = None) -> Envelope:
    return envelope(False, error=message, meta=meta)


class ArgumentError(ValueError):
    """A tool argument is out of range or malformed."""


def check_int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArgumentError(f"{name} must be an integer")
    if not low <= value <= high:
        raise ArgumentError(f"{name} must be between {low} and {high}, got {value}")
    return value


def check_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArgumentError(f"{name} must be a non-empty string")
    return value.strip()


def check_optional_text(name: str, value: Any) -> str | None:
    return None if value is None else check_text(name, value)


def check_target(
    device_id: Any,
    object_type: Any,
    object_instance: Any,
    property_id: Any,
    array_index: Any = None,
    address: Any = None,
) -> dict[str, Any]:
    """Validate the arguments that identify one property of one object."""
    return {
        "device_id": check_int("device_id", device_id, 0, MAX_DEVICE_INSTANCE),
        "object_type": check_text("object_type", object_type),
        "object_instance": check_int("object_instance", object_instance, 0, MAX_INSTANCE),
        "property_id": check_text("property_id", property_id),
        "array_index": None
        if array_index is None
        else check_int("array_index", array_index, 0, 0xFFFFFFFF),
        "address": check_optional_text("address", address),
    }


def check_priority(priority: Any) -> int | None:
    return None if priority is None else check_int("priority", priority, 1, 16)


def check_value(value: Any) -> WriteValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise ArgumentError("value must be a number, a boolean, a string or null")


@dataclass(frozen=True, slots=True)
class ToolConfig:
    """Tool-level settings (the write switch lives in BACnetConfig)."""

    object_map_path: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ToolConfig:
        env = os.environ if environ is None else environ
        raw = env.get("OBJECT_MAP_FILE", "").strip()
        if not raw:
            return cls()
        try:
            return cls(object_map_path=Path(raw).expanduser())
        except RuntimeError as exc:  # e.g. ~nosuchuser/objects.json
            raise ConfigError(f"OBJECT_MAP_FILE {raw!r}: {exc}") from None


class ObjectMap:
    """Aliases for BACnet properties, loaded from a JSON file.

    The file is re-read when it changes. A file that cannot be read or parsed
    is reported through ``error`` instead of silently becoming empty.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._objects: dict[str, Any] = {}
        self._mtime: float | None = None
        self.error: str | None = None
        self.refresh()

    def refresh(self) -> None:
        if not self.path:
            self._objects, self._mtime, self.error = {}, None, None
            return
        try:
            mtime = self.path.stat().st_mtime
        except OSError as exc:
            self._objects, self._mtime = {}, None
            self.error = f"cannot read object map {self.path}: {exc.strerror or exc}"
            return
        if self._mtime is not None and mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self._objects = {}
            self.error = f"cannot read object map {self.path}: {exc}"
            return
        try:
            data = json.loads(text)
        except ValueError as exc:
            self._objects = {}
            self.error = f"object map {self.path} is not valid JSON: {exc}"
            return
        if not isinstance(data, dict):
            self._objects = {}
            self.error = f"object map {self.path} must be a JSON object keyed by alias"
            return
        self._objects = {str(alias): spec for alias, spec in data.items()}
        self.error = None

    def get(self, alias: str) -> Any:
        self.refresh()
        return self._objects.get(alias)

    def list(self) -> list[dict[str, Any]]:
        self.refresh()
        entries = []
        for alias, spec in self._objects.items():
            spec = spec if isinstance(spec, dict) else {}
            entries.append(
                {
                    "alias": alias,
                    "device": spec.get("device"),
                    "address": spec.get("address"),
                    "object_type": spec.get("object_type"),
                    "object_instance": spec.get("object_instance"),
                    "property_id": spec.get("property_id", "present-value"),
                    "priority": spec.get("priority"),
                    "description": spec.get("description"),
                }
            )
        return entries

    def count(self) -> int:
        self.refresh()
        return len(self._objects)


def alias_target(alias: str, spec: Any) -> dict[str, Any]:
    """Validate an object-map entry; required keys are never defaulted."""
    if not isinstance(spec, dict):
        raise ArgumentError(f"object map entry {alias!r} must be a JSON object")
    missing = [key for key in ("device", "object_type", "object_instance") if key not in spec]
    if missing:
        raise ArgumentError(f"object map entry {alias!r} is missing {', '.join(missing)}")
    try:
        target = check_target(
            spec["device"],
            spec["object_type"],
            spec["object_instance"],
            spec.get("property_id", "present-value"),
            spec.get("array_index"),
            spec.get("address"),
        )
        target["priority"] = check_priority(spec.get("priority"))
    except ArgumentError as exc:
        raise ArgumentError(f"object map entry {alias!r}: {exc}") from None
    return target


@dataclass(slots=True)
class ToolResources:
    client: BACnetClient
    config: ToolConfig
    object_map: ObjectMap


def write_outcome(outcome: str, request_sent: bool) -> dict[str, Any]:
    """The meta every write tool reports: written | rejected | unknown | not_sent."""
    return {"outcome": outcome, "request_sent": request_sent}


async def guarded(
    tool: str,
    action: Callable[[], Awaitable[Envelope]],
    meta: Mapping[str, Any] | None = None,
    *,
    write: bool = False,
) -> Envelope:
    """Run a tool body, turning every failure into a success:false envelope.

    For write tools the envelope also says whether the request went out. A
    failure whose origin is unclear is reported as possibly sent.
    """
    base = {"tool": tool, **(meta or {})}
    try:
        return await action()
    except WriteError as exc:
        return fail(str(exc), {**base, **write_outcome(exc.outcome, exc.request_sent)})
    except (ArgumentError, BACnetClientError) as exc:
        return fail(str(exc), {**base, **(write_outcome(NOT_SENT, False) if write else {})})
    except ErrorRejectAbortNack as exc:  # a BaseException in bacpypes3; never let it escape
        return fail(
            f"BACnet failure: {exc}", {**base, **(write_outcome(UNKNOWN, True) if write else {})}
        )
    except Exception as exc:  # noqa: BLE001 - the model gets an envelope, never a crash
        log.exception("%s failed", tool)
        return fail(
            f"{type(exc).__name__}: {exc}",
            {**base, **(write_outcome(UNKNOWN, True) if write else {})},
        )


def register_tools(server: FastMCP, resources: ToolResources) -> None:
    client = resources.client

    def register(annotations: ToolAnnotations) -> Callable:
        """Register a tool with its docstring, dedented, as the description."""

        def decorator(fn: Callable) -> Callable:
            description = inspect.cleandoc(fn.__doc__ or "")
            return server.tool(description=description, annotations=annotations)(fn)

        return decorator

    def writes_disabled(tool: str) -> Envelope | None:
        if client.config.writes_enabled:
            return None
        return fail(
            "Write operations are disabled by configuration: the server must be started "
            "with BACNET_WRITES_ENABLED=true to write to devices.",
            {"tool": tool, **write_outcome(NOT_SENT, False)},
        )

    async def do_read(target: dict[str, Any]) -> Envelope:
        value, meta = await client.read_property(
            target["device_id"],
            target["object_type"],
            target["object_instance"],
            target["property_id"],
            array_index=target["array_index"],
            address=target["address"],
        )
        data = {
            "device_id": target["device_id"],
            "address": meta.get("address"),
            "object_type": target["object_type"],
            "object_instance": target["object_instance"],
            "property_id": target["property_id"],
            "array_index": target["array_index"],
            "value": value,
        }
        return ok(data, meta)

    async def do_write(target: dict[str, Any], value: WriteValue, priority: int | None) -> Envelope:
        meta = await client.write_property(
            target["device_id"],
            target["object_type"],
            target["object_instance"],
            target["property_id"],
            value,
            priority=priority,
            array_index=target["array_index"],
            address=target["address"],
        )
        data = {
            "device_id": target["device_id"],
            "address": meta.get("address"),
            "object_type": target["object_type"],
            "object_instance": target["object_instance"],
            "property_id": target["property_id"],
            "array_index": target["array_index"],
            "value": value,
            "priority": priority,
            "acknowledged": True,
        }
        return ok(data, meta)

    @register(READ_ONLY)
    async def discover_devices(
        timeout_ms: int = 3000,
        address: str | None = None,
        low_limit: int | None = None,
        high_limit: int | None = None,
        include_details: bool = True,
    ) -> Envelope:
        """Find BACnet devices with Who-Is and collect their I-Am answers.

        Without `address` the Who-Is is a global broadcast, which needs the
        server's BACNET_INTERFACE to include a prefix length (for example
        192.168.1.10/24). With `address` it is sent to that one address, e.g.
        "192.168.1.20" or "127.0.0.1:47809" (the repository's mock device).

        Args:
            timeout_ms: How long to wait for I-Am answers (100-60000 ms).
            address: Optional target: an IPv4 address with optional port
                ("ip" or "ip:port"), or "*" / "*:*" for a broadcast.
            low_limit: Lowest device instance to ask for (use with high_limit).
            high_limit: Highest device instance to ask for (use with low_limit).
            include_details: Also read each device's object-name, vendor-name and
                model-name.

        Returns devices with device_id, address, vendor_id, max_apdu_length,
        segmentation and (with include_details) name, vendor and model.
        """

        async def action() -> Envelope:
            timeout = check_int("timeout_ms", timeout_ms, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)
            if (low_limit is None) != (high_limit is None):
                raise ArgumentError("give both low_limit and high_limit, or neither")
            if low_limit is not None:
                check_int("low_limit", low_limit, 0, MAX_INSTANCE)
                check_int("high_limit", high_limit, 0, MAX_INSTANCE)
                if low_limit > high_limit:
                    raise ArgumentError("low_limit must not be greater than high_limit")
            devices, meta = await client.discover_devices(
                timeout,
                check_optional_text("address", address),
                low_limit,
                high_limit,
                include_details=bool(include_details),
            )
            return ok({"devices": devices}, meta)

        return await guarded("discover_devices", action)

    @register(READ_ONLY)
    async def read_property(
        device_id: int,
        object_type: str,
        object_instance: int,
        property_id: str = "present-value",
        array_index: int | None = None,
        address: str | None = None,
    ) -> Envelope:
        """Read one property of one object on a BACnet device (ReadProperty).

        The device is found by its instance number: from an earlier
        discover_devices, from `address` (after checking that the device there
        is `device_id`), or by a Who-Is broadcast when the server has a
        broadcast address.

        Args:
            device_id: Device instance number (0-4194302).
            object_type: BACnet object type, e.g. "analog-input", "binary-value",
                "analog-output", "device".
            object_instance: Object instance number.
            property_id: Property name, e.g. "present-value" (default),
                "object-name", "units", "status-flags", "priority-array",
                "object-list". May carry an index: "priority-array[8]".
            array_index: Optional array index for array properties (0 = length).
            address: Optional device address, "ip" or "ip:port", e.g.
                "127.0.0.1:47809" for the mock. Its identity is checked first.

        Returns the decoded value: numbers, strings, enumeration names (e.g.
        "active"), lists for arrays, null for an empty priority slot, and
        "NaN" / "Infinity" / "-Infinity" for non-finite REALs.
        """
        meta = {"device_id": device_id}

        async def action() -> Envelope:
            target = check_target(
                device_id, object_type, object_instance, property_id, array_index, address
            )
            return await do_read(target)

        return await guarded("read_property", action, meta)

    @register(WRITES_DEVICE)
    async def write_property(
        device_id: int,
        object_type: str,
        object_instance: int,
        property_id: str,
        value: WriteValue,
        priority: int | None = None,
        array_index: int | None = None,
        address: str | None = None,
    ) -> Envelope:
        """Write one property of one object on a BACnet device (WriteProperty).

        This changes physical equipment. It is refused unless the server was
        started with BACNET_WRITES_ENABLED=true, and it reports success only
        when the device acknowledges the write.

        Args:
            device_id: Device instance number (0-4194302).
            object_type: BACnet object type, e.g. "analog-output", "binary-value".
            object_instance: Object instance number.
            property_id: Property name, usually "present-value".
            value: New value: a number for analog objects, "active"/"inactive"
                (or true/false) for binary objects, a string for text. null (or
                the string "null") relinquishes the given priority slot of a
                commandable object.
            priority: Command priority 1 (highest) to 16 (lowest) for
                commandable objects; required with a null value.
            array_index: Optional array index for array properties.
            address: Optional device address, "ip" or "ip:port".
        """
        meta = {"device_id": device_id}

        async def action() -> Envelope:
            disabled = writes_disabled("write_property")
            if disabled:
                return disabled
            target = check_target(
                device_id, object_type, object_instance, property_id, array_index, address
            )
            prio = check_priority(priority)
            new_value = check_value(value)
            if new_value is None and prio is None:
                raise ArgumentError("a null value relinquishes a priority slot: give a priority")
            return await do_write(target, new_value, prio)

        return await guarded("write_property", action, meta, write=True)

    @register(LOCAL_ONLY)
    async def list_objects() -> Envelope:
        """List the aliases defined in the object map (OBJECT_MAP_FILE).

        Each alias names one property of one object on one device and can be
        used with read_object_by_alias and write_object_by_alias. Nothing is
        sent to the network.
        """

        async def action() -> Envelope:
            objects = resources.object_map.list()
            if resources.object_map.error:
                return fail(resources.object_map.error, {"tool": "list_objects"})
            return ok({"objects": objects, "count": len(objects)})

        return await guarded("list_objects", action)

    @register(READ_ONLY)
    async def read_object_by_alias(alias: str) -> Envelope:
        """Read the property an object-map alias points to (see list_objects).

        Args:
            alias: An alias from the object map.
        """

        async def action() -> Envelope:
            name = check_text("alias", alias)
            spec = resources.object_map.get(name)
            if resources.object_map.error:
                return fail(resources.object_map.error, {"tool": "read_object_by_alias"})
            if spec is None:
                raise ArgumentError(f"Unknown alias {name!r}")
            result = await do_read(alias_target(name, spec))
            result["data"]["alias"] = name
            return result

        return await guarded("read_object_by_alias", action, {"alias": alias})

    @register(WRITES_DEVICE)
    async def write_object_by_alias(alias: str, value: WriteValue) -> Envelope:
        """Write the property an object-map alias points to (see list_objects).

        This changes physical equipment and is refused unless the server was
        started with BACNET_WRITES_ENABLED=true. The command priority comes
        from the alias's "priority" entry, if any.

        Args:
            alias: An alias from the object map.
            value: New value (see write_property); null (or the string "null")
                relinquishes the alias's priority slot.
        """

        async def action() -> Envelope:
            disabled = writes_disabled("write_object_by_alias")
            if disabled:
                return disabled
            name = check_text("alias", alias)
            spec = resources.object_map.get(name)
            if resources.object_map.error:
                return fail(resources.object_map.error, {"tool": "write_object_by_alias"})
            if spec is None:
                raise ArgumentError(f"Unknown alias {name!r}")
            target = alias_target(name, spec)
            new_value = check_value(value)
            if new_value is None and target["priority"] is None:
                raise ArgumentError(
                    f"a null value relinquishes a priority slot, but alias {name!r} has no priority"
                )
            result = await do_write(target, new_value, target["priority"])
            result["data"]["alias"] = name
            return result

        return await guarded("write_object_by_alias", action, {"alias": alias}, write=True)

    @register(LOCAL_ONLY)
    async def ping() -> Envelope:
        """Report the server's BACnet/IP settings and state; sends nothing.

        Shows the bound interface and port, the broadcast address (if any),
        the server's own device instance, whether writes are enabled, the
        devices whose addresses are known, and the object map status.
        """

        async def action() -> Envelope:
            return ok(
                {
                    "connection": client.connection_status(),
                    "writes_enabled": client.config.writes_enabled,
                    "object_map": str(resources.config.object_map_path)
                    if resources.config.object_map_path
                    else None,
                    "object_aliases": resources.object_map.count(),
                    "object_map_error": resources.object_map.error,
                }
            )

        return await guarded("ping", action)
