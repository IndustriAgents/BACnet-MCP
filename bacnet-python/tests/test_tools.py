"""The MCP tool layer, through an in-memory MCP client session."""

from __future__ import annotations

import asyncio
import json
import math
from importlib.metadata import version
from typing import Any

import anyio
import pytest
from bacpypes3.apdu import AbortPDU
from bacpypes3.primitivedata import Real
from helpers import (
    MOCK_DEVICE_ID,
    client_config,
    lose_write_acks,
    mock_address,
    mock_object,
    running_mock,
)
from mcp import ClientSession
from mcp.shared.memory import (
    create_client_server_memory_streams,
    create_connected_server_and_client_session,
)

from bacnet_mcp.bacnet_client import BACnetClient, BACnetClientError, BACnetConfig, WriteError
from bacnet_mcp.server import BACnetMCPServer
from bacnet_mcp.tools import ToolConfig

TOOLS = [
    "discover_devices",
    "list_objects",
    "ping",
    "read_object_by_alias",
    "read_property",
    "write_object_by_alias",
    "write_property",
]


class FakeClient:
    """Records what the tools ask the BACnet client to do."""

    def __init__(self, writes_enabled: bool = False, error: BaseException | None = None):
        self.config = BACnetConfig(writes_enabled=writes_enabled)
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.opened = False

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.opened = False

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error

    async def discover_devices(
        self, timeout_ms, address=None, low_limit=None, high_limit=None, include_details=True
    ):
        self._record(
            "discover",
            timeout_ms=timeout_ms,
            address=address,
            low_limit=low_limit,
            high_limit=high_limit,
            include_details=include_details,
        )
        return [{"device_id": 1, "address": "127.0.0.1:47809"}], {"count": 1}

    async def read_property(
        self,
        device_id,
        object_type,
        object_instance,
        property_id="present-value",
        array_index=None,
        address=None,
    ):
        self._record(
            "read",
            device_id=device_id,
            object_type=object_type,
            object_instance=object_instance,
            property_id=property_id,
            array_index=array_index,
            address=address,
        )
        return 21.5, {"address": address or "127.0.0.1:47809"}

    async def write_property(
        self,
        device_id,
        object_type,
        object_instance,
        property_id,
        value,
        priority=None,
        array_index=None,
        address=None,
    ):
        self._record(
            "write",
            device_id=device_id,
            object_type=object_type,
            object_instance=object_instance,
            property_id=property_id,
            value=value,
            priority=priority,
            array_index=array_index,
            address=address,
        )
        return {
            "address": address or "127.0.0.1:47809",
            "priority": priority,
            "outcome": "written",
            "request_sent": True,
        }

    def connection_status(self) -> dict[str, Any]:
        return {"running": self.opened, "writes_enabled": self.config.writes_enabled}


async def call(session, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    assert not result.isError, result
    envelope = json.loads(result.content[0].text)
    assert set(envelope) == {"success", "data", "error", "meta"}
    assert result.structuredContent == envelope
    return envelope


def with_session(server: BACnetMCPServer, scenario) -> Any:
    async def main():
        async with create_connected_server_and_client_session(server.mcp) as session:
            return await scenario(session)

    return asyncio.run(main())


def make_server(client=None, object_map=None) -> BACnetMCPServer:
    return BACnetMCPServer(
        client=client or FakeClient(),
        tool_config=ToolConfig(object_map_path=object_map),
    )


def test_every_tool_is_listed_with_a_description():
    async def scenario(session):
        return (await session.list_tools()).tools

    tools = with_session(make_server(), scenario)
    assert sorted(t.name for t in tools) == TOOLS
    for tool in tools:
        assert tool.description and len(tool.description) > 40, tool.name
        assert tool.annotations is not None, tool.name
    by_name = {t.name: t for t in tools}
    for name in ("write_property", "write_object_by_alias"):
        assert by_name[name].annotations.destructiveHint is True
        assert by_name[name].annotations.idempotentHint is False  # never invite a retry
        assert by_name[name].annotations.readOnlyHint is False
    for name in ("discover_devices", "read_property", "read_object_by_alias", "list_objects"):
        assert by_name[name].annotations.readOnlyHint is True
    assert by_name["read_property"].annotations.readOnlyHint is True
    required = set(by_name["read_property"].inputSchema["required"])
    assert required == {"device_id", "object_type", "object_instance"}


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (
            "write_property",
            {
                "device_id": 1,
                "object_type": "analog-output",
                "object_instance": 1,
                "property_id": "present-value",
                "value": 1,
            },
        ),
        ("write_object_by_alias", {"alias": "damper", "value": 1}),
    ],
)
def test_writes_are_refused_by_default(tool, arguments):
    client = FakeClient()

    async def scenario(session):
        return await call(session, tool, arguments)

    envelope = with_session(make_server(client), scenario)
    assert envelope["success"] is False
    assert "BACNET_WRITES_ENABLED=true" in envelope["error"]
    assert (envelope["meta"]["outcome"], envelope["meta"]["request_sent"]) == ("not_sent", False)
    assert client.calls == []


READ = {"device_id": 12345, "object_type": "analog-input", "object_instance": 1}
WRITE = {**READ, "object_type": "analog-output", "property_id": "present-value", "value": 1}


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("read_property", {**READ, "device_id": "abc"}, "Invalid arguments: device_id"),
        ("read_property", {"object_type": "analog-input"}, "Field required"),
        ("read_property", {**READ, "device_id": -1}, "device_id must be between"),
        ("read_property", {**READ, "device_id": 4194303}, "device_id must be between"),
        ("read_property", {**READ, "object_instance": 4194304}, "object_instance"),
        ("read_property", {**READ, "object_type": " "}, "object_type must be"),
        ("read_property", {**READ, "property_id": ""}, "property_id must be"),
        ("read_property", {**READ, "array_index": -1}, "array_index"),
        ("read_property", {**READ, "address": ""}, "address must be"),
        ("discover_devices", {"timeout_ms": 50}, "timeout_ms must be between"),
        ("discover_devices", {"timeout_ms": 600000}, "timeout_ms must be between"),
        ("discover_devices", {"low_limit": 5}, "both low_limit and high_limit"),
        ("discover_devices", {"low_limit": 9, "high_limit": 5}, "low_limit must not"),
        ("discover_devices", {"low_limit": 0, "high_limit": 5000000}, "high_limit"),
        ("write_property", {**WRITE, "priority": 0}, "priority must be between"),
        ("write_property", {**WRITE, "priority": 17}, "priority must be between"),
        ("write_property", {**WRITE, "priority": 6}, "priority 6 is reserved"),
        ("write_property", {**WRITE, "value": None}, "give a priority"),
        ("write_property", {**WRITE, "value": [1, 2]}, "Invalid arguments: value"),
        ("write_property", {**WRITE, "object_instance": "one"}, "Invalid arguments"),
        ("read_object_by_alias", {"alias": " "}, "alias must be"),
        ("read_object_by_alias", {"alias": "nope"}, "Unknown alias"),
    ],
)
def test_bad_arguments_come_back_as_envelopes(tool, arguments, message):
    client = FakeClient(writes_enabled=True)

    async def scenario(session):
        return await call(session, tool, arguments)

    envelope = with_session(make_server(client), scenario)
    assert envelope["success"] is False
    assert message in envelope["error"]
    assert envelope["meta"]["tool"] == tool
    if tool.startswith("write"):
        assert envelope["meta"]["outcome"] == "not_sent"
        assert envelope["meta"]["request_sent"] is False
    else:
        assert "outcome" not in envelope["meta"]
    assert client.calls == []


def test_valid_calls_reach_the_client():
    client = FakeClient(writes_enabled=True)

    async def scenario(session):
        read = await call(
            session, "read_property", {**READ, "property_id": "units", "address": "10.0.0.5"}
        )
        write = await call(session, "write_property", {**WRITE, "value": 42.5, "priority": 8})
        relinquish = await call(session, "write_property", {**WRITE, "value": None, "priority": 8})
        found = await call(session, "discover_devices", {"address": "127.0.0.1:47809"})
        return read, write, relinquish, found

    read, write, relinquish, found = with_session(make_server(client), scenario)
    assert read["success"] and read["data"]["value"] == 21.5
    assert read["data"]["property_id"] == "units"
    assert write["success"] and write["data"]["acknowledged"] is True
    assert (write["meta"]["outcome"], write["meta"]["request_sent"]) == ("written", True)
    assert relinquish["success"] and relinquish["data"]["value"] is None
    assert found["data"]["devices"][0]["device_id"] == 1
    assert [name for name, _ in client.calls] == ["read", "write", "write", "discover"]
    assert client.calls[0][1]["address"] == "10.0.0.5"
    assert client.calls[1][1] == {
        "device_id": 12345,
        "object_type": "analog-output",
        "object_instance": 1,
        "property_id": "present-value",
        "value": 42.5,
        "priority": 8,
        "array_index": None,
        "address": None,
    }
    assert client.calls[3][1]["timeout_ms"] == 3000


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (BACnetClientError("BACnet error: object: unknown-object"), "unknown-object"),
        (RuntimeError("boom"), "RuntimeError: boom"),
        # bacpypes3 PDUs are BaseExceptions; one must never escape a tool
        (AbortPDU(reason="noResponse"), "BACnet failure: no-response"),
    ],
)
def test_client_failures_come_back_as_envelopes(error, message):
    async def scenario(session):
        return await call(session, "read_property", READ)

    envelope = with_session(make_server(FakeClient(error=error)), scenario)
    assert envelope["success"] is False
    assert message in envelope["error"]


def test_ping_reports_one_write_switch():
    async def scenario(session):
        return await call(session, "ping", {})

    for enabled in (False, True):
        envelope = with_session(make_server(FakeClient(writes_enabled=enabled)), scenario)
        assert envelope["data"]["writes_enabled"] is enabled
        assert envelope["data"]["connection"]["writes_enabled"] is enabled
        assert envelope["data"]["connection"]["running"] is True


def write_map(tmp_path, content) -> Any:
    path = tmp_path / "objects.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content), "utf-8")
    return path


OBJECT_MAP = {
    "zone1_temp": {
        "device": 12345,
        "address": "127.0.0.1:47809",
        "object_type": "analog-input",
        "object_instance": 1,
        "description": "Zone 1 temperature",
    },
    "damper": {
        "device": 12345,
        "object_type": "analog-output",
        "object_instance": 1,
        "priority": 8,
    },
    "no_device": {"object_type": "analog-input", "object_instance": 1},
    "bad_priority": {
        "device": 1,
        "object_type": "analog-output",
        "object_instance": 1,
        "priority": 20,
    },
    "not_an_object": 5,
}


def test_object_map_aliases(tmp_path):
    client = FakeClient(writes_enabled=True)
    server = make_server(client, write_map(tmp_path, OBJECT_MAP))

    async def scenario(session):
        listed = await call(session, "list_objects", {})
        read = await call(session, "read_object_by_alias", {"alias": "zone1_temp"})
        write = await call(session, "write_object_by_alias", {"alias": "damper", "value": 30})
        missing = await call(session, "read_object_by_alias", {"alias": "no_device"})
        bad = await call(session, "write_object_by_alias", {"alias": "bad_priority", "value": 1})
        scalar = await call(session, "read_object_by_alias", {"alias": "not_an_object"})
        return listed, read, write, missing, bad, scalar

    listed, read, write, missing, bad, scalar = with_session(server, scenario)
    assert listed["data"]["count"] == len(OBJECT_MAP)
    assert {o["alias"] for o in listed["data"]["objects"]} == set(OBJECT_MAP)
    assert read["success"] and read["data"]["alias"] == "zone1_temp"
    assert write["success"] and write["data"]["priority"] == 8
    # Required keys are never defaulted (they used to fall back to device 0).
    assert missing["success"] is False and "missing device" in missing["error"]
    assert bad["success"] is False and "priority" in bad["error"]
    assert scalar["success"] is False and "must be a JSON object" in scalar["error"]
    assert client.calls == [
        (
            "read",
            {
                "device_id": 12345,
                "object_type": "analog-input",
                "object_instance": 1,
                "property_id": "present-value",
                "array_index": None,
                "address": "127.0.0.1:47809",
            },
        ),
        (
            "write",
            {
                "device_id": 12345,
                "object_type": "analog-output",
                "object_instance": 1,
                "property_id": "present-value",
                "value": 30,
                "priority": 8,
                "array_index": None,
                "address": None,
            },
        ),
    ]


@pytest.mark.parametrize(
    ("content", "message"),
    [("{not json", "not valid JSON"), ("[1, 2]", "must be a JSON object keyed by alias")],
)
def test_broken_object_map_is_reported(tmp_path, content, message):
    server = make_server(object_map=write_map(tmp_path, content))

    async def scenario(session):
        return await call(session, "list_objects", {}), await call(session, "ping", {})

    listed, ping = with_session(server, scenario)
    assert listed["success"] is False and message in listed["error"]
    assert message in ping["data"]["object_map_error"]


def test_missing_object_map_is_reported(tmp_path):
    server = make_server(object_map=tmp_path / "absent.json")

    async def scenario(session):
        return await call(session, "list_objects", {})

    envelope = with_session(server, scenario)
    assert envelope["success"] is False and "cannot read object map" in envelope["error"]


def test_tools_against_the_mock_in_process():
    """The real client behind the MCP layer, talking BACnet/IP to the mock."""

    async def main():
        async with running_mock() as mock:
            server = BACnetMCPServer(
                client=BACnetClient(client_config(writes_enabled=True)),
                tool_config=ToolConfig(),
            )
            addr = mock_address(mock)
            async with create_connected_server_and_client_session(server.mcp) as session:
                found = await call(session, "discover_devices", {"address": addr})
                read = await call(
                    session,
                    "read_property",
                    {
                        "device_id": MOCK_DEVICE_ID,
                        "object_type": "analog-input",
                        "object_instance": 2,
                    },
                )
                write = await call(
                    session,
                    "write_property",
                    {
                        "device_id": MOCK_DEVICE_ID,
                        "object_type": "analog-output",
                        "object_instance": 1,
                        "property_id": "present-value",
                        "value": 12,
                        "priority": 8,
                    },
                )
                written = float(mock_object(mock, "analog-output", 1).presentValue)
                denied = await call(
                    session,
                    "write_property",
                    {
                        "device_id": MOCK_DEVICE_ID,
                        "object_type": "analog-input",
                        "object_instance": 1,
                        "property_id": "present-value",
                        "value": 1,
                    },
                )
            return found, read, write, written, denied

    found, read, write, written, denied = asyncio.run(main())
    assert found["data"]["devices"][0]["vendor"] == "MockVendor"
    assert read["data"]["value"] == pytest.approx(68.0)
    assert write["success"] is True and written == 12.0
    assert denied["success"] is False and "write-access-denied" in denied["error"]


def test_descriptions_are_dedented():
    async def scenario(session):
        return (await session.list_tools()).tools

    for tool in with_session(make_server(), scenario):
        assert tool.description == tool.description.strip()
        body = [line for line in tool.description.splitlines()[1:] if line.strip()]
        assert min(len(line) - len(line.lstrip()) for line in body) == 0, tool.name


@pytest.mark.parametrize(
    ("tool", "arguments", "unknown"),
    [
        ("write_property", {**WRITE, "value": 12, "priorty": 8}, "priorty"),
        ("read_property", {**READ, "adress": "10.0.0.5"}, "adress"),
        ("ping", {"verbose": True}, "verbose"),
    ],
)
def test_unknown_arguments_are_refused_not_ignored(tool, arguments, unknown):
    # A misspelt "priorty" used to be dropped silently: the write went out
    # without a priority and reported success.
    client = FakeClient(writes_enabled=True)

    async def scenario(session):
        return await call(session, tool, arguments)

    envelope = with_session(make_server(client), scenario)
    assert envelope["success"] is False
    assert f"Unknown argument(s): {unknown}" in envelope["error"]
    assert client.calls == []


def test_the_string_null_relinquishes_like_null():
    # The MCP SDK turns the JSON string "null" into null before validation;
    # the tool descriptions say so.
    client = FakeClient(writes_enabled=True)

    async def scenario(session):
        return await call(session, "write_property", {**WRITE, "value": "null", "priority": 16})

    envelope = with_session(make_server(client), scenario)
    assert envelope["success"] is True
    assert client.calls[0][1]["value"] is None


def test_object_map_that_is_a_directory_is_reported_as_unreadable(tmp_path):
    server = make_server(object_map=tmp_path)

    async def scenario(session):
        return await call(session, "list_objects", {})

    envelope = with_session(server, scenario)
    assert envelope["success"] is False
    assert "cannot read object map" in envelope["error"]


def test_non_finite_values_survive_strict_json():
    """NaN/Infinity from a faulted sensor must not become null or invalid JSON."""

    def reject(constant):
        raise ValueError(f"non-standard JSON constant {constant}")

    async def main():
        async with running_mock() as mock:
            mock_object(mock, "analog-input", 1).presentValue = Real(math.inf)
            server = BACnetMCPServer(client=BACnetClient(client_config()), tool_config=ToolConfig())
            async with create_connected_server_and_client_session(server.mcp) as session:
                result = await session.call_tool(
                    "read_property",
                    {
                        "device_id": MOCK_DEVICE_ID,
                        "object_type": "analog-input",
                        "object_instance": 1,
                        "address": mock_address(mock),
                    },
                )
        return json.loads(result.content[0].text, parse_constant=reject)

    envelope = asyncio.run(main())
    assert envelope["success"] is True
    assert envelope["data"]["value"] == "Infinity"


@pytest.mark.parametrize(
    ("error", "outcome", "sent"),
    [
        (
            WriteError("BACnet error: property: write-access-denied", "rejected", True),
            "rejected",
            True,
        ),
        (WriteError("no reply: the write may have been applied", "unknown", True), "unknown", True),
        (BACnetClientError("no device 1 answered"), "not_sent", False),
        (RuntimeError("boom"), "unknown", True),
        (AbortPDU(reason="noResponse"), "unknown", True),
    ],
)
def test_write_failures_report_the_outcome(error, outcome, sent):
    client = FakeClient(writes_enabled=True, error=error)

    async def scenario(session):
        return await call(session, "write_property", {**WRITE, "priority": 8})

    envelope = with_session(make_server(client), scenario)
    assert envelope["success"] is False
    assert (envelope["meta"]["outcome"], envelope["meta"]["request_sent"]) == (outcome, sent)
    if outcome == "unknown":
        assert "may have been applied" in envelope["error"]


def test_unknown_arguments_to_a_write_say_nothing_was_sent():
    async def scenario(session):
        return await call(session, "write_object_by_alias", {"alias": "x", "value": 1, "p": 8})

    envelope = with_session(make_server(FakeClient(writes_enabled=True)), scenario)
    assert envelope["success"] is False
    assert (envelope["meta"]["outcome"], envelope["meta"]["request_sent"]) == ("not_sent", False)


def test_a_lost_ack_through_the_tool_is_unknown_and_sent_once():
    async def main():
        async with running_mock() as mock:
            server = BACnetMCPServer(
                client=BACnetClient(client_config(apdu_retries=3)), tool_config=ToolConfig()
            )
            async with create_connected_server_and_client_session(server.mcp) as session:
                await call(
                    session,
                    "read_property",
                    {
                        "device_id": MOCK_DEVICE_ID,
                        "object_type": "analog-input",
                        "object_instance": 1,
                        "address": mock_address(mock),
                    },
                )
                writes = lose_write_acks(mock)
                envelope = await call(
                    session,
                    "write_property",
                    {
                        "device_id": MOCK_DEVICE_ID,
                        "object_type": "analog-output",
                        "object_instance": 1,
                        "property_id": "present-value",
                        "value": 21,
                        "priority": 8,
                    },
                )
            return envelope, len(writes), float(mock_object(mock, "analog-output", 1).presentValue)

    envelope, count, value = asyncio.run(main())
    assert envelope["success"] is False
    assert envelope["meta"]["outcome"] == "unknown"
    assert envelope["meta"]["request_sent"] is True
    assert "the write may have been applied; read it back before retrying" in envelope["error"]
    assert count == 1
    assert value == 21.0


@pytest.mark.parametrize(
    ("tool", "arguments", "field"),
    [
        ("read_property", {**READ, "device_id": True}, "device_id"),
        ("read_property", {**READ, "device_id": "12345"}, "device_id"),
        ("read_property", {**READ, "object_instance": False}, "object_instance"),
        ("read_property", {**READ, "object_instance": 1.0}, "object_instance"),
        ("read_property", {**READ, "array_index": True}, "array_index"),
        ("write_property", {**WRITE, "priority": True}, "priority"),
        ("write_property", {**WRITE, "device_id": True}, "device_id"),
        ("discover_devices", {"timeout_ms": True}, "timeout_ms"),
        ("discover_devices", {"low_limit": True, "high_limit": 5}, "low_limit"),
        ("discover_devices", {"include_details": 1}, "include_details"),
    ],
)
def test_booleans_and_numeric_strings_are_not_integers(tool, arguments, field):
    client = FakeClient(writes_enabled=True)

    async def scenario(session):
        return await call(session, tool, arguments)

    envelope = with_session(make_server(client), scenario)
    assert envelope["success"] is False
    assert f"Invalid arguments: {field}" in envelope["error"]
    assert client.calls == []


def test_server_info_reports_this_packages_version():
    server = make_server()

    async def main():
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            lowlevel = server.mcp._mcp_server
            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    lambda: lowlevel.run(*server_streams, lowlevel.create_initialization_options())
                )
                async with ClientSession(*client_streams) as session:
                    result = await session.initialize()
                tg.cancel_scope.cancel()
        return result

    result = asyncio.run(main())
    assert result.serverInfo.name == "BACnet MCP Server"
    assert result.serverInfo.version == version("bacnet-mcp")
    assert result.serverInfo.version != version("mcp")


def test_object_map_aliases_reject_booleans(tmp_path):
    client = FakeClient()
    entry = {"device": True, "object_type": "analog-input", "object_instance": 1}
    server = make_server(client, write_map(tmp_path, {"flag": entry}))

    async def scenario(session):
        return await call(session, "read_object_by_alias", {"alias": "flag"})

    envelope = with_session(server, scenario)
    assert envelope["success"] is False and "device_id must be an integer" in envelope["error"]
    assert client.calls == []


def test_tool_calls_see_the_mcp_requests_cancel_scope():
    """The write path's cancel_called check relies on private mcp<2 internals
    (session._in_flight[request_id]._cancel_scope). Fail loudly if they move."""
    from bacnet_mcp.bacnet_client import request_cancel_scope

    server = make_server()
    seen = {}

    @server.mcp.tool()
    async def probe() -> dict:
        """Report the request cancel scope the server captured."""
        scope = request_cancel_scope.get()
        seen["scope"] = scope
        return {"success": True, "data": None, "error": None, "meta": {}}

    async def scenario(session):
        return await session.call_tool("probe", {})

    with_session(server, scenario)
    assert isinstance(seen["scope"], anyio.CancelScope)
    assert seen["scope"].cancel_called is False
    assert request_cancel_scope.get() is None  # reset after the call
