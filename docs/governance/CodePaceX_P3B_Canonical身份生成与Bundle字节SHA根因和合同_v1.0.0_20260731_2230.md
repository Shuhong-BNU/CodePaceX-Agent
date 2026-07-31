# CodePaceX P3-B：Canonical Identity Generator 与 Bundle Byte SHA 合同

> 版本：v1.0.0
> 状态：集中式零 Provider PR 待审核
> 语言：中文

## 根因

`canonical_paid_input_bundle()` 曾把 authorization acknowledgement、dispatch token、internal run ID 视为外部输入，导致调用方可手工构造身份；workflow 与 executor 又没有 `final_input_bundle_sha256`，不能机器证明两阶段读取的是同一文件字节。

根因名称：`canonical_identity_generation_and_bundle_byte_binding_missing`。

## API 与 Schema

`evals.evaluation_v2.p3b_paid_executor.generate_paid_input_bundle(root, test_only, provider_secret_present)` 是唯一生成 API。它从当前 HEAD、committed freeze、allocation 和 `PARENT_CAP` 取得绑定，使用 UTC 时间、main short SHA 和 cryptographically secure random suffix 生成三个 identity。acknowledgement 前缀直接引用唯一常量 `REQUIRED_ACKNOWLEDGEMENT_PREFIX`，不接受调用方提供的前缀。

bundle schema v2 的固定顺序为：

```text
schema_version, identity_mode, generated_at, expected_main_sha,
expected_freeze_sha256, expected_allocation_hash, approved_parent_cap_cny,
authorization_acknowledgement, dispatch_token, run_id, provider_secret_present
```

序列化规则：`json.dumps(..., ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\\n'`。SHA 不写回 bundle，避免自指；`final_input_bundle_sha256=sha256(raw_bytes)` 是 bundle 的外部绑定。

## 端到端路径

```text
generator -> canonical raw bytes + SHA
workflow base64 decode once -> recompute SHA
validate-only opens bytes -> recompute SHA -> parse/validate
paid executor opens same path -> recompute SHA -> parse/validate
preflight Artifact / terminal summary -> record identical SHA
```

任何 SHA 不匹配、解析后重序列化、重复键、未知字段、非法 schema、非末尾单 LF 或 test-only bundle 进入真实 paid CLI 都会在 Provider 前 fail-closed。
