# CodePaceX 后续整体工作方案：P3-B-R4 后

> 版本：v2.3.0
> 状态：当前有效，待 R4 PR 审核

## 当前工作顺序

1. 审核并合并单一 P3-B-R4 输入合同 PR；关闭已被吸收的 PR #81，不合并它。
2. 在合并后的 main 做只读 post-merge 核验：freeze/readiness identity、workflow/executor/gate content SHA、paid job skipped、Secret scope 与 8-run zero-provider evidence。
3. 只有用户另行明确授权时，才允许准备新的 paid input identity；先走同构 validate-only，再由用户决定是否进行唯一 paid dispatch。
4. P3-B 结果审计后仍需独立决定是否进入 P4；R4 不启动 P4。

## 禁止项

不得 retry、rerun、continuation、fallback、付费 workflow dispatch、Provider request 或复用 A2 的 acknowledgement/token/internal run identity。历史 Run 30609517826、Artifact 8784886341 和 digest sha256:7d6bfa6d48562642a3b49bcf3ac3eff00319ead4ad33497fb2ba8e3973a200e9 保持不可变。
