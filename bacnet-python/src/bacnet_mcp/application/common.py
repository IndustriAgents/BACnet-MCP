from __future__ import annotations

from typing import Any

from bacnet_mcp.domain.errors import NOT_SENT
from bacnet_mcp.domain.model import Envelope, WriteValue, fail, ok, write_outcome


class CommonOperations:
    def __init__(self, resources, cancel_scope):
        self.resources = resources
        self.cancel_scope = cancel_scope
        self.client = self.resources.client

    def writes_disabled(self, tool: str) -> Envelope | None:
        if self.client.config.writes_enabled:
            return None
        return fail(
            (
                "Write operations are disabled by configuration: "
                "the server must be started with "
                "BACNET_WRITES_ENABLED=true to write to devices."
            ),
            {"tool": tool, **write_outcome(NOT_SENT, False)},
        )

    async def do_read(self, target: dict[str, Any]) -> Envelope:
        value, meta = await self.client.read_property(
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

    async def do_write(
        self, target: dict[str, Any], value: WriteValue, priority: int | None
    ) -> Envelope:
        meta = await self.client.write_property(
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
