from __future__ import annotations

from dataclasses import dataclass

from bacnet_mcp.domain.ports import BACnetOperationsPort
from bacnet_mcp.infrastructure.maps import ObjectMap
from bacnet_mcp.infrastructure.tool_config import ToolConfig


@dataclass(slots=True)
class ToolResources:
    client: BACnetOperationsPort
    config: ToolConfig
    object_map: ObjectMap
