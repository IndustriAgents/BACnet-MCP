"""Application logic depends on normalized ports, not MCP or bacpypes3."""

import ast
from pathlib import Path


def test_application_and_domain_are_independent_of_native_protocol_types():
    package = Path(__file__).resolve().parents[1] / "src/bacnet_mcp"
    for layer in ("application", "domain"):
        for filename in (package / layer).glob("*.py"):
            for node in ast.walk(ast.parse(filename.read_text())):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                assert not any(name.split(".")[0] in {"mcp", "bacpypes3"} for name in modules), (
                    filename
                )
