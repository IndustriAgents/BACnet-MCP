from __future__ import annotations

from pydantic import StrictInt

from bacnet_mcp.application.helpers import guarded
from bacnet_mcp.domain.model import (
    ArgumentError,
    Envelope,
    WriteValue,
    alias_target,
    check_priority,
    check_target,
    check_text,
    check_value,
)


class WriteOperations:
    async def write_property(
        self,
        device_id: StrictInt,
        object_type: str,
        object_instance: StrictInt,
        property_id: str,
        value: WriteValue,
        priority: StrictInt | None = None,
        array_index: StrictInt | None = None,
        address: str | None = None,
    ) -> Envelope:
        """Write one property of one object on a BACnet device (WriteProperty)."""
        meta = {"device_id": device_id}

        async def action() -> Envelope:
            disabled = self.writes_disabled("write_property")
            if disabled:
                return disabled
            target = check_target(
                device_id, object_type, object_instance, property_id, array_index, address
            )
            prio = check_priority(priority)
            new_value = check_value(value)
            if new_value is None and prio is None:
                raise ArgumentError("a null value relinquishes a priority slot: give a priority")
            return await self.do_write(target, new_value, prio)

        return await guarded("write_property", action, meta, write=True)

    async def write_object_by_alias(self, alias: str, value: WriteValue) -> Envelope:
        """Write the property an object-map alias points to (see list_objects)."""

        async def action() -> Envelope:
            disabled = self.writes_disabled("write_object_by_alias")
            if disabled:
                return disabled
            name = check_text("alias", alias)
            spec = self.resources.object_map.get(name)
            if self.resources.object_map.error:
                raise ArgumentError(self.resources.object_map.error)
            if spec is None:
                raise ArgumentError(f"Unknown alias {name!r}")
            target = alias_target(name, spec)
            new_value = check_value(value)
            if new_value is None and target["priority"] is None:
                raise ArgumentError(
                    f"a null value relinquishes a priority slot, but alias {name!r} has no priority"
                )
            result = await self.do_write(target, new_value, target["priority"])
            result["data"]["alias"] = name
            return result

        return await guarded("write_object_by_alias", action, {"alias": alias}, write=True)
