"""CLI entry point for the BACnet MCP server."""

from __future__ import annotations

import logging
import os
import sys

from dotenv import find_dotenv, load_dotenv

from .bacnet_client import BACnetClientError, ConfigError
from .server import BACnetMCPServer

log = logging.getLogger("bacnet_mcp")


def load_environment() -> None:
    """Load ``.env`` from the working directory (or the nearest parent).

    Values already in the process environment win over the file. This runs
    before anything reads the configuration.
    """
    load_dotenv(find_dotenv(usecwd=True))


def configure_logging() -> None:
    """Send all logging to stderr; stdout is the MCP JSON-RPC channel."""
    level_name = os.environ.get("BACNET_LOG_LEVEL", "WARNING").strip().upper() or "WARNING"
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        level = logging.WARNING
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


def main() -> None:
    load_environment()
    configure_logging()
    try:
        server = BACnetMCPServer()
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
        for error in errors:
            log.error("%s", error)
        sys.exit(1)


if __name__ == "__main__":
    main()
