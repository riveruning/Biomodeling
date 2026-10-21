# T07a：检索材料与标注审核准备

本轮按用户选择“先完成审核包，暂不加入新论文”推进 T07a。已交付审核工具、可读材料包和分层评分协议；**领域审核尚未完成，T07 整体未结项**。检索算法、默认方法、产品源码和既有测试保持不变，没有引入 embedding。

## 审核材料

从已见的 `benchmarks/inputs/retrieval-development/cases.json` 生成 [审核入口 REVIEW.md](../artifacts/audit-t07-retrieval-review-20261019/review-pack/REVIEW.md)、[冻结材料与标注](../artifacts/audit-t07-retrieval-review-20261019/review-pack/packet.json)、[材料清单](../artifacts/audit-t07-retrieval-review-20261019/review-pack/inventory.json)及[审核表](../artifacts/audit-t07-retrieval-review-20261019/review-pack/review-forms.json)。

共 18 个合成开发案例、21 份文档投影、21 个必需候选片段，覆盖参数、初值、单位、公式、补充材料、条件限定、矛盾候选、显式缺失、无匹配和词汇差异。真实论文数量为 **0**，18 个案例全部为 pending；它们不能称为独立论文样本或未见留出集。

旧材料共享开发模板，本包保守归入一个同源家庭。正文、补充、澄清和更正必须使用相同的家庭/划分；校验器检查跨划分的家庭冲突和标准化后完全相同的非空页。它依赖准确的人工分组元数据，未实现近重复检测、DOI/预印本实体消歧或自动证明材料独立性。已见合成材料不能通过改写 exposure 字段进入对照划分。

每份来源保留原页字符串和哈希，每条标注带 source/page/start/end/quote、候选证据角色和回答可能性。偏移指 Python Unicode 字符，不是 UTF-8 字节。可读的逐页副本位于审核包的 sources/；`packet.json` 是评分文本权威。

一个需要优先人工复核的旧标签是 `clarification_scope`：条件 A 和 B 的初值分别为 2 与 5 nmol，可能相容。旧开发分类为 conflict，本轮显式保留“矛盾候选待审”的提示，没有把它宣称为经核实的科学矛盾。其他候选标注同样需要核对目标、条件、单位和补充材料作用域。

## 审核流程和有效范围

审核表仅预填内容哈希，不会自动填写审核人、时间、备注或勾选检查项。实际审核人需逐案检查 source_span、target_scope、answerability 和 paper_grouping，填写带时区时间、备注，以及 approved / changes_requested / excluded 决定；排除也需要记录原因。

正式对照要求所有案例均有 approved 或有理由的 excluded 决定，且至少保留一个 approved 案例。一个 approved 字段或部分审核不足以通过。审批记录需有与标注作者不同的归属姓名/标识及当前内容摘要；修改原文、引文、查询、角色、分组、作者或预算会使旧审核失效。

这是完整性与失效检查，不是身份认证、领域资质认证或独立性证明。即便本开发包经人工审核，usage 仍为 development_only，semantic_support 仍为 unknown。

本轮实际执行了 pending 包的审核门检查，结果为 [refused](../artifacts/audit-t07-retrieval-review-20261019/gate-refusal.log)，在写来源、调用检索或创建正式结果目录之前停止。测试中的“审核人 A/B”均为显式合成负/正控制，没有写入实际审核包。

## 指标与输入隔离

新协议分开报告：

- 引用位置：返回字符串是否精确等于授权源页的 start/end 切片；外部来源、错位、重复结果和计数错误单独记失败。
- 完整证据覆盖：必需候选片段是否被某一结果完整包含。只重叠同一个数值不能算完整声明。
- 相关与干扰：与非干扰标注有交集的结果、没有必需证据交集的结果，以及未标注上下文比例。未标注不等于无科研价值。
- 矛盾与缺口：分别检查两侧候选是否均被覆盖、明确“未报告”的陈述是否被检索。无匹配案例单列，不计作空分母下的完美准确率。
- 上下文和耗时：片段字符、紧凑返回 JSON 字符、重复上下文比例及检索调用耗时。字符不是 token 或费用；计时不含解析、人工审核、来源导入、评分或 LLM，固定调用顺序与缓存噪声未受控。

检索只接收来源页投影、查询、方法和预算。投影包含不编码标签的来源标识哈希，以区分正文与补充材料即使文本相同的情况；标注、答案类型、作者和审核备注不会进入排序输入。来源白名单和现有检索扫描/输出预算继续有效。

检索错误与非法引用保留在分母中，不通过丢弃失败案例提高覆盖率。每种方法默认重复 3 次并检查结果一致；报告同时按类别、同源家庭和审核状态分组。科学语义支持始终另列 unknown。

## 重跑与提交审核表

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  benchmarks/development/retrieval_review.py prepare --output /tmp/retrieval-review-pack

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  benchmarks/development/retrieval_review.py check \
  --packet /tmp/retrieval-review-pack/packet.json

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  benchmarks/development/retrieval_review.py evaluate \
  --packet /tmp/retrieval-review-pack/packet.json --mode development \
  --output /tmp/retrieval-development-run
```

输出目录须不存在。实际人工审核完成后，可给 check/evaluate 传 `--reviews 已填写的审核表.json`；正式对照使用 `--mode reviewed`。程序先检查审核表对应的整体内容哈希，再检查逐案哈希和记录，不回写候选材料。输出保存生效材料、审核记录、完整检索结果、分层指标和评测/检索源码哈希。

原 `evaluate_retrieval.py` 与旧材料保持原样，用于历史开发结果回算；新审核协议的门检查在 `retrieval_review.py` 中执行。

## 实测与下一步

原 18 例开发结果已重新计算。新协议仍得到 lexical_v1 完整覆盖 **10/21**、passage_v2 **19/21**；片段字符 **7151 / 2191**，返回 JSON 字符 **12266 / 8830**。这些与旧基线一致，本轮没有检索质量提升结论。

新协议把完整满足的正例记为 6/17 与 15/17，另列 1 个无匹配负例；因此与旧报告含负例的 7/18、16/18 是不同口径。三组候选冲突的完整覆盖为 2/3 与 3/3，仍包含待审的条件作用域标签；“未报告”陈述覆盖为 0/1 与 1/1。长段边界与词汇不一致两个开发失败继续保留。

实际结果见 [开发诊断](../artifacts/audit-t07-retrieval-review-20261019/development-comparison-final/results.json)，全部标为 pending_domain_review。归档了错误来源、错误偏移、裁剪声明、重复标注、单侧冲突、审核失效、分组泄漏、非法预算和错误分母等检查。另对源页额外字段夹带审核标注建立先失败后修复的负例，源页仅允许 number/text。

最终定向 **115 项通过**，完整回归 **1075 项通过**，耗时 200.38 秒；新增 45 项检查。产品源码和原有测试保持不变，T06 产品实现指纹一致，原开发评测器及材料字节未修改。完整验证及源码差异见 [T07a 工程验收](../artifacts/audit-t07-retrieval-review-20261019/acceptance.json)。

下一步 T07b 需要实际领域审核、处理 changes_requested，并在授权后准备逐论文分组的材料。审核完成前保持现有检索默认设置；是否加入 embedding 或重排应依据之后的实测。没有读取原独立评测集或 c13/c18 材料，没有外部模型调用、新论文下载或依赖安装。
