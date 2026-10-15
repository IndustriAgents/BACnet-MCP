from __future__ import annotations

from pydantic import StrictBool, StrictInt

from bacnet_mcp.application.helpers import guarded
from bacnet_mcp.domain.errors import MAX_INSTANCE
from bacnet_mcp.domain.model import (
    MAX_TIMEOUT_MS,
    MIN_TIMEOUT_MS,
    ArgumentError,
    Envelope,
    check_int,
    check_optional_text,
    ok,
)


class DiscoveryOperations:
    async def discover_devices(
        self,
        timeout_ms: StrictInt = 3000,
        address: str | None = None,
        low_limit: StrictInt | None = None,
        high_limit: StrictInt | None = None,
        include_details: StrictBool = True,
    ) -> Envelope:
        """Find BACnet devices with Who-Is and collect their I-Am answers."""

        async def action() -> Envelope:
            timeout = check_int("timeout_ms", timeout_ms, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS)
            if (low_limit is None) != (high_limit is None):
                raise ArgumentError("give both low_limit and high_limit, or neither")
            if low_limit is not None:
                check_int("low_limit", low_limit, 0, MAX_INSTANCE)
                check_int("high_limit", high_limit, 0, MAX_INSTANCE)
                if low_limit > high_limit:
                    raise ArgumentError("low_limit must not be greater than high_limit")
            devices, meta = await self.client.discover_devices(
                timeout,
                check_optional_text("address", address),
                low_limit,
                high_limit,
                include_details=bool(include_details),
            )
            return ok({"devices": devices}, meta)

        return await guarded("discover_devices", action)
