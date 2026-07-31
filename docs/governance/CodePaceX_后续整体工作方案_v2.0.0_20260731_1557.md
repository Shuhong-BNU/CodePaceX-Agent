# CodePaceX 后续整体工作方案

> 版本：v2.0.0  
> 状态：当前有效

## 阶段状态

```text
P0-P3-B3  已完成
P3-B-A1   历史 paid attempt，Provider 前 fail-closed，0/8
P3-B-R1   已合并：production_adapter_argument_shape_mismatch 修复
P3-B-R2   已完成：main CI 与 post-merge zero-provider readiness
P3-B-A2   未授权
P4         blocked
```

## 新 paid attempt 的唯一前置条件

1. 用户提供新的、独立且明确的付费授权。
2. 在当时的正式 main 上完成只读身份核验。
3. 生成全新的 acknowledgement、dispatch token 与 internal run ID。
4. 仅在授权合同允许后发起 exactly one dispatch。

不得复用 Run `30609517826` 对应的任何一次性身份；不得 retry、rerun、continuation 或 fallback。P4 在 P3-B 产生完整且可审计的配对结果前保持 blocked。
