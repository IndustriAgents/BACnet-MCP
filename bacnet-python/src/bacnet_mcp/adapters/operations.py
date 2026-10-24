"""Normalize bacpypes3 protocol exceptions before they cross the application port."""

from __future__ import annotations

from bacpypes3.apdu import ErrorRejectAbortNack

from ..domain.errors import MAY_HAVE_BEEN_APPLIED, UNKNOWN, BACnetClientError, WriteError


class BACnetOperations:
    def __init__(self, client):
        self.client = client

    @property
    def config(self):
        return self.client.config

    def connection_status(self):
        return self.client.connection_status()

    async def discover_devices(self, *args, **kwargs):
        return await self._call(self.client.discover_devices, args, kwargs)

    async def read_property(self, *args, **kwargs):
        return await self._call(self.client.read_property, args, kwargs)

    async def write_property(self, *args, **kwargs):
        return await self._call(self.client.write_property, args, kwargs, write=True)

    async def _call(self, operation, args, kwargs, *, write=False):
        try:
            return await operation(*args, **kwargs)
        except ErrorRejectAbortNack as exc:
            # These native PDUs derive from BaseException, not Exception.
            # Handle only that protocol family; cancellation still propagates.
            message = f"BACnet failure: {exc}"
            if write:
                raise WriteError(f"{message}: {MAY_HAVE_BEEN_APPLIED}", UNKNOWN, True) from exc
            raise BACnetClientError(message) from exc
