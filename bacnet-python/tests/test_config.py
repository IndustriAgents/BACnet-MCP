"""Configuration: read after .env, writes off by default, explicit broadcast."""

from __future__ import annotations

import logging
import os
import socket
import sys
from pathlib import Path

import pytest
from helpers import free_udp_port

from bacnet_mcp import bacnet_client, cli
from bacnet_mcp.bacnet_client import BACnetClient, BACnetClientError, BACnetConfig, ConfigError
from bacnet_mcp.server import BACnetMCPServer
from bacnet_mcp.tools import ToolConfig


def test_environment_is_read_when_the_client_is_built_not_at_import(clean_env):
    # Known issue 4: BACNET_PORT set after importing the CLI was ignored.
    clean_env["BACNET_PORT"] = "47999"
    assert BACnetClient().config.port == 47999


def project(tmp_path: Path, content: str) -> Path:
    """A stand-in for bacnet-python/.env (the real one is never touched)."""
    env = tmp_path / "bacnet-python" / ".env"
    env.parent.mkdir(exist_ok=True)
    env.write_text(content, encoding="utf-8")
    return env


def test_the_project_dotenv_sets_connection_settings(clean_env, tmp_path):
    env = project(
        tmp_path, "BACNET_INTERFACE=127.0.0.1\nBACNET_PORT=47998\nBACNET_DEVICE_INSTANCE=4321\n"
    )
    cli.load_environment(implicit_file=env)
    server = BACnetMCPServer()
    assert server.client.config.port == 47998
    assert server.client.config.device_instance == 4321


def test_the_implicit_dotenv_is_the_projects_not_the_working_directorys():
    # This checkout: <repo>/bacnet-python/src/bacnet_mcp/cli.py
    expected = Path(cli.__file__).resolve().parents[2] / ".env"
    assert cli.project_env_file() == expected
    assert expected.parent.name == "bacnet-python"
    # An installed package has no implicit .env at all.
    installed = "/usr/lib/python3.11/site-packages/bacnet_mcp/cli.py"
    assert cli.project_env_file(installed) is None


@pytest.mark.parametrize("where", ["cwd", "parent"])
def test_a_dotenv_in_the_working_directory_or_a_parent_is_not_read(
    clean_env, tmp_path, monkeypatch, where
):
    # Started by an MCP client, the working directory is the client's project,
    # which may not be trusted; settings there could redirect a write.
    (tmp_path / ".env").write_text(
        "BACNET_PORT=47994\nBACNET_INTERFACE=10.9.9.9\nBACNET_WRITES_ENABLED=true\n",
        encoding="utf-8",
    )
    cwd = tmp_path if where == "cwd" else tmp_path / "sub" / "dir"
    cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(cwd)
    cli.load_environment()
    assert "BACNET_PORT" not in clean_env
    config = BACnetConfig.from_env()
    assert (config.port, config.writes_enabled) == (47808, False)


GATE_LINES = [
    "BACNET_WRITES_ENABLED=true",
    "export BACNET_WRITES_ENABLED=true",
    "'BACNET_WRITES_ENABLED'=true",
    '"BACNET_WRITES_ENABLED"=true',
    "  BACNET_WRITES_ENABLED  =  true",
    'export   BACNET_WRITES_ENABLED="true"',
    "bacnet_writes_enabled=true",
    "Bacnet_Writes_Enabled=true",
    "export bacnet_writes_enabled='true'",
    "bacnet_wr\u0131tes_enabled=true",  # dotless i: .upper() is BACNET_WRITES_ENABLED
    "OTHER_SYSTEM_CMDS_ENABLED=true",
    "x_state_change_enabled=true",
]


def windows_environ(base: dict[str, str]) -> os._Environ:
    """os.environ as on Windows: names are upper-cased on the way in."""
    data = {k.upper(): v for k, v in base.items()}
    return os._Environ(data, str.upper, lambda v: v, str.upper, lambda v: v)


@pytest.mark.parametrize("platform", ["posix", "windows"])
@pytest.mark.parametrize("line", GATE_LINES)
def test_no_spelling_of_a_safety_switch_passes_the_implicit_dotenv(
    clean_env, tmp_path, monkeypatch, capsys, platform, line
):
    if platform == "windows":
        monkeypatch.setattr(os, "environ", windows_environ(clean_env))
    cli.load_environment(implicit_file=project(tmp_path, line + "\n"))
    assert BACnetConfig.from_env().writes_enabled is False
    assert not any(cli.is_gate(name) for name in os.environ)
    assert "ignoring" in capsys.readouterr().err


@pytest.mark.parametrize(
    "name",
    [
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "HTTPS_PROXY",
        "SSL_CERT_FILE",
        "FASTMCP_DEBUG",
        "PATH",
        "OBJECT_MAP_FILE",
        "object_map_file",
    ],
)
def test_the_implicit_dotenv_sets_only_bacnet_connection_settings(
    clean_env, tmp_path, capsys, name
):
    before = dict(os.environ)
    cli.load_environment(implicit_file=project(tmp_path, f"{name}=/tmp/x\nBACNET_PORT=47990\n"))
    assert os.environ.get(name.upper()) == before.get(name.upper())
    assert os.environ["BACNET_PORT"] == "47990"
    assert f"ignoring {name.upper()}" in capsys.readouterr().err


def test_an_explicit_env_file_can_enable_writes_and_set_the_object_map(clean_env, tmp_path, capsys):
    env_file = tmp_path / "bacnet.env"
    env_file.write_text(
        "BACNET_WRITES_ENABLED=true\nBACNET_PORT=47993\nOBJECT_MAP_FILE=/tmp/map.json\n",
        encoding="utf-8",
    )
    cli.load_environment(env_file)
    config = BACnetConfig.from_env()
    assert config.writes_enabled is True
    assert config.port == 47993
    assert os.environ["OBJECT_MAP_FILE"] == "/tmp/map.json"
    assert capsys.readouterr().err == ""


def test_a_missing_env_file_is_a_config_error(clean_env, tmp_path):
    with pytest.raises(ConfigError, match="--env-file"):
        cli.load_environment(tmp_path / "nope.env")


def test_the_process_environment_still_enables_writes(clean_env, tmp_path):
    clean_env["BACNET_WRITES_ENABLED"] = "true"
    cli.load_environment(implicit_file=project(tmp_path, "BACNET_WRITES_ENABLED=false\n"))
    assert BACnetConfig.from_env().writes_enabled is True


def test_process_environment_wins_over_dotenv(clean_env, tmp_path):
    clean_env["BACNET_PORT"] = "47995"
    cli.load_environment(implicit_file=project(tmp_path, "BACNET_PORT=47996\n"))
    assert BACnetConfig.from_env().port == 47995


def test_writes_are_disabled_by_default(clean_env):
    # Known issue 5: BACNET_WRITES_ENABLED defaulted to true.
    assert BACnetConfig().writes_enabled is False
    assert BACnetConfig.from_env().writes_enabled is False
    assert BACnetMCPServer().client.config.writes_enabled is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("1", True), ("YES", True), ("on", True), ("false", False), ("0", False)],
)
def test_writes_enabled_values(raw, expected):
    assert BACnetConfig.from_env({"BACNET_WRITES_ENABLED": raw}).writes_enabled is expected


def test_unrecognised_boolean_is_an_error_not_a_silent_default():
    with pytest.raises(ConfigError, match="BACNET_WRITES_ENABLED"):
        BACnetConfig.from_env({"BACNET_WRITES_ENABLED": "maybe"})


def test_defaults_keep_the_server_on_loopback():
    config = BACnetConfig.from_env({})
    assert config.interface == "127.0.0.1/32"
    assert config.port == 47808
    assert config.device_instance == 1234
    assert config.broadcast_ip is None


@pytest.mark.parametrize(
    ("interface", "broadcast", "bacnet_address"),
    [
        ("127.0.0.1", None, "127.0.0.1/32:47808"),
        ("192.168.1.10/24", "192.168.1.255", "192.168.1.10/24:47808"),
        ("10.1.2.3/16", "10.1.255.255", "10.1.2.3/16:47808"),
        ("10.0.0.5/31", None, "10.0.0.5/32:47808"),
        ("0.0.0.0", None, "0.0.0.0/32:47808"),
    ],
)
def test_broadcast_address_comes_from_the_prefix(interface, broadcast, bacnet_address):
    # Known issue 7: the broadcast address was always a derived /24.
    config = BACnetConfig(interface=interface)
    assert config.broadcast_ip == broadcast
    assert config.bacnet_address == bacnet_address


@pytest.mark.parametrize(
    "environ",
    [
        {"BACNET_INTERFACE": "eth0"},
        {"BACNET_INTERFACE": "192.168.1.300"},
        {"BACNET_PORT": "x"},
        {"BACNET_PORT": "0"},
        {"BACNET_PORT": "70000"},
        {"BACNET_DEVICE_INSTANCE": "4194303"},
        {"BACNET_DEVICE_INSTANCE": "-1"},
        {"BACNET_APDU_TIMEOUT_MS": "10"},
        {"BACNET_APDU_RETRIES": "99"},
    ],
)
def test_invalid_settings_are_rejected(environ):
    with pytest.raises(ConfigError):
        BACnetConfig.from_env(environ)


def test_object_map_path_is_read_from_the_environment(clean_env, tmp_path):
    clean_env["OBJECT_MAP_FILE"] = str(tmp_path / "map.json")
    assert ToolConfig.from_env().object_map_path == tmp_path / "map.json"
    assert ToolConfig.from_env({}).object_map_path is None


def test_preflight_rejects_a_busy_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as busy:
        busy.bind(("127.0.0.1", 0))
        port = busy.getsockname()[1]
        client = BACnetClient(BACnetConfig(port=port))
        with pytest.raises(BACnetClientError, match="cannot bind"):
            client.preflight()


def test_preflight_checks_the_broadcast_address(monkeypatch):
    # bacpypes3 retries a failed broadcast bind forever and every send waits
    # for it (127.0.0.255 on macOS); preflight turns that into an error.
    calls = []
    monkeypatch.setattr(
        bacnet_client, "_check_bindable", lambda ip, port, what: calls.append((ip, port))
    )
    BACnetClient(BACnetConfig(interface="127.0.0.1/24", port=47900)).preflight()
    expected = [("127.0.0.1", 47900)]
    if os.name != "nt":
        expected.append(("127.0.0.255", 47900))
    assert calls == expected

    calls.clear()
    BACnetClient(BACnetConfig(interface="127.0.0.1", port=47900)).preflight()
    assert calls == [("127.0.0.1", 47900)]


def test_unbindable_address_fails_preflight():
    # 192.0.2.0/24 is TEST-NET-1: never a local address.
    client = BACnetClient(BACnetConfig(interface="192.0.2.1/24", port=free_udp_port()))
    with pytest.raises(BACnetClientError, match="cannot bind"):
        client.preflight()


def test_object_map_path_with_an_unknown_user_is_a_config_error():
    with pytest.raises(ConfigError, match="OBJECT_MAP_FILE"):
        ToolConfig.from_env({"OBJECT_MAP_FILE": "~nosuchuser-bacnet-test/objects.json"})


@pytest.fixture
def root_logging():
    root = logging.getLogger()
    saved = (root.level, root.handlers[:])
    yield root
    root.setLevel(saved[0])
    root.handlers[:] = saved[1]


@pytest.mark.parametrize(
    ("raw", "level"), [("", "WARNING"), ("debug", "DEBUG"), (" Error ", "ERROR")]
)
def test_log_level(clean_env, root_logging, raw, level):
    clean_env["BACNET_LOG_LEVEL"] = raw
    cli.configure_logging()
    assert root_logging.level == getattr(logging, level)
    assert all(getattr(h, "stream", None) is not sys.stdout for h in root_logging.handlers)


def test_unknown_log_level_is_a_config_error(clean_env):
    clean_env["BACNET_LOG_LEVEL"] = "LOUD"
    with pytest.raises(ConfigError, match="BACNET_LOG_LEVEL"):
        cli.configure_logging()
