"""CLI entry point for the BACnet MCP server."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from importlib.metadata import version
from pathlib import Path

from dotenv import dotenv_values, find_dotenv

from .bacnet_client import BACnetClientError, ConfigError
from .server import BACnetMCPServer

# Safety switches: they turn on something that changes devices. They are read
# only from the process environment or a file named with --env-file, never
# from a .env that happens to be in the working directory, which for a server
# started directly by an MCP client is the client's (possibly untrusted)
# project. The pattern covers the names the IndustriConnect servers use.
GATE_VARIABLES = frozenset({"BACNET_WRITES_ENABLED"})
_GATE_PATTERN = re.compile(r"_(WRITES|SYSTEM_CMDS|CONFIG_CMDS|STATE_CHANGE)_ENABLED$")


def is_gate(name: str) -> bool:
    return name in GATE_VARIABLES or bool(_GATE_PATTERN.search(name))


def load_environment(env_file: str | Path | None = None) -> None:
    """Load settings from a .env file before anything reads the configuration.

    Values already in the process environment always win. With ``env_file``
    (``--env-file``) every variable in that file is used, safety switches
    included. Otherwise the ``.env`` found from the working directory (or the
    nearest parent) is used for everything except safety switches, which are
    ignored with a warning on stderr.
    """
    if env_file is not None:
        path = Path(env_file).expanduser()
        if not path.is_file():
            raise ConfigError(f"--env-file {env_file}: no such file")
        explicit = True
    else:
        found = find_dotenv(usecwd=True)
        if not found:
            return
        path = Path(found)
        explicit = False
    for name, value in dotenv_values(path).items():
        if value is None:
            continue
        if not explicit and is_gate(name):
            print(
                f"bacnet-mcp: ignoring {name} from {path}: safety switches are read only "
                "from the process environment or a file given with --env-file",
                file=sys.stderr,
            )
            continue
        os.environ.setdefault(name, value)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bacnet-mcp",
        description="BACnet/IP MCP server (speaks MCP over stdio). Configured with "
        "BACNET_* environment variables; see the README.",
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        help="read settings from this file, safety switches such as "
        "BACNET_WRITES_ENABLED included (an automatically found .env may not set "
        "them); the process environment still wins",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {version('bacnet-mcp')}")
    return parser.parse_args(argv)


LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def configure_logging() -> None:
    """Send all logging to stderr; stdout is the MCP JSON-RPC channel."""
    level_name = os.environ.get("BACNET_LOG_LEVEL", "").strip().upper() or "WARNING"
    if level_name not in LOG_LEVELS:
        raise ConfigError(
            f"BACNET_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}, got {level_name!r}"
        )
    level = getattr(logging, level_name)
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _leaves(exc: BaseException) -> list[BaseException]:
    """The individual exceptions inside (possibly nested) exception groups."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for inner in exc.exceptions for leaf in _leaves(inner)]
    return [exc]


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        load_environment(args.env_file)
        configure_logging()
        server = BACnetMCPServer()
        if server.object_map.error:
            raise ConfigError(server.object_map.error)
        server.client.preflight()
    except (ConfigError, BACnetClientError) as exc:
        sys.exit(f"bacnet-mcp: {exc}")
    try:
        server.run()
    except BaseException as exc:  # noqa: BLE001 - unwrap anyio's exception groups
        leaves = _leaves(exc)
        if all(isinstance(leaf, KeyboardInterrupt) for leaf in leaves):
            return
        errors = [leaf for leaf in leaves if isinstance(leaf, BACnetClientError)]
        if not errors:
            raise
        # Straight to stderr: logging may be set to CRITICAL.
        for error in errors:
            print(f"bacnet-mcp: {error}", file=sys.__stderr__)
        sys.exit(1)


if __name__ == "__main__":
    main()
