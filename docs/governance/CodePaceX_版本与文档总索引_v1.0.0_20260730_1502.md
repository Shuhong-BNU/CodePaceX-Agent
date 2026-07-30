# CodePaceX 版本与文档总索引

> **索引版本**：v1.0.0
> **生成时间**：2026-07-30 15:02（UTC+8）
> **状态**：Superseded
> **替代版本**：`CodePaceX_版本与文档总索引_v1.1.0_20260730_1828.md`
> **用途**：作为当前 CodePaceX 演进、方案、求职叙事和治理规范的唯一入口。
> **原则**：索引只导航，不复制详细事实。

---

# 当前统一快照

```yaml
agent_release: CPX-Agent v3.0.0
eval_release: CPX-Eval v2.0.0
current_result:
  goal4: 20 scorable / 4 resolved
  v3: 20 terminal / 19 scorable / 5 resolved / 1 infra
path_decision: Path A — keep 19/20; no immediate retry
active_plan: v1.1.0
next_release_target: CPX-Agent v3.1.0
next_gate: zero-provider activation fidelity
```

---

# 当前文档

1. [CodePaceX_项目与评测工程演进史_v1.0.0_20260730_1502.md](./CodePaceX_项目与评测工程演进史_v1.0.0_20260730_1502.md)
   解释从 Baseline、Goal 2/3/4、Stage C、Evaluation V2 到 Capability V3 的完整演进和时间线。

2. [CodePaceX_后续整体工作方案_v1.1.0_20260730_1502.md](./CodePaceX_后续整体工作方案_v1.1.0_20260730_1502.md)
   当前 P0→P4 工作方案；包含路径 A、Activation Ladder、resolved-rate 北极星和阶段出口门。

3. [CodePaceX_当前能力_简历与面试叙事_v1.0.0_20260730_1502.md](./CodePaceX_当前能力_简历与面试叙事_v1.0.0_20260730_1502.md)
   当前 Agent 产品、Eval、Infra、研究和工程能力；简历 Bullet 与面试叙事。

4. [CodePaceX_版本配置与证据治理规范_v1.0.0_20260730_1502.md](./CodePaceX_版本配置与证据治理规范_v1.0.0_20260730_1502.md)
   产品、评测、Study、Run、Artifact、文档的版本、配置和证据治理规则。

5. `CodePaceX_Goal4_vs_V3_结果与机制激活审计_20260730.md`
   V3.0 结果与机制激活证据。

6. `CodePaceX_CapabilityV3_后续工作流程方案_v1.0_20260730.md`
   上一版方案；保留为历史，后续标记 Superseded by v1.1.0。

---

# 历史权威证据

## Goal 4

```text
20/20 scorable
4 resolved
16 unresolved
Run: 29830820618
Artifact: 8496125148
```

## V3 Head

```text
Run: 30503096853
Artifact: 8744897594
digest: sha256:01324ab8b366b41c8c320e50b27cda407bc4daefb87d42e566f72b6801a50075
```

## V3 Tail

```text
Run: 30510508446
Artifact: 8749299095
digest: sha256:c09b6dcab74582ea4158ecdc2ad660c6236a9d67dbb431c66c34ab09154421bf
```

---

# 使用顺序

## 自己理解项目

```text
演进史
→ 当前能力与叙事
→ 机制激活审计
```

## 指导 Codex 开工

```text
治理规范
→ 当前整体方案 v1.1.0
→ 当前 Snapshot
→ 明确 P0 或 P1 范围
```

## 写简历和准备面试

```text
当前能力、简历与面试叙事
→ Claim Traceability Matrix
→ 源码/测试/Artifact 证据定位
```

---

# 索引变更记录

## v1.0.0 — 2026-07-30 15:02（UTC+8）

- 建立统一文档入口；
- 登记当前 Agent/Eval 版本；
- 登记路径 A；
- 连接演进史、方案、叙事和治理规范。
