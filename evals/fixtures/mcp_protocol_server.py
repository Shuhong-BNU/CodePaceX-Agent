"""Deterministic local MCP server used only by the controlled Goal 2 study."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP


SERVER_NAME = "codepacex-goal2-fixture"
TOOL_COUNT = 50
mcp = FastMCP(SERVER_NAME)


def _register_tool(index: int) -> None:
    name = f"tool_{index:02d}"

    async def deterministic_lookup(key: str) -> str:
        return json.dumps(
            {"tool": name, "key": key, "value": f"{name}:{key}"},
            sort_keys=True,
        )

    deterministic_lookup.__name__ = name
    deterministic_lookup.__doc__ = (
        f"Return the deterministic Goal 2 fixture value for namespace {name}."
    )
    mcp.tool(name=name)(deterministic_lookup)


for _index in range(1, TOOL_COUNT + 1):
    _register_tool(_index)


if __name__ == "__main__":
    mcp.run(transport="stdio")
