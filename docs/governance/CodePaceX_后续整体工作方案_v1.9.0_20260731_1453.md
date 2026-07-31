# CodePaceX 后续整体工作方案

> 版本：v1.9.0  
> 生成时间：2026-07-31 14:53（UTC+8）  
> 状态：当前有效  
> 正式 `main`：`e9e51c17a2b2b522b6967d7ae3f9497cd89d3531`

## 0. 总路线

```text
P0       V3.0 正式收口                         已完成
P1       V3.1 Activation Fidelity              已完成
P2       zero-provider L1-L3 验收              已完成
P3-A     4×2 配对 Pilot 冻结                   已完成
P3-B0    post-merge rebind                     已完成
P3-B1    真实 paid runner 接线                 已完成
P3-B2    paid job 完整 Git 历史修复            已完成
P3-B3    付费授权前只读核验                    已完成
P3-B-A1  首次 paid dispatch                    终态阻塞，0/8
P3-B-R1  生产适配层参数形状修复                下一步
P3-B-A2  新 paid attempt                       未授权
P4       8×2 fresh holdout                     被 P3-B 阻塞
```

## 1. 当前判断

本次失败比“环境配置缺失”更窄：

```text
环境数据完整
共享执行器接口完整
错误集中在 P3-B 调用共享执行器时的参数层级
```

因此不需要重建环境系统，也不需要扩张为新平台。应以一个最小 PR 修复 caller adapter 并补生产路径测试。

## 2. 下一步任务拆分

### R1-1 根因固化

- 将只读审计结论写入长期文档；
- 保留机器历史标签，同时新增精确因果分类；
- 标记 14:41 的“环境缺失”文档为 Superseded。

### R1-2 最小实现

- `_real_task_executor` 传入完整环境合同映射；
- 不改变 full replay 共享接口；
- 不更改实验变量。

### R1-3 生产路径测试

- 不再只用 fake executor；
- 覆盖 `_real_task_executor → _full_task_executor`；
- 覆盖 4 个唯一任务与 8 个 treatment run；
- Provider transport 为 0。

### R1-4 CI / readiness / 身份

- Ubuntu、macOS CI；
- P3-B zero-provider readiness；
- paid job skipped；
- 必要 freeze/readiness/runtime SHA 更新；
- 输出逐 run 预检记录。

### R1-5 PR 与停止

- 一个集中式 PR；
- 无阻塞 review；
- 不自动合并；
- 达到 `P3-B-R1_READY_FOR_MERGE` 后停止。

## 3. 预期结果

理想结果：

```text
1 个生产适配层调用修复
+ 1 组真实生产路径零 Provider 回归
+ 8/8 preflight
+ 0 Provider / 0 Usage / CNY 0
+ 1 个可审查 PR
```

该结果只证明修复了本次 preflight blocker，不证明 P3-B 模型结果，也不构成新付费授权。

## 4. 合并后的决策门

合并后进行 post-merge 只读核验。只有下列条件全部满足，才讨论 P3-B-A2：

- 正式 main 精确；
- 生产适配层回归在 main 上通过；
- zero-provider readiness 8/8；
- paid job skipped；
- 没有 active reservation；
- 没有复用原执行身份；
- 用户重新明确授权。

## 5. P4

P4 继续保持：

```text
blocked
```

P3-B 形成有效 4-pair 结果并完成审计前，不启动 fresh holdout。
