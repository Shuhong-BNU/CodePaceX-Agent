# Goal 2：真实实验、正式 Benchmark 与证据闭环运行手册

本手册冻结 Goal 2 的实验顺序和证据边界，不伪造结果。当前已完成真实 Stage A/B 最小 Pilot、3 题 SWE Pilot prediction 与零模型 evaluator recovery、Hook 100/100，以及 `long-pilot-1` 的真实 2 小时 8/8 cycles。累计按实测 Token 与冻结价格的估算成本为 CNY `19.236348`。SWE recovery 是 0/3 resolved；其中 aiogram/arviz 有 patch-contract miss，amoffat 与 gold 同受 x86_64-emulated PASS_TO_PASS 干扰。随后 formal empty-equivalent control 已使完整 20 题 SWE 标记为 `infrastructure-blocked`。不运行正式 SWE、重复子集或有效子集；三次 8 小时长会话延期到 follow-up Goal。AgentRouter 仍不在本 Goal 范围。

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

计划共有 608 个 top-level paid Runs。估算不是承诺支出：实际计费取决于 Provider request 和 Token；每次下一 trial 都必须先按最坏上限预约预算。

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

硬上限假设普通 trial 50 requests、每 request 128K input + 8192 output；它是 fail-closed reservation ceiling，不是合理消费预测。用户必须给出明确总预算 CNY。schema v2 授权 JSON 需包含 `authorized_total_cny`、`stage_limits_cny`（本 Goal 固定为 A=100、B=400、C=600）、当前 40 位 HEAD、上述 pricing hash、UTC 授权时间和 `authorized_by: user`。每次 execute 必须显式传 `--budget-stage A|B|C`；reservation 同时受阶段累计上限和总上限约束。ledger 逐 Provider request 记录 input/output Token 与估算 CNY，再记录 Trial 聚合 settlement；缺少请求级 Usage 时 fail-closed，不允许用累计字段伪造拆分。预算 ledger、authorization 和 lock 文件只能放在被忽略的本地 Artifact 目录。

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

### 5.3 六项指标

Stage B 先使用 `--scope pilot --budget-stage B` 跑最小、成对子集：MCP 每类 1 个任务、Retention 同一 seed、Permission 1 safe + 1 dangerous、Multi-Agent 同一 cross-file task；每个 arm/mode/strategy 均只重复 1 次。它们只证明 runner、唯一变量、Usage 和 Artifact 链能稳定工作，不生成正式效果 Claim。

Stage C 在新 clean commit、全绿 CI、新 authorization rebind 和 allocation 后才允许运行。每个 runner 必须额外传入 `--budget-allocation`；allocation 按所有保留 Pilot request（包括失败请求）推算 2× 类别上限，保留总授权的 15% 安全余量，且类别不可转移。SWE 与 long-session 类别额度固定为零。默认串行并保持唯一变量；Multi-Agent 内部最多 3 个真实 worker，但必须先通过零模型 `grader-preflight`。固定 Run prefix 不得改变：

```bash
python -m evals.mcp_study execute --scope formal --budget-stage C --confirm-paid-run --runs-dir evals/.runs/goal2 --run-prefix mcp-formal ...
python -m evals.retention_study execute --scope formal --budget-stage C --confirm-paid-run --runs-dir evals/.runs/goal2 --run-prefix retention-formal ...
python -m evals.permission_study execute --scope formal --budget-stage C --confirm-paid-run --runs-dir evals/.runs/goal2 --run-prefix permission-formal ...
python -m evals.multi_agent_study execute --scope formal --budget-stage C --confirm-paid-run --runs-dir evals/.runs/goal2 --run-prefix multi-formal ...
```

省略号仅代表前述完全相同的预算三参数，不代表额外配置。Hook 单独执行到 `evals/.runs/goal2/hook-study.json`，要求 100/100 受控用例、拒绝副作用为 0、`model_called=false`、`network_called=false`。

`long-pilot-1` 是唯一长会话真实证据：2 小时墙钟、8/8 cycles、planned restart、recovery、4 个 hash-chained checkpoints、CNY `0.342108` 估算成本。`long-formal-1/2/3` 明确延期到 follow-up Goal；不得将该 Pilot 描述为 8 小时正式验证。

## 6. Claims、报告和简历边界

正式 Run 完整后自动生成声明文件并重算：

```bash
python -m evals.goal2_claims generate \
  --runs-dir evals/.runs/goal2 \
  --output evals/.runs/goal2-control/claims.goal2.yaml
python -m evals.claims validate \
  --claims evals/.runs/goal2-control/claims.goal2.yaml
python -m evals.claims compile \
  --claims evals/.runs/goal2-control/claims.goal2.yaml \
  --runs-dir evals/.runs/goal2 \
  --output evals/.runs/goal2-control/claims.goal2.compiled.yaml
```

Claims 只接受同一个 frozen commit 的完整 Run；样本数必须精确相等，A/B 必须精确配对，批准的 Runtime 差异仍写入 evidence summary。Hook 使用独立确定性 JSON 作为 Claim 来源，因为它没有 Provider Runtime。任何 `insufficient-data`、infra failure、被筛掉的失败 Run、dry-run、mock、fixture-only 或 synthetic schema 都不得写成真实效果。

当前 Claims 状态：

| 证据 | 当前状态 | 可发布条件 |
| --- | --- | --- |
| 最小真实 Pilot | 已完成；旧/新 Pilot 仅作诊断或最小证据 | 不与新 formal baseline 混样 |
| SWE-bench-Live | Pilot 0/3；formal `infrastructure-blocked` | 本 Goal 不生成正式 SWE Claim |
| MCP Token / Schema | Stage B Pilot 已完成；formal 未运行 | 150 对配对样本、input/cache/output/Schema bytes/success |
| Retention | Stage B Pilot 已完成；formal 未运行 | 两组各 10 会话、每会话 12 canaries、至少 3 次真实压缩 |
| Permission | Stage B Pilot 已完成；formal 未运行 | 四策略各 50 trials、HITL 分布和危险操作拦截率 |
| Multi-Agent | Stage B Pilot 已完成；formal 受 zero-model grader gate 约束 | 两组各 25 trials、并发度/成功率/耗时/Token/成本/冲突 |
| Hook | 已完成 100/100，正式本地 Artifact 绑定 clean HEAD | 零模型、零网络、零拒绝副作用；最终报告引用 Artifact hash |
| 长会话 | 2h Pilot 已完成；3×8h formal `insufficient-data` / deferred | follow-up Goal，不阻塞本轮其他指标 |

简历候选 bullet 必须等 compiled Claims 全部为 `verified` 后，从绝对值、绝对差、相对变化、样本量、commit 和限制自动填充。当前唯一合规表述是：“构建了 fail-closed、预算约束、可恢复且 Artifact 可追溯的 Agent Benchmark 基础设施”；不能声称任何尚未产生的 Token 节省率、SWE 成绩、长会话稳定性或显著性。

面试解释顺序：先说明唯一变量与冻结 commit，再说明失败分类和预算 reservation，然后展示原始 Run → 注册计算器 → compiled Claim 的证据链，最后主动声明 controlled corpus、Python-only lite、arm64 experimental、synthetic retention load 和样本量限制。

## 7. 停止条件

遇到以下任一情况立即停止相应部分，不修改 Benchmark task、fixture、grader、gold patch 或历史 Run：预算不足；Provider usage 缺失且无法对账；API Key 不存在；官方 evaluator commit 不匹配；Docker/容器失败；未解释的全量测试失败；长会话 checkpoint 链损坏；需要系统级依赖、push、PR、merge 或读取密钥。SWE infra 阻塞不阻止其他已授权实验，但不得改记为 Agent failure。
