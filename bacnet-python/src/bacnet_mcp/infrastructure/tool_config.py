from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from bacnet_mcp.domain.errors import ConfigError


@dataclass(frozen=True, slots=True)
class ToolConfig:
    """Tool-level settings (the write switch lives in BACnetConfig)."""

    object_map_path: Path | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ToolConfig:
        env = os.environ if environ is None else environ
        raw = env.get("OBJECT_MAP_FILE", "").strip()
        if not raw:
            return cls()
        try:
            return cls(object_map_path=Path(raw).expanduser())
        except RuntimeError as exc:
            raise ConfigError(f"OBJECT_MAP_FILE {raw!r}: {exc}") from None
