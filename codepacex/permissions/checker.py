"""提供 CodePaceX 的多级工具权限判定能力。

主要包含权限模式、危险命令检测、路径沙箱和分级规则。该模块由所有工具执行前的权限检查调用，并维护默认拒绝、人工确认和工作区边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codepacex.permissions.dangerous import DangerousCommandDetector, is_safe_command
from codepacex.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from codepacex.permissions.rules import RuleEngine, extract_content
from codepacex.permissions.sandbox import PathSandbox
from codepacex.tools.base import Tool

_PLAN_MODE_ALLOWED_TOOLS = frozenset({"Agent", "ToolSearch", "AskUserQuestion", "ExitPlanMode"})


# 核心实现
@dataclass
class Decision:
    effect: DecisionEffect
    reason: str
    persistable: bool = True


@dataclass
class PermissionAssessment:
    tool: Tool
    content: str
    constraints: list[tuple[DecisionEffect, str]] = field(default_factory=list)
    explicit_effect: DecisionEffect | None = None
    explicit_reason: str = ""
    mandatory_denied: bool = False
    mandatory_safety: bool = False

    def add(self, effect: DecisionEffect, reason: str) -> None:
        self.constraints.append((effect, reason))


class PermissionChecker:


    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
        sandbox_enabled: bool = False,
        session_allow_all: bool = False,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.sandbox_enabled = sandbox_enabled
        self.session_allow_all = session_allow_all
        self.plan_file_path: str = ""
        # Layer 4b: 会话级 allow-always 集合（内存中，不持久化）
        # 存放格式为 "ToolName:pattern"，用户选择 "don't ask again" 时记录
        self._session_allowed: set[str] = set()


    def add_session_allow(self, tool_name: str, content: str) -> None:
        """将工具+内容模式加入会话级放行集合（Layer 4b）。

        比持久化规则引擎优先级更高，但不写入磁盘——会话结束即消失。
        """
        key = f"{tool_name}:{content}"
        self._session_allowed.add(key)

    def _check_session_allowed(self, tool_name: str, content: str) -> bool:
        """检查是否匹配会话级放行记录。"""
        if not self._session_allowed:
            return False
        key = f"{tool_name}:{content}"
        if key in self._session_allowed:
            return True
        # 前缀匹配：已记录的 pattern 可能带通配尾缀
        for allowed in self._session_allowed:
            if allowed.endswith("*") and key.startswith(allowed[:-1]):
                return True
        return False

    @staticmethod
    def describe_tool_action(tool_name: str, arguments: dict[str, Any]) -> str:
        """从工具参数中提取适合人工确认界面展示的操作摘要。"""
        content = extract_content(tool_name, arguments)
        if content:
            return content
        # 无法从标准字段提取时，拼接参数摘要
        parts = []
        for k, v in arguments.items():
            sv = str(v)
            if len(sv) > 80:
                sv = sv[:77] + "..."
            parts.append(f"{k}={sv}")
        return ", ".join(parts) if parts else tool_name


    def assess(self, tool: Tool, arguments: dict[str, Any]) -> PermissionAssessment:
        content = extract_content(tool.name, arguments)
        assessment = PermissionAssessment(tool=tool, content=content)

        # Mandatory safety is an un-bypassable floor.
        if tool.category == "command":
            try:
                effect, reason = self.detector.assess(content, self.sandbox.project_root)
            except Exception as exc:
                effect, reason = "ask", f"危险命令检查失败: {exc}"
            if effect:
                assessment.add(effect, f"危险命令检查: {reason}")
                assessment.mandatory_denied = effect == "deny"
                assessment.mandatory_safety = True

        # Plan-mode exceptions are ordinary policy, never safety overrides.
        if self.mode == PermissionMode.PLAN:
            if tool.name in _PLAN_MODE_ALLOWED_TOOLS:
                assessment.explicit_effect = "allow"
                assessment.explicit_reason = "Plan mode: allowed tool"
            if tool.name in ("WriteFile", "EditFile") and content:
                if self._is_plan_file(content):
                    assessment.explicit_effect = "allow"
                    assessment.explicit_reason = "Plan mode: plan file write"

        for path_access in tool.path_accesses:
            raw_path = arguments.get(path_access.field)
            if not isinstance(raw_path, str) or not raw_path:
                assessment.add("ask", f"路径参数 {path_access.field} 缺失或无效")
                continue
            try:
                ok, reason = self.sandbox.check(
                    raw_path,
                    access=path_access.mode,
                    workspace_only=True,
                )
            except Exception as exc:
                ok, reason = False, f"路径检查失败: {exc}"
            if not ok:
                effect: DecisionEffect = "deny" if path_access.scope == "workspace" else "ask"
                assessment.add(effect, f"路径沙箱拦截 {path_access.field}: {reason}")

        try:
            if tool.requires_explicit_authorization:
                assessment.add("ask", "外部网络访问和持久化供应链变更需要当次授权")
        except Exception as exc:
            assessment.add("ask", f"强制授权检查失败: {exc}")

        try:
            rule_result = self.rule_engine.evaluate(tool.name, content)
        except Exception as exc:
            assessment.add("ask", f"权限规则检查失败: {exc}")
        else:
            if rule_result is not None:
                if assessment.explicit_effect is None:
                    assessment.explicit_effect = rule_result
                    assessment.explicit_reason = f"权限规则: {rule_result}"
                else:
                    assessment.add(rule_result, f"权限规则: {rule_result}")
        return assessment

    def finalize(
        self,
        assessment: PermissionAssessment,
        *,
        hook_effect: DecisionEffect | None = None,
        hook_reason: str = "",
    ) -> Decision:
        effects = list(assessment.constraints)
        if hook_effect:
            effects.append((hook_effect, hook_reason or "pre_tool_use Hook 限制"))

        if assessment.explicit_effect is not None:
            policy_effect = assessment.explicit_effect
            policy_reason = assessment.explicit_reason
        elif self.session_allow_all:
            policy_effect, policy_reason = "allow", "实验会话级预授权"
        elif self._check_session_allowed(assessment.tool.name, assessment.content):
            policy_effect, policy_reason = "allow", "会话级放行"
        elif self.sandbox_enabled and assessment.tool.category == "command":
            try:
                auto_allowed = is_safe_command(assessment.content, self.sandbox.project_root)
            except Exception as exc:
                effects.append(("ask", f"沙箱自动放行检查失败: {exc}"))
                auto_allowed = False
            if auto_allowed:
                policy_effect, policy_reason = "allow", "受限只读命令在 OS 沙箱内执行"
            else:
                policy_effect, policy_reason = "ask", "命令不符合沙箱自动放行白名单"
        else:
            policy_effect = mode_decide(self.mode, assessment.tool.category)
            policy_reason = f"权限模式 {self.mode.value}: {policy_effect}"
        effects.append((policy_effect, policy_reason))

        order = {"allow": 0, "ask": 1, "deny": 2}
        final_effect = max((effect for effect, _ in effects), key=order.__getitem__)
        reasons: list[str] = []
        for effect, reason in effects:
            if order[effect] == order[final_effect] and reason and reason not in reasons:
                reasons.append(reason)
        persistable = final_effect == "ask" and not assessment.constraints and hook_effect is None and assessment.explicit_effect is None
        return Decision(final_effect, "; ".join(reasons), persistable=persistable)

    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        try:
            return self.finalize(self.assess(tool, arguments))
        except Exception as exc:
            return Decision("ask", f"权限安全检查失败: {exc}", persistable=False)


    def _is_plan_file(self, target_path: str) -> bool:
        if not self.plan_file_path or not target_path:
            return False
        try:
            root = self.sandbox.project_root.resolve()
            plans_root = (root / ".codepacex" / "plans").resolve()

            target = Path(target_path).expanduser()
            if not target.is_absolute():
                target = root / target
            plan = Path(self.plan_file_path).expanduser()
            if not plan.is_absolute():
                plan = root / plan

            resolved_target = target.resolve()
            resolved_plan = plan.resolve()
            resolved_plan.relative_to(plans_root)
            return resolved_target == resolved_plan
        except (OSError, RuntimeError, ValueError):
            return False
