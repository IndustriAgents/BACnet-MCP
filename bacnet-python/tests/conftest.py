"""Fixtures for the BACnet MCP server tests."""

from __future__ import annotations

import os

import pytest
from helpers import ENV_KEYS


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A private copy of os.environ without any BACNET_* / OBJECT_MAP_FILE.

    Anything a test (or load_dotenv) writes goes to the copy, which is
    discarded afterwards.
    """
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    monkeypatch.setattr(os, "environ", env)
    return env
