"""Bounded pytest execution for enabled validation sessions."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from codepacex.tools.base import PathAccess, Tool, ToolResult

MAX_TIMEOUT_SECONDS = 300
MAX_OUTPUT_CHARS = 12000
SAFE_PYTEST_FLAGS = frozenset({"-q", "-x", "--disable-warnings", "--no-header", "--no-summary"})


class RunTestParams(BaseModel):
    cwd: str = Field(description="Existing workspace directory for the test")
    argv: list[str] = Field(description="Pytest argv excluding the pytest executable")
    timeout_seconds: int = Field(default=120, ge=1, le=MAX_TIMEOUT_SECONDS)
    output_cap_chars: int = Field(default=MAX_OUTPUT_CHARS, ge=256, le=MAX_OUTPUT_CHARS)

    @field_validator("argv")
    @classmethod
    def bounded_pytest_argv(cls, value: list[str]) -> list[str]:
        if not value or any(not isinstance(item, str) or not item for item in value):
            raise ValueError("argv must contain bounded pytest targets and cannot change pytest root configuration")
        if any(any(marker in item for marker in (";", "&&", "|", "`", "$", "\n")) for item in value):
            raise ValueError("argv entries cannot contain shell syntax")
        if any(item.startswith("-") and item not in SAFE_PYTEST_FLAGS for item in value):
            raise ValueError("argv contains an unsupported pytest option")
        has_target = False
        for item in value:
            if item.startswith("-"):
                continue
            target = Path(item)
            if target.is_absolute() or item == "." or ".." in target.parts:
                raise ValueError("pytest targets must remain within cwd")
            has_target = True
        if not has_target:
            raise ValueError("argv must include at least one explicit pytest target")
        return value


class RunTest(Tool):
    name = "RunTest"
    description = "Run bounded pytest targets in cwd using argv, never a shell. Returns exit code and capped stdout/stderr. Use its actual tool_call_id for reproduction or test evidence."
    params_model = RunTestParams
    category = "command"
    path_accesses = (PathAccess("cwd", "read", "workspace"),)

    async def execute(self, params: RunTestParams) -> ToolResult:
        cwd = Path(params.cwd)
        if not cwd.is_dir():
            return ToolResult(output="RunTest cwd does not exist", is_error=True)
        try:
            process = await asyncio.create_subprocess_exec("pytest", *params.argv, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=params.timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(output=f"RunTest timed out after {params.timeout_seconds}s", is_error=True, timed_out=True)
        except OSError as exc:
            return ToolResult(output=f"RunTest failed to start: {exc}", is_error=True)
        output = ("STDOUT:\n" + stdout.decode(errors="replace") + "\nSTDERR:\n" + stderr.decode(errors="replace"))[:params.output_cap_chars]
        return ToolResult(output=output, is_error=process.returncode != 0, exit_code=process.returncode)
