# CodePaceX 后续整体工作方案

> **方案版本**：v1.1.0
> **生成时间**：2026-07-30 15:02（UTC+8）
> **方案状态**：Current
> **上一版本**：`CodePaceX_CapabilityV3_后续工作流程方案_v1.0_20260730.md`
> **本次变更性质**：MINOR——不改变 P0→P4 主线，但增加路径 A、版本治理、机制激活前置门、反过度设计规则和 resolved-rate 北极星。
> **当前快照**：`CPX-Agent v3.0.0`；`CPX-Eval v2.0.0`；V3 replay 为 19/20 scorable、5 resolved、1 infrastructure_error。
> **决策**：采用路径 A，保留 19/20，不立即补跑 `checkov-6893`。

---

# 0. 北极星与非目标

## 0.1 北极星

下一轮唯一主目标：

> **提高 Agent 在真实软件工程任务中的 resolved rate，并证明提升来自真实激活的 Evidence → Decision → Patch → Validation 机制。**

## 0.2 次级目标

- 不破坏已有 resolved；
- 不制造 Stage C 式协议死锁；
- 保持成本和失败可审计；
- 保持 V2_CONTROL 可运行；
- 降低无效请求和错误测试选择。

## 0.3 非目标

这一轮不做：

- 新的多 Agent reviewer；
- 新的自由 Reflection 框架；
- 新的治理平台；
- 新的 Budget/Ledger 体系；
- 新的 Stage 命名；
- 新的 full-20 paid replay；
- 为了报告整齐而补跑 `checkov-6893`；
- 再增加论文研究范围；
- V4。

---

# 1. 防止两种极端的总原则

## 1.1 防止过度设计

### 约束 A：一个能力主题

V3.1 只处理：

```text
repository evidence
→ advice injection
→ hypothesis falsification
→ impact-aware validation
→ candidate promotion
```

不增加平行能力主题。

### 约束 B：一个主实现 PR

P1 + P2 原则上一个主 PR 收口。

只有出现以下情况才允许拆分：

- 单个 PR 无法在合理范围内 review；
- 基础兼容修复与机制实现存在明确独立回滚边界；
- 拆分不增加第二套状态机。

### 约束 C：没有消费者就不新增事件或字段

每个新事件必须回答：

1. 谁读取它？
2. 它改变什么决策？
3. 它进入哪个验收指标？

无法回答则不新增。

### 约束 D：不以文档完整代替运行事实

Schema、flag、README 和测试桩不能单独证明机制激活。

---

## 1.2 防止“跑完 20 题才发现机制没激活”

建立四级 Activation Ladder：

### L1：构造级

- 模块被实例化；
- feature flag 生效；
- 无异常 fail-open。

### L2：产物级

- Evidence 非空；
- Advice 生成；
- Hypothesis / Matrix / Differential 在适用 fixture 中非空；
- Candidate 可晋级 C2/C3。

### L3：注入级

- Advice 确实进入实际模型 request payload；
- Artifact 保存 compact advice digest / preview；
- 能证明模型请求发生前已注入。

### L4：行为级

- Agent 引用了 Evidence；
- 错误 Hypothesis 被工具结果淘汰；
- 测试选择发生可解释改变；
- Patch 避免已知契约错误；
- paired Pilot 出现结果或近端行为改善。

**禁止放大规则**：

```text
L1 未过 → 不进入 preserved replay
L2 未过 → 不进入 post-merge readiness
L3 未过 → 不允许 paid Pilot
L4 未在小 Pilot 中出现 → 不允许 fresh holdout
```

---

# 2. 版本化工作流程

```text
P0 关闭 V3.0
→ P1 实现 V3.1 activation fidelity
→ P2 zero-provider 激活验收
→ P3 4 题严格配对 Pilot
→ P4 8 题 fresh screening holdout
→ 可选：20 题 confirmation study
```

相比 v1.0，P3 从 6 题收缩为 **4 题**，P4 从 12 题收缩为 **8 题**，先寻找可信信号，再决定是否扩大。

---

# 3. P0：正式收口 V3.0

## 3.1 目标

固化当前结果，不再补跑、不再改写，避免 V3.1 与 V3.0 混算。

## 3.2 工作内容

一个 zero-provider 文档 PR：

- V3 final report；
- activation postmortem；
- Evaluation History；
- Artifact Index；
- README 中英文；
- Claims / evidence mapping；
- 本次演进史、治理规范和求职叙事文档的仓库落位建议。

## 3.3 必须固化的口径

```text
two-run infrastructure-recovery completion
20 terminal
19 scorable
5 resolved
14 unresolved
1 infrastructure_error
Path A: no immediate checkov-6893 retry
```

## 3.4 P0 出口门

- [ ] 结果与两个 Artifact 一致；
- [ ] 19/20 边界明确；
- [ ] 机制未激活事实被记录；
- [ ] 旧 Run、Artifact、费用均未修改；
- [ ] 当前 commit / bound main 有快照；
- [ ] 后续简历表述有 Claims 对照。

---

# 4. P1：CPX-Agent v3.1.0——机制激活忠实度

## 4.1 解决的两个高价值失败类

为了提高 resolved rate，只优先处理：

1. **契约 / API / runtime compatibility 推断错误**；
2. **修改影响面和针对性验证不足**。

暂不处理：

- 新的规划算法；
- 新的多 Agent；
- 大规模搜索策略；
- 模型更换；
- pass@k。

## 4.2 最小 Evidence-to-Decision 闭环

### A. Evidence Anchoring

从 issue 中抽取：

- 类 / 函数 / 常量；
- 配置键；
- 错误文本；
- 文件和测试名；
- backend/provider 名。

然后定位：

- definition；
- direct callers；
- sibling implementations；
- project tests / fixtures；
- Python 版本、依赖、默认值；
- 必要的 git history。

强制排除：

```text
.venv/
venv/
.evaluation-v2-preflight-venv/
site-packages/
build/
dist/
__pycache__/
.pytest_cache/
```

### B. Advice Injection

必须产生完整证据链：

```text
AdviceGenerated
→ AdviceInjected
→ AdvicePresentInRequest
→ AdviceReferenced / Ignored
```

V3 advice 只提供证据和建议，不成为 Permission。

### C. Bounded Hypothesis

仅对 contract-heavy 任务触发：

- 最多 3 个假设；
- 每个有 observable prediction；
- 每个有 cheapest falsifier；
- 只有工具证据可 reject。

### D. Impact-aware Validation

从实际 changed symbols 出发：

- 推荐项目内测试；
- 读取 baseline；
- 运行 post；
- 分类 new / fixed / persistent / incomparable；
- collection error 单独处理。

### E. Candidate Promotion

- C1：非空可应用 Patch；
- C2：目标 reproducer/F2P 通过；
- C3：C2 + impacted regression / contract cases 通过。

最终只选择最高有效 Candidate，不暗示 C1 已验证。

---

# 5. P2：zero-provider 机制验收

## 5.1 为什么必须在 paid 前完成

V3.0 已证明：

```text
flag enabled
≠ mechanism materialized
≠ advice injected
≠ model behavior changed
```

所以 P2 不是再建评测平台，而是防止浪费下一轮 Provider 费用的最小激活检查。

## 5.2 最小测试集合

### 6 个 deterministic fixtures

1. Python 3.8 项目 + `str | None` 回归；
2. default / explicit config；
3. 多 backend；
4. exception family；
5. baseline 已有 failure；
6. venv 内有同名 symbol/test。

### 旧 20 题 preserved-trace replay

用途仅为 development/regression：

- 检查锚点；
- 检查 advice；
- 检查测试推荐；
- 检查事件与 Candidate；
- Provider requests 必须为 0。

## 5.3 paid 前硬门

| 指标 | 硬门 |
|---|---:|
| Provider requests / Usage / charge | 0 |
| Secret read | false |
| venv/site-packages 推荐测试 | 0 |
| 适用 fixture AdvicePresentInRequest | 100% |
| contract-heavy fixture Hypothesis | 100% |
| baseline/post fixture Differential | 100% |
| Candidate fixture C2/C3 | 100% |
| V3 导致 Permission deny | 0 |
| event replay | deterministic |
| 旧 20 题 repository anchor | ≥18/20；其余必须具体 unknown |

## 5.4 机制激活矩阵

每道 development task 必须给出：

| Task | Anchor | Advice | In Request | Hypothesis | Matrix | Differential | Best Candidate |
|---|---|---|---|---|---|---|---|

若矩阵中适用模块仍为空，不得用“readiness success”替代。

---

# 6. P3：4 题严格配对 Pilot

## 6.1 目的

回答：

> 相同代码、模型、任务和运行时间下，V3.1 是否比 V2_CONTROL 更少犯契约与验证错误？

## 6.2 任务

1. `beetbox__beets-5457`：Python compatibility；
2. `deepset-ai__haystack-8489`：backend / behavior contract；
3. `dynaconf__dynaconf-1249`：default/config surface；
4. `delgan__loguru-1297`：exception / boundary contract。

## 6.3 设计

```text
4 tasks × 2 treatments = 8 task-runs
V2_CONTROL vs V3_CORE
```

固定：

- 同一 bound main；
- 同一时间窗口；
- 同一模型、Prompt、Provider；
- 同一 evaluator；
- strict serial；
- retry=0；
- fallback=false；
- 独立 task-run identity；
- treatment 顺序预先冻结并交错；
- 新付费授权。

## 6.4 主指标

优先级 1：

- Evidence 被模型请求携带；
- Hypothesis 被真实工具证据淘汰；
- impacted-test precision；
- C2/C3 Candidate；
- collection / global regression。

优先级 2：

- resolved；
- requests；
- Token；
- cost。

## 6.5 P3 出口门

必须全部满足：

- [ ] 8 个 task-runs 形成可审计终态，或至多 1 个明确 infra；
- [ ] 适用任务 AdvicePresentInRequest = 100%；
- [ ] venv contamination = 0；
- [ ] 至少 3/4 V3 task 形成 C2/C3；
- [ ] 无 resolved/control 回退；
- [ ] 至少 1 个新增 resolved，或至少 2 个任务出现明确的近端失败改善；
- [ ] 没有 Stage C 式协议死锁；
- [ ] 成本增加有解释且可接受。

不满足则回到 P1/P2，不进入 P4。

---

# 7. P4：8 题 fresh screening holdout

## 7.1 目的

判断 V3.1 的改善是否能泛化到未参与：

- Goal 4；
- 失败分析；
- V3 设计；
- P2 fixture；
- P3 Pilot

的新任务。

## 7.2 设计

```text
8 fresh tasks × 2 treatments
```

分层覆盖：

- contract/API；
- default/config；
- exception/boundary；
- backend；
- 单文件 / 多文件；
- 不同请求复杂度。

## 7.3 解释边界

8 题是 screening holdout，不是最终 leaderboard。

只有出现正向信号，才另行决定是否做：

```text
20-task confirmation study
```

不自动扩展。

---

# 8. 方案与进度快照规则

每一版整体方案必须在顶部记录：

```yaml
plan_version:
created_at:
status:
supersedes:
agent_version:
eval_version:
bound_main:
current_study:
latest_runs:
latest_artifacts:
current_result:
open_blockers:
next_gate:
```

每次范围或出口门变化：

- 保留旧文件；
- 新建新版本；
- 旧版标记 `Superseded`；
- 在新版写明 change summary；
- 不覆盖旧版。

## 8.1 版本递增

- **PATCH**：文字澄清、错别字、链接，不改变执行范围或门槛；
- **MINOR**：任务数、阶段出口门、顺序、范围发生兼容性调整；
- **MAJOR**：北极星、实验设计、版本体系或付费策略发生根本变化。

---

# 9. 当前进度看板

| 工作包 | 状态 | 证据 | 下一动作 |
|---|---|---|---|
| V3.0 paid replay | Completed | Runs 30503096853 / 30510508446 | 不补跑 |
| V3.0 Artifact audit | Completed | 两个 Artifact + activation audit | 写入仓库 |
| P0 正式收口 | Not started | 本方案与现有审计材料 | 一个 docs PR |
| P1 V3.1 implementation | Not started | 明确 Evidence-to-Decision 范围 | P0 后开始 |
| P2 zero-provider activation | Not started | 本方案硬门 | 与 P1 同 PR |
| P3 paired Pilot | Blocked by P2 | 需要新授权 | P2 通过后 |
| P4 fresh holdout | Blocked by P3 | 需要新授权 | P3 通过后 |

---

# 10. 总停止规则

发现任一情况，不扩大范围：

1. Advice 未进入真实模型 request；
2. Evidence 仍只有 issue wrapper；
3. ImpactSlice 扫描 venv/site-packages；
4. Hypothesis / Matrix / Differential 仍是 schema-only；
5. Candidate 全部停在 C1；
6. V3 造成编辑或 Permission 死锁；
7. treatment 外条件不一致；
8. Artifact 无法证明真实执行；
9. paid run 出现未授权 dispatch；
10. resolved rate 没有信号却试图直接 Full-20。

---

# 11. 方案变更记录

## v1.1.0 — 2026-07-30 15:02（UTC+8）

相对 v1.0：

- 正式采用路径 A，不补跑 `checkov-6893`；
- 增加产品 / Eval / Study / Run / 文档版本分层；
- 增加四级 Activation Ladder；
- 将 P3 从 6 题收缩为 4 题；
- 将 P4 从 12 题收缩为 8 题 screening holdout；
- 将 resolved rate 明确设为北极星；
- 增加反过度设计约束；
- 增加方案快照和版本递增规则；
- 增加“未通过激活门不得扩大”的硬停止规则。

## v1.0.0 — 2026-07-30

- 首次提出 P0→P4；
- 规划正式收口、机制修复、zero-provider、paired Pilot 和 fresh holdout。
