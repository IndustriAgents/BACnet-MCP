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
import time
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
    # stdin is a pipe nobody writes to: if this test run is killed, the pipe
    # closes and --exit-on-stdin-eof stops the mock, so it never outlives us.
    proc = subprocess.Popen(
        [
            sys.executable,
            str(MOCK_SCRIPT),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--exit-on-stdin-eof",
        ],
        stdin=subprocess.PIPE,
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
    # Every setting is given, so a developer's bacnet-python/.env (the only
    # implicit one) cannot change the results: the process environment wins.
    return {
        "BACNET_INTERFACE": "127.0.0.1",
        "BACNET_PORT": str(free_udp_port()),
        "BACNET_DEVICE_INSTANCE": "1234",
        "BACNET_WRITES_ENABLED": "true" if writes else "false",
        "BACNET_APDU_TIMEOUT_MS": "1000",
        "BACNET_APDU_RETRIES": "1",
        "BACNET_LOG_LEVEL": "WARNING",
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
    """Every tool is called, unbuffered, at DEBUG: stdout must stay pure JSON-RPC."""
    with mock_process() as address:
        (tmp_path / "objects.json").write_text(
            json.dumps(
                {
                    "zone1": {**target("analog-input", 1), "address": address},
                    "damper": {**target("analog-output", 1), "address": address, "priority": 8},
                }
            ).replace('"device_id"', '"device"'),
            encoding="utf-8",
        )
        requests = [
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            tool_call(3, "discover_devices", {"address": address}),
            tool_call(4, "read_property", target("analog-input", 1)),
            tool_call(
                5,
                "write_property",
                {**target("analog-output", 1, property_id="present-value"), "value": 40},
            ),
            tool_call(6, "list_objects", {}),
            tool_call(7, "read_object_by_alias", {"alias": "zone1"}),
            tool_call(8, "write_object_by_alias", {"alias": "damper", "value": None}),
            tool_call(9, "ping", {}),
        ]
        env = isolated_env(
            **{
                **server_env(writes=True),
                "BACNET_LOG_LEVEL": "DEBUG",
                "OBJECT_MAP_FILE": str(tmp_path / "objects.json"),
                "PYTHONUNBUFFERED": "1",
            }
        )
        returncode, lines, responses, stderr = raw_session(SERVER_COMMAND, env, tmp_path, requests)

    assert returncode == 0, stderr
    for line in lines:
        assert json.loads(line)["jsonrpc"] == "2.0", line
    called = {r["params"]["name"] for r in requests if r.get("method") == "tools/call"}
    listed = {t["name"] for t in responses[2]["result"]["tools"]}
    assert called == listed  # every tool, so a print() anywhere would show up
    for request_id in range(3, 10):
        envelope = responses[request_id]["result"]["structuredContent"]
        assert envelope["success"] is True, envelope
    # DEBUG logging was on, and all of it went to stderr.
    assert "bacnet_mcp" in stderr


def test_the_server_starts_without_a_device(tmp_path):
    """initialize and tools/list work with nothing reachable; device tools fail cleanly."""
    silent = f"127.0.0.1:{free_udp_port()}"
    requests = [
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        tool_call(3, "read_property", target("analog-input", 1, address=silent)),
        tool_call(
            4,
            "write_property",
            {**target("analog-output", 1, property_id="present-value", address=silent), "value": 1},
        ),
        tool_call(5, "ping", {}),
    ]
    env = isolated_env(**{**server_env(writes=True), "BACNET_APDU_RETRIES": "0"})
    returncode, lines, responses, stderr = raw_session(SERVER_COMMAND, env, tmp_path, requests)
    assert returncode == 0, stderr
    assert responses[1]["result"]["serverInfo"]["name"] == "BACnet MCP Server"
    assert len(responses[2]["result"]["tools"]) == 7
    read = responses[3]["result"]["structuredContent"]
    assert read["success"] is False and "no BACnet device answered" in read["error"]
    write = responses[4]["result"]["structuredContent"]
    assert write["success"] is False
    assert (write["meta"]["outcome"], write["meta"]["request_sent"]) == ("not_sent", False)
    assert responses[5]["result"]["structuredContent"]["success"] is True


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
    print("noise from a library")
    return {"success": True, "data": None, "error": None, "meta": {}}


server.run()
"""


def test_print_to_stdout_cannot_corrupt_the_protocol(tmp_path):
    returncode, lines, responses, stderr = raw_session(
        [sys.executable, "-c", NOISY_SERVER],
        isolated_env(PYTHONUNBUFFERED="1"),
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


def test_a_dotenv_in_the_working_directory_is_not_read(tmp_path):
    # If it were read, the server would try to bind 192.0.2.1 and exit.
    (tmp_path / ".env").write_text("BACNET_INTERFACE=192.0.2.1/24\n", encoding="utf-8")
    env = server_env(writes=False)
    del env["BACNET_INTERFACE"]
    returncode, _, responses, stderr = raw_session(
        SERVER_COMMAND, isolated_env(**env), tmp_path, [tool_call(2, "ping", {})]
    )
    assert returncode == 0, stderr
    connection = responses[2]["result"]["structuredContent"]["data"]["connection"]
    assert connection["interface"] == "127.0.0.1/32"


def ping_writes_enabled(cwd: Path, env: dict[str, str], extra_args: list[str]) -> tuple[bool, str]:
    returncode, lines, responses, stderr = raw_session(
        SERVER_COMMAND + extra_args, isolated_env(**env), cwd, [tool_call(2, "ping", {})]
    )
    assert returncode == 0, stderr
    return responses[2]["result"]["structuredContent"]["data"]["writes_enabled"], stderr


def test_a_dotenv_in_the_working_directory_cannot_enable_writes(tmp_path):
    (tmp_path / ".env").write_text("BACNET_WRITES_ENABLED=true\n", encoding="utf-8")
    env = server_env(writes=False)
    del env["BACNET_WRITES_ENABLED"]
    enabled, _ = ping_writes_enabled(tmp_path, env, [])
    assert enabled is False


def test_an_explicit_env_file_can_enable_writes(tmp_path):
    (tmp_path / "bacnet.env").write_text("BACNET_WRITES_ENABLED=true\n", encoding="utf-8")
    env = server_env(writes=False)
    del env["BACNET_WRITES_ENABLED"]
    enabled, _ = ping_writes_enabled(tmp_path, env, ["--env-file", str(tmp_path / "bacnet.env")])
    assert enabled is True


SLOW_MOCK = Path(__file__).resolve().parent / "slow_identity_mock.py"
IDENTITY_DELAY = 3.0


@contextmanager
def slow_identity_mock() -> Iterator[tuple[str, LineReader]]:
    """A mock that answers identity checks IDENTITY_DELAY s late; yields its writes."""
    port = free_udp_port()
    proc = subprocess.Popen(
        [sys.executable, str(SLOW_MOCK), str(port), str(IDENTITY_DELAY)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=isolated_env(),
    )
    try:
        assert proc.stdout is not None
        output = LineReader(proc.stdout)
        assert output.next() == "listening\n"
        yield f"127.0.0.1:{port}", output
    finally:
        stop(proc)


def writes_seen(output: LineReader, wait: float) -> list[str]:
    """WRITE lines the mock printed within ``wait`` seconds of quiet."""
    lines = []
    while True:
        try:
            line = output._lines.get(timeout=wait)
        except queue.Empty:
            return lines
        if line is None:
            return lines
        if line.startswith("WRITE"):
            lines.append(line)


def start_slow_write(address: str) -> tuple[subprocess.Popen, LineReader, LineReader]:
    """Start the server and send a write that waits for the slow identity check."""
    env = isolated_env(
        **{
            **server_env(writes=True),
            "BACNET_APDU_TIMEOUT_MS": "10000",
            "BACNET_APDU_RETRIES": "0",
            "PYTHONUNBUFFERED": "1",
        }
    )
    proc = subprocess.Popen(
        SERVER_COMMAND,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    stdout, stderr = LineReader(proc.stdout), LineReader(proc.stderr)
    for request in INITIALIZE:
        proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()
    assert json.loads(stdout.next())["id"] == 1
    write = {**target("analog-output", 1, property_id="present-value", address=address)}
    proc.stdin.write(
        json.dumps(tool_call(2, "write_property", {**write, "value": 11, "priority": 8}))
    )
    proc.stdin.write("\n")
    proc.stdin.flush()
    return proc, stdout, stderr


def test_a_client_disconnect_stops_a_write_that_has_not_been_sent():
    """stdin closes while the write waits for its identity check: nothing is sent."""
    with slow_identity_mock() as (address, mock_output):
        proc, stdout, stderr = start_slow_write(address)
        try:
            time.sleep(0.7)  # the identity check is now pending at the mock
            proc.stdin.close()  # the MCP client goes away
            closed = time.monotonic()
            returncode = proc.wait(timeout=TIMEOUT)
            # The pending call is cancelled, not waited for: the identity check
            # would only be answered IDENTITY_DELAY - 0.7 s after the close.
            assert time.monotonic() - closed < 1.5
            stdout.rest()
            errors = "".join(stderr.rest())
        finally:
            stop(proc)
        # Wait past the moment the identity check is answered.
        writes = writes_seen(mock_output, IDENTITY_DELAY + 1.5)
    assert writes == []
    assert returncode == 0, errors
    assert "Traceback" not in errors


def test_a_cancelled_write_is_never_sent():
    """notifications/cancelled while the write waits for its identity check."""
    with slow_identity_mock() as (address, mock_output):
        proc, stdout, stderr = start_slow_write(address)
        try:
            time.sleep(0.7)
            cancel = {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 2, "reason": "user cancelled"},
            }
            proc.stdin.write(json.dumps(cancel) + "\n")
            proc.stdin.flush()
            writes = writes_seen(mock_output, IDENTITY_DELAY + 1.5)
            proc.stdin.write(json.dumps(tool_call(3, "ping", {})) + "\n")
            proc.stdin.flush()
            replies: dict[int, dict[str, Any]] = {}
            while 3 not in replies:
                line = stdout.next()
                assert line is not None
                message = json.loads(line)
                replies[message["id"]] = message
            proc.stdin.close()
            returncode = proc.wait(timeout=TIMEOUT)
            errors = "".join(stderr.rest())
        finally:
            stop(proc)
    assert writes == []
    if 2 in replies:  # the SDK answers a cancelled request with an error
        assert "result" not in replies[2]
    assert replies[3]["result"]["structuredContent"]["success"] is True
    assert returncode == 0, errors


def test_calls_sent_just_before_eof_are_answered_but_writes_are_not_sent():
    """ping and list_objects still answer; a pending device write is stopped."""
    with slow_identity_mock() as (address, mock_output):
        write = target("analog-output", 1, property_id="present-value", address=address)
        requests = [
            *INITIALIZE,
            tool_call(2, "ping", {}),
            tool_call(3, "list_objects", {}),
            tool_call(4, "write_property", {**write, "value": 9, "priority": 8}),
            {"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
        ]
        env = isolated_env(**{**server_env(writes=True), "BACNET_APDU_TIMEOUT_MS": "10000"})
        start = time.monotonic()
        result = subprocess.run(
            SERVER_COMMAND,
            input="".join(json.dumps(r) + "\n" for r in requests),  # then EOF
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        elapsed = time.monotonic() - start
        writes = writes_seen(mock_output, IDENTITY_DELAY + 1.0)
    replies = {m["id"]: m for m in map(json.loads, result.stdout.splitlines()) if "id" in m}
    assert result.returncode == 0, result.stderr
    assert replies[2]["result"]["structuredContent"]["success"] is True
    assert "objects" in replies[3]["result"]["structuredContent"]["data"]
    assert len(replies[5]["result"]["tools"]) == 7
    if 4 in replies:  # the stopped write answers not_sent
        meta = replies[4]["result"]["structuredContent"]["meta"]
        assert (meta["outcome"], meta["request_sent"]) == ("not_sent", False)
    assert writes == []
    assert elapsed < IDENTITY_DELAY + 1.0  # the write was not waited for


SLOW_TOOL_SERVER = """
import anyio

import bacnet_mcp.server as server_module
from bacnet_mcp.server import BACnetMCPServer
from bacnet_mcp.tools import ToolConfig

server_module.DRAIN_LIMIT_S = 0.3  # give up on in-flight calls quickly


class IdleClient:
    class config:
        writes_enabled = False

    async def open(self):
        pass

    async def close(self):
        pass

    def client_disconnected(self):
        pass


server = BACnetMCPServer(client=IdleClient(), tool_config=ToolConfig())


@server.mcp.tool()
async def slow() -> dict:
    \"\"\"Outlive the drain at end of input.\"\"\"
    await anyio.sleep(1.0)
    return {"success": True, "data": None, "error": None, "meta": {}}


server.run()
"""


def test_a_reply_that_can_no_longer_be_delivered_is_a_clean_exit(tmp_path):
    """A call still running after the drain limit cannot answer: exit 0, no traceback."""
    requests = [*INITIALIZE, tool_call(2, "slow", {})]
    result = subprocess.run(
        [sys.executable, "-c", SLOW_TOOL_SERVER],
        input="".join(json.dumps(r) + "\n" for r in requests),
        capture_output=True,
        text=True,
        env=isolated_env(),
        cwd=tmp_path,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    ids = [json.loads(line).get("id") for line in result.stdout.splitlines()]
    assert 2 not in ids
