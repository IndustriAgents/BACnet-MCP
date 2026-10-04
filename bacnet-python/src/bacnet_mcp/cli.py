"""CLI entry point for the BACnet MCP server."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from importlib.metadata import version
from pathlib import Path

from dotenv import dotenv_values

from .bacnet_client import BACnetClientError, ConfigError
from .server import BACnetMCPServer

# Settings that decide whether, or where, a device is changed. They are read
# only from the process environment or a file named with --env-file, never
# from an automatically loaded .env. The gate pattern covers the names the
# IndustriConnect servers use; OBJECT_MAP_FILE steers alias writes.
GATE_VARIABLES = frozenset({"BACNET_WRITES_ENABLED"})
_GATE_PATTERN = re.compile(r"_(WRITES|SYSTEM_CMDS|CONFIG_CMDS|STATE_CHANGE)_ENABLED$")
PROTECTED_VARIABLES = frozenset({"OBJECT_MAP_FILE"})
OWN_PREFIX = "BACNET_"


def canonical(name: str) -> str:
    """Environment names as Windows sees them: stripped and upper-cased."""
    return name.strip().upper()


def is_gate(name: str) -> bool:
    name = canonical(name)
    return name in GATE_VARIABLES or bool(_GATE_PATTERN.search(name))


def project_env_file(module_file: str | Path | None = None) -> Path | None:
    """The only .env loaded without --env-file: bacnet-python/.env of a checkout.

    It is found from this package's source (``<project>/src/bacnet_mcp``),
    never from the working directory or its parents: an MCP client that starts
    the server runs it in the client's project, which may be untrusted. An
    installed package (in site-packages) has no implicit .env at all.
    """
    package_dir = Path(module_file or __file__).resolve().parent
    project = package_dir.parent.parent
    if (
        package_dir.parent.name != "src"
        or "site-packages" in package_dir.parts
        or "dist-packages" in package_dir.parts
        or not (project / "pyproject.toml").is_file()
    ):
        return None
    return project / ".env"


def _warn(message: str) -> None:
    print(f"bacnet-mcp: {message}", file=sys.stderr)


def load_environment(
    env_file: str | Path | None = None, *, implicit_file: str | Path | None = None
) -> None:
    """Load settings from a .env file before anything reads the configuration.

    Values already in the process environment always win.

    - With ``env_file`` (``--env-file``) every variable in that file is used.
    - Otherwise only the project's own ``.env`` (:func:`project_env_file`, or
      ``implicit_file`` in tests) is read, and only for this server's own
      ``BACNET_*`` connection settings. Safety switches, ``OBJECT_MAP_FILE``,
      anything else (PATH, LD_PRELOAD, PYTHON*, proxies, ...) and non-ASCII
      names are ignored with a warning on stderr. Names are compared the way
      Windows stores them (upper case).
    """
    if env_file is not None:
        path = Path(env_file).expanduser()
        if not path.is_file():
            raise ConfigError(f"--env-file {env_file}: no such file")
        for name, value in dotenv_values(path).items():
            if value is not None:
                os.environ.setdefault(name, value)
        return

    path = Path(implicit_file) if implicit_file is not None else project_env_file()
    if path is None or not path.is_file():
        return
    for raw_name, value in dotenv_values(path).items():
        if value is None:
            continue
        name = canonical(raw_name)
        if not raw_name.isascii():
            _warn(f"ignoring {raw_name!r} from {path}: not an ASCII name")
        elif is_gate(name):
            _warn(
                f"ignoring {name} from {path}: safety switches are read only from the "
                "process environment or a file given with --env-file"
            )
        elif name in PROTECTED_VARIABLES:
            _warn(
                f"ignoring {name} from {path}: it decides where writes go, so it is read "
                "only from the process environment or a file given with --env-file"
            )
        elif not name.startswith(OWN_PREFIX):
            _warn(f"ignoring {name} from {path}: not a bacnet-mcp ({OWN_PREFIX}*) setting")
        else:
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
        help="read settings from this file, BACNET_WRITES_ENABLED and "
        "OBJECT_MAP_FILE included; without it only bacnet-python/.env of a source "
        "checkout is read, and only for BACNET_* connection settings. The process "
        "environment still wins",
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
