from __future__ import annotations

from bacnet_mcp.application.helpers import guarded
from bacnet_mcp.domain.model import Envelope, fail, ok


class MetadataOperations:
    async def list_objects(self) -> Envelope:
        """List the aliases defined in the object map (OBJECT_MAP_FILE)."""

        async def action() -> Envelope:
            objects = self.resources.object_map.list()
            if self.resources.object_map.error:
                return fail(self.resources.object_map.error, {"tool": "list_objects"})
            return ok({"objects": objects, "count": len(objects)})

        return await guarded("list_objects", action)
