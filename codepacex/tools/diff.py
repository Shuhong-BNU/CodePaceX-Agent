"""Read-only unified diff tool and reusable diff formatter."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from codepacex.tools.base import Tool, ToolResult

MAX_DIFF_LINES = 200


@dataclass
class DiffResult:
    text: str
    additions: int
    removals: int


def build_diff(old_content: str, new_content: str, *, fromfile: str = "before", tofile: str = "after") -> DiffResult:
    lines = list(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=tofile,
            n=3,
        )
    )
    additions = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removals = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + [f"… diff truncated at {MAX_DIFF_LINES} lines\n"]
    return DiffResult("".join(lines).rstrip(), additions, removals)


class DiffParams(BaseModel):
    old_file: str = Field(description="Path to the original file")
    new_file: str = Field(description="Path to the changed file")


class Diff(Tool):
    name = "Diff"
    description = "Compare two files and return a unified diff."
    params_model = DiffParams
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: DiffParams) -> ToolResult:
        old_path, new_path = Path(params.old_file), Path(params.new_file)
        try:
            old = old_path.read_text(encoding="utf-8")
            new = new_path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(output=f"Error reading files: {exc}", is_error=True)
        result = build_diff(old, new, fromfile=str(old_path), tofile=str(new_path))
        if not result.text:
            return ToolResult(output="Files are identical.")
        return ToolResult(output=result.text)
