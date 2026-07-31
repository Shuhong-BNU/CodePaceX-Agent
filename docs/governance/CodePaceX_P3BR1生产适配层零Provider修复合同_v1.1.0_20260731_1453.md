# CodePaceX P3-B-R1 生产适配层零 Provider 修复合同

> 版本：v1.1.0  
> 生成时间：2026-07-31 14:53（UTC+8）  
> 状态：可执行  
> 性质：零 Provider 工程修复；不包含付费授权

## 1. 唯一目标

修复 P3-B 生产适配层的环境合同参数形状错误，并用真实生产调用路径的零 Provider 测试证明 8 个冻结 task-run 不再在该适配层失败。

## 2. 已确认根因

```text
p3b_paid_executor._real_task_executor
  输入：完整环境合同映射
  当前行为：提前选出单项合同
  错误传参：把单项合同传给 _full_task_executor

full_replay._full_task_executor
  既有接口：接收完整环境合同映射
  行为：按 instance_id 选择单项合同
  结果：对单项对象二次索引，触发 KeyError
```

正式环境合同包含 4 个唯一任务，不允许再以“补环境 registry”为默认修复方向。

## 3. 最小代码范围

优先：

- 修改 `evals/evaluation_v2/p3b_paid_executor.py` 的生产适配调用；
- 传入完整环境合同映射；
- 保持 `full_replay._full_task_executor` 的共享接口合同不变。

允许同步：

- 对应测试；
- freeze/readiness/runtime 身份；
- 必要 workflow 身份与中文治理文档。

禁止：

- 新建第二套环境 registry；
- 为 4 个任务硬编码环境数据；
- 修改实验变量；
- 无关重构。

## 4. 必须新增的防回归证据

测试必须实际覆盖：

```text
P3-B manifest / task-run
→ paid coordinator
→ _real_task_executor
→ _full_task_executor
→ environment selection
→ Provider initialization boundary
```

最低断言：

1. 4 个唯一任务均不触发二次索引错误；
2. 8 个冻结 task-run 均进入共享执行器；
3. 传给 `_full_task_executor` 的对象保持完整 mapping 形状；
4. 任一映射缺失任务时 fail-closed，并明确报告 instance；
5. Provider transport hard-disabled；
6. Provider requests / Usage / charge = `0 / 0 / CNY 0`；
7. Secret 值未读取；
8. 原 Run 和 Artifact 不被覆盖。

## 5. CI 与 readiness 出口门

```text
root cause documented
minimal adapter fix committed
production adapter regression test passed
4/4 unique task paths passed
8/8 frozen task-run preflight passed
Provider requests / Usage / charge = 0 / 0 / CNY 0
active_reservation = null
paid job skipped
Ubuntu CI success
macOS CI success
P3-B zero-provider readiness success
new readiness Artifact complete
freeze/readiness identities synchronized
Chinese governance docs updated
PR mergeable with no blocker
```

唯一允许的出口状态：

```text
P3-B-R1_READY_FOR_MERGE
```

不得自动表述为：

```text
P3-B ready for paid rerun
```

## 6. 后续付费条件

合并后仍必须依次完成：

```text
post-merge 只读核验
→ 用户全新明确付费授权
→ 全新 acknowledgement
→ 全新 dispatch token
→ 全新 internal run ID
→ exactly one new dispatch
```
