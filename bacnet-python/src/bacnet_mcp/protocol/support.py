from __future__ import annotations

from mcp.server.fastmcp import FastMCP as FastMCP
from mcp.types import ToolAnnotations as ToolAnnotations

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
LOCAL_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITES_DEVICE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
)
