# CodePaceX 版本、配置与证据治理规范

> **规范版本**：v1.0.0
> **生成时间**：2026-07-30 15:02（UTC+8）
> **规范状态**：Proposed
> **适用范围**：产品代码、Agent 能力、评测平台、实验、Artifact、演进文档、方案文档、简历与面试材料。
> **设计原则**：轻量、可追溯、不可覆盖、避免重复；只管理会影响决策和结论的配置项。

---

# 0. 这是什么思想？

你描述的不是单一技术，而是多个成熟学科思想的组合：

1. **Software Configuration Management（软件配置管理）**
   管理代码、配置、版本、基线、变更和状态。

2. **Systems Engineering Configuration Management（系统工程配置管理）**
   管理系统从概念、设计、实现、验证到退役的完整生命周期。

3. **Experiment Tracking / MLOps / LLMOps（实验追踪）**
   记录每次实验的代码、参数、指标、Artifact 和环境。

4. **Traceability（可追溯性）**
   建立需求 → 决策 → 代码 → 测试 → 结果 → Claim 的链路。

5. **Docs-as-Code（文档即代码）**
   Markdown 文档与代码一起进入 Git、PR、review 和版本历史。

6. **Architecture / Decision Records（ADR/Decision Log）**
   保存为什么选择某个方案、放弃什么方案、以后何时重审。

7. **Reproducible Research（可复现研究）**
   让别人能够根据相同输入、代码、环境和协议重建结论。

8. **Knowledge Management（工程知识管理）**
   把隐性的项目经验变为可搜索、可继承的显性知识。

你的需求可以概括为：

> **轻量配置管理 + 实验追踪 + 决策记录 + Evidence-backed Claims。**

---

# 1. 可参照的成熟理念

## ISO 10007：配置管理

其核心结构可以简化为：

- configuration management planning；
- configuration identification；
- change control；
- configuration status accounting；
- configuration audit。

映射到 CodePaceX：

| ISO 思想 | CodePaceX 实践 |
|---|---|
| Planning | 方案文档、预算授权、Study Charter |
| Identification | commit、模型、Prompt、task、evaluator、runtime identity |
| Change Control | PR、版本递增、审批边界 |
| Status Accounting | Evaluation History、Artifact Index、进度快照 |
| Audit | digest、Claims、Artifact 独立审计 |

## Semantic Versioning

使用：

```text
MAJOR.MINOR.PATCH
```

- MAJOR：不兼容的目标、合同或含义变化；
- MINOR：兼容新增能力、范围或门槛变化；
- PATCH：不改变语义的修复和澄清。

## Keep a Changelog

每份长期维护文档和 release 应记录：

- Added；
- Changed；
- Fixed；
- Deprecated；
- Removed；
- Security；
- Superseded。

## Conventional Commits

推荐：

```text
feat(agent):
fix(eval):
docs(evolution):
test(v3):
refactor(evidence):
chore(release):
```

让 Git 历史对人和自动工具都可读。

## ADR / Decision Log

每个重要选择一个短文档：

- Context；
- Decision；
- Alternatives；
- Consequences；
- Status；
- Supersedes / Superseded by。

例如：

```text
DR-0007: Keep V3.0 at 19/20 and do not retry checkov-6893
```

旧决策不删除，只标记 superseded。

## MLflow / DVC 式实验追踪

核心不是一定引入工具，而是采用其模型：

```text
Experiment / Study
→ Run
→ Parameters
→ Metrics
→ Tags
→ Artifacts
→ Code version
```

CodePaceX 现有 Artifact 和 Evaluation History 已接近这种形式，不必立刻迁移到 MLflow。

## NIST SSDF

可借鉴：

- 跟踪需求、风险和设计决策；
- 保存变更和验证证据；
- 使用共同词汇；
- 将安全、构建和发布纳入生命周期。

---

# 2. CodePaceX 采用的最小治理模型

为了避免再次过度设计，只保留六个核心实体：

```text
Release
Study
Run
Artifact
Decision
Document
```

## 2.1 Release

代码或评测平台的可比较版本。

### Agent

```text
CPX-Agent v3.0.0
CPX-Agent v3.1.0
```

### Eval

```text
CPX-Eval v2.0.0
CPX-Eval v2.0.1
```

## 2.2 Study

一组要回答同一个问题的实验。

```text
STUDY-20260721-GOAL4-BASELINE
STUDY-20260730-V3-REPLAY
STUDY-202608xx-V3-PAIRED-PILOT
```

## 2.3 Run

一次执行，不可覆盖。

```text
github_run_id
internal_run_id
attempt_id
```

## 2.4 Artifact

Run 的不可变证据：

```text
artifact_id
artifact_name
digest
created_at
retention
```

## 2.5 Decision

重要方案选择。

```text
DR-0001-use-official-evaluator.md
DR-0002-no-auto-retry.md
DR-0003-v3-path-a-no-checkov-retry.md
```

## 2.6 Document

演进史、方案、报告、简历叙事等独立版本。

---

# 3. 四层版本体系

## 3.1 产品能力版本

回答：

> Agent 本身有什么新能力？

规则：

- MAJOR：Agent 行为合同或架构根本改变；
- MINOR：兼容新增能力；
- PATCH：能力实现 Bug 修复，不改变设计目标。

当前建议：

```text
CPX-Agent v3.0.0
```

下一版：

```text
CPX-Agent v3.1.0
```

原因：Evidence-to-Decision activation 是兼容性能力完善，不是 V4。

## 3.2 评测平台版本

回答：

> 评测协议、环境和证据链有什么变化？

当前：

```text
CPX-Eval v2.0.0
```

示例：

- 修复 collector 但不改变结果含义：v2.0.1；
- 新增兼容性指标和 activation matrix：v2.1.0；
- 改变 denominator 或 trial semantics：v3.0.0。

## 3.3 Study / Run 版本

Study 不使用 SemVer，使用日期和主题：

```text
STUDY-YYYYMMDD-TOPIC
```

同一 Study 的执行：

```text
RUN-01
RUN-02-INFRA-RECOVERY
```

但实际 GitHub Run ID 仍是唯一权威身份。

## 3.4 文档版本

每个文档独立 SemVer：

```text
<主题>_v1.1.0_YYYYMMDD_HHMM.md
```

不要设置一个覆盖全项目所有文档的全局文档版本。

---

# 4. 必须版本化但容易遗漏的内容

你不仅要管理代码和报告，还要管理以下实验输入：

## 4.1 Agent 输入

- Agent commit；
- capability version；
- System Prompt hash；
- tool schema hash；
- feature flags；
- Permission mode；
- context policy；
- Candidate policy。

## 4.2 模型与 Provider

- Provider profile；
- model；
- endpoint region；
- protocol；
- max tokens；
- thinking budget；
- retry；
- fallback；
- timeout；
- 账号切换说明，但绝不保存 Secret 内容。

## 4.3 任务与评分

- dataset revision；
- task list hash；
- repo/base commit；
- problem statement hash；
- evaluator commit；
- evaluator image/runtime；
- F2P/P2P interpretation。

## 4.4 成本

- pricing snapshot；
- authorization；
- allocation；
- hard cap；
- charge / settlement；
- conservative exposure；
- active reservation。

## 4.5 环境

- OS / architecture；
- Python；
- Docker；
- dependency lock；
- disk/inode；
- controller environment；
- evaluator environment；
- cache policy。

## 4.6 结论与对外 Claim

- Claim 内容；
- 指标分母；
- source report；
- Artifact ID；
- evidence status；
- valid / verified / insufficient；
- 简历使用状态；
- superseded claim。

这些是过去最容易遗漏的配置项。

---

# 5. 推荐目录结构

保持轻量，不要求立刻大迁移：

```text
docs/
  INDEX.md
  evolution/
    CodePaceX_项目与评测工程演进史_...
  plans/
    CodePaceX_后续整体工作方案_...
  decisions/
    DR-0001-...
  career/
    CodePaceX_当前能力_简历与面试叙事_...
  snapshots/
    SNAPSHOT-20260730.md

evals/
  EVALUATION_HISTORY.md
  EVALUATION_ARTIFACT_INDEX.md
  reports/
  studies/
```

历史文档可继续原位保存；先由 `docs/INDEX.md` 建导航，不要为了目录美观大规模移动文件。

---

# 6. 文件命名规则

## 6.1 长期文档

```text
<项目>_<主题>_v<SemVer>_<YYYYMMDD_HHMM>.md
```

示例：

```text
CodePaceX_后续整体工作方案_v1.1.0_20260730_1502.md
```

## 6.2 Decision Record

```text
DR-<四位编号>-<短标题>.md
```

编号不复用。

## 6.3 Snapshot

```text
SNAPSHOT-<YYYYMMDD_HHMM>-<主题>.md
```

## 6.4 Experiment Report

```text
STUDY-<YYYYMMDD>-<主题>_REPORT_v<SemVer>.md
```

---

# 7. 文档状态

每份计划或决策必须有状态：

- Draft；
- Proposed；
- Approved；
- In Progress；
- Completed；
- Superseded；
- Rejected；
- Archived。

旧方案被新方案替代时：

```text
status: Superseded
superseded_by: <new file>
```

不删除，不原地覆盖。

---

# 8. 最小 Snapshot 模板

```yaml
snapshot_id:
captured_at:
repo:
branch:
head:
worktree_status:
agent_version:
eval_version:
active_plan:
current_study:
latest_runs:
latest_artifacts:
formal_results:
ledger_status:
open_blockers:
approved_paid_scope:
next_gate:
claims_version:
resume_narrative_version:
```

Snapshot 只在以下时机创建：

1. 正式 release；
2. paid Study 派发前；
3. paid Study 终态审计后；
4. 整体方案重大变更；
5. 对话上下文迁移前。

不要每个小 commit 都生成 Snapshot。

---

# 9. Decision Record 触发条件

只有以下决策需要 DR：

- 是否进入 paid run；
- 是否 retry infrastructure failure；
- 是否改变模型、Prompt、任务或 evaluator；
- 是否改变实验分母；
- 是否改变版本体系；
- 是否停止或扩大 Study；
- 是否采纳新 Agent 能力机制；
- 是否把结果用于简历 Claim。

普通 Bug 修复不需要 DR。

---

# 10. Claim Traceability Matrix

建立一个简单 CSV 或 Markdown 表：

| Claim ID | 对外表述 | Scope | Source Report | Artifact | Status | Resume Allowed |
|---|---|---|---|---|---|---|
| CLM-001 | Goal 4 20/20 scorable | 20 fixed tasks | Goal4 report | 8496125148 | verified | yes |
| CLM-002 | Goal 4 4 resolved | same | Goal4 report | 8496125148 | verified | yes |
| CLM-003 | V3 5 resolved / 19 scorable + 1 infra | old 20 tasks | V3 report | 8744897594 + 8749299095 | audited | yes, with boundary |
| CLM-004 | V3 caused capability improvement | causal | none | none | unsupported | no |

这个表能把简历、面试和 Artifact 连起来。

---

# 11. Git 与发布建议

## Commit

采用 Conventional Commits：

```text
feat(agent): inject repository evidence into model requests
fix(eval): exclude virtualenv paths from impact slices
test(v3): cover candidate C2 promotion
docs(evolution): add July 2026 evaluation timeline
```

## Tag / Release

只给真正的 Agent 或 Eval release 打 tag：

```text
agent-v3.0.0
eval-v2.0.0
agent-v3.1.0
```

不要为每个 Study Run 打产品 tag。

GitHub Release 用于：

- 固定 release commit；
- release notes；
- 对应的正式报告；
- 主要 Artifact 索引；
- 已知限制。

---

# 12. 高效性规则

1. 一个事实只维护一个正式来源，其他文档链接它。
2. History 保存“发生了什么”；Plan 保存“接下来做什么”；Narrative 保存“怎么表达”。
3. Run 数据不手工复制到多份文档，优先从 Artifact 编译。
4. 文档版本只在语义变化时递增，不因每个错别字创建新文件。
5. 不为每次 CI 生成演进报告。
6. 不为了规范而迁移所有旧文件。
7. 不引入 MLflow/DVC，除非现有 Artifact Index 已无法管理规模。
8. 保留旧名称映射，未来只使用统一命名。

---

# 13. 这属于哪些学科？

| 学科/领域 | 对应内容 |
|---|---|
| 软件工程 | 版本、Git、测试、发布、配置管理 |
| 系统工程 | 生命周期、基线、变更控制、状态核算、审计 |
| MLOps / LLMOps | 模型、参数、实验、指标、Artifact、可复现 |
| 实验设计 | treatment、control、paired study、holdout |
| 科学方法 | 假设、证伪、证据、因果边界 |
| 质量管理 | 过程一致性、验证、审计、持续改进 |
| 知识管理 | 演进史、Decision Log、上下文迁移 |
| 技术沟通 | README、简历、面试叙事、Claim 边界 |

---

# 14. 推荐采用而不建议照搬的标准

## 建议采用

- SemVer 的版本语义；
- Keep a Changelog 的变更分类；
- Conventional Commits；
- ADR 的决策保留与 superseded；
- ISO 10007 的配置识别、变更控制、状态核算和审计；
- MLflow 的 Study/Run/Parameter/Metric/Artifact 抽象；
- DVC 的 Git-like data/artifact versioning 思想；
- NIST SSDF 的需求、风险和设计决策追踪。

## 不建议直接照搬

- 完整企业级 CMDB；
- 重型 ALM 平台；
- 为个人项目部署复杂 MLflow server；
- 每个小决定都写 ADR；
- 每个 commit 都发 release；
- 把所有 Artifact 提交 Git；
- 在没有团队规模前建立复杂权限审批流。

---

# 15. 当前必须创建的三个 Decision Records

## DR-0001：采用四层版本体系

- Agent Release；
- Eval Release；
- Study/Run；
- Document。

## DR-0002：V3.0 保留 19/20，不立即 retry

- 路径 A；
- 资源转向 activation fidelity；
- 原 infra Attempt 永久保留。

## DR-0003：未通过 Activation Ladder 不允许扩大 paid scale

- L1/L2/L3/L4；
- paid Pilot 前 AdvicePresentInRequest；
- Pilot 没信号不进入 fresh holdout。

---

# 16. 规范变更记录

## v1.0.0 — 2026-07-30 15:02（UTC+8）

- 首次建立统一版本、配置与证据治理规范；
- 定义六个核心实体；
- 定义四层版本体系；
- 增加 Snapshot、Decision Record 和 Claim Traceability；
- 列出容易遗漏的实验配置项；
- 将成熟标准裁剪为适合个人 Agent 项目的轻量实践。
