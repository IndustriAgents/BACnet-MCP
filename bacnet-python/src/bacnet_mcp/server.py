"""FastMCP server wiring for the BACnet tools."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import TextIOWrapper
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from .bacnet_client import BACnetClient
from .tools import ObjectMap, ToolConfig, ToolResources, fail, register_tools


@dataclass(slots=True)
class AppContext:
    client: BACnetClient


def describe_validation_error(exc: ValidationError) -> str:
    problems = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error.get("loc", ())) or "arguments"
        problems.append(f"{where}: {error.get('msg', 'invalid')}")
    return "Invalid arguments: " + "; ".join(problems)


class _BACnetFastMCP(FastMCP):
    """FastMCP with two guarantees on top.

    - Arguments that fail FastMCP's type validation come back as the usual
      ``{success: false, ...}`` envelope, like every other error, instead of
      as an MCP error result.
    - On stdio, stdout carries nothing but JSON-RPC: the transport owns the
      process's real stdout and, while the server runs, ``sys.stdout`` points
      at stderr, so a stray ``print()`` from this package or a library cannot
      corrupt the protocol stream.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            tool = self._tool_manager.get_tool(name)
            if tool is None or not isinstance(exc.__cause__, ValidationError):
                raise
            result = fail(describe_validation_error(exc.__cause__), {"tool": name})
            return tool.fn_metadata.convert_result(result)

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
        self._server = _BACnetFastMCP(
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
