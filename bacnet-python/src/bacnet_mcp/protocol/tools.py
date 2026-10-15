from __future__ import annotations

import inspect
from collections.abc import Callable

from pydantic import StrictBool, StrictInt

from bacnet_mcp.application.resources import ToolResources
from bacnet_mcp.domain.model import Envelope, WriteValue

from ..application.operations import Operations
from .support import LOCAL_ONLY, READ_ONLY, WRITES_DEVICE, FastMCP, ToolAnnotations


def register_tools(server: FastMCP, resources: ToolResources) -> None:
    operations = Operations(resources, lambda: None)

    def register(annotations: ToolAnnotations) -> Callable:
        """Register a tool with its docstring, dedented, as the description."""

        def decorator(fn: Callable) -> Callable:
            description = inspect.cleandoc(fn.__doc__ or "")
            return server.tool(description=description, annotations=annotations)(fn)

        return decorator

    @register(READ_ONLY)
    async def discover_devices(
        timeout_ms: StrictInt = 3000,
        address: str | None = None,
        low_limit: StrictInt | None = None,
        high_limit: StrictInt | None = None,
        include_details: StrictBool = True,
    ) -> Envelope:
        """Find BACnet devices with Who-Is and collect their I-Am answers.

        Without `address` the Who-Is is a global broadcast, which needs the
        server's BACNET_INTERFACE to include a prefix length (for example
        192.168.1.10/24). With `address` it is sent to that one address, e.g.
        "192.168.1.20" or "127.0.0.1:47809" (the repository's mock device).

        Args:
            timeout_ms: How long to wait for I-Am answers (100-60000 ms).
            address: Optional target: an IPv4 address with optional port
                ("ip" or "ip:port"), or "*" / "*:*" for a broadcast.
            low_limit: Lowest device instance to ask for (use with high_limit).
            high_limit: Highest device instance to ask for (use with low_limit).
            include_details: Also read each device's object-name, vendor-name and
                model-name.

        Returns devices with device_id, address, vendor_id, max_apdu_length,
        segmentation and (with include_details) name, vendor and model. Details
        get at most 10 s after the Who-Is; meta.details is "complete", or
        "partial" with details_partial set on the devices not fully read.
        """
        return await operations.discover_devices(
            timeout_ms, address, low_limit, high_limit, include_details
        )

    @register(READ_ONLY)
    async def read_property(
        device_id: StrictInt,
        object_type: str,
        object_instance: StrictInt,
        property_id: str = "present-value",
        array_index: StrictInt | None = None,
        address: str | None = None,
    ) -> Envelope:
        """Read one property of one object on a BACnet device (ReadProperty).

        The device is found by its instance number: from an earlier
        discover_devices, from `address` (after checking that the device there
        is `device_id`), or by a Who-Is broadcast when the server has a
        broadcast address.

        Args:
            device_id: Device instance number (0-4194302).
            object_type: BACnet object type, e.g. "analog-input", "binary-value",
                "analog-output", "device".
            object_instance: Object instance number.
            property_id: Property name, e.g. "present-value" (default),
                "object-name", "units", "status-flags", "priority-array",
                "object-list". May carry an index: "priority-array[8]".
            array_index: Optional array index for array properties (0 = length).
            address: Optional device address, "ip" or "ip:port", e.g.
                "127.0.0.1:47809" for the mock. Its identity is checked first.

        Returns the decoded value: numbers, strings, enumeration names (e.g.
        "active"), lists for arrays, null for an empty priority slot, and
        "NaN" / "Infinity" / "-Infinity" for non-finite REALs.
        """
        return await operations.read_property(
            device_id, object_type, object_instance, property_id, array_index, address
        )

    @register(WRITES_DEVICE)
    async def write_property(
        device_id: StrictInt,
        object_type: str,
        object_instance: StrictInt,
        property_id: str,
        value: WriteValue,
        priority: StrictInt | None = None,
        array_index: StrictInt | None = None,
        address: str | None = None,
    ) -> Envelope:
        """Write one property of one object on a BACnet device (WriteProperty).

        This changes physical equipment. It is refused unless the server was
        started with BACNET_WRITES_ENABLED=true, and it reports success only
        when the device acknowledges the write.

        Args:
            device_id: Device instance number (0-4194302).
            object_type: BACnet object type, e.g. "analog-output", "binary-value".
            object_instance: Object instance number.
            property_id: Property name, usually "present-value".
            value: New value: a number for analog objects, "active"/"inactive"
                (or true/false) for binary objects, a string for text. null (or
                the string "null") relinquishes the given priority slot of a
                commandable object.
            priority: Command priority 1 (highest) to 16 (lowest) for
                commandable objects (6 is reserved and refused); required with
                a null value.
            array_index: Optional array index for array properties.
            address: Optional device address, "ip" or "ip:port".
        """
        return await operations.write_property(
            device_id,
            object_type,
            object_instance,
            property_id,
            value,
            priority,
            array_index,
            address,
        )

    @register(LOCAL_ONLY)
    async def list_objects() -> Envelope:
        """List the aliases defined in the object map (OBJECT_MAP_FILE).

        Each alias names one property of one object on one device and can be
        used with read_object_by_alias and write_object_by_alias. Nothing is
        sent to the network.
        """
        return await operations.list_objects()

    @register(READ_ONLY)
    async def read_object_by_alias(alias: str) -> Envelope:
        """Read the property an object-map alias points to (see list_objects).

        Args:
            alias: An alias from the object map.
        """
        return await operations.read_object_by_alias(alias)

    @register(WRITES_DEVICE)
    async def write_object_by_alias(alias: str, value: WriteValue) -> Envelope:
        """Write the property an object-map alias points to (see list_objects).

        This changes physical equipment and is refused unless the server was
        started with BACNET_WRITES_ENABLED=true. The command priority comes
        from the alias's "priority" entry, if any.

        Args:
            alias: An alias from the object map.
            value: New value (see write_property); null (or the string "null")
                relinquishes the alias's priority slot.
        """
        return await operations.write_object_by_alias(alias, value)

    @register(LOCAL_ONLY)
    async def ping() -> Envelope:
        """Report the server's BACnet/IP settings and state; sends nothing.

        Shows the bound interface and port, the broadcast address (if any),
        the server's own device instance, whether writes are enabled, the
        devices whose addresses are known, and the object map status.
        """
        return await operations.ping()

    from .contract import install_contract

    install_contract(server)
