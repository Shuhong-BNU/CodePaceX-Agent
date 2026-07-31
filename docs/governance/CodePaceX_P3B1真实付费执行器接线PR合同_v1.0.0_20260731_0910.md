# CodePaceX P3-B1 真实付费执行器接线 PR 合同

> 版本：v1.0.0  
> 时间：2026-07-31 09:10（UTC+8）  
> 状态：可执行  
> 语言：中文  
> 正式基线：`origin/main@3e18174bd110502d8b8baecd67c12027a8b2520a`  
> 性质：zero-provider engineering only

## 唯一目标

从正式 main 创建隔离 worktree 和一个 P3-B1 PR，把当前故意 `exit 1` 的 paid job 接成真实、可审计、仍默认关闭的 8-run paid executor。

本轮不得执行 P3-B 真实付费 Pilot。

## 当前缺口

当前 workflow 的 `p3b-paid-execution` 最终执行：

```bash
echo 'P3-B paid executor requires a separate explicit user authorization and is intentionally not run by P3-B0.'
exit 1
```

当前 `p3b_post_merge_rebind.py` 只支持 fake transport rehearsal，没有真实 paid CLI。

## 实现要求

### 正式入口

新增明确入口，例如：

```bash
python -m evals.evaluation_v2.p3b_post_merge_rebind execute-paid ...
```

或独立模块，但不得复制一套平行预算系统。

### Workflow

`p3b-paid-execution` 必须：

- 仅 `workflow_dispatch`；
- 仅 `main`；
- `paid_execution=true`；
- 精确 freeze SHA；
- 精确 allocation hash；
- parent cap 精确为 `292.945921`；
- acknowledgement、dispatch token、run ID 非空；
- Secret presence true；
- 调用真实 executor；
- 上传唯一 paid Artifact。

普通 PR 和未授权 dispatch 中必须 skipped。

### Runner

严格串行执行 8 个冻结 task-run：

- checkout frozen task/base commit；
- 构造 workspace；
- treatment 是唯一实验差异；
- Agent.run；
- Provider；
- Candidate；
- evaluator；
- Usage；
- ledger；
- terminal result；
- 进入下一个 task。

### 预算与停止

- parent cap：`CNY 292.945921`；
- 8 child cap：各 `CNY 36.618240`；
- spendable：`CNY 292.945920`；
- reserve：`CNY 0.000001`；
- Provider 前 reservation；
- child / parent cap 前置阻断；
- Usage 缺失保守结算并停止；
- retry=0；
- fallback=false；
- 不 rerun；
- 不 continuation；
- 不第二次 dispatch；
- accounting / active reservation failure 停止整轮；
- request ceiling 形成终态并保留 Candidate。

### Artifact

唯一 paid Artifact 至少包含：

- freeze；
- authorization；
- allocation；
- ledger；
- dispatch guard；
- 8-run manifest；
- 每 run request record；
- Candidate；
- V3 Artifact；
- predictions；
- evaluator report；
- task result；
- terminal summary；
- 4-pair merge；
- Usage / charge；
- integrity assertions。

## Zero-provider 测试

必须使用 fake Provider 验证同一真实 executor 路径：

- 8-run strict serial；
- Provider 边界前预算检查；
- Usage settlement；
- duplicate / second dispatch；
- child / parent cap；
- request ceiling；
- Usage missing；
- evaluator failure；
- Artifact missing；
- pair missing；
- active reservation；
- paid job skipped。

测试期间：

```text
Provider requests = 0
真实 Usage = 0
真实 charge = CNY 0
Secret value read = false
```

## 范围限制

不得改变任务、顺序、模型、Prompt、Provider、endpoint、evaluator、Pricing、request ceiling、retry、fallback、parent / child cap 和 Agent V3.1 算法。

严格禁止真实 Provider、Secret 值读取、paid workflow、真实 task-run、P4、Tag、Release、自动合并和修改原始本地 main。

## 停止点

创建一个 PR，等待 CI 与 review 终态后停止，不自动合并。

## 可直接发给 Codex 的提示词

请从：

```text
origin/main@3e18174bd110502d8b8baecd67c12027a8b2520a
```

创建隔离 worktree、分支和一个 P3-B1 zero-provider PR。

本轮唯一目标：将当前故意 `exit 1` 的 P3-B paid job 接成真实 8-run paid executor，但绝不执行真实 Provider 或 paid workflow。

优先复用：

- `evals/evaluation_v2/full_replay.py`
- `evals/paid_gate.py`
- `evals/evaluation_v2/control_canary.py`
- P3-B0 freeze / manifest / allocation / paired merge

不得重新设计平行 Runner、预算或 Artifact 系统。

必须实现并验证：

- 正式 paid CLI；
- workflow 调用真实 executor；
- main / freeze / allocation / cap / acknowledgement / token / run_id 全部 fail-closed；
- 8-run strict serial；
- parent / 8 child reservation 与 settlement；
- 真实 Usage 收集；
- Usage 缺失保守结算并停止；
- request ceiling；
- retry=0；
- fallback=false；
- duplicate / second dispatch 拒绝；
- 真实 task workspace；
- Candidate；
- evaluator；
- V3 raw Artifact；
- ledger closure；
- 4-pair merge；
- 唯一 paid Artifact；
- CI 中 paid job skipped。

冻结条件不得改变：

```text
parent cap = CNY 292.945921
child cap = 每个 CNY 36.618240
8-run
strict serial
request ceiling = 40/run
retry = 0
fallback = false
```

严格禁止真实 Provider、Secret 值读取、paid workflow、真实 task-run、P4、Tag、Release、自动合并和修改原始本地 main。

完成后停止并汇报：

- branch / commits / PR；
- changed files；
- paid executor 入口；
- workflow dispatch 参数；
- freeze / allocation / workflow / runner hash；
- 8-run runner；
- budget / settlement；
- negative tests；
- Provider / Usage / charge；
- Secret presence；
- CI / review；
- 是否建议合并；
- P3-B 是否仍 blocked。
