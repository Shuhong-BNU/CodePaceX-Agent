# CodePaceX Goal 4 → Capability V3 结果与机制激活审计

> 日期：2026-07-30
> 证据：V3 head Artifact `8744897594` 与 tail Artifact `8749299095` 的只读解析。
> 口径：two-run infrastructure-recovery completion；前四题取 head run，后十六题取 tail run。

## 1. 结果总览

| 指标 | Goal 4 | V3 |
|---|---:|---:|
| 注册任务 | 20 | 20 |
| 可评分 | 20 | 19 |
| Resolved | 4 | 5 |
| Unresolved / request-ceiling scorable | 16 | 14 |
| Infrastructure error | 0 | 1 |
| Provider requests | 537 | 429 |
| 账本/已核验成本 | CNY 165.044424 verified actual | CNY 132.932760 total ledger |

观察到：
- `deepset-ai__haystack-8489`：unresolved → resolved。
- Goal 4 的四个 resolved 在 V3 中全部保持 resolved。
- `bridgecrewio__checkov-6893`：历史 unresolved → 本轮 infrastructure_error。
- 没有 resolved → unresolved 的任务级终态变化。

## 2. V3 机制真实激活情况

| 机制信号 | 20 题 Artifact 统计 |
|---|---:|
| Evidence target symbols | 0 |
| Direct callers | 0 |
| Implementations | 0 |
| Tests / fixtures evidence | 0 |
| Defaults / config evidence | 0 |
| History evidence | 0 |
| Hypotheses | 0 |
| Contract matrices | 0 |
| Differential validation records | 0 |
| Candidate snapshots | 84 |
| C2/C3 candidates | 0 |
| Impact-test recommendations | 186 |
| 其中 venv/site-packages 路径 | 112 (60.2%) |

结论：真实 paid run 中主要活跃的是 Candidate snapshot/restore、budget finalization、Oracle risk 与 observer telemetry。设计中的证据恢复、有界假设、契约矩阵、差分验证并未形成有效运行状态；ImpactSlice 还受到 venv/site-packages 污染。

## 3. 逐题结果

| instance                          | goal4_terminal   | v3_terminal             | resolved   |   provider_requests |   cost_cny | candidate_status   | evaluator_status   | failure_classification                                                                                                                           | transition          |
|:----------------------------------|:-----------------|:------------------------|:-----------|--------------------:|-----------:|:-------------------|:-------------------|:-------------------------------------------------------------------------------------------------------------------------------------------------|:--------------------|
| aws-cloudformation__cfn-lint-3749 | unresolved       | unresolved              | False      |                  32 |   8.36122  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| aws-cloudformation__cfn-lint-3764 | unresolved       | unresolved              | False      |                  14 |   1.53188  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| beetbox__beets-5457               | unresolved       | request_ceiling_reached | False      |                  40 |  11.3057   | exported_nonempty  | completed          | request_ceiling_reached                                                                                                                          | unchanged           |
| beetbox__beets-5495               | resolved         | resolved                | True       |                  12 |   1.03518  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| deepset-ai__haystack-8489         | unresolved       | resolved                | True       |                  26 |   7.53851  | exported_nonempty  | completed          |                                                                                                                                                  | unresolved→resolved |
| beancount__beancount-931          | resolved         | resolved                | True       |                  21 |   2.53146  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| beeware__briefcase-2075           | resolved         | resolved                | True       |                  14 |   1.10078  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| beeware__briefcase-2085           | resolved         | resolved                | True       |                   9 |   1.79412  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| bridgecrewio__checkov-6893        | unresolved       | infrastructure_error    | False      |                  10 |   2.70527  | not_exported       | not_run            | openai.APITimeoutError/httpx.ConnectTimeout/httpcore.ConnectTimeout/builtins.TimeoutError/asyncio.exceptions.CancelledError/ssl.SSLWantReadError | unresolved→infra    |
| bridgecrewio__checkov-6895        | unresolved       | unresolved              | False      |                  11 |   1.11673  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| conan-io__conan-17092             | unresolved       | unresolved              | False      |                  15 |   5.61937  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| conan-io__conan-17102             | unresolved       | request_ceiling_reached | False      |                  40 |  14.8345   | exported_nonempty  | completed          | request_ceiling_reached                                                                                                                          | unchanged           |
| cyclotruc__gitingest-115          | unresolved       | unresolved              | False      |                  14 |   1.84504  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| cyclotruc__gitingest-134          | unresolved       | unresolved              | False      |                  21 |   4.79917  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| deepset-ai__haystack-8525         | unresolved       | unresolved              | False      |                  15 |   2.9687   | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| delgan__loguru-1297               | unresolved       | unresolved              | False      |                   7 |   0.640572 | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| delgan__loguru-1306               | unresolved       | unresolved              | False      |                  10 |   1.29835  | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| dynaconf__dynaconf-1225           | unresolved       | request_ceiling_reached | False      |                  40 |  32.1608   | exported_nonempty  | completed          | request_ceiling_reached                                                                                                                          | unchanged           |
| dynaconf__dynaconf-1249           | unresolved       | unresolved              | False      |                  38 |  16.3296   | exported_nonempty  | completed          |                                                                                                                                                  | unchanged           |
| instructlab__instructlab-2540     | unresolved       | request_ceiling_reached | False      |                  40 |  11.5848   | exported_nonempty  | completed          | request_ceiling_reached                                                                                                                          | unchanged           |

## 4. 证据边界

- Goal 4 与 V3 不是同一时刻、同一运行环境下的随机配对实验，不能把净增 1 题全部归因于 V3。
- 相同 20 题已被用于失败分析和机制设计，不是新鲜 holdout。
- `haystack-8489` 的 V3 patch 比 Goal 4 patch更谨慎，但 Artifact 不能证明改进来自 V3 evidence/hypothesis/matrix，因为这些机制在 telemetry 中没有激活。
- V3 的 19/20 scorable 与 Goal 4 的 20/20 scorable应同时报告。
