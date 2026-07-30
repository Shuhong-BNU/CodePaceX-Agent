# CodePaceX 当前能力、简历与面试叙事

> **文档版本**：v1.0.0
> **生成时间**：2026-07-30 15:02（UTC+8）
> **文档状态**：Current
> **适用场景**：简历、面试、自我复盘、项目介绍、招聘沟通。
> **当前事实快照**：Goal 4 `20/20 scorable, 4 resolved`；V3.0 `20 terminal, 19 scorable, 5 resolved, 1 infra`；路径 A，不补跑。
> **核心原则**：展示真实工程与研究能力，不把 1 题净增夸大为确定性因果提升。

---

# 0. 当前项目一句话定位

> **CodePaceX-Agent 是一个面向真实代码仓库的终端 AI Coding Agent，以及围绕它构建的可验证、可计费、可恢复评测系统。**

项目并不只是在“生成代码”，而是覆盖：

```text
理解任务
→ 搜索仓库
→ 调用工具
→ 修改代码
→ 运行测试
→ 导出 Candidate
→ official evaluator
→ Usage / 成本
→ Artifact / Claims
→ 失败归因
→ 能力迭代
```

---

# 1. 当前版本展现出的能力

## 1.1 Agent 产品能力

### Agent Loop 与工作模式

- ReAct 多轮工具循环；
- Plan Mode；
- 模型根据工具反馈决定下一步；
- 请求 ceiling 与完成终态；
- 任务执行、停止和异常处理。

### 工具能力

- ReadFile；
- WriteFile；
- EditFile；
- Bash；
- Glob；
- Grep；
- RunTest / evaluator 接线；
- 工具注册、参数解析和结果回传。

### 多模型与协议

- Anthropic；
- OpenAI；
- OpenAI-compatible；
- 流式事件归一化；
- Provider profile 与模型切换；
- retry / fallback 配置。

### 上下文与记忆

- 大工具结果落盘；
- 对话压缩；
- tool_use / tool_result 一致性；
- JSONL 会话恢复；
- 跨会话记忆；
- consolidation。

### 扩展能力

- MCP；
- Skill；
- deferred tool loading；
- Sub-Agent；
- Team / Coordinator；
- Git worktree 隔离；
- mailbox 通信。

### 安全能力

- deny > ask > allow；
- 危险命令；
- 路径边界；
- symlink 真实路径；
- protected directories；
- Human-in-the-loop；
- macOS Seatbelt；
- Linux bwrap；
- Hook 顺序。

---

## 1.2 Agent Eval 能力

### 从轻量 Eval 到官方 SWE

项目经历：

```text
6-task lightweight baseline
→ Goal 2 实验控制面
→ Goal 3 official evaluator Pilot
→ Goal 4 formal 20-task baseline
→ Evaluation V2 Golden Path
→ Capability V3 replay
```

### 正式能力基线

Goal 4：

```text
20 / 20 scorable
4 resolved
16 unresolved
537 Provider requests
CNY 165.044424 verified actual cost
```

### V3 replay

```text
20 terminal
19 scorable
5 resolved
14 unresolved
1 infrastructure_error
429 known-Usage Provider requests
CNY 132.932760 ledger consumption
```

### 评测单位边界

能够区分：

- Study；
- Instance；
- Trial；
- Attempt；
- Run；
- Pilot；
- Diagnostic；
- zero-provider；
- infrastructure retry；
- evaluator-only recovery；
- Artifact；
- Claim。

---

## 1.3 Agent Infra 能力

### Provider 成本治理

- 每请求 reservation；
- charge；
- Usage settlement；
- conservative settlement；
- hard cap；
- parent / child allocation；
- `active_reservation=null` 收口；
- 不确定 Usage 不伪造 Token。

### 运行身份冻结

冻结：

- task / base commit；
- problem statement；
- model / Provider；
- Prompt；
- tool schema；
- evaluator commit；
- pricing；
- runtime；
- authorization；
- request ceiling；
- retry / fallback。

### 环境隔离

- 每题独立 workspace；
- evaluator 与 controller 隔离；
- 防止任务安装依赖污染宿主；
- Linux x86_64；
- Docker；
- disk / inode telemetry；
- venv / cache 回收。

### 失败恢复

- task-scoped transport failure；
- request ceiling finalization；
- Candidate snapshot / restore；
- fail-closed；
- Artifact 上传；
- two-run infrastructure recovery；
- 原 Attempt 永久保留。

---

## 1.4 实验与研究能力

### 失败归因

不是只看 resolved / unresolved，而是分析：

```text
上游原因
→ Agent 错误决策
→ Patch 近端失败
→ F2P / P2P 表面
→ 架构层问题
```

Goal 4 的主要认识：

> 主要问题不是普遍找不到代码，而是找到相关代码后，难以恢复真实契约并形成最小、完整、经过针对性验证的 Patch。

### 论文与开源项目研究

研究目的不是堆名词，而是从失败类型映射机制：

- repository-grounded evidence；
- contract recovery；
- bounded hypotheses；
- impact-aware validation；
- anytime Candidate；
- differential validation。

### Treatment 审计

V3 replay 后继续审计：

```text
feature exists
≠ feature activated
≠ model saw it
≠ model used it
≠ feature caused improvement
```

最终发现：

- Candidate / budget / failure isolation 真实生效；
- Evidence / Hypothesis / Matrix / Differential 未充分激活；
- ImpactSlice 被 venv/site-packages 污染；
- 84 个 Candidate 全为 C1。

这展示了实验设计与科学审慎，而不是只追求好看的分数。

---

## 1.5 工程成熟度

### 已经具备

- 真实失败保留；
- 不覆盖 Trial；
- 不把 workflow success 当实验成功；
- 不把 zero-provider 当模型能力；
- 不把 infrastructure error 当 unresolved；
- 不确定 Usage 不伪造；
- 实验结果和简历 Claim 有证据边界；
- GitHub CI、Artifact、digest 和报告可核验；
- 能发现控制面与数据面失衡。

### 当前仍需改进

- Evidence 真正进入 Agent 决策；
- repository anchor；
- impacted-test 精度；
- C2/C3 Candidate；
- paired causal evidence；
- fresh holdout 泛化证据；
- Python / runtime compatibility contract。

---

# 2. 按时间讲清项目演进

## 30 秒版本

> CodePaceX 是我围绕真实代码仓库构建的终端 Coding Agent。除了 ReAct、工具、MCP、记忆、多 Agent 和权限系统，我重点搭建了一套真实 Provider、SWE-bench-Live official evaluator、成本账本和 Artifact 评测闭环。2026 年 7 月我先建立 20 题正式基线，4 题 resolved；随后对 16 个失败做因果归因并设计 Capability V3。V3 replay 保留了原有 4 个成功任务并新增解决 1 题，同时实现单题网络故障隔离和 Candidate 恢复。我又通过 Artifact 审计发现核心 Evidence 机制没有充分激活，因此下一步先修 activation fidelity，再做小型严格配对和 fresh holdout。

---

## 1 分钟版本

> 项目最开始是一个终端 Coding Agent，支持 ReAct、Plan、文件和 Bash 工具、MCP、Skill、上下文压缩、记忆、多 Agent 和权限沙箱。之后我发现只展示功能不够，需要知道它在真实软件工程任务上到底能不能工作，所以从 2026 年 7 月开始建设评测线。
>
> 我先建立真实 Provider、Usage、预算和 Artifact；然后在 Linux x86_64 和 Docker 中接通 SWE-bench-Live official evaluator。Goal 4 用固定 20 题得到 20/20 可评分、4 resolved 的正式基线。
>
> 接着我没有盲目重跑，而是分析 16 个失败，发现核心问题是契约理解、修改传播和测试选择。随后重建 Evaluation V2 的 Golden Path，并基于论文和开源项目设计 Capability V3。
>
> V3 最终得到 19/20 可评分、5 resolved，保留全部历史成功任务；请求从 537 降到 429。不过我继续审计发现 V3 的 Candidate 和恢复机制生效了，但 Evidence、Hypothesis 和 Differential 没充分激活。因此我现在把下一轮目标收缩为让 Evidence 真正进入模型决策，先 zero-provider 验收，再做 4 题 V2/V3 配对，而不是再盲跑 20 题。

---

## 3 分钟结构

### 第一段：产品

- Coding Agent 是什么；
- ReAct、工具、上下文、记忆、MCP、多 Agent、安全；
- 为什么不仅是聊天机器人。

### 第二段：评测

- 为什么需要真实 SWE；
- Goal 2 建成本和证据；
- Goal 3 打通 official evaluator；
- Goal 4 得到 4/20 基线。

### 第三段：失败驱动迭代

- 16 题归因；
- 控制面和数据面；
- Stage C 协议死锁教训；
- Evaluation V2 Golden Path；
- V3 A/B/C/D。

### 第四段：结果与边界

- V3 5 resolved / 19 scorable + 1 infra；
- haystack 转化；
- 没有历史 resolved 回退；
- 请求下降；
- 不能把净增 1 题全部归因于 V3。

### 第五段：下一步

- Evidence-to-Decision；
- activation ladder；
- zero-provider；
- 4 题 paired Pilot；
- 8 题 fresh holdout。

---

# 3. 当前推荐简历版本

## 项目标题

**CodePaceX-Agent｜终端 AI Coding Agent 与可验证评测系统**

## 项目介绍

面向真实代码仓库构建终端 Coding Agent，支持 ReAct/Plan、文件与 Bash 工具、MCP/Skill、上下文压缩、跨会话记忆、多 Agent 协作、权限聚合及 macOS/Linux 沙箱，并围绕真实 Provider 调用搭建可复现、可计费、可恢复的评测闭环。

## 推荐 5 条 Bullet

- 搭建基于 SWE-bench-Live official evaluator 的正式评测链路，贯通任务冻结、Agent/Provider 执行、Candidate 导出、官方评分、Usage/Token、预算账本、Artifact 与 Claims；Goal 4 完成 `20/20` 可评分并解决 `4` 题。
- 对 `16` 个 unresolved 实例开展逐题轨迹与 F2P/P2P 因果归因，识别契约/API 形态推断、改动传播、异常边界、预算收敛及测试噪声等主要失败来源。
- 基于失败证据及 Coding Agent 论文、成熟开源项目机制设计 Capability V3，引入仓库证据恢复、影响面验证、Anytime Candidate、请求预算收口与差分验证框架。
- 完成 V3 two-run infrastructure-recovery replay：`20` 题均形成终态，`19` 题可评分、`5` 题 resolved、`1` 题基础设施错误；保持全部 `4` 个历史成功任务并新增解决 `haystack-8489`，Provider 请求由 `537` 降至 `429`。
- 实现 task-scoped transport failure 隔离、未知 Usage 保守结算、40-request ceiling 下 Candidate 保存与后续任务继续执行；通过 Artifact activation audit 定位 Evidence/Hypothesis/Differential 未充分生效的问题，并将下一轮收缩为 activation fidelity 与严格配对验证。

---

# 4. 简历数字的安全边界

## 可以写

- Goal 4：20/20 scorable、4 resolved；
- V3：20 terminal、19 scorable、5 resolved、1 infra；
- 537 → 429 requests；
- 保持四个历史 resolved；
- 新增解决 haystack-8489；
- task-scoped failure isolation；
- Candidate 与账本收口；
- activation audit。

## 不建议写

- “V3 成功率提升 31.5%”；
- “论文研究使成功率从 20% 提高到 26.3%”；
- “V3 已证明泛化提升”；
- “解决 5/20”而不注明 1 infra；
- “20 题全部完成正式评分”；
- leaderboard / pass@k；
- 统计显著；
- V3 Evidence 已完整工作。

---

# 5. 面试官可能追问与核心回答

## 为什么只解决 4～5 题？

- 真实 SWE 难；
- 固定模型、40 请求、retry=0；
- 目标是建立可信基线，不刷分；
- 失败归因比掩盖失败更有价值；
- 下一轮针对高频契约错误。

## V2 和 V3 的区别？

- V2：评测链路是否可信；
- V3：Agent 的解题方法是否改进。

## 为什么 V3 没大幅提升？

- 研究预测本身只有 1～3 题转化；
- Candidate 和恢复生效；
- Evidence / Hypothesis / Differential 未形成实际 treatment；
- 通过 Artifact 审计发现，不夸大因果。

## 为什么不再直接 Full-20？

- 先验证机制是否激活；
- 用 4 题 paired Pilot 找信号；
- 通过后再进入 fresh holdout；
- 节省费用和时间。

## 项目是否 AI 辅助开发？

建议诚实表达：

> 项目开发大量使用 Codex 和 ChatGPT 加速源码阅读、实现和文档整理；我主要负责目标定义、实验合同、变量控制、失败归因、方案选择、付费授权和最终证据审计。具体模块会依据源码和测试准备追问，不把 AI 生成代码包装成完全手写。

---

# 6. 如何清楚地讲给自己

把项目记成五句话：

1. **我做了一个能执行真实代码任务的终端 Agent。**
2. **我为它建立了真实 Provider、官方 evaluator 和成本证据闭环。**
3. **我得到 4/20 基线，并逐题找出为什么失败。**
4. **我根据失败设计 V3，工程恢复进步、能力小幅进步，但核心 treatment 未完整激活。**
5. **下一步先证明机制真实进入决策，再做 paired Pilot 和 fresh holdout。**

---

# 7. 当前投递建议

可以立即投递：

- AI Agent 开发；
- LLM 应用开发；
- Agent Eval / Agent Infra；
- AI Native 应用；
- FDE / Solutions Engineer；
- 偏工程的 AI 平台实习。

不要等待 V3.1 完成后才投递。

下一阶段可在面试中作为“正在进行的工程计划”，显示：

- 不盲目刷 benchmark；
- 懂 activation fidelity；
- 懂 treatment 和 causal evidence；
- 能控制实验成本；
- 能承认边界。

---

# 8. 文档变更记录

## v1.0.0 — 2026-07-30 15:02（UTC+8）

- 首次系统整理当前 Agent 产品、Eval、Infra、研究与工程成熟度；
- 增加完整时间叙事；
- 给出 30 秒、1 分钟、3 分钟介绍；
- 固化当前推荐简历项目介绍与 5 条 Bullet；
- 明确数字和因果边界；
- 明确当前即可投递。
