"""FastMCP server wiring for the BACnet tools."""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.metadata import version as package_version
from io import TextIOWrapper
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from .bacnet_client import NOT_SENT, BACnetClient
from .tools import ObjectMap, ToolConfig, ToolResources, fail, register_tools, write_outcome


@dataclass(slots=True)
class AppContext:
    client: BACnetClient


log = logging.getLogger(__name__)

#: Errors from writing a reply to a client that has disconnected.
_UNDELIVERABLE = (
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
    BrokenPipeError,
    ConnectionResetError,
)


def _refusal_meta(tool: Any) -> dict[str, Any]:
    """Meta for arguments refused before the tool ran; writes say nothing was sent."""
    meta: dict[str, Any] = {"tool": tool.name}
    if tool.annotations is not None and tool.annotations.destructiveHint:
        meta.update(write_outcome(NOT_SENT, False))
    return meta


def describe_validation_error(exc: ValidationError) -> str:
    problems = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error.get("loc", ())) or "arguments"
        problems.append(f"{where}: {error.get('msg', 'invalid')}")
    return "Invalid arguments: " + "; ".join(problems)


class _BACnetFastMCP(FastMCP):
    """FastMCP with two guarantees on top.

    - Arguments that fail FastMCP's type validation, or that the tool does not
      take at all, come back as the usual ``{success: false, ...}`` envelope,
      like every other error, instead of being an MCP error or ignored.
    - On stdio, stdout carries nothing but JSON-RPC: the transport owns the
      process's real stdout and, while the server runs, ``sys.stdout`` points
      at stderr, so a stray ``print()`` from this package or a library cannot
      corrupt the protocol stream.
    """

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tool_manager.get_tool(name)
        if tool is not None:
            # FastMCP drops arguments it does not know; a misspelt "priorty"
            # would turn a priority-8 write into one without a priority.
            known = set(tool.parameters.get("properties", {}))
            unknown = sorted(set(arguments or {}) - known)
            if unknown:
                result = fail(
                    f"Unknown argument(s): {', '.join(unknown)}; "
                    f"{name} takes {', '.join(sorted(known)) or 'no arguments'}",
                    _refusal_meta(tool),
                )
                return tool.fn_metadata.convert_result(result)
        try:
            return await super().call_tool(name, arguments)
        except ToolError as exc:
            tool = self._tool_manager.get_tool(name)
            if tool is None or not isinstance(exc.__cause__, ValidationError):
                raise
            result = fail(describe_validation_error(exc.__cause__), _refusal_meta(tool))
            return tool.fn_metadata.convert_result(result)

    #: Called once when the client's stdin reaches end of file.
    on_client_disconnect: Callable[[], None] | None = None
    #: True once the client's stdin has reached end of file.
    client_gone: bool = False

    async def run_stdio_async(self) -> None:
        """Serve MCP on stdio; stop pending device requests when the client leaves.

        FastMCP stops reading at end of file but lets in-flight tool calls run
        on, so a write still checking its target would be sent after the
        client had gone. The client's messages are relayed through a stream
        whose end calls ``on_client_disconnect`` first.
        """
        protocol_out = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))
        saved_stdout, sys.stdout = sys.stdout, sys.stderr
        self.client_gone = False
        try:
            async with stdio_server(stdout=protocol_out) as (read_stream, write_stream):
                relay_send, relay_receive = anyio.create_memory_object_stream(0)
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._relay_until_eof, read_stream, relay_send)
                    await self._mcp_server.run(
                        relay_receive,
                        write_stream,
                        self._mcp_server.create_initialization_options(),
                    )
                    tg.cancel_scope.cancel()
        except BaseExceptionGroup as group:
            # Replies to calls that were still running cannot be delivered once
            # the client is gone: that is a normal end, not a crash.
            _, rest = group.split(_UNDELIVERABLE)
            if not self.client_gone or rest is not None:
                raise
            log.info("dropped replies for the disconnected MCP client")
        finally:
            sys.stdout = saved_stdout

    async def _relay_until_eof(self, source: Any, sink: Any) -> None:
        try:
            async with sink:
                async for message in source:
                    await sink.send(message)
        finally:
            self.client_gone = True
            log.info("MCP client disconnected; stopping pending device requests")
            if self.on_client_disconnect is not None:
                self.on_client_disconnect()


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
        # serverInfo.version: this package's version, not the mcp SDK's default.
        self._server._mcp_server.version = package_version("bacnet-mcp")
        self._server.on_client_disconnect = getattr(self.client, "client_disconnected", None)
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
