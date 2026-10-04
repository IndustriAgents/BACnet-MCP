"""CLI entry point for the BACnet MCP server."""

from __future__ import annotations

from dotenv import find_dotenv, load_dotenv

from .server import BACnetMCPServer


def main() -> None:
    # Load .env from the working directory (or a parent) before anything reads
    # the configuration; the process environment still wins over the file.
    load_dotenv(find_dotenv(usecwd=True))
    try:
        BACnetMCPServer().run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
