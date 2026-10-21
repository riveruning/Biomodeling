# T06：成熟本地 PDF 解析器适配

T06 新增 PyMuPDF 后端，通过成熟引擎读取文本块、位置、图片区域和表格，项目只保留有界调用与证据适配。默认 pypdf 路径与已保存来源继续兼容；新能力通过 `--pdf-parser pymupdf` 显式启用。

## 选择与使用

核查过的 [PDF skill](https://github.com/anthropics/skills/tree/main/skills/pdf) 是操作指引，应用的确定性运行时仍需要解析工具。本轮采用 [PyMuPDF 的文本、坐标和表格接口](https://pymupdf.readthedocs.io/en/latest/page.html)，未安装全局 skill、OCR 系统包或依赖云端解析服务。项目可选依赖锁定为 PyMuPDF 1.28.2；许可与实际安装位置保存在 [依赖记录](../artifacts/audit-t06-pdf-adapter-20261019/dependency-install.json)。

```bash
.venv/bin/python -m pip install -r requirements-pdf.lock
.venv/bin/python -m biomodel_agent source-ingest /path/to/paper.pdf \
  --pdf-parser pymupdf --pdf-timeout 30 --store artifacts/my-store
```

`source-ingest` 不调用 LLM，无需模型地址或密钥。输出 source ref、解析器版本、页数、警告和解析产物 ref。原 `agent --source ...` 入口也接受同样的 `--pdf-parser` 与 `--pdf-timeout` 选项；其后续模型调用沿用原有配置与授权边界。

Python 调用可进一步收紧输出限额：

```python
ref = evidence.ingest_source(store, "paper.pdf", pdf_parser="pymupdf",
                             pdf_timeout_s=30,
                             pdf_limits={"max_pages": 50, "max_chars": 500_000})
```

## 来源与位置契约

原 PDF 字节保存在不可变 blob，source 引用该 blob 及独立的 `pdf_extraction` 产物。后者保存版本化适配结果，包括实际引擎版本、页、文本块、原生块编号/坐标、表格单元格、能力声明和限额。文本按引擎的坐标排序结果逐块拼接，必要时仅加入分隔换行；未自行识别 PDF 语法或布局。

每页保留 `number/text`，兼容现有读取、检索和 T05 绑定，并增加宽高、旋转、cropbox、块、图片区域及表格状态。每个文本块保存原生文本和对应 `start/end`，可直接核对 `page.text[start:end] == block.text`。

**字符偏移属于保存的提取页文本，不是 PDF 二进制字节偏移。** bbox 使用 PyMuPDF 的未旋转页面点坐标；旋转值单独保留。定位精度为文本块，不宣称每个字符都有独立坐标。

新 claim 携带 `locator`：原 PDF blob、解析产物 ref、页、字符范围，以及相交块的编号和 bbox。校验时重新比对 source 与不可变解析产物，并重算 locator；篡改 bbox、脱离原产物或删除必需定位均不能算作有效的新 PDF 引用。导出包保留整个来源引用闭包。

## 表格、扫描页与未知内容

表格由引擎的 `find_tables()` / `extract()` 生成，保存行列、单元格坐标与 `extracted_unverified` 状态。它是单独的辅助数据，不能拿转换后的表格行或 Markdown 伪造页文本引用。现有 Agent 可读取原生页文本；完整结构化表格可通过解析产物查看，本轮未增加通用表格推理工具。

| 情况 | 保存与反馈 |
|---|---|
| 原生文本 | 保留文本和块位置，状态 text_extracted |
| 文本与图片混合 | 保留图片区域，提示未转录，状态 text_with_images |
| 无可提取文本 | 保留空页和图片位置，状态 textless；需要视觉检查或 OCR |
| 表格检测/单元格提取失败 | 原生文本仍保留，表格覆盖标为未知并给出警告 |
| 字体替换字符、引擎修复文档结构 | 明确提示复核，原始 PDF 字节保持不变 |
| 整页解析失败、损坏、密码保护或超限 | 拒绝生成成功来源；保留原 PDF 和失败诊断 |

OCR 与公式识别始终记录为 `not_run`。空文本不能证明页中没有方程，文本提取成功也不证明图片公式被完整读取。坐标排序不保证复杂双栏阅读顺序正确。Agent 的初始材料概览会显示有界警告和能力状态，读取反馈附带定位；需要的证据无法读取时仍应追问。

## 有界调用与兼容

新后端在独立进程中执行。每个 PDF 调用默认总期限 30 秒，允许设置至 300 秒，包含进程启动与等待；超时或工作进程崩溃后清理子进程。这与 T04 的科学计算期限分别计量。原生库标准输出诊断转入 stderr，避免干扰 CLI JSON。

默认上限为 200 页、200 万字符、2 万文本块/图片区域、1000 张表及 10 万单元格；只能向下收紧。字符限额计入辅助表格文本，避免重复数据绕过预算。超限不悄悄截断成功来源。每条 locator 最多展示 32 个相交块，并披露总数与截断状态。

这些是输入/输出及耗时限制，不是操作系统级 RAM 配额或实时性保证。传统 pypdf 路径保持原语义，未新增其进程期限。重选引擎会生成新的来源 ref，不会覆盖旧来源、claim 或冻结任务。T03b 仍要求相同源码版本；旧版本任务不能绕过指纹检查恢复。

## 验收证据

原 13 个最小用例在修改前全部失败；首轮定向 83 项通过，扩展定向 109 项通过，首轮完整回归 1003 项通过。后续完整检查暴露原生库提示污染 stdout 的竞态，另用强制刷新的诊断稳定复现并修复；原始失败日志保留。

最终 PDF 专项 **40 项通过**，完整回归 **1030 项通过**，耗时 189.88 秒；原有测试文件未修改，基础依赖版本及 requirements.lock 不变。最终回归、补丁和哈希见 [T06 验收记录](../artifacts/audit-t06-pdf-adapter-20261019/acceptance.json)。

[实际 PDF 演练](../artifacts/audit-t06-pdf-adapter-20261019/final-evidence/pdf-results.json)使用本地生成的合成文档，包含原生文字、带框表格、图片和仅图像页；从检索、绑定到真实 ODE 求解、导出及中断认领均有产物。图像页保留缺口，求解结果的 scientific_status 仍为 not_verified。

决策来自脚本，解析和科学计算调用真实工具。这些是开发回归和接口证据，不是复杂论文提取率、独立留出集成绩或真实模型科研能力提升。未读取独立评测集或 c13/c18 材料，未调用外部模型，安装只下载项目依赖。
