"""Describe implemented backend operations, without probing devices."""


def capabilities(writes_enabled: bool) -> dict:
    names = (
        "read_property",
        "write_property",
        "browse_objects",
        "discover_devices",
        "list_objects",
        "ping",
    )
    return {
        "protocol": "bacnet",
        "backend": "bacpypes3",
        "simulated": False,
        "scope": "backend_support",
        "device_verified": False,
        "operations": {
            name: {
                "supported": True,
                "enabled": writes_enabled if name == "write_property" else True,
            }
            for name in names
        },
        "prerequisites": [
            "Available local IPv4 UDP bind address",
            "Reachable BACnet/IP device implementing the requested service",
        ],
        "limitations": [
            "BACnet/IP only; no BACnet/SC or direct MS/TP transport",
            "No COV subscriptions, alarm acknowledgement, or history tools",
            "Discovery and object-list browsing are bounded observations, not snapshots",
            "enabled describes native configuration, not MCP policy or device permission",
        ],
    }
