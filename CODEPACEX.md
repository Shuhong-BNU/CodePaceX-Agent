# CodePaceX 项目开发指令

## 项目定位

CodePaceX Agent 是使用 Python 实现的终端 Coding Agent。核心能力包括模型工具循环、Plan Mode、多协议 LLM 客户端、MCP、Skill、上下文压缩、会话记忆、权限控制和多 Agent 协作。

修改代码前先确认当前模块所属层次及其上下游，避免在交互层复制引擎逻辑，或在工具实现中绕过权限检查。

## 开发环境

- Python：3.12
- 包与虚拟环境：uv
- 测试：pytest、pytest-asyncio

常用命令：

```bash
uv sync --group dev
uv run pytest -q
uv run pytest tests/test_agent.py -q
uv run python -m compileall -q codepacex tests
uv run codepacex --help
```

## 架构边界

1. 交互层：`app.py`、`remote.py`、`commands/`。只负责输入输出、审批和状态展示。
2. 引擎层：`agent.py`、`client.py`、`conversation.py`。负责模型事件、工具循环和对话状态。
3. 工具层：`tools/`、`mcp/`、`skills/`、`hooks/`。所有副作用必须经过明确的工具边界。
4. 上下文层：`context/`、`memory/`、`filehistory/`。负责持久化、Token 预算和恢复。
5. 安全协作层：`permissions/`、`agents/`、`teams/`、`worktree/`。负责权限、委派和隔离。

不要让协议客户端依赖 TUI，也不要让工具直接操作 UI。共享初始化逻辑应抽取到运行时装配层，而不是分别复制到多个入口。

## 代码规范

- 使用 snake_case 变量和函数名，类名使用 PascalCase。
- 公共参数和状态优先使用明确类型，不用无约束字典代替已有模型。
- 异步 I/O 使用 `async`/`await`，不要在事件循环中调用长时间阻塞操作。
- 系统边界需要校验输入；内部代码依赖已建立的类型和不变量。
- 不捕获后静默丢弃本应上报的业务错误。Best-effort 后台能力可以降级，但必须避免破坏主流程。
- 不为了假设中的未来需求增加兼容层、抽象或配置项。
- 修改文件和测试时使用 UTF-8。

## 注释与文档

- 每个 Python 文件保留模块 docstring，说明职责、组成、调用方和关键边界。
- 注释解释原因、约束、协议差异和非显然行为，不逐行复述代码。
- 行为变化后同步修改注释，不保留失效的迁移背景或旧实现名称。
- README 只记录实际能力、实际测试结果和可复现数据。
- 不把合成测试体积直接描述成真实 Token、延迟或吞吐收益。

## 扩展 Provider

1. 在 `client.py` 实现 `LLMClient.stream()`，转换成统一 StreamEvent。
2. 在 `serialization.py` 增加对应消息转换。
3. 在配置校验和客户端工厂中注册协议。
4. 处理鉴权、限流、网络错误、usage 和工具调用增量。
5. 为文本、thinking、工具参数、缓存 Token 和错误映射补测试。

## 扩展 Tool 与 MCP

- 本地 Tool 继承 `Tool`，使用 Pydantic 参数模型，声明 `category` 和并发安全性。
- 写文件与 Shell 工具必须经过 PermissionChecker，不能在调用方手动绕过。
- MCP 工具名称包含服务器命名空间，避免不同服务器工具冲突。
- 只有 Schema 暴露采用延迟加载；不要误写成 MCP 连接本身完全延迟。
- 外部结果属于不可信输入，不能直接当作系统指令执行。

## 扩展 Skill

- Skill 名称使用小写字母、数字和连字符。
- `allowedTools` 只授予完成工作所需的最小工具集合。
- fork Skill 必须明确上下文范围和最大轮数。
- 目录型 Skill 的 Python 实现在当前进程运行，只加载可信代码。
- 新 Skill 至少覆盖解析、加载、参数替换和工具依赖测试。

## 扩展 Agent 与 Team

- 子 Agent 必须有清晰职责和受限工具集合。
- 后台任务通过 TaskManager 回传完成通知，不直接修改 UI。
- Team 成员通过 Mailbox 和 SharedTaskStore 协作。
- 并行写入优先使用 worktree 隔离；共享目录并发写入需要显式避免冲突。
- worktree 改动不会自动合并，完成结果必须报告保留路径和分支。

## 权限与危险操作

- deny 应优先于普通 allow；任何优先级调整都需要权限回归测试。
- Plan Mode 只允许精确计划文件写入，不以字符串包含关系代替路径解析。
- PathSandbox 是应用级路径检查，不是 OS sandbox。
- 禁止在测试或实现中真实执行破坏性磁盘、权限和远程脚本命令。
- `bypassPermissions` 不应成为修复权限逻辑的捷径。

## 测试完成标准

提交前至少执行：

```bash
uv run python -m compileall -q codepacex tests
uv run pytest -q
uv run codepacex --help
```

修改协议、权限、上下文、会话或并发逻辑时，必须运行对应模块测试和全量测试。若存在失败，报告测试名、实际错误和是否为修改前已存在的问题；不得声称测试全部通过。

## 工作原则

- 先阅读再修改，优先复用现有模型和注册机制。
- 只完成用户明确要求的范围。
- 不擅自修复 `CODE_CHANGE_PROPOSALS.md` 中的问题。
- 不虚构实现状态、测试覆盖、性能数据或安全保证。
- 任何有外部副作用、破坏性或不可逆的操作都需要用户确认。
