"""Bounded object inventory uses the normal read port and keeps partial progress."""

import asyncio
import json

from helpers import MOCK_DEVICE_ID, client_config, mock_address, running_mock
from mcp.shared.memory import create_connected_server_and_client_session
from test_tools import FakeClient, call, make_server, with_session

from bacnet_mcp.bacnet_client import BACnetClient, BACnetClientError


class InventoryClient(FakeClient):
    def __init__(self, *, failure=None, delay=0):
        super().__init__()
        self.failure, self.delay = failure, delay

    async def read_property(self, *args, **kwargs):
        self.calls.append(kwargs)
        index = kwargs["array_index"]
        if index == self.failure:
            raise BACnetClientError("read failed")
        if index and self.delay:
            await asyncio.sleep(self.delay)
        return (3 if index == 0 else f"analog-input,{index}"), {"address": "127.0.0.1:47809"}


def browse(client, **args):
    async def scenario(session):
        return await call(session, "browse_objects", {"device_id": 1, **args})

    return with_session(make_server(client), scenario)


def test_page_is_bounded_and_resumes_without_rereading_entries():
    client = InventoryClient()
    page = browse(client, limit=2)
    assert page["success"] and page["meta"]["next_index"] == 3
    assert [c["array_index"] for c in client.calls] == [0, 1, 2]
    assert page["meta"]["complete"] is False
    next_page = browse(client, start_index=3, limit=2)
    assert next_page["meta"]["complete"] is True and next_page["meta"]["next_index"] is None
    assert [x["array_index"] for x in next_page["data"]["objects"]] == [3]


def test_failure_retains_completed_entries_and_retry_position():
    result = browse(InventoryClient(failure=2))
    assert result["success"] is False and result["meta"]["next_index"] == 2
    assert [x["array_index"] for x in result["data"]["objects"]] == [1]


def test_whole_page_deadline_keeps_resume_position():
    result = browse(InventoryClient(delay=0.2), timeout_ms=100)
    assert result["success"] is False and result["meta"]["next_index"] == 1
    assert result["data"]["objects"] == []


def test_invalid_page_never_reaches_client():
    for args in ({"limit": 101}, {"start_index": 0}, {"timeout_ms": -1}):
        client = InventoryClient()
        assert browse(client, **args)["success"] is False
        assert client.calls == []


def test_real_bacnet_object_list_over_udp():
    async def scenario():
        async with running_mock() as mock:
            client = BACnetClient(client_config())
            async with create_connected_server_and_client_session(
                make_server(client).mcp
            ) as session:
                result = await call(
                    session,
                    "browse_objects",
                    {"device_id": MOCK_DEVICE_ID, "address": mock_address(mock), "limit": 2},
                )
                assert result["success"], result
                assert len(result["data"]["objects"]) == 2
                assert result["data"]["total_objects"] > 2
                assert result["meta"]["next_index"] == 3

    asyncio.run(scenario())


def test_unexpected_failure_redacts_logs_and_response(caplog):
    async def scenario(session):
        return await call(
            session,
            "read_property",
            {"device_id": 1, "object_type": "analog-input", "object_instance": 1},
        )

    result = with_session(
        make_server(FakeClient(error=RuntimeError("secret-payload-123"))), scenario
    )
    assert "secret-payload-123" not in json.dumps(result) + caplog.text
