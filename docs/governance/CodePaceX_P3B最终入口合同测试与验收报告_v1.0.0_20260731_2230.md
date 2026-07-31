# CodePaceX P3-B 最终入口合同：测试与验收报告

> 版本：v1.0.0
> 状态：PR CI 与 post-merge 核验待完成
> 语言：中文

## 本地定向验收

定向套件覆盖 generator 的 test-only 身份、唯一 acknowledgement prefix、token/run ID 正则、稳定 canonical bytes、两次生成不重复、raw byte mutation、重序列化、unknown/duplicate/schema 拒绝、validate-only SHA、paid executor SHA、失败 preflight Artifact、workflow paid skipped、4/4 instance 与 8/8 production adapter 路径。

本地结果将在 PR head 固定后重跑。所有这些测试只使用 recording Provider 或硬禁用的 Provider 初始化边界：Secret value read=false，Provider requests / Usage / charge=`0 / 0 / CNY 0`，active_reservation=null。

## 合并门

只有定向测试、完整测试、Ubuntu/macOS CI 与 P3-B zero-provider readiness 全部通过，才允许普通 locked-head merge。post-merge 使用 generator 的 test-only bundle 做 validate-only，并在 paid executor 的 explicit test-only hard-disabled seam 验证两处 `final_input_bundle_sha256` 完全相同；paid job 必须 skipped。
