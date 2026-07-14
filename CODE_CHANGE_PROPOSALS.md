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

## 7. 实验 Runtime mapping 与真实基准 — Goal 2 工程闭环完成，真实运行待授权

Goal 2 没有复用只改变 manifest 标签的 legacy feature flag，而是引入受约束的 `ExperimentProfile`。`tool_loading`、`compression_profile`、`permission_strategy` 和 `agent_mode` 会传入真实子进程并改变 ToolRegistry、压缩恢复附件、权限装配与子 Agent 工具；effective profile、profile hash、runtime contract hash、Provider payload hash 和工具 Schema 字节数都会进入 Artifact。未知或 non-empty legacy `feature_flags` 继续 fail-closed。

已完成的工程资产包括：

1. 30-task controlled MCP corpus，eager/deferred 各 5 次；
2. 10×2 Retention、10×5×4 Permission、5×5×2 Multi-Agent；
3. 100-case deterministic Hook study；
4. 1×2h + 3×8h hash-chained long-session supervisor；
5. Microsoft SWE-bench-Live `python-only` 的 3 Pilot + 20 formal + 5×2 repeat 冻结与官方 evaluator adapter；
6. clean-commit 分阶段预算授权、worst-next-trial reservation、逐 Provider request Usage/CNY ledger、Trial 聚合结算和 Goal 2 Claims 自动生成；Stage B 使用成对最小 Pilot scope，正式矩阵保持不变。

截至 2026-07-14，Stage A/B 最小 Pilot、Hook、三条 SWE prediction 的 recovery evaluation，以及一个真实 2 小时长会话 Pilot 已保留为 Artifact。SWE recovery 为 0/3 resolved：aiogram/arviz 有 patch-contract miss，amoffat 与 gold 同受 emulated evaluator fd 干扰；formal 20 题因此是 `infrastructure-blocked`，本 Goal 不生成 SWE Claim。正式 MCP、Retention、Permission 和经 zero-model grader gate 放行的 Multi-Agent 仍待新基线 allocation/CI；三次 8 小时长会话延期到 follow-up Goal。fixture、mock、synthetic load 和 dry-run 均不得表述为真实效果。

## 8. Permission Git 集成测试 cwd 依赖 — 已完成

- 位置：`tests/test_permissions.py::test_e2e_rule_allows_git`。
- 实现：测试在 `tmp_path` 内初始化真实 Git 仓库，并为 Bash sandbox 配置明确 `work_dir`，不再依赖启动 pytest 的当前目录。
- 验证：用例从项目根目录和项目外 cwd 运行都能验证真实 `git status` 的权限与执行闭环，不使用 Bash mock。
