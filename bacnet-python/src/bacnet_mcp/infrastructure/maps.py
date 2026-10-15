from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ObjectMap:
    """Aliases for BACnet properties, loaded from a JSON file.

    The file is re-read when it changes. A file that cannot be read or parsed
    is reported through ``error`` instead of silently becoming empty.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._objects: dict[str, Any] = {}
        self._mtime: float | None = None
        self.error: str | None = None
        self.refresh()

    def refresh(self) -> None:
        if not self.path:
            self._objects, self._mtime, self.error = ({}, None, None)
            return
        try:
            mtime = self.path.stat().st_mtime
        except OSError as exc:
            self._objects, self._mtime = ({}, None)
            self.error = f"cannot read object map {self.path}: {exc.strerror or exc}"
            return
        if self._mtime is not None and mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self._objects = {}
            self.error = f"cannot read object map {self.path}: {exc}"
            return
        try:
            data = json.loads(text)
        except ValueError as exc:
            self._objects = {}
            self.error = f"object map {self.path} is not valid JSON: {exc}"
            return
        if not isinstance(data, dict):
            self._objects = {}
            self.error = f"object map {self.path} must be a JSON object keyed by alias"
            return
        self._objects = {str(alias): spec for alias, spec in data.items()}
        self.error = None

    def get(self, alias: str) -> Any:
        self.refresh()
        return self._objects.get(alias)

    def list(self) -> list[dict[str, Any]]:
        self.refresh()
        entries = []
        for alias, spec in self._objects.items():
            spec = spec if isinstance(spec, dict) else {}
            entries.append(
                {
                    "alias": alias,
                    "device": spec.get("device"),
                    "address": spec.get("address"),
                    "object_type": spec.get("object_type"),
                    "object_instance": spec.get("object_instance"),
                    "property_id": spec.get("property_id", "present-value"),
                    "priority": spec.get("priority"),
                    "description": spec.get("description"),
                }
            )
        return entries

    def count(self) -> int:
        self.refresh()
        return len(self._objects)
