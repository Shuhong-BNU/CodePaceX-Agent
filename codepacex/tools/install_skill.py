"""Tool wrapper for safe Skill installation."""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field

from codepacex.tools.base import Tool, ToolResult


class InstallSkillParams(BaseModel):
    url: str = Field(description="HTTPS GitHub tree or raw SKILL.md URL")
    overwrite: bool = Field(default=False, description="Replace an installed skill with the same name")


class InstallSkill(Tool):
    name = "InstallSkill"
    description = "Download and atomically install a Skill from GitHub."
    params_model = InstallSkillParams
    category = "write"
    should_defer = True

    def __init__(self) -> None:
        self._on_installed: Callable[[], None] | None = None

    def set_on_installed(self, callback: Callable[[], None]) -> None:
        self._on_installed = callback

    async def execute(self, params: InstallSkillParams) -> ToolResult:
        from codepacex.skills.install import install_skill, parse_skill_url

        try:
            report = await install_skill(parse_skill_url(params.url), overwrite=params.overwrite)
        except Exception as exc:
            return ToolResult(output=f"Skill installation failed: {exc}", is_error=True)
        if self._on_installed:
            self._on_installed()
        return ToolResult(output=f"Installed {report.name}: {report.file_count} files, {report.total_bytes} bytes, sha256={report.sha256}")
