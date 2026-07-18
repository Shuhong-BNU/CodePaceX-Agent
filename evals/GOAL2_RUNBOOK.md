# Goal 2：真实实验、正式 Benchmark 与证据闭环运行手册

本手册冻结 Goal 2 的实验顺序、已完成的证据和不可变边界，不伪造结果。正式 MCP 已完成 eager/deferred 各 150 个 terminal Trial；其 299 Usage-complete、149 Usage/Token pair 仅保留为会计与配对事实，不能在冻结 source trace 缺失时表述为成功 MCP 执行证据，见 [`GOAL2_MCP_EXECUTION_EVIDENCE_ERRATUM.md`](GOAL2_MCP_EXECUTION_EVIDENCE_ERRATUM.md)。正式 Permission 已完成四策略各 50 个 terminal Trial。Retention 以一个已保守结算、最终 Provider Usage unknown 的 `summary_only` infrastructure-error session 作可审计 partial 收口。Multi-Agent 历史零模型 `NO-GO` 因 runtime-log scope mismatch 且冻结 control Artifact 缺失而为 `evidence_insufficient`；没有正式 Provider Trial，见 [`GOAL2_MULTI_AGENT_SCOPE_EVIDENCE_ERRATUM.md`](GOAL2_MULTI_AGENT_SCOPE_EVIDENCE_ERRATUM.md)。当前 ledger 为 CNY `92.579316` spent、`1225` request charges、`1114` settlements、`active_reservation=null`；CNY `90` safety reserve 未动用。Formal SWE 固定为 `infrastructure-blocked`，三次 8 小时长会话固定为 deferred。完整指标、Artifact hash 和不可声明范围见 [`GOAL2_FINAL_REPORT.md`](GOAL2_FINAL_REPORT.md)。

## 1. 冻结身份与不可变边界

- Provider：`bailian-qwen37-max`
- 协议：`openai-compat`
- 模型：`qwen3.7-max-2026-06-08`
- Region：China (Beijing)
- `max_output_tokens=8192`，`max_iterations=50`，SDK retry `0`，fallback `false`
- 默认 Pilot：`codepacex_001_config_bugfix`，1 repetition，1 attempt，串行
- Runtime 变量通过 `ExperimentProfile` 生效；legacy `feature_flags` 仍 fail-closed
- 正式 Artifact 根目录：`evals/.runs/goal2/`，不得提交
- 付费执行必须绑定一个 clean Git commit；任何代码或实验资产变更都会使预算授权失效
- 不读取、显示、记录或推断 API Key 内容，只允许检查 `BAILIAN_API_KEY` 是否存在
- AgentRouter 明确延期，不属于本 Goal 的实验变量

正式实验前记录 `git rev-parse HEAD`、`git rev-parse HEAD^{tree}`、`uname -a`、Python 版本和 Docker 版本。不得从未提交或脏工作区启动付费 Run。

## 2. 非付费验证

```bash
python -m evals.goal2_studies
python -m evals.costing
python -m evals.pilot validate
python -m evals.pilot dry-run --runs-dir evals/.runs/goal2-dry --run-id pilot-dry
python -m evals.mcp_study validate
python -m evals.mcp_study dry-run --runs-dir evals/.runs/goal2-dry --run-prefix mcp-dry
python -m evals.retention_study validate
python -m evals.retention_study dry-run --runs-dir evals/.runs/goal2-dry --run-prefix retention-dry
python -m evals.permission_study validate
python -m evals.permission_study dry-run --runs-dir evals/.runs/goal2-dry --run-prefix permission-dry
python -m evals.multi_agent_study validate
python -m evals.multi_agent_study grader-preflight
python -m evals.multi_agent_study dry-run --runs-dir evals/.runs/goal2-dry --run-prefix multi-dry
python -m evals.long_session_study validate
python -m evals.long_session_study dry-run --runs-dir evals/.runs/goal2-dry --run-prefix long-dry --kind pilot --index 1
python -m evals.hook_study validate
python -m evals.hook_study run --output evals/.runs/goal2-dry/hook-study.json
python -m evals.claims validate
python -m evals.secret_scan
python -m pytest -q
git diff --check
```

Dry-run、mock、fixture 和合成数据均不得进入真实效果 Claim。Hook 的 100 个用例是确定性安全实验，不调用模型；Retention 的会话负载是明确标注的 deterministic synthetic filler，但 canary 提取、压缩和召回路径必须真实运行。

## 3. 官方 SWE-bench-Live 环境闸门

官方适配冻结在 [`goal2/swe_official_environment.json`](goal2/swe_official_environment.json)：Microsoft `python-only` 分支，commit `ad79b850f15e33992e96f03f6e97f05ddf9aa0be`，dataset `SWE-bench-Live/SWE-bench-Live`，split `lite`，Docker namespace `starryzhang`。

只允许在独立临时 virtualenv 中安装该精确 checkout，不得改变项目共享 `.venv`。安装属于需用户明确批准的依赖操作。批准后使用独立解释器运行 CodePaceX 和官方 evaluator，并执行：

```bash
/private/tmp/codepacex-goal2-swe-venv/bin/python -m evals.swe_inference preflight
```

Preflight 必须同时满足：官方模块可用、安装 checkout 的 Git commit 精确匹配、Docker daemon 可用。固定 `starryzhang` namespace 没有本轮 Pilot 实例的公开 arm64 镜像；Apple Silicon 主机因此显式选择官方 x86_64 镜像并由 Docker Desktop 做 amd64 仿真。该选择写入 Run manifest，结果必须标注为 emulated/experimental，不能表述为原生 arm64。随后从官方 `lite` split 的不可变 dataset revision 导出 JSONL，记录 revision 与文件 SHA-256，再冻结清单：

```bash
/private/tmp/codepacex-goal2-swe-venv/bin/python -m evals.swe_inference freeze \
  --dataset-jsonl evals/.runs/goal2-assets/swe-lite.jsonl \
  --dataset-revision REPLACE_WITH_IMMUTABLE_DATASET_REVISION \
  --matrix evals/.runs/goal2-assets/swe_matrix.json
```

冻结器必须产生互不重叠的 3 个 Pilot 和 20 个正式实例；正式分布为 8 个单文件、8 个 2–4 文件、4 个 5+ 文件，每仓库最多 2 题；重复子集为 2/2/1。Gold patch 只用于选题、分桶和冻结校验，绝不进入 Agent prompt。空 patch 直接失败。官方 evaluator 或 Docker 失败属于 infra failure，不得计为 Agent failure。当前 smoke 中，`aiogram__aiogram-1594` 的官方 gold patch 在 x86_64 仿真路径 resolved；`amoffat__sh-744` 因 QEMU 文件描述符语义导致两个 PASS_TO_PASS 失败，单独保留为 evaluator/architecture evidence，不与 Agent 成绩混合。

## 4. 预算闸门

定价快照来自阿里云 Model Studio 官方价格页，2026-07-13 冻结值为输入 CNY 12/M tokens、输出 CNY 36/M tokens；不假设免费额度、折扣或 cache 优惠。价格快照 SHA-256 为 `a09eb6e6955b9fb68d3e011771c948f7a14b7bbca5316a2433cab099d0b643d3`。

下表是冻结前的容量估计，不是待执行清单或承诺支出。MCP 与 Permission 正式矩阵已经结束，Retention/Multi-Agent/SWE/长会话按本手册的最终状态处理；不得由表中旧数量触发重跑。实际计费取决于 Provider request 和 Token；每次下一 **Provider request** 都必须先按该单次请求的最坏上限预约预算。

| 部分 | 数量 | 最小估算 CNY | 预期估算 CNY | 工程硬上限 CNY |
| --- | ---: | ---: | ---: | ---: |
| 最小 Pilot | 1 | 0.04 | 0.86 | 91.55 |
| SWE Pilot | 3 | 0.13 | 2.59 | 274.64 |
| SWE 正式 20 题 | 20 | 0.84 | 17.28 | 1830.91 |
| SWE 重复子集额外 10 Runs | 10 | 0.42 | 8.64 | 915.46 |
| MCP | 300 | 12.60 | 259.20 | 27463.68 |
| Retention | 20 | 0.84 | 17.28 | 1830.91 |
| Permission | 200 | 8.40 | 172.80 | 18309.12 |
| Multi-Agent | 50 | 2.10 | 43.20 | 4577.28 |
| Hook | 0 paid | 0 | 0 | 0 |
| 2h 长会话 Pilot | 8 cycles | 0.34 | 3.46 | 146.47 |
| 3×8h 长会话 | 96 cycles | 4.03 | 41.47 | 1757.68 |
| 总计 | 608 Runs / 104 long cycles | 29.74 | 566.78 | 57197.69 |

硬上限假设普通 trial 最多 50 requests、每 request 128K input + 8192 output；它只用于预注册成本上界和类别 forecast，**不是**一次性 Trial reservation。用户必须给出明确总预算 CNY。schema v2 授权 JSON 需包含 `authorized_total_cny`、`stage_limits_cny`（本 Goal 固定为 A=100、B=400、C=600）、当前 40 位 HEAD、上述 pricing hash、UTC 授权时间和 `authorized_by: user`。每次 execute 必须显式传 `--budget-stage A|B|C`；每条真实 Provider request 在发出前分别检查全局、阶段、类别和 15% 安全余量，并获得唯一 reservation ID。收到真实 Provider Usage 后立即单独 settlement 并释放未使用预留；同一 Trial 的请求只能串行进行。Usage 缺失、timeout 或进程崩溃时 active reservation 保留并停止后续付费调用；预留失败写入 `budget_blocked`，不伪装成能力失败。预算 ledger、authorization 和 lock 文件只能放在被忽略的本地 Artifact 目录。

## 5. 真实执行顺序

所有命令都必须从同一个 clean frozen checkout 运行，共用以下三个参数：

```text
--pricing-snapshot evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json
--budget-authorization evals/.runs/goal2-control/budget-authorization.json
--budget-ledger evals/.runs/goal2-control/budget-ledger.json
--budget-stage A|B|C
```

### 5.1 最小 Pilot

```bash
python -m evals.pilot execute --confirm-paid-run \
  --runs-dir evals/.runs/goal2 --run-id pilot-minimum \
  --pricing-snapshot evals/goal2/pricing_bailian_qwen37_max_2026-07-13.json \
  --budget-authorization evals/.runs/goal2-control/budget-authorization.json \
  --budget-ledger evals/.runs/goal2-control/budget-ledger.json \
  --budget-stage A
```

必须验证 `model_called=true`、`network_called=true`、Runtime hash、Provider Usage、terminal Trial、五个核心 Artifact、Claims 可读性和 Secret Scanner。失败只诊断一次，不修改 task、fixture、grader 或成功标准来提高通过率。

### 5.2 SWE-bench-Live

`swe-pilot-v2` 的三条真实 prediction 已由固定官方 evaluator recovery 评测为 0/3 resolved。完整 formal 集合的 empty-equivalent control 随后发现非任务 PASS_TO_PASS 失败，因此当前 Apple-Silicon/x86_64-emulated 路径不能支持完整 20 题 Claim。SWE 在本 Goal 固定为 `infrastructure-blocked`：不得运行 `swe-formal`、`swe-repeat-*`、gold control、有效子集或正式 resolved-rate Claim。

### 5.3 正式矩阵的最终状态

Stage A/B 最小 Pilot 只证明 runner、唯一变量、Usage 与 Artifact 链；不与正式 Claim 混样。正式矩阵已经冻结：MCP 不得重跑任何 terminal Trial，尤其不得重跑 `mcp_one_08/1`；Permission 不得重跑任何四策略 terminal Trial；Retention 不得重跑 `retention-session-01`。Multi-Agent 历史 gate 的 scope 证据为 `evidence_insufficient`，且没有正式 Provider Trial；不得以修复后的零模型 preflight 触发正式 Trial。Hook 单独保留为零模型、零网络的确定性研究，不与 Provider 实验混合。

`long-pilot-1` 是唯一长会话真实证据：2 小时墙钟、8/8 cycles、planned restart、recovery、4 个 hash-chained checkpoints、CNY `0.342108` 估算成本。`long-formal-1/2/3` 明确延期到 follow-up Goal；不得将该 Pilot 描述为 8 小时正式验证。

## 6. Claims、报告和简历边界

对已冻结 Artifact 重新生成和复核 Claim 时使用：

```bash
python -m evals.goal2_claims generate \
  --runs-dir evals/.runs/goal2 \
  --exclude-multi \
  --cohort-index evals/.runs/goal2-control/mcp-formal-cohort-index.json \
  --output evals/.runs/goal2-control/claims.goal2.yaml
python -m evals.claims validate \
  --claims evals/.runs/goal2-control/claims.goal2.yaml
python -m evals.goal2_claims compile \
  --claims evals/.runs/goal2-control/claims.goal2.yaml \
  --runs-dir evals/.runs/goal2 \
  --cohort-index evals/.runs/goal2-control/mcp-formal-cohort-index.json \
  --output evals/.runs/goal2-control/claims.goal2.compiled.yaml
```

普通 Claims 编译器继续按完整 Run fail-closed。MCP 使用单独的 hash-pinned Trial cohort：编译器逐条复核终态、Usage、结算和 provenance；`mcp_one_08/1` 只从 Usage/Token pair 排除，而不从总尝试或成本删除。样本数必须精确相等，A/B 必须精确配对，批准的 Runtime 差异仍写入 evidence summary。任何 `insufficient-data`、infra failure、被筛掉的失败 Run、dry-run、mock、fixture-only 或 synthetic schema 都不得写成真实效果。

若某个 Provider request 已成功落盘 reservation、但无法恢复 Provider Usage、request ID 或可审计账单证据，则不得按零成本取消。记录 `conservative_reserved_amount` 审计结算：全额计入原 reservation、Token 字段保持 unknown、Trial 保持 `infrastructure_error`，并从 Token matched-pair 指标中排除；该金额是预算保守计提，不是 Provider 实际账单。该结算同样扣减全局与类别预算，且只允许一次。

当前 Claims 状态：

| 证据 | 当前状态 | 可发布条件 |
| --- | --- | --- |
| 最小真实 Pilot | 已完成；旧/新 Pilot 仅作诊断或最小证据 | 不与新 formal baseline 混样 |
| SWE-bench-Live | Pilot 0/3；formal `infrastructure-blocked` | 本 Goal 不生成正式 SWE Claim |
| MCP Token / Schema | formal 300/300 terminal；299 Usage complete、149 Usage/Token pairs | 计费与配对记录保留；由于冻结 source trace 本地缺失，MCP 成功执行与能力 Claim 为 `evidence_insufficient`；schema bytes 仍因原始 runtime telemetry 缺失为 `insufficient-data` |
| Retention | formal auditable partial | `summary_only` session-01 不重跑；不声明 profile comparison |
| Permission | formal 四策略各 50 terminal | 结果限 Darwin arm64；含 Usage unknown infrastructure error 的 sandbox arm 保持其 Claim 边界 |
| Multi-Agent | historical zero-model gate `evidence_insufficient` | 冻结 control Artifact 缺失且发现 runtime-log scope mismatch；不运行 Provider Trial，生成 `insufficient-data` Claim |
| Hook | 已完成 100/100，正式本地 Artifact 绑定 clean HEAD | 零模型、零网络、零拒绝副作用；最终报告引用 Artifact hash |
| 长会话 | 2h Pilot 已完成；3×8h formal `insufficient-data` / deferred | follow-up Goal，不阻塞本轮其他指标 |

简历候选 bullet 只能引用单条 `verified` Claim，并带上绝对值、样本量、commit 与限制；`insufficient-data` 不得写成负面或正面效果。不得声称未测得的 SWE 成绩、长会话稳定性、多 Agent 比较或显著性。

面试解释顺序：先说明唯一变量与冻结 commit，再说明失败分类和预算 reservation，然后展示原始 Run → 注册计算器 → compiled Claim 的证据链，最后主动声明 controlled corpus、Python-only lite、arm64 experimental、synthetic retention load 和样本量限制。

## 7. 停止条件

遇到以下任一情况立即停止相应部分，不修改 Benchmark task、fixture、grader、gold patch 或历史 Run：预算不足；Provider usage 缺失且无法对账；API Key 不存在；官方 evaluator commit 不匹配；Docker/容器失败；未解释的全量测试失败；长会话 checkpoint 链损坏；需要系统级依赖、push、PR、merge 或读取密钥。SWE infra 阻塞不阻止其他已授权实验，但不得改记为 Agent failure。
