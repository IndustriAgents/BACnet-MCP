"""Configuration: read after .env, writes off by default, explicit broadcast."""

from __future__ import annotations

import os
import socket

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


def test_dotenv_values_reach_the_client(clean_env, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "BACNET_INTERFACE=127.0.0.1\n"
        "BACNET_PORT=47998\n"
        "BACNET_DEVICE_INSTANCE=4321\n"
        "BACNET_WRITES_ENABLED=true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    cli.load_environment()
    server = BACnetMCPServer()
    assert server.client.config.port == 47998
    assert server.client.config.device_instance == 4321
    assert server.client.config.writes_enabled is True


def test_dotenv_is_found_from_a_subdirectory(clean_env, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("BACNET_PORT=47997\n", encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    cli.load_environment()
    assert BACnetConfig.from_env().port == 47997


def test_process_environment_wins_over_dotenv(clean_env, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("BACNET_PORT=47996\n", encoding="utf-8")
    clean_env["BACNET_PORT"] = "47995"
    monkeypatch.chdir(tmp_path)
    cli.load_environment()
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
