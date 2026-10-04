"""End to end: the mock device and the real MCP server, each in its own process.

The mock runs as a BACnet/IP device on a free 127.0.0.1 UDP port and the
server is driven over stdio, exactly as an MCP client runs it: once through
the mcp client SDK and once with raw JSON-RPC lines to check that stdout
carries nothing else (known issue 1).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import anyio
import pytest
from helpers import MOCK_DEVICE_ID, MOCK_SCRIPT, free_udp_port
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_COMMAND = [sys.executable, "-m", "bacnet_mcp.cli"]


@contextmanager
def mock_process() -> Iterator[str]:
    """Run the mock device; yield its address once it is listening."""
    port = free_udp_port()
    proc = subprocess.Popen(
        [sys.executable, str(MOCK_SCRIPT), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        assert proc.stderr is not None
        while time.monotonic() < deadline:
            line = proc.stderr.readline()
            if "listening on" in line:
                break
            if not line and proc.poll() is not None:
                pytest.fail(f"mock device exited with {proc.returncode}")
        else:
            pytest.fail("mock device did not start")
        yield f"127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if proc.stderr is not None:
            proc.stderr.close()


def server_env(writes: bool) -> dict[str, str]:
    return {
        "BACNET_INTERFACE": "127.0.0.1",
        "BACNET_PORT": str(free_udp_port()),
        "BACNET_WRITES_ENABLED": "true" if writes else "false",
        "BACNET_APDU_TIMEOUT_MS": "1000",
        "BACNET_APDU_RETRIES": "1",
    }


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    assert not result.isError, result
    return json.loads(result.content[0].text)


def target(object_type: str, instance: int, **extra: Any) -> dict[str, Any]:
    return {
        "device_id": MOCK_DEVICE_ID,
        "object_type": object_type,
        "object_instance": instance,
        **extra,
    }


async def sdk_session(env: dict[str, str], scenario) -> Any:
    params = StdioServerParameters(
        command=SERVER_COMMAND[0], args=SERVER_COMMAND[1:], env=env, cwd=os.getcwd()
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await scenario(session)


def test_mcp_sdk_session_against_the_mock(caplog):
    with mock_process() as address:

        async def scenario(session: ClientSession):
            tools = (await session.list_tools()).tools
            assert len(tools) == 7 and all(t.description for t in tools)

            found = await call(session, "discover_devices", {"address": address})
            assert found["success"], found
            (device,) = found["data"]["devices"]
            assert device["device_id"] == MOCK_DEVICE_ID
            assert (device["vendor"], device["model"]) == ("MockVendor", "TestController")

            temp = await call(session, "read_property", target("analog-input", 1))
            assert temp["success"], temp
            assert 72.5 - 5.01 <= temp["data"]["value"] <= 72.5 + 5.01

            ao = target("analog-output", 1, property_id="present-value")
            written = await call(session, "write_property", {**ao, "value": 33.5, "priority": 8})
            assert written["success"] and written["data"]["acknowledged"], written
            readback = await call(session, "read_property", ao)
            assert readback["data"]["value"] == pytest.approx(33.5)

            released = await call(session, "write_property", {**ao, "value": None, "priority": 8})
            assert released["success"], released
            readback = await call(session, "read_property", ao)
            assert readback["data"]["value"] == pytest.approx(50.0)

            missing = await call(session, "read_property", target("analog-input", 9))
            assert missing["success"] is False and "unknown-object" in missing["error"]
            denied = await call(
                session,
                "write_property",
                {**target("analog-input", 1, property_id="present-value"), "value": 1},
            )
            assert denied["success"] is False and "write-access-denied" in denied["error"]

        # Leaving the session without an exception is the point: with BAC0's
        # stdout logging, stdio_client teardown raised BrokenResourceError.
        anyio.run(sdk_session, server_env(writes=True), scenario)
    assert "Failed to parse" not in caplog.text


def test_writes_are_refused_by_default_end_to_end():
    with mock_process() as address:

        async def scenario(session: ClientSession):
            ao = target("analog-output", 1, property_id="present-value", address=address)
            refused = await call(session, "write_property", {**ao, "value": 10, "priority": 8})
            assert refused["success"] is False
            assert "BACNET_WRITES_ENABLED" in refused["error"]
            value = await call(session, "read_property", ao)
            assert value["data"]["value"] == pytest.approx(50.0)
            ping = await call(session, "ping", {})
            assert ping["data"]["writes_enabled"] is False

        env = server_env(writes=False)
        del env["BACNET_WRITES_ENABLED"]
        anyio.run(sdk_session, env, scenario)


def test_stdout_carries_only_json_rpc():
    with mock_process() as address:
        requests = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "raw", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "discover_devices", "arguments": {"address": address}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "read_property", "arguments": target("analog-input", 1)},
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {}},
            },
        ]
        env = {**os.environ, **server_env(writes=False), "BACNET_LOG_LEVEL": "DEBUG"}
        proc = subprocess.Popen(
            SERVER_COMMAND,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
        # Drain stderr in the background so DEBUG logging can never fill the pipe.
        stderr_chunks: list[str] = []
        drain = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()))
        drain.start()
        responses: dict[int, dict[str, Any]] = {}
        lines: list[str] = []
        try:
            for request in requests:
                proc.stdin.write(json.dumps(request) + "\n")
                proc.stdin.flush()
                if "id" not in request:
                    continue
                while request["id"] not in responses:
                    line = proc.stdout.readline()
                    assert line, "server closed stdout early"
                    lines.append(line)
                    message = json.loads(line)
                    if "id" in message:
                        responses[message["id"]] = message
            proc.stdin.close()
            lines.extend(proc.stdout.readlines())
            proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            drain.join(timeout=10)
            proc.stdout.close()
            proc.stderr.close()
        stderr = "".join(stderr_chunks)

        assert proc.returncode == 0, stderr
        for line in lines:
            assert json.loads(line)["jsonrpc"] == "2.0", line
        assert responses[3]["result"]["structuredContent"]["success"] is True
        assert responses[4]["result"]["structuredContent"]["success"] is True
        # DEBUG logging was on, and all of it went to stderr.
        assert "bacnet_mcp" in stderr


NOISY_SERVER = """
from bacnet_mcp.server import BACnetMCPServer
from bacnet_mcp.tools import ToolConfig


class IdleClient:
    class config:
        writes_enabled = False

    async def open(self):
        pass

    async def close(self):
        pass


server = BACnetMCPServer(client=IdleClient(), tool_config=ToolConfig())


@server.mcp.tool()
async def noisy() -> dict:
    \"\"\"Print to stdout, as a careless library might.\"\"\"
    print("noise from a library", flush=True)
    return {"success": True, "data": None, "error": None, "meta": {}}


server.run()
"""


def test_print_to_stdout_cannot_corrupt_the_protocol():
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "raw", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "noisy", "arguments": {}},
        },
    ]
    stdin = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.Popen(
        [sys.executable, "-c", NOISY_SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(stdin)
    proc.stdin.flush()
    lines = []
    while len([line for line in lines if '"id":2' in line.replace(" ", "")]) == 0:
        line = proc.stdout.readline()
        assert line, "server closed stdout early"
        lines.append(line)
    out, err = proc.communicate(timeout=30)
    lines.extend(out.splitlines(keepends=True))
    for line in lines:
        assert json.loads(line)["jsonrpc"] == "2.0", line
    assert "noise from a library" in err


def test_startup_fails_cleanly_on_a_busy_port():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy:
        busy.bind(("127.0.0.1", 0))
        env = {**os.environ, **server_env(writes=False)}
        env["BACNET_PORT"] = str(busy.getsockname()[1])
        result = subprocess.run(
            SERVER_COMMAND, input="", capture_output=True, text=True, env=env, timeout=60
        )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "cannot bind" in result.stderr


def test_startup_fails_cleanly_on_bad_configuration():
    env = {**os.environ, **server_env(writes=False), "BACNET_WRITES_ENABLED": "perhaps"}
    result = subprocess.run(
        SERVER_COMMAND, input="", capture_output=True, text=True, env=env, timeout=60
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "BACNET_WRITES_ENABLED" in result.stderr
