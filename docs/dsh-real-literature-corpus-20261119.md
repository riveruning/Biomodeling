# DSH 真实复杂文献任务包

材料位于 `/home/riveruning/biologymodel_agent/artifacts/development/real-literature-corpus-20261119/`。

26 篇已有本地全文/官方派生文本，24 篇新增；28 篇候选跨 15 个文献族。18 篇有原始 HTML，另 8 篇只有可能缺公式/图像的官方 BioC 派生文本；6 篇有主文 PDF，18 个附件已保存。R08/R12 全文获取受阻，保留失败记录。所有案例尚未运行，补充材料与数学完整性尚未审计，不是独立评测集。

交给 DSH 时先读 `DSH-PROMPT.md`，再读 `README.md`、`manifest.json`、`queue.json` 与 `papers/Rxx/task-card.md`。`validation.json` 记录文件指纹和 PDF 解析检查；`coverage-receipt.json` 明确保留未覆盖案例。首批建议审计 R02/R05/R26，再用 R09/R03/R14 检查周期刺激、事件分裂与随机反应合同。

原文与附件只保留在被忽略的 artifacts 中，未公开发布。ARIS 核验脚本不可用，故 helper 状态为 unverified；已完成的原站元数据/PMC 标识核验另列，不混称自动验证成功。
