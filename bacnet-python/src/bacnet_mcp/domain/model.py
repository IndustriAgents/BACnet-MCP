from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from bacnet_mcp.domain.errors import MAX_DEVICE_INSTANCE, MAX_INSTANCE

log = logging.getLogger(__name__)
Envelope = dict[str, Any]
WriteValue = bool | int | float | str | None
MIN_TIMEOUT_MS = 100
MAX_TIMEOUT_MS = 60000


def envelope(
    success: bool, data: Any = None, error: str | None = None, meta: Mapping[str, Any] | None = None
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
        else check_int("array_index", array_index, 0, 4294967295),
        "address": check_optional_text("address", address),
    }


def check_priority(priority: Any) -> int | None:
    if priority is None:
        return None
    check_int("priority", priority, 1, 16)
    if priority == 6:
        raise ArgumentError("priority 6 is reserved for the minimum on/off algorithm")
    return priority


def check_value(value: Any) -> WriteValue:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise ArgumentError("value must be a number, a boolean, a string or null")


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


def write_outcome(outcome: str, request_sent: bool) -> dict[str, Any]:
    """The meta every write tool reports: written | rejected | unknown | not_sent."""
    return {"outcome": outcome, "request_sent": request_sent}
