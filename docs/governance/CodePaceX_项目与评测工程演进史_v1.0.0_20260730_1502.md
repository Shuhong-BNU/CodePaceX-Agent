# CodePaceX 项目与评测工程演进史

> **文档版本**：v1.0.0
> **生成时间**：2026-07-30 15:02（UTC+8）
> **文档状态**：Current
> **文档职责**：解释 CodePaceX 产品、评测工程、实验研究与能力迭代如何演进；为旧的 Goal / Stage / V2 / V3 名称建立统一映射。
> **事实边界**：历史 Run、Artifact、commit 和原始报告保持不可变；本文只增加统一解释层，不重命名或覆盖历史证据。

---

# 0. 一句话总览

CodePaceX 从一个可运行的终端 Coding Agent，逐步演进为一个能够进行真实 Provider 调用、官方 SWE 评分、成本治理、失败恢复和证据审计的 Agent 工程系统；但项目历史上长期存在“控制面强、数据面弱”的失衡。当前最重要的转向是：

> **停止继续扩大评测治理范围，把下一轮迭代集中到能真正提高 resolved rate 的 Evidence → Decision → Patch → Validation 数据面闭环。**

---

# 1. 以后采用的统一身份体系

历史名称保留，但从现在开始不再把不同概念都叫作“版本”。

| 身份类型 | 回答的问题 | 推荐格式 | 示例 |
|---|---|---|---|
| Agent Capability Release | Agent 本身具备什么能力 | `CPX-Agent vMAJOR.MINOR.PATCH` | `CPX-Agent v3.0.0` |
| Eval Harness Release | 评测平台和协议是什么版本 | `CPX-Eval vMAJOR.MINOR.PATCH` | `CPX-Eval v2.0.0` |
| Study | 这一组实验想回答什么问题 | `STUDY-YYYYMMDD-主题` | `STUDY-20260730-V3-REPLAY` |
| Run | 某一次不可变执行 | GitHub Run / Internal Run ID | `30510508446` |
| Artifact | 某次 Run 的不可变证据 | Artifact ID + digest | `8749299095` |
| Plan Document | 接下来怎么做 | 文档独立 SemVer + 时间戳 | `...方案_v1.1.0_20260730_1502.md` |
| Narrative Document | 当前如何对外表达 | 文档独立 SemVer + 时间戳 | `...简历与面试叙事_v1.0.0_...md` |

## 1.1 术语使用边界

- **Goal**：只表示历史项目目标或管理目标，不再表示产品版本。
- **Stage**：只表示某项 Study 内部的执行阶段，不再表示全项目版本。
- **vX.Y.Z**：只用于真实可比较的代码、协议或文档版本。
- **Study**：一组有共同问题、对照和结论的实验。
- **Run**：一次不可变执行，不因后续恢复而被覆盖。
- **Artifact**：Run 的证据快照。
- **Snapshot**：某时刻代码、评测、文档与结论的组合截面。

---

# 2. 历史阶段时间线

> 日期来自已有项目记录。部分持续阶段以日期范围表示，不强行伪造小时级精度。

## E0：产品原型与轻量回归

### 2026-07-06｜Lightweight Baseline

**目标**：证明 Agent Loop、工具调用和固定任务回归能够运行。

**结果**：

```text
6 / 6 PASS
Run: 20260706-231810-49af3753
```

**主要价值**：

- 从“能聊天”进入“能执行固定代码任务”；
- 建立最早的 Eval Harness；
- 暴露 Python 环境、依赖污染和跨任务隔离问题。

**当前解释**：

- 属于轻量 deterministic baseline；
- 不是 SWE-bench-Live 正式能力结果。

---

## E1：评测控制面建立

### 2026-07-13 ～ 2026-07-18｜历史 Goal 2

**目标**：建立真实 Provider、Usage、预算、Artifact 和 Claims 基础。

**关键结果**：

- MCP：300 个 terminal Trials；
- Permission：200 个 terminal Trials；
- Hook：100/100 deterministic cases；
- Retention：auditable partial；
- Multi-Agent：evidence insufficient；
- Formal SWE：当时 infrastructure-blocked；
- `spent_cny = 92.579316`；
- `active_reservation = null`；
- PR #16 于 2026-07-18 合并。

**主要进步**：

- 每次请求开始有 reservation、charge 和 settlement；
- 不确定 Usage 使用保守结算，不编造 Token；
- 开始区分正式实验、Pilot、diagnostic、zero-provider 和 insufficient-data；
- 建立 Artifact 和 Claims。

**主要问题**：

- 评测治理迅速复杂化；
- 控制面成熟速度快于真实 SWE 数据面。

**统一映射**：

```text
历史 Goal 2
≈ CPX-Eval 早期控制面建设期
≈ Evolution Era E1
```

---

## E2：真实 SWE 官方评分链路

### 2026-07-18 ～ 2026-07-20｜历史 Goal 3

**目标**：在 Linux x86_64 + Docker + official evaluator 下打通真实 SWE Pilot。

**正式 Pilot 结果**：

```text
3 / 3 scorable
1 resolved
2 unresolved
CNY 9.078540
active_reservation = null
```

**主要进步**：

- 从自建 grader 进入 official evaluator；
- 打通 Agent → Candidate → Docker evaluator → resolved/unresolved；
- 证明真实付费 SWE 链路可以工作。

**主要问题**：

- 环境安装、workflow dispatch、collector 和 Artifact 路径仍较脆弱。

**统一映射**：

```text
历史 Goal 3
≈ SWE official-evaluator Pilot Study
≈ Evolution Era E2
```

---

## E3：正式能力基线

### 2026-07-20 ～ 2026-07-21｜历史 Goal 4

**目标**：在固定 20 个 SWE-bench-Live 任务上获得完整正式基线。

**最终结果**：

```text
20 / 20 scorable
4 resolved
16 unresolved
Provider requests = 537
verified actual cost = CNY 165.044424
active_reservation = null
Claims = valid / verified
```

**历史 resolved**：

1. `beetbox__beets-5495`
2. `beancount__beancount-931`
3. `beeware__briefcase-2075`
4. `beeware__briefcase-2085`

**主要进步**：

- 首次获得完整 20 题官方可评分能力基线；
- 请求、Token、费用、Candidate、evaluator 和 Artifact 全部可追溯；
- 为后续失败归因提供真实样本。

**核心意义**：

> Goal 4 回答了“当前 Agent 到底能解决多少真实任务”。

**统一映射**：

```text
历史 Goal 4
≈ STUDY-20260721-GOAL4-BASELINE
≈ 当前永久历史能力基线
```

---

## E4：失败归因后的验证门尝试

### 2026-07-21 ～ 2026-07-23｜Stage B / Stage C / Stage D / Stage D.1

## Stage B

增加：

- reproduction-before-edit；
- contract inventory；
- target-test completion；
- regression comparison；
- request checkpoints；
- completion gate。

**初衷**：减少盲改、漏测和不完整 Patch。

## Stage C Phase 1

结果：

```text
6 / 6 scorable
0 resolved
```

但主要原因是强 validation gate 阻止真实 Edit/Write，形成协议死锁，不能解释为公平能力退化。

## Stage D / D.1

逐步放松真实工具调用兼容性：

- RunTest 开始执行；
- 结构化参数问题修复；
- 最终产生真实编辑和非空 Candidate；
- 但 official evaluator recovery 仍在 wiring / collector 边界失败。

**主要教训**：

> 把理想工程行为做成强门禁，可能阻止 Agent 正常探索；安全与验证机制应优先提供 evidence/advice，而不是成为新的 Permission。

**统一映射**：

```text
Stage B/C/D/D.1
≈ STUDY-20260722-VALIDATION-GATES
≈ 已关闭的实验分支，不是产品版本递进
```

---

## E5：评测 Golden Path 重建

### 2026-07-23 ～ 2026-07-25｜Evaluation V2

**目标**：把 Provider → Agent → 工具 → Candidate → official evaluator → ledger → Artifact 的 Golden Path 做可靠。

**完成的重要修复**：

- 每题独立环境；
- 防止任务 `pip install` 污染宿主；
- evaluator 与 controller 环境隔离；
- 20/20 preflight；
- 磁盘与 inode telemetry；
- request ceiling 终态；
- Provider Usage 合同；
- Candidate/diff identity；
- full-run dispatch coverage；
- fail-closed 与 Artifact 收口。

**典型失败及价值**：

- Full-20 曾只执行 1/20 或 3/20；
- workflow 绿色但实验本体不完整；
- Provider SDK 被任务依赖降级；
- completion token 超过冻结合同；
- GitHub Runner 磁盘不足。

这些失败推动 V2 证明：

> “有 20 行结果”不等于“20 道题都经过真实 Agent、Provider 和 evaluator”。

**当前解释**：

- V2 主要提高评测可信度；
- 没有单独证明 Agent resolved rate 高于 Goal 4。

**统一映射**：

```text
Evaluation V2
≈ CPX-Eval v2.0.0 的形成期
≈ Evolution Era E5
```

---

## E6：失败研究到 Capability V3

### 2026-07-25 ～ 2026-07-26｜失败交叉验证与研究

**工作内容**：

- 对 Goal 4 的 16 个 unresolved 做独立归因、证据对照和争议裁决；
- 建立“上游原因 → 错误决策 → 近端失败 → evaluator 表面”的联合因果模型；
- 研究经典论文、近期 Coding Agent 论文和成熟开源项目；
- 将主要问题收敛为：契约恢复、改动影响、边界验证和 Candidate 收敛。

**稳定结论**：

> Agent 的主要问题不是普遍找不到代码，而是找到相关代码后，难以准确恢复真实契约，并形成最小、完整、经过针对性验证的 Patch。

---

### 2026-07-26 ～ 2026-07-28｜Capability V3 设计、实现与 Controlled Pilot

**V3 设计模块**：

- V3-A：evidence-grounded contract recovery；
- V3-B：impact slice / contract coverage；
- V3-C：Anytime Candidate / budget finalization；
- V3-D：differential validation。

**工程进步**：

- V3 observer / controller；
- Candidate snapshot / restore；
- Oracle risk；
- request-budget phase；
- fail-open 接入；
- V2_CONTROL / V3_CORE；
- Controlled Pilot 和正式 workflow 接线。

**历史 Controlled Pilot 的意义**：

- 验证 V2/V3 treatment 可以在同一合同下运行；
- 但未证明 V3 resolved rate 优于 V2。

---

### 2026-07-29 ～ 2026-07-30｜V3_CORE 20 题 replay

**执行形式**：

```text
two-run infrastructure-recovery completion
```

Head Run：

```text
Actions Run: 30503096853
Artifact: 8744897594
```

Tail Run：

```text
Actions Run: 30510508446
Artifact: 8749299095
```

**最终结果**：

```text
20 terminal
19 scorable
5 resolved
14 unresolved
1 infrastructure_error
429 known-Usage Provider requests
CNY 132.932760 total ledger consumption
```

**结果变化**：

- Goal 4 的 4 个 resolved 全部保持；
- `deepset-ai__haystack-8489`：unresolved → resolved；
- `bridgecrewio__checkov-6893`：infrastructure_error；
- 没有 resolved → unresolved 的终态变化；
- 不立即补跑 `checkov-6893`，采用路径 A，保留 19/20。

**工程进步**：

- 单题网络故障不再停止整轮；
- 40-request ceiling 下可保留 Candidate 并进入 evaluator；
- 两轮账本闭合；
- 未重跑前四题；
- 未发生第二条未授权 paid dispatch。

---

### 2026-07-30｜V3 机制激活审计

Artifact 审计发现：

- Evidence target symbols：0；
- callers / implementations / tests / config / history：0；
- hypotheses：0；
- ContractMatrix：0；
- DifferentialValidation：0；
- Candidate snapshots：84；
- C2/C3 Candidate：0；
- 186 个 impacted-test 推荐中 112 个来自 venv/site-packages。

**新结论**：

> 当前 V3.0 主要提升了观察、Candidate 保存、预算收口和故障隔离；契约恢复、假设证伪和差分验证尚未形成完整的 Evidence → Decision 闭环。

**当前推荐产品解释**：

```text
CPX-Agent v3.0.0
= observable / recoverable capability baseline
```

---

# 3. 旧名称到新体系的映射

| 历史名称 | 历史性质 | 新的统一解释 |
|---|---|---|
| Baseline v1 | 轻量回归 | `E0 / Lightweight Baseline` |
| Goal 2 | 评测控制面建设 | `E1 / Eval Governance` |
| Goal 3 | SWE Pilot | `E2 / Official Evaluator Pilot Study` |
| Goal 4 | 正式 20 题基线 | `E3 / STUDY-GOAL4-BASELINE` |
| Stage B/C/D/D.1 | 验证门实验 | `E4 / Validation-Gate Study` |
| Evaluation V2 | 评测 Golden Path | `E5 / CPX-Eval v2.0.0` |
| Capability V3 | Agent 能力机制 | `E6 / CPX-Agent v3.0.0` |
| V3.1（规划） | 激活忠实度修复 | `CPX-Agent v3.1.0` |
| fresh holdout（规划） | 泛化验证 | 独立 Study，不是产品版本 |

---

# 4. 当前项目快照

```yaml
snapshot_time: 2026-07-30T15:02:00+08:00
agent_capability_release: CPX-Agent v3.0.0
eval_harness_release: CPX-Eval v2.0.0
historical_baseline:
  study: Goal 4
  result: 20 scorable / 4 resolved
current_v3_replay:
  completion: two-run infrastructure-recovery
  result: 20 terminal / 19 scorable / 5 resolved / 1 infra
  path_decision: keep 19/20; no immediate retry
current_priority:
  - formal closeout
  - activation fidelity
  - zero-provider mechanism verification
  - small paired pilot
  - fresh holdout
```

---

# 5. 这条演进路线应该怎样理解

## 产品线

```text
终端 Agent
→ 安全、记忆、MCP、多 Agent
→ 可测试 Agent
→ 可被真实 SWE 评测的 Agent
→ 基于失败证据进行能力迭代的 Agent
```

## 评测线

```text
固定任务回归
→ 真实 Provider 与成本
→ official evaluator Pilot
→ 20 题正式基线
→ 失败因果归因
→ Golden Path 稳定化
→ treatment activation audit
→ paired experiment
→ fresh holdout
```

## 认知线

```text
“能跑”
→ “能评分”
→ “评分可信”
→ “知道为什么失败”
→ “机制真的执行”
→ “机制是否导致提升”
→ “是否能泛化”
```

---

# 6. 当前最重要的历史教训

1. **不能把 workflow 绿色等同于实验成功。**
2. **不能把 feature flag 打开等同于机制激活。**
3. **不能跑完 Full-20 后才检查 treatment 是否发生。**
4. **不能让验证建议升级成新的 Permission 死锁。**
5. **不能只优化账本和 Artifact，而忽略 Agent 实际看到的证据。**
6. **旧 20 题在用于分析和调试后，只能作为 development/regression set。**
7. **新鲜 holdout 必须最后进入，不能在机制尚未激活时浪费。**
8. **失败 Run、旧方案和旧文档应标记 superseded，而不是覆盖删除。**

---

# 7. 文档变更记录

## v1.0.0 — 2026-07-30 15:02（UTC+8）

- 首次建立完整项目与评测演进史；
- 统一 Goal / Stage / V2 / V3 的历史解释；
- 引入 Agent、Eval、Study、Run、Artifact、Document 分层身份；
- 固化路径 A：保留 V3.0 的 19/20，不立即补跑；
- 将当前版本定位为 `CPX-Agent v3.0.0` 与 `CPX-Eval v2.0.0`；
- 记录下一阶段为 V3.1 activation fidelity。
