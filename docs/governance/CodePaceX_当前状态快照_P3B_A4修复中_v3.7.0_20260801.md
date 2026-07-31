# CodePaceX 当前状态快照：P3-B A4 修复中

> 状态：A4 已终态失败并消耗；阶段一修复的本地零 Provider 验证完成，尚未创建或执行 A5 paid identity。

当前 formal main 为 `adcf8483e77bd45d8aee3e16cbc5d086c7d05050`，PR #83 保持 OPEN 且不得修改。A4 的唯一 Run `30655334724` 在第一个 V2 task-run 的子 Agent 预算 gate 处失败，原始消息为 `budget authorization is not bound to current HEAD`。随后的 raw Artifact 检查抛出掩盖性错误；两者已在集中式修复中分别处理。

本地定向验证确认：真实 P3-B coordinator 到子 Agent request-budget bridge 的 8 条冻结路径都继承明确的 descendant commit-binding 合同，并在 Provider transport 前停止。Provider requests / Usage / charge 为 `0 / 0 / CNY 0`，Secret value read=false，active_reservation=null，paid job 未触发。

下一步仅为：完整测试、readiness、集中 PR、CI、locked-head review、普通 merge 和 post-merge 门。P4 继续阻塞。
