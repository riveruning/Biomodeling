# 真实论文复现评测：首个开发案例

日期：2026-10-17。状态：离线评测基础、数值对照及两组真实 LLM 试验完成；参考答案待人工审核，未实现完整论文复现。

这是 v0.1 之后的一个开发切片，不代表整个 v0.2 已交付，也不是已建立的论文金标准集。

## 案例与材料

Gonze D, Abou-Jaoudé W (2013), *The Goodwin Model: Behind the Hill Function*，
[PLOS 原文](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0069573)，DOI 10.1371/journal.pone.0069573。
文章为开放获取 CC BY；原始许可说明保存在出版社 XML 中。

本例只取 Results / Limit-cycle Oscillations in the Goodwin Model，包括 Figure 2 图注。
出版社 XML、Europe PMC XML、HTML、8 个方程/参数 TIFF 原图及 PNG 查看副本已保存。
原文、转换文本、辅助转录和参考答案的 SHA-256 固定在 `benchmarks/manifests/literature-pilot.json`。
PNG 是格式转换副本；未使用自动 OCR。辅助文本由 Codex 查看图片后转录，**尚未由独立的人类审核**。

## 两条输入轨道

| 轨道 | Agent 可见材料 | 评测范围 |
|---|---|---|
| raw | 指定 JATS 节的普通文本；图片公式保留显式占位符 | 方程/参数图片不可访问时能否停下、请求补充材料 |
| assisted | 对方程 (1)–(3) 与 Figure 2B 参数图片的辅助转录 | 8 个参数值、3 条方程及证据；3 个初值应保持未知 |

两条轨道都是已读过的开发案例，不是盲测。原文的方程存在于图片中，raw 的 null 表示“对这次文本输入不可访问”，不表示论文完全没有方程。
初值未在选定节与图注中报告；没有声称已穷尽全文、补充材料和作者代码。
物理单位、协议完整性与任意符号重命名不在本次逐项评分范围。任务明确要求使用作者符号；Ki 是 K_i 的 ASCII 表示。

## 评分与防止误报

`src/biomodel_agent/literature_scoring.py` 分开报告：

- 数值是否匹配；表达式是否在冻结探测点上一致。
- 引用是否来自允许材料、是否唯一出现、是否覆盖该目标的参考锚点。
- 有依据的正确提取数量，以及未知信息的留空数量。
- 缺失信息被无依据补全的数量、额外目标和逐项失败。

表达式检查不是符号等价证明。引用锚点是待人审的开发标注，不能代替语义审核；所有结果固定为 `pending_human_review`。
仅返回空答案也会留下“可提取项目全部缺失”的记录，不会被计算成提取成功。
raw 的全留空只是一种安全停止行为，不能估计文献提取成功率。

离线正向控制直接从参考答案生成，**只验证评分器，不算固定工作流/Agent 的能力成绩**。
辅助输入正向控制应有 11 项有依据提取、3 项正确留空；破坏一条方程、一处引用并编造一个初值后，应降为 9 项有依据提取，并报告 1 项无依据补全。

## 数值对照

辅助转录模型通过 SBML/RoadRunner 执行；独立手写 SciPy DOP853 方程提供对照，不调用产品的表达式解析器。
固定初值 X=0.1、Y=0.2、Z=0.3 是**分析者选择的探索条件**，不输入文献提取 Agent，也不标记为论文初值。
时间窗 0–240，481 个采样点，固定状态尺度 10，最大缩放误差阈值 1e-3。

实际最大缩放误差 `1.2165e-5`，数值对照通过；严格科学状态仍为 `not_verified`。
轨迹后段的粗采样峰间隔均值约 42.75 a.u.，只作探索性诊断，不是原图数字化误差或极限环证明。
当前未实现原 Figure 2B 轨迹的严格复现。

## 运行

离线运行不需要网络、凭证或 LLM：

```bash
.venv/bin/python benchmarks/development/evaluate_literature.py \
  --output artifacts/my-literature-offline
```

运行器要求新目录或空目录，防止混入旧结果；先核验所有冻结输入，再运行评分控制和数值对照。
可以重建 raw 文本：

```bash
.venv/bin/python benchmarks/development/prepare_literature.py
```

真实 Agent 运行需要明确配置服务、模型，以及已经设置的专用环境变量：

```bash
.venv/bin/python benchmarks/development/evaluate_literature.py \
  --output artifacts/my-literature-live \
  --base-url https://YOUR_PROVIDER/v1 --live-model YOUR_MODEL \
  --api-key-env BIOMODEL_API_KEY
```

每条轨道最多 8 次调用、2 次修复、单次请求超时 90 秒，返回 token 上限 4000。
运行器只读取指定环境变量，不自行读取 DSH 配置或凭证文件。参考答案、独立参考曲线和探索初值不进入 Agent 输入。
评分保存最后一次提案，包括被工具拒绝的提案，并单列提案状态；因此提取分数不等于工具已接受或科学已通过。
未提交草稿的请求人工帮助会保留 `not_submitted`，不能假装已经完成提取。

## 验收与后续

DSH 按 `tasks/v02-literature-scoring.json` 交付评分器和测试，Codex 独立审查、修正并执行验证。
修复包括引用判定中的未定义变量、留空计入有依据提取、伪重复引用测试、参考表达式与探测点验证，以及同来源的多个引用锚点被覆盖。
新增测试覆盖来源哈希变化、图像公式缺口、人工初值与 Agent 输入隔离、被拒提案与错误来源等边界。

最终运行：**214 项 Python 测试全部通过，18 项开发工作流测试全部通过**。
验收机器记录在 `artifacts/v0.2/acceptance.json`，JUnit 记录在 `artifacts/v0.2/pytest.xml`；
离线数值与评分产物在 `artifacts/v0.2/literature-offline-verified/`。
脚本化 Agent 集成测试确认：11 项方程/参数及来源可以提交，3 个初值保持 null 时进入 `awaiting_input`，不创建可执行模型或模拟运行。该测试不算真实 LLM 能力成绩。

初次从 DSH 凭证文件读取密钥用于真实评测的操作被自动审批拒绝；该历史记录在 `artifacts/v0.2/live-evaluation-blocked.json`。用户随后明确允许复用现有 DSH/USTC 凭证及配额，已在该授权下完成两组调用。密钥仅在调用进程环境中使用，没有保存到评测文件或输出中。

## 首次真实调用结果

模型 qwen3.8-chat；共 6 次调用，服务报告总 token 19,407。完整结果在 `artifacts/v0.2/literature-live-20261017/report.md`，逐项分数、提案、调用记录和复核记录均保留在同目录。

| 轨道 | 结果 | 限制 |
|---|---|---|
| raw | 2 次调用，主动询问不可访问的方程、参数与初值；未提交草稿或运行模拟 | 只验证本次正确停止，不能当成提取成功 |
| assisted | 4 次调用，最后提案的 8 个参数、3 条方程及引用匹配，三个初值保持 null | 发生 2 次格式修复，最终还有 profile 阻断；未创建可执行模型或运行模拟 |

辅助组第一次把单位设为 null，第二次区室大小仍为 null，均被类型检查拒绝。第三次进入 awaiting_input；同时保留了三个 nonnegative=true 与 rate-rule profile 不兼容的阻断项。因此不能将状态解读为“只差初值”，也不能将 11/11 的核心内容分数解读为完整模型通过。

下一步：明确提示词中不同字段的 unknown/null 规则、区室默认值的适用条件和 rate-rule 非负性表示；由研究者审核辅助转录及参考答案，再接入人审与续跑，之后扩展至 5–10 篇冻结评测案例。仍无通用助手/固定流程/Agent 的同预算优势结论。
