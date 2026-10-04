"""FastMCP server wiring for the BACnet tools."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import TextIOWrapper

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server

from .bacnet_client import BACnetClient
from .tools import ObjectMap, ToolConfig, ToolResources, register_tools


@dataclass(slots=True)
class AppContext:
    client: BACnetClient


class _StdioFastMCP(FastMCP):
    """FastMCP whose stdout carries nothing but JSON-RPC.

    The MCP stdio transport owns the process's real stdout. While the server
    runs, ``sys.stdout`` points at stderr, so a stray ``print()`` from this
    package or from a library cannot corrupt the protocol stream.
    """

    async def run_stdio_async(self) -> None:
        protocol_out = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
        saved_stdout, sys.stdout = sys.stdout, sys.stderr
        try:
            async with stdio_server(stdout=protocol_out) as (read_stream, write_stream):
                await self._mcp_server.run(
                    read_stream,
                    write_stream,
                    self._mcp_server.create_initialization_options(),
                )
        finally:
            sys.stdout = saved_stdout


class BACnetMCPServer:
    """The BACnet MCP server: configuration, BACnet/IP client and tools.

    Configuration is read from the environment when the server is built, so
    build it after loading ``.env`` (the CLI does).
    """

    def __init__(
        self,
        client: BACnetClient | None = None,
        tool_config: ToolConfig | None = None,
    ) -> None:
        self.client = client or BACnetClient()
        self.tool_config = tool_config or ToolConfig.from_env()
        self.object_map = ObjectMap(self.tool_config.object_map_path)
        self._server = _StdioFastMCP(
            name="BACnet MCP Server",
            instructions=(
                "Tools for BACnet/IP building automation devices. Use discover_devices "
                "to find devices (Who-Is/I-Am), read_property to read object properties "
                "(ReadProperty) and write_property to change them (WriteProperty). "
                "Writes move physical equipment and are refused unless the server was "
                "started with BACNET_WRITES_ENABLED=true. Every tool returns "
                "{success, data, error, meta}."
            ),
            lifespan=self._lifespan,
        )
        resources = ToolResources(
            client=self.client,
            config=self.tool_config,
            object_map=self.object_map,
        )
        register_tools(self._server, resources)

    @property
    def mcp(self) -> FastMCP:
        return self._server

    def run(self) -> None:
        self._server.run()

    @asynccontextmanager
    async def _lifespan(self, server: FastMCP) -> AsyncIterator[AppContext]:  # noqa: ARG002
        await self.client.open()
        try:
            yield AppContext(client=self.client)
        finally:
            await self.client.close()
