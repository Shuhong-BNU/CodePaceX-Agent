# CodePaceX 工程闭环状态与后续提案

本文记录 PR #13 合并后的 correctness closure 结果，以及因范围或实验边界而延期的工作。当前工程基线为 `origin/main` 的 PR #13 merge commit `e44f3a1`；下列“已完成”项在其上的本地修复分支中实现并有回归测试。

## 1. 自动记忆提取落盘闭环 — 已完成

- 位置：`codepacex/memory/auto_memory.py`，`MemoryManager.extract()`。
- 实现：提取模型返回受约束 JSON；Pydantic 校验 type、name、description 和 content；文件名经过净化；user/feedback 与 project/reference 分别路由到用户级和项目级目录；记忆文件和索引使用临时文件加原子替换写入。
- 失败语义：非法 JSON、结构校验失败或写入异常均不推进提取游标；合法空结果可以推进游标；同一作用域的重复名称更新现有记忆并保持单一索引项。
- 回归覆盖：作用域路由、非法 JSON、路径穿越、重复更新、原子写入失败和游标行为。

## 2. 权限 deny 优先级 — 已完成

- 位置：`codepacex/permissions/checker.py`，`PermissionChecker.check()`。
- 实现：显式规则不再被 Plan 提前放行绕过；路径、规则和 Hook 决策统一按 `deny > ask > allow` 聚合。危险命令和设备操作仍属于不可覆盖的强制安全层。
- 目标语义：显式 deny 优先于 Plan allow，ask 优先于普通 allow；`bypassPermissions` 只能跳过模式兜底，不能跳过强制安全决定。
- 回归覆盖：Plan 精确目标叠加显式 deny，以及既有 safe/deny、sandbox、session 和 dangerous 命令组合。

## 3. Plan 文件精确路径授权 — 已完成

- 位置：`codepacex/permissions/checker.py`，`_is_plan_file()`。
- 实现：配置的计划文件和工具目标都相对项目根目录执行 `resolve()`；只有当前项目 `.codepacex/plans/` 内的配置目标与请求目标完全相等时才具有 Plan allow 效果。
- 拒绝范围：同 basename 文件、包含 `.codepacex/plans` 字样的相似目录、路径别名和项目外目标不会获得 Plan 放行。
- 回归覆盖：同名文件、相似嵌套目录和精确目标上的显式 deny。

## 4. TUI、Remote、`-p` 运行时 capability contract — 设计完成，实现延期

进一步核验表明三个入口并非只缺少一两个工具，而是具有不同的同步/异步生命周期、事件消费者和关闭责任。直接抽取完整 RuntimeBuilder 会大幅改变 CLI/TUI/Remote 生命周期，触发本轮停止实现阈值；因此本轮不加入只改变标签而不消除遗漏的伪 contract。

当前 capability contract：

| 能力 | TUI | Remote | `-p` | 合同分类 |
| --- | --- | --- | --- | --- |
| provider/client、核心 ToolRegistry、权限检查、项目指令、`ToolSearch`、`InstallSkill`、Agent loop、上下文遥测 | 是 | 是 | 是 | 所有入口共享 |
| Session、Memory、Skill loader/`LoadSkill`、MCP 生命周期 | 是 | 是 | 否 | 当前交互入口共享，需评估是否提升为核心 |
| FileHistory、`AskUserQuestion`、`ExitPlanMode`、worktree/team UI | 是 | 否 | 否 | TUI 交互专属 |
| 子 Agent、worktree-backed delegation、Team 工具 | 是 | 否 | 是 | TUI 与 `-p` 共享，Remote 当前缺失 |
| 流式非交互输出、拒绝式审批 | 否 | 否 | 是 | `-p` 专属 |

最小后续方案：

1. 定义不可变 `RuntimeProfile`，显式列出 core、interactive、prompt-only capability 和资源关闭责任。
2. 提取只负责公共 client/registry/permission/instructions/Agent 基础装配的 `RuntimeBuilder`；MCP、Session、Memory 和 UI 工具通过 profile adapter 接入。
3. 统一异步 `close()`/上下文管理协议，不改变各入口事件消费方式。
4. 增加入口级 characterization 测试：断言每个 profile 的必需工具、manager、MCP 生命周期和关闭行为，并要求每个差异有显式声明。

## 5. Agent Hook action — 已完成安全收口

- 位置：`codepacex/hooks/loader.py`、`codepacex/hooks/executors.py`。
- 实现：当前支持的 action 明确为 `command`、`prompt` 和 `http`；未实现的 `agent` action 在配置加载阶段被拒绝。
- 防御语义：即使内部代码直接调用 `execute_agent()`，也返回失败而不是 stub success。
- 后续：只有在受限 Agent runner、超时、工具白名单、递归防护和输出上限均有设计与测试后，才重新开放配置协议。

## 6. Worktree inspect → approve → integrate — 新功能，延期

- 位置：`codepacex/worktree/`、`codepacex/tools/agent_tool.py`、团队清理逻辑。
- 当前行为：隔离 Agent 的改动保留在独立 worktree 和分支，不会自动合并；这属于安全保留机制，不是完整集成工作流。
- 设计方向：inspect 阶段输出 branch、commit、diffstat、未提交状态和冲突预检；approve 阶段要求用户选择拒绝、保留或集成；integrate 阶段执行受控操作并记录结果。
- 安全约束：不得自动覆盖脏主工作区，不得把无冲突预检等同于可安全集成，用户拒绝必须保持零主工作区修改。
- 后续测试：无改动、未提交改动、已有提交、冲突、脏主工作区、用户拒绝和中途失败恢复。

## 7. Feature Flag runtime mapping 与真实基准 — 延期到实验 Goal

当前 Pilot 与 Claims 会拒绝所有未注册或未映射的 feature flag。这是刻意的 fail-closed 行为：仅把 flag 写入 manifest 标签而不改变运行时，不允许作为 A/B 证据。

首选最小实验为 `deferred_tools`，但真实闭环需要同时修改 Pilot 子进程配置、核心 ToolRegistry、三个入口的装配、有效运行时证据和 Claims 兼容规则，已经超出本轮小范围修复。后续方案：

1. 冻结 flag 语义：`true` 使用当前 deferred/discovery 行为，`false` 在首个模型请求前使同一工具集合的 Schema 全部可见。
2. 把 flag 写入子进程可读取的冻结配置，并由 RuntimeBuilder 应用；禁止仅在 manifest 中记标签。
3. 在 run event/manifest 中记录应用后的 effective flag、初始 Schema 哈希、可见工具名哈希和 CodePaceX commit。
4. 单元测试证明同一 registry 在 flag 两侧产生不同的初始 Schema/哈希，同时工具执行语义不变；集成测试证明 Pilot 记录值与子进程应用值一致。
5. 只有上述证据通过后，Claims 才允许以该 flag 作为唯一实验差异；未知 flag 继续拒绝。

本轮没有运行真实模型、真实网络实验或付费调用，也没有运行真实 Pilot、SWE-bench、AgentRouter、正式 A/B 或长会话。fixture、mock、合成 Schema 和 dry-run 不能被表述为这些真实实验。

## 8. Permission Git 集成测试 cwd 依赖 — 已完成

- 位置：`tests/test_permissions.py::test_e2e_rule_allows_git`。
- 实现：测试在 `tmp_path` 内初始化真实 Git 仓库，并为 Bash sandbox 配置明确 `work_dir`，不再依赖启动 pytest 的当前目录。
- 验证：用例从项目根目录和项目外 cwd 运行都能验证真实 `git status` 的权限与执行闭环，不使用 Bash mock。
