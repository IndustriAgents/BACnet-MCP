from __future__ import annotations

from pydantic import StrictInt

from bacnet_mcp.application.helpers import guarded
from bacnet_mcp.domain.model import (
    ArgumentError,
    Envelope,
    alias_target,
    check_target,
    check_text,
    fail,
)


class ReadOperations:
    async def read_property(
        self,
        device_id: StrictInt,
        object_type: str,
        object_instance: StrictInt,
        property_id: str = "present-value",
        array_index: StrictInt | None = None,
        address: str | None = None,
    ) -> Envelope:
        """Read one property of one object on a BACnet device (ReadProperty)."""
        meta = {"device_id": device_id}

        async def action() -> Envelope:
            target = check_target(
                device_id, object_type, object_instance, property_id, array_index, address
            )
            return await self.do_read(target)

        return await guarded("read_property", action, meta)

    async def read_object_by_alias(self, alias: str) -> Envelope:
        """Read the property an object-map alias points to (see list_objects)."""

        async def action() -> Envelope:
            name = check_text("alias", alias)
            spec = self.resources.object_map.get(name)
            if self.resources.object_map.error:
                return fail(self.resources.object_map.error, {"tool": "read_object_by_alias"})
            if spec is None:
                raise ArgumentError(f"Unknown alias {name!r}")
            result = await self.do_read(alias_target(name, spec))
            result["data"]["alias"] = name
            return result

        return await guarded("read_object_by_alias", action, {"alias": alias})
