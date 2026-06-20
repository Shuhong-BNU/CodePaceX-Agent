# CodePaceX 代码修改提案

本文记录审阅过程中发现、但尚未获准实施的业务代码修改。每项提案都需要单独确认后再进入编码阶段。

## 1. 自动记忆提取缺少落盘闭环

- 位置：`codepacex/memory/auto_memory.py`，`MemoryManager.extract()`。
- 当前行为：函数调用 LLM 并收集文本到 `collected`，随后只更新消息计数；返回内容没有解析，也没有创建记忆文件或更新 `MEMORY.md`。
- 风险：界面和提示词表现为支持自动记忆，但会话结束后实际没有新增可召回内容。
- 建议方案：让提取模型返回受约束 JSON；校验 `type`、标题、描述和正文；净化文件名；按 user/project 类型路由；使用临时文件加原子替换写入；去重并更新索引。
- 目标行为：值得保留的对话信息能够稳定写入正确作用域，无有效内容时不修改磁盘，部分失败不会损坏已有索引。
- 测试方法：覆盖空结果、非法 JSON、路径穿越、重复记忆、用户/项目路由、原子写入失败和下一会话召回。

## 2. 权限流水线不是严格 deny 优先

- 位置：`codepacex/permissions/checker.py`，`PermissionChecker.check()`。
- 当前行为：被识别为安全的 Shell 命令会在规则引擎之前直接返回 allow，因此显式 deny 规则可能没有机会生效。
- 风险：用户认为已禁止的命令仍可能执行，实际语义不符合纵深权限模型。
- 建议方案：区分强制 deny、显式规则、沙箱结果、会话 allow 和模式默认值；先收集不可覆盖的 deny，再决定 allow/ask。为每个优先级写成可读的决策表。
- 目标行为：危险命令和显式 deny 始终优先；普通安全命令只在没有更高优先级限制时自动放行。
- 测试方法：增加 safe+deny、sandbox+allow、session allow+deny、bypass+dangerous 等组合测试。

## 3. Plan 文件路径判断过宽

- 位置：`codepacex/permissions/checker.py`，`_is_plan_file()` 及 Plan Mode 提前放行分支。
- 当前行为：目标路径只要包含 `.codepacex/plans/`，或 basename 与计划文件相同，就可能被视为计划文件；提前返回还会跳过后续路径检查。
- 风险：构造相似路径可能扩大 Plan Mode 的写入范围。
- 建议方案：保存唯一的已解析计划文件绝对路径；对目标路径执行 `resolve()`；只允许完全相等；计划文件也必须位于当前项目的 `.codepacex/plans` 下并经过 PathSandbox。
- 目标行为：Plan Mode 只能写当前会话生成的唯一计划文件。
- 测试方法：覆盖绝对/相对路径、符号链接、同名文件、包含相似目录名、`..` 和项目外路径。

## 4. 非交互模式运行时装配不完整

- 位置：`codepacex/__main__.py` 的 `_run_prompt()`，以及 `codepacex/app.py` 的初始化流程。
- 当前行为：`-p` 模式创建基础工具和子 Agent，但没有完整复用 TUI 的 MCP、Skill、Memory、Session、文件历史等装配过程。
- 风险：同一个任务在 TUI 和 CI/脚本模式下拥有不同能力，文档和用户预期容易失真。
- 建议方案：抽取 RuntimeBuilder/RuntimeContext，统一构造 client、registry、permission、MCP、Skill、Memory、Session、TeamManager 和清理钩子；不同界面只负责事件消费。
- 目标行为：TUI、Remote 和非交互模式共享相同核心能力，可通过显式选项关闭交互专属工具。
- 测试方法：为三个入口比较注册工具、MCP 生命周期、会话写入、记忆注入和关闭清理。

## 5. Agent Hook executor 尚未实现

- 位置：`codepacex/hooks/executors.py`，`execute_agent()`。
- 当前行为：Agent 类型 Hook 返回 stub 文本，没有真正执行受限 Agent。
- 风险：配置能够通过校验但不会产生预期效果，属于静默功能缺失。
- 建议方案：二选一：注入受限 Agent runner 并实现超时、工具白名单和结果截断；或从配置协议中删除 Agent action，直到实现完成。
- 目标行为：受支持的 Hook action 都有明确、可测试的执行语义；未支持类型在加载时失败。
- 测试方法：覆盖成功、超时、异常、权限拒绝、递归 Hook 防护和输出上限。

## 6. Worktree 缺少显式变更集成流程

- 位置：`codepacex/worktree/`、`codepacex/tools/agent_tool.py`、团队清理逻辑。
- 当前行为：隔离 Agent 完成后保留含改动的 worktree 和分支，但不会生成结构化 diff、冲突预检或受控集成操作。
- 风险：用户需要手动定位和合并，多 Agent 并行产生的冲突无法在任务层表达。
- 建议方案：增加 inspect → approve → integrate 三阶段流程；先报告 commit、diffstat 和冲突预检，再由用户选择 cherry-pick、merge 或保留。
- 目标行为：任何工作区集成都可审查、可拒绝、可追踪，不自动覆盖主工作区未提交改动。
- 测试方法：覆盖无改动、未提交改动、已有提交、冲突、主工作区脏状态和用户拒绝。

## 7. 性能结论缺少真实基准

- 位置：延迟工具、上下文压缩和多 Agent 相关测试及文档。
- 当前行为：延迟工具只有模拟 Schema 字符体积测试；长会话与多 Agent 没有统一任务集和测量工具。
- 风险：把合成体积等同 Token，把机制存在等同稳定性或加速比，会产生不可复现结论。
- 建议方案：新增独立 benchmark 目录，固定模型、提示、工具集合、任务语料和重复次数；记录 Token、墙钟时间、成功率、压缩次数、恢复完整度和总成本。
- 目标行为：每个数字都能通过命令复现，并明确环境、样本量和误差范围。
- 测试方法：benchmark 不进入普通单元测试门禁；在固定配置下生成 JSON 和 Markdown 报告，并校验报告字段完整性。

## 8. 权限集成测试依赖不存在的 Git 仓库

- 位置：`tests/test_permissions.py::test_e2e_rule_allows_git`。
- 当前行为：测试在 `tempfile.mkdtemp()` 创建的普通目录中运行 `git status`，同时断言命令成功。
- 风险：全量测试在正常环境稳定出现 1 个失败，掩盖真正的回归。
- 建议方案：测试开始时在临时目录执行 `git init`，或把命令改为不依赖仓库的只读 Git 命令；不要 mock Bash，因为该测试需要验证端到端权限与执行结果。
- 目标行为：测试只验证权限规则是否免于 HITL，同时提供满足命令成功条件的真实环境。
- 测试方法：单独运行该用例和全量测试，预期从 553/1 变为 554/0。
