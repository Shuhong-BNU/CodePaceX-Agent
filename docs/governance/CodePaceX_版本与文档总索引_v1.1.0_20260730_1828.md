# CodePaceX 版本与文档总索引

> **索引版本**：v1.1.0
> **生成时间**：2026-07-30 18:28（UTC+8）
> **状态**：Current
> **替代版本**：`CodePaceX_版本与文档总索引_v1.0.0_20260730_1502.md`
> **使用方式**：将本索引与下列文件放在同一文件夹中；Markdown 阅读器支持相对路径时，可直接点击跳转。
> **审计范围**：上一版文档包中的 9 个文件，以及本次新增索引。

---

# 1. 当前统一快照

```yaml
snapshot_time: 2026-07-30T18:28:00+08:00
repository: Shuhong-BNU/CodePaceX-Agent
agent_release_label: CPX-Agent v3.0.0
agent_release_label_status: proposed logical label; Git tag/release not yet created
eval_release_label: CPX-Eval v2.0.0
eval_release_label_status: proposed logical label; Git tag/release not yet created
repository_document_integration: P0 zero-provider documentation branch prepared; PR pending
formal_goal4:
  result: 20/20 scorable; 4 resolved; 16 unresolved
  final_run: 29830820618
  artifact_id: 8496125148
current_v3_replay:
  completion: two-run infrastructure-recovery completion
  result: 20 terminal; 19 scorable; 5 resolved; 14 unresolved; 1 infrastructure_error
  head_run: 30503096853
  head_artifact: 8744897594
  tail_run: 30510508446
  tail_artifact: 8749299095
path_decision: Path A; keep 19/20 and do not immediately retry checkov-6893
active_plan: CodePaceX 后续整体工作方案 v1.1.0
next_release_target: CPX-Agent v3.1.0 activation fidelity
next_gate: P0 review and CI, then P1/P2 zero-provider activation verification
```

> **重要边界**：`CPX-Agent v3.0.0`、`CPX-Eval v2.0.0` 当前用于统一文档叙事和后续版本规划；在仓库完成 tag / GitHub Release 前，不应对外表述为“已经正式发布的 release”。

---

# 2. 文档数量与状态

加入 P0 Snapshot 后同一文件夹中共有 **11 个相关文件**：

| 分类 | 数量 | 说明 |
|---|---:|---|
| Current 人类可读文档 | 7 | 本索引、演进史、当前方案、当前能力叙事、治理规范、结果与激活审计、P0 Snapshot |
| Current 结构化证据表 | 2 | 20 题逐题结果 CSV、机制激活 CSV |
| Superseded 历史文档 | 2 | 上一版方案、上一版索引 |
| **合计** | **11** | 旧版保留但不再指导当前执行 |

---

# 3. 当前文档入口

## 3.1 当前执行与理解文档

1. [项目与评测工程演进史 v1.0.0](./CodePaceX_项目与评测工程演进史_v1.0.0_20260730_1502.md)
   从 2026-07-06 的轻量 Baseline，经过 Goal 2/3/4、Stage B/C/D、Evaluation V2，到 2026-07-30 Capability V3 replay 与 activation audit。

2. [后续整体工作方案 v1.1.0](./CodePaceX_后续整体工作方案_v1.1.0_20260730_1502.md)
   当前唯一执行方案。采用路径 A；先 P0 收口，再 P1 数据面修复、P2 zero-provider 激活验收、P3 小型配对、P4 fresh holdout。

3. [当前能力、简历与面试叙事 v1.0.0](./CodePaceX_当前能力_简历与面试叙事_v1.0.0_20260730_1502.md)
   用于理解当前 Agent 产品、Eval、Infra、研究和工程能力，并形成保守可信的求职表达。

4. [版本、配置与证据治理规范 v1.0.0](./CodePaceX_版本配置与证据治理规范_v1.0.0_20260730_1502.md)
   规定 Release、Study、Run、Artifact、Decision、Document 的身份、版本、Snapshot 和 Claim 追溯方式。

5. [Goal 4 → V3 结果与机制激活审计](./CodePaceX_Goal4_vs_V3_结果与机制激活审计_20260730.md)
   当前 V3 结果和“哪些机制实际激活”的权威分析入口。

6. [P0 V3.0 当前 Snapshot](./CURRENT_SNAPSHOT_P0_V3_CLOSEOUT_20260730.md)
   记录 P0 closeout 的分支、逻辑版本标签、Path A 和下一 gate。

7. [Capability V3.0 final report](../../evals/CAPABILITY_V3_GOAL4_FINAL_REPORT.md)
   仓库内唯一正式的 20 题 two-run completion 报告。

8. [Capability V3.0 activation postmortem](../../evals/CAPABILITY_V3_ACTIVATION_POSTMORTEM.md)
   Artifact 所支持的真实机制激活边界。

## 3.2 结构化证据

9. [Goal 4 → V3 20 题逐题结果 CSV](./CodePaceX_Goal4_vs_V3_20题逐题结果_20260730.csv)

10. [V3 机制激活审计 CSV](./CodePaceX_V3_机制激活审计_20260730.csv)

## 3.3 历史版本（不再指导当前执行）

11. [Capability V3 后续工作流程方案 v1.0](./CodePaceX_CapabilityV3_后续工作流程方案_v1.0_20260730.md)
   **状态：Superseded**；由“后续整体工作方案 v1.1.0”替代。

12. [版本与文档总索引 v1.0.0](./CodePaceX_版本与文档总索引_v1.0.0_20260730_1502.md)
   **状态：Superseded**；由本索引替代。

---

# 4. 文档一致性审计结论

## 4.1 已确认符合当前事实

- Goal 4 最终基线为 `20/20 scorable，4 resolved，16 unresolved`；最终闭环身份是 Run `29830820618`、Artifact `8496125148`。
- V3 使用 two-run infrastructure-recovery completion：Head Run `30503096853` + Tail Run `30510508446`。
- V3 合并结果为 `20 terminal，19 scorable，5 resolved，14 unresolved，1 infrastructure_error`。
- `haystack-8489` 为 unresolved→resolved；历史 4 个 resolved 均保持 resolved。
- `checkov-6893` 保留 infrastructure_error，采用路径 A，不立即补跑。
- V3 activation audit 中 Evidence/Hypothesis/Matrix/Differential 基本未形成有效运行产物，Candidate/预算/故障隔离真实生效。
- 后续主线是 P0→P4，不直接扩大到新的 full-20 paid replay。

## 4.2 需要明确而非篡改旧文档的事项

1. **版本标签尚未正式发布**
   `CPX-Agent v3.0.0` 与 `CPX-Eval v2.0.0` 是本轮建立的逻辑版本标签。旧文档中的“当前版本”应按此理解，不能等同于已经存在的 Git tag。

2. **P0 仍处于 review/CI 前的文档分支状态**
   本索引及配套文档已落位仓库分支；PR、CI、合并和 release/tag 尚未完成。

3. **Goal 4 存在早期 partial run 与最终 recovery run**
   Run `29803967008` / Artifact `8486382695` 是较早的 partial/finalizer 审计对象；当前 `4/20` 正式基线应引用后续 final recovery Run `29830820618` / Artifact `8496125148`。两者不能混为同一 Artifact。

4. **“北极星”不是单一无条件优化指标**
   resolved rate 是下一轮最高优先级结果指标，但必须同时受 scorable 完整性、历史 resolved 不回退、机制真实激活、成本和基础设施错误等 guardrails 约束。

## 4.3 当前无需大范围重写的结论

- 演进史、当前方案、能力叙事和治理规范的主事实与当前决策一致。
- 目前不需要为上述四份文件各自制造一个小 Patch 版本；由本索引集中增加澄清，能够避免无价值的文档膨胀。
- 当 P0 真正进入仓库并创建 release/tag 后，再统一生成下一批 PATCH/MINOR 文档版本。

---

# 5. 使用顺序

## 开始后续工程工作

```text
本索引
→ 后续整体工作方案 v1.1.0
→ Goal 4 → V3 激活审计
→ P0 Snapshot / Codex 执行提示词
```

## 自己理解项目

```text
项目与评测工程演进史
→ 当前能力、简历与面试叙事
→ Goal 4 → V3 激活审计
```

## 面试或简历核验

```text
当前能力、简历与面试叙事
→ 逐题结果 CSV
→ Artifact / Evaluation History
```

---

# 6. 索引变更记录

## v1.1.0 — 2026-07-30 18:28（UTC+8）

- 增加全部 9 个既有文件的相对路径跳转；
- 将文档集合重新分类为 Current、Evidence 和 Superseded；
- 明确更新后共 10 个相关文件；
- 增加文档一致性审计；
- 澄清 Agent/Eval 版本是逻辑标签，尚未形成 Git tag/release；
- 澄清 P0 仓库文档收口尚未完成；
- 区分 Goal 4 早期 partial run 与最终 recovery run；
- 补充 resolved-rate 北极星的 guardrails。

## v1.0.0 — 2026-07-30 15:02（UTC+8）

- 建立首版统一文档入口；
- 登记 Agent/Eval 逻辑版本、路径 A 和当前方案。
