"""提供 CodePaceX 的 review能力。

主要包含斜杠命令的解析、注册、补全与处理器。该模块由终端应用的命令分发层调用，并维护命令参数和 UI 状态一致性。
"""

from __future__ import annotations

from codepacex.commands.registry import Command, CommandContext, CommandType


REVIEW_PROMPT = (
    "请审查当前 git diff 中的代码变更。重点关注：\n"
    "1. 逻辑错误\n"
    "2. 安全问题\n"
    "3. 性能问题\n"
    "4. 代码风格"
)


# 核心实现
async def handle_review(ctx: CommandContext) -> None:
    prompt = REVIEW_PROMPT
    if ctx.args:
        prompt += f"\n\n额外关注：{ctx.args}"
    ctx.ui.send_user_message(prompt)


REVIEW_COMMAND = Command(
    name="review",
    description="审查代码变更",
    usage="/review [额外关注点]",
    type=CommandType.PROMPT,
    handler=handle_review,
)

