"""End to end: the mock device and the real MCP server, each in its own process.

The mock runs as a BACnet/IP device on a free 127.0.0.1 UDP port and the
server is driven over stdio, exactly as an MCP client runs it: once through
the mcp client SDK and once with raw JSON-RPC lines to check that stdout
carries nothing else (known issue 1).

Every wait has a deadline, the subprocesses run in a temporary directory (so
no developer .env is picked up) and get no BACNET_* settings from the
caller's environment.
"""

from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

import anyio
import pytest
from helpers import ENV_KEYS, MOCK_DEVICE_ID, MOCK_SCRIPT, free_udp_port
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_COMMAND = [sys.executable, "-m", "bacnet_mcp.cli"]
TIMEOUT = 30.0


class LineReader:
    """Read a subprocess stream on a thread, so every wait can time out."""

    def __init__(self, stream: IO[str]) -> None:
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream: IO[str]) -> None:
        for line in stream:
            self._lines.put(line)
        self._lines.put(None)

    def next(self, timeout: float = TIMEOUT) -> str | None:
        """The next line, or None at end of stream."""
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            pytest.fail(f"no output within {timeout} s")

    def rest(self, timeout: float = TIMEOUT) -> list[str]:
        """Every remaining line, up to end of stream."""
        lines = []
        while (line := self.next(timeout)) is not None:
            lines.append(line)
        return lines


def isolated_env(**settings: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(settings)
    return env


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@contextmanager
def mock_process() -> Iterator[str]:
    """Run the mock device; yield its address once it is listening."""
    port = free_udp_port()
    proc = subprocess.Popen(
        [sys.executable, str(MOCK_SCRIPT), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=isolated_env(),
    )
    try:
        assert proc.stderr is not None
        stderr = LineReader(proc.stderr)  # keeps draining the pipe afterwards
        while True:
            line = stderr.next()
            if line is None:
                pytest.fail(f"mock device exited with {proc.wait()}")
            if "listening on" in line:
                break
        yield f"127.0.0.1:{port}"
    finally:
        stop(proc)


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


def run_sdk_session(cwd: Path, env: dict[str, str], scenario) -> Any:
    async def main():
        # StdioServerParameters adds only a safe default environment (PATH,
        # HOME, ...) to env, so no BACNET_* setting leaks in from the caller.
        params = StdioServerParameters(
            command=SERVER_COMMAND[0], args=SERVER_COMMAND[1:], env=env, cwd=str(cwd)
        )
        with anyio.fail_after(120):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await scenario(session)

    return anyio.run(main)


def test_mcp_sdk_session_against_the_mock(tmp_path, caplog):
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
        run_sdk_session(tmp_path, server_env(writes=True), scenario)
    assert "Failed to parse" not in caplog.text


def test_writes_are_refused_by_default_end_to_end(tmp_path):
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
        run_sdk_session(tmp_path, env, scenario)


INITIALIZE = [
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
]


def raw_session(
    command: list[str], env: dict[str, str], cwd: Path, requests: list[dict[str, Any]]
) -> tuple[int, list[str], dict[int, dict[str, Any]], str]:
    """Send JSON-RPC lines, wait for each answer, close stdin, collect everything."""
    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    stdout, stderr = LineReader(proc.stdout), LineReader(proc.stderr)
    lines: list[str] = []
    responses: dict[int, dict[str, Any]] = {}
    try:
        for request in INITIALIZE + requests:
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            if "id" not in request:
                continue
            while request["id"] not in responses:
                line = stdout.next()
                assert line is not None, "server closed stdout early"
                lines.append(line)
                message = json.loads(line)
                if "id" in message:
                    responses[message["id"]] = message
        proc.stdin.close()
        lines.extend(stdout.rest())
        returncode = proc.wait(timeout=TIMEOUT)
    finally:
        stop(proc)
    return returncode, lines, responses, "".join(stderr.rest())


def tool_call(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def test_stdout_carries_only_json_rpc(tmp_path):
    with mock_process() as address:
        requests = [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            tool_call(3, "discover_devices", {"address": address}),
            tool_call(4, "read_property", target("analog-input", 1)),
            tool_call(5, "ping", {}),
        ]
        env = isolated_env(**server_env(writes=False), BACNET_LOG_LEVEL="DEBUG")
        returncode, lines, responses, stderr = raw_session(SERVER_COMMAND, env, tmp_path, requests)

    assert returncode == 0, stderr
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


def test_print_to_stdout_cannot_corrupt_the_protocol(tmp_path):
    returncode, lines, responses, stderr = raw_session(
        [sys.executable, "-c", NOISY_SERVER],
        isolated_env(),
        tmp_path,
        [tool_call(2, "noisy", {})],
    )
    assert returncode == 0, stderr
    for line in lines:
        assert json.loads(line)["jsonrpc"] == "2.0", line
    assert 2 in responses
    assert "noise from a library" in stderr


def start_and_fail(tmp_path: Path, **settings: str) -> subprocess.CompletedProcess:
    env = isolated_env(**{**server_env(writes=False), **settings})
    return subprocess.run(
        SERVER_COMMAND,
        input="",
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )


def test_startup_fails_cleanly_on_a_busy_port(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy:
        busy.bind(("127.0.0.1", 0))
        result = start_and_fail(tmp_path, BACNET_PORT=str(busy.getsockname()[1]))
    assert result.returncode == 1
    assert result.stdout == ""
    assert "cannot bind" in result.stderr


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"BACNET_WRITES_ENABLED": "perhaps"}, "BACNET_WRITES_ENABLED"),
        ({"BACNET_LOG_LEVEL": "LOUD"}, "BACNET_LOG_LEVEL"),
        ({"BACNET_INTERFACE": "eth0"}, "BACNET_INTERFACE"),
        ({"OBJECT_MAP_FILE": "missing.json"}, "cannot read object map"),
        ({"OBJECT_MAP_FILE": "broken.json"}, "not valid JSON"),
    ],
)
def test_startup_fails_cleanly_on_bad_configuration(tmp_path, settings, message):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    result = start_and_fail(tmp_path, **settings)
    assert result.returncode == 1
    assert result.stdout == ""
    assert message in result.stderr
    assert "Traceback" not in result.stderr


def test_a_dotenv_in_the_working_directory_is_used(tmp_path):
    (tmp_path / ".env").write_text("BACNET_WRITES_ENABLED=maybe\n", encoding="utf-8")
    env = server_env(writes=False)
    del env["BACNET_WRITES_ENABLED"]
    result = subprocess.run(
        SERVER_COMMAND,
        input="",
        capture_output=True,
        text=True,
        env=isolated_env(**env),
        cwd=tmp_path,
        timeout=60,
    )
    assert result.returncode == 1
    assert "BACNET_WRITES_ENABLED must be true or false, got 'maybe'" in result.stderr
