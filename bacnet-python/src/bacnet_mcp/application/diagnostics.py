from __future__ import annotations

from bacnet_mcp.application.helpers import guarded
from bacnet_mcp.domain.capabilities import capabilities
from bacnet_mcp.domain.model import Envelope, ok


class DiagnosticsOperations:
    async def ping(self) -> Envelope:
        """Report the server's BACnet/IP settings and state; sends nothing."""

        async def action() -> Envelope:
            return ok(
                {
                    "connection": self.client.connection_status(),
                    "capabilities": capabilities(self.client.config.writes_enabled),
                    "writes_enabled": self.client.config.writes_enabled,
                    "object_map": str(self.resources.config.object_map_path)
                    if self.resources.config.object_map_path
                    else None,
                    "object_aliases": self.resources.object_map.count(),
                    "object_map_error": self.resources.object_map.error,
                }
            )

        return await guarded("ping", action)
