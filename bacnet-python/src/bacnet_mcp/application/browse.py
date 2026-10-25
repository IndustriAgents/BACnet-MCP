"""Bounded object-list traversal over the protocol-neutral operation port."""

from __future__ import annotations

import anyio

from ..domain.errors import MAX_DEVICE_INSTANCE, BACnetClientError
from ..domain.model import Envelope, check_int, check_optional_text, ok
from .helpers import guarded


class BrowseOperations:
    async def browse_objects(
        self,
        device_id: int,
        address: str | None = None,
        start_index: int = 1,
        limit: int = 50,
        timeout_ms: int = 10000,
    ) -> Envelope:
        async def action() -> Envelope:
            check_int("device_id", device_id, 0, MAX_DEVICE_INSTANCE)
            check_int("start_index", start_index, 1, 4294967295)
            check_int("limit", limit, 1, 100)
            check_int("timeout_ms", timeout_ms, 100, 60000)
            target = check_optional_text("address", address)
            objects = []
            total = None
            next_index = start_index
            error = None
            resolved = target
            with anyio.move_on_after(timeout_ms / 1000) as scope:
                total, metadata = await self.client.read_property(
                    device_id,
                    "device",
                    device_id,
                    "object-list",
                    array_index=0,
                    address=target,
                )
                resolved = metadata.get("address", target)
                if (
                    isinstance(total, bool)
                    or not isinstance(total, int)
                    or not 0 <= total <= 4294967295
                ):
                    raise BACnetClientError("Device returned an invalid object-list length")
                stop = min(total + 1, start_index + limit)
                for index in range(start_index, stop):
                    try:
                        value, _ = await self.client.read_property(
                            device_id,
                            "device",
                            device_id,
                            "object-list",
                            array_index=index,
                            address=resolved,
                        )
                    except BACnetClientError:
                        error = "An object-list entry could not be read; resume at next_index"
                        break
                    objects.append({"array_index": index, "object_identifier": value})
                    next_index = index + 1
            if scope.cancel_called:
                error = "Object-list browse deadline reached; resume at next_index"
            complete = total is not None and next_index > total and error is None
            result = ok(
                {
                    "device_id": device_id,
                    "address": resolved,
                    "total_objects": total,
                    "objects": objects,
                },
                {
                    "complete": complete,
                    "next_index": None if complete else next_index,
                    "returned": len(objects),
                    "snapshot": False,
                },
            )
            if error:
                result.update(success=False, error=error)
                result["meta"]["code"] = "incomplete_result"
            return result

        return await guarded("browse_objects", action, {"device_id": device_id})
