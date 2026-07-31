# CodePaceX 后续整体工作方案：P3-B 最终入口合同后

> 版本：v2.4.0
> 状态：当前有效，待集中式 PR 合并与 post-merge 核验
> 语言：中文

1. 审核、锁定并普通合并本集中式 PR；PR #83 保持 OPEN，不合并、不修改 Head。
2. 在新 main 运行只读 identities、main CI、test-only generator validate-only 和 zero-provider readiness 核验。
3. 合并后仍不得 paid dispatch。只有用户后续提供新的明确付费授权，才可由 generator 创建一次新的 authorized bundle；先对该原始 bytes 做 validate-only，再由单独授权决定是否 dispatch。
4. 完成真实 P3-B terminal 审计后，是否进入 P4 仍须独立用户决定。

禁止 retry、rerun、continuation、fallback、Provider 调用、P4、Tag、Release，以及复用任何 A2/A3 identity。
