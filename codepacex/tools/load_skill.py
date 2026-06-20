"""提供 CodePaceX 的 load skill能力。

主要包含工具参数模型、执行逻辑和结果封装。该模块由工具注册表与 Agent 调度器调用，并维护输入校验、权限分类和副作用范围。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from codepacex.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from codepacex.agent import Agent
    from codepacex.skills.directory import register_skill_tools
    from codepacex.skills.loader import SkillLoader


# 核心实现
class LoadSkillParams(BaseModel):
    name: str = Field(description="The name of the skill to load")


class LoadSkill(Tool):
    name = "LoadSkill"
    description = (
        "Load and activate a skill by name. "
        "Returns the full SOP body so you can follow its instructions. "
        "Any specialized tools will be registered."
    )
    params_model = LoadSkillParams
    category = "read"
    is_concurrency_safe = False
    is_system_tool = True


    def __init__(self) -> None:
        self._loader: SkillLoader | None = None
        self._agent: Agent | None = None


    def set_loader(self, loader: SkillLoader) -> None:
        self._loader = loader

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent


    async def execute(self, params: BaseModel) -> ToolResult:
        assert isinstance(params, LoadSkillParams)

        if self._loader is None or self._agent is None:
            return ToolResult(
                output="Error: LoadSkill not properly initialized",
                is_error=True,
            )

        skill = self._loader.get(params.name)
        if skill is None:
            available = ", ".join(n for n, _ in self._loader.get_catalog())
            return ToolResult(
                output=f"Error: unknown skill '{params.name}'. Available skills: {available}",
                is_error=True,
            )

        self._agent.activate_skill(skill.name, skill.prompt_body)

        tool_count = 0
        if skill.is_directory and skill.source_path is not None:
            from codepacex.skills.directory import register_skill_tools
            skill_dir = skill.source_path.parent
            tool_count = register_skill_tools(skill_dir, self._agent.registry)

        header = f"# Skill: {skill.name} ({tool_count} specialized tools registered)\n\n"
        return ToolResult(output=header + skill.prompt_body)
