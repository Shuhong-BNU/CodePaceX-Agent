# CodePaceX Capability V3 后续工作流程方案

> **版本**：v1.0
> **日期**：2026-07-30（UTC+8）
> **状态**：Superseded
> **替代版本**：`CodePaceX_后续整体工作方案_v1.1.0_20260730_1502.md`
> **当前基线**：Goal 4 `20/20 scorable, 4 resolved`；Capability V3 two-run completion `20 terminal, 19 scorable, 5 resolved, 14 unresolved, 1 infrastructure_error`
> **原则**：先收口、再修机制忠实度；先零 Provider 验收、再小规模配对；最后才进入新鲜 holdout。不得把旧 20 题继续包装成新鲜泛化测试。

---

# 0. 执行摘要

当前 Capability V3 已证明：

- 历史 4 个 resolved 全部保持 resolved；
- `haystack-8489` 从 unresolved 变为 resolved；
- 单题 transport failure 不再停止整轮；
- 40-request ceiling 下可以保存非空 Candidate 并继续 evaluator；
- 429 个已知 Usage 请求，累计内部账本消费 CNY 132.932760；
- 两轮 ledger 均关闭，`active_reservation=null`。

但 Artifact 激活审计同时发现：

- Evidence target symbols、callers、implementations、tests/config/history 均为 0；
- Hypotheses、ContractMatrix、DifferentialValidation 均为 0；
- 84 个 Candidate snapshots 全部停留在 C1，没有 C2/C3；
- 186 个 impacted-test 推荐中 112 个来自 venv/site-packages；
- 因此当前 V3 主要完成了 Candidate、预算和运行恢复机制，尚未完整实现“让仓库证据影响 Agent 决策”。

后续不应继续叠加论文、模块、Agent 或治理层。主线只做：

```text
P0 正式收口
→ P1 修复机制激活忠实度
→ P2 zero-provider 机制验收
→ P3 小型严格配对 Pilot
→ P4 新鲜 holdout
```

---

# 1. 当前版本的正式定位

建议将当前版本标记为：

```text
Capability V3.0 — observable/recoverable baseline
```

它已经具备：

- V3 生命周期和 Artifact；
- Candidate snapshot / restore；
- request-budget finalization；
- task-scoped failure isolation；
- Oracle risk 与 ImpactSlice telemetry；
- V3_CORE full-run 接线。

它尚未被证明具备：

- 可靠的 repository-grounded contract recovery；
- 真实被工具证据证伪的 bounded hypotheses；
- 有效的 contract matrix；
- 精确的 changed-symbol → impacted-test 映射；
- baseline/post differential validation；
- C2/C3 Candidate 晋级；
- 可证明被模型看到并使用的 V3 advice。

---

# 2. `checkov-6893` 是否补跑的决策

## 2.1 当前事实

当前 V3 的 `bridgecrewio__checkov-6893`：

- 已执行 10 个 Provider requests；
- 前置目标测试通过；
- 后续请求发生 `ConnectTimeout / APITimeoutError / NetworkError`；
- Agent exit code = 1；
- Candidate 未导出；
- evaluator 未运行；
- terminal = `infrastructure_error`；
- 原 Trial、费用和保守结算均已保留；
- 故障后剩余任务继续运行。

## 2.2 两种合法路径

### 路径 A：保留 19/20，不立即补跑（推荐）

适用目标：

- 尽快完成正式收口；
- 不为旧 V3.0 再花费；
- 把资源集中到机制激活修复；
- 接受当前基线为 `5 resolved / 19 scorable + 1 infra`。

优点：

- 不增加 paid attempt；
- 不因一次网络随机性继续延长旧版本；
- 更快进入真正影响能力的 P1。

缺点：

- 当前 V3.0 不是 20/20 scorable；
- 对外表达必须始终注明 1 个 infrastructure error。

### 路径 B：对当前 V3.0 做一次精确合同的基础设施 retry

仅在以下目标成立时使用：

- 需要把当前 V3.0 固化为长期发布的完整 20 题基线；
- 愿意为“实验完整性”而不是“机制提升”支付一次费用。

必须满足：

- 在任何 P1 代码修改之前执行；
- 使用当前 V3.0 完全相同的 main、模型、Prompt、Provider、evaluator、Pricing 和任务 base commit；
- 新 Trial ID、新 authorization；
- 原 infrastructure Attempt 永久保留；
- exactly one retry；
- `retry=0`、`fallback=false`；
- 不得混入修复后的 V3.1 结果；
- 需要新的明确 paid 授权。

## 2.3 推荐结论

默认选择 **路径 A**。

理由：

> 补跑只能补齐当前 V3.0 的分母，不能解决 Evidence、Hypothesis、ImpactSlice 和 DifferentialValidation 未激活的问题。当前更高价值的工作是修机制忠实度。

若用户明确需要一份“当前 V3.0 20/20 scorable 的永久正式基线”，则在 P1 前选择路径 B；一旦 P1 修改代码，就不应再把新 retry 拼回旧 V3.0。

---

# 3. P0：正式收口，不再付费运行

## 3.1 目标

把 Goal 4 → V3.0 的事实永久固化，避免后续 V3.1 修改覆盖或混淆当前结果。

## 3.2 工作内容

### A. 最终报告

生成并入库：

```text
evals/CAPABILITY_V3_GOAL4_FINAL_REPORT.md
```

必须包含：

- `two-run infrastructure-recovery completion`；
- head run `30503096853`；
- tail run `30510508446`；
- 两个 Artifact ID 和 digest；
- 20 题逐题结果；
- 19 scorable / 5 resolved / 14 unresolved / 1 infra；
- Goal 4 的 4/20；
- unresolved→resolved：`haystack-8489`；
- resolved→unresolved：0；
- `checkov-6893` 的 infrastructure error；
- Provider requests、Token、费用；
- 两轮 `active_reservation=null`；
- 未重跑前四题；
- 未发生第二条未授权 workflow；
- 因果与证据边界。

### B. 机制激活复盘

生成并入库：

```text
evals/CAPABILITY_V3_ACTIVATION_POSTMORTEM.md
```

至少记录：

- config flags 打开不等于机制产出；
- Evidence、Hypothesis、Matrix、Differential 的真实计数；
- Candidate C1/C2/C3 分布；
- ImpactSlice 的 venv/site-packages 污染；
- `beets-5457` Python 3.8 兼容回归；
- 当前 V3.0 更接近 observer/recovery layer。

### C. 更新正式索引

集中更新：

- `evals/EVALUATION_HISTORY.md`
- `evals/EVALUATION_ARTIFACT_INDEX.md`
- `evals/README.md`
- `evals/README.en.md`
- 根 README 中保守、简短的项目结果
- Claims / evidence index（若现有体系支持）

### D. 单一零 Provider 文档 PR

要求：

- 不调用 Provider；
- 不修改历史 Artifact；
- 不改变 evaluator 输出；
- 不创建新实验；
- 一个 PR 收口；
- CI、链接、hash 和文档一致性全部通过。

## 3.3 P0 出口门

全部满足后才能进入 P1：

- [ ] 最终结果在仓库内可定位；
- [ ] 逐题结果、请求、Token、费用与 Artifact 一致；
- [ ] 19/20 scorable 边界明确；
- [ ] V3.0 机制激活不足被正式记录；
- [ ] 简历和面试禁止夸大边界已写入文档；
- [ ] 当前 V3.0 commit 与结果被冻结。

---

# 4. P1：只修“V3 机制激活忠实度”

## 4.1 目标

不增加新模块，不重构整个 Agent，只让已经设计的 V3-A/B/C/D 在真实执行链中产生可验证状态，并把有效建议真正送入 Agent。

建议版本：

```text
Capability V3.1 — activation fidelity
```

## 4.2 一个主 PR 的六个工作包

### 工作包 1：Evidence Collector 真实仓库锚定

修复内容：

- 先剥离通用任务包装语句；
- 从 issue 提取类名、函数名、常量、配置键、错误文本、文件名；
- 用 AST / `rg` / import / inheritance 定位 definition；
- 读取 direct callers；
- 读取 sibling implementations；
- 读取项目 tests、fixtures；
- 读取 Python 版本、依赖、默认值、配置和 serialization；
- 必要时读取 git history；
- 无锚点时记录具体失败原因，不能只写泛化的 unknown。

强制排除：

```text
.git/
.venv/
venv/
.evaluation-v2-preflight-venv/
site-packages/
build/
dist/
__pycache__/
.pytest_cache/
.mypy_cache/
```

### 工作包 2：Advice 注入闭环

新增明确事件：

```text
AdviceGenerated
AdviceInjected
AdviceVisibleToModel
AdviceReferencedByAgent
AdviceIgnoredOrRejected
```

要求：

- Evidence compact view 必须进入当前模型请求；
- 注入内容有 token 上限；
- 只提供 evidence/advice，不进入 Permission deny；
- Evidence 缺失时 fail-open；
- Artifact 必须能够证明哪一轮模型真正收到了什么 advice。

### 工作包 3：Bounded Hypothesis 真正运行

在 contract-heavy issue 上：

- 最多 2～3 个假设；
- 每个假设有不同 observable prediction；
- 每个假设有 cheapest falsifier；
- 只有工具输出能 reject；
- 首次编辑前没有假设不硬阻断，但必须有 telemetry；
- selected/rejected/unknown 均进入 Artifact。

### 工作包 4：ImpactSlice 与 ContractMatrix 忠实化

ImpactSlice：

- 从实际 changed symbols 出发；
- 优先项目内 F2P、历史失败和 sibling tests；
- 过滤虚拟环境；
- 动态边标记 unknown；
- 不把第三方依赖测试推荐为项目测试。

ContractMatrix：

- 仅在 Evidence 支持时生成维度；
- Python 版本和 compatibility surface 必须成为可触发维度；
- default/explicit、valid/invalid、backend/provider、exception family 等按风险选择；
- 不超过 6 个维度、12 个 case；
- 不生成组合爆炸。

### 工作包 5：Differential Validation 接通

必须接入：

```text
baseline result
→ patch
→ post result
→ comparable / incomparable
→ new failures / fixed failures / persistent failures
→ failure attribution
```

要求：

- pre/post 测试命令和环境身份一致；
- collection error 单独分类；
- evaluator noise 与 Agent regression 分开；
- 不把 baseline 已有 failure 记为新回归；
- 结果进入 Candidate scoring。

### 工作包 6：Candidate C1/C2/C3 晋级

正式定义：

- **C1**：非空、可应用的原子 Patch；
- **C2**：目标 reproducer/F2P 或等价目标测试通过；
- **C3**：C2 + impacted regression / contract matrix 通过，且无新 collection error。

要求：

- Candidate 晋级有证据；
- Candidate 降级或 stale 有事件；
- Finalization 选择最高等级、最新有效 Candidate；
- 若仅有 C1，报告不得暗示已验证；
- request ceiling 前保留最好 Candidate。

## 4.3 P1 禁止事项

- 不增加 Reviewer Agent；
- 不增加自由反思轮次；
- 不增加 mandatory checkpoint；
- 不改变 Permission；
- 不自动跑 full suite；
- 不调用 Provider；
- 不创建 paid workflow；
- 不改变正式 evaluator；
- 不访问 gold/hidden tests；
- 不重新设计 V4。

## 4.4 P1 出口门

- [ ] Evidence packet 能定位真实仓库符号；
- [ ] Advice 注入有端到端事件；
- [ ] Hypothesis 有真实 proposal/rejection；
- [ ] ContractMatrix 有真实 cases；
- [ ] DifferentialValidation 不再为 null；
- [ ] Candidate 可晋级 C2/C3；
- [ ] 虚拟环境测试推荐为 0；
- [ ] V3 内部失败仍 fail-open；
- [ ] V2_CONTROL 行为兼容；
- [ ] 全量本地测试与 CI 通过。

---

# 5. P2：zero-provider 机制验收

## 5.1 目标

在不产生任何 Provider 费用的情况下，证明 V3.1 不是“只有 schema 和开关”，而是能在 preserved traces、fixtures 和确定性仓库上真实激活。

## 5.2 测试层次

### 层 1：单元测试

覆盖：

- issue entity normalization；
- symbol anchoring；
- exclusion rules；
- caller / implementation / test discovery；
- hypothesis 状态机；
- advice serialization 与注入；
- impact-test ranking；
- matrix generation；
- differential classification；
- C1/C2/C3 promotion；
- event replay determinism。

### 层 2：定向 fixture

至少包含：

1. Python 3.8 项目 + `str | None` Patch；
2. 多 backend 行为；
3. default/config 双 surface；
4. exception family；
5. pre-existing baseline failure；
6. venv 内存在同名测试；
7. request ceiling 前有 C2 Candidate；
8. Evidence 找不到锚点的合法 unknown。

### 层 3：旧 Goal 4 preserved-trace replay

旧 20 题只能作为 development/regression set，验证：

- Evidence 能否找到锚点；
- 哪些 advice 会生成；
- 哪些 hypothesis 会被提出；
- 哪些 impacted tests 被推荐；
- Candidate 是否可能晋级；
- 不发送 Provider 请求；
- 不重新计算能力成绩。

## 5.3 硬验收指标

| 指标 | 验收门 |
|---|---:|
| Provider requests / Usage / charge | 0 |
| Secret read | false |
| V3 导致的 Permission deny | 0 |
| venv/site-packages 推荐测试 | 0 |
| 定向 fixture AdviceInjected | 100% |
| contract-heavy fixture hypotheses | 100% |
| 触发条件满足时 ContractMatrix | 100% |
| baseline/post fixture DifferentialValidation | 100% |
| Candidate fixture C2/C3 晋级 | 100% |
| event replay | deterministic |
| Evidence 无锚点 | 显式、具体 unknown |
| V2_CONTROL compatibility | 通过 |

旧 20 题建议目标：

- 至少 18/20 找到一个有效 repository anchor；
- 剩余任务必须给出具体 unknown；
- 不允许再出现 112/186 这类虚拟环境污染。

## 5.4 P2 Artifact

一次 zero-provider readiness Artifact 应包含：

- 20 题 activation matrix；
- advice injection matrix；
- Evidence/Hypothesis/Matrix/Differential 计数；
- C1/C2/C3 分布；
- exclusions audit；
- Provider/Secret/ledger 零值证明；
- 失败 fixture 与 remediation。

## 5.5 P2 出口门

只有 P2 全部通过，才允许设计 P3 的付费 Pilot。否则继续修同一个 V3.1 PR，不增加阶段和新框架。

---

# 6. P3：小型严格配对 Pilot

## 6.1 目标

回答一个受控问题：

> 在同一时间、同一 main、同一任务、同一模型、同一 Provider 和同一 evaluator 下，仅开启 V3.1 是否改变 Agent 行为和结果？

## 6.2 建议任务

使用 6 个旧 development tasks，覆盖不同失败类型：

1. `beetbox__beets-5457`：Python 版本 / compatibility；
2. `deepset-ai__haystack-8489`：backend 回归；
3. `aws-cloudformation__cfn-lint-3749`：契约/API 解释；
4. `delgan__loguru-1297`：异常边界；
5. `dynaconf__dynaconf-1249`：default/config surface；
6. `instructlab__instructlab-2540`：多 surface + 请求预算。

这些任务不能作为新鲜 holdout，只用于验证机制行为。

## 6.3 运行设计

```text
6 tasks × 2 treatments
V2_CONTROL vs V3_CORE
```

固定：

- 同一 bound main；
- 同一时间窗口；
- 同一 endpoint/账号；
- 同一模型和 Prompt；
- 同一 task/base commit/problem statement；
- 同一工具、Permission 和 evaluator；
- strict serial；
- retry=0；
- fallback=false；
- 每个 task-run 独立 identity；
- treatment 顺序应交错或预先冻结；
- hard cap 在派发前按当前定价单独批准。

## 6.4 主指标

第一优先级是机制忠实度：

- valid repository evidence referenced；
- advice seen by model；
- hypotheses rejected by tool evidence；
- impacted-test precision；
- ContractMatrix coverage；
- C2/C3 Candidate；
- collection/global regression；
- request ceiling finalization。

第二优先级才是：

- resolved；
- requests；
- Token；
- cost；
- resolved→unresolved。

## 6.5 进入 P4 的门槛

建议同时满足：

- [ ] 6 对均形成可审计终态，或至多 1 个明确 infrastructure error；
- [ ] V3_CORE 在适用任务中 ≥80% 产生有效 Evidence + AdviceInjected；
- [ ] venv contamination = 0；
- [ ] 至少 4/6 形成 C2 或 C3 Candidate；
- [ ] 无历史 resolved/control 发生 resolved→unresolved；
- [ ] 至少 2 题出现可解释的行为级改善，或至少新增 1 resolved 且无能力回退；
- [ ] V3 成本没有无解释地显著增加；
- [ ] 没有 Stage C 式协议死锁。

未满足时，不进入 fresh holdout；回到 P1/P2 修同一机制。

---

# 7. P4：新鲜 holdout

## 7.1 目标

在未用于 Goal 4 失败分析、V3 设计、P2 replay 或 P3 调试的新任务上，验证 V3.1 是否具有泛化提升。

## 7.2 任务集

建议第一轮冻结：

```text
12 个 fresh SWE-bench-Live tasks
```

分层覆盖：

- 契约/API；
- 编辑范围；
- 异常/边界；
- default/config；
- 多 backend；
- 预算收敛；
- 单文件 / 2–4 文件 / 5+ 文件。

冻结前：

- 不读取 gold patch；
- 不逐题做失败分析；
- 不依据预期结果挑题；
- 固定 dataset revision、base commit、problem statement 和 evaluator；
- 预注册任务与分层规则。

## 7.3 实验设计

主比较仍是：

```text
V2_CONTROL vs V3_CORE
```

要求：

- 相同最新 main；
- treatment 之外全部固定；
- 顺序随机或交错并预注册；
- 每题独立身份与账本；
- strict serial；
- retry=0；
- fallback=false；
- infrastructure retry 规则预先冻结；
- 不在运行中根据结果改任务。

## 7.4 报告

报告必须区分：

- capability result；
- infrastructure result；
- mechanism activation；
- cost efficiency；
- regression；
- inconclusive。

不得把：

- 旧 20 题的 5 resolved；
- P3 development Pilot；
- P4 fresh holdout

混成一个成功率。

## 7.5 后续扩展

12 题 holdout 显示正向信号后，再单独决定是否扩展为新的 20 题确认评测。扩展不是默认自动动作，需要独立预算和授权。

---

# 8. 建议 Git / PR 组织

## PR-A：P0 正式收口

- docs/evidence only；
- zero Provider；
- 固化 V3.0。

## PR-B：P1 + P2 主实现

- 单一 V3.1 主 PR；
- 完成机制修复和 zero-provider 验收；
- 不拆成多个治理 PR；
- 不包含 paid workflow dispatch。

## Paid Run-C：P3

- PR-B 合并并通过 post-merge zero-provider readiness 后；
- 单独明确授权；
- 仅 6×2 配对 Pilot。

## Paid Run-D：P4

- P3 达到出口门后；
- 单独设计、冻结、授权；
- 12 个 fresh holdout 的 V2/V3 配对。

---

# 9. 总停止规则

任何阶段发现以下情况，停止扩大范围：

1. V3 advice 没有真实注入模型；
2. Evidence 仍主要来自 issue wrapper；
3. ImpactSlice 仍扫描 venv/site-packages；
4. Hypothesis/Matrix/Differential 仍为 schema-only；
5. Candidate 全部停在 C1；
6. V3 造成 Permission 或编辑死锁；
7. V2/V3 条件不一致；
8. Artifact 不能证明实际执行；
9. paid run 出现第二条未授权 dispatch；
10. 费用、Usage 或 active reservation 无法安全闭合。

---

# 10. 最终完成定义

Capability V3 后续工作不以“再跑一次 20 题”为完成标准。

真正完成需要同时证明：

```text
设计存在
→ 代码实现
→ zero-provider 可激活
→ Advice 真正进入模型
→ 模型行为受到可解释影响
→ Candidate 质量提升
→ 小型严格配对有信号
→ fresh holdout 有泛化证据
→ 费用和失败边界可审计
```

只有达到最后一步，才能把 Capability V3 表述为“经过受控实验验证的 Agent 能力提升机制”。

在此之前，最准确的定位是：

> Capability V3 已提升 CodePaceX 的 Candidate 保存、预算收口、任务级故障隔离和运行证据；契约恢复、假设证伪与差分验证仍需完成机制激活忠实度修复和严格配对验证。
