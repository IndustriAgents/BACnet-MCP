from __future__ import annotations

from typing import Any, Protocol


class BACnetOperationsPort(Protocol):
    config: Any

    async def discover_devices(
        self,
        timeout_ms: Any = ...,
        address: Any = ...,
        low_limit: Any = ...,
        high_limit: Any = ...,
        include_details: Any = ...,
    ) -> Any: ...

    async def read_property(
        self,
        device_id: Any,
        object_type: Any,
        object_instance: Any,
        property_id: Any = ...,
        array_index: Any = ...,
        address: Any = ...,
    ) -> Any: ...

    async def write_property(
        self,
        device_id: Any,
        object_type: Any,
        object_instance: Any,
        property_id: Any,
        value: Any,
        priority: Any = ...,
        array_index: Any = ...,
        address: Any = ...,
    ) -> Any: ...

    def connection_status(self) -> Any: ...


class BACnetClientPort(BACnetOperationsPort, Protocol):
    def is_open(self) -> Any: ...

    def preflight(self) -> Any: ...

    async def open(self) -> Any: ...

    async def close(self) -> Any: ...

    def client_disconnected(self) -> Any: ...

    def parse_address(address: Any, *, allow_broadcast: Any = ...) -> Any: ...

    def request_deadline(self) -> Any: ...

    def read_deadline(self) -> Any: ...

    def write_deadline(self) -> Any: ...
