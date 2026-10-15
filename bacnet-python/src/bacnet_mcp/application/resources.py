from __future__ import annotations

from dataclasses import dataclass

from bacnet_mcp.domain.ports import BACnetClientPort
from bacnet_mcp.infrastructure.maps import ObjectMap
from bacnet_mcp.infrastructure.tool_config import ToolConfig


@dataclass(slots=True)
class ToolResources:
    client: BACnetClientPort
    config: ToolConfig
    object_map: ObjectMap
